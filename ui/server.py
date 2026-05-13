"""FastAPI dashboard for the job-sourcing pipeline.

Replaces the Streamlit dashboard with a vanilla-frontend setup:
- Jinja2 server-side templates served from ``ui/templates``.
- Plain CSS + JS in ``ui/static``.
- JSON REST endpoints under ``/api`` for the JS to consume.

Launched via:
    uvicorn ui.server:app --reload --port 8501

(Makefile target ``make review`` wraps this.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from job_agent.config import load_config
from job_agent.db import repo

UI_ROOT = Path(__file__).resolve().parent

app = FastAPI(title="Role Collector — job-sourcing pipeline dashboard")
app.mount("/static", StaticFiles(directory=UI_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(UI_ROOT / "templates"))


# ---------------------------------------------------------------------------
# DB helpers


def _db_path() -> Path:
    return Path(load_config().storage.sqlite_path)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    # sqlite3.Row supports key-iteration; keep it simple.
    return {k: row[k] for k in row.keys()}  # noqa: SIM118 — sqlite3.Row has no __iter__ semantics like dict


# ---------------------------------------------------------------------------
# Pages — return server-rendered HTML shells. The JS in app.js fills them via /api.


@app.get("/", response_class=HTMLResponse)
def page_home(request: Request) -> HTMLResponse:
    # Jobs is the landing page; no separate overview.
    return templates.TemplateResponse(request, "jobs.html", {"page": "jobs"})


@app.get("/jobs", response_class=HTMLResponse)
def page_jobs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "jobs.html", {"page": "jobs"})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def page_job_detail(request: Request, job_id: int) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "job_detail.html", {"page": "jobs", "job_id": job_id}
    )


@app.get("/dedup", response_class=HTMLResponse)
def page_dedup(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dedup.html", {"page": "dedup"})


@app.get("/discovery", response_class=HTMLResponse)
def page_discovery(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "discovery.html", {"page": "discovery"})


@app.get("/runs", response_class=HTMLResponse)
def page_runs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "runs.html", {"page": "runs"})


# ---------------------------------------------------------------------------
# JSON API


@app.get("/api/overview")
def api_overview() -> JSONResponse:
    with _connect() as conn:
        totals = conn.execute(
            """
            SELECT
              COUNT(*) AS jobs,
              SUM(CASE WHEN duplicate_status='new' THEN 1 ELSE 0 END) AS new,
              SUM(CASE WHEN duplicate_status='possible_duplicate' THEN 1 ELSE 0 END) AS possible,
              SUM(CASE WHEN duplicate_status='duplicate' THEN 1 ELSE 0 END) AS dup,
              SUM(needs_review) AS needs_review
            FROM jobs
            """
        ).fetchone()
        runs_n = conn.execute("SELECT COUNT(*) AS n FROM search_runs").fetchone()["n"]
        funding_n = conn.execute("SELECT COUNT(*) AS n FROM funding_events").fetchone()["n"]
        linkedin_n = conn.execute(
            "SELECT COUNT(*) AS n FROM linkedin_posts WHERE post_url IS NOT NULL"
        ).fetchone()["n"]
        companies_n = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
        watchlist_n = conn.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE ats_url IS NOT NULL"
        ).fetchone()["n"]
        latest = conn.execute(
            """
            SELECT id, company_name, title, location, location_normalized_json,
                   role_family, role_match_status, level,
                   ats_type, duplicate_status, first_seen_at, apply_url, canonical_url,
                   posted_at_source, observed_at, freshness_bucket
            FROM jobs
            ORDER BY id DESC
            LIMIT 10
            """
        ).fetchall()
    return JSONResponse(
        {
            "totals": {
                "jobs": int(totals["jobs"] or 0),
                "new": int(totals["new"] or 0),
                "possible": int(totals["possible"] or 0),
                "dup": int(totals["dup"] or 0),
                "needs_review": int(totals["needs_review"] or 0),
                "runs": int(runs_n),
                "funding": int(funding_n),
                "linkedin": int(linkedin_n),
                "companies": int(companies_n),
                "watchlist": int(watchlist_n),
            },
            "latest": [_row_to_dict(r) for r in latest],
            "db_path": str(_db_path()),
        }
    )


@app.get("/api/jobs")
def api_jobs(
    q: str | None = None,
    dup_status: str | None = None,
    ats_type: str | None = None,
    remote_type: str | None = None,
    freshness_bucket: str | None = None,
    fresh_24h_only: bool | None = None,
    needs_review: bool | None = None,
    page: int = 1,
    limit: int = 50,
) -> JSONResponse:
    where: list[str] = []
    params: list[Any] = []
    if q:
        where.append("(LOWER(company_name) LIKE ? OR LOWER(title) LIKE ?)")
        needle = f"%{q.lower()}%"
        params.extend([needle, needle])
    if dup_status:
        where.append("duplicate_status = ?")
        params.append(dup_status)
    if ats_type:
        where.append("ats_type = ?")
        params.append(ats_type)
    if remote_type:
        where.append("remote_type = ?")
        params.append(remote_type)
    if freshness_bucket:
        where.append("freshness_bucket = ?")
        params.append(freshness_bucket)
    if fresh_24h_only:
        where.append("freshness_bucket = 'lt_24h'")
    if needs_review:
        where.append("needs_review = 1")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    offset = max(0, (page - 1) * limit)
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM jobs {where_sql}", params).fetchone()["n"]
        rows = conn.execute(
            f"""
            SELECT id, company_name, title, location, location_normalized_json,
                   role_family, role_match_status, level,
                   remote_type, ats_type,
                   duplicate_status, needs_review, first_seen_at, apply_url,
                   posted_at_source, observed_at, freshness_bucket,
                   canonical_url
            FROM jobs
            {where_sql}
            ORDER BY
              CASE freshness_bucket
                WHEN 'lt_24h' THEN 0
                WHEN '24_72h' THEN 1
                WHEN 'gt_72h' THEN 2
                ELSE 3
              END ASC,
              posted_at_source DESC,
              first_seen_at DESC,
              id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        # Distinct values for filter dropdowns.
        ats_options = [
            r["ats_type"]
            for r in conn.execute(
                "SELECT DISTINCT ats_type FROM jobs WHERE ats_type IS NOT NULL ORDER BY ats_type"
            ).fetchall()
        ]
        dup_options = [
            r["duplicate_status"]
            for r in conn.execute(
                "SELECT DISTINCT duplicate_status FROM jobs ORDER BY duplicate_status"
            ).fetchall()
        ]
        remote_options = [
            r["remote_type"]
            for r in conn.execute(
                "SELECT DISTINCT remote_type FROM jobs WHERE remote_type IS NOT NULL ORDER BY remote_type"
            ).fetchall()
        ]
        freshness_options = [
            r["freshness_bucket"]
            for r in conn.execute(
                "SELECT DISTINCT freshness_bucket FROM jobs WHERE freshness_bucket IS NOT NULL ORDER BY freshness_bucket"
            ).fetchall()
            if r["freshness_bucket"] is not None
        ]
    return JSONResponse(
        {
            "total": int(total),
            "page": page,
            "limit": limit,
            "rows": [_row_to_dict(r) for r in rows],
            "filters": {
                "ats_type": ats_options,
                "dup_status": dup_options,
                "remote_type": remote_options,
                "freshness_bucket": freshness_options,
            },
        }
    )


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: int) -> JSONResponse:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        sources = conn.execute(
            """
            SELECT source_type, batch_id, source_url, canonical_source_url, source_query, found_at
            FROM job_sources WHERE job_id = ? ORDER BY id DESC
            """,
            (job_id,),
        ).fetchall()
        # Score breakdown if it's a possible-duplicate.
        breakdown = None
        if row["duplicate_status"] in ("possible_duplicate", "duplicate") and row["duplicate_of_job_id"]:
            breakdown = conn.execute(
                """
                SELECT company_score, title_score, description_score, location_score,
                       skills_score, duplicate_score, decision
                FROM duplicate_candidates
                WHERE job_id = ? AND candidate_job_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (job_id, row["duplicate_of_job_id"]),
            ).fetchone()
    return JSONResponse(
        {
            "job": _row_to_dict(row),
            "sources": [_row_to_dict(r) for r in sources],
            "dedup_breakdown": _row_to_dict(breakdown) if breakdown else None,
        }
    )


@app.get("/api/dedup-pairs")
def api_dedup_pairs() -> JSONResponse:
    with _connect() as conn:
        possibles = conn.execute(
            """
            SELECT id, company_name, title, location, ats_type, apply_url,
                   description, duplicate_of_job_id, duplicate_score, first_seen_at
            FROM jobs
            WHERE duplicate_status = 'possible_duplicate'
            ORDER BY duplicate_score DESC, id DESC
            """
        ).fetchall()
        pairs: list[dict[str, Any]] = []
        for p in possibles:
            match_id = p["duplicate_of_job_id"]
            match = None
            breakdown = None
            if match_id is not None:
                match_row = conn.execute(
                    """
                    SELECT id, company_name, title, location, ats_type, apply_url,
                           description, first_seen_at
                    FROM jobs WHERE id = ?
                    """,
                    (match_id,),
                ).fetchone()
                if match_row is not None:
                    match = _row_to_dict(match_row)
                breakdown_row = conn.execute(
                    """
                    SELECT company_score, title_score, description_score,
                           location_score, skills_score, duplicate_score, decision
                    FROM duplicate_candidates
                    WHERE job_id = ? AND candidate_job_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (p["id"], match_id),
                ).fetchone()
                if breakdown_row is not None:
                    breakdown = _row_to_dict(breakdown_row)
            pairs.append(
                {
                    "new": _row_to_dict(p),
                    "match": match,
                    "breakdown": breakdown,
                }
            )
    return JSONResponse({"pairs": pairs})


