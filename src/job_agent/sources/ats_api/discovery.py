"""Seed-slug ATS API discovery.

Walks a configured list of known company slugs per provider (Lever /
Greenhouse / Ashby) and enumerates each via the provider's public
JSON API. Yields a list of candidate URLs ready to feed into
``state["candidate_urls"]`` — same shape as the ATS Google search
output.

Why: Google rate-limits `site:` queries aggressively (we hit the block
at query 12 in production). The same data is publicly available via
ATS APIs with no rate limit. 30 seed slugs x ~20 jobs each =
~600 candidate URLs with zero Google calls.

The seed list grows organically: every time a funding event resolves
to a careers page that maps to one of these three ATS hosts, the slug
gets added to `companies.ats_url` and is picked up by the watchlist
node. Discovery here is the bootstrap layer for slugs we know about
up-front from a curated config.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import requests

from job_agent.sources.ats_api.clients import (
    enumerate_ashby,
    enumerate_greenhouse,
    enumerate_icims,
    enumerate_lever,
    enumerate_smartrecruiters,
    enumerate_workday,
)
from job_agent.sources.ats_api.normalize import (
    freshness_bucket,
    normalize_employment_type,
    normalize_iso_datetime,
    normalize_remote_type,
)

log = logging.getLogger(__name__)

Provider = Literal[
    "lever",
    "greenhouse",
    "ashby",
    "workday",
    "smartrecruiters",
    "icims",
]


@dataclass
class DiscoveryStats:
    slugs_checked: int = 0
    slugs_with_jobs: int = 0
    slugs_failed: int = 0
    jobs_total: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


_ENUMERATORS = {
    "lever": enumerate_lever,
    "greenhouse": enumerate_greenhouse,
    "ashby": enumerate_ashby,
    "workday": enumerate_workday,
    "smartrecruiters": enumerate_smartrecruiters,
    "icims": enumerate_icims,
}

_HOST_BY_PROVIDER = {
    "lever": "jobs.lever.co",
    "greenhouse": "boards.greenhouse.io",
    "ashby": "jobs.ashbyhq.com",
    "workday": "myworkdayjobs.com",
    "smartrecruiters": "jobs.smartrecruiters.com",
    "icims": "careers.icims.com",
}

_ATS_TYPE_BY_PROVIDER = {
    "lever": "lever",
    "greenhouse": "greenhouse",
    "ashby": "ashby",
    "workday": "workday",
    "smartrecruiters": "smartrecruiters",
    "icims": "icims",
}


def _read_slug_file(path: Path) -> list[str]:
    """Read a slug-per-line file. Strips blanks and `#` comments."""
    if not path.exists():
        log.warning("[ats-api/discovery] slug file not found: %s", path)
        return []
    slugs: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            slugs.append(s)
    return slugs


def load_seeds(
    inline_seeds: dict[str, list[str]] | None = None,
    slug_files: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Merge inline seeds with file-based seeds.

    For each provider the result is a deduplicated, order-preserving union
    of (inline list + every slug in the file at slug_files[provider]).
    """
    inline_seeds = inline_seeds or {}
    slug_files = slug_files or {}
    out: dict[str, list[str]] = {}
    providers = sorted(set(inline_seeds.keys()) | set(slug_files.keys()))
    for provider in providers:
        merged: list[str] = []
        seen: set[str] = set()
        for slug in inline_seeds.get(provider, []) + (
            _read_slug_file(Path(slug_files[provider])) if provider in slug_files else []
        ):
            normalized = slug.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        if merged:
            out[provider] = merged
    return out


def shard_seeds(
    seeds: dict[str, list[str]],
    *,
    slugs_per_cycle: int,
    offset: int,
) -> tuple[dict[str, list[str]], int]:
    """Slice the flattened slug list into a deterministic shard.

    Returns (shard_seeds_by_provider, next_offset). When ``slugs_per_cycle``
    is 0 or covers the entire catalog the offset wraps to 0 — callers can
    persist ``next_offset`` to agent_memory so the next cycle continues
    from where this one stopped.
    """
    flat: list[tuple[str, str]] = []
    for provider in sorted(seeds.keys()):
        for slug in seeds[provider]:
            flat.append((provider, slug))
    total = len(flat)
    if total == 0:
        return ({}, 0)
    if slugs_per_cycle <= 0 or slugs_per_cycle >= total:
        return (dict(seeds), 0)
    offset = offset % total
    end = offset + slugs_per_cycle
    if end <= total:
        window = flat[offset:end]
        next_offset = end % total
    else:
        # Wrap: take from offset to end, then from 0 to remainder.
        window = flat[offset:] + flat[: end - total]
        next_offset = end - total
    shard: dict[str, list[str]] = {}
    for provider, slug in window:
        shard.setdefault(provider, []).append(slug)
    return (shard, next_offset)


