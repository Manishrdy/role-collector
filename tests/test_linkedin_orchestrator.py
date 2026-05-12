"""Orchestrator end-to-end with mocked fetcher + search."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.browser.page_fetch import FetchedPage
from job_agent.config import AppConfig
from job_agent.db import repo
from job_agent.db.migrate import migrate
from job_agent.sources.ats_search import OrchestrationStats
from job_agent.sources.funding.schema import (
    FundingEventCandidate,  # noqa: F401  (avoid import-order issues)
)
from job_agent.sources.linkedin import orchestrator
from job_agent.sources.linkedin.fetcher import LinkedInFetchStats


def _cfg(*, enabled: bool = True, login_allowed: bool = False) -> AppConfig:
    return AppConfig.model_validate(
        {
            "search": {
                "time_windows": ["past_24h"],
                "roles": ["software engineer"],
                "locations": [],
                "max_queries_per_run": 5,
                "max_results_per_query": 5,
            },
            "sources": {
                "ats_google_search": {"enabled": True, "domains": []},
                "funding_discovery": {"enabled": False},
                "linkedin_public_search": {
                    "enabled": enabled,
                    "login_allowed": login_allowed,
                    "min_delay_per_post_seconds": 0.01,
                    "max_delay_per_post_seconds": 0.02,
                    "max_results_per_query": 5,
                },
            },
        }
    )


def _make_candidate(url: str) -> object:
    """Build a CandidateURL-shaped object that survives `run_ats_search`'s
    contract — we use a plain object since run_ats_search is monkeypatched."""
    from job_agent.sources.ats_search import CandidateURL

    return CandidateURL(
        url=url,
        canonical_url=url,
        title="post",
        snippet="",
        rank=1,
        engine="google",
        source_type="linkedin_public_search",
        source_query="x",
        target_domain="linkedin.com",
        ats_type="unknown",
        time_window="past_24h",
        role="",
        location=None,
    )


def _make_fetched_page(url: str, html: str, status: str = "ok") -> FetchedPage:
    return FetchedPage(
        url=url,
        canonical_url=url,
        final_url=url,
        domain="linkedin.com",
        page_title=None,
        http_status=200,
        html=html,
        content_hash="abc",
        fetched_at="2026-01-01T00:00:00+00:00",
        status=status,
    )


def test_disabled_short_circuits(isolated_db: Path) -> None:
    migrate()
    stats = orchestrator.discover_linkedin_posts(_cfg(enabled=False))
    assert stats.candidate_urls == 0
    assert stats.posts_inserted == 0


def test_login_allowed_rejected(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense-in-depth: if config is mistakenly flipped, abort the channel."""
    migrate()
    stats = orchestrator.discover_linkedin_posts(_cfg(login_allowed=True))
    assert stats.posts_inserted == 0
    assert any("login_allowed=True" in e for e in stats.errors)


def test_hiring_post_inserted_and_company_seeded(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()

    def fake_run_ats(**kwargs: object) -> tuple[list, OrchestrationStats]:
        return ([_make_candidate("https://linkedin.com/posts/jane-1")], OrchestrationStats())

    def fake_fetch_posts(urls, **kwargs):  # type: ignore[no-untyped-def]
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type":"SocialMediaPosting","articleBody":"We are hiring a Staff Engineer at Acme Inc! #hiring",'
            '"author":{"@type":"Person","name":"Jane Doe"}}'
            "</script></head></html>"
        )
        return ([_make_fetched_page(urls[0], html)], LinkedInFetchStats(urls_attempted=1, urls_ok=1))

    monkeypatch.setattr(orchestrator, "run_ats_search", fake_run_ats)
    monkeypatch.setattr(orchestrator, "fetch_linkedin_posts", fake_fetch_posts)

    stats = orchestrator.discover_linkedin_posts(_cfg())
    assert stats.candidate_urls == 1
    assert stats.posts_fetched == 1
    assert stats.posts_classified_hiring == 1
    assert stats.posts_inserted == 1
    assert stats.companies_seeded == 1
    # Verify the linkedin_posts row landed.
    import sqlite3

    with sqlite3.connect(repo.load_config().storage.sqlite_path) as conn:
        rows = conn.execute("SELECT post_url, company_name FROM linkedin_posts").fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "Acme"  # Inc stripped


def test_non_hiring_post_skipped(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()

    def fake_run_ats(**kwargs: object) -> tuple[list, OrchestrationStats]:
        return ([_make_candidate("https://linkedin.com/posts/coffee")], OrchestrationStats())

    def fake_fetch_posts(urls, **kwargs):  # type: ignore[no-untyped-def]
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type":"SocialMediaPosting","articleBody":"Just having coffee, lovely day",'
            '"author":{"@type":"Person","name":"Bob"}}'
            "</script></head></html>"
        )
        return ([_make_fetched_page(urls[0], html)], LinkedInFetchStats(urls_attempted=1, urls_ok=1))

    monkeypatch.setattr(orchestrator, "run_ats_search", fake_run_ats)
    monkeypatch.setattr(orchestrator, "fetch_linkedin_posts", fake_fetch_posts)

    stats = orchestrator.discover_linkedin_posts(_cfg())
    assert stats.posts_classified_hiring == 0
    assert stats.posts_inserted == 0


def test_captcha_stops_early(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate()

    def fake_run_ats(**kwargs: object) -> tuple[list, OrchestrationStats]:
        return ([_make_candidate("https://linkedin.com/posts/x")], OrchestrationStats())

    def fake_fetch_posts(urls, **kwargs):  # type: ignore[no-untyped-def]
        s = LinkedInFetchStats(
            urls_attempted=1, urls_blocked=1, stopped_early=True, stop_reason="block_signal:px-captcha"
        )
        return (
            [_make_fetched_page(urls[0], "", status="blocked")],
            s,
        )

    monkeypatch.setattr(orchestrator, "run_ats_search", fake_run_ats)
    monkeypatch.setattr(orchestrator, "fetch_linkedin_posts", fake_fetch_posts)

    stats = orchestrator.discover_linkedin_posts(_cfg())
    assert stats.stopped_early is True
    assert stats.stop_reason == "block_signal:px-captcha"
    assert stats.posts_inserted == 0


def test_no_linkedin_urls_in_results_skips_fetch(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Google returns nothing with linkedin.com in it, don't bother fetching."""
    migrate()

    def fake_run_ats(**kwargs: object) -> tuple[list, OrchestrationStats]:
        return ([_make_candidate("https://example.com/post")], OrchestrationStats())

    monkeypatch.setattr(orchestrator, "run_ats_search", fake_run_ats)
    # If fetch_linkedin_posts is called, fail loudly.
    monkeypatch.setattr(
        orchestrator,
        "fetch_linkedin_posts",
        lambda **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )

    stats = orchestrator.discover_linkedin_posts(_cfg())
    assert stats.candidate_urls == 0
    assert stats.posts_fetched == 0
