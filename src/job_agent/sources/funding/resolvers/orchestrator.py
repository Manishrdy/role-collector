"""Funding resolver orchestrator.

Walks unresolved companies from the `companies` table (those flagged by
funding_events but missing a `website_url`) and runs the three-step
resolver chain: name -> website -> careers URL -> ATS.

Persists each step independently via `repo.update_company_resolution`,
so a partial resolution (website but no careers page) is still recorded
and not retried from scratch.

The Google fallback for website resolution costs a nodriver session
($$$, slow), so it's gated: we only spin one up if at least one
unresolved company has no usable source URL.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import requests

from job_agent.db import repo
from job_agent.sources.funding.filters import (
    looks_like_personal_name,
    looks_like_vc_fund,
    resolver_retry_budget,
)
from job_agent.sources.funding.resolvers._fetch import FetchFallback
from job_agent.sources.funding.resolvers.ats import resolve_ats
from job_agent.sources.funding.resolvers.careers import resolve_careers_url
from job_agent.sources.funding.resolvers.website import (
    resolve_website_cheap,
    resolve_website_via_google,
)

log = logging.getLogger(__name__)


@dataclass
class ResolverStats:
    companies_checked: int = 0
    websites_resolved: int = 0
    careers_resolved: int = 0
    ats_resolved: int = 0
    skipped_vc_funds: int = 0
    skipped_personal_names: int = 0
    errors: list[str] = field(default_factory=list)
    per_company: list[dict[str, str | None]] = field(default_factory=list)


def resolve_unresolved_companies(
    *,
    limit: int = 20,
    enable_google_fallback: bool = False,
    enable_playwright_fallback: bool = True,
    db_path: str | None = None,
) -> ResolverStats:
    """Run the resolver chain over pending companies. Returns stats.

    `enable_google_fallback` gates the nodriver-based website lookup. It's
    off by default because spinning up nodriver for one or two companies
    isn't worth the overhead — flip it on for batch backfills.

    `enable_playwright_fallback` gates the per-URL Playwright escalation
    used when plain ``requests`` is blocked / served a JS shell. Worth
    keeping on; the Playwright browser is only launched on first need.
    """
    stats = ResolverStats()
    pending = repo.list_unresolved_funding_companies(limit=limit, db_path=db_path)
    if not pending:
        log.info("[resolvers] no companies to resolve")
        return stats

    session = requests.Session()

    # `with` block owns the Playwright lifecycle for the whole batch.
    # Lazy-init means a run that never escalates pays zero overhead.
    fb: FetchFallback | None = FetchFallback() if enable_playwright_fallback else None
    try:
        # Identify which companies the cheap path can resolve and which need Google.
        needs_google: list[repo.CompanyResolutionRow] = []
        for row in pending:
            stats.companies_checked += 1
            per: dict[str, str | None] = {"name": row.name}
            is_vc = looks_like_vc_fund(row.name)
            is_personal = looks_like_personal_name(row.name)
            if is_vc:
                stats.skipped_vc_funds += 1
                per["skip_reason"] = "vc_fund"
            if is_personal:
                stats.skipped_personal_names += 1
                per["skip_reason"] = per.get("skip_reason") or "personal_name"

            website = resolve_website_cheap(row.latest_funding_source_url)
            if website is None and not (is_vc or is_personal):
                # Worth a Google query if we haven't already given up.
                needs_google.append(row)
                stats.per_company.append(per)
                continue
            if website is None and (is_vc or is_personal):
                # Persist a sentinel so re-runs don't re-check the same
                # known-unresolvable rows. Plain repo update — the resolver
                # chain has nothing to write here.
                reason = per.get("skip_reason") or "skipped"
                try:
                    repo.update_company_resolution(
                        company_id=row.company_id,
                        notes=f"skipped: {reason}",
                        db_path=db_path,
                    )
                except Exception as e:
                    log.exception("[resolvers] sentinel persist failed for %s", row.name)
                    stats.errors.append(f"{row.name}: sentinel: {e}")
                stats.per_company.append(per)
                continue
            per["website"] = website
            _persist_chain(
                row, website=website, stats=stats, per=per, session=session, fallback=fb
            )
            stats.per_company.append(per)

        if needs_google and enable_google_fallback:
            log.info("[resolvers] running google fallback for %d companies", len(needs_google))
            asyncio.run(
                _resolve_via_google_async(
                    pending=needs_google,
                    stats=stats,
                    session=session,
                    db_path=db_path,
                    fallback=fb,
                )
            )
    finally:
        if fb is not None:
            fb.close()

    log.info(
        "[resolvers] checked=%d websites=%d careers=%d ats=%d vc=%d personal=%d errors=%d",
        stats.companies_checked,
        stats.websites_resolved,
        stats.careers_resolved,
        stats.ats_resolved,
        stats.skipped_vc_funds,
        stats.skipped_personal_names,
        len(stats.errors),
    )
    return stats


def _persist_chain(
    row: repo.CompanyResolutionRow,
    *,
    website: str | None,
    stats: ResolverStats,
    per: dict[str, str | None],
    session: requests.Session,
    db_path: str | None = None,
    fallback: FetchFallback | None = None,
) -> None:
    """Given a resolved website, run careers + ATS and persist."""
    if not website:
        return
    stats.websites_resolved += 1
    budget = resolver_retry_budget(row.name)
    try:
        careers = resolve_careers_url(
            website, retry_budget=budget, session=session, fallback=fallback
        )
    except Exception as e:
        log.exception("[resolvers] careers step failed for %s", row.name)
        stats.errors.append(f"{row.name}: careers: {e}")
        careers = None
    per["careers"] = careers

    ats_type: str | None = None
    ats_url: str | None = None
    if careers:
        stats.careers_resolved += 1
        try:
            ats_type, ats_url = resolve_ats(careers, session=session, fallback=fallback)
        except Exception as e:
            log.exception("[resolvers] ats step failed for %s", row.name)
            stats.errors.append(f"{row.name}: ats: {e}")
    if ats_type:
        stats.ats_resolved += 1
    per["ats_type"] = ats_type
    per["ats_url"] = ats_url

    try:
        repo.update_company_resolution(
            company_id=row.company_id,
            website_url=website,
            careers_url=careers,
            ats_type=ats_type,
            ats_url=ats_url,
            db_path=db_path,
        )
    except Exception as e:
        log.exception("[resolvers] persist failed for %s", row.name)
        stats.errors.append(f"{row.name}: persist: {e}")


async def _resolve_via_google_async(
    *,
    pending: list[repo.CompanyResolutionRow],
    stats: ResolverStats,
    session: requests.Session,
    db_path: str | None = None,
    fallback: FetchFallback | None = None,
) -> None:
    """Spin up nodriver once, then query for each unresolved company."""
    import nodriver as uc

    browser = await uc.start(headless=False)
    try:
        for row in pending:
            try:
                website = await resolve_website_via_google(row.name, browser=browser)
            except Exception as e:
                log.exception("[resolvers] google fallback failed for %s", row.name)
                stats.errors.append(f"{row.name}: website (google): {e}")
                continue
            per: dict[str, str | None] = {"name": row.name, "website": website}
            _persist_chain(
                row,
                website=website,
                stats=stats,
                per=per,
                session=session,
                db_path=db_path,
                fallback=fallback,
            )
            stats.per_company.append(per)
    finally:
        try:
            browser.stop()
        except Exception as e:
            log.debug("nodriver shutdown noise: %s", e)
