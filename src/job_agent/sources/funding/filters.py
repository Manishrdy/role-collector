"""Name-based filters for funding-event downstream processing.

These are advisory signals, not hard cuts: a name that looks like a VC fund
gets fewer resolver retries (because most VC funds don't have engineering
careers pages), but is not dropped outright. Some funds (a16z, Sequoia
internal) DO have engineering roles — we just don't want to spend the
same budget on them as we do on operating startups.
"""

from __future__ import annotations

import re

# Substrings that strongly indicate a VC fund / investor / asset manager
# rather than an operating company. Case-insensitive substring match.
_VC_NAME_MARKERS: tuple[str, ...] = (
    "capital",
    "ventures",
    "venture partners",
    "partners",
    "fund",
    "funds",
    "investments",
    "asset management",
    "growth equity",
    "private equity",
    "vc",
)

# Personal-name pattern: two consecutive Title-Case words and nothing else.
# Used to drop "Katie Haun"-style false positives from the funding extractor.
_PERSONAL_NAME_RE = re.compile(r"^[A-Z][a-z]+\s+[A-Z][a-z]+$")

# Legal / business suffixes that look like the second half of a personal
# name to the regex above ("Acme Inc"). Exclude them so we don't drop
# valid companies as personal names.
_BUSINESS_SUFFIXES: frozenset[str] = frozenset(
    {"inc", "corp", "ltd", "llc", "co", "gmbh", "ag", "sa", "plc", "kk", "oy", "ab"}
)


def looks_like_vc_fund(name: str) -> bool:
    """True if the name reads like a VC fund or institutional investor."""
    lowered = name.lower()
    return any(marker in lowered for marker in _VC_NAME_MARKERS)


def looks_like_personal_name(name: str) -> bool:
    """True if the name is a bare two-word personal name.

    Excludes pairs where the second word is a business suffix ("Acme Inc")
    or where the whole pair triggers the VC-fund heuristic ("Sequoia
    Capital") — those are companies, not people.
    """
    stripped = name.strip()
    if not _PERSONAL_NAME_RE.match(stripped):
        return False
    if looks_like_vc_fund(stripped):
        return False
    second_word = stripped.split()[1].lower().rstrip(".")
    return second_word not in _BUSINESS_SUFFIXES


def resolver_retry_budget(name: str) -> int:
    """How many careers-page probes the resolver should try.

    Operating companies get the full budget; suspected VC funds and bare
    personal names get a reduced budget so a run with 50 candidates
    doesn't spend most of its budget on funds that won't yield a
    careers page.
    """
    if looks_like_personal_name(name) or looks_like_vc_fund(name):
        return 3
    return 8
