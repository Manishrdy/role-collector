"""LinkedIn post HTML extractor.

Public LinkedIn post pages expose structured data in two places:
1. JSON-LD ``<script type="application/ld+json">`` blocks with `articleBody`,
   `author`, `headline`. Most reliable when present.
2. OpenGraph meta tags (`og:description`, `og:title`) that LinkedIn
   renders for unauthenticated viewers as a fallback.
3. DOM-level `<meta name="description">` and visible text.

We try in that order. Company + role extraction over the body text uses
small regex patterns ("we're hiring a <role> at <company>") plus
hashtag fallback.

The classifier runs as the final step so the extractor only emits
fully-processed ``ExtractedLinkedInPost`` records.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from job_agent.sources.linkedin.classifier import classify_post
from job_agent.sources.linkedin.schema import ExtractedLinkedInPost

log = logging.getLogger(__name__)

# "hiring a <role> at <company>" / "looking for a <role> at <company>"
_ROLE_AT_COMPANY_RE = re.compile(
    r"(?:hiring|looking for)\s+(?:an?\s+)?(?P<role>[\w\s\-/]{3,60}?)\s+(?:at|@)\s+(?P<company>[A-Z][\w&'.\- ]{1,60}?)(?:[.!,]|\s+(?:to|for|on)|$)",
    re.IGNORECASE,
)

# "<Company> is hiring" — company name first
_COMPANY_IS_HIRING_RE = re.compile(
    r"\b(?P<company>[A-Z][\w&'.\- ]{1,60}?)\s+is\s+hiring\b",
)


def _strip_company(name: str) -> str:
    return re.sub(
        r"\s+(?:inc\.?|corp\.?|ltd\.?|llc\.?|co\.?)\b.*$",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    ).strip(" ,.;-")


def _extract_jsonld(soup: BeautifulSoup) -> dict[str, Any] | None:
    """Return the first JSON-LD block that looks like a SocialMediaPosting / Article."""
    for tag in soup.find_all("script", type="application/ld+json"):
        body = tag.string or ""
        if not body:
            continue
        try:
            data = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            typ = item.get("@type", "")
            if isinstance(typ, str) and any(
                t in typ for t in ("SocialMediaPosting", "Article", "BlogPosting")
            ):
                return item
    return None


def _author_from_jsonld(item: dict[str, Any]) -> tuple[str | None, str | None]:
    author = item.get("author") or {}
    if isinstance(author, list):
        author = author[0] if author else {}
    if not isinstance(author, dict):
        return None, None
    return (
        author.get("name") if isinstance(author.get("name"), str) else None,
        author.get("url") if isinstance(author.get("url"), str) else None,
    )


def _meta_text(soup: BeautifulSoup, name_or_property: str, *, attr: str = "property") -> str | None:
    tag = soup.find("meta", attrs={attr: name_or_property})
    if tag is None:
        return None
    content = tag.get("content") if hasattr(tag, "get") else None
    return content if isinstance(content, str) and content.strip() else None


def extract_from_html(*, post_url: str, html: str) -> ExtractedLinkedInPost | None:
    """Parse a public LinkedIn post page; return structured record or None."""
    if not html or not html.strip():
        return None
    soup = BeautifulSoup(html, "lxml")

    author_name: str | None = None
    author_url: str | None = None
    post_text: str | None = None

    jsonld = _extract_jsonld(soup)
    if jsonld is not None:
        author_name, author_url = _author_from_jsonld(jsonld)
        body = jsonld.get("articleBody") or jsonld.get("text") or jsonld.get("description")
        if isinstance(body, str) and body.strip():
            post_text = body.strip()

    if post_text is None:
        post_text = (
            _meta_text(soup, "og:description")
            or _meta_text(soup, "description", attr="name")
        )

    if not post_text:
        return None

    # Best-effort company + role extraction from body.
    company: str | None = None
    detected_role: str | None = None
    m = _ROLE_AT_COMPANY_RE.search(post_text)
    if m:
        detected_role = m.group("role").strip()
        company = _strip_company(m.group("company"))
    else:
        m2 = _COMPANY_IS_HIRING_RE.search(post_text)
        if m2:
            company = _strip_company(m2.group("company"))

    is_hiring, signals, classifier_confidence = classify_post(post_text)
    # Final confidence: lift the floor when we successfully extracted a
    # company name, since that's strong evidence beyond the keyword match.
    confidence = min(classifier_confidence + (0.10 if company else 0.0), 1.0)

    return ExtractedLinkedInPost(
        post_url=post_url,
        post_text=post_text[:4000],
        author_name=author_name,
        author_url=author_url,
        company_name=company,
        detected_role=detected_role,
        is_hiring_post=is_hiring,
        hiring_signals=signals,
        extraction_source="regex",
        extraction_confidence=confidence,
    )
