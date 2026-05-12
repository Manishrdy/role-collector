from __future__ import annotations

import json

from job_agent.sources.linkedin.extractor import extract_from_html


def _jsonld_html(body: str, author: str = "Jane Doe") -> str:
    payload = json.dumps(
        {
            "@type": "SocialMediaPosting",
            "headline": "post",
            "articleBody": body,
            "author": {
                "@type": "Person",
                "name": author,
                "url": f"https://linkedin.com/in/{author.lower().replace(' ', '-')}",
            },
        }
    )
    return f'<html><head><script type="application/ld+json">{payload}</script></head></html>'


def test_extracts_from_jsonld_block() -> None:
    html = _jsonld_html("We're hiring a Senior Backend Engineer at Acme Inc. DM me!")
    post = extract_from_html(post_url="https://linkedin.com/posts/x", html=html)
    assert post is not None
    assert post.author_name == "Jane Doe"
    assert post.company_name == "Acme"  # Inc stripped
    assert post.detected_role == "Senior Backend Engineer"
    assert post.is_hiring_post is True


def test_extracts_company_is_hiring_phrasing() -> None:
    html = _jsonld_html("Acme is hiring engineers across all teams. #hiring")
    post = extract_from_html(post_url="https://linkedin.com/posts/x", html=html)
    assert post is not None
    assert post.company_name == "Acme"
    # No "hiring a <role> at" pattern, so detected_role stays None.
    assert post.detected_role is None
    assert post.is_hiring_post is True


def test_falls_back_to_og_description() -> None:
    html = """
    <html><head>
      <meta property="og:description" content="We are hiring a Staff Engineer at Beta Corp! Apply now.">
    </head></html>
    """
    post = extract_from_html(post_url="https://linkedin.com/posts/y", html=html)
    assert post is not None
    assert post.company_name == "Beta"  # Corp stripped
    assert "Staff Engineer" in (post.detected_role or "")
    assert post.is_hiring_post is True


def test_non_hiring_post_marked_not_hiring() -> None:
    html = _jsonld_html("Just enjoying a coffee and reflecting on the year")
    post = extract_from_html(post_url="https://linkedin.com/posts/z", html=html)
    assert post is not None
    assert post.is_hiring_post is False
    assert post.company_name is None


def test_empty_html_returns_none() -> None:
    assert extract_from_html(post_url="https://x", html="") is None
    assert extract_from_html(post_url="https://x", html="   ") is None


def test_no_body_no_meta_returns_none() -> None:
    """A page with no JSON-LD AND no og:description / meta description = None."""
    html = "<html><head><title>Sign In</title></head><body></body></html>"
    assert extract_from_html(post_url="https://x", html=html) is None


def test_company_extraction_boosts_confidence() -> None:
    """When company extraction succeeds, the confidence floor goes up
    relative to classifier-only confidence."""
    html_with = _jsonld_html("Acme is hiring engineers #hiring")
    html_without = _jsonld_html("We are hiring engineers")
    with_co = extract_from_html(post_url="https://linkedin.com/x", html=html_with)
    without_co = extract_from_html(post_url="https://linkedin.com/y", html=html_without)
    assert with_co is not None and without_co is not None
    assert with_co.company_name == "Acme"
    assert without_co.company_name is None
    # With-company should have confidence boosted by 0.10.
    assert with_co.extraction_confidence > without_co.extraction_confidence
