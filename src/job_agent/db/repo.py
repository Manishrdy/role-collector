"""Typed read/write helpers for SQLite. Connections are short-lived per call.

Phase-1 surface: search_runs, agent_events.
Phase-3 adds: companies upsert, jobs upsert with idempotency, job_sources, page_fetches.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from job_agent.config import load_config
from job_agent.extract.schema import ExtractedJob


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


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace. Used for company/title dedupe keys."""
    return re.sub(r"\s+", " ", text).strip().lower()


def upsert_company(
    *,
    name: str,
    ats_type: str | None = None,
    careers_url: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    """Insert or refresh a companies row keyed on normalized_name. Returns id."""
    normalized = _normalize(name)
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM companies WHERE normalized_name = ? LIMIT 1",
            (normalized,),
        ).fetchone()
        now = _utc_now_iso()
        if existing is not None:
            conn.execute(
                "UPDATE companies SET last_checked_at = ? WHERE id = ?",
                (now, existing["id"]),
            )
            return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO companies
                (name, normalized_name, ats_type, careers_url, first_seen_at, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, normalized, ats_type, careers_url, now, now),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        return new_id


@dataclass(frozen=True)
class UpsertResult:
    job_id: int
    inserted: bool


def upsert_job(
    *,
    extracted: ExtractedJob,
    canonical_url: str,
    raw_url: str,
    source_type: str,
    source_query: str | None,
    search_run_id: int | None,
    needs_review: bool,
    description_hash: str | None = None,
    description_embedding: list[float] | None = None,
    duplicate_status: str = "new",
    duplicate_of_job_id: int | None = None,
    duplicate_score: float | None = None,
    db_path: str | Path | None = None,
) -> UpsertResult:
    """Insert or update a jobs row.

    Idempotency: dedupe on ``(ats_type, ats_job_id)`` if both are present
    (most reliable, since the same posting can surface under different URLs),
    else fall back to ``canonical_url``.

    Always appends a ``job_sources`` row so we keep a full audit trail of
    every search run that re-discovered the posting.
    """
    company_id = upsert_company(
        name=extracted.company_name, ats_type=extracted.ats_type, db_path=db_path
    )
    normalized_company = _normalize(extracted.company_name)
    normalized_title = _normalize(extracted.title)
    skills_json = json.dumps(extracted.skills) if extracted.skills else None
    description = extracted.description or extracted.description_summary
    embedding_json = (
        json.dumps(description_embedding) if description_embedding else None
    )

    with connect(db_path) as conn:
        existing = None
        if extracted.ats_type and extracted.ats_job_id:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE ats_type = ? AND ats_job_id = ? LIMIT 1",
                (extracted.ats_type, extracted.ats_job_id),
            ).fetchone()
        if existing is None:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE canonical_url = ? LIMIT 1",
                (canonical_url,),
            ).fetchone()

        now = _utc_now_iso()
        if existing is not None:
            job_id = int(existing["id"])
            conn.execute(
                """
                UPDATE jobs
                   SET last_seen_at = ?,
                       updated_at = ?,
                       title = ?,
                       normalized_title = ?,
                       location = COALESCE(?, location),
                       remote_type = COALESCE(?, remote_type),
                       apply_url = COALESCE(?, apply_url),
                       posted_date = COALESCE(?, posted_date),
                       posted_date_confidence = COALESCE(?, posted_date_confidence),
                       description = COALESCE(?, description),
                       skills_json = COALESCE(?, skills_json),
                       seniority = COALESCE(?, seniority),
                       employment_type = COALESCE(?, employment_type),
                       salary_text = COALESCE(?, salary_text),
                       -- ATS fingerprints are immutable: once set, never overwrite.
                       -- COALESCE(existing, new) preserves the original and only
                       -- backfills when the existing column is NULL — useful when
                       -- an early row was saved before the parser learned to emit
                       -- the fingerprint.
                       ats_type = COALESCE(ats_type, ?),
                       ats_job_id = COALESCE(ats_job_id, ?),
                       description_hash = COALESCE(?, description_hash),
                       description_embedding_json = COALESCE(?, description_embedding_json),
                       duplicate_status = ?,
                       duplicate_of_job_id = ?,
                       duplicate_score = ?,
                       extraction_confidence = ?,
                       needs_review = ?
                 WHERE id = ?
                """,
                (
                    now,
                    now,
                    extracted.title,
                    normalized_title,
                    extracted.location,
                    extracted.remote_type,
                    extracted.apply_url,
                    extracted.posted_date,
                    extracted.posted_date_confidence,
                    description,
                    skills_json,
                    extracted.seniority,
                    extracted.employment_type,
                    extracted.salary_text,
                    extracted.ats_type,
                    extracted.ats_job_id,
                    description_hash,
                    embedding_json,
                    duplicate_status,
                    duplicate_of_job_id,
                    duplicate_score,
                    extracted.extraction_confidence,
                    1 if needs_review else 0,
                    job_id,
                ),
            )
            inserted = False
        else:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    company_id, company_name, normalized_company_name,
                    title, normalized_title,
                    location, remote_type,
                    canonical_url, apply_url,
                    ats_type, ats_job_id,
                    posted_date, posted_date_confidence,
                    first_seen_at, last_seen_at,
                    description, description_hash, description_embedding_json,
                    skills_json,
                    seniority, employment_type, salary_text,
                    source_type, source_query,
                    duplicate_status, duplicate_of_job_id, duplicate_score,
                    extraction_confidence, needs_review,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    company_id,
                    extracted.company_name,
                    normalized_company,
                    extracted.title,
                    normalized_title,
                    extracted.location,
                    extracted.remote_type,
                    canonical_url,
                    extracted.apply_url,
                    extracted.ats_type,
                    extracted.ats_job_id,
                    extracted.posted_date,
                    extracted.posted_date_confidence,
                    now,
                    now,
                    description,
                    description_hash,
                    embedding_json,
                    skills_json,
                    extracted.seniority,
                    extracted.employment_type,
                    extracted.salary_text,
                    source_type,
                    source_query,
                    duplicate_status,
                    duplicate_of_job_id,
                    duplicate_score,
                    extracted.extraction_confidence,
                    1 if needs_review else 0,
                    now,
                    now,
                ),
            )
            new_id = cur.lastrowid
            assert new_id is not None
            job_id = int(new_id)
            inserted = True

        conn.execute(
            """
            INSERT INTO job_sources
                (job_id, source_type, source_url, canonical_source_url, source_query,
                 search_run_id, found_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                source_type,
                raw_url,
                canonical_url,
                source_query,
                search_run_id,
                now,
            ),
        )

    return UpsertResult(job_id=job_id, inserted=inserted)


def find_job_by_description_hash(
    description_hash: str,
    *,
    db_path: str | Path | None = None,
) -> int | None:
    """Return the id of any job already stored with the given description hash."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE description_hash = ? LIMIT 1",
            (description_hash,),
        ).fetchone()
    return int(row["id"]) if row else None


