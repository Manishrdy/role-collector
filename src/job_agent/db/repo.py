"""Typed read/write helpers for SQLite. Connections are short-lived per call.

Phase-1 surface: just enough to start a search_run, log events, and finish a run.
Job/company/dedupe writes will land in Phase 2-4.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_agent.config import load_config


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    cfg = load_config()
    resolved = Path(db_path or cfg.storage.sqlite_path).resolve()
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def start_search_run(
    *,
    source_type: str,
    query: str | None = None,
    time_window: str | None = None,
    config_snapshot: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO search_runs
                (source_type, query, time_window, started_at, status, config_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_type,
                query,
                time_window,
                _utc_now_iso(),
                "running",
                json.dumps(config_snapshot) if config_snapshot else None,
            ),
        )
        run_id = cur.lastrowid
        assert run_id is not None
        return run_id


def finish_search_run(
    run_id: int,
    *,
    status: str,
    error_message: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE search_runs
               SET finished_at = ?, status = ?, error_message = ?
             WHERE id = ?
            """,
            (_utc_now_iso(), status, error_message, run_id),
        )


def log_agent_event(
    *,
    search_run_id: int | None,
    event_type: str,
    message: str | None = None,
    safe_metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO agent_events
                (search_run_id, event_type, event_message, safe_metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                search_run_id,
                event_type,
                message,
                json.dumps(safe_metadata) if safe_metadata else None,
                _utc_now_iso(),
            ),
        )


def list_table_names(db_path: str | Path | None = None) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]
