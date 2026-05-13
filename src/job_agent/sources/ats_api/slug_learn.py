"""Organic slug learning.

Hooks into the candidate-URL stream of the worker pipeline. Every cycle
extracts the company slug from any Lever / Greenhouse / Ashby URL it
sees (via Google, watchlist, anywhere) and appends new ones to a
per-provider slug file. Next cycle's ATS API discovery channel walks
the learned catalog directly via JSON, far cheaper than re-Googling
for the same companies.

Net effect: Google discovers a slug once, then ATS API mines that
company's full board forever after.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from job_agent.sources.ats_api.clients import (
    ashby_slug_from_url,
    greenhouse_slug_from_url,
    lever_slug_from_url,
)

log = logging.getLogger(__name__)


# (provider key in config.slug_files, extractor)
_EXTRACTORS = (
    ("ashby", ashby_slug_from_url),
    ("lever", lever_slug_from_url),
    ("greenhouse", greenhouse_slug_from_url),
)


def _read_existing_slugs(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            out.add(s)
    return out


def _append_new_slugs(path: Path, new_slugs: list[str]) -> None:
    """Append slugs to a per-provider catalog file, creating it if needed.

    Idempotent: callers must dedupe against existing contents before
    invoking this. The file is opened in append mode so a partial write
    won't corrupt the existing catalog.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Slugs learned from the worker's candidate URL stream.\n"
            "# Manually-added slugs may live in this file too; new lines\n"
            "# are appended in discovery order.\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as f:
        for slug in new_slugs:
            f.write(f"{slug}\n")


def learn_slugs_from_urls(
    urls: Iterable[str],
    *,
    slug_files: dict[str, str],
) -> dict[str, int]:
    """Extract provider slugs from ``urls`` and append new ones to each
    provider's slug file.

    ``slug_files`` is the same shape as
    ``cfg.sources.ats_api_discovery.slug_files`` — provider name to path.
    Providers absent from ``slug_files`` are skipped (we don't want to
    learn into a path the operator hasn't opted into).

    Returns a per-provider count of *new* slugs added this call.
    """
    if not slug_files:
        return {}
    # Bucket slugs per provider.
    candidates: dict[str, list[str]] = {provider: [] for provider in slug_files}
    seen_per_provider: dict[str, set[str]] = {
        provider: set() for provider in slug_files
    }
    for url in urls:
        if not isinstance(url, str):
            continue
        for provider, extractor in _EXTRACTORS:
            if provider not in slug_files:
                continue
            slug = extractor(url)
            if not slug:
                continue
            normalized = slug.strip().lower()
            if not normalized or normalized in seen_per_provider[provider]:
                continue
            seen_per_provider[provider].add(normalized)
            candidates[provider].append(normalized)

    added: dict[str, int] = {}
    for provider, found in candidates.items():
        if not found:
            continue
        path = Path(slug_files[provider])
        existing = _read_existing_slugs(path)
        new_slugs = [s for s in found if s not in existing]
        if not new_slugs:
            continue
        try:
            _append_new_slugs(path, new_slugs)
        except OSError as e:
            log.warning(
                "[slug-learn] failed to append %d %s slugs to %s: %s",
                len(new_slugs),
                provider,
                path,
                e,
            )
            continue
        added[provider] = len(new_slugs)
        log.info(
            "[slug-learn] %s: appended %d new slugs to %s",
            provider,
            len(new_slugs),
            path,
        )
    return added