class DedupDecisionPayload(BaseModel):
    job_id: int
    decision: str  # "duplicate" | "new"


@app.post("/api/dedup-decision")
def api_dedup_decision(payload: DedupDecisionPayload) -> JSONResponse:
    if payload.decision not in ("duplicate", "new"):
        raise HTTPException(status_code=400, detail="decision must be 'duplicate' or 'new'")
    try:
        repo.set_duplicate_decision(job_id=payload.job_id, decision=payload.decision)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"persist failed: {e}") from e
    return JSONResponse({"ok": True, "job_id": payload.job_id, "decision": payload.decision})


@app.get("/api/discovery")
def api_discovery() -> JSONResponse:
    with _connect() as conn:
        funding = conn.execute(
            """
            SELECT id, company_name, round, amount, announced_date, source_url,
                   investors, found_at
            FROM funding_events ORDER BY id DESC LIMIT 100
            """
        ).fetchall()
        linkedin = conn.execute(
            """
            SELECT id, author_name, company_name, detected_role, role_family,
                   role_match_status, level, level_confidence, extraction_source,
                   post_url, confidence, processed_status, found_at
            FROM linkedin_posts ORDER BY id DESC LIMIT 100
            """
        ).fetchall()
        watchlist = conn.execute(
            """
            SELECT id, name, website_url, careers_url, ats_type, ats_url,
                   last_checked_at, last_polled_at
            FROM companies
            WHERE ats_url IS NOT NULL
            ORDER BY last_polled_at ASC NULLS FIRST
            """
        ).fetchall()
    return JSONResponse(
        {
            "funding": [_row_to_dict(r) for r in funding],
            "linkedin": [_row_to_dict(r) for r in linkedin],
            "watchlist": [_row_to_dict(r) for r in watchlist],
        }
    )


