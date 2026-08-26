#!/usr/bin/env python3
"""Materialize one new sealed Nautilus simulation closure from reviewed inputs.

This tool has no acquisition mode. It copies an exact sealed runtime inventory,
replaces the repository launcher and selected input-bound Nautilus wheel, builds
the policy-bound native entry guard from sealed private toolchains, root-attests
the sealed staging tree, then atomically publishes and re-attests the same inode
tree at a previously absent destination.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from pathlib import Path, PurePath, PurePosixPath
from typing import NoReturn, Sequence

sys.dont_write_bytecode = True

# Direct execution puts ``scripts/`` first on sys.path. Bootstrap the checkout
# root before importing the controller attestor; no ambient installation is
# allowed to satisfy this materializer's authority dependency.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.job_worker.engine_spawn_interface import EngineSpawnError
from services.job_worker.nautilus_closure import (
    NautilusClosureConfig,
    attest_nautilus_backtest_closure,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SOURCE_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_INPUT_CACHE_TOOL = _ROOT / "scripts/prepare_nautilus_input_cache.py"
_RUST_TOOLCHAIN_TOOL = _ROOT / "scripts/prepare_nautilus_toolchain.py"
_RUST_TOOLCHAIN_POLICY = _ROOT / "engines/nautilus/toolchain-inputs.json"
_LLVM_TOOLCHAIN_TOOL = _ROOT / "scripts/prepare_nautilus_llvm_toolchain.py"
_LLVM_TOOLCHAIN_POLICY = _ROOT / "engines/nautilus/llvm-toolchain-policy.json"
_POLICY_FIELDS = {
    "argv_prefix",
    "artifact_manifest_sha256",
    "base_file_count",
    "base_file_inventory_sha256",
    "base_runtime_manifest_sha256",
    "dependency_import_policy",
    "engine_name",
    "engine_upstream_commit",
    "engine_version",
    "engine_wheel_mode",
    "engine_wheel_target",
    "entrypoint",
    "launcher_inventory",
    "native_entry_guard",
    "profile",
    "profile_manifest_schema_version",
    "python_identity",
    "result_validator_id",
    "schema_version",
    "semantic_profile",
    "source_commit",
    "timeout_seconds",
}
_NATIVE_GUARD_POLICY_FIELDS = {
    "binary_sha256",
    "binary_size",
    "cargo_identity",
    "cargo_lock",
    "cargo_lock_sha256",
    "cargo_manifest",
    "cargo_manifest_sha256",
    "llvm_toolchain_policy_sha256",
    "mode",
    "rust_toolchain_policy_sha256",
    "rustc_identity",
    "source",
    "source_sha256",
    "target",
    "target_triple",
}
_NATIVE_GUARD_PROVENANCE_FIELDS = {
    "binary_sha256",
    "binary_size",
    "cargo_identity",
    "cargo_lock",
    "cargo_lock_sha256",
    "cargo_manifest",
    "cargo_manifest_sha256",
    "llvm_toolchain_policy_sha256",
    "mode",
    "rust_toolchain_policy_sha256",
    "rustc_identity",
    "source",
    "source_sha256",
    "target",
    "target_triple",
}
_BASE_MANIFEST_FIELDS = {
    "argv_prefix",
    "artifact_manifest_sha256",
    "engine_name",
    "engine_version",
    "entrypoint",
    "files",
    "python_identity",
    "result_validator_id",
    "schema_version",
    "source_commit",
    "timeout_seconds",
}
_FILE_FIELDS = {"mode", "path", "sha256", "size", "target"}
_ARTIFACT_MANIFEST = "artifact-manifest.json"
_CLOSURE_MANIFEST = "closure-manifest.json"
_SIMULATION_LAUNCHER_INVENTORY = (
    ("engines/nautilus/launcher/nautilus_backtest.py", "/engine/launcher/nautilus_backtest.py"),
    ("engines/nautilus/launcher/target_portfolio_strategy.py", "/engine/launcher/target_portfolio_strategy.py"),
)
_PAPER_LAUNCHER_INVENTORY = (
    ("engines/nautilus/launcher/nautilus_paper_compat.py", "/engine/launcher/nautilus_paper_compat.py"),
    *_SIMULATION_LAUNCHER_INVENTORY,
)
_BASE_LAUNCHER_TARGET = _SIMULATION_LAUNCHER_INVENTORY[0][1]
_NATIVE_GUARD_SOURCE = "engines/nautilus/native_entry_guard/src/main.rs"
_NATIVE_GUARD_CARGO_MANIFEST = "engines/nautilus/native_entry_guard/Cargo.toml"
_NATIVE_GUARD_CARGO_LOCK = "engines/nautilus/native_entry_guard/Cargo.lock"
_NATIVE_GUARD_TARGET = "/engine/bin/nautilus-entry-guard"
_NATIVE_GUARD_TARGET_TRIPLE = "x86_64-unknown-linux-gnu"
_NATIVE_GUARD_BINARY_NAME = "nautilus-entry-guard"
_EXPECTED_CARGO_IDENTITY = "cargo 1.95.0 (f2d3ce0bd 2026-03-21)"
_EXPECTED_RUSTC_IDENTITY = "rustc 1.95.0 (59807616e 2026-04-14)"
_PROFILE_SPECS = {
    "execution-simulation": {
        "argv_prefix": (
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ),
        "launcher_inventory": _SIMULATION_LAUNCHER_INVENTORY,
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "semantic_profile": "nautilus-execution-simulation-v2",
    },
    "paper-compatibility": {
        "argv_prefix": (
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_paper_compat.py",
            "--profile",
            "paper-compatibility",
        ),
        "launcher_inventory": _PAPER_LAUNCHER_INVENTORY,
        "result_validator_id": "nautilus-paper-compatibility-result-v1",
        "semantic_profile": "nautilus-paper-compatibility-v1",
    },
}
_REPOSITORY_LAUNCHER_SOURCES = {
    _ROOT / source
    for specification in _PROFILE_SPECS.values()
    for source, _target in specification["launcher_inventory"]
}
_DEPENDENCY_IMPORT_POLICY = (
    "native-guarded-stdlib-first-sealed-wheel-path-v1"
)
_RENAME_NOREPLACE = 1
_CANDIDATE_BASE_POLICY = _ROOT / "engines/nautilus/runtime-closure-policy.json"
_CANDIDATE_BUILDER = _ROOT / "scripts/build_nautilus_engine.py"
_CANDIDATE_SANDBOX = Path("/usr/bin/bwrap")
_SELECTED_RUNTIME_GENERATION = "runtime-closure-v12-r12-simulation"
_SELECTED_ARTIFACT_GENERATION = (
    "artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c"
)
_SELECTED_POLICY_SHA256 = (
    "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2"
)
_SELECTED_MANIFEST_SHA256 = (
    "b143564cf3ad63b4ca01afb9a27e7496c9b1c6ff1f3c46cf10b6c4a047545d20"
)
_SELECTED_CLOSURE_SHA256 = (
    "14d4fd990dccfdbb8b6dfe964a04ae9e80fefb30914cf433de1bc503b8ad03fa"
)
_SELECTED_ARTIFACT_MANIFEST_SHA256 = (
    "105579383ea3c5e44104bbe162ab78380f7abb5654e15ac3b600beee54ed93d2"
)
_CANDIDATE_IMPORT_SCRIPT = (
    "import pathlib,sys,tempfile,zipfile; "
    "wheel_root=pathlib.Path('/engine/wheels'); target=pathlib.Path(tempfile.mkdtemp(dir='/tmp')); "
    "roots=[]; "
    "[(lambda d,w:(d.mkdir(),zipfile.ZipFile(w).extractall(d),roots.append(str(d))))(target/str(i),w) for i,w in enumerate(sorted(wheel_root.glob('*.whl')))]; "
    "sys.path[:0]=roots; import nautilus_trader; "
    "assert nautilus_trader.__version__=='1.231.0'; print(nautilus_trader.__version__)"
)
_CANDIDATE_ARTIFACT_FIELDS = {
    "activation_status",
    "engine",
    "manifest_kind",
    "native_libraries",
    "network",
    "policy_hashes",
    "python",
    "reproducible_build",
    "runtime_wheels",
    "schema_version",
    "source",
    "toolchain",
    "wheel",
}
_CANDIDATE_ENGINE_FIELDS = {"name", "upstream_commit", "upstream_tag", "version"}
_CANDIDATE_PYTHON_FIELDS = {
    "abi",
    "executable_sha256",
    "identity",
    "stdlib_tree_sha256",
}
_CANDIDATE_TOOLCHAIN_FIELDS = {
    "cargo_identity",
    "command_router_authority",
    "llvm_version",
    "rustc_identity",
}
_CANDIDATE_WHEEL_FIELDS = {"filename", "sha256", "size"}
_CANDIDATE_REPRODUCIBILITY_FIELDS = {
    "authoritative_manifest_equality",
    "build_a_receipt_sha256",
    "build_b_receipt_sha256",
    "build_count",
    "fresh_physical_stages",
    "logical_stages_absent_after_build",
    "native_inventory_equality",
    "raw_wheel_equality",
    "process_identities",
    "source_fd_identities",
    "wheel_sha256",
    "x4_authority_receipt_sha256",
}
_CANDIDATE_SOURCE_FD_FIELDS = {
    "P1_U04_SOURCE_ST_DEV",
    "P1_U04_SOURCE_ST_INO",
}
_CANDIDATE_PROCESS_IDENTITY_FIELDS = {"boot_id", "pid", "start_time_ticks"}
_CANDIDATE_CLOSURE_FIELDS = {
    "activation_status",
    "artifact_manifest_sha256",
    "base_runtime",
    "engine",
    "file_inventory_sha256",
    "files",
    "loader_assumptions",
    "manifest_kind",
    "native_inventory_sha256",
    "native_libraries",
    "network",
    "policy_hashes",
    "python",
    "qualification",
    "runtime_wheels",
    "schema_version",
    "source",
    "toolchain",
}
_CANDIDATE_BASE_HISTORICAL_FIELDS = {
    "engine_name",
    "engine_version",
    "file_count",
    "file_inventory_sha256",
    "manifest_sha256",
    "non_engine_file_count",
    "non_engine_file_inventory_sha256",
    "python_identity",
    "schema_version",
    "source_commit",
}


class RuntimeClosureMaterializationError(ValueError):
    """A reviewed runtime-closure input or atomic publication is invalid."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_file(path: Path, *, label: str, sealed: bool) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        observed = os.fstat(descriptor)
        mode = stat.S_IMODE(observed.st_mode)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or observed.st_nlink != 1
            or (sealed and mode not in {0o400, 0o500})
        ):
            raise RuntimeClosureMaterializationError(
                f"{label} has an unsafe mode or immutable-file identity"
            )
        chunks: list[bytes] = []
        while block := os.read(descriptor, 1024 * 1024):
            chunks.append(block)
        return b"".join(chunks)
    except RuntimeClosureMaterializationError:
        raise
    except OSError as exc:
        raise RuntimeClosureMaterializationError(f"{label} cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_build_output(path: Path) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or not observed.st_mode & stat.S_IXUSR
            or observed.st_mode & 0o022
        ):
            raise RuntimeClosureMaterializationError(
                "built native entry guard identity is unsafe"
            )
        chunks: list[bytes] = []
        while block := os.read(descriptor, 1024 * 1024):
            chunks.append(block)
        return b"".join(chunks)
    except RuntimeClosureMaterializationError:
        raise
    except OSError as exc:
        raise RuntimeClosureMaterializationError(
            "built native entry guard cannot be read"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeClosureMaterializationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeClosureMaterializationError(f"{label} must be an object")
    return value


def _sealed_directory(path: Path, *, label: str) -> None:
    try:
        observed = path.lstat()
    except OSError as exc:
        raise RuntimeClosureMaterializationError(f"{label} is unavailable") from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
        or stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o500
    ):
        raise RuntimeClosureMaterializationError(
            f"{label} is not a sealed private directory"
        )


