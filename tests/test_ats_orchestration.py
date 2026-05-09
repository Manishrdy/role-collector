from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.browser.search_engines import SearchOutcome, SearchResult
from job_agent.config import (
    AppConfig,
    ATSGoogleSearchSection,
    BrowserSection,
    LimitsSection,
    SearchSection,
    SourcesSection,
)
from job_agent.sources import ats_search
from job_agent.sources.queries import PlannedQuery


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig(
        search=SearchSection(
            roles=["software engineer"],
            locations=[],
            time_windows=["past_24h"],
            max_results_per_query=5,
        ),
        sources=SourcesSection(
            ats_google_search=ATSGoogleSearchSection(enabled=True, domains=["jobs.ashbyhq.com"]),
        ),
        browser=BrowserSection(search_engine_order=["google", "bing"]),
        limits=LimitsSection(
            min_delay_between_searches_seconds=0,
            max_delay_between_searches_seconds=0,
        ),
    )


def _mk_result(url: str, rank: int, *, engine: str = "google") -> SearchResult:
    return SearchResult(
        title=f"title {rank}",
        url=url,
        canonical_url=url,
        snippet="snip",
        rank=rank,
        engine=engine,
        query="q",
        time_window="past_24h",
    )


def _plans(n: int) -> list[PlannedQuery]:
    return [
        PlannedQuery(
            query=f'site:jobs.ashbyhq.com "role-{i}"',
            time_window="past_24h",
            source_type="ats_google_search",
            target_domain="jobs.ashbyhq.com",
            ats_type="ashby",
            role=f"role-{i}",
        )
        for i in range(n)
    ]


def test_google_returns_results_no_block(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    """Happy path: Google returns results for all queries; Bing untouched."""
    google_calls: list[str] = []

    def fake_google(context, *, query, time_window, target_domain, max_results, **kw):
        google_calls.append(query)
        return SearchOutcome(
            engine="google",
            query=query,
            time_window=time_window,
            results=[_mk_result(f"https://jobs.ashbyhq.com/acme/{len(google_calls)}", 1)],
        )

    def fake_bing(*a, **kw):
        raise AssertionError("bing should not be called")

    monkeypatch.setattr(ats_search, "run_google_search", fake_google)
    monkeypatch.setattr(ats_search, "run_bing_search", fake_bing)

    candidates, stats = ats_search.run_ats_search(
        context=object(),  # type: ignore[arg-type]
        cfg=cfg,
        plans=_plans(3),
        max_results_per_query=5,
        sleep_fn=lambda _: None,
    )
    assert len(google_calls) == 3
    assert stats.queries_succeeded == 3
    assert stats.queries_blocked == 0
    assert stats.google_blocked_at is None
    assert len(candidates) == 3


def test_google_blocks_on_first_query_falls_back_to_bing(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    """When Google blocks, the orchestrator switches to Bing for the rest of the run."""
    google_calls = 0
    bing_calls = 0

    def fake_google(context, *, query, time_window, target_domain, max_results, **kw):
        nonlocal google_calls
        google_calls += 1
        return SearchOutcome(
            engine="google",
            query=query,
            time_window=time_window,
            results=[],
            blocked_reason="google_block_redirect",
        )

    def fake_bing(context, *, query, time_window, target_domain, max_results, **kw):
        nonlocal bing_calls
        bing_calls += 1
        return SearchOutcome(
            engine="bing",
            query=query,
            time_window=time_window,
            results=[_mk_result(f"https://jobs.ashbyhq.com/x/{bing_calls}", 1, engine="bing")],
        )

    monkeypatch.setattr(ats_search, "run_google_search", fake_google)
    monkeypatch.setattr(ats_search, "run_bing_search", fake_bing)

    candidates, stats = ats_search.run_ats_search(
        context=object(),  # type: ignore[arg-type]
        cfg=cfg,
        plans=_plans(3),
        max_results_per_query=5,
        sleep_fn=lambda _: None,
    )
    # Google is tried only once — once it's blocked we don't keep asking.
    assert google_calls == 1
    assert bing_calls == 3
    assert stats.google_blocked_at == 1
    assert stats.queries_succeeded == 3
    assert len(candidates) == 3
    assert all(c.engine == "bing" for c in candidates)


def test_dedup_across_queries(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    """Two queries hitting the same canonical URL produce one CandidateURL."""

    def fake_google(context, *, query, time_window, target_domain, max_results, **kw):
        return SearchOutcome(
            engine="google",
            query=query,
            time_window=time_window,
            results=[_mk_result("https://jobs.ashbyhq.com/acme/12345", 1)],
        )

    monkeypatch.setattr(ats_search, "run_google_search", fake_google)
    monkeypatch.setattr(ats_search, "run_bing_search", lambda *a, **kw: None)

    candidates, stats = ats_search.run_ats_search(
        context=object(),  # type: ignore[arg-type]
        cfg=cfg,
        plans=_plans(3),
        max_results_per_query=5,
        sleep_fn=lambda _: None,
    )
    assert len(candidates) == 1
    assert stats.duplicate_results == 2
    assert stats.total_results == 1


def test_both_engines_blocked_marks_query_failed(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    def blocked(engine: str):
        def fn(context, *, query, time_window, target_domain, max_results, **kw):
            return SearchOutcome(
                engine=engine,
                query=query,
                time_window=time_window,
                results=[],
                blocked_reason=f"{engine}_blocked",
            )

        return fn

    monkeypatch.setattr(ats_search, "run_google_search", blocked("google"))
    monkeypatch.setattr(ats_search, "run_bing_search", blocked("bing"))

    candidates, stats = ats_search.run_ats_search(
        context=object(),  # type: ignore[arg-type]
        cfg=cfg,
        plans=_plans(2),
        max_results_per_query=5,
        sleep_fn=lambda _: None,
    )
    assert candidates == []
    # Once both engines have blocked once, subsequent queries are skipped
    # entirely (engine_order is exhausted).
    assert stats.queries_blocked >= 1
    assert stats.bing_blocked is True
    assert stats.google_blocked_at == 1


def test_sleep_is_invoked_between_queries(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    sleeps: list[float] = []

    def fake_google(context, *, query, time_window, target_domain, max_results, **kw):
        return SearchOutcome(engine="google", query=query, time_window=time_window, results=[])

    monkeypatch.setattr(ats_search, "run_google_search", fake_google)
    monkeypatch.setattr(ats_search, "run_bing_search", lambda *a, **kw: None)

    ats_search.run_ats_search(
        context=object(),  # type: ignore[arg-type]
        cfg=cfg,
        plans=_plans(3),
        max_results_per_query=5,
        sleep_fn=sleeps.append,
    )
    # Sleep happens BETWEEN queries, not after the last one.
    assert len(sleeps) == 2
