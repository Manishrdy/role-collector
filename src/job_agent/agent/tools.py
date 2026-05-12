"""Internal tool registry for the autonomous worker."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from job_agent.agent.intelligence import compact_for_observation
from job_agent.agent.schemas import ToolInput, ToolObservation
from job_agent.db import repo
from job_agent.graph import nodes
from job_agent.graph.state import AgentState


@dataclass(frozen=True)
class AgentTool:
    name: str
    source_name: str
    budget_cost: int
    input_model: type[BaseModel]
    runner: Callable[[ToolInput], ToolObservation]
    safety_policy: str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def run(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        agent_cycle_id: int | None,
        search_run_id: int | None,
    ) -> ToolObservation:
        tool = self._tools[name]
        started = time.perf_counter()
        status = "failed"
        observation: ToolObservation | None = None
        error: str | None = None
        try:
            parsed = tool.input_model.model_validate(payload)
            if not isinstance(parsed, ToolInput):
                parsed = ToolInput.model_validate(parsed.model_dump())
            observation = tool.runner(parsed)
            status = observation.status
            return observation
        except Exception as e:
            error = str(e)
            observation = ToolObservation(
                status="failed",
                source_name=tool.source_name,
                message=error,
            )
            return observation
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            repo.record_agent_tool_call(
                agent_cycle_id=agent_cycle_id,
                search_run_id=search_run_id,
                tool_name=name,
                source_name=tool.source_name,
                status=status,
                input=compact_for_observation(payload),
                output=compact_for_observation(observation.model_dump() if observation else {}),
                error_message=error,
                latency_ms=elapsed_ms,
            )


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def _stateful(
        source: str,
        fn: Callable[[AgentState], AgentState],
    ) -> Callable[[ToolInput], ToolObservation]:
        def run(inp: ToolInput) -> ToolObservation:
            state = inp.runtime.setdefault("state", {})
            if not isinstance(state, dict):
                raise TypeError("runtime.state must be a dict")
            if inp.runtime.get("skip_tool"):
                return ToolObservation(
                    status="skipped",
                    source_name=source,
                    message=str(inp.runtime.get("skip_reason") or "skipped"),
                )

            before_candidates = len(state.get("candidate_urls", []) or [])
            before_saved = len(state.get("saved_jobs", []) or [])
            typed_state = cast(AgentState, state)
            fn(typed_state)
            after_candidates = len(state.get("candidate_urls", []) or [])
            after_saved = len(state.get("saved_jobs", []) or [])
            return ToolObservation(
                status="succeeded",
                source_name=source,
                candidates=max(0, after_candidates - before_candidates),
                jobs_saved=max(0, after_saved - before_saved),
                message="ok",
            )

        return run

    for name, source, fn in (
        ("discover_ats_api_jobs", "ats_api_discovery", nodes.run_ats_api_discovery_node),
        ("search_google_ats_jobs", "ats_google_search", nodes.run_ats_google_search_node),
        ("discover_funding_events", "funding_discovery", nodes.run_funding_discovery_node),
        ("resolve_funded_companies", "funding_resolvers", nodes.run_funding_resolvers_node),
        ("poll_watchlist", "watchlist", nodes.run_watchlist_node),
        ("search_linkedin_public_posts", "linkedin_public_search", nodes.run_linkedin_public_search_node),
        ("fetch_candidate_pages", "fetch_pages", nodes.fetch_candidate_pages_node),
        ("extract_job_data", "extract_jobs", nodes.extract_job_data_node),
        ("exact_idempotency_check", "exact_idempotency", nodes.exact_idempotency_check_node),
        ("semantic_duplicate_check", "semantic_dedupe", nodes.semantic_duplicate_check_node),
        ("save_job_batch", "save_jobs", nodes.save_jobs_node),
        ("write_cycle_summary", "summary", nodes.write_summary_node),
    ):
        registry.register(
            AgentTool(
                name=name,
                source_name=source,
                budget_cost=1,
                input_model=ToolInput,
                runner=_stateful(source, fn),
                safety_policy="uses existing node-level guardrails",
            )
        )
    return registry
