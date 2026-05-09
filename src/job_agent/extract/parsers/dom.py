"""Generic DOM fallback for pages without an ATS hook or JSON-LD.

This is intentionally heuristic — we lean on Open Graph metadata and
``<h1>`` since virtually every job page exposes those. Confidence is
deliberately low so the orchestrator prefers an LLM pass when configured.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from job_agent.extract.parsers._common import clean_text, detect_remote_type
from job_agent.extract.schema import ExtractedJob


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    if isinstance(tag, Tag):
        content = tag.get("content")
        if isinstance(content, str):
            return clean_text(content)
    return None


def _h1(soup: BeautifulSoup) -> str | None:
    tag = soup.find("h1")
    if isinstance(tag, Tag):
        return clean_text(tag.get_text(" "))
    return None


def parse(html: str, url: str) -> ExtractedJob | None:
    del url
    soup = BeautifulSoup(html, "lxml")

    title = _meta(soup, "og:title") or _h1(soup) or clean_text(
        soup.title.string if soup.title and soup.title.string else None
    )
    if not title:
        return None

    site_name = _meta(soup, "og:site_name")
    description = _meta(soup, "og:description") or _meta(soup, "description")

    if not site_name:
        return None

    return ExtractedJob(
        title=title,
        company_name=site_name,
        location=None,
        remote_type=detect_remote_type(title, description),
        description=description,
        extraction_source="dom",
        extraction_confidence=0.4,
    )
