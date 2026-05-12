"""End-to-end test for the Phase 4 dedup nodes.

We bypass the fetcher (no Playwright launched) by seeding ``state["fetched_pages"]``
directly with two job fixtures: one normal Greenhouse posting plus a
near-duplicate of the Sleeper Ashby posting (different URL, same description).
The test then runs:

  extract → exact_idempotency_check → semantic_duplicate_check → save

and verifies the dedup classifications + persistence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from job_agent.config import load_config
from job_agent.db import repo
from job_agent.db.migrate import migrate
from job_agent.dedupe import embeddings as embedding_mod
from job_agent.graph import nodes
from job_agent.graph.state import AgentState

FIXTURES = Path(__file__).parent / "fixtures" / "extract"


def _seed_state(urls_meta: list[dict[str, object]]) -> AgentState:
    state: AgentState = {}
    state["candidate_urls"] = urls_meta  # type: ignore[typeddict-item]
    state["fetched_pages"] = []
    state["extracted_jobs"] = []
    state["saved_jobs"] = []
    state["runtime"] = {}
    return state


def _fetched(url: str, html: str) -> dict[str, object]:
    return {
        "url": url,
        "canonical_url": url,
        "final_url": url,
        "domain": url.split("/")[2],
        "page_title": "x",
        "http_status": 200,
        "html": html,
        "content_hash": "hash",
        "fetched_at": "2026-05-09T00:00:00+00:00",
        "status": "ok",
        "error": None,
        "blocked_reason": None,
    }


@pytest.fixture(autouse=True)
def patched_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid loading the real sentence-transformers model in tests.

    The fake encoder maps any text containing the word 'sleeper' to one
    vector and everything else to a different vector — enough to let
    cosine similarity actually distinguish "near-duplicate" pairs.
    """

    def fake_encode(texts: list[str], **_: object) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            out.append([1.0, 0.0] if "sleeper" in t.lower() else [0.0, 1.0])
        return out

    monkeypatch.setattr(embedding_mod, "encode_texts", fake_encode)


