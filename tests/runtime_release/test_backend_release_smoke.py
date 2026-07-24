from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

import pytest

from scripts.smoke_phase4_backend_release import run_fixture_snapshot


@pytest.fixture
def linux_tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="phase4b-smoke-", dir="/home/thenam176/.cache"))
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_fixture_snapshot_uses_copied_release_python_fixed_argv_and_filtered_env(
    linux_tmp_path: Path, monkeypatch,
) -> None:
    tmp_path = linux_tmp_path
    release = tmp_path / "backend-release"
    interpreter = release / ".venv/bin/python3.11"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    interpreter.chmod(0o755)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed.update(argv=argv, **kwargs)
        report = output / "report_fixture.json"
        report.write_text(
            '{"schema_version":"phase4b-fixture/v1","research_only":true,'
            '"backend_commit":"41f055b48033714c660f44cc20498b7545366e75"}\n'
        )
        report.chmod(0o600)
        return SimpleNamespace(returncode=0, stdout="fixture snapshot ok\n", stderr="")

    monkeypatch.setattr("scripts.smoke_phase4_backend_release.subprocess.run", fake_run)

    result = run_fixture_snapshot(release, output)

    argv = observed["argv"]
    assert argv[:3] == [str(interpreter), "-B", "-c"]
    assert 'name.startswith("exchange.")' in argv[3]
    assert observed["cwd"] == release
    assert observed["shell"] is False
    env = observed["env"]
    assert env["TRADING_MODE"] == "paper"
    assert env["LIVE_EXECUTION_ENABLED"] == "false"
    assert env["LIVE_TRADING_APPROVED"] == "false"
    assert not any("TOKEN" in key or "SECRET" in key or "API_KEY" in key for key in env)
    assert result == output / "report_fixture.json"
