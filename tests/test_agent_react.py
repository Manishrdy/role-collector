from __future__ import annotations

import json
from typing import Any

import httpx

from job_agent.agent.react import OllamaReActPlanner, ReActLoop
from job_agent.agent.schemas import ReActDecision, ToolObservation
from job_agent.config import load_config


class _FakePlanner:
    def choose_next(
        self,
        *,
        objective: str,
        phase: str,
        available_tools: list[str],
        completed_tools: list[str],
        last_observation: ToolObservation | None,
    ) -> ReActDecision:
        del objective, phase, available_tools, completed_tools, last_observation
        return ReActDecision(
            action="run_tool",
            thought_summary="invalid tool on purpose",
            tool_name="not-a-real-tool",
            reason="test",
        )

    def close(self) -> None:
        return None


class _FinishPlanner:
    def choose_next(
        self,
        *,
        objective: str,
        phase: str,
        available_tools: list[str],
        completed_tools: list[str],
        last_observation: ToolObservation | None,
    ) -> ReActDecision:
        del objective, phase, available_tools, completed_tools, last_observation
        return ReActDecision(
            action="finish",
            thought_summary="done",
            tool_name=None,
            reason="test_finish",
        )

    def close(self) -> None:
        return None


def test_react_loop_deterministic_tool_progression() -> None:
    cfg = load_config()
    loop = ReActLoop(cfg=cfg, source_tools=["discover_ats_api_jobs"], dry_run=True)
    steps: list[str] = []
    while True:
        decision = loop.next_decision()
        if decision.action == "finish":
            break
        assert decision.tool_name is not None
        steps.append(decision.tool_name)
        loop.record_observation(
            decision.tool_name,
            ToolObservation(status="succeeded", source_name=decision.tool_name),
        )
    assert steps == [
        "discover_ats_api_jobs",
        "fetch_candidate_pages",
        "extract_job_data",
        "exact_idempotency_check",
        "semantic_duplicate_check",
        "save_job_batch",
        "write_cycle_summary",
    ]
    assert loop.metrics["planner_calls"] == 0
    assert loop.metrics["planner_fallbacks"] == 0


def test_react_loop_falls_back_when_planner_returns_invalid_tool() -> None:
    cfg = load_config()
    cfg = cfg.model_copy(update={"agent_loop": cfg.agent_loop.model_copy(update={"react_llm_enabled": True})})
    loop = ReActLoop(
        cfg=cfg,
        source_tools=["search_google_ats_jobs"],
        dry_run=False,
        planner=_FakePlanner(),  # type: ignore[arg-type]
    )
    decision = loop.next_decision()
    assert decision.tool_name == "search_google_ats_jobs"
    assert decision.reason == "invalid_tool_fallback"
    assert loop.metrics["planner_calls"] == 1
    assert loop.metrics["planner_valid"] == 1
    assert loop.metrics["planner_fallbacks"] == 1
    assert loop.metrics["invalid_tool_choices"] == 1


def test_react_loop_counts_finish_declined_with_pending_work() -> None:
    cfg = load_config()
    cfg = cfg.model_copy(update={"agent_loop": cfg.agent_loop.model_copy(update={"react_llm_enabled": True})})
    loop = ReActLoop(
        cfg=cfg,
        source_tools=["discover_ats_api_jobs"],
        dry_run=False,
        planner=_FinishPlanner(),  # type: ignore[arg-type]
    )
    decision = loop.next_decision()
    assert decision.action == "run_tool"
    assert decision.reason == "finish_declined_pending_work"
    assert loop.metrics["finish_declined_count"] == 1
    assert loop.metrics["planner_fallbacks"] == 1


def test_ollama_react_planner_parses_json_action() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "response": json.dumps(
                    {
                        "action": "run_tool",
                        "thought_summary": "Run source first",
                        "tool_name": "discover_ats_api_jobs",
                        "reason": "high yield",
                    }
                )
            },
        )

    cfg = load_config()
    planner = OllamaReActPlanner(cfg, transport=httpx.MockTransport(handler))
    try:
        decision = planner.choose_next(
            objective="test",
            phase="source_discovery",
            available_tools=["discover_ats_api_jobs"],
            completed_tools=[],
            last_observation=None,
        )
    finally:
        planner.close()
    assert decision is not None
    assert decision.tool_name == "discover_ats_api_jobs"
    body: dict[str, Any] = json.loads(captured[0].content)
    assert body["format"] == "json"
    assert body["stream"] is False
