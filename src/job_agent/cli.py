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
def run() -> None:
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
        metadata={"mode": cfg.agent.mode, "model": cfg.llm.model},
        tags=["env:dev", f"model:{cfg.llm.model}"],
    )
    console.print(f"[cyan]langfuse trace[/cyan] enabled={tracer.enabled} trace_id={trace.trace_id}")

    workflow = compile_app()
    initial: AgentState = {}
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
    console.print(f"[green]run finished[/green] (run_id={final.get('run_id')})")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("[red]interrupted[/red]")
        sys.exit(130)


if __name__ == "__main__":
    main()
