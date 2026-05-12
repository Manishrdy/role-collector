"""Funding discovery orchestrator.

Fans out to each enabled aggregator, deduplicates by `(normalized_name,
source_url)`, and persists. Aggregator-level failures are isolated so a
single broken source doesn't abort the run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from job_agent.config import AppConfig
from job_agent.db import repo
from job_agent.sources.funding.aggregators.google import fetch_google_funding
from job_agent.sources.funding.aggregators.hackernews import fetch_hackernews_funding
from job_agent.sources.funding.aggregators.techcrunch import fetch_techcrunch_funding
from job_agent.sources.funding.schema import FundingEventCandidate

log = logging.getLogger(__name__)


@dataclass
class FundingDiscoveryStats:
    candidates_total: int = 0
    candidates_unique: int = 0
    candidates_inserted: int = 0
    candidates_existing: int = 0
    by_aggregator: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _safe_call(
    name: str,
    func: Callable[[], list[FundingEventCandidate]],
    stats: FundingDiscoveryStats,
) -> list[FundingEventCandidate]:
    try:
        out = func()
    except Exception as e:
        log.exception("[funding] aggregator %s failed", name)
        stats.errors.append(f"{name}: {e}")
        return []
    stats.by_aggregator[name] = len(out)
    return out


def discover_funding_events(
    cfg: AppConfig,
    *,
    search_run_id: int | None = None,
) -> FundingDiscoveryStats:
    """Run all enabled aggregators, dedupe, persist. Returns stats."""
    stats = FundingDiscoveryStats()
    if not cfg.sources.funding_discovery.enabled:
        return stats

    enabled = cfg.sources.funding_discovery.aggregators
    all_cands: list[FundingEventCandidate] = []
    if enabled.hackernews:
        all_cands.extend(_safe_call("hackernews", fetch_hackernews_funding, stats))
    if enabled.techcrunch:
        all_cands.extend(_safe_call("techcrunch", fetch_techcrunch_funding, stats))
    if enabled.google:
        all_cands.extend(
            _safe_call(
                "google",
                lambda: fetch_google_funding(cfg, search_run_id=search_run_id),
                stats,
            )
        )

    stats.candidates_total = len(all_cands)

    seen: set[tuple[str, str]] = set()
    deduped: list[FundingEventCandidate] = []
    for c in all_cands:
        key = (_normalize(c.company_name), c.source_url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    stats.candidates_unique = len(deduped)

    for c in deduped:
        try:
            result = repo.upsert_funding_event(
                company_name=c.company_name,
                source_url=c.source_url,
                round=c.round,
                amount=c.amount,
                announced_date=c.announced_date,
                investors=c.investors,
                raw_snippet=c.raw_snippet,
            )
        except Exception as e:
            log.exception("[funding] persist failed for %s", c.company_name)
            stats.errors.append(f"persist {c.company_name}: {e}")
            continue
        if result.inserted:
            stats.candidates_inserted += 1
        else:
            stats.candidates_existing += 1

    log.info(
        "[funding] %d total / %d unique / %d new / %d existing / %d errors",
        stats.candidates_total,
        stats.candidates_unique,
        stats.candidates_inserted,
        stats.candidates_existing,
        len(stats.errors),
    )
    return stats
