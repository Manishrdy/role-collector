"""Dedicated Chromium profile manager for the job agent.

Used for non-search browsing (e.g. fetching ATS job-detail pages in
Phase 3). The Phase-2 search path is driven by ``nodriver`` instead —
see ``sources/ats_search.py``.

The browser is launched with a persistent context isolated from the
user's personal Chrome (no Gmail, no saved passwords, no bank cookies).
See design_plan.md §5.2.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from job_agent.config import AppConfig, load_config

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext as AsyncBrowserContext
    from playwright.sync_api import BrowserContext as SyncBrowserContext

log = logging.getLogger(__name__)


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


def _launch_args(cfg: AppConfig) -> dict[str, Any]:
    return {
        "headless": cfg.browser.headless,
        "accept_downloads": False,
        "viewport": {"width": 1280, "height": 900},
        "user_agent": _USER_AGENT,
    }


def profile_path(cfg: AppConfig | None = None) -> Path:
    cfg = cfg or load_config()
    path = Path(cfg.browser.dedicated_profile_path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@asynccontextmanager
async def launch_context(cfg: AppConfig | None = None) -> AsyncIterator[AsyncBrowserContext]:
    """Async persistent Chromium context. Downloads are blocked unconditionally."""
    cfg = cfg or load_config()
    from playwright.async_api import async_playwright

    user_data_dir = profile_path(cfg)
    log.info("launching browser profile at %s (headless=%s)", user_data_dir, cfg.browser.headless)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(user_data_dir), **_launch_args(cfg)
        )
        try:
            yield context
        finally:
            with contextlib.suppress(Exception):
                await context.close()


@contextmanager
def launch_sync_context(cfg: AppConfig | None = None) -> Iterator[SyncBrowserContext]:
    """Sync persistent Chromium context.

    Cannot run inside a thread that already has a running asyncio loop
    (Playwright sync API restriction). Our CLI is plain sync, so this is fine.
    """
    cfg = cfg or load_config()
    from playwright.sync_api import sync_playwright

    user_data_dir = profile_path(cfg)
    log.info(
        "launching sync browser profile at %s (headless=%s)",
        user_data_dir,
        cfg.browser.headless,
    )

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(user_data_dir), **_launch_args(cfg))
        try:
            yield context
        finally:
            with contextlib.suppress(Exception):
                context.close()
