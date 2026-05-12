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
from dataclasses import dataclass, field
from typing import Literal

import requests

from job_agent.sources.ats_api.clients import (
    enumerate_ashby,
    enumerate_greenhouse,
    enumerate_lever,
)

log = logging.getLogger(__name__)

Provider = Literal["lever", "greenhouse", "ashby"]


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
}

_HOST_BY_PROVIDER = {
    "lever": "jobs.lever.co",
    "greenhouse": "boards.greenhouse.io",
    "ashby": "jobs.ashbyhq.com",
}

_ATS_TYPE_BY_PROVIDER = {
    "lever": "lever",
    "greenhouse": "greenhouse",
    "ashby": "ashby",
}


def discover_from_seeds(
    seeds: dict[str, list[str]],
    *,
    max_jobs_per_slug: int = 20,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, object]], DiscoveryStats]:
    """Enumerate jobs for every (provider, slug) in ``seeds``.

    Returns ``(candidate_url_dicts, stats)``. Each candidate dict matches
    the shape that ``state["candidate_urls"]`` expects (mirrors what the
    ATS-search orchestrator emits).
    """
    sess = session or requests.Session()
    stats = DiscoveryStats()
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    for provider, slugs in seeds.items():
        if provider not in _ENUMERATORS:
            log.info("[ats-api/discovery] unknown provider %r, skipping", provider)
            continue
        enumerator = _ENUMERATORS[provider]
        ats_type = _ATS_TYPE_BY_PROVIDER[provider]
        host = _HOST_BY_PROVIDER[provider]
        provider_count = 0

        for slug in slugs:
            slug = slug.strip()
            if not slug:
                continue
            stats.slugs_checked += 1
            try:
                urls = enumerator(slug, session=sess, max_jobs=max_jobs_per_slug)
            except Exception as e:
                log.exception("[ats-api/discovery] %s/%s threw", provider, slug)
                stats.slugs_failed += 1
                stats.errors.append(f"{provider}/{slug}: {e}")
                continue
            if urls is None:
                stats.slugs_failed += 1
                continue
            if not urls:
                # Slug exists but has no public postings — fine.
                continue
            stats.slugs_with_jobs += 1
            for url in urls:
                if url in seen:
                    continue
                seen.add(url)
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
                        "location": None,
                    }
                )
                provider_count += 1

        stats.by_provider[provider] = provider_count
        stats.jobs_total += provider_count

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
