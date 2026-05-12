"""Bounded job normalization/classification helpers.

DeepSeek/Ollama is optional here: deterministic heuristics always produce
valid metadata, and a future planner can replace the internals without
changing the save path.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from job_agent.agent.classifiers import OllamaClassifierClient
from job_agent.agent.schemas import JobIntelligence, Level, LocationNormalization, RoleMatchStatus
from job_agent.config import AppConfig
from job_agent.extract.schema import ExtractedJob

_ROLE_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai_ml", ("machine learning", "ml engineer", " ai ", "artificial intelligence", "llm")),
    ("backend", ("backend", "back-end", "server", "platform", "infrastructure")),
    ("full_stack", ("full stack", "full-stack")),
    ("devops", ("devops", "sre", "site reliability", "cloud engineer")),
    ("qa", ("qa", "quality", "test engineer", "automation engineer")),
    ("manager", ("engineering manager", "manager", "director", "head of engineering")),
    ("software", ("software engineer", "sde", "developer", "engineer")),
)

_LEVEL_HINTS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("principal", ("principal", "distinguished"), 0.9),
    ("staff", ("staff", "lead engineer"), 0.85),
    ("senior", ("senior", "sr.", "sr ", "5+ years", "6+ years", "7+ years"), 0.8),
    ("manager", ("manager", "director", "head of"), 0.85),
    ("entry", ("entry level", "new grad", "graduate", "0-2 years", "1+ years"), 0.75),
    ("intern", ("intern", "internship"), 0.9),
    ("mid", ("mid-level", "2+ years", "3+ years", "4+ years"), 0.65),
)

_COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "united states": "United States",
    "canada": "Canada",
    "india": "India",
    "israel": "Israel",
}


def classify_job(
    job: ExtractedJob,
    cfg: AppConfig,
    *,
    classifier: OllamaClassifierClient | None = None,
) -> JobIntelligence:
    """Return schema-valid enrichment for saving/filtering jobs."""
    result, _ = classify_job_with_source(job, cfg, classifier=classifier)
    return result


def classify_job_with_source(
    job: ExtractedJob,
    cfg: AppConfig,
    *,
    classifier: OllamaClassifierClient | None = None,
) -> tuple[JobIntelligence, str]:
    """Return enrichment and source tag: ``llm`` or ``deterministic``."""
    if classifier is not None:
        llm_result = classifier.classify_job(job=job, configured_roles=cfg.search.roles)
        if llm_result is not None:
            return llm_result, "llm"
    return classify_job_deterministic(job, cfg), "deterministic"


def classify_job_deterministic(job: ExtractedJob, cfg: AppConfig) -> JobIntelligence:
    """Deterministic fallback used when LLM classification is disabled/fails."""
    text = " ".join(
        part
        for part in (
            job.title,
            job.company_name,
            job.location or "",
            job.description_summary or "",
            (job.description or "")[:4000],
        )
        if part
    )
    lowered = f" {text.lower()} "
    role_family = _role_family(lowered)
    return JobIntelligence(
        role_family=role_family,
        role_match_status=_role_match_status(job.title, cfg.search.roles, role_family),
        level=_level(lowered)[0],
        level_confidence=_level(lowered)[1],
        location=normalize_location(job.location, remote_type=job.remote_type),
    )


def _role_family(lowered_text: str) -> str:
    for family, hints in _ROLE_FAMILIES:
        if any(h in lowered_text for h in hints):
            return family
    if "engineer" in lowered_text:
        return "software"
    return "unknown"


def _role_match_status(title: str, configured_roles: list[str], family: str) -> RoleMatchStatus:
    title_lc = title.lower()
    if any(role.lower() in title_lc for role in configured_roles):
        return "exact_match"
    if family != "unknown":
        return "adjacent"
    return "unknown"


def _level(lowered_text: str) -> tuple[Level, float]:
    for level, hints, confidence in _LEVEL_HINTS:
        if any(h in lowered_text for h in hints):
            return cast("Level", level), confidence
    years = re.search(r"\b([0-9]{1,2})\+?\s+years?\b", lowered_text)
    if years:
        n = int(years.group(1))
        if n <= 1:
            return "entry", 0.65
        if n <= 4:
            return "mid", 0.65
        if n <= 7:
            return "senior", 0.7
        return "staff", 0.65
    return "unknown", 0.0


def normalize_location(raw: str | None, *, remote_type: str | None = None) -> LocationNormalization:
    if not raw:
        return LocationNormalization(raw=raw, remote_type=remote_type, confidence=0.0)
    cleaned = " ".join(raw.replace("|", ",").split())
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    city: str | None = None
    region: str | None = None
    country: str | None = None
    if parts:
        city = parts[0]
    if len(parts) >= 2:
        maybe_country = _COUNTRY_ALIASES.get(parts[-1].lower(), parts[-1])
        country = maybe_country
        if len(parts) >= 3:
            region = parts[-2]
    if country is None:
        lowered = cleaned.lower()
        for alias, canonical in _COUNTRY_ALIASES.items():
            if alias in lowered:
                country = canonical
                break
    if city and city.lower() in {"remote", "hybrid", "worldwide"}:
        city = None
    display = ", ".join(p for p in (city, country) if p) or cleaned
    inferred_remote = remote_type
    if inferred_remote is None:
        lowered = cleaned.lower()
        if "remote" in lowered:
            inferred_remote = "remote"
        elif "hybrid" in lowered:
            inferred_remote = "hybrid"
    return LocationNormalization(
        raw=raw,
        city=city,
        region=region,
        country=country,
        display=display,
        remote_type=inferred_remote,
        confidence=0.75 if country or city else 0.4,
    )


def intelligence_json(intel: JobIntelligence) -> str:
    return json.dumps(intel.location.model_dump())


def compact_for_observation(value: Any, *, max_chars: int = 1000) -> Any:
    raw = json.dumps(value, default=str)
    if len(raw) <= max_chars:
        return value
    return {"truncated": True, "preview": raw[:max_chars]}
