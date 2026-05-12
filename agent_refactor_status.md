# Agent Refactor Status

Last updated: 2026-05-12

## Goal

Build a config-driven autonomous job sourcing system with:

- `job-agent-worker`: continuously sources, extracts, enriches, dedupes, batches, and persists jobs.
- `job-dashboard-api`: read-only visibility into jobs, runs, cycles, source health, and agent metrics.

## Current State (Completed)

### Worker and execution

- Autonomous `worker` CLI is implemented (`--once`, `--dry-run`, forever mode).
- Worker now uses a real tool execution path (not placeholder no-ops).
- Source scheduling is implemented:
  - config enable/disable
  - cadence eligibility
  - source backoff eligibility
- Source prioritization is implemented (deterministic scoring from historical source stats).
- ReAct loop is implemented with bounded steps and deterministic safety behavior:
  - optional Ollama planner (`react_llm_enabled`)
  - deterministic fallback when planner output is missing/invalid
  - invalid tool fallback and finish-decline safety

### Reliability and operations

- Singleton worker lock with stale-lock recovery.
- Graceful shutdown (SIGINT/SIGTERM).
- Cycle deadline enforcement.
- Repeated-failure sleep backoff.
- Source-specific blocked-signal backoff persistence (Google/LinkedIn).

### Data model and persistence

- Agent cycle/tool/source stats/memory tables and job-batch links are in place.
- Save path includes enrichment fields:
  - `role_family`
  - `role_match_status`
  - `level`
  - `level_confidence`
  - `location_normalized_json`

### LLM intelligence and reflection

- Added bounded Ollama JSON classifiers:
  - job intelligence classification
  - cycle reflection
- Deterministic fallback remains primary safety path.
- Config-gated rollout:
  - `agent_loop.intelligence_llm_enabled`
  - `agent_loop.cycle_reflection_llm_enabled`

### Tracing and observability

- Langfuse tracing is wired for worker cycles:
  - cycle root trace
  - schedule observation
  - per-tool spans with outputs
  - final cycle summary/status output
- ReAct canary metrics captured in summary:
  - planner calls/valid/fallbacks/invalid-tool/finish-declined
- Intelligence metrics captured in summary:
  - classifier calls/valid/fallbacks
- Runs dashboard now shows:
  - ReAct planner metrics table
  - LLM intelligence/reflection metrics table

### Quality gates

- Verified:
  - `ruff check src tests`
  - `mypy`
  - `pytest`
- Latest test count: `316 passed`.

## Pending Items

1. Verify/refine batch flush behavior with runtime-level continuity:
   - `save_jobs_node` now preserves `batch_current_count` across tool calls and flushes on `batch_max_jobs`.
   - End-of-cycle partial flush is now owned by worker finalization of the active runtime batch.
   - Added focused partial-batch tests; full execution pending local dependency parity (`rapidfuzz` missing in current shell).
2. Deeper dashboard/API drilldowns:
   - detailed ReAct trace view
   - richer tool-call and backoff diagnostics
3. Non-dry-run integration tests with mocked source permutations:
   - blocked/captcha/error/success mixes across sources.
4. Optional advanced prioritization:
   - LLM-guided source prioritization (current deterministic prioritization is already operational).
5. Optional classifier telemetry expansion:
   - per-classifier latency/error distributions in dashboard.

## Next Recommended Implementation

Complete verification + observability for batch flush refinement before more model-driven behavior.

Rationale:

- It tightens write semantics and run consistency.
- It reduces operational ambiguity in cycle-level outputs.
- It is low-risk and improves production predictability immediately.

## Next-Session Handoff Anchor

Continue from: `agent_refactor_status.md` → section **"Pending Items"** and **"Next Recommended Implementation"**.

Suggested first task next session:

1. Update `save_jobs_node` flush behavior to respect both `batch_min_jobs` and `batch_max_jobs` with clear end-of-cycle finalization semantics.
2. Add focused tests for partial-batch edge cases.
3. Run full quality gates in project environment (`ruff`, `mypy`, `pytest`) and adjust any regressions.