def find_dedup_candidates(
    *,
    normalized_company_name: str,
    normalized_title: str,
    description_hash: str | None,
    days_back: int,
    exclude_job_id: int | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return candidate jobs for semantic comparison (design §18.5).

    Pre-filter: same normalised company OR same normalised title OR same
    description hash, restricted to jobs whose ``last_seen_at`` is within
    ``days_back`` days. Caller does the rapidfuzz-and-embedding scoring.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days_back)
    cutoff_iso = cutoff.isoformat(timespec="seconds")

    sql = """
        SELECT id, company_name, normalized_company_name,
               title, normalized_title,
               location, description, description_embedding_json,
               skills_json, ats_type, ats_job_id,
               canonical_url, last_seen_at
          FROM jobs
         WHERE last_seen_at >= ?
           AND (
                normalized_company_name = ?
             OR normalized_title = ?
             OR (description_hash IS NOT NULL AND description_hash = ?)
           )
    """
    params: list[Any] = [
        cutoff_iso,
        normalized_company_name,
        normalized_title,
        description_hash or "",
    ]
    if exclude_job_id is not None:
        sql += " AND id != ?"
        params.append(exclude_job_id)

    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "company_name": r["company_name"],
                "normalized_company_name": r["normalized_company_name"],
                "title": r["title"],
                "normalized_title": r["normalized_title"],
                "location": r["location"],
                "description": r["description"],
                "description_embedding_json": r["description_embedding_json"],
                "skills_json": r["skills_json"],
                "ats_type": r["ats_type"],
                "ats_job_id": r["ats_job_id"],
                "canonical_url": r["canonical_url"],
                "last_seen_at": r["last_seen_at"],
            }
        )
    return out


