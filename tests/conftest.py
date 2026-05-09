from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from job_agent.config import reset_config_cache


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the agent at a throwaway sqlite file for a single test."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("JOB_AGENT_DB_PATH", str(db))
    reset_config_cache()
    yield db
    reset_config_cache()


@pytest.fixture(autouse=True)
def _reset_cfg_cache() -> Iterator[None]:
    """Make sure no test leaks a cached config to the next."""
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def repo_root() -> Path:
    return Path(os.environ.get("PWD", ".")).resolve()
