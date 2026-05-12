from __future__ import annotations

from job_agent.config import AppConfig
from job_agent.sources.linkedin.queries import generate_linkedin_queries


def _make_cfg(linkedin_enabled: bool, time_windows: list[str] | None = None) -> AppConfig:
    return AppConfig.model_validate(
        {
            "search": {
                "time_windows": time_windows or ["past_24h"],
                "roles": ["software engineer"],
                "locations": [],
                "max_queries_per_run": 10,
                "max_results_per_query": 5,
            },
            "sources": {
                "ats_google_search": {"enabled": True, "domains": []},
                "funding_discovery": {"enabled": False},
                "linkedin_public_search": {
                    "enabled": linkedin_enabled,
                    "login_allowed": False,
                },
            },
        }
    )


def test_returns_empty_when_linkedin_disabled() -> None:
    assert generate_linkedin_queries(_make_cfg(linkedin_enabled=False)) == []


def test_generates_one_query_per_phrase() -> None:
    plans = generate_linkedin_queries(
        _make_cfg(linkedin_enabled=True), max_queries=20
    )
    assert len(plans) == 9


def test_queries_target_linkedin_posts_path() -> None:
    plans = generate_linkedin_queries(
        _make_cfg(linkedin_enabled=True), max_queries=20
    )
    for p in plans:
        assert "site:linkedin.com/posts/" in p.query
        assert p.source_type == "linkedin_public_search"
        assert p.target_domain == "linkedin.com"


def test_queries_include_hiring_hashtags_and_we_are_hiring() -> None:
    plans = generate_linkedin_queries(
        _make_cfg(linkedin_enabled=True), max_queries=20
    )
    labels = {p.tags[0] for p in plans}
    assert "we_are_hiring" in labels
    assert "hiring_hashtag" in labels
    assert "nowhiring_hashtag" in labels
    assert any("#hiring" in p.query for p in plans)


def test_max_queries_cap() -> None:
    plans = generate_linkedin_queries(_make_cfg(linkedin_enabled=True), max_queries=2)
    assert len(plans) == 2


def test_multiple_time_windows_multiply_queries() -> None:
    plans = generate_linkedin_queries(
        _make_cfg(linkedin_enabled=True, time_windows=["past_24h", "past_week"]),
        max_queries=40,
    )
    assert len(plans) == 18  # 9 phrases x 2 windows
