"""Autonomous config-driven worker loop."""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from job_agent.agent.classifiers import OllamaClassifierClient
from job_agent.agent.react import OllamaReActPlanner, ReActLoop
from job_agent.agent.scheduler import compute_source_schedule
from job_agent.agent.tools import build_default_registry
from job_agent.config import AppConfig, load_config
from job_agent.db import repo
from job_agent.db.migrate import migrate
from job_agent.graph.state import AgentState
from job_agent.tracing.langfuse_client import _TraceLike, get_tracer

log = logging.getLogger(__name__)
_shutdown_requested = False


class WorkerShutdownRequested(RuntimeError):
    pass


def _request_shutdown(signum: int, _frame: Any) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    log.warning("worker shutdown requested by signal %s", signum)


@dataclass(frozen=True)
class WorkerCycleResult:
    cycle_id: int
    search_run_id: int
    status: str
    summary: dict[str, Any]
    sleep_seconds: float


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _sleep_seconds(cfg: AppConfig, *, fatal: bool = False) -> float:
    if fatal:
        return max(1.0, cfg.agent_loop.fatal_retry_sleep_minutes * 60.0)
    return max(1.0, cfg.agent_loop.cycle_sleep_hours * 3600.0)


def _lock_path(cfg: AppConfig) -> Path:
    db_parent = Path(cfg.storage.sqlite_path).resolve().parent
    return db_parent / "job-agent-worker.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_worker_lock(cfg: AppConfig) -> Path:
    path = _lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": _iso(_utc_now())}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as e:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_pid = int(existing.get("pid", -1))
        except Exception:
            existing_pid = -1
        if existing_pid > 0 and not _pid_alive(existing_pid):
            path.unlink(missing_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        else:
            raise RuntimeError(f"worker lock already held: {path}") from e
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))
    return path


def _release_worker_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        log.exception("failed to release worker lock: %s", path)


def _check_shutdown() -> None:
    if _shutdown_requested:
        raise WorkerShutdownRequested("shutdown requested")


def _check_deadline(deadline: datetime) -> None:
    if _utc_now() >= deadline:
        raise TimeoutError("worker cycle exceeded max_cycle_runtime_minutes")


def _llm_planner_sampled_in(cfg: AppConfig, *, dry_run: bool) -> bool:
    if dry_run or not cfg.agent_loop.react_llm_enabled:
        return False
    rate = cfg.agent_loop.react_llm_sample_rate
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    return random.random() < rate


def run_worker_forever(*, once: bool = False, dry_run: bool = False) -> None:
    """Run cycles forever unless ``once`` is true."""
    global _shutdown_requested
    _shutdown_requested = False
    old_int = signal.signal(signal.SIGINT, _request_shutdown)
    old_term = signal.signal(signal.SIGTERM, _request_shutdown)
    consecutive_failures = 0
    try:
        while True:
            if _shutdown_requested:
                log.info("worker stopping before next cycle")
                return
            cfg = load_config()
            result = run_one_cycle(cfg=cfg, dry_run=dry_run)
            if result.status == "succeeded":
                consecutive_failures = 0
            else:
                consecutive_failures += 1
            if once:
                return
            multiplier = 1.0 if consecutive_failures <= 1 else float(min(4, 2 ** (consecutive_failures - 1)))
            sleep_seconds = result.sleep_seconds * multiplier
            log.info(
                "cycle %s finished status=%s; sleeping %.0fs",
                result.cycle_id,
                result.status,
                sleep_seconds,
            )
            wake_at = time.time() + sleep_seconds
            while time.time() < wake_at:
                if _shutdown_requested:
                    log.info("worker stop requested during sleep")
                    return
                time.sleep(min(1.0, wake_at - time.time()))
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


