from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.config import load_config, reset_config_cache


def test_loads_real_config_yaml() -> None:
    cfg = load_config()
    assert cfg.agent.mode in {"mvp", "beta", "prod"}
    assert cfg.llm.model == "deepseek-r1:8b"
    assert cfg.agent_loop.enabled is True
    assert 0.0 <= cfg.agent_loop.react_llm_sample_rate <= 1.0
    assert cfg.agent_loop.intelligence_llm_enabled is False
    assert cfg.agent_loop.cycle_reflection_llm_enabled is False
    assert cfg.search.roles, "default config must define roles"
    assert cfg.allowlist_domains, "default config must define an allowlist"
    assert cfg.dedupe.duplicate_threshold > cfg.dedupe.possible_duplicate_threshold


def test_db_path_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "override.db"
    monkeypatch.setenv("JOB_AGENT_DB_PATH", str(db))
    reset_config_cache()
    cfg = load_config()
    assert cfg.storage.sqlite_path == str(db)


def test_dedupe_weights_default_to_design_doc() -> None:
    cfg = load_config()
    w = cfg.dedupe.weights
    assert w.company == 0.30
    assert w.title == 0.25
    assert w.description == 0.30
    assert w.location == 0.10
    assert w.skills == 0.05
