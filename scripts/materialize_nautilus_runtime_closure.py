#!/usr/bin/env python3
"""Materialize one new sealed Nautilus simulation closure from reviewed inputs.

This tool has no acquisition or build mode.  It copies an exact sealed runtime
inventory, replaces only the repository launcher and selected input-bound
Nautilus wheel, atomically publishes a previously absent generation, and asks
the root attestor to verify the result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
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
_POLICY_FIELDS = {
    "argv_prefix",
    "artifact_manifest_sha256",
    "base_file_count",
    "base_file_inventory_sha256",
    "base_runtime_manifest_sha256",
    "engine_name",
    "engine_version",
    "engine_wheel_mode",
    "engine_wheel_target",
    "entrypoint",
    "launcher_mode",
    "launcher_sha256",
    "launcher_source",
    "launcher_target",
    "profile",
    "profile_manifest_schema_version",
    "python_identity",
    "result_validator_id",
    "schema_version",
    "source_commit",
    "timeout_seconds",
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
_LAUNCHER_SOURCE = "engines/nautilus/launcher/nautilus_backtest.py"
_LAUNCHER_TARGET = "/engine/launcher/nautilus_backtest.py"
_PROFILE = "execution-simulation"
_ARGV_PREFIX = (
    "-I",
    "-S",
    _LAUNCHER_TARGET,
    "--profile",
    _PROFILE,
)
_VALIDATOR = "nautilus-backtest-simulation-result-v1"


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


def _load_policy(path: Path) -> dict[str, object]:
    policy = _json_object(
        _read_file(path, label="runtime closure policy", sealed=False),
        label="runtime closure policy",
    )
    if set(policy) != _POLICY_FIELDS:
        raise RuntimeClosureMaterializationError(
            "runtime closure policy fields are missing or unknown"
        )
    if (
        policy["schema_version"] != 1
        or policy["profile_manifest_schema_version"] != 2
        or policy["profile"] != _PROFILE
        or policy["launcher_source"] != _LAUNCHER_SOURCE
        or policy["launcher_target"] != _LAUNCHER_TARGET
        or tuple(policy["argv_prefix"]) != _ARGV_PREFIX
        or policy["result_validator_id"] != _VALIDATOR
        or policy["launcher_mode"] != "0400"
        or policy["engine_wheel_mode"] != "0400"
        or policy["engine_name"] != "nautilus_trader"
        or policy["engine_version"] != "1.227.0"
        or not isinstance(policy["python_identity"], str)
        or not str(policy["python_identity"]).startswith("CPython 3.12.")
        or not isinstance(policy["source_commit"], str)
        or _SOURCE_COMMIT.fullmatch(str(policy["source_commit"])) is None
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
        "launcher_sha256",
    ):
        _require_sha256(policy[field], label=f"policy {field}")
    _safe_target(policy["engine_wheel_target"], label="engine wheel target")
    _safe_target(policy["entrypoint"], label="closure entrypoint")
    return policy


def _validate_base_runtime(
    base_runtime: Path, policy: dict[str, object]
) -> tuple[dict[str, object], list[dict[str, object]]]:
    _sealed_directory(base_runtime, label="base runtime")
    manifest_raw = _read_file(
        base_runtime / _CLOSURE_MANIFEST,
        label="base runtime manifest",
        sealed=True,
    )
    if _sha256_bytes(manifest_raw) != policy["base_runtime_manifest_sha256"]:
        raise RuntimeClosureMaterializationError("base runtime manifest digest drifted")
    manifest = _json_object(manifest_raw, label="base runtime manifest")
    if (
        set(manifest) != _BASE_MANIFEST_FIELDS
        or manifest["schema_version"] != 1
        or manifest["engine_name"] != policy["engine_name"]
        or manifest["engine_version"] != policy["engine_version"]
        or manifest["python_identity"] != policy["python_identity"]
        or manifest["source_commit"] != policy["source_commit"]
        or manifest["entrypoint"] != policy["entrypoint"]
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
        source = base_runtime.joinpath(*relative.parts)
        raw = _read_file(source, label="base runtime file", sealed=True)
        observed = source.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(observed.st_mode) != int(str(mode_text), 8)
            or observed.st_size != record["size"]
            or _sha256_bytes(raw) != record["sha256"]
        ):
            raise RuntimeClosureMaterializationError(
                "base runtime inventory bytes, digest, or mode drifted"
            )
        listed.add(relative)
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
    return manifest, files


def _validate_artifact(
    artifact_directory: Path, policy: dict[str, object]
) -> tuple[dict[str, object], Path]:
    _sealed_directory(artifact_directory, label="selected artifact directory")
    manifest_path = artifact_directory / _ARTIFACT_MANIFEST
    manifest_raw = _read_file(
        manifest_path, label="selected artifact manifest", sealed=True
    )
    if _sha256_bytes(manifest_raw) != policy["artifact_manifest_sha256"]:
        raise RuntimeClosureMaterializationError("selected artifact manifest digest drifted")
    manifest = _json_object(manifest_raw, label="selected artifact manifest")
    wheel = manifest.get("wheel")
    if (
        manifest.get("engine_name") != policy["engine_name"]
        or manifest.get("engine_version") != policy["engine_version"]
        or manifest.get("python_identity") != policy["python_identity"]
        or manifest.get("upstream_commit") != policy["source_commit"]
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
    wheel_path = artifact_directory / str(wheel["filename"])
    wheel_raw = _read_file(wheel_path, label="selected engine wheel", sealed=True)
    if (
        wheel.get("size") != len(wheel_raw)
        or wheel.get("sha256") != _sha256_bytes(wheel_raw)
    ):
        raise RuntimeClosureMaterializationError("selected engine wheel digest drifted")
    if set(artifact_directory.iterdir()) != {manifest_path, wheel_path}:
        raise RuntimeClosureMaterializationError(
            "selected artifact directory contains an unlisted file"
        )
    return manifest, wheel_path


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
    raw = _read_file(source, label="materialization source file", sealed=source != _ROOT / _LAUNCHER_SOURCE)
    destination.write_bytes(raw)
    destination.chmod(mode)
    return {"sha256": _sha256_bytes(raw), "size": len(raw), "mode": f"{mode:04o}"}


def _seal_tree(root: Path) -> None:
    for directory, child_directories, _files in os.walk(root, topdown=False):
        current = Path(directory)
        for name in child_directories:
            (current / name).chmod(0o500)
        current.chmod(0o500)


def materialize_runtime_closure(
    *,
    policy_path: Path,
    base_runtime: Path,
    artifact_directory: Path,
    destination: Path,
    sandbox_executable: Path,
) -> Path:
    """Publish one new execution-simulation closure or fail without selecting it."""

    paths = tuple(Path(value) for value in (policy_path, base_runtime, artifact_directory, destination, sandbox_executable))
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
    launcher = _ROOT / str(policy["launcher_source"])
    launcher_raw = _read_file(launcher, label="repository launcher", sealed=False)
    if _sha256_bytes(launcher_raw) != policy["launcher_sha256"]:
        raise RuntimeClosureMaterializationError("repository launcher digest drifted")

    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-", dir=destination.parent
        )
    )
    published = False
    try:
        output_records: list[dict[str, object]] = []
        for record in records:
            relative = _safe_relative(record["path"], label="runtime file path")
            target = str(record["target"])
            if target == policy["launcher_target"]:
                source = launcher
                mode = int(str(policy["launcher_mode"]), 8)
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
        if not any(
            record["target"] == policy["launcher_target"]
            for record in output_records
        ) or not any(
            record["target"] == policy["engine_wheel_target"]
            for record in output_records
        ):
            raise RuntimeClosureMaterializationError(
                "runtime inventory lacks a required replacement target"
            )
        manifest = {
            "argv_prefix": list(policy["argv_prefix"]),
            "artifact_manifest_sha256": policy["artifact_manifest_sha256"],
            "engine_name": policy["engine_name"],
            "engine_version": policy["engine_version"],
            "entrypoint": policy["entrypoint"],
            "files": output_records,
            "profile": policy["profile"],
            "python_identity": policy["python_identity"],
            "result_validator_id": policy["result_validator_id"],
            "schema_version": policy["profile_manifest_schema_version"],
            "source_commit": policy["source_commit"],
            "timeout_seconds": policy["timeout_seconds"],
        }
        manifest_path = staging / _CLOSURE_MANIFEST
        manifest_path.write_bytes(_canonical_json_bytes(manifest) + b"\n")
        manifest_path.chmod(0o400)
        _seal_tree(staging)
        os.replace(staging, destination)
        published = True
        attest_nautilus_backtest_closure(
            NautilusClosureConfig(
                runtime_root=destination,
                artifact_directory=artifact_directory,
                sandbox_executable=sandbox_executable,
            ),
            expected_profile=_PROFILE,
        )
        return destination
    except RuntimeClosureMaterializationError:
        raise
    except (EngineSpawnError, OSError, TypeError, ValueError) as exc:
        label = "published closure verification" if published else "atomic publish"
        raise RuntimeClosureMaterializationError(f"{label} failed") from exc
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
        )
    except RuntimeClosureMaterializationError as exc:
        _fail(str(exc))
    print(destination)


if __name__ == "__main__":
    main()
