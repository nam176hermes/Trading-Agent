from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_static_toolchain_is_pinned_and_scoped_to_production_python() -> None:
    workflow = (ROOT / "scripts/dev.py").read_text(encoding="utf-8")
    ruff_config = (ROOT / "ruff.toml").read_text(encoding="utf-8")

    assert '"ruff==0.16.5"' in workflow
    assert '"basedpyright==1.39.10"' in workflow
    assert 'target-version = "py311"' in ruff_config
    assert 'select = ["E9", "F821", "F822"]' in ruff_config

    assert '"apps/control_api", "apps/job_api", "packages", "services", "scripts"' in workflow
    assert '"--project"' in workflow
    assert '"pyrightconfig.legacy.json"' in workflow
    assert '"legacy/research-backend/.venv/bin/python"' in workflow


def test_basedpyright_baselines_exist_for_both_python_projects() -> None:
    assert (ROOT / "pyrightconfig.json").is_file()
    assert (ROOT / ".basedpyright/baseline.json").is_file()
    assert (ROOT / "pyrightconfig.legacy.json").is_file()
    assert (ROOT / ".basedpyright/legacy-baseline.json").is_file()
    assert not (ROOT / "legacy/research-backend/pyrightconfig.json").exists()
    assert not (ROOT / "legacy/research-backend/.basedpyright").exists()
