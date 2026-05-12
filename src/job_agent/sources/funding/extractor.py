"""Funding event extractor: regex-first, LLM fallback.

The shape mirrors `extract.pipeline`: a deterministic regex pass handles the
dominant TechCrunch / HN headline phrasings ("X raises $YM Series A"), and
an optional LLMExtractor handles the long tail. Both return
`FundingEventCandidate | None`.

We intentionally over-fit to common phrasings rather than try to be clever:
most funding news headlines follow 5-6 templates, and the false positives
from a too-generous regex are noisier than a missed extraction.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from job_agent.sources.funding.schema import FundingEventCandidate

log = logging.getLogger(__name__)


class LLMFundingExtractor(Protocol):
    """Optional LLM fallback used when regex extraction misses.

    Implementations should return None if the LLM can't produce a confident
    structured event. The orchestrator passes a real client in production
    and `None` in tests.
    """

    def extract(
        self,
        *,
        snippet: str,
        source_url: str,
        aggregator: str,
    ) -> FundingEventCandidate | None: ...


# ---------------------------------------------------------------------------
# Round normalisation


_ROUND_NORMALIZE: dict[str, str] = {
    "pre-seed": "Pre-Seed",
    "pre seed": "Pre-Seed",
    "seed": "Seed",
    "series a": "Series A",
    "series b": "Series B",
    "series c": "Series C",
    "series d": "Series D",
    "series e": "Series E",
    "series f": "Series F",
    "series g": "Series G",
}

_ROUND_ALT = (
    r"(?:Pre[- ]Seed|Seed|Series\s+[A-Z])"
)


def _normalize_round(raw: str | None) -> str | None:
    if not raw:
        return None
    key = re.sub(r"\s+", " ", raw.strip().lower())
    return _ROUND_NORMALIZE.get(key, raw.strip().title())


# ---------------------------------------------------------------------------
# Amount + investor patterns


# Matches "$25M", "$25 million", "$2.5M", "$250K", "USD 25M" (with optional
# currency symbol $/€/£). Magnitude tokens are listed longest-first so the
# regex prefers "million" over the prefix "m" when both could match.
_AMOUNT = (
    r"(?P<amount>(?:\$|€|£|USD\s*|EUR\s*|GBP\s*)\s*"
    r"\d+(?:\.\d+)?\s*"
    r"(?:thousand|million|billion|[KMB])\b)"
)

# Three core templates. We compile a unioned pattern and rely on named
# groups; the first one that fully matches wins. Anchored to word
# boundaries so we don't latch onto substrings inside larger words.
_TEMPLATES = (
    # "Acme raises $25M Series A", "AcmeCo raised $5M in a seed round"
    re.compile(
        r"(?P<company>[A-Z][\w&'.\- ]{1,60}?)\s+"
        r"(?:raises|raised|closes|closed|announces|announced|secures|secured|nabs|lands)\s+"
        + _AMOUNT
        + r"(?:\s+(?:in\s+(?:a\s+)?|seed-stage|its)?\s*(?P<round>"
        + _ROUND_ALT
        + r")(?:\s+round|\s+funding)?)?",
        re.IGNORECASE,
    ),
    # "$25M Series A for Acme", "$5M seed round for AcmeCo"
    re.compile(
        _AMOUNT
        + r"\s+(?P<round>"
        + _ROUND_ALT
        + r")(?:\s+round|\s+funding)?\s+for\s+(?P<company>[A-Z][\w&'.\- ]{1,60})",
        re.IGNORECASE,
    ),
    # "Acme bags Series A funding of $25M"
    re.compile(
        r"(?P<company>[A-Z][\w&'.\- ]{1,60}?)\s+"
        r"(?:bags|scores)\s+(?P<round>"
        + _ROUND_ALT
        + r")\s+funding\s+of\s+"
        + _AMOUNT,
        re.IGNORECASE,
    ),
)

_INVESTOR_RE = re.compile(
    r"led\s+by\s+(?P<investors>[A-Z][\w&'.\- ]{1,80}?(?:,\s*[A-Z][\w&'.\- ]{1,80}?)*?)"
    r"(?=\s+(?:with|along|and)\b|[.,;]|\s*$)",
    re.IGNORECASE,
)


def _strip_trailing_filler(company: str) -> str:
    """Strip articles / conjunctions that occasionally get captured by greedy
    company patterns ('Acme today', 'Acme Inc., a startup', etc.)."""
    cleaned = re.sub(
        r"\s+(?:today|yesterday|inc\.?|corp\.?|ltd\.?|llc\.?|co\.?|gmbh)\b.*$",
        "",
        company,
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" ,.;-")


def extract_from_text(
    *,
    text: str,
    source_url: str,
    aggregator: str,
    announced_date: str | None = None,
    llm: LLMFundingExtractor | None = None,
) -> FundingEventCandidate | None:
    """Run the regex chain. If nothing matches and an LLM is available, try
    that as fallback. Returns None if both miss.
    """
    if not text or not text.strip():
        return None

    for tmpl in _TEMPLATES:
        m = tmpl.search(text)
        if not m:
            continue
        company = _strip_trailing_filler(m.group("company"))
        if not company or len(company) < 2:
            continue
        amount = m.group("amount").strip() if m.groupdict().get("amount") else None
        round_raw = m.group("round") if "round" in m.groupdict() else None
        round_norm = _normalize_round(round_raw)

        # Confidence: full hit > round-missing > amount-missing
        if company and amount and round_norm:
            confidence = 0.85
        elif company and amount:
            confidence = 0.75
        elif company and round_norm:
            confidence = 0.70
        else:
            confidence = 0.55

        investors_match = _INVESTOR_RE.search(text)
        investors = investors_match.group("investors").strip() if investors_match else None

        return FundingEventCandidate(
            company_name=company,
            round=round_norm,
            amount=amount,
            announced_date=announced_date,
            investors=investors,
            source_url=source_url,
            raw_snippet=text[:500],
            aggregator=aggregator,
            extraction_source="regex",
            extraction_confidence=confidence,
        )

    if llm is not None:
        try:
            return llm.extract(snippet=text, source_url=source_url, aggregator=aggregator)
        except Exception as e:
            log.warning("[funding-extractor] llm fallback errored: %s", e)
            return None
    return None
