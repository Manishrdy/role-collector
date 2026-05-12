"""LangGraph workflow wiring.

Linear graph for Phase 1. Branching (skip-on-disabled-source, retry on
captcha) lands in Phase 2.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from typing import Any

from langgraph.graph import END, START, StateGraph

from job_agent.graph import nodes
from job_agent.graph.state import AgentState

# Order of node registration. Each entry is (name, fn).
_NODE_SEQUENCE: list[tuple[str, Callable[[AgentState], AgentState]]] = [
    ("load_config", nodes.load_config_node),
    ("create_search_run", nodes.create_search_run_node),
    ("generate_search_plan", nodes.generate_search_plan_node),
    ("run_ats_api_discovery", nodes.run_ats_api_discovery_node),
    ("run_ats_google_search", nodes.run_ats_google_search_node),
    ("run_funding_discovery", nodes.run_funding_discovery_node),
    ("run_funding_resolvers", nodes.run_funding_resolvers_node),
    ("run_watchlist", nodes.run_watchlist_node),
    ("run_linkedin_public_search", nodes.run_linkedin_public_search_node),
    ("fetch_candidate_pages", nodes.fetch_candidate_pages_node),
    ("extract_job_data", nodes.extract_job_data_node),
    ("exact_idempotency_check", nodes.exact_idempotency_check_node),
    ("semantic_duplicate_check", nodes.semantic_duplicate_check_node),
    ("save_jobs", nodes.save_jobs_node),
    ("write_summary", nodes.write_summary_node),
    ("finish_run", nodes.finish_run_node),
]


def build_graph() -> StateGraph[AgentState, None, Any, Any]:
    g: StateGraph[AgentState, None, Any, Any] = StateGraph(AgentState)

    for name, fn in _NODE_SEQUENCE:
        g.add_node(name, fn)  # type: ignore[call-overload]

    g.add_edge(START, _NODE_SEQUENCE[0][0])
    for (a, _), (b, _) in pairwise(_NODE_SEQUENCE):
        g.add_edge(a, b)
    g.add_edge(_NODE_SEQUENCE[-1][0], END)

    return g


def compile_app() -> Any:
    """Compile the LangGraph workflow. Returns a Pregel instance with .invoke()."""
    return build_graph().compile()