def _authority_directory_identity(path: Path, *, label: str) -> tuple[int, int]:
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        raise RuntimeClosureMaterializationError(f"{label} path is unsafe")
    current = path
    while True:
        try:
            observed = current.lstat()
        except OSError as exc:
            raise RuntimeClosureMaterializationError(
                f"{label} has a missing ancestor"
            ) from exc
        if stat.S_ISLNK(observed.st_mode):
            raise RuntimeClosureMaterializationError(
                f"{label} has a symlinked ancestor"
            )
        if not stat.S_ISDIR(observed.st_mode):
            raise RuntimeClosureMaterializationError(
                f"{label} has a non-directory ancestor"
            )
        if current == path:
            identity = (observed.st_dev, observed.st_ino)
        if current == current.parent:
            return identity
        current = current.parent


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeClosureMaterializationError(f"{label} digest is invalid")
    return value


def _safe_relative(value: object, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise RuntimeClosureMaterializationError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeClosureMaterializationError(f"{label} is unsafe")
    return path


def _safe_target(value: object, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise RuntimeClosureMaterializationError(f"{label} is invalid")
    path = PurePosixPath(value)
    if not path.is_absolute() or path == PurePosixPath("/") or ".." in path.parts:
        raise RuntimeClosureMaterializationError(f"{label} is unsafe")
    return path


def _load_local_tool(path: Path, name: str):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeClosureMaterializationError(
            "native guard build verifier is unavailable"
        )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _reject_ambient_cargo_configuration(path: Path) -> None:
    for directory in (path, *path.parents):
        cargo_directory = directory / ".cargo"
        if cargo_directory.is_symlink():
            raise RuntimeClosureMaterializationError(
                "native entry guard build found ambient Cargo configuration"
            )
        if any(
            candidate.exists() or candidate.is_symlink()
            for candidate in (
                cargo_directory / "config",
                cargo_directory / "config.toml",
            )
        ):
            raise RuntimeClosureMaterializationError(
                "native entry guard build found ambient Cargo configuration"
            )


def _native_guard_provenance(
    guard: dict[str, object],
) -> dict[str, object]:
    return {field: guard[field] for field in sorted(_NATIVE_GUARD_PROVENANCE_FIELDS)}


def _validate_native_guard_policy(
    value: object,
    *,
    source_reader: Callable[[Path, str], bytes] | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _NATIVE_GUARD_POLICY_FIELDS:
        raise RuntimeClosureMaterializationError(
            "native entry guard policy fields are missing or unknown"
        )
    if (
        value["cargo_lock"] != _NATIVE_GUARD_CARGO_LOCK
        or value["cargo_manifest"] != _NATIVE_GUARD_CARGO_MANIFEST
        or value["source"] != _NATIVE_GUARD_SOURCE
        or value["target"] != _NATIVE_GUARD_TARGET
        or value["target_triple"] != _NATIVE_GUARD_TARGET_TRIPLE
        or value["mode"] != "0500"
        or value["cargo_identity"] != _EXPECTED_CARGO_IDENTITY
        or value["rustc_identity"] != _EXPECTED_RUSTC_IDENTITY
        or isinstance(value["binary_size"], bool)
        or not isinstance(value["binary_size"], int)
        or int(value["binary_size"]) <= 0
    ):
        raise RuntimeClosureMaterializationError(
            "native entry guard policy identity is invalid"
        )
    for field in (
        "binary_sha256",
        "cargo_lock_sha256",
        "cargo_manifest_sha256",
        "llvm_toolchain_policy_sha256",
        "rust_toolchain_policy_sha256",
        "source_sha256",
    ):
        _require_sha256(value[field], label=f"native entry guard {field}")
    if source_reader is None:
        source_reader = lambda path, label: _read_file(
            path,
            label=label,
            sealed=False,
        )
    for path_field, digest_field in (
        ("source", "source_sha256"),
        ("cargo_manifest", "cargo_manifest_sha256"),
        ("cargo_lock", "cargo_lock_sha256"),
    ):
        relative = _safe_relative(value[path_field], label=path_field)
        raw = source_reader(
            _ROOT.joinpath(*relative.parts),
            f"native entry guard {path_field}",
        )
        if _sha256_bytes(raw) != value[digest_field]:
            raise RuntimeClosureMaterializationError(
                f"native entry guard {path_field} digest drifted"
            )
    for policy_path, field in (
        (_RUST_TOOLCHAIN_POLICY, "rust_toolchain_policy_sha256"),
        (_LLVM_TOOLCHAIN_POLICY, "llvm_toolchain_policy_sha256"),
    ):
        raw = source_reader(policy_path, "native build toolchain policy")
        if _sha256_bytes(raw) != value[field]:
            raise RuntimeClosureMaterializationError(
                "native entry guard toolchain policy digest drifted"
            )
    return value


def _validate_policy_bytes(
    raw: bytes,
    *,
    source_reader: Callable[[Path, str], bytes],
) -> dict[str, object]:
    policy = _json_object(
        raw,
        label="runtime closure policy",
    )
    if set(policy) != _POLICY_FIELDS:
        raise RuntimeClosureMaterializationError(
            "runtime closure policy fields are missing or unknown"
        )
    profile = policy.get("profile")
    specification = _PROFILE_SPECS.get(str(profile))
    if specification is None:
        raise RuntimeClosureMaterializationError(
            "runtime closure policy profile or identity is invalid"
        )
    if (
        policy["schema_version"] != 1
        or policy["profile_manifest_schema_version"] != 6
        or policy["dependency_import_policy"] != _DEPENDENCY_IMPORT_POLICY
        or tuple(policy["argv_prefix"]) != specification["argv_prefix"]
        or policy["result_validator_id"] != specification["result_validator_id"]
        or policy["semantic_profile"] != specification["semantic_profile"]
        or policy["entrypoint"] != _NATIVE_GUARD_TARGET
        or policy["engine_wheel_mode"] != "0400"
        or policy["engine_name"] != "nautilus_trader"
        or policy["engine_version"] != "1.227.0"
        or not isinstance(policy["python_identity"], str)
        or not str(policy["python_identity"]).startswith("CPython 3.12.")
        or not isinstance(policy["source_commit"], str)
        or _SOURCE_COMMIT.fullmatch(str(policy["source_commit"])) is None
        or not isinstance(policy["engine_upstream_commit"], str)
        or _SOURCE_COMMIT.fullmatch(str(policy["engine_upstream_commit"])) is None
        or policy["source_commit"] == policy["engine_upstream_commit"]
        or isinstance(policy["base_file_count"], bool)
        or not isinstance(policy["base_file_count"], int)
        or int(policy["base_file_count"]) <= 0
        or isinstance(policy["timeout_seconds"], bool)
        or not isinstance(policy["timeout_seconds"], int)
        or not 0 < int(policy["timeout_seconds"]) <= 3_600
    ):
        raise RuntimeClosureMaterializationError(
            "runtime closure policy profile or identity is invalid"
        )
    for field in (
        "artifact_manifest_sha256",
        "base_file_inventory_sha256",
        "base_runtime_manifest_sha256",
    ):
        _require_sha256(policy[field], label=f"policy {field}")
    inventory = policy["launcher_inventory"]
    expected_launchers = specification["launcher_inventory"]
    assert isinstance(expected_launchers, tuple)
    if not isinstance(inventory, list) or len(inventory) != len(expected_launchers):
        raise RuntimeClosureMaterializationError("launcher inventory is invalid")
    observed_launchers: set[tuple[str, str]] = set()
    for record in inventory:
        if not isinstance(record, dict) or set(record) != {"mode", "sha256", "source", "target"}:
            raise RuntimeClosureMaterializationError("launcher inventory record is invalid")
        if record["mode"] != "0400":
            raise RuntimeClosureMaterializationError("launcher inventory mode is unsafe")
        _require_sha256(record["sha256"], label="launcher inventory digest")
        source = _safe_relative(record["source"], label="launcher source").as_posix()
        target = _safe_target(record["target"], label="launcher target").as_posix()
        observed_launchers.add((source, target))
    if observed_launchers != set(expected_launchers):
        raise RuntimeClosureMaterializationError("launcher inventory is not the fixed strategy set")
    _validate_native_guard_policy(
        policy["native_entry_guard"],
        source_reader=source_reader,
    )
    _safe_target(policy["engine_wheel_target"], label="engine wheel target")
    _safe_target(policy["entrypoint"], label="closure entrypoint")
    return policy


def _load_policy(path: Path) -> dict[str, object]:
    raw = _read_file(path, label="runtime closure policy", sealed=False)
    return _validate_policy_bytes(
        raw,
        source_reader=lambda source, label: _read_file(
            source,
            label=label,
            sealed=False,
        ),
    )


def _validate_base_runtime(
    base_runtime: Path, policy: dict[str, object]
) -> tuple[dict[str, object], list[dict[str, object]]]:
    _sealed_directory(base_runtime, label="base runtime")
    manifest_raw = _read_file(
        base_runtime / _CLOSURE_MANIFEST,
        label="base runtime manifest",
        sealed=True,
    )

    def read_base_file(relative: PurePosixPath, expected_mode: int) -> bytes:
        source = base_runtime.joinpath(*relative.parts)
        raw = _read_file(source, label="base runtime file", sealed=True)
        observed = source.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(observed.st_mode) != expected_mode
            or observed.st_size != len(raw)
        ):
            raise RuntimeClosureMaterializationError(
                "base runtime inventory bytes, digest, or mode drifted"
            )
        return raw

    manifest, files, raw_by_path = _validate_base_runtime_bytes(
        manifest_raw,
        policy,
        file_reader=read_base_file,
    )
    listed = {PurePosixPath(path) for path in raw_by_path}
    _validate_base_runtime_path_inventory(base_runtime, listed)
    return manifest, files


def _validate_base_runtime_path_inventory(
    base_runtime: Path,
    listed: set[PurePosixPath],
) -> None:
    files_root = base_runtime / "files"
    actual = {
        PurePosixPath("files", *path.relative_to(files_root).parts)
        for path in files_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != listed:
        raise RuntimeClosureMaterializationError(
            "base runtime inventory contains an unlisted or missing file"
        )
    for directory in (files_root, *(path for path in files_root.rglob("*") if path.is_dir())):
        observed = directory.lstat()
        if stat.S_ISLNK(observed.st_mode) or stat.S_IMODE(observed.st_mode) != 0o500:
            raise RuntimeClosureMaterializationError("base runtime directory mode is unsafe")


def _validate_base_runtime_bytes(
    manifest_raw: bytes,
    policy: dict[str, object],
    *,
    file_reader: Callable[[PurePosixPath, int], bytes],
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, bytes]]:
    if _sha256_bytes(manifest_raw) != policy["base_runtime_manifest_sha256"]:
        raise RuntimeClosureMaterializationError("base runtime manifest digest drifted")
    manifest = _json_object(manifest_raw, label="base runtime manifest")
    if (
        set(manifest) != _BASE_MANIFEST_FIELDS
        or manifest["schema_version"] != 1
        or manifest["engine_name"] != policy["engine_name"]
        or manifest["engine_version"] != policy["engine_version"]
        or manifest["python_identity"] != policy["python_identity"]
        or manifest["source_commit"] != policy["engine_upstream_commit"]
        or manifest["entrypoint"] != policy["argv_prefix"][0]
        or tuple(manifest["argv_prefix"])
        != ("-I", "-S", _BASE_LAUNCHER_TARGET)
        or manifest["result_validator_id"] != "nautilus-backtest-result-v1"
    ):
        raise RuntimeClosureMaterializationError(
            "base runtime profile or identity is invalid"
        )
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != policy["base_file_count"]:
        raise RuntimeClosureMaterializationError("base runtime inventory is invalid")
    if _sha256_bytes(_canonical_json_bytes(files)) != policy["base_file_inventory_sha256"]:
        raise RuntimeClosureMaterializationError("base runtime inventory digest drifted")

    listed: set[PurePosixPath] = set()
    raw_by_path: dict[str, bytes] = {}
    for record in files:
        if not isinstance(record, dict) or set(record) != _FILE_FIELDS:
            raise RuntimeClosureMaterializationError("base runtime inventory record is invalid")
        relative = _safe_relative(record["path"], label="base runtime file path")
        if not relative.is_relative_to(PurePosixPath("files")) or relative in listed:
            raise RuntimeClosureMaterializationError("base runtime inventory path is invalid")
        _safe_target(record["target"], label="base runtime target")
        mode_text = record["mode"]
        if mode_text not in {"0400", "0500"}:
            raise RuntimeClosureMaterializationError("base runtime file mode is unsafe")
        raw = file_reader(relative, int(str(mode_text), 8))
        if (
            len(raw) != record["size"]
            or _sha256_bytes(raw) != record["sha256"]
        ):
            raise RuntimeClosureMaterializationError(
                "base runtime inventory bytes, digest, or mode drifted"
            )
        listed.add(relative)
        raw_by_path[relative.as_posix()] = raw
    return manifest, files, raw_by_path


def _validate_artifact(
    artifact_directory: Path, policy: dict[str, object]
) -> tuple[dict[str, object], Path]:
    _sealed_directory(artifact_directory, label="selected artifact directory")
    manifest_path = artifact_directory / _ARTIFACT_MANIFEST
    manifest_raw = _read_file(
        manifest_path, label="selected artifact manifest", sealed=True
    )
    manifest, wheel_filename, _wheel_raw = _validate_artifact_bytes(
        manifest_raw,
        policy,
        wheel_reader=lambda filename: _read_file(
            artifact_directory / filename,
            label="selected engine wheel",
            sealed=True,
        ),
    )
    wheel_path = artifact_directory / wheel_filename
    _validate_artifact_path_inventory(
        artifact_directory,
        {manifest_path, wheel_path},
    )
    return manifest, wheel_path


def _validate_artifact_path_inventory(
    artifact_directory: Path,
    expected: set[Path],
) -> None:
    if set(artifact_directory.iterdir()) != expected:
        raise RuntimeClosureMaterializationError(
            "selected artifact directory contains an unlisted file"
        )


def _validate_artifact_bytes(
    manifest_raw: bytes,
    policy: dict[str, object],
    *,
    wheel_reader: Callable[[str], bytes],
) -> tuple[dict[str, object], str, bytes]:
    if _sha256_bytes(manifest_raw) != policy["artifact_manifest_sha256"]:
        raise RuntimeClosureMaterializationError("selected artifact manifest digest drifted")
    artifact_manifest = _json_object(
        manifest_raw, label="selected artifact manifest"
    )
    if not all(
        field in artifact_manifest
        for field in (
            "engine_name",
            "engine_version",
            "python_identity",
            "upstream_commit",
            "wheel",
        )
    ):
        raise RuntimeClosureMaterializationError(
            "selected artifact identity or wheel is invalid"
        )
    wheel = artifact_manifest["wheel"]
    if (
        artifact_manifest["engine_name"] != policy["engine_name"]
        or artifact_manifest["engine_version"] != policy["engine_version"]
        or artifact_manifest["python_identity"] != policy["python_identity"]
        or artifact_manifest["upstream_commit"]
        != policy["engine_upstream_commit"]
        or not isinstance(wheel, dict)
        or set(wheel) != {"filename", "sha256", "size"}
        or not isinstance(wheel.get("filename"), str)
        or PurePosixPath(str(wheel["filename"])).name != wheel["filename"]
        or PurePosixPath(str(policy["engine_wheel_target"])).name
        != wheel["filename"]
    ):
        raise RuntimeClosureMaterializationError(
            "selected artifact identity or wheel is invalid"
        )
    wheel_filename = str(wheel["filename"])
    wheel_raw = wheel_reader(wheel_filename)
    if (
        wheel.get("size") != len(wheel_raw)
        or wheel.get("sha256") != _sha256_bytes(wheel_raw)
    ):
        raise RuntimeClosureMaterializationError("selected engine wheel digest drifted")
    return artifact_manifest, wheel_filename, wheel_raw


def _verify_native_guard_toolchains(
    *, cargo: Path, llvm_toolchain: Path, guard: dict[str, object]
) -> tuple[str, str]:
    if (
        not cargo.is_absolute()
        or cargo.name != "cargo"
        or cargo.parent.name != "bin"
        or not llvm_toolchain.is_absolute()
        or llvm_toolchain == Path("/")
        or ".." in cargo.parts
        or ".." in llvm_toolchain.parts
    ):
        raise RuntimeClosureMaterializationError(
            "native entry guard requires explicit private build toolchains"
        )
    rust_toolchain = cargo.parent.parent
    private_tool_verifier = _load_local_tool(
        _INPUT_CACHE_TOOL, "native_guard_private_tool_verifier"
    )
    rust_verifier = _load_local_tool(
        _RUST_TOOLCHAIN_TOOL, "native_guard_rust_toolchain_verifier"
    )
    llvm_verifier = _load_local_tool(
        _LLVM_TOOLCHAIN_TOOL, "native_guard_llvm_toolchain_verifier"
    )
    try:
        rust_policy = rust_verifier.load_manifest(_RUST_TOOLCHAIN_POLICY)
        rust_verifier.verify_materialized_toolchain(rust_toolchain, rust_policy)
        llvm_policy = llvm_verifier.load_policy(_LLVM_TOOLCHAIN_POLICY)
        llvm_verifier.verify_materialized(llvm_toolchain, llvm_policy)
        cargo_identity = private_tool_verifier.validate_private_cargo(
            cargo, "1.95.0"
        )
        rustc_identity = private_tool_verifier.validate_private_rustc(
            cargo.with_name("rustc"), "1.95.0"
        )
    except (OSError, ValueError) as exc:
        raise RuntimeClosureMaterializationError(
            "native entry guard build toolchain verification failed"
        ) from exc
    if (
        cargo_identity != guard["cargo_identity"]
        or rustc_identity != guard["rustc_identity"]
    ):
        raise RuntimeClosureMaterializationError(
            "native entry guard compiler identity drifted"
        )
    return cargo_identity, rustc_identity


def _build_native_entry_guard(
    *,
    staging: Path,
    policy: dict[str, object],
    cargo: Path,
    llvm_toolchain: Path,
) -> dict[str, dict[str, object]]:
    """Build one exact no-dependency guard with only sealed private tools."""

    try:
        observed_stage = staging.lstat()
    except OSError as exc:
        raise RuntimeClosureMaterializationError(
            "native entry guard staging directory is unavailable"
        ) from exc
    if (
        staging.is_symlink()
        or not stat.S_ISDIR(observed_stage.st_mode)
        or observed_stage.st_uid != os.geteuid()
        or stat.S_IMODE(observed_stage.st_mode) != 0o700
    ):
        raise RuntimeClosureMaterializationError(
            "native entry guard staging directory is unsafe"
        )
    guard = _validate_native_guard_policy(policy.get("native_entry_guard"))
    _reject_ambient_cargo_configuration(staging)
    cargo_identity, rustc_identity = _verify_native_guard_toolchains(
        cargo=cargo,
        llvm_toolchain=llvm_toolchain,
        guard=guard,
    )
    destination_relative = PurePosixPath("files", *_safe_target(guard["target"], label="native guard target").parts[1:])
    destination = staging.joinpath(*destination_relative.parts)
    if destination.exists() or destination.is_symlink():
        raise RuntimeClosureMaterializationError(
            "native entry guard destination already exists"
        )

    with tempfile.TemporaryDirectory(
        prefix=".native-entry-guard-build-", dir=staging
    ) as temporary:
        build_root = Path(temporary)
        os.chmod(build_root, 0o700)
        project = build_root / "source"
        (project / "src").mkdir(parents=True, mode=0o700)
        for relative_text, digest_field in (
            (_NATIVE_GUARD_CARGO_MANIFEST, "cargo_manifest_sha256"),
            (_NATIVE_GUARD_CARGO_LOCK, "cargo_lock_sha256"),
            (_NATIVE_GUARD_SOURCE, "source_sha256"),
        ):
            relative = PurePosixPath(relative_text)
            source = _ROOT.joinpath(*relative.parts)
            target = (
                project / "Cargo.toml"
                if relative_text == _NATIVE_GUARD_CARGO_MANIFEST
                else project / "Cargo.lock"
                if relative_text == _NATIVE_GUARD_CARGO_LOCK
                else project / "src/main.rs"
            )
            raw = _read_file(
                source,
                label="native entry guard build source",
                sealed=False,
            )
            if _sha256_bytes(raw) != guard[digest_field]:
                raise RuntimeClosureMaterializationError(
                    "native entry guard build input drifted"
                )
            target.write_bytes(raw)
            target.chmod(0o400)
        cargo_home = build_root / "cargo-home"
        target_directory = build_root / "target"
        compiler_tmp = build_root / "compiler-tmp"
        home = build_root / "home"
        for directory in (cargo_home, target_directory, compiler_tmp, home):
            directory.mkdir(mode=0o700)
        rustc = cargo.with_name("rustc")
        linker = llvm_toolchain / "bin/clang"
        environment = {
            "CARGO_HOME": str(cargo_home),
            "CARGO_INCREMENTAL": "0",
            "CARGO_NET_OFFLINE": "true",
            "CARGO_TARGET_DIR": str(target_directory),
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NAUTILUS_GUARD_ENTRYPOINT": str(policy["entrypoint"]),
            "NAUTILUS_GUARD_LAUNCHER": str(policy["argv_prefix"][3]),
            "NAUTILUS_GUARD_PROFILE": str(policy["profile"]),
            "NAUTILUS_GUARD_PYTHON": str(policy["argv_prefix"][0]),
            "NAUTILUS_GUARD_REQUEST": "/inputs/request.json",
            "NAUTILUS_GUARD_SIDECAR": "/inputs/request.sha256",
            "PATH": f"{cargo.parent}:{llvm_toolchain / 'bin'}",
            "RUSTC": str(rustc),
            "RUSTFLAGS": (
                f"-C linker={linker} -C link-arg=-fuse-ld=lld "
                "-C link-arg=-Wl,--build-id=none"
            ),
            "SOURCE_DATE_EPOCH": "0",
            "TEMP": str(compiler_tmp),
            "TMP": str(compiler_tmp),
            "TMPDIR": str(compiler_tmp),
        }
        try:
            subprocess.run(
                [
                    str(cargo),
                    "build",
                    "--manifest-path",
                    str(project / "Cargo.toml"),
                    "--locked",
                    "--offline",
                    "--release",
                    "--target",
                    str(guard["target_triple"]),
                ],
                check=True,
                cwd=project,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeClosureMaterializationError(
                "offline native entry guard build failed"
            ) from exc
        built = (
            target_directory
            / str(guard["target_triple"])
            / "release"
            / _NATIVE_GUARD_BINARY_NAME
        )
        raw = _read_private_build_output(built)
        if (
            len(raw) != guard["binary_size"]
            or _sha256_bytes(raw) != guard["binary_sha256"]
        ):
            raise RuntimeClosureMaterializationError(
                "native entry guard binary identity drifted"
            )
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination.write_bytes(raw)
        destination.chmod(0o500)

    provenance = _native_guard_provenance(guard)
    provenance["cargo_identity"] = cargo_identity
    provenance["rustc_identity"] = rustc_identity
    return {
        "file": {
            "mode": "0500",
            "path": destination_relative.as_posix(),
            "sha256": provenance["binary_sha256"],
            "size": provenance["binary_size"],
            "target": provenance["target"],
        },
        "provenance": provenance,
    }


def _unseal_and_remove(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        return
    for directory, child_directories, files in os.walk(path, topdown=False):
        current = Path(directory)
        for name in files:
            candidate = current / name
            if not candidate.is_symlink():
                candidate.chmod(0o600)
        for name in child_directories:
            candidate = current / name
            if not candidate.is_symlink():
                candidate.chmod(0o700)
        current.chmod(0o700)
    shutil.rmtree(path)


def _copy_file(source: Path, destination: Path, *, mode: int) -> dict[str, object]:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = _read_file(
        source,
        label="materialization source file",
        sealed=source not in _REPOSITORY_LAUNCHER_SOURCES,
    )
    destination.write_bytes(raw)
    destination.chmod(mode)
    return {"sha256": _sha256_bytes(raw), "size": len(raw), "mode": f"{mode:04o}"}


def _seal_tree(root: Path) -> None:
    for directory, child_directories, _files in os.walk(root, topdown=False):
        current = Path(directory)
        for name in child_directories:
            (current / name).chmod(0o500)
        current.chmod(0o500)


def _renameat2_noreplace(
    parent_fd: int,
    source_name: bytes,
    destination_name: bytes,
) -> None:
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable") from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if (
        renameat2(
            parent_fd,
            source_name,
            parent_fd,
            destination_name,
            _RENAME_NOREPLACE,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


@contextmanager
def _publish_noreplace(
    staging: Path,
    destination: Path,
    *,
    parent_identity: tuple[int, int],
    staging_identity: tuple[int, int],
    completed_close_is_success: bool = False,
    publication_committed: Callable[[], None] | None = None,
) -> Iterator[None]:
    if (
        staging.parent != destination.parent
        or staging.name in {"", ".", ".."}
        or destination.name in {"", ".", ".."}
    ):
        raise RuntimeClosureMaterializationError(
            "atomic no-clobber publication paths are invalid"
        )
    parent_fd = -1
    renamed = False
    try:
        try:
            parent_fd = os.open(
                destination.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            observed = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(observed.st_mode)
                or (observed.st_dev, observed.st_ino) != parent_identity
                or observed.st_uid != os.geteuid()
                or stat.S_IMODE(observed.st_mode) & 0o077
            ):
                raise RuntimeClosureMaterializationError(
                    "destination parent identity changed before atomic publish"
                )
            try:
                staged = os.stat(
                    staging.name, dir_fd=parent_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise RuntimeClosureMaterializationError(
                    "staging identity changed before atomic publish"
                ) from exc
            if (
                not stat.S_ISDIR(staged.st_mode)
                or (staged.st_dev, staged.st_ino) != staging_identity
            ):
                raise RuntimeClosureMaterializationError(
                    "staging identity changed before atomic publish"
                )
            try:
                _renameat2_noreplace(
                    parent_fd,
                    os.fsencode(staging.name),
                    os.fsencode(destination.name),
                )
                renamed = True
                if publication_committed is not None:
                    publication_committed()
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise RuntimeClosureMaterializationError(
                        "destination already exists at atomic publish"
                    ) from exc
                if exc.errno in {
                    errno.ENOSYS,
                    errno.EINVAL,
                    getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
                }:
                    raise RuntimeClosureMaterializationError(
                        "Linux renameat2 RENAME_NOREPLACE is unavailable"
                    ) from exc
                raise RuntimeClosureMaterializationError(
                    "atomic no-clobber publish failed"
                ) from exc
            try:
                published = os.stat(
                    destination.name, dir_fd=parent_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise RuntimeClosureMaterializationError(
                    "published closure identity changed during atomic rename"
                ) from exc
            if (
                not stat.S_ISDIR(published.st_mode)
                or (published.st_dev, published.st_ino) != staging_identity
                or published.st_uid != os.geteuid()
                or stat.S_IMODE(published.st_mode) != 0o500
            ):
                raise RuntimeClosureMaterializationError(
                    "published closure identity changed during atomic rename"
                )
        except RuntimeClosureMaterializationError:
            raise
        except OSError as exc:
            raise RuntimeClosureMaterializationError(
                "destination parent cannot be opened for atomic publish"
            ) from exc

        # Publication has committed before control returns to the caller. Keep
        # the verified parent descriptor open until the caller has recorded
        # that state and completed destination identity checks and attestation.
        yield
    finally:
        if parent_fd >= 0:
            descriptor = parent_fd
            parent_fd = -1
            try:
                os.close(descriptor)
            except OSError:
                if not renamed or not completed_close_is_success:
                    raise


def _build_output_manifest(
    policy: dict[str, object],
    output_records: list[dict[str, object]],
    native_entry_guard: dict[str, object] | None = None,
) -> dict[str, object]:
    guard = _validate_native_guard_policy(policy["native_entry_guard"])
    expected_guard = _native_guard_provenance(guard)
    if native_entry_guard is not None and native_entry_guard != expected_guard:
        raise RuntimeClosureMaterializationError(
            "native entry guard output provenance drifted"
        )
    return {
        "argv_prefix": list(policy["argv_prefix"]),
        "artifact_manifest_sha256": policy["artifact_manifest_sha256"],
        "dependency_import_policy": policy["dependency_import_policy"],
        "engine_name": policy["engine_name"],
        "engine_upstream_commit": policy["engine_upstream_commit"],
        "engine_version": policy["engine_version"],
        "entrypoint": policy["entrypoint"],
        "files": output_records,
        "native_entry_guard": (
            expected_guard
            if native_entry_guard is None
            else native_entry_guard
        ),
        "profile": policy["profile"],
        "python_identity": policy["python_identity"],
        "result_validator_id": policy["result_validator_id"],
        "schema_version": policy["profile_manifest_schema_version"],
        "semantic_profile": policy["semantic_profile"],
        "source_commit": policy["source_commit"],
        "timeout_seconds": policy["timeout_seconds"],
    }


def materialize_runtime_closure(
    *,
    policy_path: Path,
    base_runtime: Path,
    artifact_directory: Path,
    destination: Path,
    sandbox_executable: Path,
    cargo: Path,
    llvm_toolchain: Path,
) -> Path:
    """Publish one new fixed-profile closure or fail without selecting it."""

    paths = tuple(
        Path(value)
        for value in (
            policy_path,
            base_runtime,
            artifact_directory,
            destination,
            sandbox_executable,
            cargo,
            llvm_toolchain,
        )
    )
    if any(not path.is_absolute() or path == Path("/") or ".." in path.parts for path in paths):
        raise RuntimeClosureMaterializationError("all materializer paths must be absolute and safe")
    if destination.exists() or destination.is_symlink():
        raise RuntimeClosureMaterializationError("destination already exists")
    try:
        parent = destination.parent.lstat()
    except OSError as exc:
        raise RuntimeClosureMaterializationError("destination parent is unavailable") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o077
    ):
        raise RuntimeClosureMaterializationError("destination parent is not private")

    policy = _load_policy(policy_path)
    profile = str(policy["profile"])
    _base_manifest, records = _validate_base_runtime(base_runtime, policy)
    _artifact_manifest, selected_wheel = _validate_artifact(
        artifact_directory, policy
    )
    launchers: dict[str, tuple[Path, int]] = {}
    for record in policy["launcher_inventory"]:
        assert isinstance(record, dict)
        source = _ROOT / str(record["source"])
        raw = _read_file(source, label="repository launcher", sealed=False)
        if _sha256_bytes(raw) != record["sha256"]:
            raise RuntimeClosureMaterializationError("repository launcher digest drifted")
        launchers[str(record["target"])] = (source, int(str(record["mode"]), 8))

    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-", dir=destination.parent
        )
    )
    published = False
    phase = "runtime closure materialization"
    try:
        output_records: list[dict[str, object]] = []
        for record in records:
            relative = _safe_relative(record["path"], label="runtime file path")
            target = str(record["target"])
            if target in launchers:
                source, mode = launchers[target]
            elif target == policy["engine_wheel_target"]:
                source = selected_wheel
                mode = int(str(policy["engine_wheel_mode"]), 8)
            else:
                source = base_runtime.joinpath(*relative.parts)
                mode = int(str(record["mode"]), 8)
            copied = _copy_file(
                source, staging.joinpath(*relative.parts), mode=mode
            )
            output_records.append(
                {
                    "path": relative.as_posix(),
                    "target": target,
                    **copied,
                }
            )
        listed_targets = {str(record["target"]) for record in output_records}
        for target, (source, mode) in launchers.items():
            if target not in listed_targets:
                relative = PurePosixPath("files", *PurePosixPath(target).parts[1:])
                copied = _copy_file(source, staging.joinpath(*relative.parts), mode=mode)
                output_records.append({"path": relative.as_posix(), "target": target, **copied})
        native_guard = _build_native_entry_guard(
            staging=staging,
            policy=policy,
            cargo=cargo,
            llvm_toolchain=llvm_toolchain,
        )
        guard_file = native_guard["file"]
        if any(
            record["path"] == guard_file["path"]
            or record["target"] == guard_file["target"]
            for record in output_records
        ):
            raise RuntimeClosureMaterializationError(
                "native entry guard conflicts with the base runtime inventory"
            )
        output_records.append(guard_file)
        if not any(
            record["target"] == policy["argv_prefix"][3]
            for record in output_records
        ) or not set(launchers).issubset({str(record["target"]) for record in output_records}) or not any(
            record["target"] == policy["engine_wheel_target"]
            for record in output_records
        ) or not any(
            record["target"] == policy["argv_prefix"][0]
            and record["mode"] == "0500"
            for record in output_records
        ) or not any(
            record["target"] == policy["entrypoint"]
            and record["mode"] == "0500"
            for record in output_records
        ):
            raise RuntimeClosureMaterializationError(
                "runtime inventory lacks a required replacement target"
            )
        manifest = _build_output_manifest(
            policy,
            output_records,
            native_entry_guard=native_guard["provenance"],
        )
        manifest_path = staging / _CLOSURE_MANIFEST
        manifest_path.write_bytes(_canonical_json_bytes(manifest) + b"\n")
        manifest_path.chmod(0o400)
        _seal_tree(staging)

        phase = "staging closure attestation"
        staged_before = staging.lstat()
        staged_identity = (staged_before.st_dev, staged_before.st_ino)
        staging_attestation = attest_nautilus_backtest_closure(
            NautilusClosureConfig(
                runtime_root=staging,
                artifact_directory=artifact_directory,
                sandbox_executable=sandbox_executable,
            ),
            expected_profile=profile,
        )
        staged_after = staging.lstat()
        if (staged_after.st_dev, staged_after.st_ino) != staged_identity:
            raise RuntimeClosureMaterializationError(
                "staging closure identity changed after attestation"
            )

        phase = "atomic publish"
        with _publish_noreplace(
            staging,
            destination,
            parent_identity=(parent.st_dev, parent.st_ino),
            staging_identity=staged_identity,
        ):
            published = True
            observed = destination.lstat()
            if (observed.st_dev, observed.st_ino) != staged_identity:
                raise RuntimeClosureMaterializationError(
                    "published closure identity changed during atomic rename"
                )

            phase = "published closure re-attestation"
            destination_before_attestation = destination.lstat()
            if (
                destination_before_attestation.st_dev,
                destination_before_attestation.st_ino,
            ) != staged_identity:
                raise RuntimeClosureMaterializationError(
                    "destination closure identity changed before re-attestation"
                )
            published_attestation = attest_nautilus_backtest_closure(
                NautilusClosureConfig(
                    runtime_root=destination,
                    artifact_directory=artifact_directory,
                    sandbox_executable=sandbox_executable,
                ),
                expected_profile=profile,
            )
            destination_after_attestation = destination.lstat()
            if (
                destination_after_attestation.st_dev,
                destination_after_attestation.st_ino,
            ) != staged_identity:
                raise RuntimeClosureMaterializationError(
                    "destination closure identity changed after re-attestation"
                )
            if (
                published_attestation.profile != staging_attestation.profile
                or published_attestation.closure_sha256
                != staging_attestation.closure_sha256
                or published_attestation.result_validator_id
                != staging_attestation.result_validator_id
            ):
                raise RuntimeClosureMaterializationError(
                    "published closure attestation changed after atomic rename"
                )
            phase = "publication descriptor cleanup"
        return destination
    except RuntimeClosureMaterializationError:
        raise
    except (EngineSpawnError, OSError, TypeError, ValueError) as exc:
        raise RuntimeClosureMaterializationError(f"{phase} failed") from exc
    finally:
        if published:
            # A successfully verified destination is the selected generation.
            # If an exception is active, remove only this task-created generation.
            if sys.exc_info()[0] is not None:
                _unseal_and_remove(destination)
        else:
            _unseal_and_remove(staging)


def _candidate_builder_tool():
    return _load_local_tool(_CANDIDATE_BUILDER, "build_nautilus_engine_candidate")


def _candidate_authority() -> tuple[object, dict[str, object], dict[str, object], dict[str, Path]]:
    builder = _candidate_builder_tool()
    engine, inputs = builder._verify_candidate_authority()
    roots = builder._candidate_roots(engine)
    if roots["candidate_runtime_root"].exists() or roots["candidate_runtime_root"].is_symlink():
        raise RuntimeClosureMaterializationError("candidate runtime destination is not absent")
    return builder, engine, inputs, roots


def _validate_candidate_artifact(
    builder: object,
    engine: dict[str, object],
    inputs: dict[str, object],
    roots: dict[str, Path],
) -> tuple[Path, dict[str, object], bytes]:
    directory = roots["candidate_build_root"] / "artifacts"
    manifest_path = directory / _ARTIFACT_MANIFEST
    _sealed_directory(directory, label="candidate artifact directory")
    raw = _read_file(manifest_path, label="candidate artifact manifest", sealed=True)
    try:
        document = builder._candidate_json(manifest_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeClosureMaterializationError("candidate artifact manifest is invalid") from exc
    if raw != (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("ascii"):
        raise RuntimeClosureMaterializationError(
            "candidate artifact manifest is not exact canonical JSON"
        )
    candidate = engine.get("candidate")
    input_python = inputs.get("python")
    input_source = inputs.get("source")
    source_artifact = input_source.get("artifact") if isinstance(input_source, dict) else None
    runtime_wheels = inputs.get("runtime_wheels")
    if (
        set(document) != _CANDIDATE_ARTIFACT_FIELDS
        or not isinstance(candidate, dict)
        or not isinstance(input_python, dict)
        or not isinstance(source_artifact, dict)
        or not isinstance(runtime_wheels, list)
        or not all(isinstance(record, dict) for record in runtime_wheels)
    ):
        raise RuntimeClosureMaterializationError("candidate artifact field set is invalid")
    stdlib = input_python.get("stdlib_inventory")
    rust = engine.get("rust")
    llvm = engine.get("llvm_toolchain")
    router = inputs.get("command_router")
    expected_engine = {
        "name": "nautilus_trader",
        "version": candidate.get("release"),
        "upstream_tag": candidate.get("upstream_tag"),
        "upstream_commit": candidate.get("upstream_commit"),
    }
    expected_python = {
        "identity": input_python.get("identity"),
        "abi": input_python.get("abi"),
        "executable_sha256": input_python.get("executable_sha256"),
        "stdlib_tree_sha256": stdlib.get("tree_sha256") if isinstance(stdlib, dict) else None,
    }
    expected_toolchain = {
        "rustc_identity": rust.get("rustc_identity") if isinstance(rust, dict) else None,
        "cargo_identity": rust.get("cargo_identity") if isinstance(rust, dict) else None,
        "llvm_version": llvm.get("version") if isinstance(llvm, dict) else None,
        "command_router_authority": router.get("authority") if isinstance(router, dict) else None,
    }
    engine_record = document.get("engine")
    python_record = document.get("python")
    toolchain_record = document.get("toolchain")
    if (
        document.get("schema_version") != 7
        or document.get("manifest_kind") != "NAUTILUS_V1_231_CANDIDATE_ARTIFACT"
        or document.get("activation_status") != "CANDIDATE_ONLY_NOT_ACTIVATED"
        or not isinstance(engine_record, dict)
        or set(engine_record) != _CANDIDATE_ENGINE_FIELDS
        or engine_record != expected_engine
        or not isinstance(python_record, dict)
        or set(python_record) != _CANDIDATE_PYTHON_FIELDS
        or python_record != expected_python
        or document.get("policy_hashes") != inputs.get("policy_hashes")
        or not isinstance(toolchain_record, dict)
        or set(toolchain_record) != _CANDIDATE_TOOLCHAIN_FIELDS
        or toolchain_record != expected_toolchain
        or document.get("network") != "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL"
    ):
        raise RuntimeClosureMaterializationError("candidate artifact authority drifted")
    source_record = document.get("source")
    if (
        not isinstance(source_record, dict)
        or set(source_record) != {*source_artifact, "verified_extracted_tree_sha256"}
        or {key: source_record.get(key) for key in source_artifact} != source_artifact
    ):
        raise RuntimeClosureMaterializationError("candidate artifact source authority drifted")
    wheel_record = document.get("wheel")
    if (
        not isinstance(wheel_record, dict)
        or set(wheel_record) != _CANDIDATE_WHEEL_FIELDS
        or wheel_record.get("filename") != builder._CANDIDATE_WHEEL_FILENAME
    ):
        raise RuntimeClosureMaterializationError("candidate artifact wheel record is invalid")
    wheel = directory / str(wheel_record["filename"])
    wheel_raw = _read_file(wheel, label="candidate engine wheel", sealed=True)
    if len(wheel_raw) != wheel_record.get("size") or _sha256_bytes(wheel_raw) != wheel_record.get("sha256"):
        raise RuntimeClosureMaterializationError("candidate artifact engine wheel bytes drifted")
    if set(directory.iterdir()) != {manifest_path, wheel}:
        raise RuntimeClosureMaterializationError("candidate artifact directory file set drifted")
    try:
        builder._verify_candidate_wheel_archive(wheel)
        native = builder._candidate_native_inventory(wheel)
        source_archive = roots["candidate_input_root"] / "source-inputs" / str(source_artifact["filename"])
        _source_inventory, source_tree_sha256 = (
            builder._candidate_source_archive_inventory(
                source_archive, source_artifact
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeClosureMaterializationError(
            "candidate artifact derived authority verification failed"
        ) from exc
    if (
        source_record.get("verified_extracted_tree_sha256") != source_tree_sha256
        or document.get("native_libraries") != native
    ):
        raise RuntimeClosureMaterializationError("candidate artifact derived authority drifted")
    runtime_projection = [
        {
            key: record[key]
            for key in ("filename", "package", "version", "mode", "size", "sha256")
        }
        for record in runtime_wheels
    ]
    if document.get("runtime_wheels") != runtime_projection:
        raise RuntimeClosureMaterializationError(
            "candidate artifact runtime wheel authority drifted"
        )
    receipt = document.get("reproducible_build")
    identities = receipt.get("source_fd_identities") if isinstance(receipt, dict) else None
    process_identities = (
        receipt.get("process_identities") if isinstance(receipt, dict) else None
    )
    required_true = (
        "fresh_physical_stages",
        "logical_stages_absent_after_build",
        "raw_wheel_equality",
        "native_inventory_equality",
        "authoritative_manifest_equality",
    )
    if (
        not isinstance(receipt, dict)
        or set(receipt) != _CANDIDATE_REPRODUCIBILITY_FIELDS
        or receipt.get("build_count") != 2
        or any(receipt.get(field) is not True for field in required_true)
        or receipt.get("wheel_sha256") != wheel_record["sha256"]
        or any(
            not isinstance(receipt.get(field), str)
            or _SHA256.fullmatch(receipt[field]) is None
            for field in (
                "build_a_receipt_sha256",
                "build_b_receipt_sha256",
                "x4_authority_receipt_sha256",
            )
        )
        or not isinstance(identities, list)
        or len(identities) != 2
        or identities[0] == identities[1]
        or any(
            not isinstance(identity, dict)
            or set(identity) != _CANDIDATE_SOURCE_FD_FIELDS
            or any(
                not isinstance(value, str)
                or not value.isascii()
                or not value.isdecimal()
                or value.startswith("0")
                for value in identity.values()
            )
            for identity in identities
        )
        or not isinstance(process_identities, list)
        or len(process_identities) != 2
        or process_identities[0] == process_identities[1]
        or any(
            not isinstance(identity, dict)
            or set(identity) != _CANDIDATE_PROCESS_IDENTITY_FIELDS
            or not isinstance(identity.get("boot_id"), str)
            or re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                identity["boot_id"],
            )
            is None
            or any(
                not isinstance(identity.get(field), int)
                or isinstance(identity[field], bool)
                or identity[field] <= 0
                for field in ("pid", "start_time_ticks")
            )
            for identity in process_identities
        )
    ):
        raise RuntimeClosureMaterializationError(
            "candidate artifact reproducibility authority drifted"
        )
    try:
        authority_identities = builder._candidate_external_identities(engine, inputs)
        build_a = builder._load_candidate_build_result(roots, label="A")
        build_b = builder._load_candidate_build_result(roots, label="B")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeClosureMaterializationError(
            "candidate artifact reproducibility authority drifted"
        ) from exc
    a_payload, a_core, a_receipt, a_digest = build_a
    b_payload, b_core, b_receipt, b_digest = build_b
    final_core = {
        key: value for key, value in document.items() if key != "reproducible_build"
    }
    if not (
        a_digest == receipt["build_a_receipt_sha256"]
        and b_digest == receipt["build_b_receipt_sha256"]
        and a_receipt["x4_authority_receipt_sha256"]
        == receipt["x4_authority_receipt_sha256"]
        and b_receipt["x4_authority_receipt_sha256"]
        == receipt["x4_authority_receipt_sha256"]
        and a_payload == b_payload == wheel.read_bytes()
        and a_core == b_core
        and a_receipt["process_identity"] == process_identities[0]
        and b_receipt["process_identity"] == process_identities[1]
        and a_receipt["source_identity"] == identities[0]
        and b_receipt["source_identity"] == identities[1]
        and a_receipt["candidate"] == b_receipt["candidate"]
        and a_receipt["policy_sha256"] == b_receipt["policy_sha256"]
        and a_receipt["authority_identities"]
        == b_receipt["authority_identities"]
        == authority_identities
        and a_receipt["sanitized_environment_sha256"]
        == b_receipt["sanitized_environment_sha256"]
        and a_core == b_core == final_core
    ):
        raise RuntimeClosureMaterializationError(
            "candidate artifact reproducibility authority drifted"
        )
    return wheel, document, raw


def _candidate_native_inventory(
    builder: object,
    staging: Path,
    wheel_sources: list[tuple[Path, str]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    records: list[dict[str, object]] = []

    def normalized_origin(path: str, value: str | None) -> list[str]:
        if value is None:
            return []
        if "!" in path:
            prefix, member = path.split("!", 1)
            base = list(PurePosixPath(member).parent.parts)
            floor = 0
            rendered_prefix = prefix + "!"
        else:
            base = list(PurePosixPath(path).parent.parts[1:])
            floor = 1
            rendered_prefix = "/"
        roots: list[str] = []
        for component in value.split(":"):
            if component == "$ORIGIN":
                suffix: tuple[str, ...] = ()
            elif component.startswith("$ORIGIN/"):
                suffix = PurePosixPath(component[len("$ORIGIN/") :]).parts
            else:
                raise RuntimeClosureMaterializationError(
                    "candidate native RPATH is not relative to ORIGIN"
                )
            resolved = list(base)
            for part in suffix:
                if part in ("", "."):
                    continue
                if part == "..":
                    if len(resolved) <= floor:
                        raise RuntimeClosureMaterializationError(
                            "candidate native RPATH escapes its sealed root"
                        )
                    resolved.pop()
                else:
                    resolved.append(part)
            roots.append(rendered_prefix + "/".join(resolved))
        return roots

    def add_record(path: str, mode: str, payload: bytes) -> None:
        metadata = builder._elf_metadata(payload, path)
        record = {
            "path": path,
            "mode": mode,
            "size": len(payload),
            "sha256": _sha256_bytes(payload),
            "elf": metadata,
        }
        records.append(record)

    engine_target = f"/engine/wheels/{builder._CANDIDATE_WHEEL_FILENAME}"
    if sum(target == engine_target for _source, target in wheel_sources) != 1:
        raise RuntimeClosureMaterializationError(
            "candidate runtime closure engine wheel target is not exact"
        )
    for source, target in wheel_sources:
        try:
            if target == engine_target:
                builder._verify_candidate_wheel_archive(source)
            with zipfile.ZipFile(source) as archive:
                for member in sorted(archive.infolist(), key=lambda item: item.filename):
                    if member.is_dir() or not re.search(r"(?:\.so(?:\.\d+)*|\.pyd|\.dylib|\.dll)$", member.filename, re.IGNORECASE):
                        continue
                    add_record(
                        f"{target}!{member.filename}",
                        f"{(member.external_attr >> 16) & 0o777:04o}",
                        archive.read(member),
                    )
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
            raise RuntimeClosureMaterializationError("candidate runtime wheel native inventory failed") from exc

    files_root = staging / "files"
    for path in sorted(files_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        payload = path.read_bytes()
        if payload[:4] != b"\x7fELF":
            continue
        add_record(
            "/" + path.relative_to(files_root).as_posix(),
            f"{stat.S_IMODE(path.stat().st_mode):04o}",
            payload,
        )
    by_path: dict[str, dict[str, object]] = {}
    for record in records:
        path = str(record["path"])
        if path in by_path:
            raise RuntimeClosureMaterializationError(
                f"ambiguous candidate native path: {path}"
            )
        by_path[path] = record

    def aliases(record: dict[str, object]) -> tuple[str, ...]:
        path = str(record["path"])
        basename = PurePosixPath(path.split("!", 1)[-1]).name
        metadata = record["elf"]
        assert isinstance(metadata, dict)
        soname = metadata.get("soname")
        if soname is not None and (
            not isinstance(soname, str)
            or not soname
            or "/" in soname
            or PurePosixPath(soname).name != soname
        ):
            raise RuntimeClosureMaterializationError(
                f"candidate native SONAME is unsafe: {path}"
            )
        return tuple(dict.fromkeys((basename, *(() if soname is None else (soname,)))))

    soname_aliases = {
        str(metadata["soname"])
        for record in records
        if isinstance((metadata := record["elf"]), dict)
        and metadata.get("soname") is not None
    }
    for alias in soname_aliases:
        digests = {
            str(record["sha256"])
            for record in records
            if alias in aliases(record)
        }
        if len(digests) != 1:
            raise RuntimeClosureMaterializationError(
                f"candidate native alias collision is divergent: {alias}"
            )

    default_roots = (
        "/lib/x86_64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu",
        "/lib",
        "/usr/lib",
    )
    for record in records:
        metadata = record["elf"]
        assert isinstance(metadata, dict)
        if metadata["interpreter"] not in (None, "/lib64/ld-linux-x86-64.so.2"):
            raise RuntimeClosureMaterializationError(
                f"candidate native PT_INTERP is not exact: {record['path']}"
            )
        normalized_rpath = normalized_origin(
            str(record["path"]),
            None if metadata["rpath"] is None else str(metadata["rpath"]),
        )
        normalized_runpath = normalized_origin(
            str(record["path"]),
            None if metadata["runpath"] is None else str(metadata["runpath"]),
        )
        record["normalized_rpath"] = normalized_rpath
        record["normalized_runpath"] = normalized_runpath
    loader_path = staging / "files/lib64/ld-linux-x86-64.so.2"
    loader = loader_path.read_bytes()
    loader_record = by_path.get("/lib64/ld-linux-x86-64.so.2")
    if loader_record is None:
        raise RuntimeClosureMaterializationError(
            "candidate preloaded interpreter record is absent"
        )
    loader_metadata = loader_record["elf"]
    assert isinstance(loader_metadata, dict)
    if (
        loader_record["size"] != len(loader)
        or loader_record["sha256"] != _sha256_bytes(loader)
        or loader_metadata.get("soname") != "ld-linux-x86-64.so.2"
        or loader_metadata.get("interpreter") is not None
    ):
        raise RuntimeClosureMaterializationError(
            "candidate preloaded interpreter identity drifted"
        )

    def load_from(
        root_record: dict[str, object],
        *,
        inherited_rpath: tuple[str, ...] = (),
        loaded: dict[str, dict[str, object]],
        active: set[str],
    ) -> dict[str, object]:
        path = str(root_record["path"])
        existing_records = [
            loaded[name] for name in aliases(root_record) if name in loaded
        ]
        if any(
            record["sha256"] != root_record["sha256"]
            for record in existing_records
        ):
            raise RuntimeClosureMaterializationError(
                f"candidate native loaded-object alias collision is divergent: {path}"
            )
        existing = next(iter(existing_records), None)
        if existing is not None:
            return existing
        for name in aliases(root_record):
            loaded[name] = root_record
        if path in active:
            return root_record
        active.add(path)
        runpath = tuple(str(value) for value in root_record["normalized_runpath"])  # type: ignore[union-attr]
        rpath = tuple(str(value) for value in root_record["normalized_rpath"])  # type: ignore[union-attr]
        search_roots = runpath or (*rpath, *inherited_rpath)
        child_rpath = () if runpath else tuple(dict.fromkeys((*rpath, *inherited_rpath)))
        metadata = root_record["elf"]
        assert isinstance(metadata, dict)
        resolutions: list[dict[str, str]] = []
        for needed in metadata["needed"]:
            name = str(needed)
            if not name or "/" in name:
                raise RuntimeClosureMaterializationError(
                    f"candidate native DT_NEEDED is unsafe: {path} -> {name}"
                )
            resolved_record = loaded.get(name)
            if resolved_record is None:
                resolved_path = next(
                    (
                        candidate
                        for search_root in (*search_roots, *default_roots)
                        if (
                            candidate := search_root
                            + ("" if search_root.endswith("!") else "/")
                            + name
                        )
                        in by_path
                    ),
                    None,
                )
                if resolved_path is None:
                    raise RuntimeClosureMaterializationError(
                        f"candidate native DT_NEEDED closure is incomplete: {path} -> {needed}"
                    )
                resolved_record = load_from(
                    by_path[resolved_path],
                    inherited_rpath=child_rpath,
                    loaded=loaded,
                    active=active,
                )
            else:
                shadow_path = next(
                    (
                        candidate
                        for search_root in (*search_roots, *default_roots)
                        if (
                            candidate := search_root
                            + ("" if search_root.endswith("!") else "/")
                            + name
                        )
                        in by_path
                    ),
                    None,
                )
                if (
                    shadow_path is not None
                    and by_path[shadow_path]["sha256"] != resolved_record["sha256"]
                ):
                    raise RuntimeClosureMaterializationError(
                        f"candidate native loaded-object alias collision is divergent: {path} -> {name}"
                    )
            resolutions.append(
                {"name": name, "resolved_path": str(resolved_record["path"])}
            )
        active.remove(path)
        previous = root_record.get("needed_resolution")
        if previous is not None and previous != resolutions:
            raise RuntimeClosureMaterializationError(
                f"candidate native resolution is context-ambiguous: {path}"
            )
        root_record["needed_resolution"] = resolutions
        return root_record

    for record in records:
        loaded = {name: loader_record for name in aliases(loader_record)}
        if record is loader_record:
            record["needed_resolution"] = []
            continue
        load_from(record, loaded=loaded, active=set())

    loader_assumptions = {
        "path": "/lib64/ld-linux-x86-64.so.2",
        "mode": f"{stat.S_IMODE(loader_path.stat().st_mode):04o}",
        "size": len(loader),
        "sha256": _sha256_bytes(loader),
        "native_record_count": len(records),
        "resolution": "ELF_LOADER_SEARCH_ORDER_V2",
    }
    return records, loader_assumptions


def _candidate_base_binding(
    base_runtime: Path,
    base_policy: dict[str, object],
    base_manifest: dict[str, object],
    base_records: list[dict[str, object]],
) -> dict[str, object]:
    non_engine = sorted(
        (
            record
            for record in base_records
            if not str(record["target"]).startswith("/engine/")
        ),
        key=lambda record: str(record["path"]),
    )
    historical = {
        "schema_version": base_manifest["schema_version"],
        "manifest_sha256": base_policy["base_runtime_manifest_sha256"],
        "engine_name": base_manifest["engine_name"],
        "engine_version": base_manifest["engine_version"],
        "python_identity": base_manifest["python_identity"],
        "source_commit": base_manifest["source_commit"],
        "file_count": len(base_records),
        "file_inventory_sha256": _sha256_bytes(_canonical_json_bytes(base_records)),
        "non_engine_file_count": len(non_engine),
        "non_engine_file_inventory_sha256": _sha256_bytes(
            _canonical_json_bytes(non_engine)
        ),
    }
    if (
        set(historical) != _CANDIDATE_BASE_HISTORICAL_FIELDS
        or historical["schema_version"] != 1
    ):
        raise RuntimeClosureMaterializationError(
            "candidate closure base-runtime authority is invalid"
        )
    return {
        "selected_authority": _selected_base_authority(
            base_runtime.parent,
            base_policy=base_policy,
            historical_manifest=base_manifest,
            historical_records=base_records,
        ),
        "historical_manifest": historical,
    }


def _project_attestation(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _project_attestation(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, tuple):
        return [_project_attestation(item) for item in value]
    return value


def _selected_base_authority(
    rollback_root: Path,
    *,
    base_policy: dict[str, object],
    historical_manifest: dict[str, object],
    historical_records: list[dict[str, object]],
) -> dict[str, object]:
    selected_root = rollback_root / _SELECTED_RUNTIME_GENERATION
    selected_artifact = rollback_root / _SELECTED_ARTIFACT_GENERATION
    authority_directories = (
        (rollback_root, "rollback root"),
        (selected_root, "selected schema-6 runtime"),
        (selected_artifact, "selected schema-6 artifact"),
    )
    directory_identities = {
        path: _authority_directory_identity(path, label=label)
        for path, label in authority_directories
    }
    policy_raw = _read_file(
        _CANDIDATE_BASE_POLICY,
        label="selected schema-6 policy",
        sealed=False,
    )
    policy = _json_object(policy_raw, label="selected schema-6 policy")
    if (
        _sha256_bytes(policy_raw) != _SELECTED_POLICY_SHA256
        or set(policy) != _POLICY_FIELDS
        or policy != base_policy
    ):
        raise RuntimeClosureMaterializationError(
            "selected schema-6 policy bytes or fields drifted"
        )
    _sealed_directory(selected_root, label="selected schema-6 runtime")
    manifest_path = selected_root / _CLOSURE_MANIFEST
    manifest_raw = _read_file(
        manifest_path,
        label="selected schema-6 manifest",
        sealed=True,
    )
    manifest_stat = manifest_path.stat(follow_symlinks=False)
    if (
        _sha256_bytes(manifest_raw) != _SELECTED_MANIFEST_SHA256
        or stat.S_IMODE(manifest_stat.st_mode) != 0o400
    ):
        raise RuntimeClosureMaterializationError(
            "selected schema-6 manifest bytes, hash, or mode drifted"
        )
    manifest = _json_object(manifest_raw, label="selected schema-6 manifest")
    _validate_artifact(selected_artifact, base_policy)
    artifact_raw = _read_file(
        selected_artifact / _ARTIFACT_MANIFEST,
        label="selected schema-6 artifact manifest",
        sealed=True,
    )
    if (
        _sha256_bytes(artifact_raw) != _SELECTED_ARTIFACT_MANIFEST_SHA256
        or base_policy["artifact_manifest_sha256"]
        != _SELECTED_ARTIFACT_MANIFEST_SHA256
    ):
        raise RuntimeClosureMaterializationError(
            "selected schema-6 artifact manifest hash drifted"
        )
    attestation = attest_nautilus_backtest_closure(
        NautilusClosureConfig(
            runtime_root=selected_root,
            artifact_directory=selected_artifact,
            sandbox_executable=_CANDIDATE_SANDBOX,
        ),
        expected_profile="execution-simulation",
    )
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeClosureMaterializationError(
            "selected schema-6 manifest inventory is invalid"
        )
    expected_mounts: list[tuple[Path, PurePosixPath, tuple[int, int], int, int, str]] = []
    for record in files:
        if not isinstance(record, dict) or set(record) != _FILE_FIELDS:
            raise RuntimeClosureMaterializationError(
                "selected schema-6 manifest inventory is invalid"
            )
        relative = _safe_relative(record["path"], label="selected schema-6 file")
        source = selected_root.joinpath(*relative.parts)
        try:
            observed = source.stat(follow_symlinks=False)
        except OSError as exc:
            raise RuntimeClosureMaterializationError(
                "selected schema-6 manifest path is unavailable"
            ) from exc
        expected_mounts.append(
            (
                source,
                _safe_target(record["target"], label="selected schema-6 target"),
                (observed.st_dev, observed.st_ino),
                int(record["size"]),
                int(str(record["mode"]), 8),
                str(record["sha256"]),
            )
        )
    observed_mounts = [
        (
            mount.source,
            mount.target,
            mount.identity,
            mount.size,
            mount.mode,
            mount.sha256,
        )
        for mount in attestation.mounts
    ]
    sidecar = attestation.closure_manifest
    guard = attestation.native_entry_guard
    if (
        attestation.manifest_schema_version != 6
        or attestation.profile != base_policy["profile"]
        or attestation.source_commit != base_policy["source_commit"]
        or attestation.closure_sha256 != _SELECTED_CLOSURE_SHA256
        or observed_mounts != expected_mounts
        or attestation.entrypoint
        != PurePosixPath(str(base_policy["entrypoint"]))
        or attestation.argv_prefix
        != tuple(str(value) for value in base_policy["argv_prefix"])
        or attestation.timeout_seconds != base_policy["timeout_seconds"]
        or attestation.result_validator_id != base_policy["result_validator_id"]
        or attestation.semantic_profile != base_policy["semantic_profile"]
        or attestation.dependency_import_policy
        != base_policy["dependency_import_policy"]
        or sidecar is None
        or sidecar.source != manifest_path
        or sidecar.identity != (manifest_stat.st_dev, manifest_stat.st_ino)
        or sidecar.size != len(manifest_raw)
        or sidecar.mode != 0o400
        or sidecar.sha256 != _SELECTED_MANIFEST_SHA256
        or guard is None
        or guard.target
        != PurePosixPath(str(base_policy["native_entry_guard"]["target"]))  # type: ignore[index]
        or guard.guarded_executable
        != PurePosixPath(str(base_policy["argv_prefix"][0]))
        or attestation.sandbox.executable != _CANDIDATE_SANDBOX
    ):
        raise RuntimeClosureMaterializationError(
            "selected schema-6 attestation authority drifted"
        )
    selected_non_engine = sorted(
        (
            record
            for record in files
            if not str(record["target"]).startswith("/engine/")
        ),
        key=lambda record: str(record["path"]),
    )
    historical_non_engine = sorted(
        (
            record
            for record in historical_records
            if not str(record["target"]).startswith("/engine/")
        ),
        key=lambda record: str(record["path"]),
    )
    if selected_non_engine != historical_non_engine:
        raise RuntimeClosureMaterializationError(
            "selected and historical non-engine projections diverged"
        )
    if (
        historical_manifest.get("schema_version") != 1
        or historical_manifest.get("files") != historical_records
    ):
        raise RuntimeClosureMaterializationError(
            "historical schema-1 base receipt drifted"
        )
    for path, label in authority_directories:
        if _authority_directory_identity(path, label=label) != directory_identities[path]:
            raise RuntimeClosureMaterializationError(
                f"{label} identity changed during physical attestation"
            )
    return {
        "generation": _SELECTED_RUNTIME_GENERATION,
        "artifact_generation": _SELECTED_ARTIFACT_GENERATION,
        "policy": base_policy,
        "policy_sha256": _sha256_bytes(policy_raw),
        "manifest": manifest,
        "manifest_sha256": _sha256_bytes(manifest_raw),
        "manifest_size": len(manifest_raw),
        "manifest_mode": f"{stat.S_IMODE(manifest_stat.st_mode):04o}",
        "artifact_manifest_sha256": _sha256_bytes(artifact_raw),
        "closure_sha256": attestation.closure_sha256,
        "non_engine_file_count": len(selected_non_engine),
        "non_engine_file_inventory_sha256": _sha256_bytes(
            _canonical_json_bytes(selected_non_engine)
        ),
        "attestation": _project_attestation(attestation),
    }


def _candidate_manifest(
    *,
    artifact: dict[str, object],
    artifact_sha256: str,
    base_runtime: Path,
    base_policy: dict[str, object],
    base_manifest: dict[str, object],
    base_records: list[dict[str, object]],
    output_records: list[dict[str, object]],
    native_inventory: list[dict[str, object]],
    loader_assumptions: dict[str, object],
    qualification_sha256: str,
) -> dict[str, object]:
    inventory_sha256 = _sha256_bytes(_canonical_json_bytes(output_records))
    native_sha256 = _sha256_bytes(_canonical_json_bytes(native_inventory))
    return {
        "schema_version": 7,
        "manifest_kind": "NAUTILUS_V1_231_CANDIDATE_RUNTIME_CLOSURE",
        "activation_status": "CANDIDATE_ONLY_NOT_ACTIVATED",
        "engine": artifact["engine"],
        "source": artifact["source"],
        "python": artifact["python"],
        "policy_hashes": artifact["policy_hashes"],
        "toolchain": artifact["toolchain"],
        "network": artifact["network"],
        "runtime_wheels": artifact["runtime_wheels"],
        "qualification": {
            "argv": ["/usr/bin/python3.12", "-I", "-S"],
            "script_sha256": qualification_sha256,
            "status": "PASS",
        },
        "artifact_manifest_sha256": artifact_sha256,
        "base_runtime": _candidate_base_binding(
            base_runtime, base_policy, base_manifest, base_records
        ),
        "file_inventory_sha256": inventory_sha256,
        "native_inventory_sha256": native_sha256,
        "files": output_records,
        "native_libraries": native_inventory,
        "loader_assumptions": loader_assumptions,
    }


def _attest_candidate_closure(
    root: Path,
    *,
    artifact: dict[str, object],
    artifact_sha256: str,
    inputs: dict[str, object],
    base_runtime: Path,
    base_policy: dict[str, object],
) -> dict[str, object]:
    _sealed_directory(root, label="candidate runtime closure")
    raw = _read_file(root / _CLOSURE_MANIFEST, label="candidate closure manifest", sealed=True)
    builder = _candidate_builder_tool()
    try:
        manifest = builder._candidate_json(root / _CLOSURE_MANIFEST)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeClosureMaterializationError("candidate closure manifest is invalid") from exc
    if raw != _canonical_json_bytes(manifest) + b"\n":
        raise RuntimeClosureMaterializationError(
            "candidate closure manifest is not canonical duplicate-key-safe JSON"
        )
    base_manifest, base_records = _validate_base_runtime(base_runtime, base_policy)
    input_python = inputs.get("python")
    input_source = inputs.get("source")
    source_artifact = input_source.get("artifact") if isinstance(input_source, dict) else None
    runtime_wheels = inputs.get("runtime_wheels")
    artifact_source = artifact.get("source")
    artifact_python = artifact.get("python")
    if (
        set(manifest) != _CANDIDATE_CLOSURE_FIELDS
        or manifest.get("schema_version") != 7
        or manifest.get("manifest_kind") != "NAUTILUS_V1_231_CANDIDATE_RUNTIME_CLOSURE"
        or manifest.get("activation_status") != "CANDIDATE_ONLY_NOT_ACTIVATED"
        or manifest.get("engine") != artifact.get("engine")
        or manifest.get("source") != artifact_source
        or manifest.get("python") != artifact_python
        or manifest.get("policy_hashes") != artifact.get("policy_hashes")
        or manifest.get("toolchain") != artifact.get("toolchain")
        or manifest.get("network") != artifact.get("network")
        or manifest.get("runtime_wheels") != artifact.get("runtime_wheels")
        or manifest.get("artifact_manifest_sha256") != artifact_sha256
        or manifest.get("base_runtime")
        != _candidate_base_binding(
            base_runtime, base_policy, base_manifest, base_records
        )
        or manifest.get("qualification")
        != {
            "argv": ["/usr/bin/python3.12", "-I", "-S"],
            "script_sha256": _sha256_bytes(_CANDIDATE_IMPORT_SCRIPT.encode("ascii")),
            "status": "PASS",
        }
        or artifact.get("policy_hashes") != inputs.get("policy_hashes")
        or not isinstance(input_python, dict)
        or not isinstance(artifact_python, dict)
        or artifact_python.get("identity") != input_python.get("identity")
        or artifact_python.get("abi") != input_python.get("abi")
        or artifact_python.get("executable_sha256") != input_python.get("executable_sha256")
        or not isinstance(input_python.get("stdlib_inventory"), dict)
        or artifact_python.get("stdlib_tree_sha256")
        != input_python["stdlib_inventory"].get("tree_sha256")
        or not isinstance(source_artifact, dict)
        or not isinstance(artifact_source, dict)
        or {key: artifact_source.get(key) for key in source_artifact} != source_artifact
        or not isinstance(runtime_wheels, list)
        or not all(isinstance(record, dict) for record in runtime_wheels)
    ):
        raise RuntimeClosureMaterializationError("candidate closure authority is invalid")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeClosureMaterializationError("candidate closure file inventory is invalid")
    artifact_wheel = artifact.get("wheel")
    if not isinstance(artifact_wheel, dict):
        raise RuntimeClosureMaterializationError("candidate closure engine wheel is invalid")
    expected_files = sorted(
        (
            *(
                record
                for record in base_records
                if not str(record["target"]).startswith("/engine/")
            ),
            *(
                {
                    "mode": "0400",
                    "path": f"files/engine/wheels/{record['filename']}",
                    "sha256": record["sha256"],
                    "size": record["size"],
                    "target": f"/engine/wheels/{record['filename']}",
                }
                for record in (*runtime_wheels, artifact_wheel)
            ),
        ),
        key=lambda record: str(record["path"]),
    )
    if files != expected_files:
        raise RuntimeClosureMaterializationError(
            "candidate closure complete base or wheel projection drifted"
        )
    if _sha256_bytes(_canonical_json_bytes(files)) != manifest.get("file_inventory_sha256"):
        raise RuntimeClosureMaterializationError("candidate closure inventory digest drifted")
    native = manifest.get("native_libraries")
    if not isinstance(native, list) or _sha256_bytes(_canonical_json_bytes(native)) != manifest.get("native_inventory_sha256"):
        raise RuntimeClosureMaterializationError("candidate closure native inventory digest drifted")
    expected: set[PurePosixPath] = set()
    for record in files:
        if not isinstance(record, dict) or set(record) != _FILE_FIELDS:
            raise RuntimeClosureMaterializationError("candidate closure file record is invalid")
        relative = _safe_relative(record["path"], label="candidate closure file")
        path = root.joinpath(*relative.parts)
        payload = _read_file(path, label="candidate closure file", sealed=True)
        if (
            len(payload) != record["size"]
            or _sha256_bytes(payload) != record["sha256"]
            or f"{stat.S_IMODE(path.stat().st_mode):04o}" != record["mode"]
        ):
            raise RuntimeClosureMaterializationError("candidate closure file bytes or mode drifted")
        expected.add(relative)
    observed = {
        PurePosixPath(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    expected.add(PurePosixPath(_CLOSURE_MANIFEST))
    if observed != expected:
        raise RuntimeClosureMaterializationError("candidate closure exact file set drifted")
    expected_directories = {PurePosixPath(".")}
    for relative in expected:
        parent = relative.parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent)
            parent = parent.parent
    observed_directories = {PurePosixPath(".")} | {
        PurePosixPath(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_dir()
    }
    if observed_directories != expected_directories:
        raise RuntimeClosureMaterializationError(
            "candidate closure exact directory set drifted"
        )
    for relative in observed_directories:
        directory = root if relative == PurePosixPath(".") else root.joinpath(*relative.parts)
        observed_mode = directory.lstat()
        if stat.S_ISLNK(observed_mode.st_mode) or stat.S_IMODE(observed_mode.st_mode) != 0o500:
            raise RuntimeClosureMaterializationError(
                "candidate closure directory mode drifted"
            )
    wheel_sources = [
        (
            root / f"files/engine/wheels/{record['filename']}",
            f"/engine/wheels/{record['filename']}",
        )
        for record in (*runtime_wheels, artifact_wheel)
    ]
    observed_native, observed_loader = _candidate_native_inventory(
        builder, root, wheel_sources
    )
    observed_native.sort(key=lambda record: str(record["path"]))
    if observed_native != native or observed_loader != manifest.get("loader_assumptions"):
        raise RuntimeClosureMaterializationError(
            "candidate closure native metadata does not match exact closure bytes"
        )
    return manifest


def _qualify_candidate_import(root: Path) -> str:
    command = [
        str(_CANDIDATE_SANDBOX),
        "--die-with-parent",
        "--unshare-all",
        "--new-session",
        "--tmpfs",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--dir",
        "/engine",
        "--ro-bind",
        str(root / "files/engine/wheels"),
        "/engine/wheels",
        "--ro-bind",
        str(root / "files/usr"),
        "/usr",
        "--ro-bind",
        str(root / "files/lib"),
        "/lib",
        "--ro-bind",
        str(root / "files/lib64"),
        "/lib64",
        "--tmpfs",
        "/tmp",
        "--clearenv",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--",
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "-c",
        _CANDIDATE_IMPORT_SCRIPT,
    ]
    try:
        result = subprocess.run(command, env={}, capture_output=True, text=True, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeClosureMaterializationError("sealed candidate CPython import qualification failed") from exc
    if result.stdout != "1.231.0\n" or result.stderr:
        raise RuntimeClosureMaterializationError("candidate import qualification output drifted")
    return _sha256_bytes(_CANDIDATE_IMPORT_SCRIPT.encode("ascii"))


def materialize_candidate_runtime_closure() -> Path:
    builder, _engine, inputs, roots = _candidate_authority()
    artifact_wheel, artifact, artifact_raw = _validate_candidate_artifact(
        builder, _engine, inputs, roots
    )
    artifact_sha256 = _sha256_bytes(artifact_raw)
    base_runtime = roots["rollback_root"] / "runtime-closure-v3"
    base_policy = _load_policy(_CANDIDATE_BASE_POLICY)
    base_manifest, base_records = _validate_base_runtime(base_runtime, base_policy)
    destination = roots["candidate_runtime_root"]
    parent = destination.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode) or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o077:
        raise RuntimeClosureMaterializationError("candidate runtime parent is not private")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    published = False
    identity: tuple[int, int] | None = None

    def publication_committed() -> None:
        nonlocal published
        published = True

    parent_identity = (parent.st_dev, parent.st_ino)
    try:
        output_records: list[dict[str, object]] = []
        for record in base_records:
            target = str(record["target"])
            if target.startswith("/engine/"):
                continue
            relative = _safe_relative(record["path"], label="candidate base file")
            copied = _copy_file(
                base_runtime.joinpath(*relative.parts),
                staging.joinpath(*relative.parts),
                mode=int(str(record["mode"]), 8),
            )
            output_records.append({"path": relative.as_posix(), "target": target, **copied})
        runtime_records = inputs.get("runtime_wheels")
        if not isinstance(runtime_records, list):
            raise RuntimeClosureMaterializationError("candidate runtime wheel authority is invalid")
        wheel_sources: list[tuple[Path, str]] = []
        input_wheels = roots["candidate_input_root"] / "wheels"
        for record in runtime_records:
            if not isinstance(record, dict):
                raise RuntimeClosureMaterializationError("candidate runtime wheel record is invalid")
            source = input_wheels / str(record["filename"])
            raw = _read_file(source, label="candidate runtime wheel", sealed=True)
            if len(raw) != record["size"] or _sha256_bytes(raw) != record["sha256"]:
                raise RuntimeClosureMaterializationError("candidate runtime wheel bytes drifted")
            target = f"/engine/wheels/{source.name}"
            relative = PurePosixPath("files", *PurePosixPath(target).parts[1:])
            copied = _copy_file(source, staging.joinpath(*relative.parts), mode=0o400)
            output_records.append({"path": relative.as_posix(), "target": target, **copied})
            wheel_sources.append((source, target))
        engine_target = f"/engine/wheels/{artifact_wheel.name}"
        engine_relative = PurePosixPath("files", *PurePosixPath(engine_target).parts[1:])
        copied = _copy_file(artifact_wheel, staging.joinpath(*engine_relative.parts), mode=0o400)
        output_records.append({"path": engine_relative.as_posix(), "target": engine_target, **copied})
        wheel_sources.append((artifact_wheel, engine_target))
        output_records.sort(key=lambda record: str(record["path"]))
        native, loader = _candidate_native_inventory(builder, staging, wheel_sources)
        native.sort(key=lambda record: str(record["path"]))
        manifest = _candidate_manifest(
            artifact=artifact,
            artifact_sha256=artifact_sha256,
            base_runtime=base_runtime,
            base_policy=base_policy,
            base_manifest=base_manifest,
            base_records=base_records,
            output_records=output_records,
            native_inventory=native,
            loader_assumptions=loader,
            qualification_sha256=_sha256_bytes(
                _CANDIDATE_IMPORT_SCRIPT.encode("ascii")
            ),
        )
        manifest_path = staging / _CLOSURE_MANIFEST
        manifest_path.write_bytes(_canonical_json_bytes(manifest) + b"\n")
        manifest_path.chmod(0o400)
        _seal_tree(staging)
        before = staging.lstat()
        identity = (before.st_dev, before.st_ino)
        _attest_candidate_closure(
            staging,
            artifact=artifact,
            artifact_sha256=artifact_sha256,
            inputs=inputs,
            base_runtime=base_runtime,
            base_policy=base_policy,
        )
        after_attestation = staging.lstat()
        if (after_attestation.st_dev, after_attestation.st_ino) != identity:
            raise RuntimeClosureMaterializationError(
                "candidate runtime staging identity changed during attestation"
            )
        qualification_sha256 = _qualify_candidate_import(staging)
        if qualification_sha256 != manifest["qualification"]["script_sha256"]:  # type: ignore[index]
            raise RuntimeClosureMaterializationError(
                "candidate import qualification receipt drifted"
            )
        after_qualification = staging.lstat()
        if (after_qualification.st_dev, after_qualification.st_ino) != identity:
            raise RuntimeClosureMaterializationError(
                "candidate runtime staging identity changed during import qualification"
            )
        with _publish_noreplace(
            staging,
            destination,
            parent_identity=parent_identity,
            staging_identity=identity,
            completed_close_is_success=True,
            publication_committed=publication_committed,
        ):
            pass
        return destination
    finally:
        if not published and identity is None:
            _unseal_and_remove(staging)


def attest_candidate_runtime_closure() -> dict[str, object]:
    builder = _candidate_builder_tool()
    engine, inputs = builder._verify_candidate_authority()
    roots = builder._candidate_roots(engine)
    _artifact_wheel, artifact, artifact_raw = _validate_candidate_artifact(
        builder, engine, inputs, roots
    )
    artifact_sha256 = _sha256_bytes(artifact_raw)
    root = roots["candidate_runtime_root"]
    base_runtime = roots["rollback_root"] / "runtime-closure-v3"
    base_policy = _load_policy(_CANDIDATE_BASE_POLICY)
    manifest = _attest_candidate_closure(
        root,
        artifact=artifact,
        artifact_sha256=artifact_sha256,
        inputs=inputs,
        base_runtime=base_runtime,
        base_policy=base_policy,
    )
    qualification_sha256 = _qualify_candidate_import(root)
    qualification = manifest.get("qualification")
    if (
        not isinstance(qualification, dict)
        or qualification_sha256 != qualification.get("script_sha256")
        or qualification.get("status") != "PASS"
    ):
        raise RuntimeClosureMaterializationError(
            "candidate import qualification receipt drifted"
        )
    closure_raw = _read_file(
        root / _CLOSURE_MANIFEST, label="candidate closure manifest", sealed=True
    )
    return {
        "schema_version": 1,
        "manifest_kind": "NAUTILUS_V1_231_CANDIDATE_CLOSURE_ATTESTATION",
        "activation_status": "CANDIDATE_ONLY_NOT_ACTIVATED",
        "closure_schema_version": manifest["schema_version"],
        "engine": manifest["engine"],
        "source": manifest["source"],
        "python": manifest["python"],
        "policy_hashes": manifest["policy_hashes"],
        "toolchain": manifest["toolchain"],
        "network": manifest["network"],
        "runtime_wheels": manifest["runtime_wheels"],
        "base_runtime": manifest["base_runtime"],
        "artifact_manifest_sha256": artifact_sha256,
        "closure_manifest_sha256": _sha256_bytes(closure_raw),
        "file_inventory_sha256": manifest["file_inventory_sha256"],
        "native_inventory_sha256": manifest["native_inventory_sha256"],
        "qualification": qualification,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize one sealed Nautilus execution-simulation closure"
    )
    candidate = parser.add_mutually_exclusive_group()
    candidate.add_argument("--materialize-candidate", action="store_true")
    candidate.add_argument("--attest-candidate", action="store_true")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--base-runtime", type=Path)
    parser.add_argument("--artifact-directory", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--sandbox", type=Path)
    parser.add_argument("--cargo", type=Path)
    parser.add_argument("--llvm-toolchain", type=Path)
    return parser


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"error: {message}")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    try:
        supplied = (
            arguments.policy,
            arguments.base_runtime,
            arguments.artifact_directory,
            arguments.destination,
            arguments.sandbox,
            arguments.cargo,
            arguments.llvm_toolchain,
        )
        if arguments.materialize_candidate:
            if any(value is not None for value in supplied):
                raise RuntimeClosureMaterializationError(
                    "candidate materialization accepts no caller-supplied authority"
                )
            materialize_candidate_runtime_closure()
            return
        if arguments.attest_candidate:
            if any(value is not None for value in supplied):
                raise RuntimeClosureMaterializationError(
                    "candidate attestation accepts no caller-supplied authority"
                )
            print(
                _canonical_json_bytes(attest_candidate_runtime_closure()).decode(
                    "ascii"
                )
            )
            return
        if any(value is None for value in supplied):
            raise RuntimeClosureMaterializationError(
                "legacy materialization requires all explicit authority paths"
            )
        destination = materialize_runtime_closure(
            policy_path=arguments.policy,
            base_runtime=arguments.base_runtime,
            artifact_directory=arguments.artifact_directory,
            destination=arguments.destination,
            sandbox_executable=arguments.sandbox,
            cargo=arguments.cargo,
            llvm_toolchain=arguments.llvm_toolchain,
        )
    except RuntimeClosureMaterializationError as exc:
        _fail(str(exc))
    print(destination)


if __name__ == "__main__":
    main()
