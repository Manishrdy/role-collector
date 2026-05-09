"""Sync Playwright drivers for Google + Bing public web search.

Design choices:

- **Time filter via URL params**, not UI clicks. Google uses ``tbs=qdr:d|d2|w``;
  Bing uses ``filters=ex1:"ez1|ez2|ez3"`` (day / 2 days / week). Both are
  far more stable than clicking through the search-tools menu.
- **URL-pattern-first parsing**. We pull every anchor href on the result page,
  filter to the target ATS domain, then enrich with title/snippet via CSS
  selectors when available. If Google rearranges its result-card markup
  the URL extraction still works.
- **Block detection** runs before parsing. If the page redirected to
  ``sorry.google.com`` or shows a recaptcha element, we return a `Blocked`
  result so the orchestrator can switch engines instead of polluting the
  pipeline with empty-but-successful searches.

Tests cover the URL builders and HTML parsers against fixture pages so the
live calls are exercised only by the manual smoke run.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus, urlparse

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

from job_agent.browser.safety import canonicalize_url, get_domain
from job_agent.sources.queries import TimeWindow

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    canonical_url: str
    snippet: str
    rank: int
    engine: str
    query: str
    time_window: TimeWindow


@dataclass(frozen=True)
class SearchOutcome:
    """Result of one search attempt. Either we got results or we got blocked."""

    engine: str
    query: str
    time_window: TimeWindow
    results: list[SearchResult]
    blocked_reason: str | None = None

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None


# ---------------------------------------------------------------------------
# URL builders (pure — unit-testable)


_GOOGLE_TBS = {
    "past_24h": "qdr:d",
    "past_48h": "qdr:d2",
    "past_week": "qdr:w",
}

# Bing: ex1=ez1 (day), ez2 (week-ish in some experiments — Bing's "past 24h"
# corresponds to ez1, which we treat as the closest analogue for past_24h
# AND past_48h since Bing has no native 48h option). past_week -> ez3 — wait,
# Bing's documented mapping: ez1=day, ez2=week, ez3=month. We approximate.
_BING_FILTERS = {
    "past_24h": 'ex1:"ez1"',
    "past_48h": 'ex1:"ez1"',  # closest available; orchestrator dedupes anyway
    "past_week": 'ex1:"ez2"',
}


def google_search_url(query: str, time_window: TimeWindow, *, num: int = 10) -> str:
    """Build a Google search URL with the requested time filter.

    `num` is treated as a hint; Google does not always honour it.
    """
    parts = [f"q={quote_plus(query)}", f"num={num}"]
    tbs = _GOOGLE_TBS.get(time_window)
    if tbs:
        parts.append(f"tbs={tbs}")
    # Disable Google's instant-answer / personalisation panels.
    parts.append("hl=en")
    return "https://www.google.com/search?" + "&".join(parts)


def bing_search_url(query: str, time_window: TimeWindow, *, count: int = 10) -> str:
    parts = [f"q={quote_plus(query)}", f"count={count}"]
    flt = _BING_FILTERS.get(time_window)
    if flt:
        parts.append(f"filters={quote_plus(flt)}")
    return "https://www.bing.com/search?" + "&".join(parts)


# ---------------------------------------------------------------------------
# Block detection


_GOOGLE_BLOCK_HOSTS = ("sorry.google.com", "ipv4.google.com")
_GOOGLE_BLOCK_TEXTS = (
    "unusual traffic from your computer network",
    "Our systems have detected unusual traffic",
    "to continue, please type the characters",
)
_BING_BLOCK_TEXTS = (
    "We're sorry, but we are unable to display results",
    "to give us a chance to verify",
)


def detect_block(*, current_url: str, page_text: str, engine: str) -> str | None:
    """Return a short reason string if the page looks blocked, else None."""
    host = (urlparse(current_url).hostname or "").lower()
    if engine == "google" and any(b in host for b in _GOOGLE_BLOCK_HOSTS):
        return f"google_block_redirect:{host}"
    needles = _GOOGLE_BLOCK_TEXTS if engine == "google" else _BING_BLOCK_TEXTS
    for n in needles:
        if n.lower() in page_text.lower():
            return f"{engine}_block_text"
    if "captcha" in page_text.lower() and "g-recaptcha" in page_text.lower():
        return f"{engine}_captcha"
    return None


# ---------------------------------------------------------------------------
# Result parsing — URL-pattern primary, CSS fallback


# Google wraps outbound links in /url?q=... redirects sometimes; strip those.
_GOOGLE_URL_PREFIX = re.compile(r"^/url\?q=([^&]+)")


def _unwrap_google_redirect(href: str) -> str:
    m = _GOOGLE_URL_PREFIX.match(href)
    if m:
        from urllib.parse import unquote

        return unquote(m.group(1))
    return href


def parse_results(
    *,
    page_html: str,
    page: Any | None,
    target_domain: str | None,
    engine: str,
    query: str,
    time_window: TimeWindow,
    max_results: int,
) -> list[SearchResult]:
    """Parse a results page into SearchResult records.

    Uses Playwright's locator API when ``page`` is provided (live mode),
    otherwise falls back to a BeautifulSoup parse for tests with fixture HTML.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page_html, "lxml")

    # Step 1: URL-pattern primary — every anchor on the page, filtered.
    raw_links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        # bs4 4.14+ types attrs as str | AttributeValueList; coerce.
        href_attr = a.get("href")
        href = href_attr if isinstance(href_attr, str) else " ".join(href_attr or [])
        if href.startswith("/url?"):
            href = _unwrap_google_redirect(href)
        if not href.startswith("http"):
            continue
        host = get_domain(href)
        # Match exact host or subdomain suffix (e.g. boards.greenhouse.io
        # matches greenhouse.io target).
        if target_domain and not (
            host == target_domain or host.endswith("." + target_domain) or target_domain in host
        ):
            continue
        # Skip the engine's own internal links and our own search anchors.
        if engine == "google" and ("google.com" in host or "googleusercontent" in host):
            continue
        if engine == "bing" and "bing.com" in host:
            continue
        title = a.get_text(strip=True) or ""
        raw_links.append((href, title))

    # Step 2: dedupe by canonical URL while preserving first-seen rank.
    seen: dict[str, tuple[int, str]] = {}
    rank_counter = 0
    for href, title in raw_links:
        try:
            canonical = canonicalize_url(href)
        except Exception:
            continue
        if canonical in seen:
            # Prefer a longer/non-empty title from earlier or later occurrence.
            existing_rank, existing_title = seen[canonical]
            if not existing_title and title:
                seen[canonical] = (existing_rank, title)
            continue
        rank_counter += 1
        seen[canonical] = (rank_counter, title)
        if len(seen) >= max_results:
            break

    # Step 3: snippet extraction (best-effort).
    snippets_by_url: dict[str, str] = {}
    for div in soup.find_all(["div", "span", "p"]):
        text = div.get_text(" ", strip=True)
        if not text or len(text) < 30:
            continue
        # Look at nearby anchor href as the most likely owner of this snippet.
        anchor = div.find("a", href=True)
        if not anchor:
            continue
        href_attr = anchor.get("href")
        href = href_attr if isinstance(href_attr, str) else " ".join(href_attr or [])
        if href.startswith("/url?"):
            href = _unwrap_google_redirect(href)
        if not href.startswith("http"):
            continue
        try:
            canonical = canonicalize_url(href)
        except Exception:
            continue
        if canonical in seen and canonical not in snippets_by_url:
            snippets_by_url[canonical] = text[:400]

    out: list[SearchResult] = []
    for canonical, (rank, title) in sorted(seen.items(), key=lambda kv: kv[1][0]):
        out.append(
            SearchResult(
                title=title,
                url=canonical,
                canonical_url=canonical,
                snippet=snippets_by_url.get(canonical, ""),
                rank=rank,
                engine=engine,
                query=query,
                time_window=time_window,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Live drivers


def run_google_search(
    context: BrowserContext,
    *,
    query: str,
    time_window: TimeWindow,
    target_domain: str | None,
    max_results: int = 10,
    timeout_ms: int = 15000,
    debug_dump_dir: Path | None = None,
) -> SearchOutcome:
    """Open Google for the given query and parse results in the same context."""
    url = google_search_url(query, time_window, num=max_results)
    return _run_engine(
        context,
        engine="google",
        url=url,
        query=query,
        time_window=time_window,
        target_domain=target_domain,
        max_results=max_results,
        timeout_ms=timeout_ms,
        debug_dump_dir=debug_dump_dir,
    )


def run_bing_search(
    context: BrowserContext,
    *,
    query: str,
    time_window: TimeWindow,
    target_domain: str | None,
    max_results: int = 10,
    timeout_ms: int = 15000,
    debug_dump_dir: Path | None = None,
) -> SearchOutcome:
    url = bing_search_url(query, time_window, count=max_results)
    return _run_engine(
        context,
        engine="bing",
        url=url,
        query=query,
        time_window=time_window,
        target_domain=target_domain,
        max_results=max_results,
        timeout_ms=timeout_ms,
        debug_dump_dir=debug_dump_dir,
    )


def _safe_filename(s: str, *, max_len: int = 60) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:max_len].strip("_")
    return cleaned or "query"


def _dump_page(
    *, page: Page, html: str, current_url: str, dump_dir: Path, engine: str, query: str
) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{engine}_{_safe_filename(query)}"
    (dump_dir / f"{stem}.html").write_text(html, encoding="utf-8")
    (dump_dir / f"{stem}.url.txt").write_text(current_url, encoding="utf-8")
    with contextlib.suppress(Exception):
        page.screenshot(path=str(dump_dir / f"{stem}.png"), full_page=False)
    log.info("[%s] debug dump written to %s", engine, dump_dir / f"{stem}.html")


def _run_engine(
    context: BrowserContext,
    *,
    engine: str,
    url: str,
    query: str,
    time_window: TimeWindow,
    target_domain: str | None,
    max_results: int,
    timeout_ms: int,
    debug_dump_dir: Path | None = None,
) -> SearchOutcome:
    page: Page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=3000)

        page_html = page.content()
        page_text = page.inner_text("body") if page.locator("body").count() else ""
        current_url = page.url

        if debug_dump_dir is not None:
            _dump_page(
                page=page,
                html=page_html,
                current_url=current_url,
                dump_dir=debug_dump_dir,
                engine=engine,
                query=query,
            )

        blocked = detect_block(current_url=current_url, page_text=page_text, engine=engine)
        if blocked:
            log.warning("[%s] blocked: %s (url=%s)", engine, blocked, current_url)
            return SearchOutcome(
                engine=engine,
                query=query,
                time_window=time_window,
                results=[],
                blocked_reason=blocked,
            )

        results = parse_results(
            page_html=page_html,
            page=page,
            target_domain=target_domain,
            engine=engine,
            query=query,
            time_window=time_window,
            max_results=max_results,
        )
        log.info("[%s] %d results for %r", engine, len(results), query)
        return SearchOutcome(
            engine=engine,
            query=query,
            time_window=time_window,
            results=results,
        )
    finally:
        page.close()
