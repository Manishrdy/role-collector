"""Greenhouse detail-page parser.

Greenhouse exposes job listings under two subdomains:

* ``boards.greenhouse.io/<company>/jobs/<id>``   — classic
* ``job-boards.greenhouse.io/<company>/jobs/<id>`` — refreshed UI

Both render server-side HTML. We use BeautifulSoup over lxml since
the structure is stable: ``#header h1`` for title, ``.company-name``
and ``.location`` divs, and a ``#content`` body for the description.
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

_JOB_ID_RX = re.compile(r"/jobs/(\d+)")


def _job_id_from_url(url: str) -> str | None:
    m = _JOB_ID_RX.search(urlparse(url).path)
    return m.group(1) if m else None


def _company_from_url(url: str) -> str | None:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return None
    slug = parts[0]
    return slug.replace("-", " ").title() if slug else None


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for sel in selectors:
        node = soup.select_one(sel)
        if isinstance(node, Tag):
            text = clean_text(node.get_text(" "))
            if text:
                return text
    return None


def parse(html: str, url: str) -> ExtractedJob | None:
    if detect_ats_type(url) != "greenhouse":
        return None

    soup = BeautifulSoup(html, "lxml")

    title = _first_text(
        soup, ["#header h1", "h1.app-title", "h1.section-header", "h1"]
    )
    if not title:
        return None

    company = (
        _first_text(soup, [".company-name", "#header .company-name"])
        or _company_from_url(url)
    )
    if not company:
        return None

    # Greenhouse often prefixes "at " — strip it.
    if company.lower().startswith("at "):
        company = company[3:].strip()

    location = _first_text(soup, [".location", "#header .location"])
    description = _first_text(soup, ["#content", ".job__description"])

    apply_link = soup.select_one("a#submit_app, a.btn-apply, a.template-btn-submit")
    apply_url = (
        apply_link.get("href")
        if isinstance(apply_link, Tag) and isinstance(apply_link.get("href"), str)
        else None
    )

    return ExtractedJob(
        title=title,
        company_name=company,
        location=location,
        remote_type=detect_remote_type(location, description),
        apply_url=apply_url if isinstance(apply_url, str) else None,
        description=description,
        ats_type="greenhouse",
        ats_job_id=_job_id_from_url(url),
        extraction_source="ats_parser",
        extraction_confidence=0.92,
    )
