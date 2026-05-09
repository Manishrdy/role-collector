"""PII redaction for trace payloads. See design_plan.md §15.4.

This is the single chokepoint before any data leaves the process to Langfuse.
Conservative by design — when in doubt, redact.
"""

from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
API_KEY_RE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]+")
LINKEDIN_PROFILE_RE = re.compile(r"(?i)\blinkedin\.com/in/[A-Za-z0-9_-]+")

SENSITIVE_KEY_FRAGMENTS = (
    "resume",
    "cover_letter",
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "phone",
    "email",
    "address",
    "application_answer",
)

REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    text = EMAIL_RE.sub(REDACTED, text)
    text = PHONE_RE.sub(REDACTED, text)
    text = SSN_RE.sub(REDACTED, text)
    text = API_KEY_RE.sub(REDACTED, text)
    text = BEARER_RE.sub(REDACTED, text)
    text = LINKEDIN_PROFILE_RE.sub("linkedin.com/in/[REDACTED]", text)
    return text


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(frag in lowered for frag in SENSITIVE_KEY_FRAGMENTS)


def redact(value: Any) -> Any:
    """Recursively redact strings and drop values under sensitive keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(str(k)):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        cleaned = [redact(v) for v in value]
        return cleaned if isinstance(value, list) else tuple(cleaned)
    return value
