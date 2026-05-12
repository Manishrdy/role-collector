from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.db import repo
from job_agent.db.migrate import migrate


def test_upsert_inserts_linkedin_post(isolated_db: Path) -> None:
    migrate()
    r = repo.upsert_linkedin_post(
        post_url="https://linkedin.com/posts/jane-1",
        post_text="We're hiring a Senior Engineer at Acme!",
        author_name="Jane Doe",
        company_name="Acme",
        detected_role="Senior Engineer",
        confidence=0.85,
    )
    assert r.inserted is True
    assert r.post_id > 0


def test_upsert_dedupes_on_post_url(isolated_db: Path) -> None:
    migrate()
    a = repo.upsert_linkedin_post(
        post_url="https://linkedin.com/posts/x", post_text="hiring engineers"
    )
    b = repo.upsert_linkedin_post(
        post_url="https://linkedin.com/posts/x", post_text="updated body"
    )
    assert a.inserted is True
    assert b.inserted is False
    assert a.post_id == b.post_id


def test_different_post_urls_insert_separately(isolated_db: Path) -> None:
    migrate()
    a = repo.upsert_linkedin_post(
        post_url="https://linkedin.com/posts/a", post_text="hiring"
    )
    b = repo.upsert_linkedin_post(
        post_url="https://linkedin.com/posts/b", post_text="hiring"
    )
    assert a.inserted is True
    assert b.inserted is True
    assert a.post_id != b.post_id


def test_blank_inputs_raise(isolated_db: Path) -> None:
    migrate()
    with pytest.raises(ValueError):
        repo.upsert_linkedin_post(post_url="", post_text="x")
    with pytest.raises(ValueError):
        repo.upsert_linkedin_post(post_url="https://x", post_text="")
