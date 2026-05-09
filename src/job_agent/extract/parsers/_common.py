"""Helpers shared across parsers — URL detection, JSON-tree walking, text cleanup."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_HOST_TO_ATS = (
    ("jobs.ashbyhq.com", "ashby"),
    ("jobs.lever.co", "lever"),
    ("boards.greenhouse.io", "greenhouse"),
    ("job-boards.greenhouse.io", "greenhouse"),
    ("myworkdayjobs.com", "workday"),
    ("myworkdaysite.com", "workday"),
    ("jobs.smartrecruiters.com", "smartrecruiters"),
)


def detect_ats_type(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for needle, ats in _HOST_TO_ATS:
        if host == needle or host.endswith("." + needle):
            return ats
    return None


def walk_json(node: Any, predicate: Any) -> list[dict[str, Any]]:
    """Walk a nested dict/list tree, returning every dict where predicate is truthy."""
    found: list[dict[str, Any]] = []

    def visit(n: Any) -> None:
        if isinstance(n, dict):
            if predicate(n):
                found.append(n)
                return
            for v in n.values():
                visit(v)
        elif isinstance(n, list):
            for item in n:
                visit(item)

    visit(node)
    return found


def clean_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


_REMOTE_HINTS = (
    (re.compile(r"\bremote\b", re.I), "remote"),
    (re.compile(r"\bhybrid\b", re.I), "hybrid"),
    (re.compile(r"\b(on[- ]site|in[- ]office)\b", re.I), "onsite"),
)


def detect_remote_type(*candidates: str | None) -> str | None:
    """Return remote/hybrid/onsite if any candidate string hints at one."""
    for candidate in candidates:
        if not candidate:
            continue
        for rx, label in _REMOTE_HINTS:
            if rx.search(candidate):
                return label
    return None
