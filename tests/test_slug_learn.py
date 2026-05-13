from __future__ import annotations

from pathlib import Path

from job_agent.sources.ats_api.slug_learn import learn_slugs_from_urls


def test_extracts_and_appends_new_slugs(tmp_path: Path) -> None:
    ashby_file = tmp_path / "ashby.txt"
    urls = [
        "https://jobs.ashbyhq.com/openai/uuid-1",
        "https://jobs.ashbyhq.com/anthropic/uuid-2",
        "https://jobs.lever.co/spotify/abc",  # not in slug_files -> skipped
        "https://example.com/foo",  # not ATS -> skipped
    ]
    added = learn_slugs_from_urls(urls, slug_files={"ashby": str(ashby_file)})
    assert added == {"ashby": 2}
    contents = ashby_file.read_text(encoding="utf-8")
    assert "openai" in contents
    assert "anthropic" in contents
    assert "spotify" not in contents


def test_dedupes_against_existing_file(tmp_path: Path) -> None:
    ashby_file = tmp_path / "ashby.txt"
    ashby_file.write_text("openai\n# comment\nanthropic\n", encoding="utf-8")
    urls = [
        "https://jobs.ashbyhq.com/openai/x",  # already known
        "https://jobs.ashbyhq.com/replit/y",  # new
    ]
    added = learn_slugs_from_urls(urls, slug_files={"ashby": str(ashby_file)})
    assert added == {"ashby": 1}
    final_slugs = [
        line.strip()
        for line in ashby_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    # Existing slugs preserved; replit appended; no openai duplicate.
    assert final_slugs.count("openai") == 1
    assert "replit" in final_slugs


def test_dedupes_within_a_single_batch(tmp_path: Path) -> None:
    ashby_file = tmp_path / "ashby.txt"
    urls = [
        "https://jobs.ashbyhq.com/openai/job1",
        "https://jobs.ashbyhq.com/openai/job2",  # same slug
        "https://jobs.ashbyhq.com/openai/job3",  # same slug
    ]
    added = learn_slugs_from_urls(urls, slug_files={"ashby": str(ashby_file)})
    assert added == {"ashby": 1}
    final_slugs = [
        line.strip()
        for line in ashby_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert final_slugs == ["openai"]


def test_multiple_providers_in_one_call(tmp_path: Path) -> None:
    paths = {
        "ashby": str(tmp_path / "ashby.txt"),
        "lever": str(tmp_path / "lever.txt"),
        "greenhouse": str(tmp_path / "greenhouse.txt"),
    }
    urls = [
        "https://jobs.ashbyhq.com/openai/x",
        "https://jobs.lever.co/spotify/y",
        "https://boards.greenhouse.io/stripe/jobs/z",
        "https://job-boards.greenhouse.io/figma/jobs/q",
    ]
    added = learn_slugs_from_urls(urls, slug_files=paths)
    assert added == {"ashby": 1, "lever": 1, "greenhouse": 2}


def test_returns_empty_when_no_slug_files_configured() -> None:
    urls = ["https://jobs.ashbyhq.com/openai/x"]
    assert learn_slugs_from_urls(urls, slug_files={}) == {}


def test_skips_providers_not_in_slug_files(tmp_path: Path) -> None:
    """Defensive: only learn for providers the operator has opted into."""
    ashby_file = tmp_path / "ashby.txt"
    urls = [
        "https://jobs.ashbyhq.com/openai/x",
        "https://jobs.lever.co/spotify/y",  # no lever path configured
    ]
    added = learn_slugs_from_urls(urls, slug_files={"ashby": str(ashby_file)})
    assert added == {"ashby": 1}
    # Lever was opt-out; the lever file is never created.
    assert not (tmp_path / "lever.txt").exists()


def test_creates_file_with_header_on_first_write(tmp_path: Path) -> None:
    ashby_file = tmp_path / "subdir" / "ashby.txt"
    urls = ["https://jobs.ashbyhq.com/openai/x"]
    learn_slugs_from_urls(urls, slug_files={"ashby": str(ashby_file)})
    contents = ashby_file.read_text(encoding="utf-8")
    assert contents.startswith("#")  # header comment present
    assert "openai" in contents


def test_handles_malformed_urls_gracefully(tmp_path: Path) -> None:
    ashby_file = tmp_path / "ashby.txt"
    urls = [
        "https://jobs.ashbyhq.com/openai/x",  # valid
        "not-a-url",
        "",
        "https://jobs.ashbyhq.com/",  # no slug
    ]
    added = learn_slugs_from_urls(urls, slug_files={"ashby": str(ashby_file)})
    assert added == {"ashby": 1}


def test_normalizes_slug_to_lowercase(tmp_path: Path) -> None:
    ashby_file = tmp_path / "ashby.txt"
    urls = [
        "https://jobs.ashbyhq.com/OpenAI/x",
        "https://jobs.ashbyhq.com/openai/y",  # same slug different case
    ]
    added = learn_slugs_from_urls(urls, slug_files={"ashby": str(ashby_file)})
    assert added == {"ashby": 1}
    final = ashby_file.read_text(encoding="utf-8")
    assert "openai" in final
    assert "OpenAI" not in final
