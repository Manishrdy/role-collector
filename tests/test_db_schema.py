from __future__ import annotations

import sqlite3
from pathlib import Path

from job_agent.db.migrate import migrate
from job_agent.db.repo import (
    finish_search_run,
    list_table_names,
    log_agent_event,
    start_search_run,
)

EXPECTED_TABLES = {
    "search_runs",
    "companies",
    "funding_events",
    "linkedin_posts",
    "jobs",
    "job_sources",
    "duplicate_candidates",
    "page_fetches",
    "agent_events",
    "agent_cycles",
    "agent_tool_calls",
    "agent_source_stats",
    "agent_memory",
    "job_batches",
}


def test_migrate_creates_all_tables(isolated_db: Path) -> None:
    migrate()
    tables = set(list_table_names())
    assert EXPECTED_TABLES.issubset(tables)


def test_migrate_creates_expected_indexes(isolated_db: Path) -> None:
    migrate()
    with sqlite3.connect(isolated_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()
    names = {r[0] for r in rows}
    assert "idx_jobs_canonical_url" in names
    assert "idx_jobs_normalized_company" in names
    assert "idx_jobs_normalized_title" in names
    assert "idx_jobs_description_hash" in names
    assert "idx_agent_source_stats_source" in names
    assert "idx_agent_memory_key_scope" in names


def test_migrate_creates_agent_columns(isolated_db: Path) -> None:
    migrate()
    with sqlite3.connect(isolated_db) as conn:
        job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        source_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_sources)").fetchall()}
        linkedin_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(linkedin_posts)").fetchall()
        }
    assert {
        "location_normalized_json",
        "role_family",
        "role_match_status",
        "level",
        "level_confidence",
    }.issubset(job_cols)
    assert "batch_id" in source_cols
    assert "company_id" in linkedin_cols


def test_migrate_is_idempotent(isolated_db: Path) -> None:
    migrate()
    migrate()  # second call must not raise
    tables = set(list_table_names())
    assert EXPECTED_TABLES.issubset(tables)


def test_search_run_lifecycle_persists(isolated_db: Path) -> None:
    migrate()
    run_id = start_search_run(source_type="test", query="q", time_window="past_24h")
    assert run_id > 0
    log_agent_event(search_run_id=run_id, event_type="hello", message="world")
    finish_search_run(run_id, status="succeeded")

    with sqlite3.connect(isolated_db) as conn:
        run = conn.execute(
            "SELECT status, finished_at FROM search_runs WHERE id = ?", (run_id,)
        ).fetchone()
        events = conn.execute(
            "SELECT event_type, event_message FROM agent_events WHERE search_run_id = ?",
            (run_id,),
        ).fetchall()

    assert run is not None and run[0] == "succeeded" and run[1] is not None
    assert events == [("hello", "world")]
