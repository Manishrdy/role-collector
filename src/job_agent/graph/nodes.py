"""LangGraph node implementations.

Phase-1: pass-through stubs.
Phase-2: the ATS Google search node now actually drives the browser.
Phase-3: fetch / extract / save are real implementations driving Playwright,
the deterministic parser chain, and the Ollama LLM fallback.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from job_agent.browser.page_fetch import FetchedPage, fetch_pages
from job_agent.browser.safety import normalize_candidate_url
from job_agent.config import load_config
from job_agent.db import repo
from job_agent.extract.llm import OllamaExtractor
from job_agent.extract.pipeline import extract_job
from job_agent.extract.schema import ExtractedJob
from job_agent.graph.state import AgentState
from job_agent.sources import queries as q_mod
from job_agent.sources.ats_search import run_ats_search

log = logging.getLogger(__name__)


def _event(state: AgentState, event_type: str, message: str) -> None:
    run_id = state.get("run_id")
    log.info("[%s] %s", event_type, message)
    if run_id is not None:
        repo.log_agent_event(search_run_id=run_id, event_type=event_type, message=message)


def load_config_node(state: AgentState) -> AgentState:
    cfg = load_config()
    state["config"] = cfg.model_dump(exclude={"langfuse_secret_key", "langfuse_public_key"})
    state.setdefault("errors", [])
    state.setdefault("search_plan", [])
    state.setdefault("candidate_urls", [])
    state.setdefault("fetched_pages", [])
    state.setdefault("extracted_jobs", [])
    state.setdefault("saved_jobs", [])
    state.setdefault("duplicate_candidates", [])
    return state


def create_search_run_node(state: AgentState) -> AgentState:
    cfg = load_config()
    run_id = repo.start_search_run(
        source_type="agent_run",
        config_snapshot={"agent": cfg.agent.model_dump(), "search": cfg.search.model_dump()},
    )
    state["run_id"] = run_id
    _event(state, "run_started", f"search_run id={run_id}")
    return state


def generate_search_plan_node(state: AgentState) -> AgentState:
    """Build the deterministic search plan (currently ATS Google queries only)."""
    cfg = load_config()
    runtime = state.get("runtime", {}) or {}
    max_queries: int | None = runtime.get("max_queries_override")
    plans = q_mod.generate_ats_queries(cfg, max_queries=max_queries)
    state["search_plan"] = [
        {
            "query": p.query,
            "time_window": p.time_window,
            "source_type": p.source_type,
            "target_domain": p.target_domain,
            "ats_type": p.ats_type,
            "role": p.role,
            "location": p.location,
        }
        for p in plans
    ]
    _event(state, "plan_generated", f"{len(plans)} ATS queries planned")
    return state


def run_ats_google_search_node(state: AgentState) -> AgentState:
    cfg = load_config()
    runtime = state.get("runtime", {}) or {}

    if runtime.get("dry_run"):
        _event(state, "ats_google_search", "dry_run: skipping browser")
        return state
    if not cfg.sources.ats_google_search.enabled:
        _event(state, "ats_google_search", "disabled in config")
        return state

    plans = q_mod.generate_ats_queries(cfg, max_queries=runtime.get("max_queries_override"))
    if not plans:
        _event(state, "ats_google_search", "no queries to run")
        return state

    max_results = runtime.get("max_results_override") or cfg.search.max_results_per_query
    run_id = state.get("run_id")

    debug_dir: Path | None = None
    if runtime.get("debug_dump"):
        debug_dir = Path("data/debug") / f"run-{run_id or 'unknown'}"
        debug_dir.mkdir(parents=True, exist_ok=True)

    _event(
        state,
        "ats_google_search",
        f"running {len(plans)} queries with max_results={max_results}"
        + (f" (debug dump -> {debug_dir})" if debug_dir else ""),
    )

    candidates, stats = run_ats_search(
        cfg=cfg,
        plans=plans,
        max_results_per_query=max_results,
        search_run_id=run_id,
        debug_dump_dir=debug_dir,
    )

    state["candidate_urls"] = [asdict(c) for c in candidates]
    _event(
        state,
        "ats_google_search_done",
        (
            f"{len(candidates)} unique URLs / "
            f"{stats.queries_succeeded} ok / "
            f"{stats.queries_blocked} blocked / "
            f"google_blocked_at={stats.google_blocked_at}"
        ),
    )
    return state


def run_funding_discovery_node(state: AgentState) -> AgentState:
    cfg = load_config()
    if not cfg.sources.funding_discovery.enabled:
        _event(state, "funding_discovery", "disabled in config")
        return state
    _event(state, "funding_discovery", "[stub] enabled but not yet implemented")
    return state


def run_linkedin_public_search_node(state: AgentState) -> AgentState:
    cfg = load_config()
    if not cfg.sources.linkedin_public_search.enabled:
        _event(state, "linkedin_public_search", "disabled in config")
        return state
    _event(state, "linkedin_public_search", "[stub] enabled but not yet implemented")
    return state


def _fetched_to_state(p: FetchedPage) -> dict[str, Any]:
    """Project a FetchedPage into the JSON-friendly shape we keep in AgentState."""
    return {
        "url": p.url,
        "canonical_url": p.canonical_url,
        "final_url": p.final_url,
        "domain": p.domain,
        "page_title": p.page_title,
        "http_status": p.http_status,
        "html": p.html,
        "content_hash": p.content_hash,
        "fetched_at": p.fetched_at,
        "status": p.status,
        "error": p.error,
        "blocked_reason": p.blocked_reason,
    }


def fetch_candidate_pages_node(state: AgentState) -> AgentState:
    cfg = load_config()
    runtime = state.get("runtime", {}) or {}
    candidates = state.get("candidate_urls", []) or []

    if not candidates:
        _event(state, "fetch_pages", "no candidate URLs to fetch")
        state["fetched_pages"] = []
        return state

    if runtime.get("dry_run"):
        _event(state, "fetch_pages", f"dry_run: skipping {len(candidates)} fetches")
        state["fetched_pages"] = []
        return state

    # Some Phase-2 candidates land on ATS apply-flow URLs
    # (e.g. /<co>/<uuid>/application?utm_*=...) which the safety layer
    # would reject. Rewrite them to the canonical detail URL first, then
    # collapse any duplicates that result from the normalization.
    normalized_count = 0
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for c in candidates:
        normalized = normalize_candidate_url(c["url"])
        if normalized != c["url"]:
            normalized_count += 1
            c["url"] = normalized
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(c)
    if len(deduped) != len(candidates):
        _event(
            state,
            "fetch_pages",
            f"collapsed {len(candidates) - len(deduped)} duplicate(s) after URL normalization",
        )
    state["candidate_urls"] = deduped
    candidates = deduped

    urls = [c["url"] for c in candidates]
    _event(
        state,
        "fetch_pages",
        f"fetching {len(urls)} candidates ({normalized_count} normalized)",
    )

    pages = fetch_pages(urls, cfg=cfg, concurrency=4)
    run_id = state.get("run_id")
    ok = blocked = errored = 0
    for p in pages:
        if p.status == "ok":
            ok += 1
        elif p.status == "blocked":
            blocked += 1
        else:
            errored += 1
        repo.record_page_fetch(
            url=p.url,
            canonical_url=p.canonical_url,
            domain=p.domain,
            status=p.status,
            http_status=p.http_status,
            content_hash=p.content_hash,
            detected_page_type="job_detail",
            blocked_reason=p.blocked_reason or p.error,
            search_run_id=run_id,
        )

    state["fetched_pages"] = [_fetched_to_state(p) for p in pages]
    _event(
        state,
        "fetch_pages_done",
        f"{ok} ok / {blocked} blocked / {errored} errored",
    )
    return state


def _build_llm_extractor(cfg: Any) -> OllamaExtractor | None:
    """Create an OllamaExtractor unless the LLM provider is disabled."""
    if cfg.llm.provider != "ollama":
        return None
    return OllamaExtractor(cfg)


def extract_job_data_node(state: AgentState) -> AgentState:
    cfg = load_config()
    pages = state.get("fetched_pages", []) or []
    candidates_by_url = {c["url"]: c for c in (state.get("candidate_urls", []) or [])}

    if not pages:
        _event(state, "extract_jobs", "no fetched pages to extract")
        state["extracted_jobs"] = []
        return state

    threshold = cfg.llm.low_confidence_threshold
    extracted: list[dict[str, Any]] = []
    counts = {"ats_parser": 0, "jsonld": 0, "dom": 0, "llm": 0, "missed": 0}

    llm = _build_llm_extractor(cfg)
    try:
        for page in pages:
            if page["status"] != "ok" or not page.get("html"):
                counts["missed"] += 1
                continue
            try:
                job: ExtractedJob | None = extract_job(
                    html=page["html"], url=page["url"], llm=llm
                )
            except Exception as e:
                log.warning("extract crashed for %s: %s", page["url"], e)
                counts["missed"] += 1
                continue
            if job is None:
                counts["missed"] += 1
                continue
            counts[job.extraction_source] = counts.get(job.extraction_source, 0) + 1
            cand = candidates_by_url.get(page["url"], {})
            extracted.append(
                {
                    "url": page["url"],
                    "canonical_url": page["canonical_url"],
                    "domain": page["domain"],
                    "source_type": cand.get("source_type", "ats_google_search"),
                    "source_query": cand.get("source_query"),
                    "needs_review": job.extraction_confidence < threshold,
                    "job": job.model_dump(),
                }
            )
    finally:
        if llm is not None:
            llm.close()

    state["extracted_jobs"] = extracted
    _event(
        state,
        "extract_jobs_done",
        (
            f"{len(extracted)} extracted "
            f"(ats={counts['ats_parser']} jsonld={counts['jsonld']} "
            f"dom={counts['dom']} llm={counts['llm']} missed={counts['missed']})"
        ),
    )
    return state


def exact_idempotency_check_node(state: AgentState) -> AgentState:
    _event(state, "idempotency_check", "[stub] no jobs to check")
    return state


def semantic_duplicate_check_node(state: AgentState) -> AgentState:
    _event(state, "dedupe_check", "[stub] no jobs to dedupe")
    return state


def save_jobs_node(state: AgentState) -> AgentState:
    extracted = state.get("extracted_jobs", []) or []
    if not extracted:
        _event(state, "save_jobs", "no extracted jobs to save")
        state["saved_jobs"] = []
        return state

    run_id = state.get("run_id")
    saved_ids: list[int] = []
    inserted = updated = needs_review = 0
    for record in extracted:
        try:
            job = ExtractedJob.model_validate(record["job"])
        except Exception as e:
            log.warning("invalid extracted job for %s: %s", record.get("url"), e)
            continue
        try:
            result = repo.upsert_job(
                extracted=job,
                canonical_url=record["canonical_url"],
                raw_url=record["url"],
                source_type=record["source_type"],
                source_query=record.get("source_query"),
                search_run_id=run_id,
                needs_review=bool(record.get("needs_review")),
            )
        except Exception as e:
            log.warning("upsert failed for %s: %s", record.get("url"), e)
            continue
        saved_ids.append(result.job_id)
        if result.inserted:
            inserted += 1
        else:
            updated += 1
        if record.get("needs_review"):
            needs_review += 1

    state["saved_jobs"] = saved_ids
    _event(
        state,
        "save_jobs_done",
        f"{inserted} inserted / {updated} updated / {needs_review} flagged for review",
    )
    return state


def write_summary_node(state: AgentState) -> AgentState:
    summary: dict[str, Any] = {
        "candidate_urls": len(state.get("candidate_urls", [])),
        "fetched_pages": len(state.get("fetched_pages", [])),
        "extracted_jobs": len(state.get("extracted_jobs", [])),
        "saved_jobs": len(state.get("saved_jobs", [])),
        "duplicate_candidates": len(state.get("duplicate_candidates", [])),
        "errors": len(state.get("errors", [])),
    }
    state["summary"] = summary
    _event(state, "summary", str(summary))
    return state


def finish_run_node(state: AgentState) -> AgentState:
    run_id = state.get("run_id")
    if run_id is not None:
        status = "failed" if state.get("errors") else "succeeded"
        repo.finish_search_run(run_id, status=status)
        _event(state, "run_finished", f"status={status}")
    return state
