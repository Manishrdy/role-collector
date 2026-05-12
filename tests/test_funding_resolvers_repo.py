"""Repo helpers for company resolution: update + list."""

from __future__ import annotations

from pathlib import Path

from job_agent.db import repo
from job_agent.db.migrate import migrate


def _seed(name: str, source_url: str) -> int:
    """Insert a funding event so the company shows up in list_unresolved."""
    return repo.upsert_funding_event(
        company_name=name, source_url=source_url
    ).company_id  # type: ignore[return-value]


def test_update_company_resolution_writes_only_provided_fields(
    isolated_db: Path,
) -> None:
    migrate()
    company_id = _seed("Acme", "https://hn.example/acme")
    assert company_id is not None
    repo.update_company_resolution(
        company_id=company_id,
        website_url="https://acme.io",
        careers_url="https://acme.io/careers",
    )
    rows = repo.list_unresolved_funding_companies()
    # Acme now has a website_url -> falls out of the unresolved list.
    assert all(r.company_id != company_id for r in rows)


def test_list_unresolved_excludes_companies_with_website(isolated_db: Path) -> None:
    migrate()
    a = _seed("Acme", "https://hn.example/a")
    b = _seed("Beta", "https://hn.example/b")
    assert a is not None and b is not None
    repo.update_company_resolution(company_id=a, website_url="https://acme.io")
    pending = repo.list_unresolved_funding_companies()
    ids = [r.company_id for r in pending]
    assert a not in ids
    assert b in ids


def test_list_unresolved_carries_latest_funding_source_url(isolated_db: Path) -> None:
    migrate()
    a = _seed("Acme", "https://hn.example/old")
    repo.upsert_funding_event(
        company_name="Acme", source_url="https://hn.example/new"
    )
    pending = repo.list_unresolved_funding_companies()
    found = next(r for r in pending if r.company_id == a)
    # Most-recent funding source should win.
    assert found.latest_funding_source_url == "https://hn.example/new"


def test_list_watchlist_returns_only_resolved_with_ats(isolated_db: Path) -> None:
    migrate()
    a = _seed("Acme", "https://hn.example/a")
    b = _seed("Beta", "https://hn.example/b")
    assert a is not None and b is not None
    # a has website but no ATS; b is fully resolved.
    repo.update_company_resolution(company_id=a, website_url="https://acme.io")
    repo.update_company_resolution(
        company_id=b,
        website_url="https://beta.io",
        careers_url="https://beta.io/careers",
        ats_type="greenhouse",
        ats_url="https://boards.greenhouse.io/beta",
    )
    watchlist = repo.list_watchlist_companies()
    ids = [r.company_id for r in watchlist]
    assert a not in ids
    assert b in ids


def test_update_with_no_fields_is_noop(isolated_db: Path) -> None:
    migrate()
    company_id = _seed("Acme", "https://hn.example/a")
    assert company_id is not None
    # Passing only the company_id (all field args None) should not error.
    repo.update_company_resolution(company_id=company_id)
    pending = repo.list_unresolved_funding_companies()
    assert any(r.company_id == company_id for r in pending)
