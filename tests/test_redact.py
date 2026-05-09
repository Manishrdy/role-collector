from __future__ import annotations

from job_agent.tracing.redact import REDACTED, redact, redact_text


def test_redact_emails_and_phones() -> None:
    text = "Reach me at jane.doe@example.com or (415) 555-1234"
    out = redact_text(text)
    assert "jane.doe@example.com" not in out
    assert "555-1234" not in out
    assert REDACTED in out


def test_redact_api_key() -> None:
    text = "key=sk-abc123XYZ_long-enough-token"
    out = redact_text(text)
    assert "sk-abc123" not in out


def test_redact_drops_sensitive_keys_in_dict() -> None:
    payload = {
        "job_id": 42,
        "resume_text": "long resume here",
        "headers": {"Authorization": "Bearer abc.def.ghi"},
        "skills": ["python", "aws"],
    }
    cleaned = redact(payload)
    assert cleaned["job_id"] == 42
    assert cleaned["resume_text"] == REDACTED
    # nested dict still gets recursed; sensitive key inside still redacted.
    assert cleaned["headers"]["Authorization"] == REDACTED
    assert cleaned["skills"] == ["python", "aws"]


def test_redact_recurses_into_lists() -> None:
    payload = ["call me at 415-555-1234", {"email": "x@y.com", "ok": "yes"}]
    cleaned = redact(payload)
    assert "555-1234" not in cleaned[0]
    assert cleaned[1]["email"] == REDACTED
    assert cleaned[1]["ok"] == "yes"
