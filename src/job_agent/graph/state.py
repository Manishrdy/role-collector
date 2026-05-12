"""LangGraph state object. See design_plan.md §23.1."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: int
    config: dict[str, Any]
    # Per-invocation overrides not stored in config.yaml — set by the CLI
    # for dry-run, smoke-test budgets, etc.
    runtime: dict[str, Any]
    search_plan: list[dict[str, Any]]
    candidate_urls: list[dict[str, Any]]
    fetched_pages: list[dict[str, Any]]
    extracted_jobs: list[dict[str, Any]]
    saved_jobs: list[int]
    job_batches: list[dict[str, Any]]
    duplicate_candidates: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    summary: dict[str, Any]
