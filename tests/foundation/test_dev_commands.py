from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import dev


def test_types_reports_both_components_when_core_fails(monkeypatch):
    commands = []

    def run(command):
        commands.append(command)
        return 1 if len(commands) == 1 else 0

    monkeypatch.setattr(dev, "_run", run)
    assert dev.main(["types"]) == 1
    assert len(commands) == 2
    assert "pyrightconfig.legacy.json" in commands[1]


def test_static_reports_types_even_when_lint_fails(monkeypatch):
    called = []
    monkeypatch.setattr(dev, "_lint", lambda: 2)
    monkeypatch.setattr(dev, "_types", lambda: called.append(True) or 0)
    assert dev.main(["static"]) == 2
    assert called == [True]


@pytest.mark.parametrize("command", ["test", "test-debug"])
def test_native_option_is_passed_to_wrapper_not_pytest(monkeypatch, command):
    calls = []
    monkeypatch.setattr(dev, "_run", lambda args: calls.append(args) or 0)
    assert dev.main([command, "--native-custody", "tests/foundation", "-k", "custody"]) == 0
    args = calls[0]
    assert args.index("--native-custody") < args.index("--")
    assert args[-3:] == ("tests/foundation", "-k", "custody")


@pytest.mark.parametrize("status", [0, 7])
def test_native_wrapper_builds_validates_and_cleans_up(tmp_path, status):
    output = tmp_path / "build-path"
    program = (
        "import os, pathlib; "
        "from services.paper_runtime.evidence import _require_native_fd_custody; "
        "_require_native_fd_custody(); "
        f"pathlib.Path({str(output)!r}).write_text(os.environ['PACKAGE6_FD_CUSTODY_EXTENSION_PATH']); "
        f"raise SystemExit({status})"
    )
    environment = os.environ.copy()
    for name in ("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256"):
        environment.pop(name, None)
    result = subprocess.run(
        [sys.executable, "scripts/run_with_trusted_test_tmp.py", "--component", "native-bootstrap-test",
         "--native-custody", "--", sys.executable, "-c", program],
        cwd=dev.ROOT, env=environment, capture_output=True, text=True,
    )
    assert result.returncode == status, result.stdout + result.stderr
    assert not Path(output.read_text()).parent.parent.exists()


def test_native_wrapper_rejects_incomplete_caller_identity(tmp_path):
    marker = tmp_path / "must-not-run"
    environment = os.environ.copy()
    environment["PACKAGE6_FD_CUSTODY_EXTENSION_PATH"] = str(tmp_path / "missing.so")
    environment.pop("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", None)
    result = subprocess.run(
        [sys.executable, "scripts/run_with_trusted_test_tmp.py", "--component", "native-bootstrap-test",
         "--native-custody", "--", sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
        cwd=dev.ROOT, env=environment, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "incomplete" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("stubborn_descendant", [False, True])
@pytest.mark.parametrize("terminate", [False, True])
def test_wrapper_reaps_children_before_cleaning_private_storage(tmp_path, stubborn_descendant, terminate):
    import signal
    import time

    ready = tmp_path / "ready"
    descendant = tmp_path / "descendant"
    spawn = ""
    if stubborn_descendant:
        child_code = (
            "import os, pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"pathlib.Path({str(descendant)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )
        spawn = (
            "import subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            f"\nwhile not pathlib.Path({str(descendant)!r}).exists(): time.sleep(0.01)\n"
        )
    program = (
        "import os, pathlib, time; "
        + spawn +
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid())+'\\n'+os.environ['TMPDIR']); "
        + ("time.sleep(60)" if terminate else "raise SystemExit(0)")
    )
    with subprocess.Popen(
        [sys.executable, "scripts/run_with_trusted_test_tmp.py", "--component", "termination-test",
         "--", sys.executable, "-c", program], cwd=dev.ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as wrapper:
        try:
            deadline = time.monotonic() + 10
            while (not ready.exists() or (stubborn_descendant and not descendant.exists())) and wrapper.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), wrapper.poll()
            pid, scratch = ready.read_text().splitlines()
            if terminate:
                wrapper.send_signal(signal.SIGTERM)
            output = wrapper.communicate(timeout=10)
            assert wrapper.returncode == (128 + signal.SIGTERM if terminate else 0), output
            assert not Path(scratch).exists()
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid), 0)
            if stubborn_descendant:
                status = Path('/proc') / descendant.read_text() / 'stat'
                deadline = time.monotonic() + 2
                while status.exists() and status.read_text().split()[2] != 'Z' and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert not status.exists() or status.read_text().split()[2] == 'Z'
        finally:
            if descendant.exists():
                try:
                    os.kill(int(descendant.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait()
