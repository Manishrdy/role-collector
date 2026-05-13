"""Normalization helpers for ATS discovery metadata."""

from __future__ import annotations

from datetime import UTC, datetime


_EMPLOYMENT_MAP = {
    "full-time": "FULL_TIME",
    "full time": "FULL_TIME",
    "fulltime": "FULL_TIME",
    "regular": "FULL_TIME",
    "part-time": "PART_TIME",
    "part time": "PART_TIME",
    "parttime": "PART_TIME",
    "contract": "CONTRACT",
    "contractor": "CONTRACT",
    "freelance": "CONTRACT",
    "intern": "INTERN",
    "internship": "INTERN",
    "temporary": "TEMPORARY",
    "seasonal": "TEMPORARY",
}


def normalize_employment_type(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    norm = raw.strip().lower()
    if not norm:
        return None
    for key, value in _EMPLOYMENT_MAP.items():
        if key in norm:
            return value
    return None


def normalize_remote_type(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    norm = raw.strip().lower().replace("_", "-")
    if norm in {"remote", "work-from-home", "wfh"}:
        return "remote"
    if norm in {"hybrid"}:
        return "hybrid"
    if norm in {"onsite", "on-site", "in-office", "in office"}:
        return "onsite"
    return None


def normalize_iso_datetime(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def freshness_bucket(posted_at_iso: str | None, *, observed_at_iso: str) -> str:
    """Bucket age based on source posted timestamp when available."""
    observed = normalize_iso_datetime(observed_at_iso)
    if observed is None:
        return "unknown"
    obs = datetime.fromisoformat(observed)
    posted = normalize_iso_datetime(posted_at_iso)
    if posted is None:
        return "unknown"
    age_hours = (obs - datetime.fromisoformat(posted)).total_seconds() / 3600.0
    if age_hours < 0:
        return "unknown"
    if age_hours <= 24:
        return "lt_24h"
    if age_hours <= 72:
        return "24_72h"
    return "gt_72h"
