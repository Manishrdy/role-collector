from __future__ import annotations

import pytest

from job_agent.sources.funding.extractor import extract_from_text
from job_agent.sources.funding.schema import FundingEventCandidate


def _extract(text: str) -> FundingEventCandidate | None:
    return extract_from_text(text=text, source_url="https://x", aggregator="test")


def test_extracts_company_round_amount_from_raises_template() -> None:
    r = _extract("Acme raises $25M Series A")
    assert r is not None
    assert r.company_name == "Acme"
    assert r.round == "Series A"
    assert r.amount == "$25M"
    assert r.extraction_confidence == 0.85
    assert r.extraction_source == "regex"


def test_extracts_seed_round_with_investors() -> None:
    r = _extract("AcmeCo raised $5M in a seed round led by Sequoia and Benchmark")
    assert r is not None
    assert r.round == "Seed"
    assert r.investors is not None
    assert "Sequoia" in r.investors


def test_extracts_announces_phrasing() -> None:
    r = _extract("Lemonade announces $13M Series B funding")
    assert r is not None
    assert r.company_name == "Lemonade"
    assert r.round == "Series B"


def test_extracts_inverted_amount_first_template() -> None:
    r = _extract("$25M Series A for Acme")
    assert r is not None
    assert r.company_name == "Acme"
    assert r.round == "Series A"


def test_extracts_bags_phrasing() -> None:
    r = _extract("Acme bags Series A funding of $25M")
    assert r is not None
    assert r.round == "Series A"
    assert r.amount == "$25M"


def test_extracts_million_long_form() -> None:
    r = _extract("Stripe secured $600 million in Series H")
    assert r is not None
    assert r.amount == "$600 million"
    assert r.round == "Series H"


def test_extracts_billion_word() -> None:
    r = _extract("OpenAI raised $10 billion led by Microsoft")
    assert r is not None
    assert r.amount == "$10 billion"
    assert r.investors == "Microsoft"


def test_misses_non_funding_headline() -> None:
    assert _extract("NotAFundingHeadline about cats") is None


def test_strips_company_filler_words() -> None:
    r = _extract("Acme Inc. today raised $5M Series A")
    assert r is not None
    # Trailing "Inc." should be stripped from company.
    assert r.company_name in ("Acme", "Acme Inc")


def test_empty_text_returns_none() -> None:
    assert _extract("") is None
    assert _extract("   ") is None


def test_llm_fallback_invoked_when_regex_misses() -> None:
    class StubLLM:
        def extract(self, *, snippet: str, source_url: str, aggregator: str):  # type: ignore[no-untyped-def]
            return FundingEventCandidate(
                company_name="LLMCompany",
                source_url=source_url,
                aggregator=aggregator,
                extraction_source="llm",
                extraction_confidence=0.6,
            )

    r = extract_from_text(
        text="nothing matches here", source_url="https://x", aggregator="test", llm=StubLLM()
    )
    assert r is not None
    assert r.extraction_source == "llm"
    assert r.company_name == "LLMCompany"


def test_llm_fallback_failure_is_swallowed() -> None:
    class BrokenLLM:
        def extract(self, *, snippet: str, source_url: str, aggregator: str):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    r = extract_from_text(
        text="nothing matches here", source_url="https://x", aggregator="test", llm=BrokenLLM()
    )
    assert r is None


def test_round_alternation_prefers_long_form_over_letter() -> None:
    """Regression: '$600 m' (just the letter m) was returned before we
    reordered the magnitude alternation. The full word must win."""
    r = _extract("Stripe secured $600 million in Series H")
    assert r is not None
    assert r.amount and "million" in r.amount


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("seed", "Seed"),
        ("Series A", "Series A"),
        ("series b", "Series B"),
        ("pre-seed", "Pre-Seed"),
    ],
)
def test_round_normalization_via_full_extraction(raw: str, expected: str) -> None:
    r = _extract(f"Acme raised $5M {raw} round")
    assert r is not None
    assert r.round == expected
