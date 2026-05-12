"""Funding-event Pydantic schema.

Mirrors the `funding_events` SQLite table plus the provenance fields the
orchestrator needs (which aggregator produced it, how confident the
extractor was, and the raw snippet for forensic re-extraction).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExtractionSource = Literal["regex", "structured_api", "llm"]


class FundingEventCandidate(BaseModel):
    """A funding event after extraction, before idempotent persistence.

    Required: ``company_name`` and ``source_url`` — anything missing both is
    treated as a failed extraction by the orchestrator and is dropped.
    """

    company_name: str
    round: str | None = None  # "Series A", "Seed", "Series B", "pre-seed", ...
    amount: str | None = None  # "$5M", "$25 million" — kept as text per schema
    announced_date: str | None = None  # ISO-8601 date if known
    investors: str | None = None  # comma-separated free-text list
    source_url: str
    raw_snippet: str | None = None

    aggregator: str  # "techcrunch" / "hackernews" / "google"
    extraction_source: ExtractionSource
    extraction_confidence: float = Field(ge=0.0, le=1.0)
