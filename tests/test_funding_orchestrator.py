from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.config import AppConfig
from job_agent.db.migrate import migrate
from job_agent.sources.funding import orchestrator
from job_agent.sources.funding.schema import FundingEventCandidate


def _cfg(*, hn: bool = True, tc: bool = False, google: bool = False) -> AppConfig:
    return AppConfig.model_validate(
        {
            "search": {
                "time_windows": ["past_week"],
                "roles": ["software engineer"],
                "locations": [],
                "max_queries_per_run": 5,
                "max_results_per_query": 5,
            },
            "sources": {
                "ats_google_search": {"enabled": True, "domains": []},
                "funding_discovery": {
                    "enabled": True,
                    "aggregators": {"hackernews": hn, "techcrunch": tc, "google": google},
                },
                "linkedin_public_search": {"enabled": False, "login_allowed": False},
            },
        }
    )


def _cand(name: str, url: str) -> FundingEventCandidate:
    return FundingEventCandidate(
        company_name=name,
        source_url=url,
        aggregator="hackernews",
        extraction_source="regex",
        extraction_confidence=0.85,
    )


def test_disabled_returns_empty_stats(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    cfg = AppConfig.model_validate(
        {
            "sources": {
                "ats_google_search": {"enabled": True, "domains": []},
                "funding_discovery": {"enabled": False},
                "linkedin_public_search": {"enabled": False, "login_allowed": False},
            },
        }
    )
    stats = orchestrator.discover_funding_events(cfg)
    assert stats.candidates_total == 0
    assert stats.candidates_inserted == 0


def test_runs_enabled_aggregators_and_persists(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()
    monkeypatch.setattr(
        orchestrator,
        "fetch_hackernews_funding",
        lambda: [_cand("Acme", "https://hn.example/a")],
    )
    stats = orchestrator.discover_funding_events(_cfg(hn=True))
    assert stats.candidates_total == 1
    assert stats.candidates_unique == 1
    assert stats.candidates_inserted == 1
    assert stats.by_aggregator == {"hackernews": 1}


def test_dedupes_across_aggregators(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same (company, source_url) seen by two aggregators counts once."""
    migrate()
    same = _cand("Acme", "https://shared.example/x")
    monkeypatch.setattr(orchestrator, "fetch_hackernews_funding", lambda: [same])
    monkeypatch.setattr(orchestrator, "fetch_techcrunch_funding", lambda: [same])
    stats = orchestrator.discover_funding_events(_cfg(hn=True, tc=True))
    assert stats.candidates_total == 2
    assert stats.candidates_unique == 1
    assert stats.candidates_inserted == 1


def test_aggregator_exception_does_not_abort_run(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken aggregator must not kill the run."""
    migrate()

    def _boom() -> list[FundingEventCandidate]:
        raise RuntimeError("HN exploded")

    monkeypatch.setattr(orchestrator, "fetch_hackernews_funding", _boom)
    monkeypatch.setattr(
        orchestrator,
        "fetch_techcrunch_funding",
        lambda: [_cand("Beta", "https://tc.example/b")],
    )
    stats = orchestrator.discover_funding_events(_cfg(hn=True, tc=True))
    assert stats.candidates_inserted == 1
    assert len(stats.errors) == 1
    assert "hackernews" in stats.errors[0]


def test_re_run_is_idempotent(isolated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migrate()
    monkeypatch.setattr(
        orchestrator,
        "fetch_hackernews_funding",
        lambda: [_cand("Acme", "https://hn.example/a")],
    )
    first = orchestrator.discover_funding_events(_cfg())
    second = orchestrator.discover_funding_events(_cfg())
    assert first.candidates_inserted == 1
    assert second.candidates_inserted == 0
    assert second.candidates_existing == 1
