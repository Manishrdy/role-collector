"""ReAct loop controller for autonomous tool execution."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from job_agent.agent.schemas import ReActDecision, ToolObservation
from job_agent.config import AppConfig

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an autonomous job-sourcing worker planner. "
    "Pick exactly one next tool from the available tool list. "
    "Never invent tool names. Keep reasoning concise. "
    "Return JSON only."
)

_USER_TEMPLATE = """Choose the next action for this worker cycle.

Objective:
{objective}

Phase:
{phase}

Available tools:
{available_tools}

Completed tools:
{completed_tools}

Last observation:
{last_observation}

Return a JSON object with this schema:
{{
  "action": "run_tool" | "finish",
  "thought_summary": string,
  "tool_name": string | null,
  "reason": string
}}
"""


@dataclass(frozen=True)
class ReActStep:
    step_index: int
    phase: str
    decision: ReActDecision
    observation: ToolObservation | None = None


class OllamaReActPlanner:
    """Ollama-backed chooser for next ReAct action."""

    def __init__(self, cfg: AppConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self._cfg = cfg
        self._client = httpx.Client(
            base_url=cfg.ollama_base_url,
            timeout=cfg.llm.request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def choose_next(
        self,
        *,
        objective: str,
        phase: str,
        available_tools: list[str],
        completed_tools: list[str],
        last_observation: ToolObservation | None,
    ) -> ReActDecision | None:
        prompt = _USER_TEMPLATE.format(
            objective=objective,
            phase=phase,
            available_tools=json.dumps(available_tools),
            completed_tools=json.dumps(completed_tools),
            last_observation=json.dumps(last_observation.model_dump() if last_observation else None),
        )
        payload: dict[str, Any] = {
            "model": self._cfg.llm.model,
            "prompt": prompt,
            "system": _SYSTEM_PROMPT,
            "format": "json",
            "stream": False,
            "options": {"temperature": self._cfg.llm.temperature},
        }
        try:
            resp = self._client.post("/api/generate", json=payload)
            resp.raise_for_status()
            body = resp.json()
            raw = body.get("response")
            if not isinstance(raw, str):
                return None
            return ReActDecision.model_validate_json(raw)
        except Exception as e:
            log.warning("react planner call failed: %s", e)
            return None


class ReActLoop:
    """Finite-state executor loop that chooses one tool action per step."""

    def __init__(
        self,
        *,
        cfg: AppConfig,
        source_tools: list[str],
        dry_run: bool,
        planner: OllamaReActPlanner | None = None,
    ) -> None:
        self._cfg = cfg
        self._source_pending = list(source_tools)
        self._pipeline_pending = [
            "fetch_candidate_pages",
            "extract_job_data",
            "exact_idempotency_check",
            "semantic_duplicate_check",
            "save_job_batch",
            "write_cycle_summary",
        ]
        self._completed: list[str] = []
        self._trace: list[ReActStep] = []
        self._last_observation: ToolObservation | None = None
        self._llm_enabled = cfg.agent_loop.react_llm_enabled and not dry_run
        self._planner = planner if self._llm_enabled else None
        self._objective = (
            "Run scheduled sources, then fetch/extract/dedupe/save jobs, "
            "and finally write cycle summary."
        )
        self._metrics: dict[str, int] = {
            "planner_calls": 0,
            "planner_valid": 0,
            "planner_fallbacks": 0,
            "invalid_tool_choices": 0,
            "finish_declined_count": 0,
        }

    @property
    def trace(self) -> list[dict[str, Any]]:
        return [
            {
                "step_index": s.step_index,
                "phase": s.phase,
                "decision": s.decision.model_dump(),
                "observation": s.observation.model_dump() if s.observation else None,
            }
            for s in self._trace
        ]

    def close(self) -> None:
        if self._planner is not None:
            self._planner.close()

    @property
    def metrics(self) -> dict[str, int]:
        return dict(self._metrics)

    def next_decision(self) -> ReActDecision:
        phase, available = self._phase_and_available()
        if not available:
            return ReActDecision(
                action="finish",
                thought_summary="All planned tools completed.",
                tool_name=None,
                reason="no_pending_tools",
            )

        decision: ReActDecision | None = None
        if self._planner is not None:
            self._metrics["planner_calls"] += 1
            decision = self._planner.choose_next(
                objective=self._objective,
                phase=phase,
                available_tools=available,
                completed_tools=self._completed,
                last_observation=self._last_observation,
            )
            if decision is not None:
                self._metrics["planner_valid"] += 1

        if decision is None:
            if self._planner is not None:
                self._metrics["planner_fallbacks"] += 1
            decision = ReActDecision(
                action="run_tool",
                thought_summary=f"Deterministic fallback for phase={phase}.",
                tool_name=available[0],
                reason="fallback_first_available",
            )

        if decision.action == "finish":
            # Protect deterministic completeness for the first production loop.
            if self._planner is not None:
                self._metrics["finish_declined_count"] += 1
                self._metrics["planner_fallbacks"] += 1
            decision = ReActDecision(
                action="run_tool",
                thought_summary="Finish declined while work is pending.",
                tool_name=available[0],
                reason="finish_declined_pending_work",
            )

        if decision.tool_name not in available:
            if self._planner is not None:
                self._metrics["invalid_tool_choices"] += 1
                self._metrics["planner_fallbacks"] += 1
            decision = ReActDecision(
                action="run_tool",
                thought_summary="Planner returned unknown tool; fallback to safe candidate.",
                tool_name=available[0],
                reason="invalid_tool_fallback",
            )
        assert decision.tool_name is not None
        self._mark_started(decision.tool_name)
        step = ReActStep(step_index=len(self._trace) + 1, phase=phase, decision=decision)
        self._trace.append(step)
        return decision

    def record_observation(self, tool_name: str, observation: ToolObservation) -> None:
        self._last_observation = observation
        self._completed.append(tool_name)
        if not self._trace:
            return
        last = self._trace[-1]
        self._trace[-1] = ReActStep(
            step_index=last.step_index,
            phase=last.phase,
            decision=last.decision,
            observation=observation,
        )

    def _phase_and_available(self) -> tuple[str, list[str]]:
        if self._source_pending:
            return "source_discovery", list(self._source_pending)
        return "pipeline", list(self._pipeline_pending)

    def _mark_started(self, tool_name: str) -> None:
        if tool_name in self._source_pending:
            self._source_pending.remove(tool_name)
            return
        if tool_name in self._pipeline_pending:
            self._pipeline_pending.remove(tool_name)
