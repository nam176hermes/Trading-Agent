"""Bounded process custody for an already-consumed engine spawn."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class _BuiltEngineProcess(Protocol):
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    pass_fds: tuple[int, ...]
    close_after_spawn_fds: tuple[int, ...]
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class CapturedEngineProcess:
    """Immutable output from one completed isolated engine process."""

    stdout: bytes
    stderr: bytes
    returncode: int


def _close_descriptors(descriptors: tuple[int, ...]) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


def capture_prepared_engine_process(
    built: _BuiltEngineProcess,
    *,
    popen_factory: Callable[..., object] = subprocess.Popen,
) -> CapturedEngineProcess:
    """Spawn once, release transferred descriptors, and capture once."""

    try:
        process = popen_factory(
            built.argv,
            cwd=built.cwd,
            env=built.environment,
            pass_fds=built.pass_fds,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
    finally:
        _close_descriptors(built.close_after_spawn_fds)

    try:
        stdout, stderr = process.communicate(timeout=built.timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        try:
            process.communicate()
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        raise

    returncode = process.returncode
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise TypeError("captured engine process stdout and stderr must be bytes")
    if type(returncode) is not int:
        raise TypeError("captured engine process return code must be an integer")
    return CapturedEngineProcess(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


__all__ = ["CapturedEngineProcess", "capture_prepared_engine_process"]
