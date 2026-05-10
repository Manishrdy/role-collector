"""ATS Google/Bing query plan generation.

Builds search-engine queries from config: cartesian over
(role x ATS domain x time_window), optionally narrowed with a location term.

The output is a deterministic list of `PlannedQuery` records -- no I/O, no
randomness, easy to unit-test. Phase 2 source orchestration consumes these.

See design_plan.md section 10.6 for the underlying templates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from job_agent.config import AppConfig

TimeWindow = Literal["past_24h", "past_48h", "past_week", "any"]


# ATS-specific extra terms layered on top of the base `site:domain "role"` query.
# Empty string = no extra qualifier. We intentionally keep this small for v1
# to limit the query budget; design_plan.md §10.6 has many variants.
_ATS_QUALIFIERS: dict[str, list[str]] = {
    "jobs.ashbyhq.com": [""],
    "jobs.lever.co": [""],
    "job-boards.greenhouse.io": [""],
    "boards.greenhouse.io": [""],
    "myworkdayjobs.com": ['"apply"'],
    "wd1.myworkdaysite.com": ['"apply"'],
    "wd5.myworkdaysite.com": ['"apply"'],
    "jobs.smartrecruiters.com": [""],
}


def domain_to_ats_type(domain: str) -> str:
    """Map an ATS domain to the short ATS provider name we store in jobs.ats_type."""
    if "ashbyhq.com" in domain:
        return "ashby"
    if "lever.co" in domain:
        return "lever"
    if "greenhouse.io" in domain:
        return "greenhouse"
    if "myworkdayjobs.com" in domain or "myworkdaysite.com" in domain:
        return "workday"
    if "smartrecruiters.com" in domain:
        return "smartrecruiters"
    return "unknown"


@dataclass(frozen=True)
class PlannedQuery:
    query: str
    time_window: TimeWindow
    source_type: str
    target_domain: str
    ats_type: str
    role: str
    location: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def _build_query(domain: str, role: str, location: str | None, qualifier: str) -> str:
    parts = [f"site:{domain}", f'"{role}"']
    if qualifier:
        parts.append(qualifier)
    if location:
        parts.append(f'"{location}"')
    return " ".join(parts)


def generate_ats_queries(
    cfg: AppConfig,
    *,
    max_queries: int | None = None,
) -> list[PlannedQuery]:
    """Generate Google ATS site queries from cfg.search x cfg.sources.ats_google_search.

    Order: time_window -> role -> domain -> location. Locations are optional;
    we always emit a location-less variant first to maximise coverage, then
    one variant per configured location.

    `max_queries` overrides cfg.search.max_queries_per_run when provided.
    """
    if not cfg.sources.ats_google_search.enabled:
        return []

    domains = cfg.sources.ats_google_search.domains
    roles = cfg.search.roles
    locations: list[str | None] = [None, *cfg.search.locations]
    time_windows: list[TimeWindow] = [tw for tw in cfg.search.time_windows]  # type: ignore[misc]

    cap = max_queries if max_queries is not None else cfg.search.max_queries_per_run
    out: list[PlannedQuery] = []

    # Iteration order is tw -> role -> location -> domain -> qualifier.
    # Keeping `location` outside `domain` is intentional: a small `max_queries`
    # budget then exercises every ATS host once before cycling through
    # locations on the first host. Phase-3 smokes need this to surface
    # Greenhouse / Lever / SmartRecruiters URLs alongside Ashby.
    for tw in time_windows:
        for role in roles:
            for location in locations:
                for domain in domains:
                    qualifiers = _ATS_QUALIFIERS.get(domain, [""])
                    for qualifier in qualifiers:
                        if len(out) >= cap:
                            return out
                        q = _build_query(domain, role, location, qualifier)
                        out.append(
                            PlannedQuery(
                                query=q,
                                time_window=tw,
                                source_type="ats_google_search",
                                target_domain=domain,
                                ats_type=domain_to_ats_type(domain),
                                role=role,
                                location=location,
                                tags=(f"ats:{domain_to_ats_type(domain)}", f"window:{tw}"),
                            )
                        )

    return out


# Broad-coverage Google query templates. These don't target a specific ATS
# host -- they use Google's wildcard subdomain matching to surface postings
# from ATS deployments we haven't named (incl. white-label Cornerstone /
# SAP careers pages). They also force the JSON-LD / DOM / LLM fallback
# chain to do real work in production.
_BROAD_QUERY_TEMPLATES: tuple[tuple[str, str, bool], ...] = (
    # (template, label, needs_location)
    ('"{role}" site:talent.* {loc}', "broad_talent", True),
    ('"{role}" site:jobs.* remote', "broad_jobs", False),
    (
        '"{role}" (site:careers.* OR site:*/careers/* OR site:*/career/*) remote',
        "broad_careers",
        False,
    ),
)


def generate_broad_queries(
    cfg: AppConfig,
    *,
    max_queries: int | None = None,
) -> list[PlannedQuery]:
    """Generate broad-coverage Google queries (no fixed target_domain).

    Templates surface URLs from any ATS Google indexes — including the long
    tail of Cornerstone / SAP / Personio / Recruitee deployments we don't
    have parsers for. They land in the candidate pool with ats_type set to
    ``unknown`` and ``target_domain=""`` (the search engine treats empty
    target_domain as "no host filter").
    """
    if not cfg.sources.ats_google_search.enabled:
        return []
    cap = max_queries if max_queries is not None else cfg.search.max_queries_per_run
    out: list[PlannedQuery] = []
    time_windows: list[TimeWindow] = [tw for tw in cfg.search.time_windows]  # type: ignore[misc]
    locations = cfg.search.locations or [""]

    for tw in time_windows:
        for role in cfg.search.roles:
            for tmpl, label, needs_loc in _BROAD_QUERY_TEMPLATES:
                loc_iter: list[str] = locations if needs_loc else [""]
                for loc in loc_iter:
                    if len(out) >= cap:
                        return out
                    formatted_loc = f'"{loc}"' if loc else ""
                    query = tmpl.format(role=role, loc=formatted_loc).strip()
                    # Collapse double spaces from optional ``{loc}`` substitution.
                    query = " ".join(query.split())
                    out.append(
                        PlannedQuery(
                            query=query,
                            time_window=tw,
                            source_type="ats_google_search_broad",
                            target_domain="",
                            ats_type="unknown",
                            role=role,
                            location=loc or None,
                            tags=(label, f"window:{tw}"),
                        )
                    )
    return out
