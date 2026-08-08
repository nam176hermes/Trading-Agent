"""Fail-closed attestation for the external Nautilus CPython 3.12 closure.

The root control plane only reads the closure manifest.  It never imports the
Nautilus wheel: the wheel, interpreter, launcher, standard library, and ELF
dependencies are materialized outside the checkout and mounted one immutable
file at a time by :mod:`services.job_worker.engine_spawn`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .engine_spawn import (
    CompleteEngineClosureAttestation,
    NativeEntryGuardAttestation,
    OsSandboxProof,
    ReadOnlyClosureMount,
)
from .engine_spawn_interface import EngineSpawnError


_ROOT = Path(__file__).resolve().parents[2]
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SOURCE_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_PYTHON_IDENTITY = re.compile(r"^CPython 3\.12\.\d+$", re.ASCII)
_BWRAP_VERSION = re.compile(
    r"^bubblewrap (?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$",
    re.ASCII,
)
_REQUIRED_SANDBOX_PROFILE_SHA256 = (
    "742d3d2cf313a0dc5832fd88d277da1d00e07c6e4abcc4ca51bf0ebcd7c3936e"
)
_RESERVED_TARGETS = tuple(PurePosixPath(value) for value in ("/inputs", "/proc", "/dev", "/tmp"))
_MANIFEST_NAME = "closure-manifest.json"
_MANIFEST_TARGET = PurePosixPath("/engine/closure-manifest.json")
_ARTIFACT_MANIFEST_NAME = "artifact-manifest.json"
_FILES_DIRECTORY = "files"
_EXPECTED_ENGINE_NAME = "nautilus_trader"
_EXPECTED_ENGINE_VERSION = "1.227.0"
_PROFILES = {
    "zero-order": {
        "argv_prefix": ("-I", "-S", "/engine/launcher/nautilus_backtest.py"),
        "result_validator_id": "nautilus-backtest-result-v1",
    },
    "execution-simulation": {
        "argv_prefix": (
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ),
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
    },
}
_MANIFEST_FIELDS_V1 = {
    "schema_version",
    "engine_name",
    "engine_version",
    "python_identity",
    "source_commit",
    "artifact_manifest_sha256",
    "entrypoint",
    "argv_prefix",
    "timeout_seconds",
    "result_validator_id",
    "files",
}
_MANIFEST_FIELDS_V2 = {*_MANIFEST_FIELDS_V1, "profile"}
_MANIFEST_FIELDS_V3 = {*_MANIFEST_FIELDS_V2, "semantic_profile"}
_MANIFEST_FIELDS_V4 = {*_MANIFEST_FIELDS_V3, "engine_upstream_commit"}
_MANIFEST_FIELDS_V5 = {*_MANIFEST_FIELDS_V4, "native_entry_guard"}
_MANIFEST_FIELDS_V6 = {*_MANIFEST_FIELDS_V5, "dependency_import_policy"}
_FILE_FIELDS = {"path", "target", "sha256", "size", "mode"}
_NATIVE_GUARD_FIELDS = {
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
_NATIVE_GUARD_TARGET = PurePosixPath("/engine/bin/nautilus-entry-guard")
_NATIVE_GUARD_SOURCE = "engines/nautilus/native_entry_guard/src/main.rs"
_NATIVE_GUARD_CARGO_MANIFEST = "engines/nautilus/native_entry_guard/Cargo.toml"
_NATIVE_GUARD_CARGO_LOCK = "engines/nautilus/native_entry_guard/Cargo.lock"
_NATIVE_GUARDED_ARGV_PREFIX = (
    "/usr/bin/python3.12",
    "-I",
    "-S",
    "/engine/launcher/nautilus_backtest.py",
    "--profile",
    "execution-simulation",
)
_RUST_IDENTITY = re.compile(r"^rustc 1\.95\.0 \([^\x00\r\n]+\)$", re.ASCII)
_CARGO_IDENTITY = re.compile(r"^cargo 1\.95\.0 \([^\x00\r\n]+\)$", re.ASCII)
_SEMANTIC_PROFILE = "nautilus-execution-simulation-v2"
_DEPENDENCY_IMPORT_POLICY = (
    "native-guarded-stdlib-first-sealed-wheel-path-v1"
)


@dataclass(frozen=True, slots=True)
class NautilusClosureConfig:
    """Explicit external locations consumed by the Nautilus closure verifier."""

    runtime_root: Path
    artifact_directory: Path
    sandbox_executable: Path


def _blocked(reason: str, message: str) -> None:
    raise EngineSpawnError(reason, message)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        return digest.hexdigest()
    except OSError as exc:
        _blocked("ENGINE_CLOSURE_STALE", "closure file cannot be read")
        raise AssertionError("unreachable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _ensure_external_private_directory(path: Path, label: str) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or path == Path("/") or ".." in path.parts:
        _blocked("ENGINE_CLOSURE_INVALID", f"{label} must be an absolute non-root path")
    try:
        path.relative_to(_ROOT)
    except ValueError:
        pass
    else:
        _blocked("ENGINE_CLOSURE_INVALID", f"{label} must remain external to the checkout")
    try:
        observed = path.lstat()
    except OSError as exc:
        raise EngineSpawnError("ENGINE_CLOSURE_UNAVAILABLE", f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o500
    ):
        _blocked("ENGINE_CLOSURE_INVALID", f"{label} is not a sealed private directory")


def _sealed_file(path: Path, label: str) -> os.stat_result:
    try:
        observed = path.lstat()
    except OSError as exc:
        raise EngineSpawnError("ENGINE_CLOSURE_UNAVAILABLE", f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or observed.st_nlink != 1
        or stat.S_IMODE(observed.st_mode) not in {0o400, 0o500}
    ):
        _blocked("ENGINE_CLOSURE_INVALID", f"{label} is not an immutable regular file")
    return observed


def _read_json(path: Path, label: str) -> dict[str, object]:
    _sealed_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EngineSpawnError("ENGINE_CLOSURE_INVALID", f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        _blocked("ENGINE_CLOSURE_INVALID", f"{label} must be an object")
    return value


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        _blocked("ENGINE_CLOSURE_INVALID", f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _blocked("ENGINE_CLOSURE_INVALID", f"{label} is unsafe")
    return path


def _safe_target(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        _blocked("ENGINE_CLOSURE_INVALID", "closure target is invalid")
    target = PurePosixPath(value)
    if (
        not target.is_absolute()
        or target == PurePosixPath("/")
        or any(part in {"", ".", ".."} for part in target.parts)
        or any(target == reserved or target.is_relative_to(reserved) for reserved in _RESERVED_TARGETS)
    ):
        _blocked("ENGINE_CLOSURE_INVALID", "closure target is unsafe")
    return target


def _mode(value: object) -> int:
    if not isinstance(value, str) or re.fullmatch(r"0[45]00", value, re.ASCII) is None:
        _blocked("ENGINE_CLOSURE_INVALID", "closure file mode is invalid")
    return int(value, 8)


def _manifest_files(runtime_root: Path, value: object) -> tuple[ReadOnlyClosureMount, ...]:
    if not isinstance(value, list) or not value:
        _blocked("ENGINE_CLOSURE_INVALID", "closure manifest has no files")
    listed_sources: set[PurePosixPath] = set()
    listed_targets: set[PurePosixPath] = set()
    mounts: list[ReadOnlyClosureMount] = []
    for record in value:
        if not isinstance(record, dict) or set(record) != _FILE_FIELDS:
            _blocked("ENGINE_CLOSURE_INVALID", "closure file record is invalid")
        source_relative = _safe_relative(record["path"], "closure file path")
        if not source_relative.parts or source_relative.parts[0] != _FILES_DIRECTORY:
            _blocked("ENGINE_CLOSURE_INVALID", "closure file must be in the files directory")
        target = _safe_target(record["target"])
        if source_relative in listed_sources or target in listed_targets:
            _blocked("ENGINE_CLOSURE_INVALID", "closure file path or target is duplicated")
        expected_digest = record["sha256"]
        if not isinstance(expected_digest, str) or _SHA256.fullmatch(expected_digest) is None:
            _blocked("ENGINE_CLOSURE_INVALID", "closure file digest is invalid")
        expected_size = record["size"]
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
            _blocked("ENGINE_CLOSURE_INVALID", "closure file size is invalid")
        expected_mode = _mode(record["mode"])
        source = runtime_root.joinpath(*source_relative.parts)
        observed = _sealed_file(source, "closure file")
        actual_mode = stat.S_IMODE(observed.st_mode)
        if observed.st_size != expected_size or actual_mode != expected_mode or _sha256_path(source) != expected_digest:
            _blocked("ENGINE_CLOSURE_STALE", "closure file bytes, size, or mode drifted")
        listed_sources.add(source_relative)
        listed_targets.add(target)
        mounts.append(
            ReadOnlyClosureMount(
                source=source,
                target=target,
                identity=(observed.st_dev, observed.st_ino),
                size=observed.st_size,
                mode=actual_mode,
                sha256=expected_digest,
            )
        )

    files_root = runtime_root / _FILES_DIRECTORY
    try:
        actual_sources = {
            PurePosixPath(_FILES_DIRECTORY, *path.relative_to(files_root).parts)
            for path in files_root.rglob("*")
            if path.is_file() or path.is_symlink()
        }
    except OSError as exc:
        raise EngineSpawnError("ENGINE_CLOSURE_UNAVAILABLE", "closure files are unavailable") from exc
    if actual_sources != listed_sources:
        _blocked("ENGINE_CLOSURE_INVALID", "closure contains unlisted or missing runtime files")
    return tuple(mounts)


def _sandbox_proof(path: Path) -> OsSandboxProof:
    try:
        observed = path.lstat()
        if (
            stat.S_ISLNK(observed.st_mode)
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_uid not in {0, os.geteuid()}
            or observed.st_mode & 0o022
            or not observed.st_mode & stat.S_IXUSR
        ):
            _blocked("ENGINE_SANDBOX_PROOF_INVALID", "Bubblewrap executable is unsafe")
        version = subprocess.run(
            [str(path), "--version"], check=True, capture_output=True, text=True, env={}, timeout=5
        ).stdout.strip()
        help_text = subprocess.run(
            [str(path), "--help"], check=True, capture_output=True, text=True, env={}, timeout=5
        ).stdout
    except EngineSpawnError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise EngineSpawnError("ENGINE_SANDBOX_PROOF_UNAVAILABLE", "Bubblewrap cannot be verified") from exc
    if _BWRAP_VERSION.fullmatch(version) is None or "--perms" not in help_text or "--ro-bind-data" not in help_text:
        _blocked("ENGINE_SANDBOX_PROOF_INVALID", "Bubblewrap capabilities are not reviewed")
    return OsSandboxProof(
        executable=path,
        identity=(observed.st_dev, observed.st_ino),
        executable_sha256=_sha256_path(path),
        profile_sha256=_REQUIRED_SANDBOX_PROFILE_SHA256,
        version=version,
        capabilities=("--perms", "--ro-bind-data"),
    )


def _closure_digest(
    *,
    closure_manifest: dict[str, object],
    artifact_digest: str,
    profile: str,
    semantic_profile: str | None,
    mounts: tuple[ReadOnlyClosureMount, ...],
    entrypoint: PurePosixPath,
    timeout: int,
    closure_manifest_sidecar: ReadOnlyClosureMount | None = None,
) -> str:
    digest_document = {
        "artifact_manifest_sha256": artifact_digest,
        "argv_prefix": closure_manifest["argv_prefix"],
        "entrypoint": str(entrypoint),
        "files": [
            {
                "mode": f"{mount.mode:04o}",
                "sha256": mount.sha256,
                "size": mount.size,
                "target": str(mount.target),
            }
            for mount in mounts
        ],
        "result_validator_id": closure_manifest["result_validator_id"],
        "profile": profile,
        "source_commit": closure_manifest["source_commit"],
        "timeout_seconds": timeout,
    }
    if semantic_profile is not None:
        digest_document["semantic_profile"] = semantic_profile
    schema_version = closure_manifest["schema_version"]
    if schema_version in {4, 5, 6}:
        digest_document["engine_upstream_commit"] = closure_manifest[
            "engine_upstream_commit"
        ]
        if closure_manifest_sidecar is None:
            _blocked(
                "ENGINE_CLOSURE_INVALID",
                f"schema-v{schema_version} closure manifest sidecar is missing",
            )
        digest_document["closure_manifest"] = {
            "identity": list(closure_manifest_sidecar.identity),
            "mode": f"{closure_manifest_sidecar.mode:04o}",
            "sha256": closure_manifest_sidecar.sha256,
            "size": closure_manifest_sidecar.size,
            "target": str(closure_manifest_sidecar.target),
        }
    if schema_version in {5, 6}:
        digest_document["manifest_schema_version"] = schema_version
        digest_document["native_entry_guard"] = closure_manifest[
            "native_entry_guard"
        ]
    if schema_version == 6:
        digest_document["dependency_import_policy"] = closure_manifest[
            "dependency_import_policy"
        ]
    return hashlib.sha256(_canonical_json_bytes(digest_document)).hexdigest()


def _closure_manifest_sidecar(path: Path) -> ReadOnlyClosureMount:
    observed = _sealed_file(path, "closure manifest")
    mode = stat.S_IMODE(observed.st_mode)
    if mode != 0o400:
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "closure manifest sidecar mode is invalid",
        )
    return ReadOnlyClosureMount(
        source=path,
        target=_MANIFEST_TARGET,
        identity=(observed.st_dev, observed.st_ino),
        size=observed.st_size,
        mode=mode,
        sha256=_sha256_path(path),
    )


def _native_entry_guard(
    value: object,
    *,
    mounts: tuple[ReadOnlyClosureMount, ...],
    entrypoint: PurePosixPath,
    argv_prefix: tuple[str, ...],
) -> NativeEntryGuardAttestation:
    if not isinstance(value, dict) or set(value) != _NATIVE_GUARD_FIELDS:
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "native entry guard fields are missing or unknown",
        )
    for field in (
        "binary_sha256",
        "cargo_lock_sha256",
        "cargo_manifest_sha256",
        "llvm_toolchain_policy_sha256",
        "rust_toolchain_policy_sha256",
        "source_sha256",
    ):
        if not isinstance(value[field], str) or _SHA256.fullmatch(value[field]) is None:
            _blocked("ENGINE_CLOSURE_INVALID", "native entry guard digest is invalid")
    binary_size = value["binary_size"]
    if (
        isinstance(binary_size, bool)
        or not isinstance(binary_size, int)
        or binary_size <= 0
        or value["mode"] != "0500"
        or value["source"] != _NATIVE_GUARD_SOURCE
        or value["cargo_manifest"] != _NATIVE_GUARD_CARGO_MANIFEST
        or value["cargo_lock"] != _NATIVE_GUARD_CARGO_LOCK
        or value["target_triple"] != "x86_64-unknown-linux-gnu"
        or not isinstance(value["cargo_identity"], str)
        or _CARGO_IDENTITY.fullmatch(value["cargo_identity"]) is None
        or not isinstance(value["rustc_identity"], str)
        or _RUST_IDENTITY.fullmatch(value["rustc_identity"]) is None
    ):
        _blocked("ENGINE_CLOSURE_INVALID", "native entry guard identity is invalid")
    target = _safe_target(value["target"])
    if (
        target != _NATIVE_GUARD_TARGET
        or entrypoint != target
        or argv_prefix != _NATIVE_GUARDED_ARGV_PREFIX
    ):
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "native entry guard argv or entrypoint is invalid",
        )
    matching_guard = [mount for mount in mounts if mount.target == target]
    if (
        len(matching_guard) != 1
        or matching_guard[0].mode != 0o500
        or matching_guard[0].size != binary_size
        or matching_guard[0].sha256 != value["binary_sha256"]
    ):
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "native entry guard binary identity drifted",
        )
    guarded_executable = _safe_target(argv_prefix[0])
    matching_python = [
        mount for mount in mounts if mount.target == guarded_executable
    ]
    if (
        guarded_executable == target
        or len(matching_python) != 1
        or matching_python[0].mode != 0o500
    ):
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "guarded CPython is not one exact executable closure file",
        )
    return NativeEntryGuardAttestation(
        target=target,
        guarded_executable=guarded_executable,
        binary_sha256=value["binary_sha256"],
        binary_size=binary_size,
        mode=0o500,
        source=value["source"],
        source_sha256=value["source_sha256"],
        cargo_manifest=value["cargo_manifest"],
        cargo_manifest_sha256=value["cargo_manifest_sha256"],
        cargo_lock=value["cargo_lock"],
        cargo_lock_sha256=value["cargo_lock_sha256"],
        cargo_identity=value["cargo_identity"],
        rustc_identity=value["rustc_identity"],
        rust_toolchain_policy_sha256=value["rust_toolchain_policy_sha256"],
        llvm_toolchain_policy_sha256=value["llvm_toolchain_policy_sha256"],
        target_triple=value["target_triple"],
    )


def attest_nautilus_backtest_closure(
    config: NautilusClosureConfig,
    *,
    expected_profile: str,
) -> CompleteEngineClosureAttestation:
    """Return the complete immutable CPython 3.12 closure for one backtest.

    Any external cache drift is an authority failure before a request can be
    prepared, not a launcher-time warning.
    """

    if type(config) is not NautilusClosureConfig:
        raise TypeError("NautilusClosureConfig is required")
    if expected_profile not in _PROFILES:
        raise ValueError("explicit supported Nautilus closure profile is required")
    _ensure_external_private_directory(config.runtime_root, "Nautilus runtime root")
    _ensure_external_private_directory(config.artifact_directory, "Nautilus artifact directory")
    closure_manifest = _read_json(config.runtime_root / _MANIFEST_NAME, "closure manifest")
    artifact_manifest_path = config.artifact_directory / _ARTIFACT_MANIFEST_NAME
    artifact_manifest = _read_json(artifact_manifest_path, "artifact manifest")
    schema_version = closure_manifest.get("schema_version")
    if type(schema_version) is not int:
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "closure manifest schema generation is invalid",
        )
    if schema_version == 1 and set(closure_manifest) == _MANIFEST_FIELDS_V1:
        profile = "zero-order"
        semantic_profile = None
    elif schema_version == 2 and set(closure_manifest) == _MANIFEST_FIELDS_V2:
        profile = closure_manifest.get("profile")
        semantic_profile = None
    elif schema_version == 3 and set(closure_manifest) == _MANIFEST_FIELDS_V3:
        profile = closure_manifest.get("profile")
        semantic_profile = closure_manifest.get("semantic_profile")
    elif schema_version == 4 and set(closure_manifest) == _MANIFEST_FIELDS_V4:
        profile = closure_manifest.get("profile")
        semantic_profile = closure_manifest.get("semantic_profile")
    elif schema_version == 5 and set(closure_manifest) == _MANIFEST_FIELDS_V5:
        profile = closure_manifest.get("profile")
        semantic_profile = closure_manifest.get("semantic_profile")
    elif schema_version == 6 and set(closure_manifest) == _MANIFEST_FIELDS_V6:
        profile = closure_manifest.get("profile")
        semantic_profile = closure_manifest.get("semantic_profile")
    else:
        _blocked("ENGINE_CLOSURE_INVALID", "closure manifest fields are missing or unknown")
    if profile != expected_profile or profile not in _PROFILES:
        _blocked("ENGINE_CLOSURE_INVALID", "closure profile does not match explicit authority")
    if profile == "execution-simulation" and semantic_profile != _SEMANTIC_PROFILE:
        _blocked("ENGINE_CLOSURE_INVALID", "closure semantic profile is invalid")
    if schema_version in {3, 4, 5, 6} and profile != "execution-simulation":
        _blocked("ENGINE_CLOSURE_INVALID", "closure semantic profile is invalid")
    expected_identity = _PROFILES[profile]
    if (
        closure_manifest["engine_name"] != _EXPECTED_ENGINE_NAME
        or closure_manifest["engine_version"] != _EXPECTED_ENGINE_VERSION
        or not isinstance(closure_manifest["python_identity"], str)
        or _PYTHON_IDENTITY.fullmatch(closure_manifest["python_identity"]) is None
        or not isinstance(closure_manifest["source_commit"], str)
        or _SOURCE_COMMIT.fullmatch(closure_manifest["source_commit"]) is None
        or (
            schema_version in {4, 5, 6}
            and (
                not isinstance(closure_manifest["engine_upstream_commit"], str)
                or _SOURCE_COMMIT.fullmatch(
                    closure_manifest["engine_upstream_commit"]
                )
                is None
                or closure_manifest["source_commit"]
                == closure_manifest["engine_upstream_commit"]
            )
        )
        or closure_manifest["result_validator_id"]
        != expected_identity["result_validator_id"]
        or tuple(closure_manifest.get("argv_prefix", ()))
        != (
            _NATIVE_GUARDED_ARGV_PREFIX
            if schema_version in {5, 6}
            else expected_identity["argv_prefix"]
        )
        or (
            schema_version == 6
            and closure_manifest["dependency_import_policy"]
            != _DEPENDENCY_IMPORT_POLICY
        )
    ):
        _blocked("ENGINE_CLOSURE_INVALID", "closure manifest identity is invalid")
    artifact_digest = closure_manifest["artifact_manifest_sha256"]
    if not isinstance(artifact_digest, str) or _SHA256.fullmatch(artifact_digest) is None or _sha256_path(artifact_manifest_path) != artifact_digest:
        _blocked("ENGINE_CLOSURE_STALE", "artifact manifest digest drifted")
    if (
        artifact_manifest.get("engine_name") != _EXPECTED_ENGINE_NAME
        or artifact_manifest.get("engine_version") != _EXPECTED_ENGINE_VERSION
        or artifact_manifest.get("python_identity") != closure_manifest["python_identity"]
        or artifact_manifest.get("upstream_commit")
        != (
            closure_manifest["engine_upstream_commit"]
            if schema_version in {4, 5, 6}
            else closure_manifest["source_commit"]
        )
    ):
        _blocked("ENGINE_CLOSURE_INVALID", "artifact manifest identity is incompatible")
    mounts = _manifest_files(config.runtime_root, closure_manifest["files"])
    closure_manifest_sidecar = (
        _closure_manifest_sidecar(config.runtime_root / _MANIFEST_NAME)
        if schema_version in {4, 5, 6}
        else None
    )
    if closure_manifest_sidecar is not None and any(
        mount.target == _MANIFEST_TARGET
        or mount.target.is_relative_to(_MANIFEST_TARGET)
        or _MANIFEST_TARGET.is_relative_to(mount.target)
        for mount in mounts
    ):
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "closure manifest must be a separate sidecar",
        )
    entrypoint = _safe_target(closure_manifest["entrypoint"])
    matching = [mount for mount in mounts if mount.target == entrypoint]
    if len(matching) != 1 or not matching[0].mode & 0o100:
        _blocked("ENGINE_CLOSURE_INVALID", "closure entrypoint is not executable")
    argv_value = closure_manifest["argv_prefix"]
    if (
        not isinstance(argv_value, list)
        or not argv_value
        or any(not isinstance(argument, str) or not argument or "\x00" in argument for argument in argv_value)
    ):
        _blocked("ENGINE_CLOSURE_INVALID", "closure argv prefix is invalid")
    native_entry_guard = (
        _native_entry_guard(
            closure_manifest["native_entry_guard"],
            mounts=mounts,
            entrypoint=entrypoint,
            argv_prefix=tuple(argv_value),
        )
        if schema_version in {5, 6}
        else None
    )
    timeout = closure_manifest["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= 3_600:
        _blocked("ENGINE_CLOSURE_INVALID", "closure timeout is invalid")
    return CompleteEngineClosureAttestation(
        manifest_schema_version=schema_version,
        profile=profile,
        source_commit=closure_manifest["source_commit"],
        closure_sha256=_closure_digest(
            closure_manifest=closure_manifest,
            artifact_digest=artifact_digest,
            profile=profile,
            semantic_profile=semantic_profile,
            mounts=mounts,
            entrypoint=entrypoint,
            timeout=timeout,
            closure_manifest_sidecar=closure_manifest_sidecar,
        ),
        mounts=mounts,
        entrypoint=entrypoint,
        argv_prefix=tuple(argv_value),
        timeout_seconds=timeout,
        result_validator_id=closure_manifest["result_validator_id"],
        sandbox=_sandbox_proof(config.sandbox_executable),
        semantic_profile=semantic_profile,
        closure_manifest=closure_manifest_sidecar,
        native_entry_guard=native_entry_guard,
        dependency_import_policy=(
            closure_manifest["dependency_import_policy"]
            if schema_version == 6
            else None
        ),
    )


__all__ = ["NautilusClosureConfig", "attest_nautilus_backtest_closure"]
