"""Hiring-post classifier.

Pure regex over post body text. False positives are acceptable (the
downstream extractor will fail to find a company / role on noisy posts
and they get filtered out); false negatives matter more because we'd
drop the post entirely.

Returns ``(is_hiring, signals, confidence)`` so the orchestrator can
log which phrases matched. LLM fallback intentionally omitted in v1 —
the regex set is broad enough that adding an LLM call per post would
explode the rate-limit budget without much gain.
"""

from __future__ import annotations

import re

# Match both ASCII (U+0027) and curly (U+2019) apostrophes — LinkedIn
# serves the curly one in most post bodies. Built from explicit escapes
# so the source file stays ASCII.
_APOS_CLASS = "[\u2019']"

# (compiled_regex, signal_label, weight). Weights blend into the final
# confidence score. A post matching multiple phrases scores higher.
_HIRING_SIGNALS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(rf"\bwe{_APOS_CLASS}re hiring\b", re.IGNORECASE), "we're_hiring", 0.45),
    (re.compile(r"\bwe are hiring\b", re.IGNORECASE), "we_are_hiring", 0.45),
    (re.compile(r"\bopen roles?\b", re.IGNORECASE), "open_role", 0.30),
    (re.compile(r"\bopen position", re.IGNORECASE), "open_position", 0.30),
    (re.compile(r"\bjoin (?:our|the) team\b", re.IGNORECASE), "join_our_team", 0.30),
    (re.compile(r"\blooking for (?:an? )?[\w\s\-]{3,40}\b", re.IGNORECASE), "looking_for", 0.20),
    (re.compile(r"\bhiring (?:for|a|an)\b", re.IGNORECASE), "hiring_for", 0.40),
    (re.compile(r"\bnow hiring\b", re.IGNORECASE), "now_hiring", 0.50),
    (re.compile(r"#hiring\b", re.IGNORECASE), "hiring_hashtag", 0.40),
    (re.compile(r"\bapply (?:now|here|at|via)\b", re.IGNORECASE), "apply_cta", 0.25),
    (re.compile(r"\bdm me\b.*\b(role|hiring|interest)", re.IGNORECASE), "dm_me_role", 0.45),
)


def classify_post(post_text: str) -> tuple[bool, list[str], float]:
    """Return ``(is_hiring, matched_signals, confidence)``.

    ``is_hiring`` is True iff confidence >= 0.40 — single weak signals
    aren't enough on their own (avoids generic "looking for advice"-
    style false positives).
    """
    if not post_text or not post_text.strip():
        return False, [], 0.0
    signals: list[str] = []
    score = 0.0
    for pat, label, weight in _HIRING_SIGNALS:
        if pat.search(post_text):
            signals.append(label)
            score += weight
    # Cap at 1.0 — a post with every signal isn't infinitely more hiring-ish.
    confidence = min(score, 1.0)
    is_hiring = confidence >= 0.40
    return is_hiring, signals, confidence
