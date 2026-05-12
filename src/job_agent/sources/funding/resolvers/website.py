"""Company website resolver: name -> official website.

Two-tier resolution:

1. **Cheap path** — if the funding event's `source_url` is a non-aggregator
   host (e.g. HN-linked company blog), take its apex domain as the
   website. No network call.
2. **Google fallback** — query `"<Company>" official site` via the
   existing nodriver driver, take the first result whose host is not in
   the aggregator blocklist. Async, optional.

Most HN sources point straight at the company; TechCrunch sources always
land on techcrunch.com and need the Google fallback. The orchestrator
gates the fallback so a run with 0 TC-only events doesn't spin up a
nodriver session.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    import nodriver

log = logging.getLogger(__name__)


# Hosts that should never be returned as a company's "official site."
_AGGREGATOR_HOSTS: frozenset[str] = frozenset(
    {
        "techcrunch.com",
        "news.ycombinator.com",
        "ycombinator.com",
        "hn.algolia.com",
        "crunchbase.com",
        "news.crunchbase.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "medium.com",
        "wikipedia.org",
        "youtube.com",
        "github.com",  # not a "website" — companies' GH org isn't a careers page
        "bloomberg.com",
        "reuters.com",
        "forbes.com",
        "businesswire.com",
        "prnewswire.com",
    }
)


def _apex_host(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    # Strip leading "www." but keep multi-label subdomains for ATS hosts.
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _is_aggregator(host: str) -> bool:
    return any(host == agg or host.endswith("." + agg) for agg in _AGGREGATOR_HOSTS)


def resolve_website_cheap(source_url: str | None) -> str | None:
    """Cheap path: extract the apex host from a non-aggregator source URL.

    Returns ``https://<host>`` on a hit, ``None`` otherwise.
    """
    if not source_url:
        return None
    host = _apex_host(source_url)
    if not host or _is_aggregator(host):
        return None
    return f"https://{host}"


async def resolve_website_via_google(
    company_name: str,
    *,
    browser: nodriver.Browser,
    max_results: int = 5,
) -> str | None:
    """Google fallback. Returns the first non-aggregator result URL or None."""
    from job_agent.browser.search_engines import run_google_search_async

    query = f'"{company_name}" official site'
    outcome = await run_google_search_async(
        browser,
        query=query,
        time_window="any",
        target_domain=None,
        max_results=max_results,
    )
    if outcome.blocked:
        log.info("[website-resolver] google blocked for %r", company_name)
        return None
    for r in outcome.results:
        host = _apex_host(r.canonical_url)
        if not host or _is_aggregator(host):
            continue
        return f"https://{host}"
    return None
