"""Weighted similarity scoring per design §18.3 + §18.6.

Inputs are two job records (one new, one existing). We compute five
sub-scores in [0, 1], blend them with the configured weights, and
return a :class:`DuplicateScore` ready to persist to
``duplicate_candidates``.

The semantic-dedup node calls :func:`score_pair` after the rapidfuzz
pre-filter has narrowed candidates down — full O(N²) pairing is never
done.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz

from job_agent.config import DedupeWeights
from job_agent.dedupe.embeddings import cosine


@dataclass(frozen=True)
class JobForScoring:
    """Minimal projection of a job record needed for similarity scoring."""

    company: str
    title: str
    description: str | None
    location: str | None
    skills: list[str]
    embedding: list[float] | None


@dataclass(frozen=True)
class DuplicateScore:
    """Per-pair similarity breakdown + final weighted score."""

    company_score: float
    title_score: float
    description_score: float
    location_score: float
    skills_score: float
    duplicate_score: float


def _ratio(a: str | None, b: str | None) -> float:
    """rapidfuzz token-set ratio in [0, 1]; treats None/empty as 0 sim."""
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a, b)) / 100.0


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard similarity over normalised skill tokens."""
    set_a = {s.strip().lower() for s in a if s and s.strip()}
    set_b = {s.strip().lower() for s in b if s and s.strip()}
    if not set_a and not set_b:
        return 0.0
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union) if union else 0.0


def score_pair(
    new_job: JobForScoring,
    existing: JobForScoring,
    *,
    weights: DedupeWeights,
) -> DuplicateScore:
    """Compute the §18.3 weighted similarity between two postings."""
    company_score = _ratio(new_job.company, existing.company)
    title_score = _ratio(new_job.title, existing.title)
    description_score = cosine(new_job.embedding, existing.embedding)
    location_score = _ratio(new_job.location, existing.location)
    skills_score = _jaccard(new_job.skills, existing.skills)

    duplicate_score = (
        weights.company * company_score
        + weights.title * title_score
        + weights.description * description_score
        + weights.location * location_score
        + weights.skills * skills_score
    )

    return DuplicateScore(
        company_score=company_score,
        title_score=title_score,
        description_score=description_score,
        location_score=location_score,
        skills_score=skills_score,
        duplicate_score=duplicate_score,
    )


def decide(
    duplicate_score: float,
    *,
    duplicate_threshold: float,
    possible_duplicate_threshold: float,
) -> str:
    """Classify a duplicate_score into 'duplicate' / 'possible_duplicate' / 'new'.

    Thresholds default to ``cfg.dedupe.duplicate_threshold`` (0.92) and
    ``cfg.dedupe.possible_duplicate_threshold`` (0.80) per design §18.4.
    """
    if duplicate_score >= duplicate_threshold:
        return "duplicate"
    if duplicate_score >= possible_duplicate_threshold:
        return "possible_duplicate"
    return "new"
