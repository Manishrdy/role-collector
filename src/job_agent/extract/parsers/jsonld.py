"""schema.org/JobPosting JSON-LD parser.

Many ATS pages and most modern career sites embed a ``JobPosting`` block
under ``<script type="application/ld+json">``. When present, it's the
single highest-fidelity source of structured job data.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, cast
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from job_agent.extract.parsers._common import (
    clean_text,
    detect_ats_type,
    detect_remote_type,
)
from job_agent.extract.schema import ExtractedJob

# URL-shape patterns to recover the ATS-side job id when JSON-LD doesn't
# expose ``identifier.value``.
_ATS_JOB_ID_FROM_URL: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ashby", re.compile(r"/[^/]+/([0-9a-fA-F-]{20,})")),
    ("lever", re.compile(r"/[^/]+/([0-9a-fA-F-]{20,})")),
    ("greenhouse", re.compile(r"/jobs/(\d+)")),
)

log = logging.getLogger(__name__)


def _iter_jsonld_blocks(html: str) -> list[Any]:
    """Return parsed JSON-LD payloads from every ld+json script tag."""
    soup = BeautifulSoup(html, "lxml")
    out: list[Any] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        if not isinstance(tag, Tag):
            continue
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError as e:
            log.debug("jsonld parse failed: %s", e)
    return out


def _is_job_posting(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    t = node.get("@type")
    if isinstance(t, list):
        return any(isinstance(x, str) and x == "JobPosting" for x in t)
    return t == "JobPosting"


def _find_job_posting(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        for item in payload:
            found = _find_job_posting(item)
            if found:
                return found
        return None
    if isinstance(payload, dict):
        if _is_job_posting(payload):
            return cast("dict[str, Any]", payload)
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                if _is_job_posting(item):
                    return cast("dict[str, Any]", item)
    return None


def _company_name(node: dict[str, Any]) -> str | None:
    org = node.get("hiringOrganization")
    if isinstance(org, dict):
        name = org.get("name")
        if isinstance(name, str):
            return clean_text(name)
    if isinstance(org, str):
        return clean_text(org)
    return None


def _location(node: dict[str, Any]) -> str | None:
    loc = node.get("jobLocation")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ]
            joined = ", ".join(p for p in parts if isinstance(p, str) and p)
            if joined:
                return joined
        if isinstance(loc.get("name"), str):
            return clean_text(loc["name"])
    return None


def _salary(node: dict[str, Any]) -> str | None:
    salary = node.get("baseSalary")
    if isinstance(salary, dict):
        value = salary.get("value")
        if isinstance(value, dict):
            min_v = value.get("minValue")
            max_v = value.get("maxValue")
            unit = value.get("unitText") or "YEAR"
            currency = salary.get("currency") or value.get("currency") or ""
            if min_v is not None and max_v is not None:
                return f"{currency} {min_v}-{max_v} / {unit}".strip()
        if isinstance(value, (int, float)):
            return f"{salary.get('currency', '')} {value}".strip()
    return None


def _job_id_from_identifier(node: dict[str, Any]) -> str | None:
    ident = node.get("identifier")
    if isinstance(ident, dict):
        value = ident.get("value")
        if isinstance(value, (str, int)):
            return str(value)
    if isinstance(ident, str):
        return ident
    return None


def _job_id_from_url(url: str, ats_type: str | None) -> str | None:
    if not ats_type:
        return None
    path = urlparse(url).path
    for needle, pattern in _ATS_JOB_ID_FROM_URL:
        if needle != ats_type:
            continue
        m = pattern.search(path)
        if m:
            return m.group(1)
    return None


def parse(html: str, url: str) -> ExtractedJob | None:
    """Return a JobPosting extracted from JSON-LD if any script tag has one."""
    for payload in _iter_jsonld_blocks(html):
        node = _find_job_posting(payload)
        if not node:
            continue

        title = clean_text(node.get("title"))
        company = _company_name(node)
        if not title or not company:
            continue

        location = _location(node)
        description = clean_text(node.get("description"))
        remote_text = node.get("jobLocationType") if isinstance(node.get("jobLocationType"), str) else None
        if isinstance(remote_text, str) and "TELECOMMUTE" in remote_text.upper():
            remote_type: str | None = "remote"
        else:
            remote_type = detect_remote_type(location, description)

        ats_type = detect_ats_type(url)
        ats_job_id = _job_id_from_identifier(node) or _job_id_from_url(url, ats_type)

        return ExtractedJob(
            title=title,
            company_name=company,
            location=location,
            remote_type=remote_type,
            salary_text=_salary(node),
            apply_url=node.get("url") if isinstance(node.get("url"), str) else None,
            posted_date=node.get("datePosted") if isinstance(node.get("datePosted"), str) else None,
            posted_date_confidence=0.9 if node.get("datePosted") else 0.0,
            employment_type=(
                node.get("employmentType")
                if isinstance(node.get("employmentType"), str)
                else None
            ),
            description=description,
            ats_type=ats_type,
            ats_job_id=ats_job_id,
            extraction_source="jsonld",
            extraction_confidence=0.85,
        )
    return None
