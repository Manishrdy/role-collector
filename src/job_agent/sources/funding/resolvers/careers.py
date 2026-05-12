"""Careers-page resolver: company website -> careers URL.

Strategy:
1. Probe canonical paths (`/careers`, `/jobs`, `/join-us`, ...). Stop at
   the first one that returns 200 and contains text plausibly about
   jobs.
2. If probing exhausts the retry budget without a hit, fetch the
   homepage and scan its `<a>` tags for links whose text or href
   contains `careers|jobs|join`. The first match wins.
3. Returns `None` if nothing matches. The orchestrator interprets None
   as "no careers page found" and may drop the company.

Plain HTTP via `requests` — pure-JS SPAs that client-render the nav are
a known gap. v2 can fall back to Playwright when the homepage has zero
`<a>` tags.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_CANONICAL_PATHS: tuple[str, ...] = (
    "/careers",
    "/careers/",
    "/jobs",
    "/jobs/",
    "/join-us",
    "/join",
    "/work-with-us",
    "/company/careers",
    "/about/careers",
)

_NAV_LINK_KEYWORDS: tuple[str, ...] = ("career", "job", "join")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_JOB_PAGE_HINTS: tuple[str, ...] = (
    "career",
    "job",
    "open role",
    "open position",
    "we're hiring",
    "we are hiring",
    "join our team",
)


def _looks_like_job_page(html: str) -> bool:
    """Cheap sanity check: a 200 OK for /careers might be a 404 SPA shell.

    Lower-case substring match against a small set of hints. False
    positives are OK — they push the page into the Phase-3 pipeline where
    the deterministic parsers will reject it for lack of structured data.
    """
    lowered = html.lower()
    return any(h in lowered for h in _JOB_PAGE_HINTS)


def _normalise_root(website_url: str) -> str:
    parsed = urlparse(website_url)
    if not parsed.scheme:
        return f"https://{website_url.rstrip('/')}"
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def resolve_careers_url(
    website_url: str,
    *,
    retry_budget: int = 8,
    session: requests.Session | None = None,
    request_timeout: float = 8.0,
) -> str | None:
    """Try canonical paths, then nav-link extraction. Return first match or None."""
    sess = session or requests.Session()
    root = _normalise_root(website_url)

    # Stage 1: canonical paths
    for tried, path in enumerate(_CANONICAL_PATHS, start=1):
        if tried > retry_budget:
            break
        candidate = f"{root}{path}"
        if _probe(sess, candidate, request_timeout):
            log.info("[careers] %s -> %s (canonical)", root, candidate)
            return candidate

    # Stage 2: scrape homepage nav
    try:
        resp = sess.get(
            root,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.info("[careers] homepage fetch failed for %s: %s", root, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    for a in soup.find_all("a", href=True):
        href_attr = a.get("href")
        href = href_attr if isinstance(href_attr, str) else " ".join(href_attr or [])
        text = (a.get_text(" ", strip=True) or "").lower()
        href_lc = href.lower()
        if not any(kw in href_lc or kw in text for kw in _NAV_LINK_KEYWORDS):
            continue
        absolute = urljoin(root + "/", href)
        # Drop obvious mailto/anchor/social.
        if not absolute.startswith(("http://", "https://")):
            continue
        if any(bad in absolute.lower() for bad in ("linkedin.com", "twitter.com", "x.com")):
            continue
        if _probe(sess, absolute, request_timeout):
            log.info("[careers] %s -> %s (nav-link)", root, absolute)
            return absolute
    log.info("[careers] no careers page found for %s", root)
    return None


def _probe(session: requests.Session, url: str, timeout: float) -> bool:
    """GET a URL with a short timeout; return True iff it looks like a job page."""
    try:
        resp = session.get(
            url,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            allow_redirects=True,
        )
    except requests.RequestException:
        return False
    if resp.status_code >= 400:
        return False
    if not resp.text or len(resp.text) < 200:
        return False
    return _looks_like_job_page(resp.text)
