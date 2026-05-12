"""Per-provider ATS API enumerators.

Each ``enumerate_<provider>(slug)`` returns ``list[str] | None``:

- ``list[str]``: canonical posting URLs (possibly empty if the company
  has no public postings).
- ``None``: API transport error (HTTP failure, malformed JSON). Caller
  should consider falling back to HTML scraping.

All clients use plain ``requests``. They're sync and stateless; safe to
call in parallel from threads, though we currently iterate sequentially
in the orchestrator.
"""

from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_DEFAULT_TIMEOUT_S = 15.0


# Slug parsers — derive a company slug from various URL shapes the
# resolvers + Google may surface.

_LEVER_SLUG_URL_RE = re.compile(
    r"^https?://jobs\.lever\.co/([^/?#]+)/?", re.IGNORECASE
)
_GREENHOUSE_SLUG_URL_RE = re.compile(
    r"^https?://(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)/?", re.IGNORECASE
)
_ASHBY_SLUG_URL_RE = re.compile(
    r"^https?://jobs\.ashbyhq\.com/([^/?#]+)/?", re.IGNORECASE
)


def lever_slug_from_url(url: str) -> str | None:
    m = _LEVER_SLUG_URL_RE.match(url.strip())
    return m.group(1) if m else None


def greenhouse_slug_from_url(url: str) -> str | None:
    m = _GREENHOUSE_SLUG_URL_RE.match(url.strip())
    return m.group(1) if m else None


def ashby_slug_from_url(url: str) -> str | None:
    m = _ASHBY_SLUG_URL_RE.match(url.strip())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Provider clients


def enumerate_lever(
    slug: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = _DEFAULT_TIMEOUT_S,
    max_jobs: int = 100,
) -> list[str] | None:
    """Hit Lever's public postings API; return ``hostedUrl`` per posting."""
    sess = session or requests.Session()
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = sess.get(
            url,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.info("[ats-api/lever] %s failed: %s", url, e)
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
    log.info("[ats-api/lever] %d jobs for %s", len(out), slug)
    return out


def enumerate_greenhouse(
    slug: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = _DEFAULT_TIMEOUT_S,
    max_jobs: int = 100,
) -> list[str] | None:
    """Hit Greenhouse's public board API; return ``absolute_url`` per posting."""
    sess = session or requests.Session()
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = sess.get(
            url,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.info("[ats-api/greenhouse] %s failed: %s", url, e)
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
    log.info("[ats-api/greenhouse] %d jobs for %s", len(out), slug)
    return out


def enumerate_ashby(
    slug: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = _DEFAULT_TIMEOUT_S,
    max_jobs: int = 100,
) -> list[str] | None:
    """Hit Ashby's public posting-api/job-board endpoint; return per-posting URLs.

    Ashby's payload shape: ``{"jobs": [{"id": "uuid", "title": "...", ...}, ...]}``
    Posting URLs are constructed: ``https://jobs.ashbyhq.com/<slug>/<id>``.
    """
    sess = session or requests.Session()
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = sess.get(
            url,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.info("[ats-api/ashby] %s failed: %s", url, e)
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
        job_id = item.get("id")
        # Ashby publishes jobUrl in some responses; prefer it when present.
        explicit_url = item.get("jobUrl")
        if isinstance(explicit_url, str) and explicit_url.startswith("https://"):
            out.append(explicit_url)
        elif isinstance(job_id, str) and job_id:
            out.append(f"https://jobs.ashbyhq.com/{slug}/{job_id}")
        if len(out) >= max_jobs:
            break
    log.info("[ats-api/ashby] %d jobs for %s", len(out), slug)
    return out
