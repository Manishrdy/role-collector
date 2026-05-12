# Phase-Wise Development Plan: Autonomous Config-Driven Job Sourcing Agent

## Summary

Refactor the project into two local microservices:

- **`job-agent-worker`**: a round-the-clock autonomous worker that reads `config.yaml`, searches all enabled job targets in cycles, extracts and normalizes jobs, dedupes, writes SQLite batches, sleeps/backoffs, then resumes.
- **`job-dashboard-api`**: a public read-only dashboard/API that lists discovered jobs from SQLite.

This is not a user-prompted chatbot agent. The runtime objective comes from config: roles, locations, ATS API seeds, Google ATS domains, funding discovery, watchlist, and LinkedIn public search.

Use **Ollama `deepseek-r1:8b`** for agent reflection/classification/normalization where useful, while keeping deterministic Python guardrails and existing source modules.

## Phase 1: Stabilize Config, Schema, And Current Baseline

- Extend config with `agent_loop`:
  - `enabled: true`
  - `cycle_sleep_hours: 4`
  - `batch_min_jobs: 5`
  - `batch_max_jobs: 10`
  - `max_cycle_runtime_minutes: 90`
  - `max_pages_per_cycle: 150`
  - `max_google_queries_per_cycle: 10`
  - `backoff_on_block_hours: 6`
  - per-source cadence for ATS API, Google, funding, watchlist, LinkedIn.
- Switch LLM default to `deepseek-r1:8b`, keeping Ollama as provider.
- Preserve current `run` command as deterministic baseline.
- Add migrations for:
  - `agent_cycles`
  - `agent_tool_calls`
  - `agent_source_stats`
  - `agent_memory`
  - `job_batches`
  - `job_sources.batch_id`
  - `jobs.location_normalized_json`
  - `jobs.role_family`
  - `jobs.role_match_status`
  - `jobs.level`
  - `jobs.level_confidence`
  - `linkedin_posts.company_id`
- Keep SQLite as the sole jobs database.

## Phase 2: Autonomous Worker Loop

- Add a new CLI command:

```bash
python -m job_agent.cli worker
```

- Worker behavior:
  - load config
  - start `agent_cycle`
  - run all enabled sources under budgets/cadence
  - collect candidates
  - fetch/extract/normalize/dedupe
  - flush jobs in batches of 5-10 or at cycle end
  - write source stats and markdown summary
  - sleep for configured hours
  - repeat forever
- Source coverage rule:
  - agent may decide priority/order
  - every enabled source must be executed, skipped with reason, or delayed due to cadence/backoff
- Sleep/backoff:
  - normal sleep: 4 hours
  - Google/LinkedIn block: source-specific 6 hour backoff
  - fatal cycle error: short retry delay, then resume next cycle

## Phase 3: Source Tool Registry

Wrap existing modules as safe internal tools:

- `discover_ats_api_jobs`
  - uses current Lever/Greenhouse/Ashby seed discovery.
- `search_google_ats_jobs`
  - long-tail fallback via nodriver.
- `discover_funding_events`
  - HN + TechCrunch + optional Google.
- `resolve_funded_companies`
  - website -> careers -> ATS.
- `poll_watchlist`
  - resolved ATS boards.
- `search_linkedin_public_posts`
  - public unauthenticated LinkedIn only.
- `fetch_candidate_pages`
- `extract_job_data`
- `dedupe_jobs`
- `save_job_batch`
- `write_cycle_summary`

Each tool must have:

- Pydantic input/output schema
- budget cost
- source name
- safety policy
- compact observation output
- `agent_tool_calls` persistence

No tool may bypass URL safety, login/apply blocking, shortener blocking, file download blocking, or per-domain limits.

## Phase 4: LLM-Assisted Agent Intelligence

Use `deepseek-r1:8b` for these bounded tasks:

- **Source prioritization**
  - choose order per cycle from enabled sources and source stats.
- **Search query expansion**
  - generate role variants from config roles.
- **Role classification**
  - save broadly, but tag each job:
    - `exact_match`
    - `adjacent`
    - `irrelevant`
    - `unknown`
- **Role family normalization**
  - examples: software, backend, full_stack, ai_ml, qa, devops, manager.
- **Level classification**
  - `intern`, `entry`, `mid`, `senior`, `staff`, `principal`, `manager`, `unknown`.
