"""Search tools — Phase-1 stubs.

Phase 2 will replace these with real Playwright-driven Google + Bing flows
including time-window UI clicks, captcha detection, and result-card parsing.
The signatures defined here are the contract the LLM-facing layer will use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

TimeWindow = Literal["past_24h", "past_48h", "past_week", "any"]
SearchEngine = Literal["google", "bing"]


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    rank: int
    engine: SearchEngine
    query: str


async def search_google(query: str, time_window: TimeWindow = "past_24h") -> list[SearchResult]:
    """Run a Google search. STUB — implemented in Phase 2."""
    log.info("[stub] search_google(query=%r, time_window=%r)", query, time_window)
    return []


async def search_bing(query: str, time_window: TimeWindow = "past_24h") -> list[SearchResult]:
    """Run a Bing search. STUB — implemented in Phase 2 (Google fallback)."""
    log.info("[stub] search_bing(query=%r, time_window=%r)", query, time_window)
    return []
