from __future__ import annotations

import pytest

from job_agent.browser.page_fetch import fetch_pages_async
from job_agent.config import load_config


@pytest.mark.asyncio
async def test_safety_blocked_urls_short_circuit() -> None:
    """All URLs are off-allowlist → fetcher must short-circuit before launching Playwright."""
    cfg = load_config()
    urls = [
        "https://evil.example.com/foo",
        "https://random-blog.test/post",
    ]
    pages = await fetch_pages_async(urls, cfg=cfg)
    assert len(pages) == len(urls)
    for page, url in zip(pages, urls, strict=True):
        assert page.url == url
        assert page.status == "blocked"
        assert page.blocked_reason is not None
        assert page.html == ""


@pytest.mark.asyncio
async def test_empty_input_returns_empty() -> None:
    cfg = load_config()
    assert await fetch_pages_async([], cfg=cfg) == []
