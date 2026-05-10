"""LangGraph node implementations.

Phase-1: pass-through stubs.
Phase-2: the ATS Google search node now actually drives the browser.
Phase-3: fetch / extract / save are real implementations driving Playwright,
the deterministic parser chain, and the Ollama LLM fallback.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from job_agent.browser.page_fetch import FetchedPage, fetch_pages
from job_agent.browser.safety import normalize_candidate_url
from job_agent.config import load_config
from job_agent.db import repo
from job_agent.dedupe import embeddings as embedding_mod
from job_agent.dedupe.hashing import description_hash as compute_description_hash
from job_agent.dedupe.scoring import JobForScoring, decide, score_pair
from job_agent.extract.llm import OllamaExtractor
from job_agent.extract.pipeline import extract_job
from job_agent.extract.schema import ExtractedJob
from job_agent.graph.state import AgentState
from job_agent.sources import queries as q_mod
from job_agent.sources.ats_search import run_ats_search

log = logging.getLogger(__name__)


def _event(state: AgentState, event_type: str, message: str) -> None:
    run_id = state.get("run_id")
    log.info("[%s] %s", event_type, message)
    if run_id is not None:
        repo.log_agent_event(search_run_id=run_id, event_type=event_type, message=message)


def load_config_node(state: AgentState) -> AgentState:
    cfg = load_config()
    state["config"] = cfg.model_dump(exclude={"langfuse_secret_key", "langfuse_public_key"})
    state.setdefault("errors", [])
    state.setdefault("search_plan", [])
    state.setdefault("candidate_urls", [])
    state.setdefault("fetched_pages", [])
    state.setdefault("extracted_jobs", [])
    state.setdefault("saved_jobs", [])
    state.setdefault("duplicate_candidates", [])
    return state


def create_search_run_node(state: AgentState) -> AgentState:
    cfg = load_config()
    run_id = repo.start_search_run(
        source_type="agent_run",
        config_snapshot={"agent": cfg.agent.model_dump(), "search": cfg.search.model_dump()},
    )
    state["run_id"] = run_id
    _event(state, "run_started", f"search_run id={run_id}")
    return state


def _build_search_plans(
    cfg: Any, max_queries: int | None
) -> list[q_mod.PlannedQuery]:
    """Combine host-targeted ATS queries with broad-coverage templates.

    Reserves ~1/4 of the budget for broad queries (min 1 slot when the cap
    is at least 2) so they never get starved by the much-larger ATS
    cartesian. Broad templates surface URLs from ATS hosts we haven't
    named (Cornerstone, SAP, Personio, etc.) and exercise the JSON-LD /
    DOM / LLM fallback chain in production.
    """
    if max_queries is None:
        return [
            *q_mod.generate_ats_queries(cfg),
            *q_mod.generate_broad_queries(cfg),
        ]

    if max_queries <= 1:
        # Tiny budget: don't bother reserving for broad — ATS is more reliable.
        return q_mod.generate_ats_queries(cfg, max_queries=max_queries)

    broad_cap = max(1, max_queries // 4)
    ats_cap = max_queries - broad_cap
    ats_plans = q_mod.generate_ats_queries(cfg, max_queries=ats_cap)
    # If the configured ATS cartesian was smaller than our share, hand the
    # leftover slots back to broad so the cap is fully utilised.
    actual_broad_cap = max_queries - len(ats_plans)
    broad_plans = q_mod.generate_broad_queries(cfg, max_queries=actual_broad_cap)
    return [*ats_plans, *broad_plans]


def generate_search_plan_node(state: AgentState) -> AgentState:
    """Build the deterministic search plan (host-targeted ATS + broad queries)."""
    cfg = load_config()
    runtime = state.get("runtime", {}) or {}
    max_queries: int | None = runtime.get("max_queries_override")
    plans = _build_search_plans(cfg, max_queries)
    state["search_plan"] = [
        {
            "query": p.query,
            "time_window": p.time_window,
            "source_type": p.source_type,
            "target_domain": p.target_domain,
            "ats_type": p.ats_type,
            "role": p.role,
            "location": p.location,
        }
        for p in plans
    ]
    n_broad = sum(1 for p in plans if p.source_type == "ats_google_search_broad")
    _event(
        state,
        "plan_generated",
        f"{len(plans)} queries planned ({len(plans) - n_broad} ATS / {n_broad} broad)",
    )
    return state


def run_ats_google_search_node(state: AgentState) -> AgentState:
    cfg = load_config()
    runtime = state.get("runtime", {}) or {}

    if runtime.get("dry_run"):
        _event(state, "ats_google_search", "dry_run: skipping browser")
        return state
    if not cfg.sources.ats_google_search.enabled:
        _event(state, "ats_google_search", "disabled in config")
        return state

    plans = _build_search_plans(cfg, runtime.get("max_queries_override"))
    if not plans:
        _event(state, "ats_google_search", "no queries to run")
        return state

    max_results = runtime.get("max_results_override") or cfg.search.max_results_per_query
    run_id = state.get("run_id")

    debug_dir: Path | None = None
    if runtime.get("debug_dump"):
        debug_dir = Path("data/debug") / f"run-{run_id or 'unknown'}"
        debug_dir.mkdir(parents=True, exist_ok=True)

    _event(
        state,
        "ats_google_search",
        f"running {len(plans)} queries with max_results={max_results}"
        + (f" (debug dump -> {debug_dir})" if debug_dir else ""),
    )

    candidates, stats = run_ats_search(
        cfg=cfg,
        plans=plans,
        max_results_per_query=max_results,
        search_run_id=run_id,
        debug_dump_dir=debug_dir,
    )

    state["candidate_urls"] = [asdict(c) for c in candidates]
    _event(
        state,
        "ats_google_search_done",
        (
            f"{len(candidates)} unique URLs / "
            f"{stats.queries_succeeded} ok / "
            f"{stats.queries_blocked} blocked / "
            f"google_blocked_at={stats.google_blocked_at}"
        ),
    )
    return state


def run_funding_discovery_node(state: AgentState) -> AgentState:
    cfg = load_config()
    if not cfg.sources.funding_discovery.enabled:
        _event(state, "funding_discovery", "disabled in config")
        return state
    _event(state, "funding_discovery", "[stub] enabled but not yet implemented")
    return state


def run_linkedin_public_search_node(state: AgentState) -> AgentState:
    cfg = load_config()
    if not cfg.sources.linkedin_public_search.enabled:
        _event(state, "linkedin_public_search", "disabled in config")
        return state
    _event(state, "linkedin_public_search", "[stub] enabled but not yet implemented")
    return state


def _fetched_to_state(p: FetchedPage) -> dict[str, Any]:
    """Project a FetchedPage into the JSON-friendly shape we keep in AgentState."""
    return {
        "url": p.url,
        "canonical_url": p.canonical_url,
        "final_url": p.final_url,
        "domain": p.domain,
        "page_title": p.page_title,
        "http_status": p.http_status,
        "html": p.html,
        "content_hash": p.content_hash,
        "fetched_at": p.fetched_at,
        "status": p.status,
        "error": p.error,
        "blocked_reason": p.blocked_reason,
    }


def fetch_candidate_pages_node(state: AgentState) -> AgentState:
    cfg = load_config()
    runtime = state.get("runtime", {}) or {}
    candidates = state.get("candidate_urls", []) or []

    if not candidates:
        _event(state, "fetch_pages", "no candidate URLs to fetch")
        state["fetched_pages"] = []
        return state

    if runtime.get("dry_run"):
        _event(state, "fetch_pages", f"dry_run: skipping {len(candidates)} fetches")
        state["fetched_pages"] = []
        return state

    # Some Phase-2 candidates land on ATS apply-flow URLs
    # (e.g. /<co>/<uuid>/application?utm_*=...) which the safety layer
    # would reject. Rewrite them to the canonical detail URL first, then
    # collapse any duplicates that result from the normalization.
    normalized_count = 0
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for c in candidates:
        normalized = normalize_candidate_url(c["url"])
        if normalized != c["url"]:
            normalized_count += 1
            c["url"] = normalized
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(c)
    if len(deduped) != len(candidates):
        _event(
            state,
            "fetch_pages",
            f"collapsed {len(candidates) - len(deduped)} duplicate(s) after URL normalization",
        )
    state["candidate_urls"] = deduped
    candidates = deduped

    urls = [c["url"] for c in candidates]
    _event(
        state,
        "fetch_pages",
        f"fetching {len(urls)} candidates ({normalized_count} normalized)",
    )

    pages = fetch_pages(urls, cfg=cfg, concurrency=4)
    run_id = state.get("run_id")
    ok = blocked = errored = 0
    for p in pages:
        if p.status == "ok":
            ok += 1
        elif p.status == "blocked":
            blocked += 1
        else:
            errored += 1
        repo.record_page_fetch(
            url=p.url,
            canonical_url=p.canonical_url,
            domain=p.domain,
            status=p.status,
            http_status=p.http_status,
            content_hash=p.content_hash,
            detected_page_type="job_detail",
            blocked_reason=p.blocked_reason or p.error,
            search_run_id=run_id,
        )

    state["fetched_pages"] = [_fetched_to_state(p) for p in pages]
    _event(
        state,
        "fetch_pages_done",
        f"{ok} ok / {blocked} blocked / {errored} errored",
    )
    return state


def _build_llm_extractor(cfg: Any) -> OllamaExtractor | None:
    """Create an OllamaExtractor unless the LLM provider is disabled."""
    if cfg.llm.provider != "ollama":
        return None
    return OllamaExtractor(cfg)


def extract_job_data_node(state: AgentState) -> AgentState:
    cfg = load_config()
    pages = state.get("fetched_pages", []) or []
    candidates_by_url = {c["url"]: c for c in (state.get("candidate_urls", []) or [])}

    if not pages:
        _event(state, "extract_jobs", "no fetched pages to extract")
        state["extracted_jobs"] = []
        return state

    threshold = cfg.llm.low_confidence_threshold
    extracted: list[dict[str, Any]] = []
    counts = {"ats_parser": 0, "jsonld": 0, "dom": 0, "llm": 0, "missed": 0}

    llm = _build_llm_extractor(cfg)
    try:
        for page in pages:
            if page["status"] != "ok" or not page.get("html"):
                counts["missed"] += 1
                continue
            try:
                job: ExtractedJob | None = extract_job(
                    html=page["html"], url=page["url"], llm=llm
                )
            except Exception as e:
                log.warning("extract crashed for %s: %s", page["url"], e)
                counts["missed"] += 1
                continue
            if job is None:
                counts["missed"] += 1
                continue
            counts[job.extraction_source] = counts.get(job.extraction_source, 0) + 1
            cand = candidates_by_url.get(page["url"], {})
            extracted.append(
                {
                    "url": page["url"],
                    "canonical_url": page["canonical_url"],
                    "domain": page["domain"],
                    "source_type": cand.get("source_type", "ats_google_search"),
                    "source_query": cand.get("source_query"),
                    "needs_review": job.extraction_confidence < threshold,
                    "job": job.model_dump(),
                }
            )
    finally:
        if llm is not None:
            llm.close()

    state["extracted_jobs"] = extracted
    _event(
        state,
        "extract_jobs_done",
        (
            f"{len(extracted)} extracted "
            f"(ats={counts['ats_parser']} jsonld={counts['jsonld']} "
            f"dom={counts['dom']} llm={counts['llm']} missed={counts['missed']})"
        ),
    )
    return state


def exact_idempotency_check_node(state: AgentState) -> AgentState:
    """Layer 1 dedup (design §18.1).

    Computes a stable description hash for each extracted job and flags
    any whose hash matches an already-saved row in the database. Flagged
    records are short-circuited at save time — no new ``jobs`` row gets
    inserted; the existing row's ``last_seen_at`` is bumped and a
    ``job_sources`` audit row is appended.

    Records that don't collide pass through unchanged (with the hash
    attached so the save layer persists it for future runs).
    """
    extracted = state.get("extracted_jobs", []) or []
    if not extracted:
        _event(state, "idempotency_check", "no extracted jobs to check")
        return state

    exact_hits = 0
    for record in extracted:
        job_dict = record.get("job") or {}
        description = job_dict.get("description") or job_dict.get("description_summary")
        d_hash = compute_description_hash(description)
        record["description_hash"] = d_hash

        if d_hash is None:
            continue
        existing_id = repo.find_job_by_description_hash(d_hash)
        if existing_id is not None:
            record["exact_dup_of_job_id"] = existing_id
            exact_hits += 1

    _event(
        state,
        "idempotency_check",
        f"{exact_hits} exact-duplicate(s) of {len(extracted)} extracted",
    )
    return state


def _build_scoring_view(record: dict[str, Any], embedding: list[float] | None) -> JobForScoring:
    job_dict = record.get("job") or {}
    description = job_dict.get("description") or job_dict.get("description_summary") or ""
    skills_raw = job_dict.get("skills") or []
    skills: list[str] = [s for s in skills_raw if isinstance(s, str)]
    return JobForScoring(
        company=job_dict.get("company_name") or "",
        title=job_dict.get("title") or "",
        description=description,
        location=job_dict.get("location"),
        skills=skills,
        embedding=embedding,
    )


def _candidate_to_scoring_view(candidate: dict[str, Any]) -> JobForScoring:
    skills_raw: list[str] = []
    if candidate.get("skills_json"):
        try:
            parsed = json.loads(candidate["skills_json"])
            if isinstance(parsed, list):
                skills_raw = [s for s in parsed if isinstance(s, str)]
        except json.JSONDecodeError:
            pass

    embedding: list[float] | None = None
    if candidate.get("description_embedding_json"):
        try:
            parsed_emb = json.loads(candidate["description_embedding_json"])
            if isinstance(parsed_emb, list):
                embedding = [float(x) for x in parsed_emb]
        except (json.JSONDecodeError, TypeError, ValueError):
            embedding = None

    return JobForScoring(
        company=candidate.get("company_name") or "",
        title=candidate.get("title") or "",
        description=candidate.get("description") or "",
        location=candidate.get("location"),
        skills=skills_raw,
        embedding=embedding,
    )


def semantic_duplicate_check_node(state: AgentState) -> AgentState:
    """Layer 2 dedup (design §18.3-§18.6).

    For each non-exact-dup extracted record, runs a rapidfuzz pre-filter
    via ``repo.find_dedup_candidates`` (limited to the past 90 days),
    computes per-pair weighted scores using sentence-transformers
    embeddings + rapidfuzz + Jaccard, and tags the record with
    ``duplicate_status``, ``duplicate_of_job_id``, and ``duplicate_score``
    for the save layer to persist.
    """
    cfg = load_config()
    extracted = state.get("extracted_jobs", []) or []
    if not extracted:
        _event(state, "dedupe_check", "no extracted jobs to dedupe")
        return state

    eligible = [r for r in extracted if r.get("exact_dup_of_job_id") is None]
    if not eligible:
        _event(state, "dedupe_check", "all records exact-dup'd; nothing to compare")
        return state

    # Collect descriptions to embed in a single batch — much cheaper than
    # one model call per record. We also need embeddings for the existing
    # candidates that don't already have one stored.
    new_texts: list[str] = []
    for record in eligible:
        job_dict = record.get("job") or {}
        text = (job_dict.get("description") or job_dict.get("description_summary") or "").strip()
        new_texts.append(text)

    new_embeddings: list[list[float] | None] = []
    nonempty_idx = [i for i, t in enumerate(new_texts) if t]
    if nonempty_idx:
        encoded = embedding_mod.encode_texts([new_texts[i] for i in nonempty_idx])
        encoded_iter = iter(encoded)
        for i in range(len(new_texts)):
            new_embeddings.append(next(encoded_iter) if i in set(nonempty_idx) else None)
    else:
        new_embeddings = [None] * len(new_texts)

    weights = cfg.dedupe.weights
    duplicate_threshold = cfg.dedupe.duplicate_threshold
    possible_threshold = cfg.dedupe.possible_duplicate_threshold

    counts = {"new": 0, "possible_duplicate": 0, "duplicate": 0}

    for record, embedding in zip(eligible, new_embeddings, strict=True):
        record["embedding"] = embedding
        record.setdefault("scored_candidates", [])

        job_dict = record.get("job") or {}
        norm_company = (job_dict.get("company_name") or "").strip().lower()
        norm_title = (job_dict.get("title") or "").strip().lower()
        d_hash = record.get("description_hash")

        candidates = repo.find_dedup_candidates(
            normalized_company_name=norm_company,
            normalized_title=norm_title,
            description_hash=d_hash,
            days_back=90,
        )

        # Filter out the candidate that IS this job (just discovered under
        # the same canonical URL or sharing its ATS fingerprint). Without
        # this, a job re-discovered from a previous run perfectly matches
        # its own existing row and gets marked as a duplicate of itself.
        new_canonical = record.get("canonical_url")
        new_ats_type = job_dict.get("ats_type")
        new_ats_job_id = job_dict.get("ats_job_id")
        candidates = [
            c
            for c in candidates
            if c.get("canonical_url") != new_canonical
            and not (
                new_ats_type
                and new_ats_job_id
                and c.get("ats_type") == new_ats_type
                and c.get("ats_job_id") == new_ats_job_id
            )
        ]

        new_view = _build_scoring_view(record, embedding)
        best_score = 0.0
        best_candidate_id: int | None = None

        # Encode candidate descriptions that don't have a stored embedding
        # so we still get a description score for legacy rows.
        missing_emb_idx: list[int] = [
            i
            for i, c in enumerate(candidates)
            if not c.get("description_embedding_json") and (c.get("description") or "").strip()
        ]
        if missing_emb_idx:
            backfill_texts = [candidates[i]["description"] or "" for i in missing_emb_idx]
            backfill_emb = embedding_mod.encode_texts(backfill_texts)
            for slot, vec in zip(missing_emb_idx, backfill_emb, strict=True):
                candidates[slot]["description_embedding_json"] = json.dumps(vec)

        for candidate in candidates:
            cand_view = _candidate_to_scoring_view(candidate)
            score = score_pair(new_view, cand_view, weights=weights)
            decision = decide(
                score.duplicate_score,
                duplicate_threshold=duplicate_threshold,
                possible_duplicate_threshold=possible_threshold,
            )
            record["scored_candidates"].append(
                {
                    "candidate_job_id": candidate["id"],
                    "duplicate_score": score.duplicate_score,
                    "company_score": score.company_score,
                    "title_score": score.title_score,
                    "description_score": score.description_score,
                    "location_score": score.location_score,
                    "skills_score": score.skills_score,
                    "decision": decision,
                }
            )
            if score.duplicate_score > best_score:
                best_score = score.duplicate_score
                best_candidate_id = candidate["id"]

        final_decision = decide(
            best_score,
            duplicate_threshold=duplicate_threshold,
            possible_duplicate_threshold=possible_threshold,
        )
        record["duplicate_status"] = final_decision
        record["duplicate_score"] = best_score
        record["duplicate_of_job_id"] = best_candidate_id if final_decision != "new" else None
        counts[final_decision] += 1

    _event(
        state,
        "dedupe_check_done",
        (
            f"new={counts['new']} possible={counts['possible_duplicate']} "
            f"duplicate={counts['duplicate']}"
        ),
    )
    return state


def save_jobs_node(state: AgentState) -> AgentState:
    extracted = state.get("extracted_jobs", []) or []
    if not extracted:
        _event(state, "save_jobs", "no extracted jobs to save")
        state["saved_jobs"] = []
        return state

    run_id = state.get("run_id")
    saved_ids: list[int] = []
    inserted = updated = needs_review = 0
    exact_dups = possible_dups = hard_dups = 0
    duplicate_candidate_rows = 0

    for record in extracted:
        # Phase-4 layer 1: exact-duplicate short-circuit. Don't insert a
        # new jobs row — bump the existing row's last_seen_at and append a
        # job_sources audit entry.
        exact_dup_id = record.get("exact_dup_of_job_id")
        if exact_dup_id is not None:
            try:
                repo.touch_existing_job(
                    job_id=exact_dup_id,
                    raw_url=record["url"],
                    canonical_url=record["canonical_url"],
                    source_type=record["source_type"],
                    source_query=record.get("source_query"),
                    search_run_id=run_id,
                )
            except Exception as e:
                log.warning(
                    "touch_existing_job failed for %s: %s", record.get("url"), e
                )
                continue
            saved_ids.append(int(exact_dup_id))
            exact_dups += 1
            continue

        try:
            job = ExtractedJob.model_validate(record["job"])
        except Exception as e:
            log.warning("invalid extracted job for %s: %s", record.get("url"), e)
            continue
        try:
            result = repo.upsert_job(
                extracted=job,
                canonical_url=record["canonical_url"],
                raw_url=record["url"],
                source_type=record["source_type"],
                source_query=record.get("source_query"),
                search_run_id=run_id,
                needs_review=bool(record.get("needs_review")),
                description_hash=record.get("description_hash"),
                description_embedding=record.get("embedding"),
                duplicate_status=record.get("duplicate_status", "new"),
                duplicate_of_job_id=record.get("duplicate_of_job_id"),
                duplicate_score=record.get("duplicate_score"),
            )
        except Exception as e:
            log.warning("upsert failed for %s: %s", record.get("url"), e)
            continue

        saved_ids.append(result.job_id)
        if result.inserted:
            inserted += 1
        else:
            updated += 1
        if record.get("needs_review"):
            needs_review += 1
        if record.get("duplicate_status") == "duplicate":
            hard_dups += 1
        elif record.get("duplicate_status") == "possible_duplicate":
            possible_dups += 1

        # Persist per-pair scores for any candidate that scored above a
        # noise floor. Keeps the duplicate_candidates table small while
        # capturing enough audit data for the review dashboard.
        for cand in record.get("scored_candidates", []) or []:
            if cand["duplicate_score"] < 0.5:
                continue
            try:
                repo.persist_duplicate_candidate(
                    job_id=result.job_id,
                    candidate_job_id=cand["candidate_job_id"],
                    duplicate_score=cand["duplicate_score"],
                    company_score=cand["company_score"],
                    title_score=cand["title_score"],
                    description_score=cand["description_score"],
                    location_score=cand["location_score"],
                    skills_score=cand["skills_score"],
                    decision=cand["decision"],
                )
                duplicate_candidate_rows += 1
            except Exception as e:
                log.warning("persist_duplicate_candidate failed: %s", e)

    state["saved_jobs"] = saved_ids
    _event(
        state,
        "save_jobs_done",
        (
            f"{inserted} inserted / {updated} updated / "
            f"{exact_dups} exact-dup'd / "
            f"{hard_dups} duplicate / {possible_dups} possible / "
            f"{duplicate_candidate_rows} candidate-rows / "
            f"{needs_review} flagged for review"
        ),
    )
    return state


def write_summary_node(state: AgentState) -> AgentState:
    summary: dict[str, Any] = {
        "candidate_urls": len(state.get("candidate_urls", [])),
        "fetched_pages": len(state.get("fetched_pages", [])),
        "extracted_jobs": len(state.get("extracted_jobs", [])),
        "saved_jobs": len(state.get("saved_jobs", [])),
        "duplicate_candidates": len(state.get("duplicate_candidates", [])),
        "errors": len(state.get("errors", [])),
    }
    state["summary"] = summary
    _event(state, "summary", str(summary))
    return state


def finish_run_node(state: AgentState) -> AgentState:
    run_id = state.get("run_id")
    if run_id is not None:
        status = "failed" if state.get("errors") else "succeeded"
        repo.finish_search_run(run_id, status=status)
        _event(state, "run_finished", f"status={status}")
    return state