def run_one_cycle(*, cfg: AppConfig | None = None, dry_run: bool = False) -> WorkerCycleResult:
    cfg = cfg or load_config()
    lock_path = _acquire_worker_lock(cfg)
    tracer = get_tracer(cfg)
    trace: _TraceLike | None = None
    trace_ended = False
    try:
        migrate()
        started_at = _utc_now()
        deadline = started_at + timedelta(minutes=max(0, cfg.agent_loop.max_cycle_runtime_minutes))

        run_id = repo.start_search_run(
            source_type="agent_worker_cycle",
            config_snapshot={
                "agent": cfg.agent.model_dump(),
                "search": cfg.search.model_dump(),
                "agent_loop": cfg.agent_loop.model_dump(),
            },
        )
        cycle_id = repo.start_agent_cycle(
            search_run_id=run_id,
            config_snapshot=cfg.model_dump(exclude={"langfuse_secret_key", "langfuse_public_key"}),
        )
        batch_id = repo.create_job_batch(
            search_run_id=run_id,
            agent_cycle_id=cycle_id,
            metadata={"batch_min_jobs": cfg.agent_loop.batch_min_jobs, "batch_max_jobs": cfg.agent_loop.batch_max_jobs},
        )
        trace = tracer.trace(
            name="job_agent_worker_cycle",
            metadata={
                "cycle_id": cycle_id,
                "search_run_id": run_id,
                "dry_run": dry_run,
                "react_enabled": cfg.agent_loop.react_enabled,
                "react_llm_enabled": cfg.agent_loop.react_llm_enabled,
                "react_llm_sample_rate": cfg.agent_loop.react_llm_sample_rate,
            },
            tags=["worker_cycle", f"mode:{cfg.agent.mode}"] + (["dry_run"] if dry_run else []),
        )

        registry = build_default_registry()
        final: AgentState = {
            "run_id": run_id,
            "runtime": {
                "dry_run": dry_run,
                "batch_id": batch_id,
                "batch_current_count": 0,
                "agent_cycle_id": cycle_id,
                "max_queries_override": cfg.agent_loop.max_google_queries_per_cycle,
            },
        }
        status = "succeeded"
        error: str | None = None
        try:
            _check_shutdown()
            schedule_span = trace.span(name="compute_source_schedule") if trace is not None else None
            source_decisions = compute_source_schedule(cfg)
            if schedule_span is not None:
                schedule_span.update(
                    output={
                        "eligible_sources": [d.source_name for d in source_decisions if d.status == "eligible"],
                        "skipped_sources": [
                            {"source": d.source_name, "reason": d.reason}
                            for d in source_decisions
                            if d.status != "eligible"
                        ],
                    }
                )
                schedule_span.end()
            final["runtime"]["source_execution_plan"] = [
                {
                    "source_name": d.source_name,
                    "tool_name": d.tool_name,
                    "status": d.status,
                    "reason": d.reason,
                    "priority_score": d.priority_score,
                    "priority_reason": d.priority_reason,
                }
                for d in source_decisions
            ]
            eligible_sources = [d for d in source_decisions if d.status == "eligible"]
            for decision in source_decisions:
                if decision.status == "eligible":
                    continue
                repo.update_source_stats(
                    source_name=decision.source_name,
                    status="skipped",
                    metadata={"reason": decision.reason},
                )

            pipeline_tools = [
                "fetch_candidate_pages",
                "extract_job_data",
                "exact_idempotency_check",
                "semantic_duplicate_check",
                "save_job_batch",
                "write_cycle_summary",
            ]
            if not cfg.agent_loop.react_enabled:
                final["runtime"]["react_trace"] = []
                final["runtime"]["react_metrics"] = {
                    "planner_calls": 0,
                    "planner_valid": 0,
                    "planner_fallbacks": 0,
                    "invalid_tool_choices": 0,
                    "finish_declined_count": 0,
                }
                for tool_name in [*(d.tool_name for d in eligible_sources), *pipeline_tools]:
                    _check_shutdown()
                    _check_deadline(deadline)
                    tool_span = trace.span(name=f"tool:{tool_name}") if trace is not None else None
                    obs = registry.run(
                        tool_name,
                        {"dry_run": dry_run, "runtime": {"state": final}},
                        agent_cycle_id=cycle_id,
                        search_run_id=run_id,
                    )
                    if tool_span is not None:
                        tool_span.update(
                            output={
                                "status": obs.status,
                                "source_name": obs.source_name,
                                "candidates": obs.candidates,
                                "jobs_saved": obs.jobs_saved,
                                "message": obs.message,
                            }
                        )
                        tool_span.end()
                    del obs
            else:
                planner_sampled = _llm_planner_sampled_in(cfg, dry_run=dry_run)
                final["runtime"]["react_planner"] = {
                    "enabled": cfg.agent_loop.react_llm_enabled and not dry_run,
                    "sample_rate": cfg.agent_loop.react_llm_sample_rate,
                    "sampled_in": planner_sampled,
                }
                log.info(
                    "react planner canary enabled=%s sample_rate=%.3f sampled_in=%s",
                    cfg.agent_loop.react_llm_enabled and not dry_run,
                    cfg.agent_loop.react_llm_sample_rate,
                    planner_sampled,
                )
                planner = OllamaReActPlanner(cfg) if planner_sampled else None
                loop = ReActLoop(
                    cfg=cfg,
                    source_tools=[d.tool_name for d in eligible_sources],
                    dry_run=dry_run,
                    planner=planner,
                )
                try:
                    max_steps = max(1, cfg.agent_loop.react_max_steps)
                    for _ in range(max_steps):
                        _check_shutdown()
                        _check_deadline(deadline)
                        react_decision = loop.next_decision()
                        if react_decision.action == "finish":
                            break
                        if react_decision.tool_name is None:
                            raise RuntimeError("react decision missing tool_name")
                        tool_span = trace.span(
                            name=f"tool:{react_decision.tool_name}",
                            metadata={
                                "react_reason": react_decision.reason,
                                "react_thought_summary": react_decision.thought_summary,
                            },
                        ) if trace is not None else None
                        obs = registry.run(
                            react_decision.tool_name,
                            {"dry_run": dry_run, "runtime": {"state": final}},
                            agent_cycle_id=cycle_id,
                            search_run_id=run_id,
                        )
                        if tool_span is not None:
                            tool_span.update(
                                output={
                                    "status": obs.status,
                                    "source_name": obs.source_name,
                                    "candidates": obs.candidates,
                                    "jobs_saved": obs.jobs_saved,
                                    "message": obs.message,
                                }
                            )
                            tool_span.end()
                        loop.record_observation(react_decision.tool_name, obs)
                    else:
                        raise RuntimeError(f"react loop exceeded max steps ({max_steps})")
                    final["runtime"]["react_trace"] = loop.trace
                    final["runtime"]["react_metrics"] = loop.metrics
                finally:
                    loop.close()
        except WorkerShutdownRequested as e:
            log.warning("agent worker cycle interrupted")
            status = "stopped"
            error = str(e)
            if trace is not None:
                trace.update(level="WARNING", status_message=error)
        except Exception as e:
            log.exception("agent worker cycle failed")
            status = "failed"
            error = str(e)
            if trace is not None:
                trace.update(level="ERROR", status_message=error)

        summary = _build_summary(final, status=status, error=error)
        _apply_cycle_reflection(cfg, summary, dry_run=dry_run)
        runtime = final.get("runtime", {}) or {}
        runtime_batch_id = runtime.get("batch_id") if isinstance(runtime, dict) else None
        runtime_batch_count = int(runtime.get("batch_current_count", 0) or 0) if isinstance(runtime, dict) else 0
        if runtime_batch_id is not None:
            repo.finish_job_batch(
                int(runtime_batch_id),
                status="flushed" if status == "succeeded" else "failed",
                job_count=runtime_batch_count,
                metadata=summary,
            )
        _persist_source_backoff_signals(cfg, final)
        _update_source_stats(final, status=status)
        _write_memory(cfg, cycle_id=cycle_id, summary=summary)
        sleep_seconds = _sleep_seconds(cfg, fatal=status == "failed")
        sleep_until = _iso(_utc_now() + timedelta(seconds=sleep_seconds))
        repo.finish_agent_cycle(
            cycle_id,
            status=status,
            summary=summary,
            error_message=error,
            sleep_until=sleep_until,
        )
        repo.finish_search_run(run_id, status=status, error_message=error)
        if trace is not None:
            trace.update(output={"summary": summary, "status": status, "status_message": error if error else "ok"})
            trace.end()
            trace_ended = True
            tracer.flush()
        return WorkerCycleResult(
            cycle_id=cycle_id,
            search_run_id=run_id,
            status=status,
            summary=summary,
            sleep_seconds=sleep_seconds,
        )
    finally:
        if trace is not None and not trace_ended:
            with suppress(Exception):
                trace.end()
            tracer.flush()
        _release_worker_lock(lock_path)


