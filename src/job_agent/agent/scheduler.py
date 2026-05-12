"""Source cadence/backoff scheduler for worker tool eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from job_agent.config import AppConfig
from job_agent.db import repo


@dataclass(frozen=True)
class SourceScheduleDecision:
    tool_name: str
    source_name: str
    status: str
    reason: str
    priority_score: float = 0.0
    priority_reason: str = ""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _is_source_enabled(cfg: AppConfig, source_name: str) -> bool:
    if source_name == "ats_api_discovery":
        return cfg.sources.ats_api_discovery.enabled
    if source_name == "ats_google_search":
        return cfg.sources.ats_google_search.enabled
    if source_name in {"funding_discovery", "funding_resolvers", "watchlist"}:
        if not cfg.sources.funding_discovery.enabled:
            return False
        if source_name == "funding_resolvers":
            return cfg.sources.funding_discovery.resolvers.enabled
        return True
    if source_name == "linkedin_public_search":
        return cfg.sources.linkedin_public_search.enabled
    return True


def _cadence_hours(cfg: AppConfig, source_name: str) -> float:
    if source_name == "ats_api_discovery":
        return cfg.agent_loop.cadence.ats_api_hours
    if source_name == "ats_google_search":
        return cfg.agent_loop.cadence.google_hours
    if source_name in {"funding_discovery", "funding_resolvers"}:
        return cfg.agent_loop.cadence.funding_hours
    if source_name == "watchlist":
        return cfg.agent_loop.cadence.watchlist_hours
    if source_name == "linkedin_public_search":
        return cfg.agent_loop.cadence.linkedin_hours
    return 0.0


def compute_source_schedule(cfg: AppConfig) -> list[SourceScheduleDecision]:
    stats = {row["source_name"]: row for row in repo.list_source_stats()}
    now = _utc_now()
    decisions: list[SourceScheduleDecision] = []

    source_tools = (
        ("discover_ats_api_jobs", "ats_api_discovery"),
        ("search_google_ats_jobs", "ats_google_search"),
        ("discover_funding_events", "funding_discovery"),
        ("resolve_funded_companies", "funding_resolvers"),
        ("poll_watchlist", "watchlist"),
        ("search_linkedin_public_posts", "linkedin_public_search"),
    )
    for tool_name, source_name in source_tools:
        if not _is_source_enabled(cfg, source_name):
            decisions.append(
                SourceScheduleDecision(
                    tool_name=tool_name,
                    source_name=source_name,
                    status="skipped",
                    reason="disabled_in_config",
                )
            )
            continue

        row: dict[str, Any] | None = stats.get(source_name)
        backoff_until = _parse_utc((row or {}).get("backoff_until"))
        if backoff_until is not None and backoff_until > now:
            decisions.append(
                SourceScheduleDecision(
                    tool_name=tool_name,
                    source_name=source_name,
                    status="skipped",
                    reason="source_backoff",
                )
            )
            continue

        cadence = max(0.0, _cadence_hours(cfg, source_name))
        if cadence <= 0:
            decisions.append(
                SourceScheduleDecision(
                    tool_name=tool_name,
                    source_name=source_name,
                    status="eligible",
                    reason="cadence_disabled",
                    priority_score=_priority_score(row),
                    priority_reason=_priority_reason(row),
                )
            )
            continue

        last_finished = _parse_utc((row or {}).get("last_finished_at"))
        if last_finished is None:
            decisions.append(
                SourceScheduleDecision(
                    tool_name=tool_name,
                    source_name=source_name,
                    status="eligible",
                    reason="never_run",
                    priority_score=0.15,
                    priority_reason="exploration_boost",
                )
            )
            continue

        next_at = last_finished + timedelta(hours=cadence)
        if next_at > now:
            decisions.append(
                SourceScheduleDecision(
                    tool_name=tool_name,
                    source_name=source_name,
                    status="skipped",
                    reason="cadence_not_due",
                )
            )
            continue

        decisions.append(
            SourceScheduleDecision(
                tool_name=tool_name,
                source_name=source_name,
                status="eligible",
                reason="cadence_due",
                priority_score=_priority_score(row),
                priority_reason=_priority_reason(row),
            )
        )
    eligible = [d for d in decisions if d.status == "eligible"]
    skipped = [d for d in decisions if d.status != "eligible"]
    eligible.sort(key=lambda d: d.priority_score, reverse=True)
    return [*eligible, *skipped]


def _priority_score(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.1
    runs = max(1, int(row.get("runs_total") or 0))
    successes = int(row.get("successes_total") or 0)
    failures = int(row.get("failures_total") or 0)
    candidates = int(row.get("candidates_total") or 0)
    jobs_saved = int(row.get("jobs_saved_total") or 0)

    success_rate = successes / runs
    failure_rate = failures / runs
    yield_rate = jobs_saved / max(1, candidates)
    return round((2.0 * yield_rate) + (0.8 * success_rate) - (0.7 * failure_rate), 6)


def _priority_reason(row: dict[str, Any] | None) -> str:
    if not row:
        return "no_history"
    candidates = int(row.get("candidates_total") or 0)
    jobs_saved = int(row.get("jobs_saved_total") or 0)
    failures = int(row.get("failures_total") or 0)
    runs = max(1, int(row.get("runs_total") or 0))
    if candidates > 0 and (jobs_saved / candidates) >= 0.2:
        return "high_yield"
    if failures / runs >= 0.5:
        return "failure_penalty"
    return "balanced"
