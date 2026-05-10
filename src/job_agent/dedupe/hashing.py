"""Stable hashes for cross-URL exact-duplicate detection (design §18.1).

The same posting can appear at multiple URLs (cross-listed, mirrored, or
re-broadcast through a different ATS). Comparing canonicalised URLs alone
misses these. SHA-256 of the *normalised* description text catches them.
"""

from __future__ import annotations

import hashlib
import re

# Strip HTML tags AND collapse whitespace BEFORE hashing — different
# wrapper markup or extra newlines would otherwise produce different
# hashes for identical postings.
_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def normalize_for_hash(text: str | None) -> str:
    """Lowercase, strip HTML tags, collapse whitespace. Returns ``""`` on None."""
    if not text:
        return ""
    stripped = _TAG_RX.sub(" ", text)
    collapsed = _WS_RX.sub(" ", stripped).strip().lower()
    return collapsed


def description_hash(text: str | None) -> str | None:
    """SHA-256 hex digest of the normalised description, or ``None`` if empty.

    ``None`` is meaningful: a job with no description text shouldn't be
    treated as a duplicate of every other description-less job. Callers
    must skip the description-hash collision check when this is None.
    """
    normalised = normalize_for_hash(text)
    if not normalised:
        return None
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
