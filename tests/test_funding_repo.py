from __future__ import annotations

from pathlib import Path

from job_agent.db import repo
from job_agent.db.migrate import migrate


def test_upsert_inserts_funding_event(isolated_db: Path) -> None:
    migrate()
    result = repo.upsert_funding_event(
        company_name="Acme Inc",
        source_url="https://techcrunch.com/2026/05/01/acme-raises",
        round="Series A",
        amount="$25M",
        announced_date="2026-05-01",
        investors="Sequoia",
        raw_snippet="Acme raises $25M Series A led by Sequoia",
    )
    assert result.inserted is True
    assert result.funding_event_id > 0
    assert result.company_id is not None


def test_upsert_dedupes_on_company_and_source_url(isolated_db: Path) -> None:
    migrate()
    first = repo.upsert_funding_event(
        company_name="Acme Inc",
        source_url="https://techcrunch.com/2026/05/01/acme-raises",
        round="Series A",
        amount="$25M",
    )
    second = repo.upsert_funding_event(
        company_name="ACME inc",  # different casing but same normalized name
        source_url="https://techcrunch.com/2026/05/01/acme-raises",
        round="Series A",
        amount="$25M",
    )
    assert first.inserted is True
    assert second.inserted is False
    assert first.funding_event_id == second.funding_event_id


def test_upsert_does_not_dedupe_across_different_source_urls(isolated_db: Path) -> None:
    migrate()
    a = repo.upsert_funding_event(
        company_name="Acme",
        source_url="https://techcrunch.com/a",
    )
    b = repo.upsert_funding_event(
        company_name="Acme",
        source_url="https://hn.algolia.com/b",
    )
    # Same company, two different aggregator URLs = two events (audit trail).
    assert a.inserted is True
    assert b.inserted is True
    assert a.funding_event_id != b.funding_event_id


def test_upsert_creates_company_row(isolated_db: Path) -> None:
    migrate()
    result = repo.upsert_funding_event(
        company_name="NewCo",
        source_url="https://techcrunch.com/newco",
    )
    assert result.company_id is not None
    # Re-upserting another funding event for the same company reuses the row.
    result2 = repo.upsert_funding_event(
        company_name="NewCo",
        source_url="https://different.url/newco",
    )
    assert result2.company_id == result.company_id


def test_blank_inputs_raise() -> None:
    import pytest

    with pytest.raises(ValueError):
        repo.upsert_funding_event(company_name="", source_url="https://x")
    with pytest.raises(ValueError):
        repo.upsert_funding_event(company_name="Acme", source_url="")
