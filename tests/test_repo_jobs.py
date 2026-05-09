from __future__ import annotations

import sqlite3
from pathlib import Path

from job_agent.db import repo
from job_agent.db.migrate import migrate
from job_agent.extract.schema import ExtractedJob


def _make_extracted(**overrides: object) -> ExtractedJob:
    base: dict[str, object] = {
        "title": "Senior Software Engineer",
        "company_name": "Acme",
        "location": "Remote",
        "remote_type": "remote",
        "ats_type": "ashby",
        "ats_job_id": "abc-123",
        "extraction_source": "ats_parser",
        "extraction_confidence": 0.95,
    }
    base.update(overrides)
    return ExtractedJob.model_validate(base)


def test_upsert_company_dedupes_on_normalized_name(isolated_db: Path) -> None:
    migrate()
    a = repo.upsert_company(name="Acme  Inc")
    b = repo.upsert_company(name="ACME inc")
    assert a == b


def test_upsert_job_inserts_then_updates_via_ats_id(isolated_db: Path) -> None:
    migrate()
    extracted = _make_extracted()
    first = repo.upsert_job(
        extracted=extracted,
        canonical_url="https://jobs.ashbyhq.com/acme/abc-123",
        raw_url="https://jobs.ashbyhq.com/acme/abc-123?utm_source=x",
        source_type="ats_google_search",
        source_query='site:jobs.ashbyhq.com intitle:"engineer"',
        search_run_id=None,
        needs_review=False,
    )
    assert first.inserted is True
    assert first.job_id > 0

    # Same posting re-discovered under a different surface URL — must dedupe
    # by (ats_type, ats_job_id) rather than canonical_url.
    second = repo.upsert_job(
        extracted=extracted,
        canonical_url="https://jobs.ashbyhq.com/acme/abc-123/different-slug",
        raw_url="https://jobs.ashbyhq.com/acme/abc-123/different-slug",
        source_type="ats_google_search",
        source_query="another query",
        search_run_id=None,
        needs_review=False,
    )
    assert second.inserted is False
    assert second.job_id == first.job_id

    with sqlite3.connect(isolated_db) as conn:
        rows = conn.execute("SELECT count(*) FROM jobs").fetchone()
        sources = conn.execute(
            "SELECT count(*) FROM job_sources WHERE job_id = ?", (first.job_id,)
        ).fetchone()
    assert rows[0] == 1
    assert sources[0] == 2


def test_upsert_job_falls_back_to_canonical_url_when_no_ats_id(isolated_db: Path) -> None:
    migrate()
    extracted = _make_extracted(ats_type=None, ats_job_id=None)
    a = repo.upsert_job(
        extracted=extracted,
        canonical_url="https://example.com/jobs/42",
        raw_url="https://example.com/jobs/42",
        source_type="dom",
        source_query=None,
        search_run_id=None,
        needs_review=False,
    )
    b = repo.upsert_job(
        extracted=extracted,
        canonical_url="https://example.com/jobs/42",
        raw_url="https://example.com/jobs/42",
        source_type="dom",
        source_query=None,
        search_run_id=None,
        needs_review=False,
    )
    assert a.inserted is True
    assert b.inserted is False
    assert a.job_id == b.job_id


def test_upsert_job_persists_needs_review_flag(isolated_db: Path) -> None:
    migrate()
    extracted = _make_extracted(extraction_confidence=0.2, extraction_source="llm")
    result = repo.upsert_job(
        extracted=extracted,
        canonical_url="https://example.com/jobs/low",
        raw_url="https://example.com/jobs/low",
        source_type="llm",
        source_query=None,
        search_run_id=None,
        needs_review=True,
    )
    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute(
            "SELECT needs_review, extraction_confidence FROM jobs WHERE id = ?",
            (result.job_id,),
        ).fetchone()
    assert row[0] == 1
    assert row[1] == 0.2


def test_upsert_job_backfills_ats_fingerprints(isolated_db: Path) -> None:
    """A row inserted before the parser knew the ATS fingerprint must get
    backfilled on the next upsert — without overwriting an already-set value."""
    migrate()
    no_fingerprint = _make_extracted(ats_type=None, ats_job_id=None)
    initial = repo.upsert_job(
        extracted=no_fingerprint,
        canonical_url="https://jobs.ashbyhq.com/acme/abc-123",
        raw_url="https://jobs.ashbyhq.com/acme/abc-123",
        source_type="ats_google_search",
        source_query=None,
        search_run_id=None,
        needs_review=False,
    )
    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute(
            "SELECT ats_type, ats_job_id FROM jobs WHERE id = ?", (initial.job_id,)
        ).fetchone()
    assert row == (None, None)

    # Re-extract the same posting, this time with fingerprints populated.
    enriched = _make_extracted(ats_type="ashby", ats_job_id="abc-123")
    repeat = repo.upsert_job(
        extracted=enriched,
        canonical_url="https://jobs.ashbyhq.com/acme/abc-123",
        raw_url="https://jobs.ashbyhq.com/acme/abc-123",
        source_type="ats_google_search",
        source_query=None,
        search_run_id=None,
        needs_review=False,
    )
    assert repeat.inserted is False
    assert repeat.job_id == initial.job_id
    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute(
            "SELECT ats_type, ats_job_id FROM jobs WHERE id = ?", (initial.job_id,)
        ).fetchone()
    assert row == ("ashby", "abc-123")

    # Subsequent upserts must NOT overwrite an already-set fingerprint, even
    # if the new extraction reports something different (defensive: prevents
    # accidental ID churn from a flaky parser).
    drift = _make_extracted(ats_type="lever", ats_job_id="zzz-999")
    repo.upsert_job(
        extracted=drift,
        canonical_url="https://jobs.ashbyhq.com/acme/abc-123",
        raw_url="https://jobs.ashbyhq.com/acme/abc-123",
        source_type="ats_google_search",
        source_query=None,
        search_run_id=None,
        needs_review=False,
    )
    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute(
            "SELECT ats_type, ats_job_id FROM jobs WHERE id = ?", (initial.job_id,)
        ).fetchone()
    assert row == ("ashby", "abc-123")


def test_record_page_fetch_inserts_row(isolated_db: Path) -> None:
    migrate()
    fetch_id = repo.record_page_fetch(
        url="https://jobs.ashbyhq.com/acme/abc-123",
        canonical_url="https://jobs.ashbyhq.com/acme/abc-123",
        domain="jobs.ashbyhq.com",
        status="ok",
        http_status=200,
        content_hash="deadbeef",
        detected_page_type="job_detail",
        blocked_reason=None,
        search_run_id=None,
    )
    assert fetch_id > 0
