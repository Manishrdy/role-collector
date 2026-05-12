"""Watchlist board enumeration.

Closes the Phase-5 loop: takes companies whose ATS URL has been resolved
and walks their ATS board page to surface individual job-posting URLs.
Those URLs are then prepended to `state["candidate_urls"]` so the
existing Phase-3 fetch / extract / save pipeline picks them up — no new
fetcher, no new parser.

Enumeration is intentionally generic: fetch the board page, find every
`<a>` whose href stays on the same ATS host AND is a child path of the
board URL (more path segments). This handles Greenhouse / Lever / Ashby
without per-provider logic. Workday is JS-rendered and won't enumerate
via plain HTTP — that's a known v2 gap, same root cause as the
Cloudflare-403 resolvers.

Plain HTTP via `requests` for the board fetch. Per-job page fetches stay
on the existing async Playwright pipeline.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _path_segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.split("/") if s]


def enumerate_board_html(
    *,
    html: str,
    board_url: str,
    max_jobs: int = 100,
) -> list[str]:
    """Extract individual job-posting URLs from an ATS board page's HTML.

    Pure function: no I/O, no state. Easy to test against a fixture.

    Keep URLs that (a) live on the same host as `board_url`, (b) have
    strictly more path segments than the board URL, and (c) don't end in
    obvious non-job suffixes (.css, .js, .png, anchors, mailto, etc.).
    """
    base = urlparse(board_url)
    base_host = (base.hostname or "").lower()
    base_segments = _path_segments(board_url)
    if not base_host:
        return []

    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, None] = {}  # preserve insertion order
    for a in soup.find_all("a", href=True):
        href_attr = a.get("href")
        href = href_attr if isinstance(href_attr, str) else " ".join(href_attr or [])
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(board_url, href)
        parsed = urlparse(absolute)
        if (parsed.hostname or "").lower() != base_host:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        # Drop static asset URLs.
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in (".css", ".js", ".png", ".jpg", ".svg", ".ico")):
            continue
        # Must be a child of the board URL (more path segments).
        if len(_path_segments(absolute)) <= len(base_segments):
            continue
        # Drop query-only differences from the board URL (e.g.
        # /acme?sort=newest); we only want real child paths.
        canonical = f"{parsed.scheme}://{base_host}{parsed.path}"
        if canonical == board_url.rstrip("/"):
            continue
        seen.setdefault(canonical, None)
        if len(seen) >= max_jobs:
            break
    out = list(seen.keys())
    log.info("[watchlist] %d job URLs enumerated from %s", len(out), board_url)
    return out


def fetch_and_enumerate(
    board_url: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = 15.0,
    max_jobs: int = 100,
) -> list[str]:
    """Fetch a board URL and enumerate. Returns [] on network failure."""
    sess = session or requests.Session()
    try:
        resp = sess.get(
            board_url,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.info("[watchlist] fetch failed for %s: %s", board_url, e)
        return []
    return enumerate_board_html(
        html=resp.text, board_url=board_url, max_jobs=max_jobs
    )