- **Location normalization**
  - raw location to JSON:
```json
{
  "raw": "Los Angeles, California, United States",
  "city": "Los Angeles",
  "region": "California",
  "country": "United States",
  "display": "Los Angeles, United States",
  "remote_type": "hybrid",
  "confidence": 0.91
}
```
- **Cycle reflection**
  - summarize what worked, what failed, and what to prioritize next.

Do not use LLM for:

- deciding URL safety
- direct SQL generation
- direct DB writes
- authenticated browsing
- auto-apply
- rate-limit bypass

All LLM outputs must be JSON-only and Pydantic-validated, with deterministic fallback on invalid output.

## Phase 5: Batch Persistence And Idempotency

- Add `job_batches` as the write unit for discovered jobs.
- Batch flush triggers:
  - 5 valid extracted jobs minimum
  - 10 jobs maximum
  - source finishes
  - cycle ends
- Preserve current idempotency:
  - ATS fingerprint first: `(ats_type, ats_job_id)`
  - canonical URL fallback
  - description hash exact duplicate
  - semantic duplicate scoring
- Link batch through `job_sources.batch_id`, not only `jobs`, because one job can be rediscovered in multiple batches.
- Store every discovered job broadly, but classify relevance/location/level for filtering.

## Phase 6: Dashboard Microservice

Keep dashboard public and read-only for now.

Update dashboard/API to show:

- title
- company
- apply/job link
- source
- first seen
- last seen
- raw location
- normalized location
- remote type
- role family
- role match status
- level
- duplicate status
- extraction confidence
- batch/cycle metadata

Add read-only pages/endpoints for:

- latest jobs
- source performance
- worker cycles
- batch history
- blocked/backoff sources

No auth, payments, user accounts, admin actions, favorites, or job hiding in this phase.

## Phase 7: Operational Memory And Summaries

Use SQLite for operational memory:

- source success rates
- blocked domains
- last searched timestamps
- duplicate-heavy queries
- strong-performing ATS seeds
- poor-performing sources
- cycle-level stats

Use Markdown for human-readable summaries:

```text
data/memory/latest.md
data/memory/runs/<cycle-id>.md
```

Do not add vector DB in v1. Revisit `sqlite-vec` later only if semantic retrieval is needed.

## Phase 8: Guardrails And Hardening

Guardrails to enforce:

- all browser/page tools pass URL safety
- no logged-in LinkedIn
- no apply/login/submit paths
- no downloads
- no shorteners
- max pages per cycle
- max pages per domain
- max Google queries before self-stop
- LinkedIn captcha/login wall stop
- source-specific backoff
- cycle timeout
- structured LLM output validation
- raw page text not logged to Langfuse
- PII redaction before traces/summaries

Failure handling:

- source failure should not fail entire cycle
- repeated invalid LLM output falls back to deterministic defaults
- blocked source records backoff and continues with other sources
- cycle always writes final status and summary

## Phase 9: Testing And Acceptance

Unit tests:

- config parses new `agent_loop` fields
- migrations create new tables/columns idempotently
- worker loop computes source eligibility/cadence correctly
- tool registry validates schemas
- LLM classifiers parse valid JSON and reject invalid JSON
- location normalization output validates
- level classification output validates
- batch flush creates batch + job source links
- source backoff works

Integration tests:

- one mocked worker cycle runs ATS API -> fetch -> extract -> dedupe -> batch save
- all enabled sources are either executed or skipped with recorded reason
- Google block causes source backoff, not full worker failure
- LinkedIn block causes source backoff, not full worker failure
- dashboard can read jobs with normalized location and level
- existing `python -m job_agent.cli run` still works

Acceptance criteria:

- worker can run multiple cycles without user input
- jobs are saved idempotently
- batch writes work at 5-10 job boundaries
- dashboard is read-only and shows discovered jobs
- DeepSeek 8B is used only through validated structured tasks
- all configured targets are covered over cycles
- SQLite remains the only jobs capture database

## Assumptions

- Default cycle sleep is 4 hours.
- SQLite remains local source of truth for jobs.
- Dashboard stays public read-only.
- Model default becomes `deepseek-r1:8b`.
- Save broadly, then classify/filter in DB/UI.
- No vector DB in v1.
- Existing deterministic pipeline remains as fallback.
- Current ATS API discovery is a primary source; Google is long-tail fallback.
