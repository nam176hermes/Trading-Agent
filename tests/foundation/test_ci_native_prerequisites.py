from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from scripts import check_ci_native_prerequisites as check
from scripts import t_g03_capability_topology as topology


@pytest.mark.parametrize("state,expected", [("AVAILABLE", 0), ("UNAVAILABLE", 0), ("BROKEN", 2)])
def test_ci_native_preflight_fails_early_without_relaxing_probe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], state: str, expected: int,
) -> None:
    from contextlib import contextmanager
    checked = []
    closed = []

    @contextmanager
    def probe(code: str, *, runner):
        runner(["fixed-native-probe"], check=False)
        try:
            yield SimpleNamespace(code=code, state=state, fact="NATIVE_PROBE_INVALID", probe={"exit_code": 1})
        finally:
            closed.append(code)

    monkeypatch.setattr(topology, "_retained_native_probe", probe)
    monkeypatch.setattr(topology, "_postcheck_native_probe", lambda session: checked.append(session.code))
    monkeypatch.setattr(check.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b"", b"fixed diagnostic\n"))
    assert check.main() == expected
    assert checked == closed == ["NATIVE-BWRAP-OS-SANDBOX", "NATIVE-USERNS-ROOT-PROVISION"]
    output = capsys.readouterr().out
    assert "fixed diagnostic" in output
    assert state in output
