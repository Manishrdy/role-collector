from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from job_agent.agent.scheduler import compute_source_schedule
from job_agent.config import load_config
from job_agent.db import repo
from job_agent.db.migrate import migrate


def test_scheduler_marks_first_run_sources_eligible(isolated_db: Path) -> None:
    del isolated_db
    migrate()
    cfg = load_config()
    decisions = compute_source_schedule(cfg)
    by_source = {d.source_name: d for d in decisions}

    assert by_source["ats_api_discovery"].status == "eligible"
    assert by_source["ats_google_search"].status == "eligible"
    expected_linkedin = "eligible" if cfg.sources.linkedin_public_search.enabled else "skipped"
    assert by_source["linkedin_public_search"].status == expected_linkedin


def test_scheduler_respects_backoff(isolated_db: Path) -> None:
    del isolated_db
    migrate()
    backoff_until = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="seconds")
    repo.update_source_stats(
        source_name="ats_google_search",
        status="failed",
        backoff_until=backoff_until,
    )
    cfg = load_config()
    decisions = compute_source_schedule(cfg)
    by_source = {d.source_name: d for d in decisions}

    assert by_source["ats_google_search"].status == "skipped"
    assert by_source["ats_google_search"].reason == "source_backoff"


def test_scheduler_prioritizes_higher_yield_sources_first(isolated_db: Path) -> None:
    del isolated_db
    migrate()
    for _ in range(5):
        repo.update_source_stats(
            source_name="ats_api_discovery",
            status="succeeded",
            candidates=20,
            jobs_saved=8,
        )
    for _ in range(5):
        repo.update_source_stats(
            source_name="funding_discovery",
            status="succeeded",
            candidates=20,
            jobs_saved=1,
        )
    cfg = load_config()
    cfg = cfg.model_copy(
        update={
            "agent_loop": cfg.agent_loop.model_copy(
                update={
                    "cadence": cfg.agent_loop.cadence.model_copy(
                        update={"ats_api_hours": 0.0, "funding_hours": 0.0}
                    )
                }
            )
        }
    )
    decisions = compute_source_schedule(cfg)
    eligible = [d for d in decisions if d.status == "eligible"]
    names = [d.source_name for d in eligible]
    assert names.index("ats_api_discovery") < names.index("funding_discovery")
