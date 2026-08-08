#!/usr/bin/env python3
"""Qualify sealed Nautilus imports offline without publishing a runtime closure."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import NoReturn


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import materialize_nautilus_runtime_closure as _closure


_PROBE = _ROOT / "engines/nautilus/launcher/import_probe.py"
_ENTRY_TARGET = PurePosixPath("/qualification/entry-launcher.py")
_PROBE_TARGET = PurePosixPath("/qualification/import_probe.py")
_MANIFEST_TARGET = PurePosixPath("/engine/closure-manifest.json")
_STRATEGY_TARGET = PurePosixPath(
    "/engine/launcher/target_portfolio_strategy.py"
)
_PYTHON_TARGET = PurePosixPath("/usr/bin/python3.12")
_PROFILE = "execution-simulation"
_PROBE_SCHEMA = "nautilus-sealed-import-probe-v1"
_RECEIPT_SCHEMA = "nautilus-sealed-import-qualification-v1"
_DEPENDENCY_IMPORT_POLICY = "native-guarded-stdlib-first-sealed-wheel-path-v1"
_SANDBOX_PROFILE = (
    b"trading-agent-engine-bwrap-v2:die-with-parent,user,pid,net,new-session,"
    b"clearenv,sealed-file-closure,fd-ro-bind-data-inputs,proc,dev,tmpfs"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.!+_-]{0,127}$", re.ASCII)
_BWRAP_VERSION = re.compile(
    rb"^bubblewrap (?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\n$",
    re.ASCII,
)
_MFD_ALLOW_SEALING = 0x0002
_MFD_CLOEXEC = 0x0001
_MEMFD_CREATE_SYSCALLS = {
    "x86_64": 319,
    "amd64": 319,
    "aarch64": 279,
    "arm64": 279,
}
_F_ADD_SEALS = 1033
_F_GET_SEALS = 1034
_F_SEAL_SEAL = 0x0001
_F_SEAL_SHRINK = 0x0002
_F_SEAL_GROW = 0x0004
_F_SEAL_WRITE = 0x0008
_ALL_SEALS = _F_SEAL_SEAL | _F_SEAL_SHRINK | _F_SEAL_GROW | _F_SEAL_WRITE


class SealedImportQualificationError(ValueError):
    """A qualification input, sandbox result, or receipt publication is unsafe."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_absolute(path: Path, *, label: str, must_exist: bool) -> None:
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        raise SealedImportQualificationError(f"{label} path must be absolute and safe")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise SealedImportQualificationError(f"{label} path is unavailable") from exc
    if resolved != path:
        raise SealedImportQualificationError(f"{label} path must not contain symlinks")


def _read_file(
    path: Path,
    *,
    label: str,
    modes: set[int] | None = None,
    source: bool = False,
    sandbox: bool = False,
) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        effective_uid = os.geteuid()
        trusted_owner = (
            _trusted_system_owner(opened.st_uid, effective_uid)
            if sandbox
            else opened.st_uid == effective_uid
        )
        if sandbox:
            safe_mode = bool(mode & 0o111) and not mode & 0o022
        elif source:
            safe_mode = not mode & 0o022
        else:
            safe_mode = modes is not None and mode in modes
        if (
            not stat.S_ISREG(opened.st_mode)
            or not trusted_owner
            or opened.st_nlink != 1
            or not safe_mode
        ):
            raise SealedImportQualificationError(f"{label} identity or mode is unsafe")
        chunks: list[bytes] = []
        while block := os.read(descriptor, 1024 * 1024):
            chunks.append(block)
        named = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            or named.st_size != opened.st_size
            or stat.S_IMODE(named.st_mode) != mode
            or named.st_mtime_ns != opened.st_mtime_ns
            or named.st_ctime_ns != opened.st_ctime_ns
        ):
            raise SealedImportQualificationError(f"{label} identity changed while read")
        return b"".join(chunks), (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            mode,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
    except SealedImportQualificationError:
        raise
    except OSError as exc:
        raise SealedImportQualificationError(f"{label} cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _trusted_system_owner(observed_uid: int, effective_uid: int) -> bool:
    return observed_uid in {0, effective_uid}


def _open_private_parent(path: Path) -> tuple[int, tuple[int, int]]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
        )
        observed = os.fstat(descriptor)
        named = path.lstat()
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SealedImportQualificationError("receipt parent is unavailable") from exc
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o700
        or not stat.S_ISDIR(named.st_mode)
        or (named.st_dev, named.st_ino) != (observed.st_dev, observed.st_ino)
    ):
        os.close(descriptor)
        raise SealedImportQualificationError(
            "receipt parent must be an existing private mode-0700 directory"
        )
    return descriptor, (observed.st_dev, observed.st_ino)


