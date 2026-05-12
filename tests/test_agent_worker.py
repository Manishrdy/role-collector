from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import job_agent.agent.worker as worker_mod
from job_agent.agent.intelligence import classify_job, normalize_location
from job_agent.agent.scheduler import SourceScheduleDecision
from job_agent.agent.worker import (
    _lock_path,
    _normalize_source_type,
    _persist_source_backoff_signals,
    _update_source_stats,
    run_one_cycle,
)
from job_agent.config import load_config
from job_agent.db import repo
from job_agent.db.migrate import migrate
from job_agent.extract.schema import ExtractedJob
from job_agent.graph import nodes


def test_location_normalization_city_country() -> None:
    loc = normalize_location("Los Angeles, California, United States", remote_type=None)
    assert loc.city == "Los Angeles"
    assert loc.region == "California"
    assert loc.country == "United States"
    assert loc.display == "Los Angeles, United States"


def test_job_intelligence_classifies_level_and_role() -> None:
    cfg = load_config()
    job = ExtractedJob(
        title="Senior Backend Engineer",
        company_name="Acme",
        location="Toronto, Canada",
        description="We need a backend engineer with 5+ years of experience.",
        extraction_source="dom",
        extraction_confidence=0.7,
    )
    intel = classify_job(job, cfg)
    assert intel.role_family == "backend"
    assert intel.role_match_status in {"exact_match", "adjacent"}
    assert intel.level == "senior"
    assert intel.location.country == "Canada"


def test_worker_dry_run_creates_cycle_batch_and_tool_calls(isolated_db: Path) -> None:
    migrate()
    result = run_one_cycle(dry_run=True)
    assert result.status == "succeeded"
    assert isinstance(result.summary.get("source_execution_plan"), list)
    assert isinstance(result.summary.get("react_metrics"), dict)
    assert result.summary.get("react_metrics", {}).get("planner_calls") == 0
    assert isinstance(result.summary.get("react_planner"), dict)
    assert result.summary.get("react_planner", {}).get("sampled_in") is False
    assert isinstance(result.summary.get("react_trace"), list)

    with sqlite3.connect(isolated_db) as conn:
        cycles = conn.execute("SELECT COUNT(*) FROM agent_cycles").fetchone()[0]
        batches = conn.execute("SELECT COUNT(*) FROM job_batches").fetchone()[0]
        tool_calls = conn.execute("SELECT COUNT(*) FROM agent_tool_calls").fetchone()[0]
        memory = conn.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0]
    assert cycles == 1
    # Lazy batch creation: a dry-run cycle that saves no jobs creates no batch.
    assert batches == 0
    assert tool_calls >= 1
    assert memory == 1


def test_worker_singleton_lock_guard(isolated_db: Path) -> None:
    del isolated_db
    cfg = load_config()
    lock = _lock_path(cfg)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock_payload = f'{{"pid": {os.getpid()}, "started_at": "2026-05-12T12:00:00+00:00"}}'
    lock.write_text(
        lock_payload,
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="worker lock already held"):
        run_one_cycle(dry_run=True)


def test_worker_cycle_timeout_marks_failed(isolated_db: Path) -> None:
    migrate()
    cfg = load_config()
    cfg = cfg.model_copy(update={"agent_loop": cfg.agent_loop.model_copy(update={"max_cycle_runtime_minutes": 0})})
    result = run_one_cycle(cfg=cfg, dry_run=True)
    assert result.status == "failed"
    assert "max_cycle_runtime_minutes" in str(result.summary.get("error") or "")


def test_source_type_normalization() -> None:
    assert _normalize_source_type("ats_google_search_broad") == "ats_google_search"
    assert _normalize_source_type("funding_google_search") == "funding_discovery"
    assert _normalize_source_type("ats_api_discovery") == "ats_api_discovery"
    assert _normalize_source_type("watchlist") == "watchlist"
    assert _normalize_source_type(None) is None
    # Unknown source_types pass through unchanged.
    assert _normalize_source_type("mystery") == "mystery"


