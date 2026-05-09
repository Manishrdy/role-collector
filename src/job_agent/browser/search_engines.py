"""Google search driver using ``nodriver``.

Why nodriver, not Playwright: every Playwright/Puppeteer/Selenium variant we
tried was blocked by Google because they detect the Chrome DevTools Protocol
artifacts those libraries leak. ``nodriver`` connects to Chrome's WebSocket
debugging endpoint with a custom protocol implementation that omits those
artifacts, and Google currently allows it through. See
``scripts/smoke_google_nodriver.py`` for the proof-of-concept run.

This module exposes:

- Pure helpers (URL builders, block detection, HTML parsing) that are sync
  and easy to test against fixture HTML.
- ``run_google_search_async`` that drives nodriver with an existing browser
  to keep the orchestrator's single-session model.

Bing has been removed: it served stripped pages on every test, so it was
never a real fallback.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote_plus, unquote, urlparse

if TYPE_CHECKING:
    import nodriver

log = logging.getLogger(__name__)


TimeWindow = Literal["past_24h", "past_48h", "past_week", "any"]


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
# URL builders


_GOOGLE_TBS = {
    "past_24h": "qdr:d",
    "past_48h": "qdr:d2",
    "past_week": "qdr:w",
}


def google_search_url(query: str, time_window: TimeWindow, *, num: int = 10) -> str:
    """Build a Google search URL with the requested time filter."""
    parts = [f"q={quote_plus(query)}", f"num={num}", "hl=en"]
    tbs = _GOOGLE_TBS.get(time_window)
    if tbs:
        parts.append(f"tbs={tbs}")
    return "https://www.google.com/search?" + "&".join(parts)


# ---------------------------------------------------------------------------
# Block detection


_GOOGLE_BLOCK_HOSTS = ("sorry.google.com", "ipv4.google.com")
_GOOGLE_BLOCK_TEXTS = (
    "unusual traffic from your computer network",
    "Our systems have detected unusual traffic",
    "to continue, please type the characters",
)


def detect_block(*, current_url: str, page_text: str) -> str | None:
    """Return a short reason string if the page looks blocked, else None."""
    if "/sorry/index" in current_url:
        return "google_block_redirect"
    host = (urlparse(current_url).hostname or "").lower()
    if any(b in host for b in _GOOGLE_BLOCK_HOSTS):
        return f"google_block_host:{host}"
    for n in _GOOGLE_BLOCK_TEXTS:
        if n.lower() in page_text.lower():
            return "google_block_text"
    return None


# ---------------------------------------------------------------------------
# Result parsing


_GOOGLE_REDIRECT_RE = re.compile(r"^/url\?q=([^&]+)")


def _unwrap_google_redirect(href: str) -> str:
    m = _GOOGLE_REDIRECT_RE.match(href)
    if m:
        return unquote(m.group(1))
    return href


def _normalise_url(href: str) -> str:
    """Drop fragments and known tracking params we don't care about."""
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return href
    netloc = (parsed.hostname or "").lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return f"{parsed.scheme.lower()}://{netloc}{parsed.path}" + (
        f"?{parsed.query}" if parsed.query else ""
    )


def parse_results(
    *,
    page_html: str,
    target_domain: str | None,
    query: str,
    time_window: TimeWindow,
    max_results: int,
    engine: str = "google",
) -> list[SearchResult]:
    """Parse a results page into ``SearchResult`` records.

    URL-pattern primary, CSS fallback for snippets. Resilient to Google's
    DOM-class churn since we don't depend on container classes for URL
    extraction.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page_html, "lxml")

    raw_links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href_attr = a.get("href")
        href = href_attr if isinstance(href_attr, str) else " ".join(href_attr or [])
        if href.startswith("/url?"):
            href = _unwrap_google_redirect(href)
        if not href.startswith("http"):
            continue
        host = (urlparse(href).hostname or "").lower()
        if target_domain and not (
            host == target_domain or host.endswith("." + target_domain) or target_domain in host
        ):
            continue
        # Drop the engine's own internal links and Google services.
        if engine == "google" and (
            "google.com" in host or "googleusercontent" in host or "youtube.com" in host
        ):
            continue
        title = a.get_text(strip=True) or ""
        raw_links.append((href, title))

    # Dedupe by canonical URL while preserving first-seen rank.
    seen: dict[str, tuple[int, str]] = {}
    rank_counter = 0
    for href, title in raw_links:
        try:
            canonical = _normalise_url(href)
        except Exception:
            continue
        if canonical in seen:
            existing_rank, existing_title = seen[canonical]
            if not existing_title and title:
                seen[canonical] = (existing_rank, title)
            continue
        rank_counter += 1
        seen[canonical] = (rank_counter, title)
        if len(seen) >= max_results:
            break

    # Snippet extraction (best-effort).
    snippets_by_url: dict[str, str] = {}
    for div in soup.find_all(["div", "span", "p"]):
        text = div.get_text(" ", strip=True)
        if not text or len(text) < 30:
            continue
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
            canonical = _normalise_url(href)
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
# Live nodriver driver (async)


async def run_google_search_async(
    browser: nodriver.Browser,
    *,
    query: str,
    time_window: TimeWindow,
    target_domain: str | None,
    max_results: int = 10,
    settle_seconds: float = 3.0,
    debug_dump_dir: Path | None = None,
) -> SearchOutcome:
    """Open Google for ``query`` via an existing nodriver Browser session.

    Reuses the browser across calls — far faster than spawning per query
    and keeps any cookies that help avoid re-challenges.
    """
    import asyncio

    url = google_search_url(query, time_window, num=max_results)
    log.info("[google] %s", url)
    page = await browser.get(url)
    await asyncio.sleep(settle_seconds)

    current_url = page.target.url if page.target else url
    page_html = await page.get_content()
    try:
        body_text = (
            await page.evaluate("document.body && document.body.innerText.slice(0, 1500)") or ""
        )
    except Exception:
        body_text = ""

    if debug_dump_dir is not None:
        _dump_page(
            html=page_html,
            current_url=current_url,
            dump_dir=debug_dump_dir,
            query=query,
        )

    blocked = detect_block(current_url=current_url, page_text=body_text)
    if blocked:
        log.warning("[google] blocked: %s (url=%s)", blocked, current_url)
        return SearchOutcome(
            engine="google",
            query=query,
            time_window=time_window,
            results=[],
            blocked_reason=blocked,
        )

    results = parse_results(
        page_html=page_html,
        target_domain=target_domain,
        query=query,
        time_window=time_window,
        max_results=max_results,
    )
    log.info("[google] %d results for %r", len(results), query)
    return SearchOutcome(
        engine="google",
        query=query,
        time_window=time_window,
        results=results,
    )


def _safe_filename(s: str, *, max_len: int = 60) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:max_len].strip("_")
    return cleaned or "query"


def _dump_page(*, html: str, current_url: str, dump_dir: Path, query: str) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    stem = f"google_{_safe_filename(query)}"
    (dump_dir / f"{stem}.html").write_text(html, encoding="utf-8")
    (dump_dir / f"{stem}.url.txt").write_text(current_url, encoding="utf-8")
    log.info("[google] debug dump -> %s", dump_dir / f"{stem}.html")
