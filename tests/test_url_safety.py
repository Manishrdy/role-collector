from __future__ import annotations

import pytest

from job_agent.browser.safety import (
    UrlSafetyError,
    assert_safe,
    canonicalize_url,
    check_url,
    is_apply_path,
    is_blocked_download,
    is_login_page,
    is_shortener,
)

# --- canonicalisation ---------------------------------------------------------


def test_canonicalize_strips_utm_and_fragment() -> None:
    raw = "https://JOBS.ASHBYHQ.COM/acme/12345?utm_source=x&utm_medium=y&keep=1#section"
    assert canonicalize_url(raw) == "https://jobs.ashbyhq.com/acme/12345?keep=1"


def test_canonicalize_strips_trailing_slash_but_preserves_root() -> None:
    assert canonicalize_url("https://example.com/foo/") == "https://example.com/foo"
    assert canonicalize_url("https://example.com/").endswith("/")


# --- guard predicates ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/login",
        "https://example.com/auth/callback",
        "https://example.com/account/login",
        "https://example.com/users/sign_in",
    ],
)
def test_login_pages_are_detected(url: str) -> None:
    assert is_login_page(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.ashbyhq.com/acme/12345/application",
        "https://jobs.lever.co/acme/abc/apply",
    ],
)
def test_apply_paths_are_detected(url: str) -> None:
    assert is_apply_path(url)


@pytest.mark.parametrize("ext", [".exe", ".dmg", ".pkg", ".zip", ".sh"])
def test_blocked_downloads(ext: str) -> None:
    assert is_blocked_download(f"https://example.com/file{ext}")


def test_shorteners_are_blocked() -> None:
    assert is_shortener("https://bit.ly/abc")
    assert is_shortener("https://lnkd.in/xyz")


# --- check_url ----------------------------------------------------------------


def test_allowed_ats_url_passes() -> None:
    decision = check_url("https://jobs.ashbyhq.com/acme/12345?utm_source=x")
    assert decision.allowed, decision.reason
    assert decision.canonical_url == "https://jobs.ashbyhq.com/acme/12345"


def test_offsite_url_rejected() -> None:
    decision = check_url("https://example.com/jobs/123")
    assert not decision.allowed
    assert "allowlist" in decision.reason


def test_login_page_rejected_even_on_allowed_domain() -> None:
    decision = check_url("https://www.linkedin.com/login")
    assert not decision.allowed
    assert "login" in decision.reason


def test_apply_path_rejected_by_default() -> None:
    decision = check_url("https://jobs.lever.co/acme/abc/apply")
    assert not decision.allowed
    assert "apply" in decision.reason


def test_apply_path_allowed_when_explicitly_permitted() -> None:
    # Future flow (with human approval) may need to open apply forms.
    decision = check_url("https://jobs.lever.co/acme/abc/apply", allow_apply=True)
    assert decision.allowed


def test_extra_allowed_lets_resolved_company_domain_through() -> None:
    decision = check_url("https://acmestartup.com/careers", extra_allowed={"acmestartup.com"})
    assert decision.allowed
    assert decision.canonical_url == "https://acmestartup.com/careers"


def test_assert_safe_raises_on_block() -> None:
    with pytest.raises(UrlSafetyError):
        assert_safe("https://example.com/jobs")
