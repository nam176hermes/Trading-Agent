from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages import nautilus_backtest


def _built(**overrides):
    values = {
        "argv": ("inert-sandbox", "engine"),
        "cwd": Path("/inert-runtime"),
        "environment": {"INERT_RUNTIME": "1"},
        "pass_fds": (),
        "close_after_spawn_fds": (),
        "timeout_seconds": 13,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_capture_returns_immutable_bytes_and_zero_return_code(tmp_path: Path) -> None:
    captured = nautilus_backtest.capture_prepared_engine_process(
        _built(
            argv=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'event\\n')",
            ),
            cwd=tmp_path,
            environment=os.environ.copy(),
        )
    )

    assert captured.stdout == b"event\n"
    assert captured.stderr == b""
    assert captured.returncode == 0
    with pytest.raises((AttributeError, TypeError)):
        captured.stdout = b"changed"


def test_capture_returns_nonzero_process_completion_without_policy_rejection(
    tmp_path: Path,
) -> None:
    captured = nautilus_backtest.capture_prepared_engine_process(
        _built(
            argv=(
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stdout.buffer.write(b'partial'); "
                    "sys.stderr.buffer.write(b'failure'); "
                    "raise SystemExit(23)"
                ),
            ),
            cwd=tmp_path,
            environment=os.environ.copy(),
        )
    )

    assert (captured.returncode, captured.stdout, captured.stderr) == (
        23,
        b"partial",
        b"failure",
    )


def test_capture_timeout_kills_and_reaps_the_process_exactly_once() -> None:
    events: list[object] = []

    class Process:
        returncode = -9

        def communicate(self, *, timeout: int | None = None):
            events.append(("communicate", timeout))
            if timeout is not None:
                raise subprocess.TimeoutExpired(("inert-sandbox",), timeout)
            return b"after-kill", b"timed-out"

        def kill(self) -> None:
            events.append("kill")

    with pytest.raises(subprocess.TimeoutExpired):
        nautilus_backtest.capture_prepared_engine_process(
            _built(), popen_factory=lambda *_args, **_kwargs: Process()
        )

    assert events == [("communicate", 13), "kill", ("communicate", None)]


def test_capture_closes_transfer_descriptors_when_popen_fails() -> None:
    read_fd, write_fd = os.pipe()

    def refuse_spawn(*_args, **_kwargs):
        raise OSError("inert spawn refusal")

    try:
        with pytest.raises(OSError, match="inert spawn refusal"):
            nautilus_backtest.capture_prepared_engine_process(
                _built(
                    pass_fds=(read_fd,),
                    close_after_spawn_fds=(read_fd,),
                ),
                popen_factory=refuse_spawn,
            )
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)
        try:
            os.close(read_fd)
        except OSError:
            pass


def test_capture_closes_transfer_descriptors_before_waiting() -> None:
    read_fd, write_fd = os.pipe()

    class Process:
        returncode = 0

        def communicate(self, *, timeout: int):
            assert timeout == 13
            with pytest.raises(OSError):
                os.fstat(read_fd)
            return b"event\n", b""

    def popen(_argv, **_kwargs):
        os.fstat(read_fd)
        return Process()

    try:
        nautilus_backtest.capture_prepared_engine_process(
            _built(
                pass_fds=(read_fd,),
                close_after_spawn_fds=(read_fd,),
            ),
            popen_factory=popen,
        )
    finally:
        os.close(write_fd)
        try:
            os.close(read_fd)
        except OSError:
            pass


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    (("event\n", b""), (b"event\n", "warning")),
)
def test_capture_rejects_non_bytes_process_output(stdout, stderr) -> None:
    class Process:
        returncode = 0

        def communicate(self, *, timeout: int):
            assert timeout == 13
            return stdout, stderr

    with pytest.raises(TypeError, match="bytes"):
        nautilus_backtest.capture_prepared_engine_process(
            _built(), popen_factory=lambda *_args, **_kwargs: Process()
        )


def test_capture_spawns_and_waits_exactly_once_with_built_authority() -> None:
    built = _built()
    events: list[object] = []

    class Process:
        returncode = 0

        def communicate(self, *, timeout: int):
            events.append(("communicate", timeout))
            return b"event\n", b""

    def popen(argv, **kwargs):
        events.append(("popen", argv, kwargs))
        return Process()

    nautilus_backtest.capture_prepared_engine_process(
        built, popen_factory=popen
    )

    assert events == [
        (
            "popen",
            built.argv,
            {
                "cwd": built.cwd,
                "env": built.environment,
                "pass_fds": built.pass_fds,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "close_fds": True,
            },
        ),
        ("communicate", 13),
    ]
