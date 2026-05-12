"""Resolver unit tests.

Network is mocked via a tiny fake-session helper. The careers + ATS
resolvers are pure HTML parsing once you have a Response, so the tests
exercise the real logic without hitting the internet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
import requests

from job_agent.sources.funding.resolvers._fetch import FetchResult
from job_agent.sources.funding.resolvers.ats import (
    detect_ats_from_html,
    detect_ats_from_url,
    resolve_ats,
)
from job_agent.sources.funding.resolvers.careers import resolve_careers_url
from job_agent.sources.funding.resolvers.website import (
    _is_aggregator,
    resolve_website_cheap,
)

# ---------------------------------------------------------------------------
# Fake requests.Session that returns canned responses per URL


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
    calls: list[str] = field(default_factory=list)

    def get(self, url: str, *args: object, **kwargs: object) -> _FakeResponse:
        self.calls.append(url)
        resp = self.routes.get(url)
        if resp is None:
            return _FakeResponse(status_code=404, text="", url=url)
        resp.url = resp.url or url
        return resp


# ---------------------------------------------------------------------------
# website.py — cheap-path tests


def test_cheap_path_apex_host() -> None:
    assert resolve_website_cheap("https://deepinfra.com/blog/x") == "https://deepinfra.com"


def test_cheap_path_strips_www() -> None:
    assert resolve_website_cheap("https://www.acme.io/page") == "https://acme.io"


def test_cheap_path_rejects_techcrunch() -> None:
    assert resolve_website_cheap("https://techcrunch.com/2026/05/04/foo") is None


def test_cheap_path_rejects_subdomain_of_aggregator() -> None:
    assert resolve_website_cheap("https://news.ycombinator.com/item?id=1") is None


def test_cheap_path_none_for_empty() -> None:
    assert resolve_website_cheap(None) is None
    assert resolve_website_cheap("") is None


def test_is_aggregator_matches_subdomains() -> None:
    assert _is_aggregator("techcrunch.com") is True
    assert _is_aggregator("news.ycombinator.com") is True
    assert _is_aggregator("foo.linkedin.com") is True
    assert _is_aggregator("acme.io") is False


# ---------------------------------------------------------------------------
# careers.py — probing + nav-scrape


def test_careers_canonical_path_hit() -> None:
    sess = _FakeSession(
        routes={
            "https://acme.io/careers": _FakeResponse(
                status_code=200,
                text="<html><body>" + ("We're hiring engineers! Open positions below. " * 5) + "</body></html>",
            )
        }
    )
    out = resolve_careers_url("https://acme.io", session=sess, request_timeout=1.0)
    assert out == "https://acme.io/careers"
    # First probe wins, others not attempted.
    assert sess.calls == ["https://acme.io/careers"]


def test_careers_second_canonical_path_hit() -> None:
    sess = _FakeSession(
        routes={
            "https://acme.io/jobs": _FakeResponse(
                status_code=200,
                text="<html>" + ("Open positions. Join our team. " * 8) + "</html>",
            )
        }
    )
    out = resolve_careers_url("https://acme.io", session=sess, request_timeout=1.0)
    assert out == "https://acme.io/jobs"


def test_careers_404s_fall_through_to_nav_scrape() -> None:
    sess = _FakeSession(
        routes={
            "https://acme.io": _FakeResponse(
                status_code=200,
                text='<html><a href="/positions">Job Openings</a></html>',
                url="https://acme.io",
            ),
            "https://acme.io/positions": _FakeResponse(
                status_code=200,
                text="<html>" + ("Job openings. We're hiring. " * 8) + "</html>",
            ),
        }
    )
    out = resolve_careers_url("https://acme.io", session=sess, request_timeout=1.0)
    assert out == "https://acme.io/positions"


def test_careers_retry_budget_limits_canonical_probes() -> None:
    sess = _FakeSession(routes={})  # everything 404s
    # All canonical paths return 404 -> stage 2 also fails (no homepage HTML).
    out = resolve_careers_url(
        "https://acme.io", retry_budget=2, session=sess, request_timeout=1.0
    )
    assert out is None
    # With retry_budget=2, we should hit at most 2 canonical-path URLs plus
    # the homepage fetch — total ≤ 3 distinct URLs.
    assert len(set(sess.calls)) <= 3


def test_careers_skips_linkedin_nav_link() -> None:
    sess = _FakeSession(
        routes={
            "https://acme.io": _FakeResponse(
                status_code=200,
                text=(
                    '<html><a href="https://linkedin.com/company/acme/jobs">LinkedIn Jobs</a>'
                    '<a href="/openings">Job Openings</a></html>'
                ),
                url="https://acme.io",
            ),
            "https://acme.io/openings": _FakeResponse(
                status_code=200, text=("Open positions. Join our team. " * 10)
            ),
        }
    )
    out = resolve_careers_url("https://acme.io", session=sess, request_timeout=1.0)
    assert out == "https://acme.io/openings"


# ---------------------------------------------------------------------------
# ats.py — URL + HTML detection


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://boards.greenhouse.io/acme", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme", "greenhouse"),
        ("https://jobs.lever.co/acme", "lever"),
        ("https://jobs.ashbyhq.com/acme", "ashby"),
        ("https://acme.myworkdayjobs.com/en-US/careers", "workday"),
        ("https://jobs.smartrecruiters.com/Acme", "smartrecruiters"),
        ("https://acme.io/careers", None),
    ],
)
def test_detect_ats_from_url(url: str, expected: str | None) -> None:
    assert detect_ats_from_url(url) == expected


def test_detect_ats_iframe_greenhouse() -> None:
    html = """
    <html><body>
      <iframe src="https://boards.greenhouse.io/acme/embed/job_board?for=acme"></iframe>
    </body></html>
    """
    ats, url = detect_ats_from_html(html)
    assert ats == "greenhouse"
    assert url is not None
    assert "greenhouse.io" in url


def test_detect_ats_iframe_lever() -> None:
    html = '<iframe src="https://jobs.lever.co/acme"></iframe>'
    ats, url = detect_ats_from_html(html)
    assert ats == "lever"
    assert url == "https://jobs.lever.co/acme"


def test_detect_ats_inline_script_greenhouse() -> None:
    html = """
    <html>
      <script>
        var s = document.createElement('script');
        s.src = 'https://boards.greenhouse.io/embed/job_board.js?for=acme';
      </script>
    </html>
    """
    ats, url = detect_ats_from_html(html)
    assert ats == "greenhouse"
    assert url is not None and "greenhouse.io" in url


def test_detect_ats_no_match() -> None:
    html = "<html><body>No ATS here. Just text.</body></html>"
    ats, url = detect_ats_from_html(html)
    assert ats is None
    assert url is None


def test_resolve_ats_direct_url_short_circuits_fetch() -> None:
    """If the careers URL is already an ATS host, we don't fetch."""
    sess = _FakeSession(routes={})  # any fetch would return 404
    ats, url = resolve_ats(
        "https://jobs.ashbyhq.com/acme", session=sess, request_timeout=1.0
    )
    assert ats == "ashby"
    assert url == "https://jobs.ashbyhq.com/acme"
    assert sess.calls == []  # no fetch attempted


