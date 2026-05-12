"""Watchlist board enumeration.

Closes the Phase-5 loop: takes companies whose ATS URL has been resolved
and walks their ATS board page to surface individual job-posting URLs.
Those URLs are then prepended to `state["candidate_urls"]` so the
existing Phase-3 fetch / extract / save pipeline picks them up — no new
fetcher, no new parser.

Two-tier enumeration:

1. **Provider-specific public APIs (preferred).** Lever and Greenhouse
   both publish documented JSON endpoints (``api.lever.co`` and
   ``boards-api.greenhouse.io``). When the board URL matches one of
   these hosts we use the API — no scraping, no JS rendering, no
   Cloudflare risk, no captcha. Lever's HTML board specifically
   *cannot* be enumerated by anchor-walking (listings aren't anchor
   tags); the API is the only reliable path.
2. **Generic HTML anchor walk (fallback).** For Ashby, Workday, and
   unknown providers we fetch the board page and look for child-path
   anchors. Playwright fallback applies here when the HTML response
   is blocked / served as a JS shell.

Per-job page fetches stay on the existing async Playwright pipeline.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from job_agent.sources.funding.resolvers._fetch import FetchFallback, fetch_with_fallback

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Provider-specific API enumerators
#
# Lever and Greenhouse both publish documented JSON endpoints that return
# every public posting for a company. We prefer these over HTML scraping
# because they're stable, captcha-proof, and don't require waiting for JS.

# `jobs.lever.co/<slug>` -> "<slug>"
_LEVER_SLUG_RE = re.compile(
    r"^https?://jobs\.lever\.co/([^/?#]+)/?", re.IGNORECASE
)

# `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` -> "<slug>"
_GREENHOUSE_SLUG_RE = re.compile(
    r"^https?://(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)/?",
    re.IGNORECASE,
)


def _match_lever_slug(board_url: str) -> str | None:
    m = _LEVER_SLUG_RE.match(board_url.strip())
    return m.group(1) if m else None


def _match_greenhouse_slug(board_url: str) -> str | None:
    m = _GREENHOUSE_SLUG_RE.match(board_url.strip())
    return m.group(1) if m else None


def _enumerate_via_lever_api(
    slug: str,
    *,
    session: requests.Session,
    request_timeout: float,
    max_jobs: int,
) -> list[str] | None:
    """Hit Lever's public postings API. Returns None on transport failure
    so the caller can fall back to HTML scraping."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = session.get(
            url,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.info("[watchlist/lever-api] %s failed: %s", url, e)
        return None
    if not isinstance(data, list):
        return None
    out: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        hosted = item.get("hostedUrl")
        if isinstance(hosted, str) and hosted.startswith("https://"):
            out.append(hosted)
            if len(out) >= max_jobs:
                break
    log.info("[watchlist/lever-api] %d jobs for %s", len(out), slug)
    return out


def _enumerate_via_greenhouse_api(
    slug: str,
    *,
    session: requests.Session,
    request_timeout: float,
    max_jobs: int,
) -> list[str] | None:
    """Hit Greenhouse's public Job Board API. Returns None on transport failure."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = session.get(
            url,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.info("[watchlist/greenhouse-api] %s failed: %s", url, e)
        return None
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return None
    out: list[str] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        absolute = item.get("absolute_url")
        if isinstance(absolute, str) and absolute.startswith("https://"):
            out.append(absolute)
            if len(out) >= max_jobs:
                break
    log.info("[watchlist/greenhouse-api] %d jobs for %s", len(out), slug)
    return out


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
    fallback: FetchFallback | None = None,
) -> list[str]:
    """Enumerate a board's individual job-posting URLs.

    Lever and Greenhouse use their documented public JSON APIs first
    (faster, more reliable, no scraping). Other providers fall back to
    the HTML anchor walk, with Playwright escalation on 403 / JS-shell
    responses when ``fallback`` is supplied.

    Returns ``[]`` on every kind of failure — callers don't need to
    distinguish "no jobs" from "fetch broke."
    """
    sess = session or requests.Session()

    # Provider-specific JSON paths.
    lever_slug = _match_lever_slug(board_url)
    if lever_slug:
        out = _enumerate_via_lever_api(
            lever_slug, session=sess, request_timeout=request_timeout, max_jobs=max_jobs
        )
        if out is not None:
            return out
        log.info("[watchlist] lever API failed; not falling back to HTML (would be 0 jobs)")
        return []

    gh_slug = _match_greenhouse_slug(board_url)
    if gh_slug:
        out = _enumerate_via_greenhouse_api(
            gh_slug, session=sess, request_timeout=request_timeout, max_jobs=max_jobs
        )
        if out is not None:
            return out
        # Greenhouse HTML boards are real anchor-tag pages — falling back is useful.
        log.info("[watchlist] greenhouse API failed; falling back to HTML walk")

    # Generic HTML path (Ashby, Workday, unknown providers, GH fallback).
    result = fetch_with_fallback(
        board_url, session=sess, fallback=fallback, request_timeout=request_timeout
    )
    if result.status_code >= 400 or result.status_code == 0 or not result.html:
        log.info(
            "[watchlist] fetch failed for %s (status=%s)", board_url, result.status_code
        )
        return []
    return enumerate_board_html(
        html=result.html, board_url=board_url, max_jobs=max_jobs
    )
