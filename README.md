# Role Collector

Role Collector is an autonomous job-sourcing agent focused on finding **fresh job postings** (especially `<24h`) across ATS platforms and discovery channels, then deduplicating, storing, and surfacing them in a review dashboard.

## What We Built

We implemented a full pipeline that continuously discovers job URLs, extracts structured job data, deduplicates records, and ranks results for fast application workflows.

### Major Capabilities

- Autonomous worker loop with scheduling, source prioritization, backoff, and cycle summaries
- Multi-source discovery:
  - ATS API discovery (seed + learned slugs)
  - ATS Google search (host-targeted + broad)
  - Funding/event discovery + company resolver + watchlist polling
  - LinkedIn public post discovery
- ATS API connector expansion (pattern-port approach):
  - `ashby`, `lever`, `greenhouse` (existing)
  - `workday`, `smartrecruiters`, `icims` (added)
- Deterministic sharded seed traversal for large catalogs
- Freshness metadata across pipeline:
  - `posted_at_source`, `observed_at`, `freshness_bucket` (`lt_24h`, `24_72h`, `gt_72h`, `unknown`)
- Two-layer dedupe:
  - Exact idempotency (description hash)
  - Semantic duplicate scoring
- Premium dashboard UI with freshness-first ranking and filtering

## Recent Implementation Highlights (What + How)

### 1) ATS Discovery Strengthening

**What:** Expanded ATS API discovery coverage and quality.

**How:**
- Added Workday / SmartRecruiters / iCIMS clients under `src/job_agent/sources/ats_api/clients.py`
- Ported robust patterns from ATS-scrapers style (pagination, retries, canonical URL shaping, metadata extraction)
- Added normalization helpers in `src/job_agent/sources/ats_api/normalize.py` for employment type, remote type, timestamps, and freshness bucketing

### 2) Deterministic Seed Sharding

**What:** Prevent offset drift and inconsistent shard traversal across cycles.

**How:**
- Seed providers are now processed in deterministic sorted order before flattening/sharding
- Offset memory remains stable across worker runs

### 3) Freshness-First Pipeline + UI

**What:** Make "apply-now" jobs easier to prioritize.

**How:**
- Persisted freshness metadata in `jobs` table (`posted_at_source`, `observed_at`, `freshness_bucket`)
- Added DB migration support in `src/job_agent/db/migrate.py`
- Jobs API now sorts freshness-first
- Dashboard adds:
  - `Freshness` filter
  - `<24h only` toggle

### 4) Compatibility + Reliability

**What:** Keep watchlist and existing flows stable with richer ATS outputs.

**How:**
- Watchlist enumeration now supports both legacy `list[str]` and richer ATS metadata records
- Added full-suite test entrypoint and validation target (`make test-all`)

## High-Level Architecture

1. Worker cycle starts (`agent/worker.py`)
2. Source schedule computed (`agent/scheduler.py`)
3. Discovery tools run (`agent/tools.py` + `graph/nodes.py`)
4. Candidate URLs normalized and fetched
5. Job extraction pipeline runs (ATS parser → JSON-LD → DOM → LLM fallback)
6. Dedupe checks (exact + semantic)
7. Upsert into SQLite + source audit records
8. UI/API serves ranked review surface

## Tech Stack

### Core Runtime
- Python 3.12
- FastAPI + Jinja2 (dashboard)
- SQLite (primary storage)
- Requests + BeautifulSoup + lxml (HTTP + HTML parsing)
- Playwright / nodriver (browser-assisted paths)
- Pydantic (typed config/schemas)

### Agent / Intelligence
- LangGraph-style node workflow
- Optional Ollama-backed LLM classification/extraction

### Dedupe
- RapidFuzz (string similarity)
- Sentence-transformers embeddings (semantic scoring)

### Observability / Ops
- Langfuse integration (configurable)
- Rich run/cycle/source stats + event logging

## Repository Layout

- `src/job_agent/agent/` — worker loop, scheduler, tool registry
- `src/job_agent/graph/` — orchestration nodes and state transitions
- `src/job_agent/sources/` — discovery channels (ATS, funding, LinkedIn)
- `src/job_agent/extract/` — extraction pipeline + parsers + schema
- `src/job_agent/dedupe/` — hashing, embeddings, scoring
- `src/job_agent/db/` — schema, migrations, repository methods
- `ui/` — FastAPI server, templates, static assets
- `tests/` — unit/integration tests

## Setup

```bash
make setup
cp .env.example .env
make migrate
```

Optional (if using local Ollama flows):

```bash
make ollama
```

## Run

### Autonomous worker

```bash
make worker
```

### Manual run

```bash
make run
```

### Dashboard

```bash
make review
# http://127.0.0.1:8501
```

## Test & Quality

```bash
make test-all    # full suite with PYTHONPATH=src
make test
make lint
make format
```

## Data Model Notes

Primary entities:
- `jobs`
- `job_sources`
- `search_runs`
- `agent_cycles`
- `agent_source_stats`
- `funding_events`
- `companies`
- `linkedin_posts`

Freshness fields in `jobs`:
- `posted_at_source`
- `observed_at`
- `freshness_bucket`

## Current Status

- End-to-end discovery, extraction, dedupe, persistence, and review workflow is operational
- ATS coverage significantly expanded
- Full test suite passing
- Premium UI and freshness-prioritized application workflow in place

## Notes

- Config lives in `config.yaml`
- Environment variables documented in `.env.example`
- Database path defaults to `./data/jobs.db`
