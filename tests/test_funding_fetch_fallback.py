"""Playwright-fallback unit tests.

Network is mocked via the same fake-session helper used in the resolver
tests. Playwright itself is never started — `FetchFallback` is replaced
with a stub whose ``fetch_via_playwright`` returns a canned result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

from job_agent.sources.funding.resolvers._fetch import (
    FetchResult,
    fetch_with_fallback,
    looks_blocked_or_js,
)


@dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ""
    url: str = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"http {self.status_code}")


@dataclass
class _FakeSession:
    routes: dict[str, _FakeResponse] = field(default_factory=dict)

    def get(self, url: str, *args: object, **kwargs: object) -> _FakeResponse:
        resp = self.routes.get(url)
        if resp is None:
            return _FakeResponse(status_code=404, text="", url=url)
        resp.url = resp.url or url
        return resp


class _StubFallback:
    """Test double for FetchFallback — records calls + returns canned HTML."""

    def __init__(self, html: str = "", status: int = 200) -> None:
        self._html = html
        self._status = status
        self.calls: list[str] = []

    def fetch_via_playwright(self, url: str) -> FetchResult:
        self.calls.append(url)
        return FetchResult(
            html=self._html, status_code=self._status, final_url=url, used_fallback=True
        )

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# looks_blocked_or_js


def test_403_is_blocked() -> None:
    assert looks_blocked_or_js(status_code=403, body="<html></html>") is True


def test_429_is_blocked() -> None:
    assert looks_blocked_or_js(status_code=429, body="<html></html>") is True


def test_503_is_blocked() -> None:
    assert looks_blocked_or_js(status_code=503, body="<html></html>") is True


def test_404_not_retried_with_fallback() -> None:
    """Genuine 404s aren't JS-shells — don't waste Playwright on them."""
    assert looks_blocked_or_js(status_code=404, body="<html>not found</html>") is False


def test_empty_body_triggers_fallback() -> None:
    assert looks_blocked_or_js(status_code=200, body="") is True
    assert looks_blocked_or_js(status_code=200, body="short") is True


def test_anchor_starved_body_triggers_fallback() -> None:
    """A 200 with no <a> tags looks like an SPA shell."""
    body = "<html><body>" + ("x" * 2000) + "</body></html>"
    assert looks_blocked_or_js(status_code=200, body=body) is True


def test_real_body_passes() -> None:
    body = (
        "<html><body>"
        + ('<a href="/x">link</a>' * 10)
        + ("filler " * 100)
        + "</body></html>"
    )
    assert looks_blocked_or_js(status_code=200, body=body) is False


# ---------------------------------------------------------------------------
# fetch_with_fallback


def test_fetch_returns_requests_result_when_response_is_good() -> None:
    body = (
        "<html><body>"
        + ('<a href="/x">link</a>' * 10)
        + ("filler " * 100)
        + "</body></html>"
    )
    sess = _FakeSession(routes={"https://acme.io": _FakeResponse(status_code=200, text=body)})
    fb = _StubFallback(html="should not be used")
    result = fetch_with_fallback("https://acme.io", session=sess, fallback=fb)  # type: ignore[arg-type]
    assert result.status_code == 200
    assert result.used_fallback is False
    assert fb.calls == []  # fallback never invoked


def test_fetch_escalates_to_fallback_on_403() -> None:
    sess = _FakeSession(routes={"https://acme.io": _FakeResponse(status_code=403, text="blocked")})
    fb = _StubFallback(html="<html>rendered</html>", status=200)
    result = fetch_with_fallback("https://acme.io", session=sess, fallback=fb)  # type: ignore[arg-type]
    assert result.used_fallback is True
    assert result.status_code == 200
    assert "rendered" in result.html
    assert fb.calls == ["https://acme.io"]


def test_fetch_escalates_on_js_shell() -> None:
    """200 with empty body should escalate too."""
    sess = _FakeSession(routes={"https://lever.co/x": _FakeResponse(status_code=200, text="")})
    fb = _StubFallback(html="<html>rendered</html>", status=200)
    result = fetch_with_fallback("https://lever.co/x", session=sess, fallback=fb)  # type: ignore[arg-type]
    assert result.used_fallback is True
    assert fb.calls == ["https://lever.co/x"]


def test_fetch_no_fallback_returns_blocked_response_unchanged() -> None:
    """Without a fallback, blocked responses still flow through (caller
    decides what to do with status_code=403)."""
    sess = _FakeSession(routes={"https://acme.io": _FakeResponse(status_code=403)})
    result = fetch_with_fallback("https://acme.io", session=sess, fallback=None)  # type: ignore[arg-type]
    assert result.status_code == 403
    assert result.used_fallback is False


def test_fetch_network_error_escalates_to_fallback() -> None:
    """If requests raises, we still try Playwright when fallback is set."""

    class _BoomSession:
        def get(self, *_a: object, **_k: object) -> _FakeResponse:
            raise requests.ConnectionError("dns")

    fb = _StubFallback(html="<html>rendered</html>", status=200)
    result = fetch_with_fallback("https://x", session=_BoomSession(), fallback=fb)  # type: ignore[arg-type]
    assert result.used_fallback is True
    assert fb.calls == ["https://x"]


def test_fetch_network_error_without_fallback_returns_empty() -> None:
    class _BoomSession:
        def get(self, *_a: object, **_k: object) -> _FakeResponse:
            raise requests.ConnectionError("dns")

    result = fetch_with_fallback("https://x", session=_BoomSession(), fallback=None)  # type: ignore[arg-type]
    assert result.html == ""
    assert result.status_code == 0
    assert result.used_fallback is False
