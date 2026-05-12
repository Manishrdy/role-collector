"""Playwright fallback for sites that block plain HTTP or render the
content client-side. Used by both the funding resolvers (careers /
ATS) and the watchlist board enumerator.

The fallback is opt-in via the ``FetchFallback`` context manager. Pass
``None`` and callers fall back to a no-op (the original requests-only
behaviour). Pass an instance and:

1. Plain ``requests.get`` runs first.
2. If the response looks blocked or JS-rendered (403 / 429 / too small
   / no anchor tags), the URL is re-fetched via a headless Playwright
   browser that holds open across the whole resolver / watchlist run.
3. The Playwright session is lazy-initialised — it only launches on
   the first URL that actually needs it.

This lets a typical run that never hits a Cloudflare wall pay zero
Playwright overhead, while still recovering coverage for the sites
that do block (DeepInfra, most Workday tenants, Lever / Ashby SPAs).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Minimum bytes a "real" page should produce. Tiny responses are almost
# always SPA shells or error pages.
_MIN_REAL_HTML_BYTES = 800

# If the response has fewer anchor tags than this AND it parses as
# HTML, treat it as a JS shell (likely client-rendered).
_MIN_REAL_ANCHOR_COUNT = 3


@dataclass
class FetchResult:
    html: str
    status_code: int
    final_url: str
    used_fallback: bool


class FetchFallback:
    """Sync Playwright session opened lazily on first fallback need.

    Use as a context manager:

        with FetchFallback() as fb:
            html, _, _ = fb.fetch_via_playwright("https://...")

    Calling ``close()`` (or exiting the ``with`` block) tears down the
    Playwright process. The class is intentionally cheap to instantiate
    when no fallback is ever triggered.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        nav_timeout_ms: int = 25000,
        networkidle_timeout_ms: int = 8000,
    ) -> None:
        self._headless = headless
        self._nav_timeout_ms = nav_timeout_ms
        # JS-heavy boards (Lever, Ashby) hydrate listings AFTER DOMContentLoaded.
        # Wait for networkidle so the anchors actually exist when we read content.
        self._networkidle_timeout_ms = networkidle_timeout_ms
        self._pw: Any = None  # sync_playwright().__enter__() return — kept Any to dodge stub gaps
        self._browser: Any = None
        self._context: Any = None

    def __enter__(self) -> FetchFallback:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _ensure_started(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        self._pw = pw
        self._browser = pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=_USER_AGENT,
        )
        log.info("[fetch-fallback] playwright session opened")

    def fetch_via_playwright(self, url: str) -> FetchResult:
        """Open ``url`` in a fresh Playwright page. Errors are surfaced as
        ``status_code=0`` results with an empty body so the caller can decide
        whether to give up.

        Waits for ``networkidle`` (best-effort, swallowed on timeout) so
        client-rendered listings have a chance to hydrate before we read
        the DOM.
        """
        import contextlib

        self._ensure_started()
        ctx = self._context
        assert ctx is not None  # mypy
        page = ctx.new_page()
        try:
            try:
                response = page.goto(
                    url, wait_until="domcontentloaded", timeout=self._nav_timeout_ms
                )
            except Exception as e:
                log.info("[fetch-fallback] goto failed for %s: %s", url, e)
                return FetchResult(html="", status_code=0, final_url=url, used_fallback=True)
            with contextlib.suppress(Exception):
                page.wait_for_load_state(
                    "networkidle", timeout=self._networkidle_timeout_ms
                )
            try:
                html = page.content()
                final_url = page.url
            except Exception as e:
                log.info("[fetch-fallback] read failed for %s: %s", url, e)
                return FetchResult(html="", status_code=0, final_url=url, used_fallback=True)
            return FetchResult(
                html=html,
                status_code=response.status if response is not None else 0,
                final_url=final_url,
                used_fallback=True,
            )
        finally:
            try:
                page.close()
            except Exception as e:
                log.debug("[fetch-fallback] page close noise: %s", e)

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception as e:
            log.debug("[fetch-fallback] context close noise: %s", e)
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception as e:
            log.debug("[fetch-fallback] browser close noise: %s", e)
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception as e:
            log.debug("[fetch-fallback] playwright stop noise: %s", e)
        self._pw = None
        self._browser = None
        self._context = None


def looks_blocked_or_js(*, status_code: int, body: str) -> bool:
    """Heuristic: should we try the Playwright fallback for this response?"""
    if status_code in (403, 429, 503):
        return True
    if status_code >= 400:
        return False  # genuine 404s aren't worth retrying with JS
    if not body or len(body) < _MIN_REAL_HTML_BYTES:
        return True
    # Quick anchor-count check: SPAs usually render <1 anchor before JS hydration.
    try:
        soup = BeautifulSoup(body, "lxml")
        if len(soup.find_all("a")) < _MIN_REAL_ANCHOR_COUNT:
            return True
    except Exception as e:
        log.debug("[fetch-fallback] bs4 parse failed: %s", e)
    return False


def fetch_with_fallback(
    url: str,
    *,
    session: requests.Session,
    fallback: FetchFallback | None,
    request_timeout: float = 10.0,
    user_agent: str | None = None,
) -> FetchResult:
    """Try ``requests.get`` first; escalate to Playwright if the response
    looks blocked or JS-rendered AND a fallback is provided.

    Returns a uniform ``FetchResult`` so callers don't have to branch on
    which path produced the HTML. On a hard failure (network error +
    no fallback), returns ``FetchResult(html="", status_code=0, ...)``.
    """
    headers = {"User-Agent": user_agent or _USER_AGENT, "Accept": "text/html"}
    try:
        resp = session.get(url, timeout=request_timeout, headers=headers, allow_redirects=True)
        status = resp.status_code
        body = resp.text or ""
        final = resp.url
    except requests.RequestException as e:
        log.info("[fetch-fallback] requests failed for %s: %s", url, e)
        if fallback is not None:
            return fallback.fetch_via_playwright(url)
        return FetchResult(html="", status_code=0, final_url=url, used_fallback=False)

    if fallback is not None and looks_blocked_or_js(status_code=status, body=body):
        log.info(
            "[fetch-fallback] escalating %s (status=%s len=%d)", url, status, len(body)
        )
        return fallback.fetch_via_playwright(url)

    return FetchResult(html=body, status_code=status, final_url=final, used_fallback=False)
