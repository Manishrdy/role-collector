from __future__ import annotations

from job_agent.dedupe.hashing import description_hash, normalize_for_hash


def test_normalize_strips_html_and_collapses_whitespace() -> None:
    raw = "<p>Hello   <b>world</b>\n\nfrom\tjob</p>"
    assert normalize_for_hash(raw) == "hello world from job"


def test_description_hash_matches_for_equivalent_strings() -> None:
    a = "<p>We are hiring a Backend Engineer.</p>"
    b = "we are    hiring a backend engineer."
    c = "WE ARE HIRING A BACKEND ENGINEER."
    assert description_hash(a) == description_hash(b) == description_hash(c)


def test_description_hash_differs_when_content_differs() -> None:
    a = description_hash("we are hiring a backend engineer")
    b = description_hash("we are hiring a frontend engineer")
    assert a != b


def test_description_hash_returns_none_for_empty() -> None:
    assert description_hash(None) is None
    assert description_hash("") is None
    assert description_hash("   <br>  ") is None
