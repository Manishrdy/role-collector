from __future__ import annotations

from pathlib import Path

from job_agent.extract.pipeline import extract_job
from job_agent.extract.schema import ExtractedJob

FIXTURES = Path(__file__).parent / "fixtures" / "extract"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


class _RecordingLLM:
    def __init__(self, return_value: ExtractedJob | None = None) -> None:
        self.calls = 0
        self.return_value = return_value

    def extract(self, *, html: str, url: str) -> ExtractedJob | None:
        del html, url
        self.calls += 1
        return self.return_value


def test_pipeline_resolves_ashby_via_jsonld_with_ats_augmentation() -> None:
    """Ashby pages are JSON-LD-only since 2026 — pipeline must still tag them
    with ats_type/ats_job_id so dedupe works downstream."""
    llm = _RecordingLLM()
    url = "https://jobs.ashbyhq.com/sleeper/af131373-94e9-4fce-8da5-70d6855f5294"
    job = extract_job(html=_load("ashby_jsonld.html"), url=url, llm=llm)
    assert job is not None
    assert job.extraction_source == "jsonld"
    assert job.ats_type == "ashby"
    assert job.ats_job_id == "af131373-94e9-4fce-8da5-70d6855f5294"
    assert llm.calls == 0


def test_pipeline_falls_through_to_jsonld() -> None:
    llm = _RecordingLLM()
    job = extract_job(
        html=_load("jsonld_jobposting.html"),
        url="https://example.com/jobs/1",
        llm=llm,
    )
    assert job is not None
    assert job.extraction_source == "jsonld"
    assert llm.calls == 0


def test_pipeline_invokes_llm_only_when_dom_is_low_confidence() -> None:
    """DOM parser returns a 0.4 confidence row → LLM must be consulted."""
    llm = _RecordingLLM(
        return_value=ExtractedJob(
            title="Robotics Software Engineer",
            company_name="Vector Robotics",
            location="Remote",
            extraction_source="llm",
            extraction_confidence=0.7,
        )
    )
    job = extract_job(
        html=_load("dom_only.html"),
        url="https://vectorrobotics.example/careers/eng",
        llm=llm,
    )
    assert llm.calls == 1
    assert job is not None
    assert job.extraction_source == "llm"


def test_pipeline_falls_back_to_dom_when_llm_returns_none() -> None:
    llm = _RecordingLLM(return_value=None)
    job = extract_job(
        html=_load("dom_only.html"),
        url="https://vectorrobotics.example/careers/eng",
        llm=llm,
    )
    assert llm.calls == 1
    assert job is not None
    assert job.extraction_source == "dom"


def test_pipeline_skips_llm_when_no_extractor_provided() -> None:
    job = extract_job(
        html=_load("dom_only.html"),
        url="https://vectorrobotics.example/careers/eng",
        llm=None,
    )
    assert job is not None
    assert job.extraction_source == "dom"


def test_pipeline_returns_none_for_empty_html() -> None:
    assert extract_job(html="", url="https://example.com", llm=None) is None