def _build_summary(final: AgentState, *, status: str, error: str | None) -> dict[str, Any]:
    base = dict(final.get("summary", {}) or {})
    candidates = final.get("candidate_urls", []) or []
    runtime = final.get("runtime", {}) or {}
    react_trace = runtime.get("react_trace", []) if isinstance(runtime, dict) else []
    react_trace_out = react_trace if isinstance(react_trace, list) else []
    by_source: dict[str, int] = {}
    for c in candidates:
        source = str(c.get("source_type") or c.get("engine") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
    base.update(
        {
            "status": status,
            "error": error,
            "candidate_sources": by_source,
            "source_execution_plan": (runtime.get("source_execution_plan", []) if isinstance(runtime, dict) else []),
            "react_steps": len(react_trace_out),
            "react_trace": react_trace_out,
            "react_metrics": (runtime.get("react_metrics", {}) if isinstance(runtime, dict) else {}),
            "react_planner": (runtime.get("react_planner", {}) if isinstance(runtime, dict) else {}),
            "intelligence_metrics": (runtime.get("intelligence_metrics", {}) if isinstance(runtime, dict) else {}),
            "source_signals": (runtime.get("source_signals", {}) if isinstance(runtime, dict) else {}),
        }
    )
    return base


def _update_source_stats(final: AgentState, *, status: str) -> None:
    candidates = final.get("candidate_urls", []) or []
    saved = len(final.get("saved_jobs", []) or [])
    by_source: dict[str, int] = {}
    for c in candidates:
        source = str(c.get("source_type") or c.get("engine") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
    if not by_source:
        for source in ("ats_api_discovery", "ats_google_search", "funding_discovery", "watchlist", "linkedin_public_search"):
            repo.update_source_stats(source_name=source, status="skipped", metadata={"reason": "no candidates"})
        return
    for source, count in by_source.items():
        repo.update_source_stats(
            source_name=source,
            status=status,
            candidates=count,
            jobs_saved=saved if source in {"ats_api_discovery", "ats_google_search", "watchlist"} else 0,
        )


def _persist_source_backoff_signals(cfg: AppConfig, final: AgentState) -> None:
    runtime = final.get("runtime", {}) or {}
    if not isinstance(runtime, dict):
        return
    signals = runtime.get("source_signals", {}) or {}
    if not isinstance(signals, dict):
        return
    until = _iso(_utc_now() + timedelta(hours=max(0.0, cfg.agent_loop.backoff_on_block_hours)))
    for source, source_signal in signals.items():
        if not isinstance(source_signal, dict):
            continue
        if not bool(source_signal.get("blocked")):
            continue
        repo.update_source_stats(
            source_name=str(source),
            status="failed",
            backoff_until=until,
            metadata={"reason": "blocked_signal", "signal": source_signal},
        )


def _write_memory(cfg: AppConfig, *, cycle_id: int, summary: dict[str, Any]) -> None:
    repo.upsert_agent_memory(
        memory_key="latest_cycle_summary",
        memory_scope="global",
        value={"cycle_id": cycle_id, "summary": summary},
        confidence=1.0,
    )
    if not cfg.agent_loop.memory_markdown_enabled:
        return
    root = Path(cfg.storage.sqlite_path).resolve().parent / "memory"
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    body = _summary_markdown(cycle_id=cycle_id, summary=summary)
    (root / "latest.md").write_text(body, encoding="utf-8")
    (runs / f"cycle-{cycle_id}.md").write_text(body, encoding="utf-8")


def _apply_cycle_reflection(cfg: AppConfig, summary: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run or not cfg.agent_loop.cycle_reflection_llm_enabled:
        return
    client = OllamaClassifierClient(cfg)
    try:
        reflection = client.reflect_cycle(summary=summary)
    finally:
        client.close()
    if reflection is not None:
        summary["cycle_reflection"] = reflection.model_dump()


def _summary_markdown(*, cycle_id: int, summary: dict[str, Any]) -> str:
    lines = [
        f"# Agent Cycle {cycle_id}",
        "",
        f"- Status: {summary.get('status')}",
        f"- Candidate URLs: {summary.get('candidate_urls', 0)}",
        f"- Fetched pages: {summary.get('fetched_pages', 0)}",
        f"- Extracted jobs: {summary.get('extracted_jobs', 0)}",
        f"- Saved jobs: {summary.get('saved_jobs', 0)}",
        f"- Errors: {summary.get('errors', 0)}",
        "",
        "## Candidate Sources",
        "",
    ]
    sources = summary.get("candidate_sources") or {}
    if isinstance(sources, dict) and sources:
        for source, count in sorted(sources.items()):
            lines.append(f"- {source}: {count}")
    else:
        lines.append("- none")
    if summary.get("error"):
        lines.extend(["", "## Error", "", str(summary["error"])])
    react_metrics = summary.get("react_metrics")
    if isinstance(react_metrics, dict) and react_metrics:
        lines.extend(["", "## ReAct Metrics", ""])
        for key in (
            "planner_calls",
            "planner_valid",
            "planner_fallbacks",
            "invalid_tool_choices",
            "finish_declined_count",
        ):
            lines.append(f"- {key}: {react_metrics.get(key, 0)}")
    react_planner = summary.get("react_planner")
    if isinstance(react_planner, dict) and react_planner:
        lines.extend(["", "## Planner Canary", ""])
        lines.append(f"- enabled: {react_planner.get('enabled')}")
        lines.append(f"- sample_rate: {react_planner.get('sample_rate')}")
        lines.append(f"- sampled_in: {react_planner.get('sampled_in')}")
    reflection = summary.get("cycle_reflection")
    if isinstance(reflection, dict) and reflection:
        lines.extend(["", "## Cycle Reflection", ""])
        lines.append(f"- summary: {reflection.get('summary', '')}")
        focus = reflection.get("next_cycle_focus") or []
        if isinstance(focus, list):
            for item in focus[:5]:
                lines.append(f"- focus: {item}")
    lines.extend(["", "```json", json.dumps(summary, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)
