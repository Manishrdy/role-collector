"""Scoring tests built around the design §18.7 examples.

We pre-compute embedding cosine values directly rather than running
sentence-transformers — keeps the tests fast and deterministic.
"""

from __future__ import annotations

from job_agent.config import DedupeWeights
from job_agent.dedupe.scoring import JobForScoring, decide, score_pair


def _job(
    *,
    company: str,
    title: str,
    location: str | None = None,
    skills: list[str] | None = None,
    embedding: list[float] | None = None,
) -> JobForScoring:
    return JobForScoring(
        company=company,
        title=title,
        description=None,
        location=location,
        skills=skills or [],
        embedding=embedding,
    )


def test_score_pair_high_overlap_yields_at_least_possible_duplicate() -> None:
    """Same company + ~similar titles + high description cosine → possible_duplicate.

    Modelled on design §18.7 example 1 (OpenAI / Backend Engineer pair).
    The 0.92 hard-duplicate threshold actually requires very similar
    titles too — token_set_ratio between "Backend Engineer" and
    "Software Engineer, Backend" is 0.76, which keeps us in the 0.80-0.92
    possible_duplicate band, not above it.
    """
    weights = DedupeWeights()
    a = JobForScoring(
        company="OpenAI",
        title="Backend Engineer",
        description=None,
        location="San Francisco",
        skills=[],
        embedding=[1.0, 0.0],
    )
    b = JobForScoring(
        company="OpenAI",
        title="Software Engineer, Backend",
        description=None,
        location="San Francisco",
        skills=[],
        embedding=[0.94, 0.34],  # cosine ~ 0.94
    )

    score = score_pair(a, b, weights=weights)
    assert 0.80 <= score.duplicate_score < 0.92
    assert score.company_score == 1.0
    assert score.location_score == 1.0


def test_score_pair_identical_jobs_clear_duplicate() -> None:
    """Identical company/title/description/location → score >= 0.92."""
    weights = DedupeWeights()
    job = JobForScoring(
        company="OpenAI",
        title="Software Engineer, Backend",
        description=None,
        location="San Francisco",
        skills=["python"],
        embedding=[1.0, 0.0],
    )
    score = score_pair(job, job, weights=weights)
    assert score.duplicate_score >= 0.92


def test_decide_classifies_correctly() -> None:
    assert decide(0.95, duplicate_threshold=0.92, possible_duplicate_threshold=0.80) == "duplicate"
    assert (
        decide(0.86, duplicate_threshold=0.92, possible_duplicate_threshold=0.80)
        == "possible_duplicate"
    )
    assert decide(0.69, duplicate_threshold=0.92, possible_duplicate_threshold=0.80) == "new"


def test_score_pair_low_when_titles_differ() -> None:
    weights = DedupeWeights()
    a = _job(company="Acme AI", title="Backend Engineer", location="Remote")
    b = _job(company="Acme AI", title="ML Engineer", location="Remote")
    score = score_pair(a, b, weights=weights)
    # No description embedding on either side → description score is 0.0
    assert score.description_score == 0.0
    # Titles differ enough that final score sits below the duplicate threshold.
    assert score.duplicate_score < 0.92


def test_score_pair_handles_missing_fields_gracefully() -> None:
    weights = DedupeWeights()
    a = _job(company="", title="", location=None)
    b = _job(company="Acme", title="Engineer")
    score = score_pair(a, b, weights=weights)
    assert score.company_score == 0.0
    assert score.title_score == 0.0
    assert score.duplicate_score < 0.5


def test_skills_jaccard_matches_intuition() -> None:
    weights = DedupeWeights()
    a = _job(company="Acme", title="Engineer", skills=["python", "postgres", "redis"])
    b = _job(company="Acme", title="Engineer", skills=["Python", "Postgres", "Kafka"])
    score = score_pair(a, b, weights=weights)
    # |intersection| = 2 (python, postgres), |union| = 4 → 0.5
    assert abs(score.skills_score - 0.5) < 0.001