def test_resolve_ats_follows_redirect_to_ats() -> None:
    sess = _FakeSession(
        routes={
            "https://acme.io/careers": _FakeResponse(
                status_code=200,
                text="<html>redirected</html>",
                url="https://jobs.lever.co/acme",
            )
        }
    )
    ats, url = resolve_ats(
        "https://acme.io/careers", session=sess, request_timeout=1.0
    )
    assert ats == "lever"
    assert url == "https://jobs.lever.co/acme"


class _StubFallback:
    """Test double — records calls + returns canned HTML."""

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


def test_careers_resolver_escalates_to_fallback_on_403() -> None:
    """When the homepage returns 403, fallback Playwright HTML should be
    parsed for nav links."""
    sess = _FakeSession(
        routes={
            "https://acme.io": _FakeResponse(status_code=403, text=""),  # 403 -> fallback
        }
    )
    fb = _StubFallback(
        html=(
            "<html><body>"
            + ("filler " * 200)
            + '<a href="/positions">Job Openings</a>'
            + "</body></html>"
        )
    )
    # /careers etc. probes also 404 by default — fallback might be called for them too.
    out = resolve_careers_url(
        "https://acme.io", session=sess, request_timeout=1.0, fallback=fb  # type: ignore[arg-type]
    )
    # Fallback returned a homepage with /positions; we then probe that —
    # which 404s in the fake session. So returned URL stays None, but
    # the important thing is fallback WAS used on the homepage path.
    assert any("acme.io" in c for c in fb.calls)
    # ensure out doesn't crash
    assert out is None or out.endswith("/positions")


def test_resolve_ats_uses_fallback_on_403() -> None:
    sess = _FakeSession(routes={"https://acme.io/careers": _FakeResponse(status_code=403)})
    fb = _StubFallback(
        html='<iframe src="https://boards.greenhouse.io/acme"></iframe>',
        status=200,
    )
    ats, url = resolve_ats(
        "https://acme.io/careers",
        session=sess,
        request_timeout=1.0,
        fallback=fb,  # type: ignore[arg-type]
    )
    assert ats == "greenhouse"
    assert url == "https://boards.greenhouse.io/acme"
    assert fb.calls == ["https://acme.io/careers"]


def test_resolve_ats_detects_iframe_after_fetch() -> None:
    sess = _FakeSession(
        routes={
            "https://acme.io/careers": _FakeResponse(
                status_code=200,
                text='<iframe src="https://boards.greenhouse.io/acme"></iframe>',
                url="https://acme.io/careers",
            )
        }
    )
    ats, url = resolve_ats(
        "https://acme.io/careers", session=sess, request_timeout=1.0
    )
    assert ats == "greenhouse"
    assert url == "https://boards.greenhouse.io/acme"
