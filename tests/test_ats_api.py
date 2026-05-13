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
    enumerate_icims,
    enumerate_lever,
    enumerate_smartrecruiters,
    enumerate_workday,
    greenhouse_slug_from_url,
    icims_slug_from_url,
    lever_slug_from_url,
    smartrecruiters_slug_from_url,
    workday_slug_from_url,
)
from job_agent.sources.ats_api.discovery import (
    discover_from_seeds,
    load_seeds,
    shard_seeds,
)


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
        params = kwargs.get("params")
        if isinstance(params, dict) and params:
            kv = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            url = f"{url}?{kv}"
        self.calls.append(url)
        return self.routes.get(url, _FakeResponse(status_code=404))

    def post(self, url: str, *args: object, **kwargs: object) -> _FakeResponse:
        payload = kwargs.get("json")
        if isinstance(payload, dict) and "offset" in payload:
            url = f"{url}#offset={payload['offset']}"
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


def test_smartrecruiters_slug_from_url() -> None:
    assert smartrecruiters_slug_from_url("https://jobs.smartrecruiters.com/Stripe/abc") == "Stripe"
    assert smartrecruiters_slug_from_url("https://jobs.lever.co/acme") is None


def test_icims_slug_from_url() -> None:
    assert icims_slug_from_url("https://careers.nvidia.icims.com/jobs/1234/foo") == "nvidia"
    assert icims_slug_from_url("https://jobs.lever.co/acme") is None


