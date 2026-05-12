"""ATS Google search orchestrator (Phase 2, nodriver-based).

Drives a list of ``PlannedQuery`` through Google via ``nodriver`` and
returns deduplicated ``CandidateURL`` records ready for Phase-3 page
extraction.

Design notes:

- A single nodriver browser is started for the whole run and reused
  across queries. Restarting per query is slow and would lose any
  cookies that help avoid re-challenges.
- We dropped the multi-engine fallback machinery: Bing returned 0
  organic results for every test query, and DuckDuckGo also failed for
  the user's manual sanity checks. Google via nodriver is the only
  engine that works.
- Block detection still runs every query so we can stop a run cleanly
  if Google's tolerance changes.
- Per-query rate limiting and cross-query deduplication are unchanged
  from the Playwright version.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_agent.browser.search_engines import (
    SearchOutcome,
    SearchResult,
    run_google_search_async,
)
from job_agent.config import AppConfig
from job_agent.db import repo
from job_agent.sources.queries import PlannedQuery

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateURL:
    """A deduplicated search result ready for Phase-3 extraction."""

    url: str
    canonical_url: str
    title: str
    snippet: str
    rank: int
    engine: str
    source_type: str
    source_query: str
    target_domain: str
    ats_type: str
    time_window: str
    role: str
    location: str | None


@dataclass
class OrchestrationStats:
    queries_attempted: int = 0
    queries_succeeded: int = 0
    queries_blocked: int = 0
    google_blocked_at: int | None = None  # 1-based query index when google first blocked
    self_stopped_at: int | None = None  # 1-based query index when self-stop cap was hit
    total_results: int = 0
    duplicate_results: int = 0


# Functions injected into the async batch so tests can replace them with
# fakes without touching nodriver. The orchestrator looks them up via the
# module so monkeypatch.setattr(ats_search, "_run_query", fake) works.
async def _run_query(
    browser: Any,
    *,
    query: str,
    time_window: str,
    target_domain: str | None,
    max_results: int,
    debug_dump_dir: Path | None,
) -> SearchOutcome:
    return await run_google_search_async(
        browser,
        query=query,
        time_window=time_window,  # type: ignore[arg-type]
        target_domain=target_domain,
        max_results=max_results,
        debug_dump_dir=debug_dump_dir,
    )


def _to_candidate(r: SearchResult, plan: PlannedQuery) -> CandidateURL:
    return CandidateURL(
        url=r.url,
        canonical_url=r.canonical_url,
        title=r.title,
        snippet=r.snippet,
        rank=r.rank,
        engine=r.engine,
        source_type=plan.source_type,
        source_query=plan.query,
        target_domain=plan.target_domain,
        ats_type=plan.ats_type,
        time_window=plan.time_window,
        role=plan.role,
        location=plan.location,
    )


def _delay_seconds(cfg: AppConfig) -> float:
    lo = cfg.limits.min_delay_between_searches_seconds
    hi = cfg.limits.max_delay_between_searches_seconds
    return random.uniform(lo, hi)


def _log_event(
    search_run_id: int | None,
    event_type: str,
    message: str,
    safe_metadata: dict[str, Any],
) -> None:
    log.info("[%s] %s", event_type, message)
    if search_run_id is not None:
        repo.log_agent_event(
            search_run_id=search_run_id,
            event_type=event_type,
            message=message,
            safe_metadata=safe_metadata,
        )


async def _drive(
    *,
    browser: Any,
    cfg: AppConfig,
    plans: list[PlannedQuery],
    max_results_per_query: int,
    search_run_id: int | None,
    debug_dump_dir: Path | None,
    sleep_async: Callable[[float], Any],
) -> tuple[list[CandidateURL], OrchestrationStats]:
    """Async core: iterate plans through nodriver, dedupe results."""
    seen: dict[str, CandidateURL] = {}
    stats = OrchestrationStats()
    google_blocked = False
    self_stop_cap = cfg.sources.ats_google_search.max_queries_before_self_stop
    self_stopped = False

    for idx, plan in enumerate(plans, start=1):
        stats.queries_attempted += 1
        if google_blocked or self_stopped:
            # Skip remaining queries — either Google blocked us, or we
            # hit the self-imposed cap to stay under the threshold.
            stats.queries_blocked += 1
            reason = "google blocked earlier" if google_blocked else "self-stop cap reached"
            _log_event(
                search_run_id,
                "search_skipped",
                f"{reason}; skipping query {idx}",
                {"query": plan.query, "time_window": plan.time_window},
            )
            continue

        log.info("[%d/%d] google: %s", idx, len(plans), plan.query)
        outcome = await _run_query(
            browser,
            query=plan.query,
            time_window=plan.time_window,
            target_domain=plan.target_domain,
            max_results=max_results_per_query,
            debug_dump_dir=debug_dump_dir,
        )

        if outcome.blocked:
            google_blocked = True
            stats.queries_blocked += 1
            stats.google_blocked_at = idx
            _log_event(
                search_run_id,
                "search_blocked",
                f"google blocked on query {idx}",
                {
                    "query": plan.query,
                    "time_window": plan.time_window,
                    "blocked_reason": outcome.blocked_reason,
                },
            )
        else:
            stats.queries_succeeded += 1
            new_count = 0
            dup_count = 0
            for r in outcome.results:
                cand = _to_candidate(r, plan)
                if cand.canonical_url in seen:
                    dup_count += 1
                    continue
                seen[cand.canonical_url] = cand
                new_count += 1
            stats.total_results += new_count
            stats.duplicate_results += dup_count
            _log_event(
                search_run_id,
                "search_results",
                f"google returned {len(outcome.results)} ({new_count} new)",
                {
                    "query": plan.query,
                    "time_window": plan.time_window,
                    "results_total": len(outcome.results),
                    "results_new": new_count,
                    "results_duplicate": dup_count,
                },
            )

        # Self-stop check: if we've succeeded N times, voluntarily stop
        # before Google rate-limits us.
        if (
            self_stop_cap > 0
            and stats.queries_succeeded >= self_stop_cap
            and not google_blocked
        ):
            self_stopped = True
            stats.self_stopped_at = idx
            _log_event(
                search_run_id,
                "search_self_stopped",
                f"self-stop cap of {self_stop_cap} hit after query {idx}",
                {"queries_succeeded": stats.queries_succeeded},
            )

        if idx < len(plans) and not google_blocked and not self_stopped:
            delay = _delay_seconds(cfg)
            log.info("sleeping %.1fs between queries", delay)
            await sleep_async(delay)

    return list(seen.values()), stats


def run_ats_search(
    *,
    cfg: AppConfig,
    plans: list[PlannedQuery],
    max_results_per_query: int,
    search_run_id: int | None = None,
    debug_dump_dir: Path | None = None,
) -> tuple[list[CandidateURL], OrchestrationStats]:
    """Sync entrypoint. Spins up a nodriver session and runs all queries.

    nodriver is async-only; this wraps the async core in ``asyncio.run``
    so the LangGraph node can stay synchronous.
    """
    if not plans:
        return [], OrchestrationStats()

    return asyncio.run(
        _run_ats_search_async(
            cfg=cfg,
            plans=plans,
            max_results_per_query=max_results_per_query,
            search_run_id=search_run_id,
            debug_dump_dir=debug_dump_dir,
        )
    )


async def _run_ats_search_async(
    *,
    cfg: AppConfig,
    plans: list[PlannedQuery],
    max_results_per_query: int,
    search_run_id: int | None,
    debug_dump_dir: Path | None,
) -> tuple[list[CandidateURL], OrchestrationStats]:
    import nodriver as uc

    browser_kwargs: dict[str, Any] = {"headless": cfg.browser.headless}

    log.info("starting nodriver session for %d queries", len(plans))
    browser = await uc.start(**browser_kwargs)
    try:
        return await _drive(
            browser=browser,
            cfg=cfg,
            plans=plans,
            max_results_per_query=max_results_per_query,
            search_run_id=search_run_id,
            debug_dump_dir=debug_dump_dir,
            sleep_async=asyncio.sleep,
        )
    finally:
        try:
            browser.stop()
        except Exception as e:
            log.debug("nodriver shutdown noise (cosmetic): %s", e)


# Public alias used by orchestrator unit tests for monkeypatching.
def __getattr__(name: str) -> Any:
    if name == "time_module":
        return time
    raise AttributeError(name)
