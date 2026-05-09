"""URL safety layer. The hard-coded permission boundary for the browser tools.

See design_plan.md §8.2, §14.6, §14.7. Every URL the agent opens MUST pass
is_safe_url(); is_safe_url is the only place these rules live so the LLM
cannot route around them via prompt manipulation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from job_agent.config import AppConfig, load_config


class UrlSafetyError(PermissionError):
    """Raised when a URL fails the safety check."""


# Login / auth paths we never visit.
_LOGIN_PATH_FRAGMENTS = (
    "/login",
    "/signin",
    "/sign-in",
    "/sign_in",
    "/auth",
    "/oauth",
    "/sso",
    "/account/login",
    "/users/sign_in",
)

# Apply / submit paths blocked in MVP — design_plan.md §14.4.
_APPLY_PATH_FRAGMENTS = (
    "/apply",
    "/application",
    "/submit",
    "/checkout",
)

# Hard-blocked file extensions — design_plan.md §14.8.
_BLOCKED_EXTENSIONS = (
    ".exe",
    ".dmg",
    ".pkg",
    ".zip",
    ".js",
    ".sh",
    ".bat",
    ".scr",
    ".msi",
    ".tar",
    ".gz",
)

# URL-shortener hosts we refuse to follow blindly. Expand-and-verify can be
# added later.
_SHORTENER_HOSTS = frozenset(
    {
        "bit.ly",
        "t.co",
        "tinyurl.com",
        "goo.gl",
        "ow.ly",
        "lnkd.in",
        "buff.ly",
        "is.gd",
    }
)

# Tracking params stripped during canonicalisation — design_plan.md §18.2.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "source",
        "src",
    }
)


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str
    canonical_url: str | None = None


def _domain_matches(host: str, allowed: str) -> bool:
    """Match host against an allowlist entry (suffix match on labels)."""
    host = host.lower().lstrip(".")
    allowed = allowed.lower().lstrip(".")
    if host == allowed:
        return True
    return host.endswith("." + allowed)


def get_domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def canonicalize_url(url: str) -> str:
    """Lowercase domain, strip tracking params, drop fragments, normalise trailing slash."""
    parsed = urlparse(url)
    netloc = (parsed.hostname or "").lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    cleaned_query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k not in _TRACKING_PARAMS
        ]
    )

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse(
        (parsed.scheme.lower() or "https", netloc, path, parsed.params, cleaned_query, "")
    )


def is_login_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(frag in path for frag in _LOGIN_PATH_FRAGMENTS)


# Per-ATS rules for stripping apply-flow suffixes from a candidate URL so
# the safety layer doesn't reject what is actually a valid posting. We only
# do this for ATS hosts whose URL shape we know — leave everything else
# untouched.
_ATS_APPLY_SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    # Ashby:        /<co>/<uuid>/application[/...]
    ("jobs.ashbyhq.com", r"^(/[^/]+/[0-9a-fA-F-]{20,})/application(?:/.*)?$"),
    # Lever:        /<co>/<uuid>/apply
    ("jobs.lever.co", r"^(/[^/]+/[0-9a-fA-F-]{20,})/apply/?$"),
    # Greenhouse:   /<co>/jobs/<id>/applications/new (and similar tails)
    ("boards.greenhouse.io", r"^(/[^/]+/jobs/\d+)/applications?(?:/.*)?$"),
    ("job-boards.greenhouse.io", r"^(/[^/]+/jobs/\d+)/applications?(?:/.*)?$"),
)


def normalize_candidate_url(url: str) -> str:
    """Rewrite an ATS apply-flow URL to its canonical detail URL.

    Phase-2 search occasionally surfaces ad-tracked links pointing at the
    apply form (``/<co>/<uuid>/application?utm_source=...``). The detail
    page lives one path segment up — this helper strips the apply suffix
    and any tracking query string for known ATS hosts. Unknown URLs are
    returned unchanged.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for needle, pattern in _ATS_APPLY_SUFFIX_RULES:
        if host != needle and not host.endswith("." + needle):
            continue
        m = re.match(pattern, parsed.path)
        if not m:
            return url
        new_path = m.group(1)
        # Drop the entire query string (it's apply-flow tracking, not the
        # detail page's params) and the fragment.
        rebuilt: str = urlunparse(
            (
                parsed.scheme.lower() or "https",
                parsed.netloc.lower(),
                new_path,
                parsed.params,
                "",
                "",
            )
        )
        return rebuilt
    return url


def is_apply_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(frag in path for frag in _APPLY_PATH_FRAGMENTS)


def is_blocked_download(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _BLOCKED_EXTENSIONS)


def is_shortener(url: str) -> bool:
    return get_domain(url) in _SHORTENER_HOSTS


def is_allowed_domain(
    url: str, cfg: AppConfig | None = None, *, extra_allowed: set[str] | None = None
) -> bool:
    cfg = cfg or load_config()
    host = get_domain(url)
    if not host:
        return False
    candidates = list(cfg.allowlist_domains)
    if extra_allowed:
        candidates.extend(extra_allowed)
    return any(_domain_matches(host, allowed) for allowed in candidates)


def check_url(
    url: str,
    *,
    cfg: AppConfig | None = None,
    extra_allowed: set[str] | None = None,
    allow_apply: bool = False,
) -> SafetyDecision:
    """Return a SafetyDecision describing whether the URL is safe to open.

    `extra_allowed` lets the career-page resolver dynamically grant access
    to a verified company domain for the duration of a run.
    """
    cfg = cfg or load_config()

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return SafetyDecision(False, f"unsupported scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        return SafetyDecision(False, "missing hostname")
    if is_shortener(url):
        return SafetyDecision(False, f"url shortener blocked: {parsed.hostname}")
    if is_blocked_download(url):
        return SafetyDecision(False, "blocked file extension")
    if is_login_page(url):
        return SafetyDecision(False, "login page blocked")
    if not allow_apply and is_apply_path(url):
        return SafetyDecision(False, "apply/submit path blocked")
    if not is_allowed_domain(url, cfg, extra_allowed=extra_allowed):
        return SafetyDecision(False, f"domain not in allowlist: {parsed.hostname}")

    return SafetyDecision(True, "ok", canonical_url=canonicalize_url(url))


def assert_safe(url: str, **kwargs: object) -> str:
    """Convenience wrapper that raises UrlSafetyError on rejection.

    Returns the canonical URL on success.
    """
    decision = check_url(url, **kwargs)  # type: ignore[arg-type]
    if not decision.allowed:
        raise UrlSafetyError(f"{decision.reason}: {url}")
    assert decision.canonical_url is not None
    return decision.canonical_url