def _same_private_parent(
    descriptor: int, path: Path, identity: tuple[int, int]
) -> bool:
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(opened.st_mode)
        and stat.S_ISDIR(named.st_mode)
        and (opened.st_dev, opened.st_ino) == identity
        and (named.st_dev, named.st_ino) == identity
        and opened.st_uid == os.geteuid()
        and named.st_uid == os.geteuid()
        and stat.S_IMODE(opened.st_mode) == 0o700
        and stat.S_IMODE(named.st_mode) == 0o700
    )


def _remember_file(
    snapshots: dict[Path, tuple[bytes, tuple[int, ...], str, set[int] | None, bool, bool]],
    path: Path,
    *,
    label: str,
    modes: set[int] | None = None,
    source: bool = False,
    sandbox: bool = False,
) -> bytes:
    existing = snapshots.get(path)
    if existing is not None:
        return existing[0]
    raw, identity = _read_file(
        path,
        label=label,
        modes=modes,
        source=source,
        sandbox=sandbox,
    )
    snapshots[path] = (raw, identity, label, modes, source, sandbox)
    return raw


def _policy_from_snapshot(raw: bytes) -> dict[str, object]:
    try:
        policy = _closure._json_object(raw, label="runtime closure policy")
        if set(policy) != _closure._POLICY_FIELDS:
            raise ValueError("runtime closure policy fields are missing or unknown")
        if (
            policy["schema_version"] != 1
            or policy["profile_manifest_schema_version"] != 6
            or policy["dependency_import_policy"] != _DEPENDENCY_IMPORT_POLICY
            or policy["profile"] != _PROFILE
            or tuple(policy["argv_prefix"]) != _closure._ARGV_PREFIX
            or policy["result_validator_id"] != _closure._VALIDATOR
            or policy["semantic_profile"] != _closure._SEMANTIC_PROFILE
            or policy["entrypoint"] != _closure._NATIVE_GUARD_TARGET
            or policy["engine_wheel_mode"] != "0400"
            or policy["engine_name"] != "nautilus_trader"
            or policy["engine_version"] != "1.227.0"
            or not isinstance(policy["python_identity"], str)
            or not str(policy["python_identity"]).startswith("CPython 3.12.")
            or not isinstance(policy["source_commit"], str)
            or _closure._SOURCE_COMMIT.fullmatch(str(policy["source_commit"])) is None
            or not isinstance(policy["engine_upstream_commit"], str)
            or _closure._SOURCE_COMMIT.fullmatch(str(policy["engine_upstream_commit"])) is None
            or policy["source_commit"] == policy["engine_upstream_commit"]
            or isinstance(policy["base_file_count"], bool)
            or not isinstance(policy["base_file_count"], int)
            or int(policy["base_file_count"]) <= 0
            or isinstance(policy["timeout_seconds"], bool)
            or not isinstance(policy["timeout_seconds"], int)
            or not 0 < int(policy["timeout_seconds"]) <= 3_600
        ):
            raise ValueError("runtime closure policy profile or identity is invalid")
        for field in (
            "artifact_manifest_sha256",
            "base_file_inventory_sha256",
            "base_runtime_manifest_sha256",
        ):
            _closure._require_sha256(policy[field], label=f"policy {field}")
        inventory = policy["launcher_inventory"]
        if not isinstance(inventory, list) or len(inventory) != len(
            _closure._LAUNCHER_INVENTORY
        ):
            raise ValueError("launcher inventory is invalid")
        observed_launchers: set[tuple[str, str]] = set()
        for record in inventory:
            if not isinstance(record, dict) or set(record) != {
                "mode",
                "sha256",
                "source",
                "target",
            }:
                raise ValueError("launcher inventory record is invalid")
            if record["mode"] != "0400":
                raise ValueError("launcher inventory mode is unsafe")
            _closure._require_sha256(record["sha256"], label="launcher inventory digest")
            source = _closure._safe_relative(
                record["source"], label="launcher source"
            ).as_posix()
            target = _closure._safe_target(
                record["target"], label="launcher target"
            ).as_posix()
            observed_launchers.add((source, target))
        if observed_launchers != set(_closure._LAUNCHER_INVENTORY):
            raise ValueError("launcher inventory is not the fixed strategy set")
        _closure._validate_native_guard_policy(policy["native_entry_guard"])
        _closure._safe_target(policy["engine_wheel_target"], label="engine wheel target")
        _closure._safe_target(policy["entrypoint"], label="closure entrypoint")
        return policy
    except (OSError, ValueError) as exc:
        raise SealedImportQualificationError(str(exc)) from exc


