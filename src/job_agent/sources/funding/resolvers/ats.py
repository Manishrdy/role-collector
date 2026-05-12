"""ATS detector: careers URL -> ATS provider + canonical ATS URL.

Detection ladder:
1. The final URL (after redirects) matches a known ATS host pattern.
2. The page embeds the ATS via `<iframe src="...">`.
3. The page references the ATS via inline-JS embed (Greenhouse's
   `s.src = 'boards.greenhouse.io/embed/...'` pattern, Lever's
   equivalent).

Returns `(ats_type, ats_url)` on a hit, `(None, None)` otherwise.
Aligned with the ATS-type strings the rest of the codebase uses
(``ashby``, ``greenhouse``, ``lever``, ``workday``, ``smartrecruiters``)
so the existing Phase-3 parsers route correctly.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from job_agent.sources.funding.resolvers._fetch import FetchFallback, fetch_with_fallback

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# (compiled_regex, ats_type). First match wins.
_URL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"jobs\.ashbyhq\.com", re.IGNORECASE), "ashby"),
    (re.compile(r"jobs\.lever\.co", re.IGNORECASE), "lever"),
    (re.compile(r"(?:boards|job-boards)\.greenhouse\.io", re.IGNORECASE), "greenhouse"),
    (re.compile(r"myworkdayjobs\.com|myworkdaysite\.com", re.IGNORECASE), "workday"),
    (re.compile(r"jobs\.smartrecruiters\.com", re.IGNORECASE), "smartrecruiters"),
)


def detect_ats_from_url(url: str) -> str | None:
    """First-pass: is the URL itself an ATS-hosted careers page?"""
    for pat, ats in _URL_PATTERNS:
        if pat.search(url):
            return ats
    return None


def detect_ats_from_html(html: str) -> tuple[str | None, str | None]:
    """Scan HTML for ATS iframes / embed scripts.

    Returns ``(ats_type, ats_url)`` if found. The URL is the actual ATS
    host URL (jobs.lever.co/acme), not the company's careers page URL.
    """
    soup = BeautifulSoup(html, "lxml")

    # Iframe-embedded ATS.
    for frame in soup.find_all("iframe", src=True):
        src_attr = frame.get("src")
        src = src_attr if isinstance(src_attr, str) else " ".join(src_attr or [])
        ats = detect_ats_from_url(src)
        if ats:
            return ats, src

    # Inline-JS embed: Greenhouse's `boards.greenhouse.io/embed/job_board?for=<company>`
    # and Lever's `jobs.lever.co/<company>` references in <script> bodies.
    for script in soup.find_all("script"):
        body = script.string or ""
        if not body:
            continue
        # Find every URL in the script, then check each one against ATS patterns.
        for url_match in re.finditer(r"https?://[^\s'\"<>]+", body):
            ats = detect_ats_from_url(url_match.group(0))
            if ats:
                return ats, url_match.group(0)

    return None, None


def resolve_ats(
    careers_url: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = 8.0,
    fallback: FetchFallback | None = None,
) -> tuple[str | None, str | None]:
    """End-to-end: probe the careers URL, follow redirects, scan for ATS markers.

    Returns ``(ats_type, ats_url)`` or ``(None, None)``. When ``fallback``
    is supplied, blocked / empty responses escalate to Playwright.
    """
    sess = session or requests.Session()

    # URL-pattern check on the input itself (no fetch).
    direct = detect_ats_from_url(careers_url)
    if direct:
        return direct, careers_url

    result = fetch_with_fallback(
        careers_url, session=sess, fallback=fallback, request_timeout=request_timeout
    )
    if result.status_code >= 400 or result.status_code == 0 or not result.html:
        log.info("[ats] fetch failed for %s (status=%s)", careers_url, result.status_code)
        return None, None

    # Did the redirect chain land on an ATS host?
    direct = detect_ats_from_url(result.final_url)
    if direct:
        return direct, result.final_url

    # HTML-embedded ATS.
    ats, ats_url = detect_ats_from_html(result.html)
    if ats and ats_url:
        # Normalise relative-protocol URLs from iframe src.
        if ats_url.startswith("//"):
            ats_url = "https:" + ats_url
        elif ats_url.startswith("/"):
            base = urlparse(result.final_url)
            ats_url = f"{base.scheme}://{base.netloc}{ats_url}"
        return ats, ats_url
    return None, None
