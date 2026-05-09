"""Extraction orchestrator: design §19.1 priority chain.

Order: ATS-specific parser → JSON-LD ``JobPosting`` → DOM heuristics →
LLM fallback. The first parser to return a non-None ``ExtractedJob``
wins; the LLM is only consulted when all deterministic strategies miss.

Tests inject a ``LLMExtractor`` callable so the pipeline can be exercised
without Ollama running.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from job_agent.extract.parsers import dom, greenhouse, jsonld, lever
from job_agent.extract.parsers._common import detect_ats_type
from job_agent.extract.schema import ExtractedJob

log = logging.getLogger(__name__)

ParserFn = Callable[[str, str], ExtractedJob | None]


class LLMExtractor(Protocol):
    def extract(self, *, html: str, url: str) -> ExtractedJob | None: ...


# Greenhouse and Lever still serve server-rendered DOM with stable hooks.
# Ashby moved to JSON-LD-only payloads, so its dedicated parser was retired
# and Ashby URLs now resolve via the JSON-LD branch with URL-derived
# ats_type / ats_job_id augmentation in parsers/jsonld.py.
_ATS_PARSERS: dict[str, ParserFn] = {
    "greenhouse": greenhouse.parse,
    "lever": lever.parse,
}


def extract_job(
    *,
    html: str,
    url: str,
    llm: LLMExtractor | None = None,
) -> ExtractedJob | None:
    """Walk the priority chain. Returns the first parser that succeeds."""
    if not html:
        return None

    ats = detect_ats_type(url)
    if ats and ats in _ATS_PARSERS:
        result = _safe(_ATS_PARSERS[ats], html, url, parser=ats)
        if result is not None:
            return result

    result = _safe(jsonld.parse, html, url, parser="jsonld")
    if result is not None:
        return result

    result = _safe(dom.parse, html, url, parser="dom")
    if result is not None and result.extraction_confidence >= 0.5:
        return result

    # LLM is the last resort — slowest and least reliable. Only when
    # configured AND deterministic parsers all missed or were too weak.
    if llm is not None:
        try:
            llm_result = llm.extract(html=html, url=url)
        except Exception as e:
            log.warning("llm extractor crashed: %s", e)
            llm_result = None
        if llm_result is not None:
            return llm_result

    # Nothing else worked — return the low-confidence DOM result if we have one
    # so the caller can save with needs_review=1.
    return result


def _safe(fn: ParserFn, html: str, url: str, *, parser: str) -> ExtractedJob | None:
    try:
        return fn(html, url)
    except Exception as e:
        log.warning("parser %s crashed: %s", parser, e)
        return None
