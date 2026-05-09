"""Async Playwright fetcher for ATS detail pages.

Phase 3 detail pages (Ashby, Greenhouse, Lever, etc.) have no bot
detection per ``scripts/smoke_ashby.py``, so plain Playwright is
sufficient — nodriver is only needed at the Google front door.

Key behaviours:

* One browser + one context shared across the batch (fast, cheap).
* ``asyncio.Semaphore(concurrency)`` bounds in-flight pages.
* Every URL is run through :func:`job_agent.browser.safety.check_url`
  before navigation. Rejects are returned as ``status="blocked"`` so
  the caller can attribute them in the ``page_fetches`` audit log.
* Errors never propagate — they become ``FetchedPage`` rows with a
  populated ``error`` field. The pipeline degrades, never crashes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from job_agent.browser.safety import (
    canonicalize_url,
    check_url,
    get_domain,
)
from job_agent.config import AppConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchedPage:
    """Result of one page fetch. ``html`` is empty when ``error`` is set."""

    url: str
    canonical_url: str
    final_url: str | None
    domain: str
    page_title: str | None
    http_status: int | None
    html: str
    content_hash: str | None
    fetched_at: str
    status: str  # ok | blocked | error
    error: str | None = None
    blocked_reason: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _fetch_one(
    context: object,
    url: str,
    *,
    nav_timeout_ms: int,
    networkidle_timeout_ms: int,
) -> FetchedPage:
    """Open ``url`` in a fresh page on ``context``. Always returns a row."""
    canonical = canonicalize_url(url)
    domain = get_domain(url)
    page = await context.new_page()  # type: ignore[attr-defined]
    try:
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=nav_timeout_ms
            )
        except Exception as e:
            return FetchedPage(
                url=url,
                canonical_url=canonical,
                final_url=None,
                domain=domain,
                page_title=None,
                http_status=None,
                html="",
                content_hash=None,
                fetched_at=_utc_now_iso(),
                status="error",
                error=f"goto failed: {e}",
            )

        # Ashby/Greenhouse/Lever ship JSON or DOM at DOMContentLoaded; the
        # networkidle wait is best-effort to let SPA shells settle. We
        # swallow timeouts because some sites never go fully idle.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state(
                "networkidle", timeout=networkidle_timeout_ms
            )

        try:
            html = await page.content()
            title = await page.title()
            final_url = page.url
        except Exception as e:
            return FetchedPage(
                url=url,
                canonical_url=canonical,
                final_url=None,
                domain=domain,
                page_title=None,
                http_status=response.status if response is not None else None,
                html="",
                content_hash=None,
                fetched_at=_utc_now_iso(),
                status="error",
                error=f"read failed: {e}",
            )

        content_hash = hashlib.sha256(html.encode("utf-8", errors="ignore")).hexdigest()
        return FetchedPage(
            url=url,
            canonical_url=canonical,
            final_url=final_url,
            domain=domain,
            page_title=title or None,
            http_status=response.status if response is not None else None,
            html=html,
            content_hash=content_hash,
            fetched_at=_utc_now_iso(),
            status="ok",
        )
    finally:
        try:
            await page.close()
        except Exception as e:
            log.debug("page close noise: %s", e)


def _safety_blocked(url: str, cfg: AppConfig) -> FetchedPage | None:
    decision = check_url(url, cfg=cfg)
    if decision.allowed:
        return None
    return FetchedPage(
        url=url,
        canonical_url=decision.canonical_url or canonicalize_url(url),
        final_url=None,
        domain=get_domain(url),
        page_title=None,
        http_status=None,
        html="",
        content_hash=None,
        fetched_at=_utc_now_iso(),
        status="blocked",
        blocked_reason=decision.reason,
    )


async def fetch_pages_async(
    urls: list[str],
    *,
    cfg: AppConfig,
    concurrency: int = 4,
    nav_timeout_ms: int = 20000,
    networkidle_timeout_ms: int = 8000,
) -> list[FetchedPage]:
    """Fetch many URLs in parallel, returning one row per input URL.

    Output order matches input order; deduplication is the caller's job.
    Safety-rejected URLs never touch the browser.
    """
    if not urls:
        return []

    pre_results: list[FetchedPage | None] = [_safety_blocked(u, cfg) for u in urls]
    to_fetch = [(i, u) for i, u in enumerate(urls) if pre_results[i] is None]

    if not to_fetch:
        return [r for r in pre_results if r is not None]

    from playwright.async_api import async_playwright

    sem = asyncio.Semaphore(max(1, concurrency))

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

            async def _bounded(idx: int, target: str) -> tuple[int, FetchedPage]:
                async with sem:
                    return idx, await _fetch_one(
                        context,
                        target,
                        nav_timeout_ms=nav_timeout_ms,
                        networkidle_timeout_ms=networkidle_timeout_ms,
                    )

            results = await asyncio.gather(
                *(_bounded(i, u) for i, u in to_fetch), return_exceptions=False
            )
        finally:
            try:
                await browser.close()
            except Exception as e:
                log.debug("browser close noise: %s", e)

    out: list[FetchedPage | None] = [None] * len(urls)
    for i, blocked in enumerate(pre_results):
        if blocked is not None:
            out[i] = blocked
    for idx, fetched in results:
        out[idx] = fetched
    assert all(p is not None for p in out)
    return [p for p in out if p is not None]


def fetch_pages(
    urls: list[str],
    *,
    cfg: AppConfig,
    concurrency: int = 4,
    nav_timeout_ms: int = 20000,
    networkidle_timeout_ms: int = 8000,
) -> list[FetchedPage]:
    """Sync entrypoint mirroring :func:`fetch_pages_async`."""
    return asyncio.run(
        fetch_pages_async(
            urls,
            cfg=cfg,
            concurrency=concurrency,
            nav_timeout_ms=nav_timeout_ms,
            networkidle_timeout_ms=networkidle_timeout_ms,
        )
    )
