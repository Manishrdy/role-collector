from __future__ import annotations

from job_agent.browser.search_engines import (
    bing_search_url,
    detect_block,
    google_search_url,
    parse_results,
)

# --- URL builders -------------------------------------------------------------


def test_google_url_includes_q_and_time_filter() -> None:
    url = google_search_url('site:jobs.ashbyhq.com "software engineer"', "past_24h", num=10)
    assert url.startswith("https://www.google.com/search?")
    assert "q=site%3Ajobs.ashbyhq.com+%22software+engineer%22" in url
    assert "tbs=qdr%3Ad" in url or "tbs=qdr:d" in url
    assert "num=10" in url
    assert "hl=en" in url


def test_google_url_past_48h_uses_qdr_d2() -> None:
    url = google_search_url("x", "past_48h", num=10)
    assert "tbs=qdr%3Ad2" in url or "tbs=qdr:d2" in url


def test_google_url_no_tbs_when_time_window_is_any() -> None:
    url = google_search_url("x", "any", num=10)
    assert "tbs=" not in url


def test_bing_url_includes_filters_and_count() -> None:
    url = bing_search_url("x", "past_week", count=10)
    assert url.startswith("https://www.bing.com/search?")
    assert "count=10" in url
    assert "filters=" in url


# --- Block detection ----------------------------------------------------------


def test_detect_google_block_redirect_to_sorry() -> None:
    reason = detect_block(
        current_url="https://sorry.google.com/sorry/index?continue=...",
        page_text="anything",
        engine="google",
    )
    assert reason is not None
    assert "google_block_redirect" in reason


def test_detect_google_unusual_traffic_text() -> None:
    reason = detect_block(
        current_url="https://www.google.com/sorry/index",
        page_text="Our systems have detected unusual traffic from your computer network",
        engine="google",
    )
    assert reason == "google_block_text"


def test_detect_bing_block_text() -> None:
    reason = detect_block(
        current_url="https://www.bing.com/search?q=x",
        page_text="We're sorry, but we are unable to display results",
        engine="bing",
    )
    assert reason == "bing_block_text"


def test_detect_block_returns_none_for_normal_page() -> None:
    reason = detect_block(
        current_url="https://www.google.com/search?q=x",
        page_text="A list of search results...",
        engine="google",
    )
    assert reason is None


# --- HTML parsing -------------------------------------------------------------


_GOOGLE_FIXTURE = """
<html><body>
<div id="rso">
  <div class="g">
    <a href="https://jobs.ashbyhq.com/acme/12345">
      <h3>Backend Engineer at Acme</h3>
    </a>
    <span>Acme is looking for a backend engineer to join our team. You will work on...</span>
  </div>
  <div class="g">
    <a href="/url?q=https://jobs.ashbyhq.com/foo/9999&amp;sa=U">
      <h3>Founding Engineer at Foo</h3>
    </a>
    <span>Foo just raised seed funding and is hiring engineers to build the platform.</span>
  </div>
  <div class="g">
    <a href="https://example.com/blog/some-post">
      <h3>An unrelated article</h3>
    </a>
  </div>
  <div class="g">
    <a href="https://www.google.com/search?q=related">internal google link</a>
  </div>
</div>
</body></html>
"""


def test_parse_results_filters_to_target_domain_and_unwraps_redirect() -> None:
    results = parse_results(
        page_html=_GOOGLE_FIXTURE,
        page=None,
        target_domain="jobs.ashbyhq.com",
        engine="google",
        query='site:jobs.ashbyhq.com "backend engineer"',
        time_window="past_24h",
        max_results=10,
    )
    urls = [r.url for r in results]
    assert "https://jobs.ashbyhq.com/acme/12345" in urls
    assert "https://jobs.ashbyhq.com/foo/9999" in urls
    assert all("example.com" not in u for u in urls)
    assert all("google.com" not in u for u in urls)


def test_parse_results_sets_rank_and_metadata() -> None:
    results = parse_results(
        page_html=_GOOGLE_FIXTURE,
        page=None,
        target_domain="jobs.ashbyhq.com",
        engine="google",
        query="q",
        time_window="past_24h",
        max_results=10,
    )
    assert len(results) == 2
    ranks = [r.rank for r in results]
    assert ranks == sorted(ranks)
    assert results[0].engine == "google"
    assert results[0].time_window == "past_24h"
    assert results[0].query == "q"
    # Snippet extraction is best-effort; at least one of the two should have
    # captured something.
    assert any(r.snippet for r in results)


def test_parse_results_no_target_domain_keeps_all_external_links() -> None:
    results = parse_results(
        page_html=_GOOGLE_FIXTURE,
        page=None,
        target_domain=None,
        engine="google",
        query="q",
        time_window="past_24h",
        max_results=10,
    )
    # The 'internal google link' is filtered (engine=google); example.com
    # plus the two ATS links should pass.
    urls = [r.url for r in results]
    assert any("example.com" in u for u in urls)
    assert len(urls) == 3


def test_parse_results_caps_at_max_results() -> None:
    results = parse_results(
        page_html=_GOOGLE_FIXTURE,
        page=None,
        target_domain="jobs.ashbyhq.com",
        engine="google",
        query="q",
        time_window="past_24h",
        max_results=1,
    )
    assert len(results) == 1