def _base_runtime_from_snapshots(
    base_runtime: Path,
    policy: dict[str, object],
    snapshots: dict[Path, tuple[bytes, tuple[int, ...], str, set[int] | None, bool, bool]],
) -> tuple[bytes, list[dict[str, object]], dict[str, bytes]]:
    try:
        _closure._sealed_directory(base_runtime, label="base runtime")
        manifest_raw = _remember_file(
            snapshots,
            base_runtime / "closure-manifest.json",
            label="base runtime manifest",
            modes={0o400},
        )
        if _sha256(manifest_raw) != policy["base_runtime_manifest_sha256"]:
            raise ValueError("base runtime manifest digest drifted")
        manifest = _closure._json_object(manifest_raw, label="base runtime manifest")
        if (
            set(manifest) != _closure._BASE_MANIFEST_FIELDS
            or manifest["schema_version"] != 1
            or manifest["engine_name"] != policy["engine_name"]
            or manifest["engine_version"] != policy["engine_version"]
            or manifest["python_identity"] != policy["python_identity"]
            or manifest["source_commit"] != policy["engine_upstream_commit"]
            or manifest["entrypoint"] != policy["argv_prefix"][0]
            or tuple(manifest["argv_prefix"])
            != ("-I", "-S", _closure._LAUNCHER_TARGET)
            or manifest["result_validator_id"] != "nautilus-backtest-result-v1"
        ):
            raise ValueError("base runtime profile or identity is invalid")
        records = manifest.get("files")
        if not isinstance(records, list) or len(records) != policy["base_file_count"]:
            raise ValueError("base runtime inventory is invalid")
        if _sha256(_canonical(records)) != policy["base_file_inventory_sha256"]:
            raise ValueError("base runtime inventory digest drifted")

        listed: set[PurePosixPath] = set()
        raw_by_path: dict[str, bytes] = {}
        for record in records:
            if not isinstance(record, dict) or set(record) != _closure._FILE_FIELDS:
                raise ValueError("base runtime inventory record is invalid")
            relative = _closure._safe_relative(
                record["path"], label="base runtime file path"
            )
            if not relative.is_relative_to(PurePosixPath("files")) or relative in listed:
                raise ValueError("base runtime inventory path is invalid")
            _closure._safe_target(record["target"], label="base runtime target")
            mode_text = record["mode"]
            if mode_text not in {"0400", "0500"}:
                raise ValueError("base runtime file mode is unsafe")
            raw = _remember_file(
                snapshots,
                base_runtime.joinpath(*relative.parts),
                label="base runtime file",
                modes={int(str(mode_text), 8)},
            )
            if (
                len(raw) != record["size"]
                or _sha256(raw) != record["sha256"]
            ):
                raise ValueError("base runtime inventory bytes, digest, or mode drifted")
            listed.add(relative)
            raw_by_path[relative.as_posix()] = raw
        files_root = base_runtime / "files"
        actual = {
            PurePosixPath("files", *path.relative_to(files_root).parts)
            for path in files_root.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        if actual != listed:
            raise ValueError("base runtime inventory contains an unlisted or missing file")
        for directory in (
            files_root,
            *(path for path in files_root.rglob("*") if path.is_dir()),
        ):
            observed = directory.lstat()
            if stat.S_ISLNK(observed.st_mode) or stat.S_IMODE(observed.st_mode) != 0o500:
                raise ValueError("base runtime directory mode is unsafe")
        return manifest_raw, records, raw_by_path
    except (OSError, ValueError) as exc:
        raise SealedImportQualificationError(str(exc)) from exc


def _artifact_from_snapshots(
    artifact_directory: Path,
    policy: dict[str, object],
    snapshots: dict[Path, tuple[bytes, tuple[int, ...], str, set[int] | None, bool, bool]],
) -> tuple[bytes, Path, bytes]:
    try:
        _closure._sealed_directory(artifact_directory, label="selected artifact directory")
        manifest_path = artifact_directory / "artifact-manifest.json"
        manifest_raw = _remember_file(
            snapshots,
            manifest_path,
            label="selected artifact manifest",
            modes={0o400},
        )
        if _sha256(manifest_raw) != policy["artifact_manifest_sha256"]:
            raise ValueError("selected artifact manifest digest drifted")
        manifest = _closure._json_object(manifest_raw, label="selected artifact manifest")
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
            raise ValueError("selected artifact identity or wheel is invalid")
        wheel_path = artifact_directory / str(wheel["filename"])
        wheel_raw = _remember_file(
            snapshots,
            wheel_path,
            label="selected engine wheel",
            modes={0o400},
        )
        if wheel.get("size") != len(wheel_raw) or wheel.get("sha256") != _sha256(
            wheel_raw
        ):
            raise ValueError("selected engine wheel digest drifted")
        if set(artifact_directory.iterdir()) != {manifest_path, wheel_path}:
            raise ValueError("selected artifact directory contains an unlisted file")
        return manifest_raw, wheel_path, wheel_raw
    except (OSError, ValueError) as exc:
        raise SealedImportQualificationError(str(exc)) from exc


def _repository_launcher_bytes(
    policy: dict[str, object],
    snapshots: dict[Path, tuple[bytes, tuple[int, ...], str, set[int] | None, bool, bool]],
) -> tuple[dict[str, tuple[bytes, int, Path]], list[dict[str, object]]]:
    launchers: dict[str, tuple[bytes, int, Path]] = {}
    manifest_records: list[dict[str, object]] = []
    for value in policy["launcher_inventory"]:
        if not isinstance(value, dict):
            raise SealedImportQualificationError("launcher inventory is invalid")
        source = _ROOT / str(value["source"])
        raw = _remember_file(
            snapshots,
            source,
            label="repository launcher source",
            source=True,
        )
        if _sha256(raw) != value["sha256"]:
            raise SealedImportQualificationError("repository launcher digest drifted")
        target = str(value["target"])
        mode = int(str(value["mode"]), 8)
        launchers[target] = (raw, mode, source)
        manifest_records.append(
            {
                "mode": f"{mode:04o}",
                "path": PurePosixPath("files", *PurePosixPath(target).parts[1:]).as_posix(),
                "sha256": _sha256(raw),
                "size": len(raw),
                "target": target,
            }
        )
    return launchers, sorted(manifest_records, key=lambda record: str(record["target"]))


def _minimal_manifest(records: list[dict[str, object]]) -> bytes:
    return _canonical({"files": records, "schema_version": 6}) + b"\n"


def _sealed_memfd(name: str, value: bytes, *, mode: int) -> int:
    descriptor = -1
    try:
        creator = getattr(os, "memfd_create", None)
        if callable(creator):
            descriptor = creator(name, _MFD_CLOEXEC | _MFD_ALLOW_SEALING)
        else:
            syscall_number = _MEMFD_CREATE_SYSCALLS.get(platform.machine().lower())
            if platform.system() != "Linux" or syscall_number is None:
                raise OSError("memfd_create is unavailable")
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            ctypes.set_errno(0)
            descriptor = int(
                libc.syscall(
                    ctypes.c_long(syscall_number),
                    ctypes.c_char_p(name.encode("ascii")),
                    ctypes.c_uint(_MFD_CLOEXEC | _MFD_ALLOW_SEALING),
                )
            )
            if descriptor < 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise OSError("sealed snapshot write made no progress")
            offset += written
        os.fchmod(descriptor, mode)
        fcntl.fcntl(descriptor, _F_ADD_SEALS, _ALL_SEALS)
        if fcntl.fcntl(descriptor, _F_GET_SEALS) != _ALL_SEALS:
            raise OSError("sealed snapshot cannot be proven")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SealedImportQualificationError("sealed memory snapshots are unavailable") from exc


def _directory_arguments(targets: tuple[PurePosixPath, ...]) -> tuple[str, ...]:
    directories: set[PurePosixPath] = set()
    for target in targets:
        parent = target.parent
        while parent != PurePosixPath("/"):
            directories.add(parent)
            parent = parent.parent
    ordered = sorted(directories, key=lambda value: (len(value.parts), value.as_posix()))
    return tuple(argument for directory in ordered for argument in ("--dir", directory.as_posix()))


def _sandbox_identity(sandbox_fd: int) -> bytes:
    common = {
        "cwd": Path("/"),
        "env": {},
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": False,
        "close_fds": True,
        "pass_fds": (sandbox_fd,),
        "timeout": 5,
    }
    try:
        executable = f"/proc/self/fd/{sandbox_fd}"
        version = subprocess.run((executable, "--version"), **common)
        help_result = subprocess.run((executable, "--help"), **common)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SealedImportQualificationError("Bubblewrap identity cannot be verified") from exc
    if (
        version.returncode != 0
        or version.stderr != b""
        or _BWRAP_VERSION.fullmatch(version.stdout) is None
        or help_result.returncode != 0
        or help_result.stderr != b""
        or b"--perms" not in help_result.stdout
        or b"--ro-bind-data" not in help_result.stdout
    ):
        raise SealedImportQualificationError("Bubblewrap identity or capabilities are invalid")
    return version.stdout[:-1]


def _parse_probe_result(raw: bytes, stderr: bytes, returncode: int) -> dict[str, object]:
    if returncode != 0 or stderr != b"" or not raw.endswith(b"\n"):
        raise SealedImportQualificationError("sealed import probe did not exit cleanly")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SealedImportQualificationError("sealed import probe result is invalid JSON") from exc
    if _canonical(document) + b"\n" != raw or not isinstance(document, dict):
        raise SealedImportQualificationError("sealed import probe result is not canonical")
    if (
        set(document) != {"schema_version", "status", "modules", "strategy_source_sha256"}
        or document["schema_version"] != _PROBE_SCHEMA
        or document["status"] != "passed"
        or _SHA256.fullmatch(str(document["strategy_source_sha256"])) is None
    ):
        raise SealedImportQualificationError("sealed import probe authority is malformed")
    modules = document["modules"]
    if not isinstance(modules, list) or len(modules) != 3:
        raise SealedImportQualificationError("sealed import probe module inventory is malformed")
    expected_names = ["nautilus_trader", "numpy", "pandas"]
    for expected, record in zip(expected_names, modules, strict=True):
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "version", "source_wheel_sha256"}
            or record["name"] != expected
            or not isinstance(record["version"], str)
            or _VERSION.fullmatch(record["version"]) is None
            or _SHA256.fullmatch(str(record["source_wheel_sha256"])) is None
        ):
            raise SealedImportQualificationError("sealed import probe module inventory is malformed")
    return document


