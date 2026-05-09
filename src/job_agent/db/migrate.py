"""SQLite schema migrator. Idempotent — safe to run on every startup.

Run via:  python -m job_agent.db.migrate
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from job_agent.config import load_config

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def migrate(db_path: str | Path | None = None) -> Path:
    """Create schema in the configured SQLite file. Returns the resolved path."""
    cfg = load_config()
    resolved = Path(db_path or cfg.storage.sqlite_path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    schema_sql = SCHEMA_FILE.read_text()
    with sqlite3.connect(resolved) as conn:
        conn.executescript(schema_sql)
        conn.commit()
    return resolved


def main() -> None:
    path = migrate()
    print(f"schema applied to {path}")


if __name__ == "__main__":
    main()
