from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess

import pytest

from scripts.dev import BASEDPYRIGHT_VERSION


ROOT = Path(__file__).resolve().parents[2]


def test_static_toolchain_is_pinned_and_scoped_to_production_python() -> None:
    workflow = (ROOT / "scripts/dev.py").read_text(encoding="utf-8")
    ruff_config = (ROOT / "ruff.toml").read_text(encoding="utf-8")

    assert '"ruff==0.16.5"' in workflow
    assert '"basedpyright==1.39.10"' in workflow
    assert 'target-version = "py311"' in ruff_config
    assert 'select = ["E9", "F821", "F822"]' in ruff_config

    for path in (
        "apps/control_api",
        "apps/job_api",
        "apps/operator_api",
        "packages",
        "services",
        "scripts",
    ):
        assert f'"{path}"' in workflow
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


@pytest.mark.parametrize("project", ["pyrightconfig.json", "pyrightconfig.legacy.json"])
def test_static_exit_policy_preserves_warnings_and_rejects_errors(tmp_path, project):
    config = json.loads((ROOT / project).read_text())
    for key in ("baselineFile", "exclude", "extraPaths"):
        config.pop(key, None)
    config["include"] = ["probe.py"]
    path = tmp_path / "pyrightconfig.json"
    path.write_text(json.dumps(config))
    probe = tmp_path / "probe.py"
    # The pinned tool is installed by `scripts/dev.py static`; this test cannot
    # contact a registry or change the repository's retained baselines.
    command = ["uvx", "--offline", "--from", BASEDPYRIGHT_VERSION, "basedpyright",
        "--level", "error", "--outputjson", "--project", str(path)]
    environment = {**os.environ, "CI": "true", "GITHUB_ACTIONS": "true"}
    probe.write_text("def identity(value):\n    return value\n")
    warning = subprocess.run(command, env=environment, cwd=tmp_path,
        capture_output=True, text=True, timeout=30)
    report = json.loads(warning.stdout)
    assert report["summary"]["errorCount"] == 0
    assert report["summary"]["warningCount"] > 0
    assert warning.returncode == 0, warning.stdout + warning.stderr
    probe.write_text('value: int = "wrong"\n')
    error = subprocess.run(command, env=environment, cwd=tmp_path,
        capture_output=True, text=True, timeout=30)
    assert json.loads(error.stdout)["summary"]["errorCount"] > 0
    assert error.returncode == 1
