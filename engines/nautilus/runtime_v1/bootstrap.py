"""Fail-closed entry and lineage checks for the sealed P1 runtime."""

from __future__ import annotations

import os
import json
import re
import stat
import sys
from dataclasses import dataclass

from .profile import P1_REAL_BACKTEST_PROFILE


PYTHON = "/usr/bin/python3.12"
MAIN = "/engine/runtime_v1/main.py"
REQUEST = "/inputs/request.json"
SIDECAR = "/inputs/request.sha256"
LINEAGE = "/engine/p1-product-lineage.json"
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
_ANONYMOUS_MOUNTS = {REQUEST, SIDECAR, LINEAGE}


def _expected_link_count(path: str) -> int:
    return 0 if path in _ANONYMOUS_MOUNTS else 1
_SIDECAR_DIGEST = re.compile(rb"[0-9a-f]{64}\n", re.ASCII)
_EXPECTED_ENVIRONMENT: tuple[tuple[str, str], ...] = ()


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
    cwd: str
    request_mode: int
    sidecar_mode: int
    sidecar_bytes: bytes


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


def _read_regular(path: str, maximum: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        opened = os.fstat(descriptor)
        expected_links = _expected_link_count(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != expected_links
            or opened.st_size <= 0
            or opened.st_size > maximum
        ):
            raise RuntimeBootstrapError("runtime fixed file identity is invalid")
        chunks: list[bytes] = []
        total = 0
        while block := os.read(descriptor, min(4096, maximum + 1 - total)):
            total += len(block)
            if total > maximum:
                raise RuntimeBootstrapError("runtime fixed file is oversized")
            chunks.append(block)
        named = os.stat(path, follow_symlinks=False)
        final = os.fstat(descriptor)
        if (
            not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            or named.st_nlink != expected_links
            or named.st_size != opened.st_size
            or final.st_nlink != expected_links
            or final.st_size != opened.st_size
        ):
            raise RuntimeBootstrapError("runtime fixed file identity changed")
        return b"".join(chunks)
    except RuntimeBootstrapError:
        raise
    except OSError as exc:
        raise RuntimeBootstrapError("runtime fixed file is unavailable") from exc
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
        or facts.environment != _EXPECTED_ENVIRONMENT
        or facts.cwd != "/"
        or not stat.S_ISREG(facts.request_mode)
        or not stat.S_ISREG(facts.sidecar_mode)
        or _SIDECAR_DIGEST.fullmatch(facts.sidecar_bytes) is None
    ):
        raise RuntimeBootstrapError("runtime requires the fixed isolated entry")


def require_runtime_entry(
    *, module_name: str, module_spec: object, module_file: str
) -> None:
    try:
        request_mode = os.stat(REQUEST, follow_symlinks=False).st_mode
        sidecar_mode = os.stat(SIDECAR, follow_symlinks=False).st_mode
        sidecar_bytes = _read_regular(SIDECAR, 65)
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
            environment=tuple(sorted(os.environ.items())),
            cwd=os.getcwd(),
            request_mode=request_mode,
            sidecar_mode=sidecar_mode,
            sidecar_bytes=sidecar_bytes,
        )
    )


def require_engine_version(version: object) -> None:
    if type(version) is not str or version != _ENGINE_VERSION:
        raise RuntimeBootstrapError("runtime engine version is not accepted")


def require_product_lineage(observed: object) -> None:
    valid = (
        type(observed) is dict
        and set(observed) == _LINEAGE_KEYS
        and type(observed.get("profile_manifest_schema_version")) is int
        and observed["profile_manifest_schema_version"] == 8
        and type(observed.get("runtime_family")) is str
        and observed["runtime_family"] == "cython-v1"
        and type(observed.get("engine_version")) is str
        and observed["engine_version"] == _ENGINE_VERSION
        and type(observed.get("profile")) is str
        and observed["profile"] == P1_REAL_BACKTEST_PROFILE
        and type(observed.get("event_schema")) is str
        and observed["event_schema"] == "nautilus-p1-event-stream-v1"
        and all(
            type(observed.get(name)) is str
            and _DIGEST.fullmatch(observed[name]) is not None
            for name in ("closure_sha256", "runtime_inventory_sha256")
        )
    )
    if not valid:
        raise RuntimeBootstrapError("runtime product lineage is not accepted")


def load_product_lineage() -> dict[str, object]:
    try:
        raw = _read_regular(LINEAGE, 1024)

        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in items:
                if key in value:
                    raise RuntimeBootstrapError("runtime product lineage is not accepted")
                value[key] = item
            return value

        def reject_number(_value: str) -> object:
            raise RuntimeBootstrapError("runtime product lineage is not accepted")

        observed = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_float=reject_number,
            parse_constant=reject_number,
        )
        if (
            type(observed) is not dict
            or raw
            != (
                json.dumps(
                    observed,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode()
        ):
            raise RuntimeBootstrapError("runtime product lineage is not accepted")
        require_product_lineage(observed)
        return observed
    except RuntimeBootstrapError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeBootstrapError("runtime product lineage is not accepted") from exc
