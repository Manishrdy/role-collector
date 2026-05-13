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
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

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
_SMARTRECRUITERS_SLUG_URL_RE = re.compile(
    r"^https?://jobs\.smartrecruiters\.com/([^/?#]+)/?", re.IGNORECASE
)
_ICIMS_SLUG_URL_RE = re.compile(
    r"^https?://careers\.([^.]+)\.icims\.com/jobs/", re.IGNORECASE
)
_WORKDAY_URL_RE = re.compile(
    r"^https?://[^/]+\.(?:myworkdayjobs|myworkdaysite)\.com/.+", re.IGNORECASE
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


def smartrecruiters_slug_from_url(url: str) -> str | None:
    m = _SMARTRECRUITERS_SLUG_URL_RE.match(url.strip())
    return m.group(1) if m else None


def icims_slug_from_url(url: str) -> str | None:
    m = _ICIMS_SLUG_URL_RE.match(url.strip())
    return m.group(1) if m else None


def workday_slug_from_url(url: str) -> str | None:
    raw = url.strip()
    m = _WORKDAY_URL_RE.match(raw)
    if not m:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return None
    first_segment = parsed.path.strip("/").split("/")[0]
    if not first_segment:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/{first_segment}"


def _iso_from_datetimeish(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _iso_from_ms(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    try:
        dt = datetime.fromtimestamp(value / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Provider clients


def enumerate_lever(
    slug: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = _DEFAULT_TIMEOUT_S,
    max_jobs: int = 100,
) -> list[dict[str, Any]] | None:
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
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        hosted = item.get("hostedUrl")
        if isinstance(hosted, str) and hosted.startswith("https://"):
            posted = _iso_from_ms(item.get("createdAt"))
            categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
            commitment = categories.get("commitment") if isinstance(categories, dict) else None
            wp = item.get("workplaceType")
            remote_type: str | None = None
            if isinstance(wp, str):
                wpn = wp.strip().lower()
                if wpn == "remote":
                    remote_type = "remote"
                elif wpn == "hybrid":
                    remote_type = "hybrid"
                elif wpn in {"on-site", "onsite", "in-office"}:
                    remote_type = "onsite"
            out.append(
                {
                    "url": hosted,
                    "posted_at_source": posted,
                    "employment_type": commitment,
                    "remote_type": remote_type,
                    "location": categories.get("location") if isinstance(categories, dict) else None,
                    "ats_job_id": item.get("id"),
                }
            )
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
) -> list[dict[str, Any]] | None:
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
    out: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        absolute = item.get("absolute_url")
        if isinstance(absolute, str) and absolute.startswith("https://"):
            location_obj = item.get("location") if isinstance(item.get("location"), dict) else {}
            posted = _iso_from_datetimeish(item.get("updated_at")) or _iso_from_datetimeish(item.get("first_published"))
            out.append(
                {
                    "url": absolute,
                    "posted_at_source": posted,
                    "employment_type": None,
                    "remote_type": None,
                    "location": location_obj.get("name") if isinstance(location_obj, dict) else None,
                    "ats_job_id": item.get("id"),
                }
            )
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
) -> list[dict[str, Any]] | None:
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
    out: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        job_id = item.get("id")
        # Ashby publishes jobUrl in some responses; prefer it when present.
        explicit_url = item.get("jobUrl")
        resolved_url: str | None = None
        if isinstance(explicit_url, str) and explicit_url.startswith("https://"):
            resolved_url = explicit_url
        elif isinstance(job_id, str) and job_id:
            resolved_url = f"https://jobs.ashbyhq.com/{slug}/{job_id}"
        if resolved_url:
            wp = item.get("workplaceType")
            remote_type: str | None = None
            if isinstance(wp, str):
                wpn = wp.strip().lower().replace("-", "").replace(" ", "")
                if wpn == "remote":
                    remote_type = "remote"
                elif wpn == "hybrid":
                    remote_type = "hybrid"
                elif wpn in {"onsite", "inperson", "office"}:
                    remote_type = "onsite"
            out.append(
                {
                    "url": resolved_url,
                    "posted_at_source": _iso_from_datetimeish(item.get("publishedAt")),
                    "employment_type": item.get("employmentType"),
                    "remote_type": remote_type,
                    "location": item.get("location"),
                    "ats_job_id": job_id,
                }
            )
        if len(out) >= max_jobs:
            break
    log.info("[ats-api/ashby] %d jobs for %s", len(out), slug)
    return out


def enumerate_smartrecruiters(
    slug: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = _DEFAULT_TIMEOUT_S,
    max_jobs: int = 100,
) -> list[dict[str, Any]] | None:
    """Hit SmartRecruiters listing API with pagination."""
    sess = session or requests.Session()
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    offset = 0
    page_limit = 100
    out: list[dict[str, Any]] = []
    while len(out) < max_jobs:
        try:
            resp = sess.get(
                base,
                timeout=request_timeout,
                params={"limit": page_limit, "offset": offset},
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.info("[ats-api/smartrecruiters] %s failed: %s", base, e)
            return None
        if not isinstance(data, dict):
            return None
        content = data.get("content")
        if not isinstance(content, list):
            return None
        if not content:
            break
        for item in content:
            if not isinstance(item, dict):
                continue
            job_id = item.get("id")
            if not isinstance(job_id, str) or not job_id:
                continue
            url = f"https://jobs.smartrecruiters.com/{slug}/{job_id}"
            location = item.get("location") if isinstance(item.get("location"), dict) else {}
            remote_type = None
            if isinstance(location, dict):
                if location.get("remote") is True:
                    remote_type = "remote"
                elif location.get("remote") is False:
                    remote_type = "onsite"
            out.append(
                {
                    "url": url,
                    "posted_at_source": _iso_from_datetimeish(item.get("releasedDate")),
                    "employment_type": (item.get("typeOfEmployment") or {}).get("label")
                    if isinstance(item.get("typeOfEmployment"), dict)
                    else None,
                    "remote_type": remote_type,
                    "location": ", ".join(
                        p for p in [location.get("city"), location.get("region"), location.get("country")] if isinstance(p, str) and p
                    ) or None,
                    "ats_job_id": job_id,
                }
            )
            if len(out) >= max_jobs:
                break
        if len(content) < page_limit:
            break
        offset += page_limit
    log.info("[ats-api/smartrecruiters] %d jobs for %s", len(out), slug)
    return out


def enumerate_icims(
    slug: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = _DEFAULT_TIMEOUT_S,
    max_jobs: int = 100,
) -> list[dict[str, Any]] | None:
    """Scrape iCIMS jobs from public search endpoint."""
    sess = session or requests.Session()
    url = f"https://careers.{slug}.icims.com/jobs/search"
    params = {"ss": "1&searchRelation=keyword_all"}
    try:
        resp = sess.get(
            url,
            params=params,
            timeout=request_timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.info("[ats-api/icims] %s failed: %s", url, e)
        return None
    html = resp.text or ""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not isinstance(href, str):
            continue
        m = re.search(r"(/jobs/\d+/[^\"#?]+)", href, flags=re.IGNORECASE)
        if not m:
            continue
        path = m.group(1)
        full = f"https://careers.{slug}.icims.com{path}"
        if full in seen:
            continue
        seen.add(full)
        job_id_match = re.search(r"/jobs/(\d+)/", path)
        card = a.find_parent(["div", "tr", "li", "article"])
        card_text = card.get_text(" ", strip=True) if card is not None else ""
        posted_at_source = None
        posted_match = re.search(
            r"(Posted|Date Posted)\s*:?\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
            card_text,
            flags=re.IGNORECASE,
        )
        if posted_match:
            try:
                posted_dt = datetime.strptime(posted_match.group(2), "%b %d, %Y")
            except ValueError:
                try:
                    posted_dt = datetime.strptime(posted_match.group(2), "%B %d, %Y")
                except ValueError:
                    posted_dt = None
            if posted_dt is not None:
                posted_at_source = posted_dt.replace(tzinfo=UTC).isoformat(timespec="seconds")

        location = None
        loc_match = re.search(r"(Location|Locations)\s*:?\s*([A-Za-z0-9, ./\\-]+)", card_text)
        if loc_match:
            location = loc_match.group(2).strip()
            location = re.split(r"\b(Date Posted|Posted)\b", location, maxsplit=1)[0].strip(" -,:")
        out.append(
            {
                "url": full,
                "posted_at_source": posted_at_source,
                "employment_type": None,
                "remote_type": None,
                "location": location,
                "ats_job_id": job_id_match.group(1) if job_id_match else None,
            }
        )
        if len(out) >= max_jobs:
            break
    log.info("[ats-api/icims] %d jobs for %s", len(out), slug)
    return out


def enumerate_workday(
    slug: str,
    *,
    session: requests.Session | None = None,
    request_timeout: float = _DEFAULT_TIMEOUT_S,
    max_jobs: int = 100,
) -> list[dict[str, Any]] | None:
    """Enumerate Workday jobs from a board root URL."""
    sess = session or requests.Session()
    board_url = slug.strip().rstrip("/")
    parsed = urlparse(board_url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parsed.netloc or len(parts) < 3:
        return None
    company = parts[1]
    site = parts[2]
    root_segment = parts[0]
    root = f"{parsed.scheme}://{parsed.netloc}/{root_segment}"
    api_url = f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{company}/{site}/jobs"
    limit = 20
    offset = 0
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    while len(out) < max_jobs:
        body = {"limit": limit, "offset": offset, "searchText": ""}
        try:
            resp = sess.post(
                api_url,
                timeout=request_timeout,
                json=body,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.info("[ats-api/workday] %s failed: %s", api_url, e)
            return None
        if not isinstance(data, dict):
            return None
        postings = data.get("jobPostings")
        if not isinstance(postings, list) or not postings:
            break
        for item in postings:
            if not isinstance(item, dict):
                continue
            ext_path = item.get("externalPath")
            if not isinstance(ext_path, str) or not ext_path:
                continue
            post_url = f"{root}{ext_path}"
            if post_url in seen:
                continue
            seen.add(post_url)
            out.append(
                {
                    "url": post_url,
                    "posted_at_source": _iso_from_datetimeish(item.get("postedOn")),
                    "employment_type": item.get("timeType"),
                    "remote_type": (
                        "remote"
                        if isinstance(item.get("locationsText"), str)
                        and "remote" in item.get("locationsText").lower()
                        else None
                    ),
                    "location": item.get("locationsText"),
                    "ats_job_id": ext_path.rsplit("/", 1)[-1] if "/" in ext_path else ext_path,
                }
            )
            if len(out) >= max_jobs:
                break
        if len(postings) < limit:
            break
        offset += limit
    log.info("[ats-api/workday] %d jobs for %s", len(out), root)
    return out