@app.get("/api/runs")
def api_runs() -> JSONResponse:
    with _connect() as conn:
        runs = conn.execute(
            """
            SELECT id, source_type, status, started_at, finished_at, query,
                   time_window, error_message
            FROM search_runs ORDER BY id DESC LIMIT 100
            """
        ).fetchall()
        events = conn.execute(
            """
            SELECT id, search_run_id, event_type, event_message, created_at
            FROM agent_events ORDER BY id DESC LIMIT 300
            """
        ).fetchall()
        fetches = conn.execute(
            """
            SELECT id, search_run_id, status, http_status, domain,
                   detected_page_type, blocked_reason, fetched_at, url
            FROM page_fetches ORDER BY id DESC LIMIT 200
            """
        ).fetchall()
        cycles = conn.execute(
            """
            SELECT id, search_run_id, status, started_at, finished_at,
                   sleep_until, error_message, summary_json
            FROM agent_cycles ORDER BY id DESC LIMIT 100
            """
        ).fetchall()
        batches = conn.execute(
            """
            SELECT id, search_run_id, agent_cycle_id, status, created_at,
                   flushed_at, job_count, metadata_json
            FROM job_batches ORDER BY id DESC LIMIT 100
            """
        ).fetchall()
        source_stats = conn.execute(
            """
            SELECT source_name, last_status, last_started_at, last_finished_at,
                   backoff_until, runs_total, successes_total, failures_total,
                   candidates_total, jobs_saved_total, metadata_json
            FROM agent_source_stats ORDER BY source_name
            """
        ).fetchall()
        tool_calls = conn.execute(
            """
            SELECT id, agent_cycle_id, tool_name, source_name, status,
                   latency_ms, error_message, created_at
            FROM agent_tool_calls ORDER BY id DESC LIMIT 200
            """
        ).fetchall()
    return JSONResponse(
        {
            "runs": [_row_to_dict(r) for r in runs],
            "events": [_row_to_dict(r) for r in events],
            "fetches": [_row_to_dict(r) for r in fetches],
            "cycles": [_row_to_dict(r) for r in cycles],
            "batches": [_row_to_dict(r) for r in batches],
            "source_stats": [_row_to_dict(r) for r in source_stats],
            "tool_calls": [_row_to_dict(r) for r in tool_calls],
        }
    )


@app.get("/api/runs/{cycle_id}/react-trace")
def api_run_react_trace(cycle_id: int) -> JSONResponse:
    with _connect() as conn:
        cycle = conn.execute(
            """
            SELECT id, search_run_id, status, started_at, finished_at, summary_json
            FROM agent_cycles
            WHERE id = ?
            """,
            (cycle_id,),
        ).fetchone()
        if cycle is None:
            raise HTTPException(status_code=404, detail="cycle not found")
        tool_calls = conn.execute(
            """
            SELECT id, tool_name, source_name, status, latency_ms, error_message, created_at
            FROM agent_tool_calls
            WHERE agent_cycle_id = ?
            ORDER BY id ASC
            """,
            (cycle_id,),
        ).fetchall()
    cycle_row = _row_to_dict(cycle)
    summary = repo.safe_json_loads(cycle_row.get("summary_json")) or {}
    react_trace = summary.get("react_trace", [])
    if not isinstance(react_trace, list):
        react_trace = []
    return JSONResponse(
        {
            "cycle": cycle_row,
            "react_trace": react_trace,
            "tool_calls": [_row_to_dict(r) for r in tool_calls],
        }
    )