def test_full_phase4_pipeline_marks_duplicates(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrate()
    monkeypatch.setattr(nodes, "_build_llm_extractor", lambda cfg: None)

    sleeper_a_url = "https://jobs.ashbyhq.com/sleeper/af131373-94e9-4fce-8da5-70d6855f5294"
    # Non-ATS host so the (ats_type, ats_job_id) dedup layer doesn't catch it
    # before the description_hash layer gets a chance.
    sleeper_b_url = "https://careers.example.com/sleeper-clone-posting"
    greenhouse_url = "https://boards.greenhouse.io/beaconai/jobs/9876543"

    sleeper_html = (FIXTURES / "ashby_jsonld.html").read_text()
    greenhouse_html = (FIXTURES / "greenhouse_detail.html").read_text()

    fetched = [
        _fetched(sleeper_a_url, sleeper_html),
        _fetched(sleeper_b_url, sleeper_html),  # same description, different URL
        _fetched(greenhouse_url, greenhouse_html),
    ]

    candidate_urls = [
        {
            "url": p["url"],
            "canonical_url": p["url"],
            "title": "irrelevant",
            "snippet": "",
            "rank": i,
            "engine": "google",
            "source_type": "ats_google_search",
            "source_query": "test",
            "target_domain": "",
            "ats_type": "",
            "time_window": "past_24h",
            "role": "software engineer",
            "location": None,
        }
        for i, p in enumerate(fetched)
    ]
    state = _seed_state(candidate_urls)
    state["fetched_pages"] = fetched  # type: ignore[typeddict-item]

    state = nodes.extract_job_data_node(state)
    assert len(state["extracted_jobs"]) == 3

    state = nodes.exact_idempotency_check_node(state)
    # First Sleeper extraction has no prior row to collide with — at this
    # point nothing is in `jobs` yet, so no exact dups should be flagged
    # in this single-pass scenario. (Cross-run exact dups exercised below.)
    flags = [r.get("exact_dup_of_job_id") for r in state["extracted_jobs"]]
    assert all(f is None for f in flags)
    # Hashes must be set on every record.
    assert all(r.get("description_hash") for r in state["extracted_jobs"])

    state = nodes.semantic_duplicate_check_node(state)
    # Within a single run there are no pre-existing rows yet, so semantic
    # comparison happens against an empty candidate pool → status='new'.
    assert all(r.get("duplicate_status") == "new" for r in state["extracted_jobs"])

    state = nodes.save_jobs_node(state)
    assert len(state["saved_jobs"]) == 3

    with sqlite3.connect(isolated_db) as conn:
        n_jobs = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        hashes = conn.execute(
            "SELECT description_hash FROM jobs WHERE description_hash IS NOT NULL"
        ).fetchall()
        embeddings_set = conn.execute(
            "SELECT count(*) FROM jobs WHERE description_embedding_json IS NOT NULL"
        ).fetchone()[0]
    assert n_jobs == 3
    # Two Sleeper rows share the same hash, so we should see two
    # description_hash values total (one shared by Sleeper, one for Greenhouse).
    assert len({h[0] for h in hashes}) == 2
    assert embeddings_set == 3


def test_exact_idempotency_short_circuits_on_repeat_run(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same description seen on a *later* run with a different URL must not insert."""
    migrate()
    monkeypatch.setattr(nodes, "_build_llm_extractor", lambda cfg: None)

    sleeper_html = (FIXTURES / "ashby_jsonld.html").read_text()
    sleeper_a_url = "https://jobs.ashbyhq.com/sleeper/af131373-94e9-4fce-8da5-70d6855f5294"
    # Non-ATS host so the (ats_type, ats_job_id) dedup layer doesn't catch it
    # before the description_hash layer gets a chance.
    sleeper_b_url = "https://careers.example.com/sleeper-clone-posting"

    # --- Run 1: insert the original.
    state1 = _seed_state(
        [
            {
                "url": sleeper_a_url,
                "canonical_url": sleeper_a_url,
                "title": "x",
                "snippet": "",
                "rank": 0,
                "engine": "google",
                "source_type": "ats_google_search",
                "source_query": "q1",
                "target_domain": "",
                "ats_type": "",
                "time_window": "past_24h",
                "role": "software engineer",
                "location": None,
            }
        ]
    )
    state1["fetched_pages"] = [_fetched(sleeper_a_url, sleeper_html)]  # type: ignore[typeddict-item]
    nodes.extract_job_data_node(state1)
    nodes.exact_idempotency_check_node(state1)
    nodes.semantic_duplicate_check_node(state1)
    nodes.save_jobs_node(state1)

    n_after_run1 = repo.list_table_names()  # noqa: F841
    with sqlite3.connect(isolated_db) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1

    # --- Run 2: same description but a DIFFERENT URL → exact-dup flagged.
    state2 = _seed_state(
        [
            {
                "url": sleeper_b_url,
                "canonical_url": sleeper_b_url,
                "title": "x",
                "snippet": "",
                "rank": 0,
                "engine": "google",
                "source_type": "ats_google_search",
                "source_query": "q2",
                "target_domain": "",
                "ats_type": "",
                "time_window": "past_24h",
                "role": "software engineer",
                "location": None,
            }
        ]
    )
    state2["fetched_pages"] = [_fetched(sleeper_b_url, sleeper_html)]  # type: ignore[typeddict-item]
    nodes.extract_job_data_node(state2)
    nodes.exact_idempotency_check_node(state2)
    # Exactly one record was flagged as an exact-dup of the run-1 row.
    assert state2["extracted_jobs"][0].get("exact_dup_of_job_id") is not None
    nodes.semantic_duplicate_check_node(state2)
    nodes.save_jobs_node(state2)

    with sqlite3.connect(isolated_db) as conn:
        n_jobs = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        n_sources = conn.execute("SELECT count(*) FROM job_sources").fetchone()[0]
    # No new jobs row was inserted; both source URLs are recorded.
    assert n_jobs == 1
    assert n_sources == 2


def test_save_jobs_batch_respects_min_max_and_keeps_partial_open(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrate()
    cfg = load_config().model_copy(deep=True)
    cfg.agent_loop.batch_min_jobs = 3
    cfg.agent_loop.batch_max_jobs = 4
    monkeypatch.setattr(nodes, "load_config", lambda: cfg)
    monkeypatch.setattr(repo, "touch_existing_job", lambda **_: None)

    run_id = repo.start_search_run(source_type="test", config_snapshot={})
    cycle_id = repo.start_agent_cycle(search_run_id=run_id, config_snapshot={})
    batch_id = repo.create_job_batch(search_run_id=run_id, agent_cycle_id=cycle_id, metadata={"t": 1})

    def _exact(url: str) -> dict[str, object]:
        return {
            "url": url,
            "canonical_url": url,
            "source_type": "ats_google_search",
            "source_query": "q",
            "exact_dup_of_job_id": 42,
        }

    state: AgentState = {
        "run_id": run_id,
        "extracted_jobs": [_exact("https://example.com/1"), _exact("https://example.com/2")],
        "runtime": {"batch_id": batch_id, "batch_current_count": 0, "agent_cycle_id": cycle_id},
    }
    state = nodes.save_jobs_node(state)
    assert state["runtime"]["batch_id"] == batch_id
    assert state["runtime"]["batch_current_count"] == 2

    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute("SELECT status, job_count FROM job_batches WHERE id = ?", (batch_id,)).fetchone()
    assert row == ("open", 0)

    state["extracted_jobs"] = [
        _exact("https://example.com/3"),
        _exact("https://example.com/4"),
        _exact("https://example.com/5"),
    ]
    state = nodes.save_jobs_node(state)
    next_batch_id = state["runtime"]["batch_id"]
    assert next_batch_id is not None and next_batch_id != batch_id
    assert state["runtime"]["batch_current_count"] == 1

    with sqlite3.connect(isolated_db) as conn:
        first = conn.execute("SELECT status, job_count FROM job_batches WHERE id = ?", (batch_id,)).fetchone()
        second = conn.execute("SELECT status, job_count FROM job_batches WHERE id = ?", (next_batch_id,)).fetchone()
    assert first == ("flushed", 4)
    assert second == ("open", 0)


def test_save_jobs_batch_finalize_flushes_under_min(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrate()
    cfg = load_config().model_copy(deep=True)
    cfg.agent_loop.batch_min_jobs = 3
    cfg.agent_loop.batch_max_jobs = 4
    monkeypatch.setattr(nodes, "load_config", lambda: cfg)
    monkeypatch.setattr(repo, "touch_existing_job", lambda **_: None)

    run_id = repo.start_search_run(source_type="test", config_snapshot={})
    cycle_id = repo.start_agent_cycle(search_run_id=run_id, config_snapshot={})
    batch_id = repo.create_job_batch(search_run_id=run_id, agent_cycle_id=cycle_id, metadata={"t": 1})

    state: AgentState = {
        "run_id": run_id,
        "extracted_jobs": [
            {
                "url": "https://example.com/final-1",
                "canonical_url": "https://example.com/final-1",
                "source_type": "ats_google_search",
                "source_query": "q",
                "exact_dup_of_job_id": 42,
            },
            {
                "url": "https://example.com/final-2",
                "canonical_url": "https://example.com/final-2",
                "source_type": "ats_google_search",
                "source_query": "q",
                "exact_dup_of_job_id": 42,
            },
        ],
        "runtime": {
            "batch_id": batch_id,
            "batch_current_count": 0,
            "agent_cycle_id": cycle_id,
            "finalize_batches": True,
        },
    }
    state = nodes.save_jobs_node(state)
    assert state["runtime"]["batch_id"] is None
    assert state["runtime"]["batch_current_count"] == 0

    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute("SELECT status, job_count FROM job_batches WHERE id = ?", (batch_id,)).fetchone()
    assert row == ("flushed", 2)
