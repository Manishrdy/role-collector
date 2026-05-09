"""Pydantic schema for extracted job records.

Mirrors design_plan.md §19.3/§19.4 with the provenance fields the persistence
layer needs (which parser produced the record, how confident it was, and the
posting's ATS-side identifier when we have one).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExtractionSource = Literal["ats_parser", "jsonld", "dom", "llm"]


class ExtractedJob(BaseModel):
    """Structured job posting after extraction.

    Required: ``title`` and ``company_name`` — anything missing both is
    treated as a failed extraction by the pipeline and is not saved.
    """

    title: str
    company_name: str
    location: str | None = None
    remote_type: str | None = None  # remote / hybrid / onsite
    salary_text: str | None = None

    apply_url: str | None = None
    posted_date: str | None = None  # ISO-8601 date if known
    posted_date_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    skills: list[str] = Field(default_factory=list)
    seniority: str | None = None
    employment_type: str | None = None  # full_time / contract / internship
    description: str | None = None
    description_summary: str | None = None

    ats_type: str | None = None
    ats_job_id: str | None = None

    extraction_source: ExtractionSource
    extraction_confidence: float = Field(ge=0.0, le=1.0)
