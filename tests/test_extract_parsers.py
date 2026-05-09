from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.extract.parsers import dom, greenhouse, jsonld, lever
from job_agent.extract.parsers._common import detect_ats_type, detect_remote_type

FIXTURES = Path(__file__).parent / "fixtures" / "extract"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://jobs.ashbyhq.com/acme/abc-123", "ashby"),
        ("https://jobs.lever.co/cobalt/uuid", "lever"),
        ("https://boards.greenhouse.io/foo/jobs/1", "greenhouse"),
        ("https://job-boards.greenhouse.io/foo/jobs/1", "greenhouse"),
        ("https://jobs.smartrecruiters.com/foo/100", "smartrecruiters"),
        ("https://example.com/jobs/1", None),
    ],
)
def test_detect_ats_type(url: str, expected: str | None) -> None:
    assert detect_ats_type(url) == expected


def test_detect_remote_type_handles_variants() -> None:
    assert detect_remote_type("Remote, US") == "remote"
    assert detect_remote_type(None, "Hybrid in NYC") == "hybrid"
    assert detect_remote_type("On-site - Berlin") == "onsite"
    assert detect_remote_type("San Francisco") is None


def test_jsonld_parser_extracts_ashby_with_url_augmentation() -> None:
    """Real Ashby pages ship JSON-LD only — augment ats_type/ats_job_id from URL/identifier."""
    url = "https://jobs.ashbyhq.com/sleeper/af131373-94e9-4fce-8da5-70d6855f5294"
    job = jsonld.parse(_load("ashby_jsonld.html"), url)
    assert job is not None
    assert job.title == "Software Engineer, Backend"
    assert job.company_name == "Sleeper"
    assert job.remote_type == "remote"  # jobLocationType=TELECOMMUTE
    assert job.ats_type == "ashby"
    assert job.ats_job_id == "af131373-94e9-4fce-8da5-70d6855f5294"
    assert job.posted_date == "2026-05-08"


def test_greenhouse_parser_extracts_fields() -> None:
    url = "https://boards.greenhouse.io/beaconai/jobs/9876543"
    job = greenhouse.parse(_load("greenhouse_detail.html"), url)
    assert job is not None
    assert job.title == "Staff Software Engineer"
    assert job.company_name == "Beacon AI"
    assert job.location == "San Francisco, CA"
    assert job.ats_type == "greenhouse"
    assert job.ats_job_id == "9876543"
    assert job.apply_url is not None and "/apply" in job.apply_url


def test_lever_parser_extracts_fields() -> None:
    url = "https://jobs.lever.co/cobalt/abcdef01-2345-6789-abcd-ef0123456789"
    job = lever.parse(_load("lever_detail.html"), url)
    assert job is not None
    assert job.title == "Founding Engineer"
    assert job.company_name == "Cobalt"
    assert job.location == "Remote"
    assert job.employment_type == "Full-time"
    assert job.remote_type == "remote"
    assert job.ats_type == "lever"
    assert job.ats_job_id == "abcdef01-2345-6789-abcd-ef0123456789"


def test_jsonld_parser_handles_jobposting_block() -> None:
    job = jsonld.parse(_load("jsonld_jobposting.html"), "https://example.com/x")
    assert job is not None
    assert job.title == "Senior ML Engineer"
    assert job.company_name == "Northbeam"
    assert job.location is not None and "New York" in job.location
    assert job.remote_type == "remote"
    assert job.salary_text is not None and "200000" in job.salary_text
    assert job.posted_date == "2026-05-01"
    assert job.extraction_source == "jsonld"


def test_dom_parser_uses_og_metadata() -> None:
    job = dom.parse(_load("dom_only.html"), "https://vectorrobotics.example/careers/eng")
    assert job is not None
    assert job.title == "Robotics Software Engineer (Remote)"
    assert job.company_name == "Vector Robotics"
    assert job.remote_type == "remote"
    assert job.extraction_source == "dom"
    # Confidence floor is intentionally below pipeline acceptance threshold.
    assert job.extraction_confidence < 0.5
