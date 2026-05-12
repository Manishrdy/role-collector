"""ATS API client + discovery orchestrator tests.

Network is mocked via a tiny fake-session helper. Clients are pure
JSON-parsing once you have a Response, so the tests exercise the
real logic without hitting the internet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests

from job_agent.sources.ats_api.clients import (
    ashby_slug_from_url,
    enumerate_ashby,
    enumerate_greenhouse,
    enumerate_lever,
    greenhouse_slug_from_url,
    lever_slug_from_url,
)
from job_agent.sources.ats_api.discovery import discover_from_seeds


@dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"http {self.status_code}")

    def json(self) -> object:
        return json.loads(self.text)


@dataclass
class _FakeSession:
    routes: dict[str, _FakeResponse] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def get(self, url: str, *args: object, **kwargs: object) -> _FakeResponse:
        self.calls.append(url)
        return self.routes.get(url, _FakeResponse(status_code=404))


# ---------------------------------------------------------------------------
# slug parsing


def test_lever_slug_from_url() -> None:
    assert lever_slug_from_url("https://jobs.lever.co/spotify") == "spotify"
    assert lever_slug_from_url("https://jobs.lever.co/Acme-Co/") == "Acme-Co"
    assert lever_slug_from_url("https://jobs.lever.co/acme?from=x") == "acme"
    assert lever_slug_from_url("https://boards.greenhouse.io/spotify") is None
    assert lever_slug_from_url("https://example.com") is None


def test_greenhouse_slug_from_url() -> None:
    assert greenhouse_slug_from_url("https://boards.greenhouse.io/figma") == "figma"
    assert greenhouse_slug_from_url("https://job-boards.greenhouse.io/figma") == "figma"
    assert greenhouse_slug_from_url("https://jobs.lever.co/figma") is None


def test_ashby_slug_from_url() -> None:
    assert ashby_slug_from_url("https://jobs.ashbyhq.com/openai") == "openai"
    assert ashby_slug_from_url("https://jobs.ashbyhq.com/openai/job-id") == "openai"
    assert ashby_slug_from_url("https://boards.greenhouse.io/openai") is None


# ---------------------------------------------------------------------------
# clients


def test_enumerate_lever_parses_hosted_urls() -> None:
    sess = _FakeSession(
        routes={
            "https://api.lever.co/v0/postings/acme?mode=json": _FakeResponse(
                status_code=200,
                text=(
                    '[{"id":"a","hostedUrl":"https://jobs.lever.co/acme/a"},'
                    '{"id":"b","hostedUrl":"https://jobs.lever.co/acme/b"}]'
                ),
            ),
        }
    )
    out = enumerate_lever("acme", session=sess)  # type: ignore[arg-type]
    assert out == ["https://jobs.lever.co/acme/a", "https://jobs.lever.co/acme/b"]


def test_enumerate_lever_returns_none_on_failure() -> None:
    sess = _FakeSession(routes={})  # 404 by default
    out = enumerate_lever("does-not-exist", session=sess)  # type: ignore[arg-type]
    assert out is None


def test_enumerate_lever_respects_max_jobs() -> None:
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
    out = enumerate_lever("acme", session=sess, max_jobs=5)  # type: ignore[arg-type]
    assert len(out or []) == 5


def test_enumerate_greenhouse_parses_absolute_urls() -> None:
    sess = _FakeSession(
        routes={
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": _FakeResponse(
                status_code=200,
                text=(
                    '{"jobs":[{"id":1,"absolute_url":"https://boards.greenhouse.io/acme/jobs/1"},'
                    '{"id":2,"absolute_url":"https://boards.greenhouse.io/acme/jobs/2"}]}'
                ),
            ),
        }
    )
    out = enumerate_greenhouse("acme", session=sess)  # type: ignore[arg-type]
    assert out == [
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://boards.greenhouse.io/acme/jobs/2",
    ]


def test_enumerate_greenhouse_returns_none_on_404() -> None:
    sess = _FakeSession(routes={})
    out = enumerate_greenhouse("nonexistent", session=sess)  # type: ignore[arg-type]
    assert out is None


def test_enumerate_ashby_with_id_constructs_url() -> None:
    """Ashby payload sometimes has `id` only; we construct the URL."""
    sess = _FakeSession(
        routes={
            "https://api.ashbyhq.com/posting-api/job-board/openai": _FakeResponse(
                status_code=200,
                text=(
                    '{"jobs":[{"id":"abc-123","title":"Engineer"},'
                    '{"id":"def-456","title":"Designer"}]}'
                ),
            ),
        }
    )
    out = enumerate_ashby("openai", session=sess)  # type: ignore[arg-type]
    assert out == [
        "https://jobs.ashbyhq.com/openai/abc-123",
        "https://jobs.ashbyhq.com/openai/def-456",
    ]


def test_enumerate_ashby_prefers_explicit_jobUrl() -> None:
    """If Ashby includes `jobUrl` in the payload, use it as-is."""
    sess = _FakeSession(
        routes={
            "https://api.ashbyhq.com/posting-api/job-board/openai": _FakeResponse(
                status_code=200,
                text=(
                    '{"jobs":[{"id":"abc","jobUrl":"https://jobs.ashbyhq.com/openai/custom-url"}]}'
                ),
            ),
        }
    )
    out = enumerate_ashby("openai", session=sess)  # type: ignore[arg-type]
    assert out == ["https://jobs.ashbyhq.com/openai/custom-url"]


# ---------------------------------------------------------------------------
# discovery orchestrator


def test_discover_from_seeds_fans_out_per_provider() -> None:
    sess = _FakeSession(
        routes={
            "https://api.lever.co/v0/postings/spotify?mode=json": _FakeResponse(
                status_code=200,
                text='[{"id":"l1","hostedUrl":"https://jobs.lever.co/spotify/l1"}]',
            ),
            "https://boards-api.greenhouse.io/v1/boards/figma/jobs": _FakeResponse(
                status_code=200,
                text='{"jobs":[{"id":1,"absolute_url":"https://boards.greenhouse.io/figma/jobs/1"}]}',
            ),
            "https://api.ashbyhq.com/posting-api/job-board/openai": _FakeResponse(
                status_code=200,
                text='{"jobs":[{"id":"a1","title":"x"}]}',
            ),
        }
    )
    candidates, stats = discover_from_seeds(
        {
            "lever": ["spotify"],
            "greenhouse": ["figma"],
            "ashby": ["openai"],
        },
        session=sess,  # type: ignore[arg-type]
    )
    assert stats.slugs_checked == 3
    assert stats.slugs_with_jobs == 3
    assert stats.jobs_total == 3
    assert stats.by_provider == {"lever": 1, "greenhouse": 1, "ashby": 1}
    urls = {c["url"] for c in candidates}
    assert "https://jobs.lever.co/spotify/l1" in urls
    assert "https://boards.greenhouse.io/figma/jobs/1" in urls
    assert "https://jobs.ashbyhq.com/openai/a1" in urls


def test_discover_dedupes_across_providers() -> None:
    """If the same URL appears via two providers (unlikely but possible),
    it's only included once."""
    sess = _FakeSession(
        routes={
            "https://api.lever.co/v0/postings/x?mode=json": _FakeResponse(
                status_code=200,
                text='[{"id":"1","hostedUrl":"https://example.com/job/1"}]',
            ),
            "https://boards-api.greenhouse.io/v1/boards/y/jobs": _FakeResponse(
                status_code=200,
                text='{"jobs":[{"id":1,"absolute_url":"https://example.com/job/1"}]}',
            ),
        }
    )
    candidates, _ = discover_from_seeds(
        {"lever": ["x"], "greenhouse": ["y"]}, session=sess  # type: ignore[arg-type]
    )
    assert len(candidates) == 1


def test_discover_handles_failed_slug_gracefully() -> None:
    """A 404 / malformed slug is recorded as `slugs_failed`, not an error."""
    sess = _FakeSession(routes={})  # all 404
    _, stats = discover_from_seeds({"lever": ["nope1", "nope2"]}, session=sess)  # type: ignore[arg-type]
    assert stats.slugs_checked == 2
    assert stats.slugs_failed == 2
    assert stats.jobs_total == 0
    assert stats.by_provider == {"lever": 0}


def test_discover_skips_empty_slug_strings() -> None:
    sess = _FakeSession(routes={})
    _, stats = discover_from_seeds({"lever": ["", "  "]}, session=sess)  # type: ignore[arg-type]
    assert stats.slugs_checked == 0


def test_discover_unknown_provider_is_skipped() -> None:
    sess = _FakeSession(routes={})
    _, stats = discover_from_seeds(
        {"workday": ["acme"], "lever": []}, session=sess  # type: ignore[arg-type]
    )
    # workday is skipped (unknown), lever has no slugs.
    assert stats.slugs_checked == 0
