"""Fail-closed entry and lineage checks for the sealed P1 runtime."""

from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass

from .profile import P1_REAL_BACKTEST_PROFILE


PYTHON = "/usr/bin/python3.12"
MAIN = "/engine/runtime_v1/main.py"
REQUEST = "/inputs/request.json"
SIDECAR = "/inputs/request.sha256"
COMMAND = (
    PYTHON,
    "-I",
    "-S",
    MAIN,
    "--profile",
    P1_REAL_BACKTEST_PROFILE,
    REQUEST,
    SIDECAR,
)
_ENGINE_VERSION = "1.231.0"
_LINEAGE_KEYS = {
    "profile_manifest_schema_version",
    "runtime_family",
    "engine_version",
    "profile",
    "event_schema",
    "closure_sha256",
    "runtime_inventory_sha256",
}
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)


class RuntimeBootstrapError(ValueError):
    """The process did not enter through the reviewed sealed boundary."""


@dataclass(frozen=True)
class EntryFacts:
    module_name: str
    module_spec: object
    module_file: str
    implementation_name: str
    version: tuple[int, int]
    isolated: int
    no_site: int
    ignore_environment: int
    no_user_site: int
    safe_path: bool
    orig_argv: tuple[str, ...]
    argv: tuple[str, ...]
    kernel_argv: tuple[bytes, ...]
    environment: tuple[tuple[str, str], ...]
    request_mode: int
    sidecar_mode: int


def _kernel_arguments() -> tuple[bytes, ...]:
    descriptor = -1
    try:
        descriptor = os.open(
            "/proc/self/cmdline",
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        chunks: list[bytes] = []
        total = 0
        while block := os.read(descriptor, 4096):
            total += len(block)
            if total > 1_048_576:
                return ()
            chunks.append(block)
        raw = b"".join(chunks)
        if not raw or not raw.endswith(b"\0"):
            return ()
        return tuple(raw[:-1].split(b"\0"))
    except OSError:
        return ()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def validate_entry(facts: EntryFacts) -> None:
    if (
        facts.module_name != "__main__"
        or facts.module_spec is not None
        or facts.module_file != MAIN
        or facts.implementation_name != "cpython"
        or facts.version != (3, 12)
        or facts.isolated != 1
        or facts.no_site != 1
        or facts.ignore_environment != 1
        or facts.no_user_site != 1
        or facts.safe_path is not True
        or facts.orig_argv != COMMAND
        or facts.argv != COMMAND[3:]
        or facts.kernel_argv != tuple(os.fsencode(value) for value in COMMAND)
        or facts.environment
        or not stat.S_ISREG(facts.request_mode)
        or not stat.S_ISREG(facts.sidecar_mode)
    ):
        raise RuntimeBootstrapError("runtime requires the fixed isolated entry")


def require_runtime_entry(
    *, module_name: str, module_spec: object, module_file: str
) -> None:
    try:
        request_mode = os.stat(REQUEST, follow_symlinks=False).st_mode
        sidecar_mode = os.stat(SIDECAR, follow_symlinks=False).st_mode
    except OSError as exc:
        raise RuntimeBootstrapError("runtime input identity is unavailable") from exc
    validate_entry(
        EntryFacts(
            module_name=module_name,
            module_spec=module_spec,
            module_file=module_file,
            implementation_name=sys.implementation.name,
            version=(sys.version_info.major, sys.version_info.minor),
            isolated=sys.flags.isolated,
            no_site=sys.flags.no_site,
            ignore_environment=sys.flags.ignore_environment,
            no_user_site=sys.flags.no_user_site,
            safe_path=sys.flags.safe_path,
            orig_argv=tuple(sys.orig_argv),
            argv=tuple(sys.argv),
            kernel_argv=_kernel_arguments(),
            environment=tuple(os.environ.items()),
            request_mode=request_mode,
            sidecar_mode=sidecar_mode,
        )
    )


def require_engine_version(version: object) -> None:
    if type(version) is not str or version != _ENGINE_VERSION:
        raise RuntimeBootstrapError("runtime engine version is not accepted")


def require_product_lineage(
    observed: object, *, expected: dict[str, object]
) -> None:
    valid_expected = (
        type(expected) is dict
        and set(expected) == _LINEAGE_KEYS
        and type(expected.get("profile_manifest_schema_version")) is int
        and expected["profile_manifest_schema_version"] == 8
        and expected.get("runtime_family") == "cython-v1"
        and expected.get("engine_version") == _ENGINE_VERSION
        and expected.get("profile") == P1_REAL_BACKTEST_PROFILE
        and expected.get("event_schema") == "nautilus-p1-event-stream-v1"
        and all(
            type(expected.get(name)) is str
            and _DIGEST.fullmatch(expected[name]) is not None
            for name in ("closure_sha256", "runtime_inventory_sha256")
        )
    )
    if not valid_expected or type(observed) is not dict or observed != expected:
        raise RuntimeBootstrapError("runtime product lineage is not accepted")
