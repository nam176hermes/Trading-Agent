"""PR03: the framed paper transport is bounded by one deadline, drains stderr, and parses frames incrementally.

Children are real Python processes owned by each test. Attestation is bypassed only through
`matches_authority`; the transport code under test is the production method. A SIGALRM guard turns a hang
into a bounded failure; each test reaps its child.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import signal
import subprocess
import sys

import pytest

from engines.nautilus.runtime_v1.control_channel import MAX_FRAME_BYTES, frame_payload
from services.paper_runtime import nautilus_process as process_module
from services.paper_runtime.nautilus_process import NautilusPaperProcess

CHECKPOINT = json.dumps({"frame_type": "CHECKPOINT"}).encode()
EVENT = json.dumps({"frame_type": "EVENT"}).encode()
COMMAND = b'{"command":"start"}'


class HungTransport(Exception):
    pass


@pytest.fixture
def guard() -> Iterator[None]:
    def expire(_signum, _frame):
        raise HungTransport("transport did not finish within the test guard")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, 8)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def children() -> Iterator[list[subprocess.Popen[bytes]]]:
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for child in started:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def _spawn(children, body: str) -> NautilusPaperProcess:
    script = "import os, sys, time\nout = sys.stdout.buffer\nerr = sys.stderr.buffer\n" + body
    child = subprocess.Popen(
        [sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    children.append(child)
    value = object.__new__(NautilusPaperProcess)
    object.__setattr__(value, "_process", child)
    object.__setattr__(value, "_closure", None)
    object.__setattr__(value, "_request", None)
    for name in ("_stdout_pending", "_stderr_tail"):
        if name in NautilusPaperProcess.__slots__:
            object.__setattr__(value, name, bytearray())
    return value


@pytest.fixture(autouse=True)
def _attested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NautilusPaperProcess, "matches_authority", lambda self, closure, request: True)
    monkeypatch.setattr(process_module, "_EXCHANGE_SECONDS", 1.0, raising=False)


READ_COMMAND = "header = sys.stdin.buffer.read(4)\nsys.stdin.buffer.read(int.from_bytes(header, 'big'))\n"


def test_exchange_times_out_without_checkpoint(guard, children) -> None:
    process = _spawn(children, READ_COMMAND + "time.sleep(60)\n")
    with pytest.raises(TimeoutError):
        process.exchange(COMMAND)
    assert children[0].poll() is not None                      # killed and reaped by the transport


def test_exchange_drains_stderr_with_bounded_retention(guard, children) -> None:
    process = _spawn(children, "err.write(b'x' * (2 * 1024 * 1024) + b'END'); err.flush()\n" + READ_COMMAND
                     + f"out.write({_frame(CHECKPOINT)!r}); out.flush()\n")
    assert process.exchange(COMMAND) == _frame(CHECKPOINT)
    assert len(process._stderr_tail) <= 65536 and bytes(process._stderr_tail).endswith(b"END")


def test_exchange_handles_partial_header_and_payload(guard, children) -> None:
    frames = _frame(EVENT) + _frame(CHECKPOINT)
    process = _spawn(children, READ_COMMAND + f"data = {frames!r}\n"
                     "for index in range(len(data)):\n    out.write(data[index:index + 1]); out.flush(); time.sleep(0.001)\n")
    assert process.exchange(COMMAND) == frames


def test_exchange_deadline_includes_blocked_stdin(guard, children) -> None:
    process = _spawn(children, "time.sleep(60)\n")               # never reads stdin
    with pytest.raises(TimeoutError):
        process.exchange(b"{" + b"x" * (MAX_FRAME_BYTES - 2) + b"}")   # a full frame exceeds the pipe buffer
    assert children[0].poll() is not None


@pytest.mark.parametrize(("output", "message"), [
    (_frame(EVENT)[:5], "truncated"),                                       # EOF inside a frame
    ((MAX_FRAME_BYTES + 1).to_bytes(4, "big"), "oversized"),
    (_frame(EVENT) * 64 + _frame(CHECKPOINT), "maximum frames"),             # 65 frames
])
def test_exchange_rejects_truncated_or_oversized_response(guard, children, output: bytes, message: str) -> None:
    process = _spawn(children, READ_COMMAND + f"out.write({output!r}); out.flush()\n")
    with pytest.raises(RuntimeError, match=message):
        process.exchange(COMMAND)
    children[0].wait(timeout=10)


def test_close_input_drains_stderr_and_reaps_child(guard, children) -> None:
    process = _spawn(children, "sys.stdin.buffer.read()\nerr.write(b'y' * (2 * 1024 * 1024)); err.flush()\n")
    assert process.close_input() == 0
    assert children[0].poll() == 0


def test_frame_payload_bound_is_unchanged() -> None:
    assert MAX_FRAME_BYTES == 65_536
    assert frame_payload(b"x")[:4] == (1).to_bytes(4, "big")


def test_close_input_rejects_unbounded_stdout_after_end_of_input(guard, children) -> None:
    """R3 REVISE: stdout after end of input is retained only up to one frame; more is a contract violation."""
    process = _spawn(children, "sys.stdin.buffer.read()\nout.write(b'z' * (8 * 1024 * 1024)); out.flush()\n")
    with pytest.raises(RuntimeError, match="after end of input"):
        process.close_input()
    assert children[0].poll() is not None
    assert len(process._stdout_pending) <= 4 + MAX_FRAME_BYTES
