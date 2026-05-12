"""Hacker News funding aggregator.

Uses HN's public Algolia search endpoint (`hn.algolia.com/api/v1/search`).
No auth, no scraping, no rate-limit issues at our query volume. Two
query themes: "Series" stories and "raised $" stories. Stories with a
URL field point to the original announcement (often TechCrunch /
company blog) — we surface that URL as the funding event source so the
follow-up resolver can crawl it for the careers page.

The Algolia response gives us a structured timestamp (`created_at_i`)
which we convert to ISO-8601 date for the `announced_date` column. Title
text drives the extractor; the URL is captured separately.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import requests

from job_agent.sources.funding.extractor import extract_from_text
from job_agent.sources.funding.schema import FundingEventCandidate

log = logging.getLogger(__name__)

_API = "https://hn.algolia.com/api/v1/search"
_QUERY_THEMES: tuple[str, ...] = (
    "raised Series",
    "raised seed",
    "raises Series",
    "raises seed",
)


def _to_iso_date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()


def fetch_hackernews_funding(
    *,
    queries: Iterable[str] = _QUERY_THEMES,
    hits_per_query: int = 20,
    session: requests.Session | None = None,
    request_timeout: float = 15.0,
) -> list[FundingEventCandidate]:
    """Hit Algolia for each theme; extract candidates from titles.

    Stories without a URL (Ask HN, Show HN text posts) are skipped — they
    can't drive a careers-page resolver in commit 2.
    """
    sess = session or requests.Session()
    out: list[FundingEventCandidate] = []
    seen_object_ids: set[str] = set()
    for q in queries:
        numeric_filter = "created_at_i>" + str(int(datetime.now(tz=UTC).timestamp()) - 7 * 86400)
        params: dict[str, str | int] = {
            "query": q,
            "tags": "story",
            "hitsPerPage": hits_per_query,
            "numericFilters": numeric_filter,
        }
        try:
            resp = sess.get(
                _API,
                params=params,
                timeout=request_timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as e:
            log.warning("[funding/hn] request failed for %r: %s", q, e)
            continue
        for hit in payload.get("hits", []):
            cand = _hit_to_candidate(hit, seen_object_ids)
            if cand:
                out.append(cand)
    log.info("[funding/hn] %d candidates across %d queries", len(out), len(list(queries)))
    return out


def _hit_to_candidate(
    hit: dict[str, Any],
    seen_object_ids: set[str],
) -> FundingEventCandidate | None:
    obj_id = hit.get("objectID")
    if obj_id and obj_id in seen_object_ids:
        return None
    if obj_id:
        seen_object_ids.add(obj_id)

    title = (hit.get("title") or "").strip()
    url = (hit.get("url") or "").strip()
    if not title or not url:
        return None

    created_at_i = hit.get("created_at_i")
    announced_date = _to_iso_date(created_at_i) if isinstance(created_at_i, int) else None

    return extract_from_text(
        text=title,
        source_url=url,
        aggregator="hackernews",
        announced_date=announced_date,
    )
