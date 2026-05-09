"""Dedicated Chromium profile manager for the job agent.

The browser is launched with a persistent context isolated from the user's
personal Chrome (no Gmail, no saved passwords, no bank cookies). See
design_plan.md §5.2.

Phase-1 exposes a context-manager-style helper. Real Playwright invocations
land in Phase 2 once the search modules are wired.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from job_agent.config import AppConfig, load_config

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

log = logging.getLogger(__name__)


def profile_path(cfg: AppConfig | None = None) -> Path:
    cfg = cfg or load_config()
    path = Path(cfg.browser.dedicated_profile_path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@asynccontextmanager
async def launch_context(cfg: AppConfig | None = None) -> AsyncIterator[BrowserContext]:
    """Launch a persistent Chromium context with the dedicated profile.

    Downloads are blocked unconditionally — design_plan.md §14.8.
    """
    cfg = cfg or load_config()
    from playwright.async_api import async_playwright

    user_data_dir = profile_path(cfg)
    log.info("launching browser profile at %s (headless=%s)", user_data_dir, cfg.browser.headless)

    launch_args: dict[str, Any] = {
        "headless": cfg.browser.headless,
        "accept_downloads": False,
        "viewport": {"width": 1280, "height": 900},
        # Identify as a recent stable Chromium build; do not spoof a different OS.
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
    }

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(str(user_data_dir), **launch_args)
        try:
            yield context
        finally:
            await context.close()