def test_workday_slug_from_url() -> None:
    assert (
        workday_slug_from_url("https://wd5.myworkdaysite.com/recruiting/airbnb/Airbnb_Careers/job/x")
        == "https://wd5.myworkdaysite.com/recruiting"
    )
    assert workday_slug_from_url("https://jobs.lever.co/acme") is None


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
    assert [x["url"] for x in (out or [])] == [
        "https://jobs.lever.co/acme/a",
        "https://jobs.lever.co/acme/b",
    ]


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
    assert [x["url"] for x in (out or [])] == [
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
    assert [x["url"] for x in (out or [])] == [
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
    assert [x["url"] for x in (out or [])] == ["https://jobs.ashbyhq.com/openai/custom-url"]


def test_enumerate_smartrecruiters_paginates() -> None:
    sess = _FakeSession(
        routes={
            "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=0": _FakeResponse(
                status_code=200,
                text='{"content":[{"id":"a"},{"id":"b"}]}',
            ),
        }
    )
    out = enumerate_smartrecruiters("acme", session=sess)  # type: ignore[arg-type]
    assert [x["url"] for x in (out or [])] == [
        "https://jobs.smartrecruiters.com/acme/a",
        "https://jobs.smartrecruiters.com/acme/b",
    ]


def test_enumerate_workday_uses_cxs_api() -> None:
    sess = _FakeSession(
        routes={
            "https://wd5.myworkdaysite.com/wday/cxs/airbnb/Airbnb_Careers/jobs#offset=0": _FakeResponse(
                status_code=200,
                text='{"jobPostings":[{"externalPath":"/Airbnb_Careers/job/NYC/J123"}]}',
            ),
        }
    )
    out = enumerate_workday(
        "https://wd5.myworkdaysite.com/recruiting/airbnb/Airbnb_Careers",
        session=sess,  # type: ignore[arg-type]
    )
    assert [x["url"] for x in (out or [])] == [
        "https://wd5.myworkdaysite.com/recruiting/Airbnb_Careers/job/NYC/J123"
    ]


def test_enumerate_icims_extracts_job_links() -> None:
    sess = _FakeSession(
        routes={
            "https://careers.nvidia.icims.com/jobs/search?ss=1&searchRelation=keyword_all": _FakeResponse(
                status_code=200,
                text='<a href="/jobs/1234/foo">x</a><a href="/jobs/4567/bar">y</a>',
            ),
        }
    )
    out = enumerate_icims("nvidia", session=sess)  # type: ignore[arg-type]
    assert [x["url"] for x in (out or [])] == [
        "https://careers.nvidia.icims.com/jobs/1234/foo",
        "https://careers.nvidia.icims.com/jobs/4567/bar",
    ]


def test_enumerate_icims_extracts_location_and_posted_date() -> None:
    sess = _FakeSession(
        routes={
            "https://careers.nvidia.icims.com/jobs/search?ss=1&searchRelation=keyword_all": _FakeResponse(
                status_code=200,
                text=(
                    '<div class="row"><a href="/jobs/1234/foo">Engineer</a>'
                    "<span>Location: New York, NY</span><span>Date Posted: May 01, 2026</span></div>"
                ),
            ),
        }
    )
    out = enumerate_icims("nvidia", session=sess)  # type: ignore[arg-type]
    assert out is not None
    assert out[0]["location"] == "New York, NY"
    assert isinstance(out[0]["posted_at_source"], str)


def test_enumerate_workday_marks_remote_when_locations_text_contains_remote() -> None:
    sess = _FakeSession(
        routes={
            "https://wd5.myworkdaysite.com/wday/cxs/airbnb/Airbnb_Careers/jobs#offset=0": _FakeResponse(
                status_code=200,
                text='{"jobPostings":[{"externalPath":"/Airbnb_Careers/job/Remote/J124","locationsText":"Remote"}]}',
            ),
        }
    )
    out = enumerate_workday(
        "https://wd5.myworkdaysite.com/recruiting/airbnb/Airbnb_Careers",
        session=sess,  # type: ignore[arg-type]
    )
    assert out is not None
    assert out[0]["remote_type"] == "remote"


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
        {"unknown_provider": ["acme"], "lever": []}, session=sess  # type: ignore[arg-type]
    )
    # unknown provider is skipped, lever has no slugs.
    assert stats.slugs_checked == 0


# ---------------------------------------------------------------------------
# seed loader + sharder


def test_load_seeds_unions_inline_and_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "ashby.txt"
    path.write_text(
        "\n".join(
            [
                "# header comment",
                "openai",
                "  anthropic  ",
                "",
                "linear",
                "# trailing comment",
            ]
        ),
        encoding="utf-8",
    )
    merged = load_seeds(
        inline_seeds={"ashby": ["openai", "stripe"]},  # openai overlaps the file
        slug_files={"ashby": str(path)},
    )
    # Order preserves inline-first; duplicates collapse; blanks/comments dropped.
    assert merged == {"ashby": ["openai", "stripe", "anthropic", "linear"]}


def test_load_seeds_handles_missing_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    merged = load_seeds(
        inline_seeds={"lever": ["spotify"]},
        slug_files={"lever": str(tmp_path / "absent.txt")},
    )
    assert merged == {"lever": ["spotify"]}


def test_load_seeds_no_inputs() -> None:
    assert load_seeds(None, None) == {}


def test_shard_seeds_walks_in_order_and_wraps() -> None:
    seeds = {"ashby": ["a", "b", "c", "d", "e"]}
    # First shard of 2 from offset 0.
    shard1, next1 = shard_seeds(seeds, slugs_per_cycle=2, offset=0)
    assert shard1 == {"ashby": ["a", "b"]}
    assert next1 == 2
    # Next shard from previous next-offset.
    shard2, next2 = shard_seeds(seeds, slugs_per_cycle=2, offset=next1)
    assert shard2 == {"ashby": ["c", "d"]}
    assert next2 == 4
    # Third shard wraps around to the start.
    shard3, next3 = shard_seeds(seeds, slugs_per_cycle=2, offset=next2)
    assert shard3 == {"ashby": ["e", "a"]}
    assert next3 == 1


def test_shard_seeds_zero_means_no_sharding() -> None:
    seeds = {"ashby": ["a", "b", "c"]}
    shard, next_off = shard_seeds(seeds, slugs_per_cycle=0, offset=0)
    assert shard == seeds
    assert next_off == 0


def test_shard_seeds_when_window_larger_than_catalog() -> None:
    seeds = {"ashby": ["a", "b"]}
    shard, next_off = shard_seeds(seeds, slugs_per_cycle=10, offset=0)
    assert shard == seeds
    assert next_off == 0


def test_shard_seeds_flat_order_across_providers() -> None:
    seeds = {"lever": ["l1", "l2"], "ashby": ["a1", "a2"]}
    shard, next_off = shard_seeds(seeds, slugs_per_cycle=3, offset=0)
    # Order: provider keys sorted deterministically -> ashby first.
    assert shard == {"ashby": ["a1", "a2"], "lever": ["l1"]}
    assert next_off == 3


def test_shard_seeds_empty_catalog() -> None:
    shard, next_off = shard_seeds({}, slugs_per_cycle=100, offset=42)
    assert shard == {}
    assert next_off == 0


def test_discover_from_seeds_uses_concurrency() -> None:
    """Concurrent fan-out still produces the same merged result set."""
    sess = _FakeSession(
        routes={
            "https://api.ashbyhq.com/posting-api/job-board/a": _FakeResponse(
                status_code=200, text='{"jobs":[{"id":"x1","title":"t"}]}',
            ),
            "https://api.ashbyhq.com/posting-api/job-board/b": _FakeResponse(
                status_code=200, text='{"jobs":[{"id":"x2","title":"t"}]}',
            ),
        }
    )
    candidates, stats = discover_from_seeds(
        {"ashby": ["a", "b"]}, session=sess, concurrency=5,  # type: ignore[arg-type]
    )
    urls = sorted(c["url"] for c in candidates)
    assert urls == [
        "https://jobs.ashbyhq.com/a/x1",
        "https://jobs.ashbyhq.com/b/x2",
    ]
    assert stats.slugs_checked == 2
    assert stats.slugs_with_jobs == 2
