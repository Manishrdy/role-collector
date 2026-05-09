"""Integration test for the Phase 3 fetch → extract → save pipeline.

We replace the real Playwright fetcher with one that hands back the
fixture HTML files, leave the deterministic parsers running for real,
and skip the LLM (the fixtures are matched by ATS/JSON-LD parsers).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from job_agent.browser import page_fetch
from job_agent.browser.page_fetch import FetchedPage
from job_agent.db.migrate import migrate
from job_agent.graph import nodes
from job_agent.graph.state import AgentState

FIXTURES = Path(__file__).parent / "fixtures" / "extract"


def _fetched(url: str, html: str) -> FetchedPage:
    return FetchedPage(
        url=url,
        canonical_url=url,
        final_url=url,
        domain=url.split("/")[2],
        page_title="x",
        http_status=200,
        html=html,
        content_hash="hash",
        fetched_at="2026-05-09T00:00:00+00:00",
        status="ok",
    )


@pytest.fixture
def patched_fetcher(monkeypatch: pytest.MonkeyPatch) -> dict[str, FetchedPage]:
    """Stub fetch_pages so the node test never launches Playwright."""
    fixtures = {
        "https://jobs.ashbyhq.com/sleeper/af131373-94e9-4fce-8da5-70d6855f5294": _fetched(
            "https://jobs.ashbyhq.com/sleeper/af131373-94e9-4fce-8da5-70d6855f5294",
            (FIXTURES / "ashby_jsonld.html").read_text(),
        ),
        "https://boards.greenhouse.io/beaconai/jobs/9876543": _fetched(
            "https://boards.greenhouse.io/beaconai/jobs/9876543",
            (FIXTURES / "greenhouse_detail.html").read_text(),
        ),
    }

    def fake_fetch(urls: list[str], **_: object) -> list[FetchedPage]:
        return [fixtures[u] for u in urls]

    monkeypatch.setattr(nodes, "fetch_pages", fake_fetch)
    monkeypatch.setattr(page_fetch, "fetch_pages", fake_fetch)
    return fixtures


def _seed_state(urls_meta: list[dict[str, object]]) -> AgentState:
    state: AgentState = {}
    state["candidate_urls"] = urls_meta  # type: ignore[typeddict-item]
    state["fetched_pages"] = []
    state["extracted_jobs"] = []
    state["saved_jobs"] = []
    state["runtime"] = {}
    return state


def test_full_phase3_pipeline_writes_jobs(
    isolated_db: Path,
    patched_fetcher: dict[str, FetchedPage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrate()

    # Skip the LLM entirely — fixtures hit the ATS/JSON-LD parsers.
    monkeypatch.setattr(nodes, "_build_llm_extractor", lambda cfg: None)

    fixture_urls = list(patched_fetcher.keys())
    ashby_canonical = next(u for u in fixture_urls if "ashbyhq" in u)
    ashby_apply = ashby_canonical + "/application?utm_source=test&gclid=zzz"
    candidate_urls = [
        {
            "url": ashby_apply,  # exercises normalize_candidate_url path
            "canonical_url": ashby_apply,
            "title": "Apply-path candidate",
            "snippet": "",
            "rank": 0,
            "engine": "google",
            "source_type": "ats_google_search",
            "source_query": 'site:jobs.ashbyhq.com intitle:"engineer"',
            "target_domain": "",
            "ats_type": "",
            "time_window": "past_24h",
            "role": "software engineer",
            "location": None,
        },
        {
            "url": next(u for u in fixture_urls if "greenhouse" in u),
            "canonical_url": next(u for u in fixture_urls if "greenhouse" in u),
            "title": "irrelevant",
            "snippet": "",
            "rank": 1,
            "engine": "google",
            "source_type": "ats_google_search",
            "source_query": "another query",
            "target_domain": "",
            "ats_type": "",
            "time_window": "past_24h",
            "role": "software engineer",
            "location": None,
        },
    ]
    state = _seed_state(candidate_urls)

    state = nodes.fetch_candidate_pages_node(state)
    assert len(state["fetched_pages"]) == 2
    assert all(p["status"] == "ok" for p in state["fetched_pages"])
    # The apply-path candidate must have been rewritten in place.
    fetched_urls = {p["url"] for p in state["fetched_pages"]}
    assert ashby_canonical in fetched_urls
    assert ashby_apply not in fetched_urls

    state = nodes.extract_job_data_node(state)
    assert len(state["extracted_jobs"]) == 2
    # Ashby resolves via JSON-LD with URL-derived ats_type augmentation.
    ashby_record = next(
        r for r in state["extracted_jobs"] if r["job"]["company_name"] == "Sleeper"
    )
    assert ashby_record["job"]["extraction_source"] == "jsonld"
    assert ashby_record["job"]["ats_type"] == "ashby"
    assert ashby_record["job"]["ats_job_id"] == "af131373-94e9-4fce-8da5-70d6855f5294"

    state = nodes.save_jobs_node(state)
    assert len(state["saved_jobs"]) == 2

    with sqlite3.connect(isolated_db) as conn:
        n_jobs = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        n_sources = conn.execute("SELECT count(*) FROM job_sources").fetchone()[0]
        n_fetches = conn.execute("SELECT count(*) FROM page_fetches").fetchone()[0]
        review = conn.execute("SELECT count(*) FROM jobs WHERE needs_review = 1").fetchone()[0]
        ats_ids = conn.execute(
            "SELECT ats_type, ats_job_id FROM jobs WHERE ats_type IS NOT NULL"
        ).fetchall()
    assert n_jobs == 2
    assert n_sources == 2
    assert n_fetches == 2
    assert review == 0
    # Both saved jobs carry their ATS fingerprint for Phase-4 dedup.
    assert {r[0] for r in ats_ids} == {"ashby", "greenhouse"}
