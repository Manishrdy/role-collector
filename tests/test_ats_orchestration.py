from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from job_agent.browser.search_engines import SearchOutcome, SearchResult
from job_agent.config import (
    AppConfig,
    ATSGoogleSearchSection,
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
        limits=LimitsSection(
            min_delay_between_searches_seconds=0,
            max_delay_between_searches_seconds=0,
        ),
    )


def _mk_result(url: str, rank: int) -> SearchResult:
    return SearchResult(
        title=f"title {rank}",
        url=url,
        canonical_url=url,
        snippet="snip",
        rank=rank,
        engine="google",
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


def _drive_with_fake(
    *,
    cfg: AppConfig,
    plans: list[PlannedQuery],
    fake_run_query: Any,
) -> tuple[list[ats_search.CandidateURL], ats_search.OrchestrationStats]:
    """Run the async orchestrator core directly with a fake query function.

    Sidesteps nodriver entirely — tests only the orchestration logic.
    """

    async def go() -> tuple[list[ats_search.CandidateURL], ats_search.OrchestrationStats]:
        return await ats_search._drive(
            browser=object(),
            cfg=cfg,
            plans=plans,
            max_results_per_query=5,
            search_run_id=None,
            debug_dump_dir=None,
            sleep_async=lambda _: asyncio.sleep(0),
        )

    return asyncio.run(go())


def test_google_returns_results_no_block(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    """Happy path: Google returns results for all queries."""
    google_calls: list[str] = []

    async def fake_run_query(
        browser, *, query, time_window, target_domain, max_results, debug_dump_dir
    ):
        google_calls.append(query)
        return SearchOutcome(
            engine="google",
            query=query,
            time_window=time_window,
            results=[_mk_result(f"https://jobs.ashbyhq.com/acme/{len(google_calls)}", 1)],
        )

    monkeypatch.setattr(ats_search, "_run_query", fake_run_query)

    candidates, stats = _drive_with_fake(cfg=cfg, plans=_plans(3), fake_run_query=fake_run_query)
    assert len(google_calls) == 3
    assert stats.queries_succeeded == 3
    assert stats.queries_blocked == 0
    assert stats.google_blocked_at is None
    assert len(candidates) == 3


def test_google_block_stops_subsequent_queries(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    """Once Google blocks, the orchestrator stops asking — no fallback engine."""
    google_calls = 0

    async def fake_run_query(
        browser, *, query, time_window, target_domain, max_results, debug_dump_dir
    ):
        nonlocal google_calls
        google_calls += 1
        return SearchOutcome(
            engine="google",
            query=query,
            time_window=time_window,
            results=[],
            blocked_reason="google_block_redirect",
        )

    monkeypatch.setattr(ats_search, "_run_query", fake_run_query)

    candidates, stats = _drive_with_fake(cfg=cfg, plans=_plans(3), fake_run_query=fake_run_query)
    # Google is tried only once — once blocked, the rest are skipped.
    assert google_calls == 1
    assert stats.google_blocked_at == 1
    assert stats.queries_blocked == 3  # 1 actual block + 2 skipped
    assert stats.queries_succeeded == 0
    assert candidates == []


def test_dedup_across_queries(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    """Two queries hitting the same canonical URL produce one CandidateURL."""

    async def fake_run_query(
        browser, *, query, time_window, target_domain, max_results, debug_dump_dir
    ):
        return SearchOutcome(
            engine="google",
            query=query,
            time_window=time_window,
            results=[_mk_result("https://jobs.ashbyhq.com/acme/12345", 1)],
        )

    monkeypatch.setattr(ats_search, "_run_query", fake_run_query)

    candidates, stats = _drive_with_fake(cfg=cfg, plans=_plans(3), fake_run_query=fake_run_query)
    assert len(candidates) == 1
    assert stats.duplicate_results == 2
    assert stats.total_results == 1


def test_sleep_is_invoked_between_queries(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, isolated_db: Path
) -> None:
    """Sleep happens BETWEEN queries, not after the last one."""
    sleeps: list[float] = []

    async def fake_run_query(
        browser, *, query, time_window, target_domain, max_results, debug_dump_dir
    ):
        return SearchOutcome(engine="google", query=query, time_window=time_window, results=[])

    async def record_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(ats_search, "_run_query", fake_run_query)

    async def go() -> None:
        await ats_search._drive(
            browser=object(),
            cfg=cfg,
            plans=_plans(3),
            max_results_per_query=5,
            search_run_id=None,
            debug_dump_dir=None,
            sleep_async=record_sleep,
        )

    asyncio.run(go())
    assert len(sleeps) == 2
