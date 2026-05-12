"""SQLite schema migrator. Idempotent — safe to run on every startup.

Run via:  python -m job_agent.db.migrate

The base schema lives in `schema.sql`. For column additions to existing DBs,
add a step to `_post_schema_migrations()` — each step must be safe to re-run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from job_agent.config import load_config

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _post_schema_migrations(conn: sqlite3.Connection) -> None:
    """Run idempotent ALTERs for changes added after the initial schema."""
    if not _column_exists(conn, "jobs", "needs_review"):
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0"
        )
    if not _column_exists(conn, "companies", "last_polled_at"):
        conn.execute("ALTER TABLE companies ADD COLUMN last_polled_at TEXT")
    if not _column_exists(conn, "jobs", "location_normalized_json"):
        conn.execute("ALTER TABLE jobs ADD COLUMN location_normalized_json TEXT")
    if not _column_exists(conn, "jobs", "role_family"):
        conn.execute("ALTER TABLE jobs ADD COLUMN role_family TEXT")
    if not _column_exists(conn, "jobs", "role_match_status"):
        conn.execute("ALTER TABLE jobs ADD COLUMN role_match_status TEXT")
    if not _column_exists(conn, "jobs", "level"):
        conn.execute("ALTER TABLE jobs ADD COLUMN level TEXT")
    if not _column_exists(conn, "jobs", "level_confidence"):
        conn.execute("ALTER TABLE jobs ADD COLUMN level_confidence REAL")
    if not _column_exists(conn, "job_sources", "batch_id"):
        conn.execute("ALTER TABLE job_sources ADD COLUMN batch_id INTEGER")
    if not _column_exists(conn, "linkedin_posts", "company_id"):
        conn.execute("ALTER TABLE linkedin_posts ADD COLUMN company_id INTEGER")


def migrate(db_path: str | Path | None = None) -> Path:
    """Create schema in the configured SQLite file. Returns the resolved path."""
    cfg = load_config()
    resolved = Path(db_path or cfg.storage.sqlite_path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    schema_sql = SCHEMA_FILE.read_text()
    with sqlite3.connect(resolved) as conn:
        conn.executescript(schema_sql)
        _post_schema_migrations(conn)
        conn.commit()
    return resolved


def main() -> None:
    path = migrate()
    print(f"schema applied to {path}")


if __name__ == "__main__":
    main()
