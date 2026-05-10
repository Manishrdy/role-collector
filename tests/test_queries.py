from __future__ import annotations

from job_agent.config import AppConfig, ATSGoogleSearchSection, SearchSection, SourcesSection
from job_agent.sources.queries import (
    PlannedQuery,
    domain_to_ats_type,
    generate_ats_queries,
    generate_broad_queries,
)


def _make_cfg(
    *,
    roles: list[str],
    locations: list[str],
    domains: list[str],
    time_windows: list[str],
    enabled: bool = True,
    max_queries: int = 1000,
) -> AppConfig:
    return AppConfig(
        search=SearchSection(
            roles=roles,
            locations=locations,
            time_windows=time_windows,
            max_queries_per_run=max_queries,
        ),
        sources=SourcesSection(
            ats_google_search=ATSGoogleSearchSection(enabled=enabled, domains=domains),
        ),
    )


def test_domain_to_ats_type_known_providers() -> None:
    assert domain_to_ats_type("jobs.ashbyhq.com") == "ashby"
    assert domain_to_ats_type("jobs.lever.co") == "lever"
    assert domain_to_ats_type("boards.greenhouse.io") == "greenhouse"
    assert domain_to_ats_type("job-boards.greenhouse.io") == "greenhouse"
    assert domain_to_ats_type("myworkdayjobs.com") == "workday"
    assert domain_to_ats_type("wd1.myworkdaysite.com") == "workday"
    assert domain_to_ats_type("jobs.smartrecruiters.com") == "smartrecruiters"
    assert domain_to_ats_type("example.com") == "unknown"


def test_disabled_source_yields_no_queries() -> None:
    cfg = _make_cfg(
        roles=["software engineer"],
        locations=["remote"],
        domains=["jobs.ashbyhq.com"],
        time_windows=["past_24h"],
        enabled=False,
    )
    assert generate_ats_queries(cfg) == []


def test_basic_query_shape_includes_site_and_role() -> None:
    cfg = _make_cfg(
        roles=["software engineer"],
        locations=[],
        domains=["jobs.ashbyhq.com"],
        time_windows=["past_24h"],
    )
    queries = generate_ats_queries(cfg)
    assert len(queries) == 1
    q = queries[0]
    assert q.query == 'site:jobs.ashbyhq.com "software engineer"'
    assert q.time_window == "past_24h"
    assert q.source_type == "ats_google_search"
    assert q.target_domain == "jobs.ashbyhq.com"
    assert q.ats_type == "ashby"
    assert q.role == "software engineer"
    assert q.location is None


def test_locations_emit_extra_variants_with_locationless_first() -> None:
    cfg = _make_cfg(
        roles=["backend engineer"],
        locations=["remote", "San Francisco"],
        domains=["jobs.lever.co"],
        time_windows=["past_24h"],
    )
    queries = generate_ats_queries(cfg)
    # locationless + 2 locations = 3 queries
    assert len(queries) == 3
    assert queries[0].location is None
    assert queries[0].query == 'site:jobs.lever.co "backend engineer"'
    assert queries[1].location == "remote"
    assert queries[1].query == 'site:jobs.lever.co "backend engineer" "remote"'
    assert queries[2].location == "San Francisco"
    assert queries[2].query == 'site:jobs.lever.co "backend engineer" "San Francisco"'


def test_workday_domain_adds_apply_qualifier() -> None:
    cfg = _make_cfg(
        roles=["software engineer"],
        locations=[],
        domains=["myworkdayjobs.com"],
        time_windows=["past_24h"],
    )
    queries = generate_ats_queries(cfg)
    assert len(queries) == 1
    assert queries[0].query == 'site:myworkdayjobs.com "software engineer" "apply"'
    assert queries[0].ats_type == "workday"


def test_max_queries_caps_output() -> None:
    cfg = _make_cfg(
        roles=["software engineer", "backend engineer", "frontend engineer"],
        locations=["remote", "United States"],
        domains=["jobs.ashbyhq.com", "jobs.lever.co"],
        time_windows=["past_24h", "past_48h"],
    )
    queries = generate_ats_queries(cfg, max_queries=5)
    assert len(queries) == 5


def test_iteration_order_time_window_then_role_then_domain() -> None:
    cfg = _make_cfg(
        roles=["a", "b"],
        locations=[],
        domains=["jobs.ashbyhq.com", "jobs.lever.co"],
        time_windows=["past_24h", "past_48h"],
    )
    queries = generate_ats_queries(cfg)
    # Outer loop is time_window, then role, then domain.
    assert [q.time_window for q in queries[:4]] == ["past_24h"] * 4
    assert [q.time_window for q in queries[4:]] == ["past_48h"] * 4
    assert [q.role for q in queries[:2]] == ["a", "a"]
    assert [q.role for q in queries[2:4]] == ["b", "b"]


def test_small_max_queries_exercises_every_domain_once() -> None:
    """Reordered cartesian: a budget equal to the domain count must hit each ATS."""
    domains = [
        "jobs.ashbyhq.com",
        "jobs.lever.co",
        "boards.greenhouse.io",
        "jobs.smartrecruiters.com",
    ]
    cfg = _make_cfg(
        roles=["software engineer"],
        locations=["remote", "United States"],
        domains=domains,
        time_windows=["past_24h"],
    )
    queries = generate_ats_queries(cfg, max_queries=len(domains))
    targeted = [q.target_domain for q in queries]
    assert set(targeted) == set(domains)


def test_broad_queries_substitute_role_and_location() -> None:
    cfg = _make_cfg(
        roles=["software engineer"],
        locations=["remote", "San Francisco"],
        domains=["jobs.ashbyhq.com"],
        time_windows=["past_24h"],
    )
    plans = generate_broad_queries(cfg)
    queries = [p.query for p in plans]
    # talent.* template needs a location → 2 variants
    assert '"software engineer" site:talent.* "remote"' in queries
    assert '"software engineer" site:talent.* "San Francisco"' in queries
    # jobs.* and careers.* templates are location-less → 1 each
    assert '"software engineer" site:jobs.* remote' in queries
    assert any("careers.*" in q for q in queries)
    # All broad plans carry the broad source_type and unknown ats_type
    for p in plans:
        assert p.source_type == "ats_google_search_broad"
        assert p.ats_type == "unknown"
        assert p.target_domain == ""


def test_broad_queries_disabled_when_source_disabled() -> None:
    cfg = _make_cfg(
        roles=["software engineer"],
        locations=["remote"],
        domains=["jobs.ashbyhq.com"],
        time_windows=["past_24h"],
        enabled=False,
    )
    assert generate_broad_queries(cfg) == []


def test_planned_query_is_hashable_and_frozen() -> None:
    q = PlannedQuery(
        query="x",
        time_window="past_24h",
        source_type="ats_google_search",
        target_domain="jobs.ashbyhq.com",
        ats_type="ashby",
        role="r",
    )
    assert hash(q)
