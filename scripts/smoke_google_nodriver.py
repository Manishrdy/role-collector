"""Test nodriver (Selenium/Playwright successor that avoids CDP detection)
against the same Google query that blocked every Playwright variant.

nodriver is async-only. Real Chrome (not bundled Chromium) is required.

Usage:
    .venv/bin/python scripts/smoke_google_nodriver.py
    .venv/bin/python scripts/smoke_google_nodriver.py --query 'site:jobs.lever.co "backend engineer"'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, unquote

import nodriver as uc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s :: %(message)s",
)
log = logging.getLogger("nodriver_smoke")


def build_google_url(query: str, time_window: str | None = "past_24h") -> str:
    parts = [f"q={quote_plus(query)}", "num=10", "hl=en"]
    tbs = {"past_24h": "qdr:d", "past_48h": "qdr:d2", "past_week": "qdr:w"}.get(
        time_window or ""
    )
    if tbs:
        parts.append(f"tbs={tbs}")
    return "https://www.google.com/search?" + "&".join(parts)


def looks_blocked(current_url: str, page_text: str) -> str | None:
    if "/sorry/index" in current_url or "sorry.google.com" in current_url:
        return f"redirect:{current_url}"
    needles = [
        "unusual traffic from your computer network",
        "Our systems have detected unusual traffic",
    ]
    for n in needles:
        if n.lower() in page_text.lower():
            return "text_match"
    return None


_HREF_RE = re.compile(r'href="([^"]+)"')


def extract_target_anchors(html: str, target_domain: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _HREF_RE.finditer(html):
        href = m.group(1)
        if href.startswith("/url?q="):
            inner = href[len("/url?q="):].split("&", 1)[0]
            href = unquote(inner)
        if not href.startswith("http"):
            continue
        if target_domain not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


async def run(args: argparse.Namespace) -> int:
    url = build_google_url(args.query, args.time_window if args.time_window != "any" else None)
    log.info("target: %s", url)

    browser_kwargs: dict = {"headless": False}
    if args.use_brave:
        browser_kwargs["browser_executable_path"] = (
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        )
    log.info("starting nodriver...")
    browser = await uc.start(**browser_kwargs)

    try:
        if args.warmup:
            log.info("warmup: opening google.com homepage")
            await browser.get("https://www.google.com/")
            await asyncio.sleep(2)

        log.info("navigating to search url")
        page = await browser.get(url)
        await asyncio.sleep(3)  # let JS settle

        current_url = page.target.url if page.target else url
        html = await page.get_content()
        try:
            text = await page.evaluate("document.body && document.body.innerText.slice(0,1000)") or ""
        except Exception:
            text = ""

        if args.dump:
            Path(args.dump).write_text(html, encoding="utf-8")
            log.info("dumped html -> %s (%d bytes)", args.dump, len(html))

        blocked = looks_blocked(current_url, text)
        anchors = extract_target_anchors(html, args.target_domain)

        report = {
            "stack": "nodriver",
            "browser": "real-brave" if args.use_brave else "real-chrome",
            "query": args.query,
            "submitted_url": url,
            "final_url": current_url,
            "html_size": len(html),
            "blocked_reason": blocked,
            f"{args.target_domain}_anchor_count": len(anchors),
            "anchor_sample": anchors[:10],
        }
        print(json.dumps(report, indent=2))

        if blocked:
            log.warning("BLOCKED: %s", blocked)
            return 2
        if not anchors:
            log.warning("page loaded, no %s links present", args.target_domain)
            return 3
        log.info("SUCCESS: %d %s links", len(anchors), args.target_domain)
        return 0
    finally:
        try:
            browser.stop()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        default='site:jobs.ashbyhq.com intitle:"software engineer"',
    )
    parser.add_argument(
        "--time-window",
        default="past_24h",
        choices=["past_24h", "past_48h", "past_week", "any"],
    )
    parser.add_argument("--target-domain", default="jobs.ashbyhq.com")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--use-brave", action="store_true")
    parser.add_argument("--dump", default=None)
    args = parser.parse_args()

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
