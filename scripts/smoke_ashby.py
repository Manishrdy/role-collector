"""PoC: open one Ashby URL with Playwright and try to extract job data.

What this validates:
1. Ashby pages load fine without bot detection (no Cloudflare, no captchas).
2. After JS hydration, the page exposes structured job data we can parse
   (either __NEXT_DATA__, an embedded JSON object, or the rendered DOM).
3. The deterministic extractor pulls out title / company / location /
   apply-link cleanly. If it can't, the LLM fallback would take over (not
   exercised in this PoC).

Earlier runs of this script confirmed Ashby is automation-friendly even
without stealth tricks — plain Playwright is enough.

Usage:
    .venv/bin/python scripts/smoke_ashby.py
    .venv/bin/python scripts/smoke_ashby.py --url https://jobs.ashbyhq.com/posthog
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Make `import job_agent` work when the script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
log = logging.getLogger("smoke_ashby")


# A handful of known-active Ashby customer pages. `--url` overrides.
DEFAULT_CANDIDATES = [
    "https://jobs.ashbyhq.com/posthog",
    "https://jobs.ashbyhq.com/ramp",
    "https://jobs.ashbyhq.com/anthropic",
    "https://jobs.ashbyhq.com/vercel",
    "https://jobs.ashbyhq.com/linear",
    "https://jobs.ashbyhq.com/notion",
    "https://jobs.ashbyhq.com/replit",
    "https://jobs.ashbyhq.com/modal-labs",
]


def _extract_next_data(html: str) -> dict[str, Any] | None:
    """Pull the __NEXT_DATA__ JSON blob if Ashby ships one."""
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not m:
        return None
    try:
        result = json.loads(m.group(1))
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_job_postings(next_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort extraction of job postings from Ashby's __NEXT_DATA__.

    Ashby's data shape changes; we walk the tree looking for any object
    that looks like a job posting (has `title` and `id` and `locationName`
    or similar). Cheaper than guessing the exact path.
    """
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            keys = set(node.keys())
            looks_like_job = "title" in keys and (
                "id" in keys or "jobPostingId" in keys
            )
            if looks_like_job:
                found.append(node)
                return  # don't recurse into the job itself
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(next_data)
    return found


def _open_one(url: str) -> dict[str, Any]:
    """Visit `url` with plain Playwright, return a small report dict."""
    report: dict[str, Any] = {"url": url}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            html = page.content()
            title = page.title()
            current_url = page.url
            visible_text_chunk = page.inner_text("body")[:500] if page.locator("body").count() else ""

            # Bot-detection sniff: Ashby/Cloudflare interstitials.
            blocked_signals = [
                "Just a moment",  # cloudflare challenge
                "Access denied",
                "Attention Required!",
            ]
            blocked_reason = next(
                (s for s in blocked_signals if s.lower() in visible_text_chunk.lower()),
                None,
            )

            report.update(
                {
                    "current_url": current_url,
                    "page_title": title,
                    "html_size": len(html),
                    "blocked_reason": blocked_reason,
                }
            )

            next_data = _extract_next_data(html)
            report["has_next_data"] = next_data is not None

            jobs: list[dict[str, Any]] = []
            if next_data:
                jobs = _extract_job_postings(next_data)
            report["job_count_in_next_data"] = len(jobs)

            # If __NEXT_DATA__ didn't yield jobs, fall back to anchor scraping.
            if not jobs:
                anchors = page.locator("a").all()
                fallback_links: list[dict[str, str]] = []
                for a in anchors[:200]:
                    href = a.get_attribute("href") or ""
                    text = (a.inner_text() or "").strip()
                    if href.startswith("/") and text and len(text) < 120:
                        fallback_links.append(
                            {"href": href, "title": text[:120]}
                        )
                # Heuristic: links whose href looks like /<company>/<uuid>
                jobby = [
                    link for link in fallback_links
                    if re.match(r"^/[a-z0-9-]+/[a-f0-9-]{36}", link["href"])
                ]
                report["fallback_link_count"] = len(jobby)
                report["fallback_sample"] = jobby[:5]
            else:
                # Sample up to 3 jobs with the fields we care about.
                samples = []
                for j in jobs[:3]:
                    samples.append(
                        {
                            "id": j.get("id") or j.get("jobPostingId"),
                            "title": j.get("title"),
                            "location": (
                                j.get("locationName")
                                or j.get("location")
                                or (j.get("locationIds") and j["locationIds"][:1])
                            ),
                            "department": j.get("departmentName"),
                            "employment_type": j.get("employmentType"),
                        }
                    )
                report["job_samples"] = samples

        finally:
            page.close()
            ctx.close()
            browser.close()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        action="append",
        help="Specific Ashby URL(s) to visit. Defaults to a built-in list.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=2,
        help="Stop after N successful loads (default: 2).",
    )
    args = parser.parse_args()

    targets = args.url or DEFAULT_CANDIDATES
    successes = 0
    for url in targets:
        log.info("=" * 60)
        log.info("trying %s", url)
        try:
            report = _open_one(url)
        except Exception as e:
            log.error("error visiting %s: %s", url, e)
            continue

        # Print the report nicely.
        print(json.dumps(report, indent=2, default=str))

        if report.get("blocked_reason"):
            log.warning("BLOCKED on %s: %s", url, report["blocked_reason"])
            continue

        successful = (
            report.get("job_count_in_next_data", 0) > 0
            or report.get("fallback_link_count", 0) > 0
        )
        if successful:
            successes += 1
            log.info("OK: extracted job data from %s", url)
            if successes >= args.max:
                break
        else:
            log.warning("page loaded but no jobs found at %s", url)

    log.info("=" * 60)
    log.info("PoC summary: %d successful loads with job data", successes)
    return 0 if successes > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
