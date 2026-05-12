from __future__ import annotations

import pytest

from job_agent.sources.funding.filters import (
    looks_like_personal_name,
    looks_like_vc_fund,
    resolver_retry_budget,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("DeepInfra", False),
        ("Stripe", False),
        ("a16z", False),
        ("OpenAI", False),
        ("Acme Inc", False),
        ("Sequoia Capital", True),
        ("Acme Ventures", True),
        ("Founders Fund", True),
        ("Acme Partners", True),
        ("Sequoia Investments", True),
        ("SpaceX backer 137 Ventures", True),  # contains "Ventures"
    ],
)
def test_looks_like_vc_fund(name: str, expected: bool) -> None:
    assert looks_like_vc_fund(name) is expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Katie Haun", True),
        ("John Smith", True),
        ("Acme Inc", False),  # business suffix
        ("Acme Inc.", False),  # business suffix with period
        ("Acme Corp", False),
        ("Sequoia Capital", False),  # caught by vc-fund layer
        ("Acme LLC", False),  # too short for personal name regex
        ("DeepInfra", False),  # single word
        ("SpaceX backer 137 Ventures", False),  # too many words
        ("a16z", False),
    ],
)
def test_looks_like_personal_name(name: str, expected: bool) -> None:
    assert looks_like_personal_name(name) is expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("DeepInfra", 8),
        ("Stripe", 8),
        ("Acme Inc", 8),
        ("Katie Haun", 3),
        ("Sequoia Capital", 3),
        ("Acme Ventures", 3),
    ],
)
def test_resolver_retry_budget(name: str, expected: int) -> None:
    assert resolver_retry_budget(name) == expected
