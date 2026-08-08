#!/usr/bin/python3.12
"""Direct, stdlib-only sealed dependency import qualification probe."""

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import re
import stat
import sys
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


_PYTHON = "/usr/bin/python3.12"
_PROBE = "/qualification/import_probe.py"
_ENTRY_LAUNCHER = "/qualification/entry-launcher.py"
_WHEEL_DIRECTORY = "/engine/wheels"
_COMMAND = (
    _PYTHON,
    "-I",
    "-S",
    _PROBE,
    "--entry-launcher",
    _ENTRY_LAUNCHER,
    "--wheel-directory",
    _WHEEL_DIRECTORY,
)
_MODULE_NAMES = ("nautilus_trader", "numpy", "pandas")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.!+_-]{0,127}$", re.ASCII)


class ImportProbeError(ValueError):
    """The qualification process or imported dependency graph is not sealed."""


@dataclass(frozen=True)
class _DirectEntryFacts:
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


def _kernel_arguments() -> tuple[bytes, ...]:
    descriptor = -1
    try:
        descriptor = os.open(
            "/proc/self/cmdline",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
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


def _validate_direct_entry(facts: _DirectEntryFacts) -> None:
    expected_kernel = tuple(os.fsencode(value) for value in _COMMAND)
    if (
        facts.module_name != "__main__"
        or facts.module_spec is not None
        or facts.module_file != _PROBE
        or facts.implementation_name != "cpython"
        or facts.version != (3, 12)
        or facts.isolated != 1
        or facts.no_site != 1
        or facts.ignore_environment != 1
        or facts.no_user_site != 1
        or facts.safe_path is not True
        or facts.orig_argv != _COMMAND
        or facts.argv != _COMMAND[3:]
        or facts.kernel_argv != expected_kernel
    ):
        raise ImportProbeError("probe requires the fixed direct isolated CPython entry")


def _require_direct_entry() -> None:
    _validate_direct_entry(
        _DirectEntryFacts(
            module_name=__name__,
            module_spec=__spec__,
            module_file=__file__,
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
        )
    )


def _require_stdlib_only_path() -> None:
    stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    platstdlib = Path(sysconfig.get_path("platstdlib")).resolve(strict=True)
    allowed = {
        stdlib,
        platstdlib,
        stdlib.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip",
    }
    if shared := sysconfig.get_config_var("DESTSHARED"):
        allowed.add(Path(shared).resolve(strict=True))
    try:
        observed = tuple(Path(value).resolve() for value in sys.path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ImportProbeError("probe sys.path is not stdlib-only") from exc
    if (
        not observed
        or len(observed) != len(sys.path)
        or len(observed) != len(set(observed))
        or any(path not in allowed for path in observed)
    ):
        raise ImportProbeError("probe sys.path is not stdlib-only")


def _load_launcher(path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "_sealed_import_qualification_launcher", path
    )
    if specification is None or specification.loader is None:
        raise ImportProbeError("reviewed entry launcher cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
    except Exception as exc:
        raise ImportProbeError("reviewed entry launcher import failed") from exc
    return module


def _regular_digest(path: Path, *, expected_mode: int | None = None) -> str:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            expected_mode is not None
            and stat.S_IMODE(opened.st_mode) != expected_mode
        ):
            raise ImportProbeError("sealed qualification file identity is invalid")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        named = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ImportProbeError("sealed qualification file identity changed")
        return digest.hexdigest()
    except ImportProbeError:
        raise
    except OSError as exc:
        raise ImportProbeError("sealed qualification file cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _module_record(
    name: str,
    module: object,
    roots: tuple[Path, ...],
    wheel_digests: dict[Path, str],
) -> dict[str, str]:
    origin_value = getattr(module, "__file__", None)
    version = getattr(module, "__version__", None)
    if (
        not isinstance(origin_value, str)
        or not isinstance(version, str)
        or _VERSION.fullmatch(version) is None
    ):
        raise ImportProbeError(f"{name} did not expose a sealed wheel identity")
    try:
        origin = Path(origin_value).resolve(strict=True)
        matches = tuple(root for root in roots if origin.is_relative_to(root))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ImportProbeError(f"{name} did not originate in a sealed wheel") from exc
    if len(matches) != 1 or matches[0] not in wheel_digests:
        raise ImportProbeError(f"{name} did not originate in a sealed wheel")
    return {
        "name": name,
        "version": version,
        "source_wheel_sha256": wheel_digests[matches[0]],
    }


def _probe_import_graph(entry_launcher: Path, wheel_directory: Path) -> dict[str, object]:
    _regular_digest(entry_launcher, expected_mode=0o400)
    launcher = _load_launcher(entry_launcher)
    try:
        extract = launcher._extract_sealed_wheels
        scope = launcher._sealed_dependency_path_scope
        load_strategy = launcher._load_target_portfolio_strategy
        strategy_path = Path(launcher._TARGET_PORTFOLIO_STRATEGY_PATH)
    except (AttributeError, TypeError) as exc:
        raise ImportProbeError("reviewed simulation launcher helpers are unavailable") from exc

    wheels = tuple(sorted(wheel_directory.glob("*.whl"), key=lambda item: item.name))
    if not wheels:
        raise ImportProbeError("sealed wheel inventory is empty")
    wheel_hashes = tuple(_regular_digest(path, expected_mode=0o400) for path in wheels)
    with tempfile.TemporaryDirectory(prefix="nautilus-import-probe-", dir="/tmp") as temp:
        extraction_root = Path(temp) / "wheels"
        try:
            roots = tuple(path.resolve(strict=True) for path in extract(wheel_directory, extraction_root))
            if len(roots) != len(wheels) or len(roots) != len(set(roots)):
                raise ImportProbeError("sealed wheel extraction inventory drifted")
            wheel_digests = dict(zip(roots, wheel_hashes, strict=True))
            with scope(roots):
                modules = [
                    _module_record(name, importlib.import_module(name), roots, wheel_digests)
                    for name in _MODULE_NAMES
                ]
                load_strategy()
        except ImportProbeError:
            raise
        except Exception as exc:
            raise ImportProbeError("sealed dependency import qualification failed") from exc

    return {
        "schema_version": "nautilus-sealed-import-probe-v1",
        "status": "passed",
        "modules": sorted(modules, key=lambda record: record["name"]),
        "strategy_source_sha256": _regular_digest(strategy_path, expected_mode=0o400),
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _write_all(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise ImportProbeError("probe stdout write made no progress")
        remaining = remaining[written:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry-launcher", required=True, type=Path)
    parser.add_argument("--wheel-directory", required=True, type=Path)
    arguments = parser.parse_args()
    _require_direct_entry()
    _require_stdlib_only_path()
    document = _probe_import_graph(arguments.entry_launcher, arguments.wheel_directory)
    _write_all(sys.stdout.fileno(), _canonical(document) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
