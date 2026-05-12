"""CLI entry point.  python -m job_agent.cli run"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path

# Import langchain_core BEFORE adding our warning filter — its module-level
# `surface_langchain_deprecation_warnings()` installs a "default" filter for
# LangChainPendingDeprecationWarning that would otherwise override ours.
# Once that's done, our LIFO filter wins and silences a noisy upstream
# `allowed_objects` warning emitted later by langgraph.
import langchain_core  # noqa: F401

warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
    category=PendingDeprecationWarning,
)

import typer  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from job_agent.config import load_config  # noqa: E402
from job_agent.db.migrate import migrate  # noqa: E402
from job_agent.graph.state import AgentState  # noqa: E402
from job_agent.graph.workflow import compile_app  # noqa: E402
from job_agent.tracing.langfuse_client import get_tracer  # noqa: E402

app = typer.Typer(add_completion=False, help="Local job sourcing agent.")
console = Console()


def _configure_logging() -> None:
    level_name = os.environ.get("JOB_AGENT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )


@app.command()
def migrate_db() -> None:
    """Apply SQLite schema (idempotent)."""
    _configure_logging()
    path = migrate()
    console.print(f"[green]schema applied[/green] at {path}")


@app.command()
def doctor() -> None:
    """Print resolved config + readiness checks."""
    _configure_logging()
    cfg = load_config()
    table = Table(title="job-sourcing-agent doctor", show_header=True, header_style="bold")
    table.add_column("check")
    table.add_column("value")
    table.add_row("config.agent.mode", cfg.agent.mode)
    table.add_row("config.llm.model", cfg.llm.model)
    table.add_row("config.storage.sqlite_path", cfg.storage.sqlite_path)
    table.add_row("ollama base url", cfg.ollama_base_url)
    table.add_row("langfuse host", cfg.langfuse_host or "[unset]")
    table.add_row(
        "langfuse keys",
        "present" if (cfg.langfuse_public_key and cfg.langfuse_secret_key) else "missing",
    )
    table.add_row("ats search enabled", str(cfg.sources.ats_google_search.enabled))
    table.add_row("funding discovery enabled", str(cfg.sources.funding_discovery.enabled))
    table.add_row("linkedin search enabled", str(cfg.sources.linkedin_public_search.enabled))
    table.add_row("allowlist domains", str(len(cfg.allowlist_domains)))
    table.add_row("db file exists", str(Path(cfg.storage.sqlite_path).exists()))
    console.print(table)


@app.command()
def run(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Generate the search plan and skip the browser. Useful for previewing queries.",
    ),
    max_queries: int | None = typer.Option(
        None,
        "--max-queries",
        help="Override cfg.search.max_queries_per_run for this run only.",
    ),
    max_results: int | None = typer.Option(
        None,
        "--max-results",
        help="Override cfg.search.max_results_per_query for this run only.",
    ),
    debug_dump: bool = typer.Option(
        False,
        "--debug-dump",
        help="Save raw HTML/screenshot of every search to data/debug/run-<id>/.",
    ),
) -> None:
    """Execute the LangGraph workflow end-to-end."""
    _configure_logging()
    cfg = load_config()

    db_path = Path(cfg.storage.sqlite_path)
    if not db_path.exists():
        console.print("[yellow]db not found, running migrate...[/yellow]")
        migrate()

    tracer = get_tracer(cfg)
    trace = tracer.trace(
        name="job_sourcing_run",
        metadata={
            "mode": cfg.agent.mode,
            "model": cfg.llm.model,
            "dry_run": dry_run,
            "max_queries_override": max_queries,
            "max_results_override": max_results,
        },
        tags=["env:dev", f"model:{cfg.llm.model}"] + (["dry_run"] if dry_run else []),
    )
    console.print(f"[cyan]langfuse trace[/cyan] enabled={tracer.enabled} trace_id={trace.trace_id}")
    if dry_run:
        console.print("[yellow]--dry-run: browser will NOT be launched[/yellow]")
    if max_queries is not None or max_results is not None:
        console.print(
            f"[yellow]overrides:[/yellow] max_queries={max_queries} max_results={max_results}"
        )

    workflow = compile_app()
    initial: AgentState = {
        "runtime": {
            "dry_run": dry_run,
            "max_queries_override": max_queries,
            "max_results_override": max_results,
            "debug_dump": debug_dump,
        }
    }
    final: AgentState = {}
    try:
        final = workflow.invoke(initial)
        trace.update(output={"summary": final.get("summary")})
    except Exception as e:
        trace.update(level="ERROR", status_message=str(e))
        raise
    finally:
        trace.end()
        tracer.flush()

    summary = final.get("summary", {})
    table = Table(title="run summary", show_header=True, header_style="bold")
    table.add_column("metric")
    table.add_column("count", justify="right")
    for key, val in sorted(summary.items()):
        table.add_row(key, str(val))
    console.print(table)

    plan = final.get("search_plan", [])
    if plan:
        console.print(f"[cyan]search plan:[/cyan] {len(plan)} queries")
        if dry_run:
            preview = Table(title="planned queries (first 10)", show_header=True)
            preview.add_column("#", justify="right")
            preview.add_column("engine target")
            preview.add_column("time")
            preview.add_column("query")
            for i, p in enumerate(plan[:10], 1):
                preview.add_row(str(i), p["target_domain"], p["time_window"], p["query"])
            console.print(preview)

    candidates = final.get("candidate_urls", [])
    if candidates:
        console.print(f"[cyan]candidate urls:[/cyan] {len(candidates)} unique")
        cand_table = Table(title="candidate URLs (first 10)", show_header=True)
        cand_table.add_column("engine")
        cand_table.add_column("ats")
        cand_table.add_column("rank", justify="right")
        cand_table.add_column("title", overflow="fold")
        cand_table.add_column("url", overflow="fold")
        for c in candidates[:10]:
            cand_table.add_row(
                c["engine"], c["ats_type"], str(c["rank"]), c["title"][:80], c["url"]
            )
        console.print(cand_table)

    console.print(f"[green]run finished[/green] (run_id={final.get('run_id')})")


@app.command()
def resolve_companies(
    limit: int = typer.Option(20, "--limit", help="Max companies to resolve per run."),
    google_fallback: bool = typer.Option(
        False,
        "--google-fallback",
        help="Spin up nodriver to look up company websites by name when the cheap path fails.",
    ),
) -> None:
    """Run the funding-resolver pipeline manually (name -> website -> careers -> ATS).

    Useful for backfilling resolutions over a batch of funding_events without
    triggering a full agent run.
    """
    _configure_logging()
    cfg = load_config()
    if not cfg.sources.funding_discovery.enabled:
        console.print("[red]funding_discovery.enabled is false in config[/red]")
        sys.exit(1)
    from job_agent.sources.funding.resolvers.orchestrator import (
        resolve_unresolved_companies,
    )

    stats = resolve_unresolved_companies(
        limit=limit,
        enable_google_fallback=google_fallback,
        enable_playwright_fallback=cfg.sources.funding_discovery.resolvers.playwright_fallback,
    )
    table = Table(title="resolver stats", show_header=True, header_style="bold")
    table.add_column("metric")
    table.add_column("count", justify="right")
    table.add_row("companies checked", str(stats.companies_checked))
    table.add_row("websites resolved", str(stats.websites_resolved))
    table.add_row("careers pages found", str(stats.careers_resolved))
    table.add_row("ATS detected", str(stats.ats_resolved))
    table.add_row("VC funds skipped", str(stats.skipped_vc_funds))
    table.add_row("personal names skipped", str(stats.skipped_personal_names))
    table.add_row("errors", str(len(stats.errors)))
    console.print(table)
    for err in stats.errors[:10]:
        console.print(f"  [red]err[/red] {err}")


@app.command()
def list_watchlist() -> None:
    """Show every company with a resolved ATS URL — these are the watchlist."""
    _configure_logging()
    from job_agent.db import repo

    rows = repo.list_watchlist_companies()
    if not rows:
        console.print("[yellow]no companies resolved yet[/yellow]")
        return
    table = Table(title=f"watchlist ({len(rows)} companies)", show_header=True)
    table.add_column("id", justify="right")
    table.add_column("name")
    table.add_column("ats")
    table.add_column("ats url", overflow="fold")
    table.add_column("last checked")
    for r in rows:
        table.add_row(
            str(r.company_id),
            r.name,
            r.ats_type or "—",
            r.ats_url or "—",
            r.last_checked_at or "—",
        )
    console.print(table)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("[red]interrupted[/red]")
        sys.exit(130)


if __name__ == "__main__":
    main()