def test_update_source_stats_only_touches_eligible_sources(isolated_db: Path) -> None:
    del isolated_db
    migrate()
    final = {
        "runtime": {
            "source_execution_plan": [
                {"source_name": "ats_api_discovery", "status": "eligible"},
                {"source_name": "ats_google_search", "status": "eligible"},
                {"source_name": "linkedin_public_search", "status": "skipped"},
            ],
        },
        "candidate_urls": [
            {"source_type": "ats_google_search_broad", "url": "u1"},
            {"source_type": "ats_google_search", "url": "u2"},
        ],
        "saved_jobs": [1, 2],
    }
    _update_source_stats(final, status="succeeded")
    stats = {row["source_name"]: row for row in repo.list_source_stats()}
    # Broad variant rolled up under ats_google_search.
    assert stats["ats_google_search"]["candidates_total"] == 2
    # Eligible source with zero candidates is still stamped (status=succeeded).
    assert stats["ats_api_discovery"]["candidates_total"] == 0
    assert stats["ats_api_discovery"]["last_status"] == "succeeded"
    # Skipped/never-eligible sources are NOT touched.
    assert "linkedin_public_search" not in stats


def test_update_source_stats_skips_blocked_sources(isolated_db: Path) -> None:
    del isolated_db
    migrate()
    # Pre-seed: simulate _persist_source_backoff_signals stamping "failed".
    repo.update_source_stats(
        source_name="ats_google_search",
        status="failed",
        backoff_until="2099-01-01T00:00:00+00:00",
    )
    final = {
        "runtime": {
            "source_execution_plan": [
                {"source_name": "ats_google_search", "status": "eligible"},
            ],
            "source_signals": {
                "ats_google_search": {"blocked": True},
            },
        },
        "candidate_urls": [],
        "saved_jobs": [],
    }
    _update_source_stats(final, status="succeeded")
    stats = {row["source_name"]: row for row in repo.list_source_stats()}
    # The blocked source's failed status is preserved, not overwritten.
    assert stats["ats_google_search"]["last_status"] == "failed"


def test_worker_persists_source_backoff_signals(isolated_db: Path) -> None:
    del isolated_db
    migrate()
    cfg = load_config()
    final = {
        "runtime": {
            "source_signals": {
                "ats_google_search": {"blocked": True, "google_blocked_at": 2},
                "linkedin_public_search": {"blocked": False},
            }
        }
    }
    _persist_source_backoff_signals(cfg, final)
    stats = {row["source_name"]: row for row in repo.list_source_stats()}
    assert stats["ats_google_search"]["backoff_until"] is not None
    assert stats["ats_google_search"]["last_status"] == "failed"
    assert stats.get("linkedin_public_search") is None


