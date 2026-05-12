"""LinkedIn post Pydantic schema.

Mirrors the `linkedin_posts` SQLite table plus extractor provenance.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExtractionSource = Literal["regex", "llm"]


class ExtractedLinkedInPost(BaseModel):
    """A LinkedIn post after extraction, before idempotent persistence.

    Required: ``post_url`` and ``post_text`` (anything missing both is
    dropped by the orchestrator). ``company_name`` is optional because
    not every hiring post names a company in the body — sometimes the
    author's profile is the company context.
    """

    post_url: str
    post_text: str

    author_name: str | None = None
    author_url: str | None = None
    company_name: str | None = None
    detected_role: str | None = None
    role_family: str | None = None
    role_match_status: str | None = None
    level: str | None = None
    level_confidence: float | None = None

    is_hiring_post: bool = False
    hiring_signals: list[str] = Field(default_factory=list)

    extraction_source: ExtractionSource = "regex"
    extraction_confidence: float = Field(ge=0.0, le=1.0)
