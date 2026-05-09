# Job Sourcing Agent

Local-first single-agent job sourcing system. Finds fresh, hidden software-engineering jobs from ATS pages, recently funded companies, and public LinkedIn posts — all running on a MacBook with Ollama, Playwright, SQLite, and LangGraph.

Full design: [`design_plan.md`](design_plan.md).

## Status — Phase 1 (skeleton)

The workflow runs end-to-end with stub source modules. No jobs land in SQLite yet — that starts in Phase 2 (Google ATS search).

What works now:

- Config loader (`config.yaml` + `.env`)
- SQLite schema & migrator (all 9 tables, indexes from the design doc)
- Langfuse tracing wrapper with PII redaction
- URL safety layer (allowlist, login/apply blocks, shortener block, canonicalisation)
- Dedicated Chromium profile launcher
- LangGraph workflow with 13 stub nodes
- Streamlit review dashboard (placeholder)
- `make` targets for setup/run/test/lint

## Setup

Requirements: macOS, Python 3.11+, [Ollama](https://ollama.com) installed, a free [Langfuse Cloud](https://langfuse.com) account.

```bash
make setup                           # venv + deps + playwright chromium
cp .env.example .env                 # fill in LANGFUSE_* keys
make ollama                          # ollama pull qwen3:8b  (~5 GB)
make migrate                         # creates data/jobs.db
make run                             # executes the workflow (stubs only in Phase 1)
```

Other commands:

```bash
make test          # pytest
make lint          # ruff + mypy
make format        # ruff format + autofix
make review        # streamlit ui
.venv/bin/python -m job_agent.cli doctor   # readiness check
```

## Layout

```
src/job_agent/
  config.py             # pydantic-validated config loader
  db/
    schema.sql          # all tables + indexes
    migrate.py          # idempotent migrator
    repo.py             # typed read/write helpers
  tracing/
    redact.py           # PII redaction
    langfuse_client.py  # tracing wrapper (no-op fallback)
  browser/
    safety.py           # URL allowlist + canonicalisation
    profile.py          # dedicated Chromium profile
  tools/
    permissions.py      # @guarded_url_arg
    search.py           # search_google / search_bing  (stubs)
    page.py             # open_allowed_url / extract_*  (stubs)
  graph/
    state.py            # AgentState
    nodes.py            # 13 node implementations (stubs)
    workflow.py         # LangGraph wiring
  cli.py                # `python -m job_agent.cli`
ui/streamlit_app.py     # review dashboard
tests/                  # pytest suite
config.yaml             # user-editable
.env.example            # secrets template
```

## What's next

Phase 2 — real Google ATS search via Playwright (Bing fallback when Google blocks). The stubs in `tools/search.py` and the `run_ats_google_search_node` are the integration points.

## Guardrails

The hard-coded URL safety layer is the security boundary, not prompt rules. Every browser-touching tool is decorated with `@guarded_url_arg`, which canonicalises the URL and rejects anything not in the allowlist, any login/apply path, any shortener, and any blocked file extension. See [`design_plan.md` §14](design_plan.md).
