"""Lever (`jobs.lever.co/<company>/<uuid>`) detail-page parser.

Lever renders structured DOM with predictable hooks:

* ``.posting-headline h2`` — title
* ``.posting-categories .location`` — location
* ``.posting-categories .commitment`` — employment type
* ``.section-wrapper.page-full-width div.section`` — description blocks
* The company name comes from the URL slug; Lever doesn't surface it
  directly in the page header.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from job_agent.extract.parsers._common import (
    clean_text,
    detect_ats_type,
    detect_remote_type,
)
from job_agent.extract.schema import ExtractedJob

_LEVER_PATH_RX = re.compile(
    r"^/(?P<company>[^/]+)/(?P<job_id>[0-9a-fA-F-]{20,})/?$"
)


def _company_and_id(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    m = _LEVER_PATH_RX.match(parsed.path)
    if not m:
        return None, None
    company_slug = m.group("company")
    return (
        company_slug.replace("-", " ").title() if company_slug else None,
        m.group("job_id"),
    )


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for sel in selectors:
        node = soup.select_one(sel)
        if isinstance(node, Tag):
            text = clean_text(node.get_text(" "))
            if text:
                return text
    return None


def parse(html: str, url: str) -> ExtractedJob | None:
    if detect_ats_type(url) != "lever":
        return None

    soup = BeautifulSoup(html, "lxml")

    title = _first_text(soup, [".posting-headline h2", "h2"])
    if not title:
        return None

    company, ats_job_id = _company_and_id(url)
    if not company:
        return None

    location = _first_text(
        soup, [".posting-categories .location", ".sort-by-time .posting-category.location"]
    )
    employment_type = _first_text(
        soup, [".posting-categories .commitment", ".posting-category.commitment"]
    )

    description_block = soup.select_one(".section-wrapper.page-full-width") or soup.select_one(
        ".content-wrapper.posting-page"
    )
    description = (
        clean_text(description_block.get_text(" "))
        if isinstance(description_block, Tag)
        else None
    )

    apply_link = soup.select_one("a.postings-btn[data-qa='btn-apply']") or soup.select_one(
        "a.template-btn-submit"
    )
    apply_url = (
        apply_link.get("href")
        if isinstance(apply_link, Tag) and isinstance(apply_link.get("href"), str)
        else None
    )

    return ExtractedJob(
        title=title,
        company_name=company,
        location=location,
        remote_type=detect_remote_type(location, employment_type, description),
        apply_url=apply_url if isinstance(apply_url, str) else None,
        employment_type=employment_type,
        description=description,
        ats_type="lever",
        ats_job_id=ats_job_id,
        extraction_source="ats_parser",
        extraction_confidence=0.9,
    )
