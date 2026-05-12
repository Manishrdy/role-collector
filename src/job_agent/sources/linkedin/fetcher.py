"""Rate-limited Playwright fetcher for public LinkedIn post URLs.

Sequential (one URL at a time), with a configurable random delay
between fetches. The 30-60s default is intentionally conservative — we
never want to look like a high-rate crawler to LinkedIn.

Detects three back-off signals in the response HTML and short-circuits
the batch when any are seen:
- captcha challenge
- forced-login wall ("Sign in to LinkedIn")
- 429 status code

Returns the same ``FetchedPage`` records as the main fetcher so the
rest of the pipeline (parsers, repo) doesn't care about the origin.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from job_agent.browser.page_fetch import FetchedPage, _fetch_one, _safety_blocked
from job_agent.config import AppConfig

log = logging.getLogger(__name__)


_BLOCK_SIGNALS: tuple[str, ...] = (
    "px-captcha",  # LinkedIn captcha widget class
    "checkpoint/challenge",  # URL fragment LinkedIn redirects to under challenge
    "sign in to linkedin",  # forced-login wall
    "join linkedin to continue",  # variant wording
    "to view this profile",  # auth-walled profile view
)


@dataclass
class LinkedInFetchStats:
    urls_attempted: int = 0
    urls_ok: int = 0
    urls_blocked: int = 0
    urls_errored: int = 0
    stopped_early: bool = False
    stop_reason: str | None = None


def _is_blocked_by_linkedin(page: FetchedPage) -> str | None:
    """Return a short reason string if the page looks captcha'd / login-walled."""
    if page.http_status == 429:
        return "http_429"
    haystack = (page.html or "")[:50000].lower()
    final = (page.final_url or "").lower()
    for sig in _BLOCK_SIGNALS:
        if sig in haystack or sig in final:
            return f"block_signal:{sig}"
    return None


async def fetch_linkedin_posts_async(
    urls: list[str],
    *,
    cfg: AppConfig,
    min_delay_s: float,
    max_delay_s: float,
    nav_timeout_ms: int = 20000,
    networkidle_timeout_ms: int = 8000,
    sleep_async: Callable[[float], Awaitable[None]] | None = None,
) -> tuple[list[FetchedPage], LinkedInFetchStats]:
    """Sequentially fetch LinkedIn URLs with strict per-domain pacing.

    ``sleep_async`` lets tests inject a faster sleep. Production uses
    ``asyncio.sleep``.
    """
    stats = LinkedInFetchStats()
    if not urls:
        return [], stats

    sleeper: Callable[[float], Awaitable[None]] = sleep_async or asyncio.sleep

    # Pre-filter safety-blocked URLs as the main fetcher does.
    pre_results: list[FetchedPage | None] = [_safety_blocked(u, cfg) for u in urls]
    to_fetch = [(i, u) for i, u in enumerate(urls) if pre_results[i] is None]

    out: list[FetchedPage | None] = list(pre_results)

    if not to_fetch:
        stats.urls_blocked = sum(1 for r in pre_results if r is not None)
        return [r for r in out if r is not None], stats

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            for n, (idx, target) in enumerate(to_fetch):
                stats.urls_attempted += 1
                page = await _fetch_one(
                    context,
                    target,
                    nav_timeout_ms=nav_timeout_ms,
                    networkidle_timeout_ms=networkidle_timeout_ms,
                )
                if page.status == "ok":
                    block_reason = _is_blocked_by_linkedin(page)
                    if block_reason:
                        # Re-classify the page as blocked + stop the batch.
                        page = FetchedPage(
                            url=page.url,
                            canonical_url=page.canonical_url,
                            final_url=page.final_url,
                            domain=page.domain,
                            page_title=page.page_title,
                            http_status=page.http_status,
                            html="",
                            content_hash=None,
                            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
                            status="blocked",
                            blocked_reason=block_reason,
                        )
                        out[idx] = page
                        stats.urls_blocked += 1
                        stats.stopped_early = True
                        stats.stop_reason = block_reason
                        log.warning("[linkedin] back-off triggered (%s) — stopping batch", block_reason)
                        break
                    stats.urls_ok += 1
                elif page.status == "blocked":
                    stats.urls_blocked += 1
                else:
                    stats.urls_errored += 1
                out[idx] = page

                # Respect the rate limit BETWEEN fetches; don't sleep after the
                # last one (n+1 == len) or after a captcha break.
                if n < len(to_fetch) - 1:
                    delay = random.uniform(min_delay_s, max_delay_s)
                    log.info("[linkedin] sleeping %.1fs before next post fetch", delay)
                    await sleeper(delay)
        finally:
            try:
                await browser.close()
            except Exception as e:
                log.debug("browser close noise: %s", e)

    return [p for p in out if p is not None], stats


def fetch_linkedin_posts(
    urls: list[str],
    *,
    cfg: AppConfig,
    min_delay_s: float,
    max_delay_s: float,
) -> tuple[list[FetchedPage], LinkedInFetchStats]:
    """Sync entrypoint for the rate-limited fetcher."""
    return asyncio.run(
        fetch_linkedin_posts_async(
            urls=urls,
            cfg=cfg,
            min_delay_s=min_delay_s,
            max_delay_s=max_delay_s,
        )
    )
