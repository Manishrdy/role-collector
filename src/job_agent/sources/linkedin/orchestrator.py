"""LinkedIn discovery orchestrator.

Pipeline: Google search -> rate-limited per-post fetch -> classify +
extract -> persist linkedin_posts -> upsert the companies row so the
Phase-5 resolver chain picks it up on its next pass.

Wrapped so a single bad post can't abort the run, and so captcha back-
off stops the LinkedIn channel without taking down the rest of the
workflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from job_agent.agent.classifiers import OllamaClassifierClient
from job_agent.config import AppConfig
from job_agent.db import repo
from job_agent.sources.ats_search import run_ats_search
from job_agent.sources.linkedin.extractor import extract_from_html
from job_agent.sources.linkedin.fetcher import fetch_linkedin_posts
from job_agent.sources.linkedin.queries import generate_linkedin_queries
from job_agent.sources.linkedin.schema import ExtractedLinkedInPost

log = logging.getLogger(__name__)


@dataclass
class LinkedInStats:
    candidate_urls: int = 0
    posts_fetched: int = 0
    posts_blocked: int = 0
    posts_errored: int = 0
    posts_classified_hiring: int = 0
    posts_inserted: int = 0
    posts_existing: int = 0
    companies_seeded: int = 0
    role_llm_calls: int = 0
    role_llm_valid: int = 0
    role_llm_fallbacks: int = 0
    stopped_early: bool = False
    stop_reason: str | None = None
    errors: list[str] = field(default_factory=list)


def _should_enrich_with_llm(post: ExtractedLinkedInPost, *, min_conf: float) -> bool:
    """Trigger the LLM only when regex output is incomplete or low-confidence."""
    if not post.detected_role:
        return True
    return post.extraction_confidence < min_conf


def discover_linkedin_posts(
    cfg: AppConfig,
    *,
    search_run_id: int | None = None,
) -> LinkedInStats:
    """Run the LinkedIn discovery channel end-to-end."""
    stats = LinkedInStats()
    if not cfg.sources.linkedin_public_search.enabled:
        return stats
    if cfg.sources.linkedin_public_search.login_allowed:
        # Defense-in-depth: never authenticate even if config is mistakenly flipped.
        log.error("[linkedin] login_allowed=True is not supported in v1 — aborting channel")
        stats.errors.append("login_allowed=True rejected")
        return stats

    # Stage 1: Google search for public LinkedIn posts.
    plans = generate_linkedin_queries(cfg)
    if not plans:
        log.info("[linkedin] no query plans generated")
        return stats

    try:
        candidates, _ = run_ats_search(
            cfg=cfg,
            plans=plans,
            max_results_per_query=cfg.sources.linkedin_public_search.max_results_per_query,
            search_run_id=search_run_id,
        )
    except Exception as e:
        log.exception("[linkedin] google search failed")
        stats.errors.append(f"google: {e}")
        return stats

    # De-dupe and constrain to actual linkedin.com URLs.
    urls: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if "linkedin.com" not in c.canonical_url:
            continue
        if c.canonical_url in seen:
            continue
        seen.add(c.canonical_url)
        urls.append(c.canonical_url)
    stats.candidate_urls = len(urls)
    if not urls:
        log.info("[linkedin] no linkedin URLs in search results")
        return stats

    # Stage 2: rate-limited fetch.
    fetched, fetch_stats = fetch_linkedin_posts(
        urls=urls,
        cfg=cfg,
        min_delay_s=cfg.sources.linkedin_public_search.min_delay_per_post_seconds,
        max_delay_s=cfg.sources.linkedin_public_search.max_delay_per_post_seconds,
    )
    stats.posts_fetched = fetch_stats.urls_ok
    stats.posts_blocked = fetch_stats.urls_blocked
    stats.posts_errored = fetch_stats.urls_errored
    stats.stopped_early = fetch_stats.stopped_early
    stats.stop_reason = fetch_stats.stop_reason

    # Stage 3: classify + extract.
    extracted: list[ExtractedLinkedInPost] = []
    for page in fetched:
        if page.status != "ok" or not page.html:
            continue
        try:
            post = extract_from_html(post_url=page.url, html=page.html)
        except Exception as e:
            log.exception("[linkedin] extractor failed for %s", page.url)
            stats.errors.append(f"extract {page.url}: {e}")
            continue
        if post is None:
            continue
        if not post.is_hiring_post:
            continue
        stats.posts_classified_hiring += 1
        extracted.append(post)

    # Stage 3b: optional LLM enrichment for role/level/family.
    llm_client: OllamaClassifierClient | None = None
    if cfg.sources.linkedin_public_search.role_llm_enabled and extracted:
        try:
            llm_client = OllamaClassifierClient(cfg)
        except Exception as e:
            log.exception("[linkedin] failed to init Ollama classifier")
            stats.errors.append(f"llm_init: {e}")
            llm_client = None
    if llm_client is not None:
        try:
            enriched: list[ExtractedLinkedInPost] = []
            min_conf = cfg.sources.linkedin_public_search.role_llm_min_confidence
            for post in extracted:
                if not _should_enrich_with_llm(post, min_conf=min_conf):
                    enriched.append(post)
                    continue
                stats.role_llm_calls += 1
                intel = llm_client.classify_linkedin_post(
                    post_text=post.post_text,
                    author_name=post.author_name,
                    company_hint=post.company_name,
                    configured_roles=list(cfg.search.roles),
                )
                if intel is None:
                    stats.role_llm_fallbacks += 1
                    enriched.append(post)
                    continue
                stats.role_llm_valid += 1
                # LLM is allowed to override role / company / level fields,
                # but the regex hiring-signal classifier and post text stay
                # authoritative — never flip is_hiring or rewrite post_text.
                updated = post.model_copy(
                    update={
                        "detected_role": intel.detected_role or post.detected_role,
                        "role_family": intel.role_family or post.role_family,
                        "role_match_status": intel.role_match_status or post.role_match_status,
                        "level": intel.level or post.level,
                        "level_confidence": intel.level_confidence,
                        "company_name": post.company_name or intel.company_hint,
                        "extraction_source": "llm",
                        "extraction_confidence": max(post.extraction_confidence, intel.confidence),
                    }
                )
                enriched.append(updated)
            extracted = enriched
        finally:
            llm_client.close()

    # Stage 4: persist + seed companies.
    for post in extracted:
        company_id: int | None = None
        if post.company_name:
            try:
                company_id = repo.upsert_company(name=post.company_name)
            except Exception as e:
                log.exception("[linkedin] upsert_company failed for %s", post.company_name)
                stats.errors.append(f"upsert_company {post.company_name}: {e}")
        try:
            r = repo.upsert_linkedin_post(
                post_url=post.post_url,
                post_text=post.post_text,
                author_name=post.author_name,
                author_url=post.author_url,
                company_name=post.company_name,
                detected_role=post.detected_role,
                role_family=post.role_family,
                role_match_status=post.role_match_status,
                level=post.level,
                level_confidence=post.level_confidence,
                extraction_source=post.extraction_source,
                confidence=post.extraction_confidence,
                source_query="linkedin_public_search",
                company_id=company_id,
            )
        except Exception as e:
            log.exception("[linkedin] persist post failed")
            stats.errors.append(f"persist {post.post_url}: {e}")
            continue
        if r.inserted:
            stats.posts_inserted += 1
        else:
            stats.posts_existing += 1

        if r.inserted and company_id is not None:
            stats.companies_seeded += 1

    log.info(
        "[linkedin] urls=%d ok=%d blocked=%d errored=%d hiring=%d inserted=%d "
        "seeded_companies=%d llm_calls=%d/%d stopped_early=%s",
        stats.candidate_urls,
        stats.posts_fetched,
        stats.posts_blocked,
        stats.posts_errored,
        stats.posts_classified_hiring,
        stats.posts_inserted,
        stats.companies_seeded,
        stats.role_llm_valid,
        stats.role_llm_calls,
        stats.stopped_early,
    )
    return stats
