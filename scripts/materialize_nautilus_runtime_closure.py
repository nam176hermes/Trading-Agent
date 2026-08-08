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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import NoReturn, Sequence

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
_LAUNCHER_INVENTORY = (
    ("engines/nautilus/launcher/nautilus_backtest.py", "/engine/launcher/nautilus_backtest.py"),
    ("engines/nautilus/launcher/target_portfolio_strategy.py", "/engine/launcher/target_portfolio_strategy.py"),
)
_LAUNCHER_TARGET = _LAUNCHER_INVENTORY[0][1]
_NATIVE_GUARD_SOURCE = "engines/nautilus/native_entry_guard/src/main.rs"
_NATIVE_GUARD_CARGO_MANIFEST = "engines/nautilus/native_entry_guard/Cargo.toml"
_NATIVE_GUARD_CARGO_LOCK = "engines/nautilus/native_entry_guard/Cargo.lock"
_NATIVE_GUARD_TARGET = "/engine/bin/nautilus-entry-guard"
_NATIVE_GUARD_TARGET_TRIPLE = "x86_64-unknown-linux-gnu"
_NATIVE_GUARD_BINARY_NAME = "nautilus-entry-guard"
_EXPECTED_CARGO_IDENTITY = "cargo 1.95.0 (f2d3ce0bd 2026-03-21)"
_EXPECTED_RUSTC_IDENTITY = "rustc 1.95.0 (59807616e 2026-04-14)"
_PROFILE = "execution-simulation"
_ARGV_PREFIX = (
    "/usr/bin/python3.12",
    "-I",
    "-S",
    _LAUNCHER_TARGET,
    "--profile",
    _PROFILE,
)
_VALIDATOR = "nautilus-backtest-simulation-result-v1"
_SEMANTIC_PROFILE = "nautilus-execution-simulation-v2"
_DEPENDENCY_IMPORT_POLICY = (
    "native-guarded-stdlib-first-sealed-wheel-path-v1"
)
_RENAME_NOREPLACE = 1


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
    if (
        policy["schema_version"] != 1
        or policy["profile_manifest_schema_version"] != 6
        or policy["dependency_import_policy"] != _DEPENDENCY_IMPORT_POLICY
        or policy["profile"] != _PROFILE
        or tuple(policy["argv_prefix"]) != _ARGV_PREFIX
        or policy["result_validator_id"] != _VALIDATOR
        or policy["semantic_profile"] != _SEMANTIC_PROFILE
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
    if not isinstance(inventory, list) or len(inventory) != len(_LAUNCHER_INVENTORY):
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
    if observed_launchers != set(_LAUNCHER_INVENTORY):
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
        != ("-I", "-S", _LAUNCHER_TARGET)
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
    manifest = _json_object(manifest_raw, label="selected artifact manifest")
    wheel = manifest.get("wheel")
    if (
        manifest.get("engine_name") != policy["engine_name"]
        or manifest.get("engine_version") != policy["engine_version"]
        or manifest.get("python_identity") != policy["python_identity"]
        or manifest.get("upstream_commit") != policy["engine_upstream_commit"]
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
    return manifest, wheel_filename, wheel_raw


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
            "NAUTILUS_GUARD_LAUNCHER": _LAUNCHER_TARGET,
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
    launcher_sources = {_ROOT / source for source, _target in _LAUNCHER_INVENTORY}
    raw = _read_file(source, label="materialization source file", sealed=source not in launcher_sources)
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
                _renameat2_noreplace(
                    parent_fd,
                    os.fsencode(staging.name),
                    os.fsencode(destination.name),
                )
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
            os.close(descriptor)


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
    """Publish one new execution-simulation closure or fail without selecting it."""

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
            record["target"] == _LAUNCHER_TARGET
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
            expected_profile=_PROFILE,
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
                expected_profile=_PROFILE,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize one sealed Nautilus execution-simulation closure"
    )
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--base-runtime", type=Path, required=True)
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--sandbox", type=Path, required=True)
    parser.add_argument("--cargo", type=Path, required=True)
    parser.add_argument("--llvm-toolchain", type=Path, required=True)
    return parser


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"error: {message}")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    try:
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
