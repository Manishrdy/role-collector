"""Funding-themed Google query generator.

Builds a small batch of broad-coverage queries that surface recent funding
news. The output uses the existing `PlannedQuery` dataclass so the
nodriver Google driver can reuse the same orchestration shape as ATS
search.

Templates intentionally avoid hard-coding specific aggregator domains —
those are crawled directly by `aggregators.techcrunch` and
`aggregators.hackernews`. The Google channel exists to catch announcements
from sites we don't have a dedicated scraper for.
"""

from __future__ import annotations

from datetime import UTC, datetime

from job_agent.config import AppConfig
from job_agent.sources.queries import PlannedQuery, TimeWindow

# Five-template set covering the dominant funding-headline phrasings.
# Tagged so the orchestrator can route results by template if needed.
_TEMPLATES: tuple[tuple[str, str], ...] = (
    ('"raised" "Series A" "startup" {month}', "raised_series_a"),
    ('"raised" "seed round" "startup" {month}', "raised_seed"),
    ('"announces" "Series B" funding {month}', "announces_series_b"),
    ('"closes" "Series C" "$" million', "closes_series_c"),
    ('"raised" "$" million site:techcrunch.com OR site:news.ycombinator.com', "broad_news"),
)


def _current_month(now: datetime | None = None) -> str:
    """Return the current month name + year — used as a freshness hint in queries.

    Past two days alone is too tight for funding news (announcements often
    take a day or two to surface in indexed pages), so we narrow with a
    month/year keyword instead and rely on `time_window=past_week` for
    deeper filtering.
    """
    n = now or datetime.now(UTC)
    return n.strftime("%B %Y")  # e.g. "May 2026"


def generate_funding_queries(
    cfg: AppConfig,
    *,
    max_queries: int | None = None,
    now: datetime | None = None,
) -> list[PlannedQuery]:
    """Generate funding-themed Google queries.

    Empty when ``cfg.sources.funding_discovery.enabled`` is False so the
    LangGraph node can call this unconditionally.
    """
    if not cfg.sources.funding_discovery.enabled:
        return []
    cap = max_queries if max_queries is not None else cfg.search.max_queries_per_run
    month = _current_month(now)
    time_windows: list[TimeWindow] = [tw for tw in cfg.search.time_windows]  # type: ignore[misc]
    out: list[PlannedQuery] = []
    for tw in time_windows:
        for tmpl, label in _TEMPLATES:
            if len(out) >= cap:
                return out
            query = tmpl.format(month=month).strip()
            out.append(
                PlannedQuery(
                    query=query,
                    time_window=tw,
                    source_type="funding_google_search",
                    target_domain="",  # broad — no host filter
                    ats_type="unknown",
                    role="",  # role-agnostic; funding events surface companies, not roles
                    location=None,
                    tags=(label, f"window:{tw}"),
                )
            )
    return out
