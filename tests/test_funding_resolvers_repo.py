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


def test_list_watchlist_min_idle_hours_excludes_recently_polled(
    isolated_db: Path,
) -> None:
    migrate()
    a = _seed("Acme", "https://hn.example/a")
    assert a is not None
    repo.update_company_resolution(
        company_id=a,
        website_url="https://acme.io",
        ats_type="greenhouse",
        ats_url="https://boards.greenhouse.io/acme",
    )
    # Never-polled: passes any min_idle_hours filter (NULL last_polled_at).
    assert any(r.company_id == a for r in repo.list_watchlist_companies(min_idle_hours=6))
    # After we bump last_polled_at, the 6h filter should exclude it.
    repo.bump_last_polled_at(company_id=a)
    assert not any(r.company_id == a for r in repo.list_watchlist_companies(min_idle_hours=6))
    # A zero / very-short window still lets us re-poll.
    assert any(
        r.company_id == a for r in repo.list_watchlist_companies(min_idle_hours=None)
    )


def test_list_careers_only_returns_no_ats_with_careers(isolated_db: Path) -> None:
    """The careers-only cohort is the long tail: careers_url set, ats_url NULL."""
    migrate()
    a = _seed("Custom", "https://hn.example/a")
    b = _seed("WithATS", "https://hn.example/b")
    c = _seed("NoCareers", "https://hn.example/c")
    assert a is not None and b is not None and c is not None
    repo.update_company_resolution(
        company_id=a,
        website_url="https://custom.io",
        careers_url="https://custom.io/careers",
    )
    repo.update_company_resolution(
        company_id=b,
        website_url="https://withats.io",
        careers_url="https://withats.io/careers",
        ats_type="greenhouse",
        ats_url="https://boards.greenhouse.io/withats",
    )
    repo.update_company_resolution(company_id=c, website_url="https://nocareers.io")
    careers_only = [r.company_id for r in repo.list_careers_only_companies()]
    # Only the custom-careers company qualifies.
    assert a in careers_only
    assert b not in careers_only  # has ATS already
    assert c not in careers_only  # no careers_url


def test_list_careers_only_respects_min_idle_hours(isolated_db: Path) -> None:
    migrate()
    a = _seed("Custom", "https://hn.example/a")
    assert a is not None
    repo.update_company_resolution(
        company_id=a,
        website_url="https://custom.io",
        careers_url="https://custom.io/careers",
    )
    # Never polled -> passes any min_idle_hours filter.
    assert any(r.company_id == a for r in repo.list_careers_only_companies(min_idle_hours=6))
    repo.bump_last_polled_at(company_id=a)
    assert not any(r.company_id == a for r in repo.list_careers_only_companies(min_idle_hours=6))


def test_bump_last_polled_at_updates_column(isolated_db: Path) -> None:
    migrate()
    a = _seed("Acme", "https://hn.example/a")
    assert a is not None
    repo.update_company_resolution(
        company_id=a,
        website_url="https://acme.io",
        ats_type="greenhouse",
        ats_url="https://x",
    )
    pre = repo.list_watchlist_companies()[0]
    assert pre.last_polled_at is None
    repo.bump_last_polled_at(company_id=a)
    post = repo.list_watchlist_companies()[0]
    assert post.last_polled_at is not None


def test_update_with_no_fields_is_noop(isolated_db: Path) -> None:
    migrate()
    company_id = _seed("Acme", "https://hn.example/a")
    assert company_id is not None
    # Passing only the company_id (all field args None) should not error.
    repo.update_company_resolution(company_id=company_id)
    pending = repo.list_unresolved_funding_companies()
    assert any(r.company_id == company_id for r in pending)
