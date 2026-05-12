"""Resolver orchestrator end-to-end with mocked resolvers."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.db import repo
from job_agent.db.migrate import migrate
from job_agent.sources.funding.resolvers import orchestrator


def _seed(name: str, source_url: str) -> int:
    r = repo.upsert_funding_event(company_name=name, source_url=source_url)
    assert r.company_id is not None
    return r.company_id


def _stub_careers(url: str | None) -> object:
    def _impl(website, *args, **kwargs):  # type: ignore[no-untyped-def]
        return url

    return _impl


def _stub_ats(ats: tuple[str, str] | tuple[None, None]) -> object:
    def _impl(careers, *args, **kwargs):  # type: ignore[no-untyped-def]
        return ats

    return _impl


def test_cheap_path_resolves_and_persists(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    cid = _seed("Acme", "https://acme.io/blog/series-a")
    monkeypatch.setattr(
        orchestrator, "resolve_careers_url", _stub_careers("https://acme.io/careers")
    )
    monkeypatch.setattr(
        orchestrator,
        "resolve_ats",
        _stub_ats(("greenhouse", "https://boards.greenhouse.io/acme")),
    )
    stats = orchestrator.resolve_unresolved_companies()
    assert stats.companies_checked == 1
    assert stats.websites_resolved == 1
    assert stats.careers_resolved == 1
    assert stats.ats_resolved == 1
    # Company moved from unresolved to watchlist.
    watch = repo.list_watchlist_companies()
    assert any(r.company_id == cid for r in watch)


def test_vc_fund_skipped_when_cheap_path_fails(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    _seed("Sequoia Capital", "https://techcrunch.com/2026/sequoia-raises")
    monkeypatch.setattr(orchestrator, "resolve_careers_url", _stub_careers(None))
    monkeypatch.setattr(orchestrator, "resolve_ats", _stub_ats((None, None)))
    stats = orchestrator.resolve_unresolved_companies(enable_google_fallback=False)
    assert stats.companies_checked == 1
    assert stats.skipped_vc_funds == 1
    # No website, no careers — but the run logs the skip and moves on.
    assert stats.websites_resolved == 0
    assert stats.errors == []


def test_personal_name_skipped_when_cheap_path_fails(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    _seed("Katie Haun", "https://techcrunch.com/2026/katie-haun-fund")
    monkeypatch.setattr(orchestrator, "resolve_careers_url", _stub_careers(None))
    monkeypatch.setattr(orchestrator, "resolve_ats", _stub_ats((None, None)))
    stats = orchestrator.resolve_unresolved_companies(enable_google_fallback=False)
    assert stats.skipped_personal_names == 1


def test_company_with_careers_but_no_ats_still_records_website_and_careers(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    cid = _seed("Acme", "https://acme.io/blog")
    monkeypatch.setattr(
        orchestrator, "resolve_careers_url", _stub_careers("https://acme.io/careers")
    )
    monkeypatch.setattr(orchestrator, "resolve_ats", _stub_ats((None, None)))
    stats = orchestrator.resolve_unresolved_companies()
    assert stats.websites_resolved == 1
    assert stats.careers_resolved == 1
    assert stats.ats_resolved == 0
    # Not in watchlist (no ats_url) but website is recorded — so re-run
    # doesn't repeat the work.
    unresolved = repo.list_unresolved_funding_companies()
    assert all(r.company_id != cid for r in unresolved)


def test_step_exception_does_not_abort_other_companies(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    _seed("Acme", "https://acme.io/blog")
    _seed("Beta", "https://beta.io/blog")

    def _flaky_careers(website, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "acme.io" in website:
            raise RuntimeError("careers boom")
        return "https://beta.io/careers"

    monkeypatch.setattr(orchestrator, "resolve_careers_url", _flaky_careers)
    monkeypatch.setattr(
        orchestrator,
        "resolve_ats",
        _stub_ats(("lever", "https://jobs.lever.co/beta")),
    )
    stats = orchestrator.resolve_unresolved_companies()
    # 2 companies checked, 2 websites (cheap path) but only 1 careers + 1 ats.
    assert stats.companies_checked == 2
    assert stats.websites_resolved == 2
    assert stats.careers_resolved == 1
    assert stats.ats_resolved == 1
    assert len(stats.errors) == 1
    assert "careers" in stats.errors[0]


def test_empty_db_returns_zero_stats(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    stats = orchestrator.resolve_unresolved_companies()
    assert stats.companies_checked == 0
    assert stats.websites_resolved == 0


def test_vc_skip_persists_sentinel_so_rerun_doesnt_recheck(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once a VC fund is skipped, it should fall out of the unresolved
    list on subsequent runs — not get re-checked forever."""
    migrate()
    _seed("Sequoia Capital", "https://techcrunch.com/2026/sequoia-raises")
    monkeypatch.setattr(orchestrator, "resolve_careers_url", _stub_careers(None))
    monkeypatch.setattr(orchestrator, "resolve_ats", _stub_ats((None, None)))
    first = orchestrator.resolve_unresolved_companies(enable_google_fallback=False)
    assert first.companies_checked == 1
    assert first.skipped_vc_funds == 1
    # Second run: the sentinel excludes the row.
    second = orchestrator.resolve_unresolved_companies(enable_google_fallback=False)
    assert second.companies_checked == 0


def test_personal_skip_persists_sentinel(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    _seed("Katie Haun", "https://techcrunch.com/2026/katie-haun-fund")
    monkeypatch.setattr(orchestrator, "resolve_careers_url", _stub_careers(None))
    monkeypatch.setattr(orchestrator, "resolve_ats", _stub_ats((None, None)))
    orchestrator.resolve_unresolved_companies(enable_google_fallback=False)
    rerun = orchestrator.resolve_unresolved_companies(enable_google_fallback=False)
    assert rerun.companies_checked == 0
