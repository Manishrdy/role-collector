"""LangGraph node implementations.

Phase-1: most nodes are pass-through stubs that log + record an agent_event.
The skeleton runs end-to-end so the next phase can replace nodes one at a
time without touching the wiring.
"""

from __future__ import annotations

import logging
from typing import Any

from job_agent.config import load_config
from job_agent.db import repo
from job_agent.graph.state import AgentState

log = logging.getLogger(__name__)


def _event(state: AgentState, event_type: str, message: str) -> None:
    run_id = state.get("run_id")
    log.info("[%s] %s", event_type, message)
    if run_id is not None:
        repo.log_agent_event(search_run_id=run_id, event_type=event_type, message=message)


def load_config_node(state: AgentState) -> AgentState:
    cfg = load_config()
    state["config"] = cfg.model_dump(exclude={"langfuse_secret_key", "langfuse_public_key"})
    state.setdefault("errors", [])
    state.setdefault("search_plan", [])
    state.setdefault("candidate_urls", [])
    state.setdefault("fetched_pages", [])
    state.setdefault("extracted_jobs", [])
    state.setdefault("saved_jobs", [])
    state.setdefault("duplicate_candidates", [])
    return state


def create_search_run_node(state: AgentState) -> AgentState:
    cfg = load_config()
    run_id = repo.start_search_run(
        source_type="agent_run",
        config_snapshot={"agent": cfg.agent.model_dump(), "search": cfg.search.model_dump()},
    )
    state["run_id"] = run_id
    _event(state, "run_started", f"search_run id={run_id}")
    return state


def generate_search_plan_node(state: AgentState) -> AgentState:
    """STUB — Phase 2 builds real query templates from cfg.search and cfg.sources."""
    _event(state, "plan_generated", "[stub] no queries generated yet")
    return state


def run_ats_google_search_node(state: AgentState) -> AgentState:
    _event(state, "ats_google_search", "[stub] no candidate URLs collected yet")
    return state


def run_funding_discovery_node(state: AgentState) -> AgentState:
    cfg = load_config()
    if not cfg.sources.funding_discovery.enabled:
        _event(state, "funding_discovery", "disabled in config")
        return state
    _event(state, "funding_discovery", "[stub] enabled but not yet implemented")
    return state


def run_linkedin_public_search_node(state: AgentState) -> AgentState:
    cfg = load_config()
    if not cfg.sources.linkedin_public_search.enabled:
        _event(state, "linkedin_public_search", "disabled in config")
        return state
    _event(state, "linkedin_public_search", "[stub] enabled but not yet implemented")
    return state


def fetch_candidate_pages_node(state: AgentState) -> AgentState:
    _event(
        state,
        "fetch_pages",
        f"[stub] fetched 0 of {len(state.get('candidate_urls', []))} candidates",
    )
    return state


def extract_job_data_node(state: AgentState) -> AgentState:
    _event(
        state, "extract_jobs", f"[stub] extracted 0 of {len(state.get('fetched_pages', []))} pages"
    )
    return state


def exact_idempotency_check_node(state: AgentState) -> AgentState:
    _event(state, "idempotency_check", "[stub] no jobs to check")
    return state


def semantic_duplicate_check_node(state: AgentState) -> AgentState:
    _event(state, "dedupe_check", "[stub] no jobs to dedupe")
    return state


def save_jobs_node(state: AgentState) -> AgentState:
    _event(state, "save_jobs", f"[stub] saved 0 of {len(state.get('extracted_jobs', []))}")
    return state


def write_summary_node(state: AgentState) -> AgentState:
    summary: dict[str, Any] = {
        "candidate_urls": len(state.get("candidate_urls", [])),
        "fetched_pages": len(state.get("fetched_pages", [])),
        "extracted_jobs": len(state.get("extracted_jobs", [])),
        "saved_jobs": len(state.get("saved_jobs", [])),
        "duplicate_candidates": len(state.get("duplicate_candidates", [])),
        "errors": len(state.get("errors", [])),
    }
    state["summary"] = summary
    _event(state, "summary", str(summary))
    return state


def finish_run_node(state: AgentState) -> AgentState:
    run_id = state.get("run_id")
    if run_id is not None:
        status = "failed" if state.get("errors") else "succeeded"
        repo.finish_search_run(run_id, status=status)
        _event(state, "run_finished", f"status={status}")
    return state
