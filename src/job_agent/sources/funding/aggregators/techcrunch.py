"""TechCrunch venture-category aggregator.

Fetches the `/category/venture/` listing page over plain HTTP — TechCrunch
doesn't block `requests` user-agents at our query rate — and walks
article cards. Each card surfaces a title + excerpt + canonical URL,
which we feed to the same regex extractor as the other aggregators.

We DON'T follow article URLs in this commit. The listing card itself
usually contains enough headline text to extract `(company, round,
amount)`. Full-article fetch lands in commit 2 alongside the careers-page
resolver, where we'll need the full article to find the company's
website anyway.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from job_agent.sources.funding.extractor import extract_from_text
from job_agent.sources.funding.schema import FundingEventCandidate

log = logging.getLogger(__name__)

_LISTING_URLS: tuple[str, ...] = (
    "https://techcrunch.com/category/venture/",
    "https://techcrunch.com/category/startups/",
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def fetch_techcrunch_funding(
    *,
    listing_urls: Iterable[str] = _LISTING_URLS,
    session: requests.Session | None = None,
    request_timeout: float = 20.0,
) -> list[FundingEventCandidate]:
    """Scrape TechCrunch listing pages; extract candidates per article card."""
    sess = session or requests.Session()
    out: list[FundingEventCandidate] = []
    seen_urls: set[str] = set()
    for listing_url in listing_urls:
        try:
            resp = sess.get(
                listing_url,
                timeout=request_timeout,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("[funding/tc] request failed for %s: %s", listing_url, e)
            continue
        out.extend(_parse_listing_html(resp.text, seen_urls))
    log.info("[funding/tc] %d candidates across %d listings", len(out), len(list(listing_urls)))
    return out


def _parse_listing_html(html: str, seen_urls: set[str]) -> list[FundingEventCandidate]:
    """Walk a TechCrunch category page. Resilient to selector churn — we
    look at `<article>` then fall back to anchors whose href looks like an
    article URL, then derive title from anchor text and excerpt from the
    parent block.
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[FundingEventCandidate] = []
    today = datetime.now(UTC).date().isoformat()

    articles = soup.find_all("article")
    if not articles:
        # Fallback: TechCrunch sometimes nests cards in <li class="...post...">
        articles = soup.select("li.wp-block-post, li.post")

    for art in articles:
        link = art.find("a", href=True)
        if not link:
            continue
        href_attr = link.get("href")
        url = href_attr if isinstance(href_attr, str) else " ".join(href_attr or [])
        if not url.startswith("http") or "/category/" in url or url in seen_urls:
            continue
        seen_urls.add(url)

        title_el = art.find(["h2", "h3"]) or link
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            continue

        # Excerpt: prefer the article's <p>, else fall back to full card text.
        excerpt_el = art.find("p")
        excerpt = excerpt_el.get_text(" ", strip=True) if excerpt_el else ""
        snippet = f"{title}. {excerpt}".strip(". ").strip()

        cand = extract_from_text(
            text=snippet or title,
            source_url=url,
            aggregator="techcrunch",
            announced_date=today,
        )
        if cand is not None:
            out.append(cand)
    return out