def _enumerate_one(
    provider: str,
    slug: str,
    *,
    max_jobs: int,
    request_timeout: float,
    session: requests.Session | None = None,
) -> tuple[str, str, list[dict[str, Any]] | None, Exception | None]:
    enumerator = _ENUMERATORS[provider]
    try:
        urls = enumerator(
            slug,
            session=session,
            request_timeout=request_timeout,
            max_jobs=max_jobs,
        )
        return (provider, slug, urls, None)
    except Exception as e:
        return (provider, slug, None, e)


def discover_from_seeds(
    seeds: dict[str, list[str]],
    *,
    max_jobs_per_slug: int = 20,
    session: requests.Session | None = None,  # kept for API compatibility
    concurrency: int = 10,
    provider_timeouts: dict[str, float] | None = None,
) -> tuple[list[dict[str, object]], DiscoveryStats]:
    """Enumerate jobs for every (provider, slug) in ``seeds`` concurrently.

    Returns ``(candidate_url_dicts, stats)``. Each candidate dict matches
    the shape that ``state["candidate_urls"]`` expects (mirrors what the
    ATS-search orchestrator emits). Concurrency is bounded by
    ``concurrency``; the public ATS APIs tolerate 10-20 in-flight requests
    in practice.
    """
    provider_timeouts = provider_timeouts or {}
    stats = DiscoveryStats()
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    provider_counts: dict[str, int] = {}

    work: list[tuple[str, str]] = []
    for provider, slugs in seeds.items():
        if provider not in _ENUMERATORS:
            log.info("[ats-api/discovery] unknown provider %r, skipping", provider)
            continue
        for slug in slugs:
            slug = slug.strip()
            if slug:
                work.append((provider, slug))
        # Pre-seed so by_provider reports the provider key even when every
        # slug failed (matches v1 behavior).
        provider_counts.setdefault(provider, 0)

    if not work:
        stats.by_provider = provider_counts
        return ([], stats)

    workers = max(1, min(concurrency, len(work)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _enumerate_one, provider, slug,
                max_jobs=max_jobs_per_slug,
                request_timeout=float(provider_timeouts.get(provider, 15.0)),
                session=session,
            )
            for provider, slug in work
        ]
        for fut in as_completed(futures):
            provider, slug, urls, err = fut.result()
            stats.slugs_checked += 1
            if err is not None:
                stats.slugs_failed += 1
                stats.errors.append(f"{provider}/{slug}: {err}")
                continue
            if urls is None:
                stats.slugs_failed += 1
                continue
            if not urls:
                continue
            stats.slugs_with_jobs += 1
            host = _HOST_BY_PROVIDER[provider]
            ats_type = _ATS_TYPE_BY_PROVIDER[provider]
            observed_at = datetime.now(UTC).isoformat(timespec="seconds")
            for item in urls:
                url = item.get("url") if isinstance(item, dict) else None
                if not isinstance(url, str):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                posted_at_source = (
                    normalize_iso_datetime(item.get("posted_at_source"))
                    if isinstance(item, dict)
                    else None
                )
                candidates.append(
                    {
                        "url": url,
                        "canonical_url": url,
                        "title": f"{slug} ({provider})",
                        "snippet": "",
                        "rank": 0,
                        "engine": "ats_api",
                        "source_type": "ats_api_discovery",
                        "source_query": slug,
                        "target_domain": host,
                        "ats_type": ats_type,
                        "time_window": "any",
                        "role": "",
                        "location": item.get("location") if isinstance(item, dict) else None,
                        "posted_at_source": posted_at_source,
                        "observed_at": observed_at,
                        "freshness_bucket": freshness_bucket(
                            posted_at_source, observed_at_iso=observed_at
                        ),
                        "employment_type": normalize_employment_type(
                            item.get("employment_type") if isinstance(item, dict) else None
                        ),
                        "remote_type": normalize_remote_type(
                            item.get("remote_type") if isinstance(item, dict) else None
                        ),
                        "ats_job_id": (
                            str(item.get("ats_job_id"))
                            if isinstance(item, dict) and item.get("ats_job_id") is not None
                            else None
                        ),
                    }
                )
                provider_counts[provider] = provider_counts.get(provider, 0) + 1

    stats.by_provider = provider_counts
    stats.jobs_total = sum(provider_counts.values())

    log.info(
        "[ats-api/discovery] slugs_checked=%d slugs_with_jobs=%d slugs_failed=%d "
        "jobs_total=%d by_provider=%s",
        stats.slugs_checked,
        stats.slugs_with_jobs,
        stats.slugs_failed,
        stats.jobs_total,
        stats.by_provider,
    )
    return candidates, stats