def test_worker_non_dry_run_handles_mixed_source_outcomes(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrate()

    def _schedule(_cfg: object) -> list[SourceScheduleDecision]:
        return [
            SourceScheduleDecision(
                tool_name="discover_ats_api_jobs",
                source_name="ats_api_discovery",
                status="eligible",
                reason="test",
                priority_score=1.0,
            ),
            SourceScheduleDecision(
                tool_name="search_google_ats_jobs",
                source_name="ats_google_search",
                status="eligible",
                reason="test",
                priority_score=0.9,
            ),
            SourceScheduleDecision(
                tool_name="search_linkedin_public_posts",
                source_name="linkedin_public_search",
                status="eligible",
                reason="test",
                priority_score=0.8,
            ),
            SourceScheduleDecision(
                tool_name="discover_funding_events",
                source_name="funding_discovery",
                status="eligible",
                reason="test",
                priority_score=0.7,
            ),
            SourceScheduleDecision(
                tool_name="resolve_funded_companies",
                source_name="funding_resolvers",
                status="skipped",
                reason="test_skipped",
            ),
            SourceScheduleDecision(
                tool_name="poll_watchlist",
                source_name="watchlist",
                status="skipped",
                reason="test_skipped",
            ),
        ]

    monkeypatch.setattr(worker_mod, "compute_source_schedule", _schedule)

    def fake_ats_api(state: dict[str, object]) -> dict[str, object]:
        candidates = list(state.get("candidate_urls", []) or [])
        candidates.append(
            {
                "url": "https://jobs.example.com/ok",
                "canonical_url": "https://jobs.example.com/ok",
                "source_type": "ats_api_discovery",
                "engine": "mock",
                "role": "software engineer",
            }
        )
        state["candidate_urls"] = candidates
        return state

    def fake_google_blocked(state: dict[str, object]) -> dict[str, object]:
        runtime = state.setdefault("runtime", {})
        assert isinstance(runtime, dict)
        signals = runtime.setdefault("source_signals", {})
        assert isinstance(signals, dict)
        signals["ats_google_search"] = {
            "blocked": True,
            "queries_blocked": 2,
            "google_blocked_at": 1,
            "self_stopped_at": 1,
        }
        return state

    def fake_linkedin_captcha(state: dict[str, object]) -> dict[str, object]:
        runtime = state.setdefault("runtime", {})
        assert isinstance(runtime, dict)
        signals = runtime.setdefault("source_signals", {})
        assert isinstance(signals, dict)
        signals["linkedin_public_search"] = {
            "blocked": True,
            "posts_blocked": 1,
            "stopped_early": True,
            "stop_reason": "captcha",
        }
        return state

    def fake_funding_error(_state: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("mock funding source failure")

    def fake_fetch(state: dict[str, object]) -> dict[str, object]:
        state["fetched_pages"] = []
        return state

    def fake_extract(state: dict[str, object]) -> dict[str, object]:
        state["extracted_jobs"] = []
        return state

    monkeypatch.setattr(nodes, "run_ats_api_discovery_node", fake_ats_api)
    monkeypatch.setattr(nodes, "run_ats_google_search_node", fake_google_blocked)
    monkeypatch.setattr(nodes, "run_linkedin_public_search_node", fake_linkedin_captcha)
    monkeypatch.setattr(nodes, "run_funding_discovery_node", fake_funding_error)
    monkeypatch.setattr(nodes, "run_funding_resolvers_node", lambda s: s)
    monkeypatch.setattr(nodes, "run_watchlist_node", lambda s: s)
    monkeypatch.setattr(nodes, "fetch_candidate_pages_node", fake_fetch)
    monkeypatch.setattr(nodes, "extract_job_data_node", fake_extract)
    monkeypatch.setattr(nodes, "exact_idempotency_check_node", lambda s: s)
    monkeypatch.setattr(nodes, "semantic_duplicate_check_node", lambda s: s)
    monkeypatch.setattr(nodes, "save_jobs_node", lambda s: s)

    cfg = load_config()
    cfg = cfg.model_copy(
        update={
            "agent_loop": cfg.agent_loop.model_copy(
                update={
                    "react_enabled": True,
                    "react_llm_enabled": False,
                    "cycle_reflection_llm_enabled": False,
                }
            )
        }
    )

    result = run_one_cycle(cfg=cfg, dry_run=False)
    assert result.status == "succeeded"
    signals = result.summary.get("source_signals", {})
    assert isinstance(signals, dict)
    assert signals.get("ats_google_search", {}).get("blocked") is True
    assert signals.get("linkedin_public_search", {}).get("stop_reason") == "captcha"
    assert result.summary.get("candidate_sources", {}).get("ats_api_discovery") == 1

    with sqlite3.connect(isolated_db) as conn:
        conn.row_factory = sqlite3.Row
        funding_call = conn.execute(
            """
            SELECT status, error_message
            FROM agent_tool_calls
            WHERE tool_name = 'discover_funding_events'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        google_stats = conn.execute(
            "SELECT last_status, backoff_until, metadata_json FROM agent_source_stats WHERE source_name = 'ats_google_search'"
        ).fetchone()
        linkedin_stats = conn.execute(
            "SELECT last_status, backoff_until, metadata_json FROM agent_source_stats WHERE source_name = 'linkedin_public_search'"
        ).fetchone()

    assert funding_call is not None
    assert funding_call["status"] == "failed"
    assert "mock funding source failure" in str(funding_call["error_message"] or "")
    assert google_stats is not None
    assert google_stats["last_status"] == "failed"
    assert google_stats["backoff_until"] is not None
    assert linkedin_stats is not None
    assert linkedin_stats["last_status"] == "failed"
    assert linkedin_stats["backoff_until"] is not None
