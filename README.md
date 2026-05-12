# Job Sourcing Agent

Local-first single-agent job sourcing system. Finds fresh, hidden software-engineering jobs from ATS pages, recently funded companies, and public LinkedIn posts — all running on a MacBook with Ollama, Playwright, SQLite, and LangGraph.

Full design: [`design_plan.md`](design_plan.md).

## Status — Phase 2 complete (live Google search via nodriver)

Phase 2 is done end-to-end. Live runs return real ATS job URLs from Google's past-24h index — Ramp, Linear, Astronomer, Mend.io, NextPatient, Oxio, Beacon AI, etc. all surfaced from a single 3-query smoke test.

**Why nodriver:** every Playwright/Puppeteer/Selenium variant we tried (Chromium, real Chrome, real Brave, with `playwright-stealth` v2, `tf-playwright-stealth`, and JS `playwright-extra` + `puppeteer-extra-plugin-stealth`) was blocked by Google with the same 6.8 KB `/sorry/index` captcha page. The bot detection works at the Chrome DevTools Protocol layer, not at the JS-fingerprint layer, so no stealth library can patch around it. **`nodriver` connects to Chrome's WebSocket debug endpoint with a custom protocol implementation that omits the artifacts CDP-based libs leak**, and it cleanly bypasses the block. See [`scripts/smoke_google_nodriver.py`](scripts/smoke_google_nodriver.py).

What works now:

- Config loader (`config.yaml` + `.env`)
- SQLite schema & migrator (all 9 tables, indexes from the design doc)
- Langfuse v4 tracing wrapper with PII redaction
- URL safety layer (allowlist, login/apply blocks, shortener block, canonicalisation)
- ATS query generator (deterministic, cartesian over role × ATS domain × time-window × location)
- **nodriver-based Google search** with URL-param time filters and block detection
- Single-session orchestrator: one Chrome instance for the whole run, per-query rate limiting, cross-query dedup, /sorry/index detection that stops the run early
- LangGraph workflow with 13 nodes (ats_google_search now real, others still stubs)
- CLI flags: `--dry-run`, `--max-queries`, `--max-results`, `--debug-dump`
- FastAPI review dashboard (vanilla HTML/CSS/JS, no framework)
- `make` targets for setup/run/test/lint
- 55 passing tests

## Setup

Requirements: macOS, Python 3.11+, **real Google Chrome installed** (nodriver requires it), [Ollama](https://ollama.com), a free [Langfuse Cloud](https://langfuse.com) account.

```bash
make setup                           # venv + deps + playwright chromium
cp .env.example .env                 # fill in LANGFUSE_* keys
make ollama                          # ollama pull qwen3:8b  (~5 GB)
make migrate                         # creates data/jobs.db
make run -- --max-queries 3 --max-results 5   # tiny live smoke
```

Other commands:

```bash
make test          # pytest
make lint          # ruff + mypy
make format        # ruff format + autofix
make review        # FastAPI dashboard on http://127.0.0.1:8501
.venv/bin/python -m job_agent.cli doctor   # readiness check
```

## Layout

```
src/job_agent/
  config.py                  # pydantic-validated config loader
  db/
    schema.sql               # all tables + indexes
    migrate.py               # idempotent migrator
    repo.py                  # typed read/write helpers
  tracing/
    redact.py                # PII redaction
    langfuse_client.py       # tracing wrapper (no-op fallback)
  browser/
    safety.py                # URL allowlist + canonicalisation
    profile.py               # Playwright Chromium profile (Phase-3 ATS fetches)
    search_engines.py        # nodriver Google driver + parser
  tools/
    permissions.py           # @guarded_url_arg
    search.py / page.py      # tool stubs (Phase 3+)
  graph/
    state.py                 # AgentState
    nodes.py                 # 13 node implementations
    workflow.py              # LangGraph wiring
  sources/
    queries.py               # PlannedQuery generator
    ats_search.py            # nodriver orchestrator (sync wrapper, async core)
  cli.py                     # python -m job_agent.cli
ui/
  server.py                  # FastAPI app + REST endpoints
  templates/*.html           # Jinja2 server-rendered pages
  static/{style.css,app.js}  # vanilla CSS + JS, no framework
scripts/
  smoke_ashby.py             # PoC: opens an Ashby company page directly
  smoke_google_nodriver.py   # PoC: confirms nodriver bypasses Google's block
tests/                       # pytest suite
config.yaml                  # user-editable
.env.example                 # secrets template
```

## What's next

**Phase 3 — Job extraction.** For each candidate URL, fetch the detail page (Playwright is fine for ATS hosts — Ashby, Greenhouse, and Lever don't have bot detection per the [`smoke_ashby.py`](scripts/smoke_ashby.py) PoC) and parse out title / company / location / apply URL / description / skills via deterministic JSON-LD parsing first, LLM fallback when not present. Persist to the `jobs` table.

## Guardrails

The hard-coded URL safety layer is the security boundary, not prompt rules. Every browser-touching tool is decorated with `@guarded_url_arg`, which canonicalises the URL and rejects anything not in the allowlist, any login/apply path, any shortener, and any blocked file extension. See [`design_plan.md` §14](design_plan.md).
