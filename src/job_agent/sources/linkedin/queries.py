"""Public LinkedIn post Google-query generator.

Returns `PlannedQuery` records so the existing nodriver Google driver
can drive them with the same orchestration as ATS / funding search.

Queries are intentionally narrow — generic LinkedIn searches return
huge amounts of noise. We pair `site:linkedin.com/posts/*` with a
hiring-intent phrase from a small whitelist so the result set is
high-signal.
"""

from __future__ import annotations

from job_agent.config import AppConfig
from job_agent.sources.queries import PlannedQuery, TimeWindow

# Hiring-intent phrases and hashtags. Exact-phrase strings are wrapped
# in quotes so Google does exact-phrase matching, not stemmed expansion.
# Hashtags don't need quoting; Google indexes them as discrete tokens
# on LinkedIn post pages.
_HIRING_PHRASES: tuple[tuple[str, str], ...] = (
    ('"we\'re hiring"', "were_hiring"),
    ('"we are hiring"', "we_are_hiring"),
    ('"open roles"', "open_roles"),
    ('"join our team"', "join_our_team"),
    ('"looking for" engineer', "looking_for_engineer"),
    ("#hiring", "hiring_hashtag"),
    ("#nowhiring", "nowhiring_hashtag"),
    ("#wearehiring", "wearehiring_hashtag"),
    ("#hiringnow", "hiringnow_hashtag"),
)


def generate_linkedin_queries(
    cfg: AppConfig,
    *,
    max_queries: int | None = None,
) -> list[PlannedQuery]:
    """Generate Google queries targeting public LinkedIn posts.

    Empty when ``cfg.sources.linkedin_public_search.enabled`` is False so
    the LangGraph node can call this unconditionally.
    """
    if not cfg.sources.linkedin_public_search.enabled:
        return []
    cap = max_queries if max_queries is not None else cfg.search.max_queries_per_run
    time_windows: list[TimeWindow] = [tw for tw in cfg.search.time_windows]  # type: ignore[misc]

    out: list[PlannedQuery] = []
    for tw in time_windows:
        for phrase, label in _HIRING_PHRASES:
            if len(out) >= cap:
                return out
            query = f"site:linkedin.com/posts/ {phrase}"
            out.append(
                PlannedQuery(
                    query=query,
                    time_window=tw,
                    source_type="linkedin_public_search",
                    target_domain="linkedin.com",
                    ats_type="unknown",
                    role="",
                    location=None,
                    tags=(label, f"window:{tw}"),
                )
            )
    return out
