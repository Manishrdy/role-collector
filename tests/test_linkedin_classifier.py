from __future__ import annotations

import pytest

from job_agent.sources.linkedin.classifier import classify_post


@pytest.mark.parametrize(
    "text,expected_hiring",
    [
        ("We are hiring a Senior Software Engineer at Acme! Apply now.", True),
        ("We're hiring. Looking for a backend engineer. Join our team!", True),
        ("Excited to announce we have open roles for engineers #hiring", True),
        ("We\u2019re hiring engineers \u2014 DM me if interested", True),
        ("Now hiring engineers across all teams", True),
        ("DM me if interested in this hiring opportunity", True),
        ("Just looking for advice on my resume", False),
        ("What a great event today, learned so much", False),
        ("I love my new job", False),
        ("", False),
        ("   ", False),
    ],
)
def test_classifier_decisions(text: str, expected_hiring: bool) -> None:
    is_hiring, _signals, _conf = classify_post(text)
    assert is_hiring is expected_hiring


def test_classifier_captures_multiple_signals() -> None:
    text = "We are hiring a Senior Engineer at Acme. Apply now. #hiring Join our team!"
    is_hiring, signals, conf = classify_post(text)
    assert is_hiring is True
    assert conf == 1.0  # capped
    assert len(signals) >= 4


def test_classifier_curly_apostrophe_equivalent_to_ascii() -> None:
    h1, _, c1 = classify_post("We're hiring")
    h2, _, c2 = classify_post("We\u2019re hiring")
    assert h1 == h2
    assert c1 == c2


def test_classifier_confidence_below_threshold_does_not_classify() -> None:
    # "looking for" alone (weight 0.20) is below the 0.40 threshold.
    is_hiring, signals, conf = classify_post("looking for a recommendation")
    assert is_hiring is False
    assert "looking_for" in signals
    assert conf == pytest.approx(0.20)
