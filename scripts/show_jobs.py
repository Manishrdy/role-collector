"""Pretty-print every row in the local `jobs` table.

Usage:
    .venv/bin/python scripts/show_jobs.py
    .venv/bin/python scripts/show_jobs.py --run-id 15            # only one run
    .venv/bin/python scripts/show_jobs.py --json                 # raw JSON dump
    .venv/bin/python scripts/show_jobs.py --db ./data/jobs.db    # custom path

The script reads `./data/jobs.db` by default (or whatever `--db` points
at), and is fully self-contained — no LangGraph or Playwright imports —
so it can run while the agent is mid-run.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DEFAULT_DB = Path("./data/jobs.db")


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        sys.exit(f"error: db not found at {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _query(
    conn: sqlite3.Connection, run_id: int | None
) -> list[sqlite3.Row]:
    if run_id is None:
        sql = """
            SELECT j.*
              FROM jobs j
             ORDER BY j.id
        """
        return list(conn.execute(sql).fetchall())

    sql = """
        SELECT DISTINCT j.*
          FROM jobs j
          JOIN job_sources js ON js.job_id = j.id
         WHERE js.search_run_id = ?
         ORDER BY j.id
    """
    return list(conn.execute(sql, (run_id,)).fetchall())


def _source_runs_for_job(conn: sqlite3.Connection, job_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT search_run_id FROM job_sources "
        "WHERE job_id = ? AND search_run_id IS NOT NULL ORDER BY search_run_id",
        (job_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _format_table(rows: Iterable[sqlite3.Row], conn: sqlite3.Connection) -> Table:
    table = Table(
        title="jobs",
        show_lines=False,
        header_style="bold cyan",
        title_style="bold",
    )
    table.add_column("id", justify="right", style="dim")
    table.add_column("company", style="bold")
    table.add_column("title")
    table.add_column("location", style="cyan")
    table.add_column("ats", justify="center")
    table.add_column("conf", justify="right")
    table.add_column("review", justify="center")
    table.add_column("seen", justify="right", style="dim")

    for r in rows:
        run_ids = _source_runs_for_job(conn, r["id"])
        seen_label = ",".join(str(rid) for rid in run_ids[-3:])
        review_cell = "[red]yes[/red]" if r["needs_review"] else ""
        ats_cell = (
            f"{r['ats_type']}\n[dim]{(r['ats_job_id'] or '')[:8]}[/dim]"
            if r["ats_type"]
            else "[dim]—[/dim]"
        )
        table.add_row(
            str(r["id"]),
            r["company_name"] or "?",
            r["title"] or "?",
            r["location"] or "[dim]—[/dim]",
            ats_cell,
            f"{(r['extraction_confidence'] or 0):.2f}",
            review_cell,
            seen_label or "[dim]—[/dim]",
        )
    return table


def _format_detail(row: sqlite3.Row, run_ids: list[int]) -> Panel:
    body = Text()
    body.append(f"{row['title']}\n", style="bold")
    body.append(f"{row['company_name']}", style="bold cyan")
    if row["location"]:
        body.append(f"  ·  {row['location']}", style="cyan")
    body.append("\n\n")

    facts: list[tuple[str, str | None]] = [
        ("remote_type", row["remote_type"]),
        ("employment_type", row["employment_type"]),
        ("seniority", row["seniority"]),
        ("salary_text", row["salary_text"]),
        ("posted_date", row["posted_date"]),
        ("ats_type", row["ats_type"]),
        ("ats_job_id", row["ats_job_id"]),
        ("apply_url", row["apply_url"]),
        ("canonical_url", row["canonical_url"]),
        ("source_type", row["source_type"]),
        ("extraction_confidence", str(row["extraction_confidence"])),
        ("needs_review", "yes" if row["needs_review"] else "no"),
        ("first_seen_at", row["first_seen_at"]),
        ("last_seen_at", row["last_seen_at"]),
        ("seen_in_runs", ", ".join(str(r) for r in run_ids) or "—"),
    ]
    for key, value in facts:
        if value is None or value == "":
            continue
        body.append(f"  {key:<22}", style="dim")
        body.append(f"{value}\n")

    if row["description"]:
        snippet = (row["description"] or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        body.append("\n  description (240 chars)\n", style="dim")
        body.append(f"  {snippet}\n")

    return Panel(
        body,
        title=f"[bold]#{row['id']}[/bold]",
        border_style="cyan" if not row["needs_review"] else "yellow",
        padding=(0, 1),
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument(
        "--run-id",
        type=int,
        default=None,
        help="Limit to jobs first/re-discovered in a given search_run.",
    )
    p.add_argument(
        "--detail",
        action="store_true",
        help="Render one panel per job instead of a single summary table.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Dump rows as JSON (machine-readable).",
    )
    args = p.parse_args()

    conn = _connect(args.db)
    rows = _query(conn, args.run_id)

    if args.json:
        out = []
        for r in rows:
            d = dict(r)
            d["search_run_ids"] = _source_runs_for_job(conn, r["id"])
            out.append(d)
        print(json.dumps(out, indent=2, default=str))
        return 0

    console = Console()
    if not rows:
        target = f"run {args.run_id}" if args.run_id else "the database"
        console.print(f"[yellow]no jobs found in {target} ({args.db})[/yellow]")
        return 0

    console.print(
        f"[dim]db:[/dim] {args.db.resolve()}    "
        f"[dim]rows:[/dim] {len(rows)}"
        + (f"    [dim]filter:[/dim] run_id={args.run_id}" if args.run_id else "")
    )
    if args.detail:
        for r in rows:
            console.print(_format_detail(r, _source_runs_for_job(conn, r["id"])))
    else:
        console.print(_format_table(rows, conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