def persist_duplicate_candidate(
    *,
    job_id: int,
    candidate_job_id: int,
    duplicate_score: float,
    company_score: float,
    title_score: float,
    description_score: float,
    location_score: float,
    skills_score: float,
    decision: str,
    db_path: str | Path | None = None,
) -> int:
    """Append a duplicate_candidates audit row. Returns the new id."""
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO duplicate_candidates (
                job_id, candidate_job_id, duplicate_score,
                company_score, title_score, description_score,
                location_score, skills_score, decision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                candidate_job_id,
                duplicate_score,
                company_score,
                title_score,
                description_score,
                location_score,
                skills_score,
                decision,
                _utc_now_iso(),
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        return new_id


def touch_existing_job(
    *,
    job_id: int,
    raw_url: str,
    canonical_url: str,
    source_type: str,
    source_query: str | None,
    search_run_id: int | None,
    db_path: str | Path | None = None,
) -> None:
    """Mark an existing job as re-discovered (design §18.1 exact_duplicate).

    Bumps ``last_seen_at`` / ``updated_at`` and appends a ``job_sources``
    row. Used when an exact-duplicate (by description hash, canonical URL,
    or ATS fingerprint) is found and we don't want to insert a new
    ``jobs`` row.
    """
    with connect(db_path) as conn:
        now = _utc_now_iso()
        conn.execute(
            "UPDATE jobs SET last_seen_at = ?, updated_at = ? WHERE id = ?",
            (now, now, job_id),
        )
        conn.execute(
            """
            INSERT INTO job_sources
                (job_id, source_type, source_url, canonical_source_url, source_query,
                 search_run_id, found_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, source_type, raw_url, canonical_url, source_query, search_run_id, now),
        )


def record_page_fetch(
    *,
    url: str,
    canonical_url: str | None,
    domain: str | None,
    status: str,
    http_status: int | None,
    content_hash: str | None,
    detected_page_type: str | None,
    blocked_reason: str | None,
    search_run_id: int | None,
    db_path: str | Path | None = None,
) -> int:
    """Append a page_fetches audit row. Returns id."""
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO page_fetches
                (url, canonical_url, domain, fetched_at, status, http_status,
                 content_hash, detected_page_type, blocked_reason, search_run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                url,
                canonical_url,
                domain,
                _utc_now_iso(),
                status,
                http_status,
                content_hash,
                detected_page_type,
                blocked_reason,
                search_run_id,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        return new_id


@dataclass(frozen=True)
class FundingUpsertResult:
    funding_event_id: int
    company_id: int | None
    inserted: bool


def upsert_funding_event(
    *,
    company_name: str,
    source_url: str,
    round: str | None = None,
    amount: str | None = None,
    announced_date: str | None = None,
    investors: str | None = None,
    raw_snippet: str | None = None,
    db_path: str | Path | None = None,
) -> FundingUpsertResult:
    """Insert or refresh a funding_events row.

    Idempotency key is ``(normalized_company_name, source_url)``: a re-discovery
    by the same aggregator does not insert a new row. The companies row is
    upserted in the same transaction so a `company_id` is always set.
    """
    if not company_name.strip():
        raise ValueError("company_name is required")
    if not source_url.strip():
        raise ValueError("source_url is required")
    normalized = _normalize(company_name)
    company_id = upsert_company(name=company_name, db_path=db_path)
    with connect(db_path) as conn:
        now = _utc_now_iso()
        existing = conn.execute(
            "SELECT id FROM funding_events "
            "WHERE normalized_company_name = ? AND source_url = ? LIMIT 1",
            (normalized, source_url),
        ).fetchone()
        if existing is not None:
            return FundingUpsertResult(
                funding_event_id=int(existing["id"]),
                company_id=company_id,
                inserted=False,
            )
        cur = conn.execute(
            """
            INSERT INTO funding_events
                (company_id, company_name, normalized_company_name, round, amount,
                 announced_date, investors, source_url, found_at, raw_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                company_name,
                normalized,
                round,
                amount,
                announced_date,
                investors,
                source_url,
                now,
                raw_snippet,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        return FundingUpsertResult(
            funding_event_id=int(new_id),
            company_id=company_id,
            inserted=True,
        )


@dataclass(frozen=True)
class LinkedInPostUpsertResult:
    post_id: int
    inserted: bool


def upsert_linkedin_post(
    *,
    post_url: str,
    post_text: str,
    author_name: str | None = None,
    author_url: str | None = None,
    company_name: str | None = None,
    detected_role: str | None = None,
    confidence: float | None = None,
    source_query: str | None = None,
    db_path: str | Path | None = None,
) -> LinkedInPostUpsertResult:
    """Insert or refresh a linkedin_posts row.

    Idempotency on ``post_url`` (the table has a UNIQUE constraint there).
    Re-discovery of the same post no-ops and returns ``inserted=False``.
    """
    if not post_url.strip():
        raise ValueError("post_url is required")
    if not post_text.strip():
        raise ValueError("post_text is required")
    normalized_company = _normalize(company_name) if company_name else None
    with connect(db_path) as conn:
        now = _utc_now_iso()
        existing = conn.execute(
            "SELECT id FROM linkedin_posts WHERE post_url = ? LIMIT 1",
            (post_url,),
        ).fetchone()
        if existing is not None:
            return LinkedInPostUpsertResult(
                post_id=int(existing["id"]), inserted=False
            )
        cur = conn.execute(
            """
            INSERT INTO linkedin_posts
                (post_url, canonical_url, author_name, author_url, company_name,
                 normalized_company_name, detected_role, post_text, source_query,
                 confidence, found_at, processed_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                post_url,
                post_url,
                author_name,
                author_url,
                company_name,
                normalized_company,
                detected_role,
                post_text,
                source_query,
                confidence,
                now,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        return LinkedInPostUpsertResult(post_id=int(new_id), inserted=True)


@dataclass(frozen=True)
class CompanyResolutionRow:
    company_id: int
    name: str
    normalized_name: str
    website_url: str | None
    careers_url: str | None
    ats_type: str | None
    ats_url: str | None
    last_checked_at: str | None
    latest_funding_source_url: str | None = None
    last_polled_at: str | None = None


def bump_last_polled_at(
    *,
    company_id: int,
    db_path: str | Path | None = None,
) -> None:
    """Record that watchlist polled this company's board just now.

    Distinct from ``last_checked_at`` (resolver activity) so the watchlist
    can age out re-polls without skipping freshly-resolved companies.
    """
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE companies SET last_polled_at = ? WHERE id = ?",
            (_utc_now_iso(), company_id),
        )


def update_company_resolution(
    *,
    company_id: int,
    website_url: str | None = None,
    careers_url: str | None = None,
    ats_type: str | None = None,
    ats_url: str | None = None,
    notes: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Patch a companies row with resolver outputs. None-valued args are not
    overwritten, so resolvers can update only the fields they discovered.
    """
    fields: list[str] = []
    values: list[Any] = []
    if website_url is not None:
        fields.append("website_url = ?")
        values.append(website_url)
    if careers_url is not None:
        fields.append("careers_url = ?")
        values.append(careers_url)
    if ats_type is not None:
        fields.append("ats_type = ?")
        values.append(ats_type)
    if ats_url is not None:
        fields.append("ats_url = ?")
        values.append(ats_url)
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes)
    fields.append("last_checked_at = ?")
    values.append(_utc_now_iso())
    if not fields:
        return
    values.append(company_id)
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE companies SET {', '.join(fields)} WHERE id = ?",
            values,
        )


def list_unresolved_funding_companies(
    *,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> list[CompanyResolutionRow]:
    """Companies referenced by funding_events whose website_url is still NULL.

    Returns at most ``limit`` rows so a single agent run doesn't try to
    resolve hundreds of companies. Ordered by most-recently discovered
    funding event so fresh announcements win.
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.normalized_name, c.website_url, c.careers_url,
                   c.ats_type, c.ats_url, c.last_checked_at,
                   (SELECT source_url FROM funding_events
                    WHERE company_id = c.id
                    ORDER BY id DESC LIMIT 1) AS latest_funding_source_url
            FROM companies c
            WHERE c.website_url IS NULL
              AND c.notes IS NULL
              AND c.id IN (SELECT DISTINCT company_id FROM funding_events
                           WHERE company_id IS NOT NULL)
            ORDER BY (
              SELECT MAX(id) FROM funding_events WHERE company_id = c.id
            ) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        CompanyResolutionRow(
            company_id=int(r["id"]),
            name=r["name"],
            normalized_name=r["normalized_name"],
            website_url=r["website_url"],
            careers_url=r["careers_url"],
            ats_type=r["ats_type"],
            ats_url=r["ats_url"],
            last_checked_at=r["last_checked_at"],
            latest_funding_source_url=r["latest_funding_source_url"],
        )
        for r in rows
    ]


def list_watchlist_companies(
    *,
    min_idle_hours: float | None = None,
    db_path: str | Path | None = None,
) -> list[CompanyResolutionRow]:
    """Companies with a resolved ATS URL — these get re-polled for fresh jobs.

    When ``min_idle_hours`` is set, exclude rows whose ``last_polled_at`` is
    within that window (so a 6h re-poll cadence skips boards already
    polled in the last 6 hours). NULL ``last_polled_at`` (never polled)
    always passes the filter.
    """
    sql = """
        SELECT id, name, normalized_name, website_url, careers_url,
               ats_type, ats_url, last_checked_at, last_polled_at
        FROM companies
        WHERE ats_url IS NOT NULL
    """
    params: list[Any] = []
    if min_idle_hours is not None:
        sql += (
            " AND (last_polled_at IS NULL "
            "OR last_polled_at < datetime('now', ?))"
        )
        params.append(f"-{min_idle_hours} hours")
    sql += " ORDER BY last_polled_at ASC NULLS FIRST"
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        CompanyResolutionRow(
            company_id=int(r["id"]),
            name=r["name"],
            normalized_name=r["normalized_name"],
            website_url=r["website_url"],
            careers_url=r["careers_url"],
            ats_type=r["ats_type"],
            ats_url=r["ats_url"],
            last_checked_at=r["last_checked_at"],
            last_polled_at=r["last_polled_at"],
        )
        for r in rows
    ]


def set_duplicate_decision(
    *,
    job_id: int,
    decision: str,
    db_path: str | Path | None = None,
) -> None:
    """Record a human review decision for a `possible_duplicate` job.

    `decision='duplicate'` confirms the auto-detected match. `decision='new'`
    rejects it and clears `duplicate_of_job_id` / `duplicate_score` so the job
    is no longer paired in the dashboard.
    """
    if decision not in ("new", "duplicate"):
        raise ValueError(f"invalid decision: {decision!r}")
    with connect(db_path) as conn:
        now = _utc_now_iso()
        if decision == "duplicate":
            conn.execute(
                "UPDATE jobs SET duplicate_status = ?, updated_at = ? WHERE id = ?",
                (decision, now, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET duplicate_status = ?, duplicate_of_job_id = NULL, "
                "duplicate_score = NULL, updated_at = ? WHERE id = ?",
                (decision, now, job_id),
            )


def list_table_names(db_path: str | Path | None = None) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]