def _publish_receipt(
    parent_fd: int,
    parent_path: Path,
    receipt_name: str,
    raw: bytes,
    parent_identity: tuple[int, int],
) -> None:
    if not _same_private_parent(parent_fd, parent_path, parent_identity):
        raise SealedImportQualificationError("receipt parent became stale")
    temporary_fd = -1
    temporary_name: str | None = None
    published = False
    completed = False
    try:
        for _attempt in range(128):
            candidate = f".{receipt_name}.{secrets.token_hex(16)}"
            try:
                temporary_fd = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o400,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd < 0 or temporary_name is None:
            raise OSError("receipt temporary name space is exhausted")
        opened = os.fstat(temporary_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o400
        ):
            raise SealedImportQualificationError("receipt staging identity is unsafe")
        offset = 0
        while offset < len(raw):
            written = os.write(temporary_fd, raw[offset:])
            if written <= 0:
                raise OSError("receipt write made no progress")
            offset += written
        os.fsync(temporary_fd)
        if not _same_private_parent(parent_fd, parent_path, parent_identity):
            raise SealedImportQualificationError("receipt parent became stale")
        os.link(
            temporary_name,
            receipt_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        published = True
        os.unlink(temporary_name, dir_fd=parent_fd)
        temporary_name = None
        if not _same_private_parent(parent_fd, parent_path, parent_identity):
            raise SealedImportQualificationError("receipt parent became stale")
        receipt_fd = os.open(
            receipt_name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            receipt = os.fstat(receipt_fd)
            chunks: list[bytes] = []
            while block := os.read(receipt_fd, 1024 * 1024):
                chunks.append(block)
        finally:
            os.close(receipt_fd)
        if (
            not stat.S_ISREG(receipt.st_mode)
            or stat.S_IMODE(receipt.st_mode) != 0o400
            or receipt.st_uid != os.geteuid()
            or receipt.st_nlink != 1
            or (receipt.st_dev, receipt.st_ino) != (opened.st_dev, opened.st_ino)
            or b"".join(chunks) != raw
        ):
            raise SealedImportQualificationError("published receipt identity is unsafe")
        if not _same_private_parent(parent_fd, parent_path, parent_identity):
            raise SealedImportQualificationError("receipt parent became stale")
        completed = True
    except FileExistsError as exc:
        raise SealedImportQualificationError("receipt already exists") from exc
    except SealedImportQualificationError:
        raise
    except OSError as exc:
        raise SealedImportQualificationError("receipt atomic no-clobber publication failed") from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        if published and not completed:
            try:
                os.unlink(receipt_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def _require_fresh_snapshots(
    snapshots: dict[Path, tuple[bytes, tuple[int, ...], str, set[int] | None, bool, bool]],
) -> None:
    try:
        for path in sorted(snapshots, key=str):
            raw, identity, label, modes, source, sandbox = snapshots[path]
            fresh_raw, fresh_identity = _read_file(
                path,
                label=label,
                modes=modes,
                source=source,
                sandbox=sandbox,
            )
            if fresh_raw != raw or fresh_identity != identity:
                raise ValueError(f"{label} bytes or identity changed")
    except (OSError, ValueError) as exc:
        raise SealedImportQualificationError(
            "named qualification input became stale"
        ) from exc


def qualify_sealed_imports(
    *,
    policy_path: Path,
    base_runtime: Path,
    artifact_directory: Path,
    sandbox_executable: Path,
    receipt_path: Path,
) -> Path:
    """Run one offline import-only sandbox and atomically write its digest receipt."""

    paths = {
        "policy": Path(policy_path),
        "base runtime": Path(base_runtime),
        "artifact directory": Path(artifact_directory),
        "sandbox": Path(sandbox_executable),
        "receipt": Path(receipt_path),
    }
    policy_path = paths["policy"]
    base_runtime = paths["base runtime"]
    artifact_directory = paths["artifact directory"]
    sandbox_executable = paths["sandbox"]
    receipt_path = paths["receipt"]
    for label, path in paths.items():
        _safe_absolute(path, label=label, must_exist=label != "receipt")
    parent_fd, parent_identity = _open_private_parent(receipt_path.parent)
    try:
        try:
            os.stat(receipt_path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SealedImportQualificationError("receipt already exists")

        snapshots: dict[
            Path,
            tuple[bytes, tuple[int, ...], str, set[int] | None, bool, bool],
        ] = {}
        policy_raw = _remember_file(
            snapshots,
            policy_path,
            label="runtime closure policy source",
            source=True,
        )
        policy = _policy_from_snapshot(policy_raw)
        sandbox_raw = _remember_file(
            snapshots,
            sandbox_executable,
            label="Bubblewrap executable",
            sandbox=True,
        )
        probe_raw = _remember_file(
            snapshots,
            _PROBE,
            label="sealed import probe source",
            source=True,
        )
        base_manifest_raw, records, base_files = _base_runtime_from_snapshots(
            base_runtime, policy, snapshots
        )
        artifact_manifest_raw, _selected_wheel, selected_wheel_raw = (
            _artifact_from_snapshots(artifact_directory, policy, snapshots)
        )
        launchers, launcher_records = _repository_launcher_bytes(policy, snapshots)
        launcher_by_target = {
            record["target"]: record for record in launcher_records
        }
        entry_target = str(policy["argv_prefix"][3])
        if entry_target not in launchers or str(_STRATEGY_TARGET) not in launchers:
            raise SealedImportQualificationError(
                "policy launchers do not cover qualification targets"
            )

        mounts: dict[PurePosixPath, tuple[bytes, int]] = {}
        wheel_inventory: list[dict[str, object]] = []
        seen_targets: set[str] = set()
        python_raw: bytes | None = None
        for record in records:
            target_text = str(record["target"])
            if target_text in seen_targets:
                raise SealedImportQualificationError(
                    "base runtime target inventory is duplicated"
                )
            seen_targets.add(target_text)
            target = PurePosixPath(target_text)
            mode = int(str(record["mode"]), 8)
            if target_text in launchers:
                raw, mode, _source_path = launchers[target_text]
            elif target_text == policy["engine_wheel_target"]:
                raw = selected_wheel_raw
                mode = int(str(policy["engine_wheel_mode"]), 8)
            else:
                raw = base_files[str(record["path"])]
            mounts[target] = (raw, mode)
            if target == _PYTHON_TARGET:
                if python_raw is not None or mode != 0o500:
                    raise SealedImportQualificationError(
                        "selected CPython inventory is invalid"
                    )
                python_raw = raw
            if target.parent == PurePosixPath("/engine/wheels"):
                wheel_inventory.append(
                    {
                        "filename": target.name,
                        "mode": f"{mode:04o}",
                        "sha256": _sha256(raw),
                        "size": len(raw),
                        "target": target.as_posix(),
                    }
                )
        for target_text, (raw, mode, _source_path) in launchers.items():
            mounts.setdefault(PurePosixPath(target_text), (raw, mode))
        if python_raw is None or not wheel_inventory:
            raise SealedImportQualificationError(
                "runtime lacks CPython or sealed wheels"
            )

        minimal_manifest = _minimal_manifest(launcher_records)
        mounts[_MANIFEST_TARGET] = (minimal_manifest, 0o400)
        mounts[_ENTRY_TARGET] = (launchers[entry_target][0], 0o400)
        mounts[_PROBE_TARGET] = (probe_raw, 0o400)

        descriptors: list[int] = []
        try:
            sandbox_fd = _sealed_memfd(
                "qualification-bwrap", sandbox_raw, mode=0o500
            )
            descriptors.append(sandbox_fd)
            _sandbox_identity(sandbox_fd)
            mounted_fds: list[tuple[PurePosixPath, int, int]] = []
            for target, (raw, mode) in sorted(
                mounts.items(), key=lambda item: item[0].as_posix()
            ):
                descriptor = _sealed_memfd(
                    f"qualification-{target.name}", raw, mode=mode
                )
                descriptors.append(descriptor)
                mounted_fds.append((target, mode, descriptor))
            targets = tuple(target for target, _mode, _fd in mounted_fds)
            mount_arguments = tuple(
                argument
                for target, mode, descriptor in mounted_fds
                for argument in (
                    "--perms",
                    f"{mode:04o}",
                    "--ro-bind-data",
                    str(descriptor),
                    target.as_posix(),
                )
            )
            argv = (
                f"/proc/self/fd/{sandbox_fd}",
                "--die-with-parent",
                "--unshare-user",
                "--unshare-pid",
                "--unshare-net",
                "--new-session",
                "--clearenv",
                *_directory_arguments(targets),
                *mount_arguments,
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--chdir",
                "/",
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "/qualification/import_probe.py",
                "--entry-launcher",
                "/qualification/entry-launcher.py",
                "--wheel-directory",
                "/engine/wheels",
            )
            try:
                result = subprocess.run(
                    argv,
                    cwd=Path("/"),
                    env={},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    close_fds=True,
                    pass_fds=tuple(descriptors),
                    timeout=min(int(policy["timeout_seconds"]), 120),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SealedImportQualificationError(
                    "sealed import probe execution failed"
                ) from exc
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

        probe_document = _parse_probe_result(
            result.stdout, result.stderr, result.returncode
        )
        wheel_digests = {record["sha256"] for record in wheel_inventory}
        if (
            probe_document["strategy_source_sha256"]
            != launcher_by_target[str(_STRATEGY_TARGET)]["sha256"]
            or any(
                record["source_wheel_sha256"] not in wheel_digests
                for record in probe_document["modules"]
            )
        ):
            raise SealedImportQualificationError(
                "probe result is not bound to the mounted inventory"
            )

        _require_fresh_snapshots(snapshots)
        modules = probe_document["modules"]
        receipt: dict[str, object] = {
            "schema_version": _RECEIPT_SCHEMA,
            "status": "passed",
            "profile": _PROFILE,
            "manifest_schema_version": 6,
            "dependency_import_policy": _DEPENDENCY_IMPORT_POLICY,
            "policy_sha256": _sha256(policy_raw),
            "base_runtime_manifest_sha256": _sha256(base_manifest_raw),
            "artifact_manifest_sha256": _sha256(artifact_manifest_raw),
            "python_sha256": _sha256(python_raw),
            "native_entry_guard_policy_sha256": _sha256(
                _canonical(policy["native_entry_guard"])
            ),
            "launcher_inventory_sha256": _sha256(
                _canonical(policy["launcher_inventory"])
            ),
            "entry_launcher_sha256": _sha256(launchers[entry_target][0]),
            "probe_sha256": _sha256(probe_raw),
            "strategy_source_sha256": launcher_by_target[str(_STRATEGY_TARGET)][
                "sha256"
            ],
            "minimal_manifest_sha256": _sha256(minimal_manifest),
            "wheel_inventory_sha256": _sha256(
                _canonical(
                    sorted(
                        wheel_inventory,
                        key=lambda record: str(record["filename"]),
                    )
                )
            ),
            "sandbox_sha256": _sha256(sandbox_raw),
            "sandbox_profile_sha256": _sha256(_SANDBOX_PROFILE),
            "probe_result_sha256": _sha256(_canonical(probe_document)),
            "modules": modules,
        }
        receipt["receipt_sha256"] = _sha256(_canonical(receipt))
        _publish_receipt(
            parent_fd,
            receipt_path.parent,
            receipt_path.name,
            _canonical(receipt) + b"\n",
            parent_identity,
        )
        return receipt_path
    finally:
        os.close(parent_fd)


def _abort(message: str) -> NoReturn:
    print(f"sealed import qualification failed: {message}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--base-runtime", required=True, type=Path)
    parser.add_argument("--artifact-directory", required=True, type=Path)
    parser.add_argument("--sandbox", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        qualify_sealed_imports(
            policy_path=arguments.policy,
            base_runtime=arguments.base_runtime,
            artifact_directory=arguments.artifact_directory,
            sandbox_executable=arguments.sandbox,
            receipt_path=arguments.receipt,
        )
    except SealedImportQualificationError as exc:
        _abort(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
