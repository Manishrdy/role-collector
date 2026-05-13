from job_agent.sources.ats_api.normalize import (
    freshness_bucket,
    normalize_employment_type,
    normalize_remote_type,
)


def test_normalize_employment_type() -> None:
    assert normalize_employment_type("Full-time") == "FULL_TIME"
    assert normalize_employment_type("contractor") == "CONTRACT"
    assert normalize_employment_type(None) is None


def test_normalize_remote_type() -> None:
    assert normalize_remote_type("remote") == "remote"
    assert normalize_remote_type("hybrid") == "hybrid"
    assert normalize_remote_type("on-site") == "onsite"


def test_freshness_bucket() -> None:
    observed = "2026-05-12T12:00:00+00:00"
    assert freshness_bucket("2026-05-12T01:00:00+00:00", observed_at_iso=observed) == "lt_24h"
    assert freshness_bucket("2026-05-10T12:00:00+00:00", observed_at_iso=observed) == "24_72h"
    assert freshness_bucket("2026-05-01T12:00:00+00:00", observed_at_iso=observed) == "gt_72h"
