"""ATS Google search orchestrator — Phase 2 entry point.

Takes a list of `PlannedQuery`, drives the browser engines (Google, then
Bing as fallback), respects rate limits, and returns deduplicated
`CandidateURL` records ready for Phase-3 page extraction.

Design notes:

- We open ONE browser context for the whole run and reuse it across
  queries. Re-launching the persistent profile per query would be slow
  and could clear cookies that help us avoid captchas.
- Block stickiness: once Google has blocked us, we don't keep re-asking
  Google. The orchestrator latches the engine to Bing for the rest of
  the run.
- Dedup happens here too — if multiple queries return the same canonical
  URL, we keep the first one and log subsequent hits as duplicates.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

from job_agent.browser.search_engines import (
    SearchOutcome,
    SearchResult,
    run_bing_search,
    run_google_search,
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
    bing_blocked: bool = False
    total_results: int = 0
    duplicate_results: int = 0


def _sleep(cfg: AppConfig, *, sleep_fn: Callable[[float], None]) -> None:
    lo = cfg.limits.min_delay_between_searches_seconds
    hi = cfg.limits.max_delay_between_searches_seconds
    delay = random.uniform(lo, hi)
    log.info("sleeping %.1fs between queries", delay)
    sleep_fn(delay)


def _outcome_to_candidates(
    outcome: SearchOutcome,
    plan: PlannedQuery,
) -> list[CandidateURL]:
    out: list[CandidateURL] = []
    for r in outcome.results:
        out.append(_to_candidate(r, plan))
    return out


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


def run_ats_search(
    *,
    context: BrowserContext,
    cfg: AppConfig,
    plans: list[PlannedQuery],
    max_results_per_query: int,
    search_run_id: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    debug_dump_dir: Path | None = None,
) -> tuple[list[CandidateURL], OrchestrationStats]:
    """Drive the configured search engines through the planned queries.

    Returns deduplicated CandidateURLs in stable order (first occurrence wins).
    Per-query outcomes are logged to agent_events for the given search_run_id.
    """
    seen: dict[str, CandidateURL] = {}
    stats = OrchestrationStats()
    google_blocked = False

    engine_order = cfg.browser.search_engine_order

    for idx, plan in enumerate(plans, start=1):
        stats.queries_attempted += 1
        outcome: SearchOutcome | None = None

        # Engine selection: try google first if not yet blocked, else fall
        # straight to bing. Respect the configured engine order.
        for engine in engine_order:
            if engine == "google" and google_blocked:
                continue
            if engine == "bing" and stats.bing_blocked:
                continue

            log.info("[%d/%d] %s: %s", idx, len(plans), engine, plan.query)
            if engine == "google":
                outcome = run_google_search(
                    context,
                    query=plan.query,
                    time_window=plan.time_window,
                    target_domain=plan.target_domain,
                    max_results=max_results_per_query,
                    debug_dump_dir=debug_dump_dir,
                )
            else:
                outcome = run_bing_search(
                    context,
                    query=plan.query,
                    time_window=plan.time_window,
                    target_domain=plan.target_domain,
                    max_results=max_results_per_query,
                    debug_dump_dir=debug_dump_dir,
                )

            if outcome.blocked:
                if engine == "google":
                    google_blocked = True
                    if stats.google_blocked_at is None:
                        stats.google_blocked_at = idx
                else:
                    stats.bing_blocked = True
                _log_event(
                    search_run_id,
                    "search_blocked",
                    f"{engine} blocked on query {idx}",
                    {
                        "engine": engine,
                        "query": plan.query,
                        "time_window": plan.time_window,
                        "blocked_reason": outcome.blocked_reason,
                    },
                )
                continue  # try the next engine
            break  # success on this engine

        if outcome is None or outcome.blocked:
            stats.queries_blocked += 1
            _log_event(
                search_run_id,
                "search_failed",
                f"all engines blocked or unavailable on query {idx}",
                {"query": plan.query, "time_window": plan.time_window},
            )
        else:
            stats.queries_succeeded += 1
            new_count = 0
            dup_count = 0
            for cand in _outcome_to_candidates(outcome, plan):
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
                f"{outcome.engine} returned {len(outcome.results)} ({new_count} new)",
                {
                    "engine": outcome.engine,
                    "query": plan.query,
                    "time_window": plan.time_window,
                    "results_total": len(outcome.results),
                    "results_new": new_count,
                    "results_duplicate": dup_count,
                },
            )

        # Rate-limit between queries (skip after the last one).
        if idx < len(plans):
            _sleep(cfg, sleep_fn=sleep_fn)

    return list(seen.values()), stats


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
