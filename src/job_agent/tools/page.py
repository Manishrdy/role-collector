"""Page-fetch tools — Phase-1 stubs.

Real implementations land in Phase 3 (extraction). The decorators are wired
now so guardrails are enforced from day one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from job_agent.tools.permissions import guarded_url_arg

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchedPage:
    url: str
    canonical_url: str
    visible_text: str
    http_status: int
    blocked_reason: str | None = None


@guarded_url_arg("url")
async def open_allowed_url(*, url: str) -> FetchedPage:
    """Open `url` (must already pass URL safety) and return visible text. STUB."""
    log.info("[stub] open_allowed_url(url=%r)", url)
    return FetchedPage(url=url, canonical_url=url, visible_text="", http_status=0)


@guarded_url_arg("url")
async def extract_visible_text(*, url: str) -> str:
    """Return visible text on `url`. STUB."""
    log.info("[stub] extract_visible_text(url=%r)", url)
    return ""


@guarded_url_arg("url")
async def extract_links(*, url: str) -> list[str]:
    """Return outgoing links on `url`. STUB."""
    log.info("[stub] extract_links(url=%r)", url)
    return []
