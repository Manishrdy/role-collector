"""Config loader. Reads config.yaml + .env and produces a typed AppConfig."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class AgentSection(BaseModel):
    name: str = "local_job_sourcing_agent"
    mode: Literal["mvp", "beta", "prod"] = "mvp"
    dry_run: bool = False


class LLMSection(BaseModel):
    provider: Literal["ollama"] = "ollama"
    model: str = "deepseek-r1:8b"
    temperature: float = 0.0
    max_context_chars_per_page: int = 12000
    request_timeout_seconds: float = 90.0
    # Below this confidence, extracted jobs are saved with needs_review=1.
    low_confidence_threshold: float = 0.5


class SearchSection(BaseModel):
    time_windows: list[str] = Field(default_factory=lambda: ["past_24h", "past_48h"])
    roles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    max_queries_per_run: int = 20
    max_results_per_query: int = 10


class ATSGoogleSearchSection(BaseModel):
    enabled: bool = True
    domains: list[str] = Field(default_factory=list)
    # Self-impose a cap so we voluntarily stop before Google rate-limits us.
    # 0 disables the cap. Google's threshold for `site:` queries is roughly
    # ~10-15 per session; 10 stays well under it.
    max_queries_before_self_stop: int = 10


class ATSAPIDiscoverySection(BaseModel):
    """Seed-slug ATS API discovery — runs BEFORE Google search.

    Hits each provider's public JSON board API for every slug in `seeds`.
    Faster, more reliable, and not rate-limited by Google. Slugs are
    curated up-front; they grow over time as the resolver/watchlist
    chain learns new ones.
    """
    enabled: bool = True
    max_jobs_per_slug: int = 20
    # Seed slugs to enumerate, keyed by provider.
    seeds: dict[str, list[str]] = Field(default_factory=dict)


class FundingAggregatorToggles(BaseModel):
    hackernews: bool = True
    techcrunch: bool = True
    google: bool = False  # off by default — costs a nodriver session


class FundingResolversSection(BaseModel):
    enabled: bool = True
    google_fallback: bool = False  # off by default — costs a nodriver session
    # Per-URL Playwright escalation when plain requests gets 403'd or served a
    # JS shell. Pays zero overhead unless a URL actually needs it.
    playwright_fallback: bool = True
    max_companies_per_run: int = 20


class FundingWatchlistSection(BaseModel):
    # Caps jobs enumerated per company board so one big board can't flood
    # the candidate queue. Set to 0 to disable.
    max_jobs_per_company: int = 20
    # Skip re-polling a company whose last_polled_at is within this window.
    # 0 disables the filter (always re-poll).
    repoll_after_hours: float = 6.0


class FundingDiscoverySection(BaseModel):
    enabled: bool = False
    aggregators: FundingAggregatorToggles = Field(default_factory=FundingAggregatorToggles)
    resolvers: FundingResolversSection = Field(default_factory=FundingResolversSection)
    watchlist: FundingWatchlistSection = Field(default_factory=FundingWatchlistSection)


class LinkedInPublicSearchSection(BaseModel):
    enabled: bool = False
    login_allowed: bool = False
    # Rate-limit knobs for fetching individual LinkedIn post pages.
    # Defaults are conservative; tune downward at your own risk.
    min_delay_per_post_seconds: float = 30.0
    max_delay_per_post_seconds: float = 60.0
    max_results_per_query: int = 5
    stop_on_captcha: bool = True


class SourcesSection(BaseModel):
    ats_google_search: ATSGoogleSearchSection = Field(default_factory=ATSGoogleSearchSection)
    ats_api_discovery: ATSAPIDiscoverySection = Field(default_factory=ATSAPIDiscoverySection)
    funding_discovery: FundingDiscoverySection = Field(default_factory=FundingDiscoverySection)
    linkedin_public_search: LinkedInPublicSearchSection = Field(
        default_factory=LinkedInPublicSearchSection
    )


class BrowserSection(BaseModel):
    headless: bool = False
    dedicated_profile_path: str = "./browser_profiles/job_agent"
    downloads_allowed: bool = False
    min_delay_seconds: int = 5
    max_delay_seconds: int = 20


class LimitsSection(BaseModel):
    max_pages_per_domain_per_run: int = 5
    max_total_pages_per_run: int = 100
    stop_on_captcha: bool = True
    stop_on_login_page: bool = True
    min_delay_between_searches_seconds: int = 20
    max_delay_between_searches_seconds: int = 60


class DedupeWeights(BaseModel):
    company: float = 0.30
    title: float = 0.25
    description: float = 0.30
    location: float = 0.10
    skills: float = 0.05


class DedupeSection(BaseModel):
    duplicate_threshold: float = 0.92
    possible_duplicate_threshold: float = 0.80
    weights: DedupeWeights = Field(default_factory=DedupeWeights)


class StorageSection(BaseModel):
    sqlite_path: str = "./data/jobs.db"


class TracingSection(BaseModel):
    langfuse_enabled: bool = True
    redact_pii: bool = True
    log_raw_page_text: bool = False
    log_resume_text: bool = False


class AgentLoopCadenceSection(BaseModel):
    ats_api_hours: float = 4.0
    google_hours: float = 4.0
    funding_hours: float = 8.0
    watchlist_hours: float = 4.0
    linkedin_hours: float = 8.0


class AgentLoopSection(BaseModel):
    enabled: bool = True
    react_enabled: bool = True
    react_llm_enabled: bool = False
    react_llm_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    react_max_steps: int = 24
    intelligence_llm_enabled: bool = False
    cycle_reflection_llm_enabled: bool = False
    cycle_sleep_hours: float = 4.0
    batch_min_jobs: int = 5
    batch_max_jobs: int = 10
    max_cycle_runtime_minutes: int = 90
    max_pages_per_cycle: int = 150
    max_google_queries_per_cycle: int = 10
    backoff_on_block_hours: float = 6.0
    fatal_retry_sleep_minutes: float = 15.0
    memory_markdown_enabled: bool = True
    cadence: AgentLoopCadenceSection = Field(default_factory=AgentLoopCadenceSection)


class AppConfig(BaseModel):
    agent: AgentSection = Field(default_factory=AgentSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    search: SearchSection = Field(default_factory=SearchSection)
    sources: SourcesSection = Field(default_factory=SourcesSection)
    browser: BrowserSection = Field(default_factory=BrowserSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    dedupe: DedupeSection = Field(default_factory=DedupeSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    tracing: TracingSection = Field(default_factory=TracingSection)
    agent_loop: AgentLoopSection = Field(default_factory=AgentLoopSection)
    allowlist_domains: list[str] = Field(default_factory=list)

    # Resolved from environment, not config.yaml
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    ollama_base_url: str = "http://localhost:11434"


def _config_path() -> Path:
    return Path(os.environ.get("JOB_AGENT_CONFIG_PATH", "./config.yaml")).resolve()


def _load_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("r") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config.yaml must be a mapping, got {type(data).__name__}")
    return data


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    """Load config.yaml + env. Cached — call reset_config_cache() to reload in tests."""
    load_dotenv()
    raw = _load_yaml(_config_path())

    db_override = os.environ.get("JOB_AGENT_DB_PATH")
    if db_override:
        raw.setdefault("storage", {})["sqlite_path"] = db_override  # type: ignore[index]

    cfg = AppConfig.model_validate(raw)
    cfg.langfuse_public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    cfg.langfuse_secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    cfg.langfuse_host = os.environ.get("LANGFUSE_HOST")
    cfg.ollama_base_url = os.environ.get("OLLAMA_BASE_URL", cfg.ollama_base_url)
    return cfg


def reset_config_cache() -> None:
    load_config.cache_clear()
