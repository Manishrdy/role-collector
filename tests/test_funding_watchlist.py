"""Watchlist board enumeration tests.

Pure HTML parsing — no network. Validates the generic board-walk works
across the three main ATS providers we care about.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

from job_agent.sources.funding.resolvers._fetch import FetchResult
from job_agent.sources.funding.watchlist import enumerate_board_html, fetch_and_enumerate


def test_greenhouse_board_extracts_job_urls() -> None:
    html = """
    <html><body>
      <div class="opening">
        <a href="/acme/jobs/12345?gh_jid=12345">Senior Engineer</a>
      </div>
      <div class="opening">
        <a href="/acme/jobs/67890">Staff Engineer</a>
      </div>
      <a href="/acme">Home</a>
      <a href="https://acme.com/about">Off-host nav</a>
    </body></html>
    """
    urls = enumerate_board_html(html=html, board_url="https://boards.greenhouse.io/acme")
    assert "https://boards.greenhouse.io/acme/jobs/12345" in urls
    assert "https://boards.greenhouse.io/acme/jobs/67890" in urls
    # Sibling-path /acme (same depth) and off-host links excluded.
    assert all("acme.com" not in u for u in urls)
    assert len(urls) == 2


def test_lever_board_extracts_job_urls() -> None:
    html = """
    <html><body>
      <a href="https://jobs.lever.co/acme/abc-def-123">Engineer</a>
      <a href="https://jobs.lever.co/acme/uuid-xyz-789">Designer</a>
      <a href="https://jobs.lever.co/acme">Listing</a>
    </body></html>
    """
    urls = enumerate_board_html(html=html, board_url="https://jobs.lever.co/acme")
    assert "https://jobs.lever.co/acme/abc-def-123" in urls
    assert "https://jobs.lever.co/acme/uuid-xyz-789" in urls
    assert len(urls) == 2


def test_ashby_board_relative_urls() -> None:
    html = """
    <html><body>
      <a href="/acme/abcdef-1234-5678">Open Role 1</a>
      <a href="/acme/zyxwvu-9876">Open Role 2</a>
    </body></html>
    """
    urls = enumerate_board_html(html=html, board_url="https://jobs.ashbyhq.com/acme")
    assert "https://jobs.ashbyhq.com/acme/abcdef-1234-5678" in urls
    assert "https://jobs.ashbyhq.com/acme/zyxwvu-9876" in urls


def test_drops_static_asset_urls() -> None:
    html = """
    <html><body>
      <a href="/acme/jobs/1">Job 1</a>
      <a href="/acme/assets/style.css">CSS</a>
      <a href="/acme/scripts/app.js">JS</a>
      <a href="/acme/logo.png">Logo</a>
    </body></html>
    """
    urls = enumerate_board_html(html=html, board_url="https://boards.greenhouse.io/acme")
    assert len(urls) == 1
    assert urls[0].endswith("/jobs/1")


def test_drops_javascript_and_mailto_links() -> None:
    html = """
    <html><body>
      <a href="javascript:void(0)">Click</a>
      <a href="mailto:hr@acme.com">Email</a>
      <a href="#section">Anchor</a>
      <a href="/acme/jobs/1">Real Job</a>
    </body></html>
    """
    urls = enumerate_board_html(html=html, board_url="https://boards.greenhouse.io/acme")
    assert urls == ["https://boards.greenhouse.io/acme/jobs/1"]


def test_max_jobs_caps_output() -> None:
    links = "".join(
        f'<a href="/acme/jobs/{i}">Job {i}</a>' for i in range(200)
    )
    html = f"<html><body>{links}</body></html>"
    urls = enumerate_board_html(
        html=html, board_url="https://boards.greenhouse.io/acme", max_jobs=25
    )
    assert len(urls) == 25


def test_deduplicates_repeated_urls() -> None:
    html = """
    <html><body>
      <a href="/acme/jobs/1">Job 1 (link 1)</a>
      <a href="/acme/jobs/1">Job 1 (link 2)</a>
      <a href="/acme/jobs/1?utm=x">Job 1 (with utm)</a>
      <a href="/acme/jobs/2">Job 2</a>
    </body></html>
    """
    urls = enumerate_board_html(html=html, board_url="https://boards.greenhouse.io/acme")
    # Canonicalisation drops query string -> /jobs/1 appears once.
    assert urls.count("https://boards.greenhouse.io/acme/jobs/1") == 1
    assert len(urls) == 2


def test_empty_html_returns_empty_list() -> None:
    assert enumerate_board_html(html="", board_url="https://boards.greenhouse.io/acme") == []
    assert (
        enumerate_board_html(html="<html></html>", board_url="https://boards.greenhouse.io/acme")
        == []
    )


def test_invalid_board_url_returns_empty() -> None:
    """A board URL without a host produces no candidates."""
    out = enumerate_board_html(html="<a href='/x/1'>x</a>", board_url="not-a-url")
    assert out == []


# ---------------------------------------------------------------------------
# fetch_and_enumerate — integration of fetch_with_fallback


@dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ""
    url: str = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"http {self.status_code}")

    def json(self) -> object:
        import json

        return json.loads(self.text)


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
    def __init__(self, html: str = "", status: int = 200) -> None:
        self._html = html
        self._status = status
        self.calls: list[str] = []

    def fetch_via_playwright(self, url: str) -> FetchResult:
        self.calls.append(url)
        return FetchResult(html=self._html, status_code=self._status, final_url=url, used_fallback=True)

    def close(self) -> None:
        pass


def test_fetch_and_enumerate_escalates_to_fallback_on_403() -> None:
    """A non-Lever, non-Greenhouse board that returns 403 should still
    yield jobs when fallback is provided. (Lever + Greenhouse have their
    own API paths and don't take the HTML+fallback branch.)"""
    sess = _FakeSession(routes={"https://jobs.ashbyhq.com/acme": _FakeResponse(status_code=403)})
    fb = _StubFallback(
        html=(
            '<html><body>'
            '<a href="https://jobs.ashbyhq.com/acme/job-1">Job 1</a>'
            '<a href="https://jobs.ashbyhq.com/acme/job-2">Job 2</a>'
            '</body></html>'
        ),
        status=200,
    )
    urls = fetch_and_enumerate(
        "https://jobs.ashbyhq.com/acme",
        session=sess,  # type: ignore[arg-type]
        fallback=fb,  # type: ignore[arg-type]
    )
    assert len(urls) == 2
    assert fb.calls == ["https://jobs.ashbyhq.com/acme"]


def test_fetch_and_enumerate_no_fallback_returns_empty_on_403() -> None:
    """Without fallback, a 403 board page returns nothing — same as before."""
    sess = _FakeSession(routes={"https://lever.co/acme": _FakeResponse(status_code=403)})
    urls = fetch_and_enumerate(
        "https://lever.co/acme",
        session=sess,  # type: ignore[arg-type]
        fallback=None,
    )
    assert urls == []


# ---------------------------------------------------------------------------
# Provider-specific API enumerators


def test_match_lever_slug() -> None:
    from job_agent.sources.funding.watchlist import _match_lever_slug

    assert _match_lever_slug("https://jobs.lever.co/acme") == "acme"
    assert _match_lever_slug("https://jobs.lever.co/Acme-Corp/") == "Acme-Corp"
    assert _match_lever_slug("https://jobs.lever.co/acme?from=x") == "acme"
    assert _match_lever_slug("https://boards.greenhouse.io/acme") is None
    assert _match_lever_slug("https://acme.io/careers") is None


def test_match_greenhouse_slug() -> None:
    from job_agent.sources.funding.watchlist import _match_greenhouse_slug

    assert _match_greenhouse_slug("https://boards.greenhouse.io/acme") == "acme"
    assert _match_greenhouse_slug("https://job-boards.greenhouse.io/acme") == "acme"
    assert _match_greenhouse_slug("https://jobs.lever.co/acme") is None


def test_lever_api_path_used_for_lever_urls() -> None:
    """`fetch_and_enumerate` against a Lever board hits the API, not HTML."""
    sess = _FakeSession(
        routes={
            "https://api.lever.co/v0/postings/acme?mode=json": _FakeResponse(
                status_code=200,
                text=(
                    '[{"id":"abc","text":"Senior Engineer",'
                    '"hostedUrl":"https://jobs.lever.co/acme/abc"},'
                    '{"id":"def","text":"Staff Engineer",'
                    '"hostedUrl":"https://jobs.lever.co/acme/def"}]'
                ),
            ),
        }
    )
    urls = fetch_and_enumerate("https://jobs.lever.co/acme", session=sess)  # type: ignore[arg-type]
    assert urls == ["https://jobs.lever.co/acme/abc", "https://jobs.lever.co/acme/def"]


def test_greenhouse_api_path_used_for_greenhouse_urls() -> None:
    sess = _FakeSession(
        routes={
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": _FakeResponse(
                status_code=200,
                text=(
                    '{"jobs":['
                    '{"id":1,"absolute_url":"https://boards.greenhouse.io/acme/jobs/1"},'
                    '{"id":2,"absolute_url":"https://boards.greenhouse.io/acme/jobs/2"}'
                    "]}"
                ),
            ),
        }
    )
    urls = fetch_and_enumerate(
        "https://boards.greenhouse.io/acme",
        session=sess,  # type: ignore[arg-type]
    )
    assert urls == [
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://boards.greenhouse.io/acme/jobs/2",
    ]


