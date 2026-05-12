from __future__ import annotations

from datetime import datetime

from job_agent.config import AppConfig
from job_agent.sources.funding.queries import generate_funding_queries


def _make_cfg(funding_enabled: bool, time_windows: list[str] | None = None) -> AppConfig:
    return AppConfig.model_validate(
        {
            "search": {
                "time_windows": time_windows or ["past_week"],
                "roles": ["software engineer"],
                "locations": [],
                "max_queries_per_run": 10,
                "max_results_per_query": 10,
            },
            "sources": {
                "ats_google_search": {"enabled": True, "domains": []},
                "funding_discovery": {"enabled": funding_enabled},
                "linkedin_public_search": {"enabled": False, "login_allowed": False},
            },
        }
    )


def test_returns_empty_when_funding_disabled() -> None:
    assert generate_funding_queries(_make_cfg(funding_enabled=False)) == []


def test_generates_one_query_per_template_per_window() -> None:
    plans = generate_funding_queries(
        _make_cfg(funding_enabled=True, time_windows=["past_24h", "past_week"]),
        max_queries=20,
    )
    assert len(plans) == 10  # 5 templates x 2 windows


def test_respects_max_queries_cap() -> None:
    plans = generate_funding_queries(_make_cfg(funding_enabled=True), max_queries=3)
    assert len(plans) == 3


def test_query_text_includes_current_month() -> None:
    fixed = datetime(2026, 5, 11, tzinfo=None)
    plans = generate_funding_queries(_make_cfg(funding_enabled=True), now=fixed)
    # At least one template substitutes {month}; verify it landed.
    assert any("May 2026" in p.query for p in plans)


def test_plan_metadata_is_consistent() -> None:
    plans = generate_funding_queries(_make_cfg(funding_enabled=True))
    for p in plans:
        assert p.source_type == "funding_google_search"
        assert p.target_domain == ""
        assert p.ats_type == "unknown"
        # Tags identify the template + window.
        assert any(t.startswith("window:") for t in p.tags)
