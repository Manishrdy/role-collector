"""Google funding aggregator — wraps the existing nodriver-based ATS
search infrastructure with funding-themed queries.

The heavy lifting (nodriver browser session, block detection, result
parsing) is reused from `sources.ats_search`. This module just drives
those primitives with funding `PlannedQuery` records and runs the
extractor over title+snippet of each result.
"""

from __future__ import annotations

import logging
from pathlib import Path

from job_agent.config import AppConfig
from job_agent.sources.ats_search import run_ats_search
from job_agent.sources.funding.extractor import extract_from_text
from job_agent.sources.funding.queries import generate_funding_queries
from job_agent.sources.funding.schema import FundingEventCandidate

log = logging.getLogger(__name__)


def fetch_google_funding(
    cfg: AppConfig,
    *,
    search_run_id: int | None = None,
    max_queries: int | None = None,
    debug_dump_dir: Path | None = None,
) -> list[FundingEventCandidate]:
    """Run funding-themed Google queries via nodriver; extract per result.

    Returns an empty list if funding_discovery is disabled or no queries
    were generated. Block events are logged through the existing ATS
    search orchestrator's `agent_events` writes — we don't re-log here.
    """
    plans = generate_funding_queries(cfg, max_queries=max_queries)
    if not plans:
        return []

    candidates, stats = run_ats_search(
        cfg=cfg,
        plans=plans,
        max_results_per_query=cfg.search.max_results_per_query,
        search_run_id=search_run_id,
        debug_dump_dir=debug_dump_dir,
    )
    log.info(
        "[funding/google] %d candidate URLs from %d queries (succeeded=%d blocked=%d)",
        len(candidates),
        stats.queries_attempted,
        stats.queries_succeeded,
        stats.queries_blocked,
    )

    out: list[FundingEventCandidate] = []
    for c in candidates:
        # Title is usually the most signal-dense field; snippet supplements
        # round/amount detection when the title is too short.
        text = c.title
        if c.snippet:
            text = f"{text}. {c.snippet}"
        cand = extract_from_text(
            text=text,
            source_url=c.canonical_url,
            aggregator="google",
        )
        if cand is not None:
            out.append(cand)
    log.info("[funding/google] %d candidates after extraction", len(out))
    return out