def test_lever_api_failure_returns_empty_no_html_fallback() -> None:
    """Lever HTML can't be enumerated by anchor-walking, so an API failure
    must NOT fall back to HTML (which would always return 0)."""
    sess = _FakeSession(
        routes={
            "https://api.lever.co/v0/postings/acme?mode=json": _FakeResponse(
                status_code=500
            ),
        }
    )
    urls = fetch_and_enumerate("https://jobs.lever.co/acme", session=sess)  # type: ignore[arg-type]
    assert urls == []


def test_greenhouse_api_failure_falls_back_to_html() -> None:
    """Greenhouse HTML boards are anchor-tag pages, so an API miss SHOULD
    fall back to the generic HTML walker."""
    html_body = (
        '<html><body>'
        '<a href="/acme/jobs/12345">Engineer</a>'
        '<a href="/acme/jobs/67890">Designer</a>'
        '</body></html>'
    )
    sess = _FakeSession(
        routes={
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": _FakeResponse(
                status_code=500
            ),
            "https://boards.greenhouse.io/acme": _FakeResponse(
                status_code=200, text=html_body
            ),
        }
    )
    urls = fetch_and_enumerate(
        "https://boards.greenhouse.io/acme",
        session=sess,  # type: ignore[arg-type]
    )
    assert len(urls) == 2
    assert "https://boards.greenhouse.io/acme/jobs/12345" in urls


def test_lever_api_respects_max_jobs() -> None:
    items = ",".join(
        f'{{"id":"{i}","hostedUrl":"https://jobs.lever.co/acme/{i}"}}' for i in range(50)
    )
    sess = _FakeSession(
        routes={
            "https://api.lever.co/v0/postings/acme?mode=json": _FakeResponse(
                status_code=200, text=f"[{items}]"
            ),
        }
    )
    urls = fetch_and_enumerate("https://jobs.lever.co/acme", session=sess, max_jobs=5)  # type: ignore[arg-type]
    assert len(urls) == 5
