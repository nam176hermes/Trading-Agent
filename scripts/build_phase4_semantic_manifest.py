#!/usr/bin/env python3
"""Plan and publish versioned, externally attested Phase 4 semantic inputs."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping


class SemanticManifestBuildError(RuntimeError):
    """The requested semantic-input publication is unsafe or incomplete."""


LOGICAL_DESTINATIONS: dict[str, str] = {
    "macro_report": "reports/macro_report.json",
    "sentiment_report": "reports/sentiment_report.json",
    "onchain_report": "reports/onchain_report.json",
    "fred_cache": "memory/macro/fred_cache.json",
    "cross_asset_cache": "memory/macro/yf_macro_cache.json",
    "crypto_global_cache": "memory/macro/coingecko_global_cache.json",
}
ROOT_AUTHORITY_UID = 0
ROOT_AUTHORITY_GID = 0
CLASSIFICATION = "READ_ONLY_EXTERNAL_INPUT"
COMMAND = "SNAPSHOT"
MAX_CLOCK_SKEW = timedelta(seconds=30)

_MAX_INPUT_BYTES = 4 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_SOURCE_MARKERS = (
    ".env", "credential", "secret", "token", "password", "private_key",
    "api_key", "kill_switch", ".mode", "safety",
)


@dataclass(frozen=True, slots=True)
class _Source:
    path: Path
    device: int
    inode: int
    content: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class SemanticManifestBuildResult:
    applied: bool
    plan_digest: str
    generated_at: str
    manifest_version: str
    destination_classification: str = CLASSIFICATION
    idempotent: bool = False


def _fail(message: str, exc: BaseException | None = None) -> None:
    error = SemanticManifestBuildError(message)
    if exc is None:
        raise error
    raise error from exc


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_absolute(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or str(path) != os.path.normpath(str(path)):
        _fail(f"{label} must be an absolute canonical path")
    return path


def _open_directory_path(path: Path, *, trusted_final: bool) -> int:
    """Open an absolute directory one no-follow component at a time."""
    path = _canonical_absolute(path, "trusted directory")
    current_fd: int | None = None
    try:
        current_fd = os.open(path.anchor, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        info = os.fstat(current_fd)
        if not stat.S_ISDIR(info.st_mode):
            _fail("trusted destination is not a directory")
        if trusted_final:
            if info.st_uid != ROOT_AUTHORITY_UID:
                _fail("trusted destination owner is unsafe")
            if stat.S_IMODE(info.st_mode) & 0o022:
                _fail("trusted destination mode is unsafe")
        return current_fd
    except SemanticManifestBuildError:
        if current_fd is not None:
            os.close(current_fd)
        raise
    except OSError as exc:
        if current_fd is not None:
            os.close(current_fd)
        _fail("trusted destination symlink or path was rejected", exc)


def _assert_directory_identity(path: Path, retained_fd: int) -> None:
    check_fd = _open_directory_path(path, trusted_final=True)
    try:
        expected = os.fstat(retained_fd)
        actual = os.fstat(check_fd)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            _fail("trusted destination changed during publication")
    finally:
        os.close(check_fd)


def _require_runtime_traversal(info: os.stat_result, uid: int, gid: int) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if uid == info.st_uid:
        allowed = bool(mode & stat.S_IXUSR)
    elif gid == info.st_gid:
        allowed = bool(mode & stat.S_IXGRP)
    else:
        allowed = bool(mode & stat.S_IXOTH)
    if not allowed:
        _fail("trusted destination cannot be traversed by runtime identity")


def _directory_attestation(info: os.stat_result) -> dict[str, int]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def _read_source(path: Path) -> _Source:
    path = _canonical_absolute(path, "explicit source")
    lowered = "/".join(path.parts).lower()
    if any(marker in lowered for marker in _FORBIDDEN_SOURCE_MARKERS):
        _fail("credential or safety named sources are forbidden")
    if path.suffix.lower() != ".json":
        _fail("each explicit source must be a JSON file")
    parent_fd = _open_directory_path(path.parent, trusted_final=False)
    file_fd: int | None = None
    try:
        file_fd = os.open(path.name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            _fail("explicit source is not a regular file")
        if info.st_size > _MAX_INPUT_BYTES:
            _fail("explicit source is oversized")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(65536, _MAX_INPUT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_INPUT_BYTES:
                _fail("explicit source is oversized")
        content = b"".join(chunks)
    except SemanticManifestBuildError:
        raise
    except OSError as exc:
        _fail("explicit source symlink or unsafe path was rejected", exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("explicit source contains invalid JSON", exc)
    if not isinstance(payload, dict):
        _fail("explicit source JSON must be an object")
    return _Source(path, info.st_dev, info.st_ino, content, hashlib.sha256(content).hexdigest())


def _write_all(fd: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            _fail("publication write did not make progress")
        remaining = remaining[written:]


def _create_directory(parent_fd: int, name: str) -> int:
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    return os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent_fd)


def _seal_directory(fd: int, runtime_uid: int, runtime_gid: int) -> None:
    os.fchown(fd, runtime_uid, runtime_gid)
    os.fchmod(fd, 0o500)


def _write_runtime_file(parent_fd: int, name: str, content: bytes, uid: int, gid: int) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=parent_fd)
    try:
        _write_all(fd, content)
        os.fsync(fd)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, 0o400)
    finally:
        os.close(fd)


def _write_authority_file(parent_fd: int, name: str, content: bytes, *, exclusive: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | _NOFOLLOW
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
    try:
        _write_all(fd, content)
        os.fsync(fd)
        os.fchown(fd, ROOT_AUTHORITY_UID, ROOT_AUTHORITY_GID)
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)


def _validate_existing_active(parent_fd: int, name: str) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        _fail("existing active authority is unsafe", exc)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != ROOT_AUTHORITY_UID
            or info.st_gid != ROOT_AUTHORITY_GID
        ):
            _fail("existing active authority is unsafe")
        if stat.S_IMODE(info.st_mode) != 0o444:
            _fail("existing active authority mode is unsafe")
    finally:
        os.close(fd)


def _open_publication_lock(parent_fd: int, active_name: str) -> int:
    lock_name = f".{active_name}.lock"
    try:
        fd = os.open(lock_name, os.O_RDWR | _NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        _fail("preprovisioned publication lock is unavailable", exc)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != ROOT_AUTHORITY_UID
            or info.st_gid != ROOT_AUTHORITY_GID
        ):
            _fail("publication lock ownership is unsafe")
        if stat.S_IMODE(info.st_mode) != 0o600:
            _fail("publication lock mode is unsafe")
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_existing_active(parent_fd: int, name: str) -> dict[str, object] | None:
    try:
        fd = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("existing active authority is unsafe", exc)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_AUTHORITY_UID:
            _fail("existing active authority is unsafe")
        if stat.S_IMODE(info.st_mode) != 0o444:
            _fail("existing active authority mode is unsafe")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, 65537 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 65536:
                _fail("existing active authority is oversized")
    finally:
        os.close(fd)
    try:
        value = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("existing active authority is invalid", exc)
    if not isinstance(value, dict):
        _fail("existing active authority is invalid")
    return value


def _parse_aware_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail(f"{label} is invalid", exc)
    if parsed.tzinfo is None:
        _fail(f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def _validate_apply_clock(plan: Mapping[str, object], clock: Callable[[], datetime]) -> None:
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        _fail("apply clock must return a timezone-aware timestamp")
    now = now.astimezone(timezone.utc)
    generated = _parse_aware_utc(plan["generated_at"], "plan generated_at")
    valid_until = generated + timedelta(minutes=int(plan["validity_minutes"]))
    if generated > now + MAX_CLOCK_SKEW:
        _fail("approved plan generated_at is in the future")
    if now >= valid_until:
        _fail("approved plan is expired")


def _activation_decision(
    current: Mapping[str, object] | None,
    expected: Mapping[str, object],
) -> bool:
    """Return true only for an exact idempotent current activation."""
    if current is None:
        return False
    if set(current) != set(expected):
        _fail("existing active authority fields are invalid")
    if current.get("schema_version") != 1 or current.get("classification") != CLASSIFICATION:
        _fail("existing active authority policy is invalid")
    for key in ("manifest_path", "plan_path", "input_directory"):
        value = current.get(key)
        if not isinstance(value, str) or PurePosixPath(value).name != value:
            _fail("existing active authority reference is invalid")
    for key in ("manifest_sha256", "plan_digest", "plan_sha256"):
        value = current.get(key)
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            _fail("existing active authority digest is invalid")
    current_generated = _parse_aware_utc(
        current.get("generated_at"), "current active generated_at",
    )
    proposed_generated = _parse_aware_utc(expected["generated_at"], "plan generated_at")
    current_version = current.get("manifest_version")
    current_digest = current.get("plan_digest")
    if (
        proposed_generated == current_generated
        and expected["manifest_version"] == current_version
        and expected["plan_digest"] == current_digest
    ):
        if current != expected:
            _fail("idempotent activation requires exact active authority")
        return True
    if proposed_generated <= current_generated:
        _fail("active semantic publication must be monotonic")
    if expected["manifest_version"] == current_version:
        _fail("active semantic publication version must advance")
    return False


def _read_exact_authority_file(
    parent_fd: int, name: str, expected: bytes,
) -> None:
    fd = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != ROOT_AUTHORITY_UID
            or info.st_gid != ROOT_AUTHORITY_GID
            or stat.S_IMODE(info.st_mode) != 0o444
            or info.st_size != len(expected)
        ):
            raise ValueError("authority metadata mismatch")
        chunks: list[bytes] = []
        remaining = len(expected) + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if b"".join(chunks) != expected:
            raise ValueError("authority content mismatch")
    finally:
        os.close(fd)


def _validate_runtime_directory(fd: int, uid: int, gid: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != 0o500
    ):
        raise ValueError("runtime directory metadata mismatch")


def _validate_runtime_file(
    parent_fd: int, name: str, source: _Source, uid: int, gid: int,
) -> None:
    fd = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != uid
            or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size != len(source.content)
        ):
            raise ValueError("runtime file metadata mismatch")
        chunks: list[bytes] = []
        remaining = len(source.content) + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if content != source.content or hashlib.sha256(content).hexdigest() != source.sha256:
            raise ValueError("runtime file content mismatch")
    finally:
        os.close(fd)


def _validate_idempotent_publication(
    *, authority_fd: int, input_fd: int, active_name: str,
    active: Mapping[str, object],
    plan_raw: bytes, manifest_raw: bytes, records: Mapping[str, _Source],
    runtime_uid: int, runtime_gid: int,
) -> None:
    try:
        _read_exact_authority_file(
            authority_fd, active_name, _canonical_bytes(active),
        )
        _read_exact_authority_file(authority_fd, str(active["plan_path"]), plan_raw)
        _read_exact_authority_file(
            authority_fd, str(active["manifest_path"]), manifest_raw,
        )
        version_fd = os.open(
            str(active["input_directory"]),
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=input_fd,
        )
        reports_fd = memory_fd = macro_fd = None
        try:
            _validate_runtime_directory(version_fd, runtime_uid, runtime_gid)
            if set(os.listdir(version_fd)) != {"reports", "memory"}:
                raise ValueError("runtime root exact set mismatch")
            reports_fd = os.open(
                "reports", os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=version_fd,
            )
            memory_fd = os.open(
                "memory", os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=version_fd,
            )
            macro_fd = os.open(
                "macro", os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=memory_fd,
            )
            for fd in (reports_fd, memory_fd, macro_fd):
                _validate_runtime_directory(fd, runtime_uid, runtime_gid)
            reports = {
                PurePosixPath(path).name
                for path in LOGICAL_DESTINATIONS.values()
                if PurePosixPath(path).parts[0] == "reports"
            }
            macro = {
                PurePosixPath(path).name
                for path in LOGICAL_DESTINATIONS.values()
                if PurePosixPath(path).parts[:2] == ("memory", "macro")
            }
            if set(os.listdir(reports_fd)) != reports:
                raise ValueError("reports exact set mismatch")
            if set(os.listdir(memory_fd)) != {"macro"}:
                raise ValueError("memory exact set mismatch")
            if set(os.listdir(macro_fd)) != macro:
                raise ValueError("macro exact set mismatch")
            parent_fds = {"reports": reports_fd, "memory/macro": macro_fd}
            for logical_name, relative in LOGICAL_DESTINATIONS.items():
                pure = PurePosixPath(relative)
                _validate_runtime_file(
                    parent_fds["/".join(pure.parts[:-1])],
                    pure.name,
                    records[logical_name],
                    runtime_uid,
                    runtime_gid,
                )
        finally:
            for fd in (macro_fd, memory_fd, reports_fd):
                if fd is not None:
                    os.close(fd)
            os.close(version_fd)
    except Exception as exc:
        _fail("existing active publication is invalid", exc)


def _cleanup_version(input_fd: int, version_name: str) -> None:
    try:
        version_fd = os.open(version_name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=input_fd)
    except OSError:
        return
    try:
        for directory, filenames in (
            ("reports", ("macro_report.json", "sentiment_report.json", "onchain_report.json")),
            ("memory/macro", ("fred_cache.json", "yf_macro_cache.json", "coingecko_global_cache.json")),
        ):
            current = os.dup(version_fd)
            try:
                for part in PurePosixPath(directory).parts:
                    next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=current)
                    os.close(current)
                    current = next_fd
                for filename in filenames:
                    try:
                        os.unlink(filename, dir_fd=current)
                    except FileNotFoundError:
                        pass
            except OSError:
                pass
            finally:
                os.close(current)
        for directory in ("memory/macro", "memory", "reports"):
            try:
                os.rmdir(directory, dir_fd=version_fd)
            except OSError:
                pass
    finally:
        os.close(version_fd)
    try:
        os.rmdir(version_name, dir_fd=input_fd)
    except OSError:
        pass


def _prepare_plan(
    *, sources: Mapping[str, Path], destination_root: Path, manifest_path: Path,
    manifest_version: str, backend_commit: str, runtime_uid: int, runtime_gid: int,
    generated_at: datetime, validity_minutes: int,
    expected_source_attestations: Mapping[str, tuple[int, int, int, str]] | None,
) -> tuple[dict[str, _Source], dict[str, object], str, str]:
    if set(sources) != set(LOGICAL_DESTINATIONS) or len(sources) != 6:
        _fail("exactly six named semantic sources are required")
    if not isinstance(manifest_version, str) or not _VERSION.fullmatch(manifest_version):
        _fail("manifest version is invalid")
    if not isinstance(backend_commit, str) or not _COMMIT.fullmatch(backend_commit):
        _fail("backend commit is invalid")
    if type(runtime_uid) is not int or runtime_uid < 1 or type(runtime_gid) is not int or runtime_gid < 1:
        _fail("runtime identity is invalid")
    if not isinstance(generated_at, datetime) or generated_at.tzinfo is None:
        _fail("generated_at must be fixed and timezone-aware")
    if type(validity_minutes) is not int or not 1 <= validity_minutes <= 30:
        _fail("validity must be between one and thirty minutes")
    destination_root = _canonical_absolute(destination_root, "destination root")
    manifest_path = _canonical_absolute(manifest_path, "active authority path")
    if "releases" in destination_root.parts:
        _fail("semantic inputs are external input, not an immutable release")
    if manifest_path.parent == destination_root:
        _fail("active authority must remain outside the input root")

    input_fd = _open_directory_path(destination_root, trusted_final=True)
    authority_fd = _open_directory_path(manifest_path.parent, trusted_final=True)
    try:
        input_info = os.fstat(input_fd)
        authority_info = os.fstat(authority_fd)
        _require_runtime_traversal(input_info, runtime_uid, runtime_gid)
        _require_runtime_traversal(authority_info, runtime_uid, runtime_gid)
        input_attestation = _directory_attestation(input_info)
        authority_attestation = _directory_attestation(authority_info)
    finally:
        os.close(input_fd)
        os.close(authority_fd)
    records = {name: _read_source(Path(sources[name])) for name in LOGICAL_DESTINATIONS}
    if expected_source_attestations is not None:
        if set(expected_source_attestations) != set(LOGICAL_DESTINATIONS):
            _fail("selected source identity set is invalid")
        for name, record in records.items():
            expected = expected_source_attestations[name]
            if (
                not isinstance(expected, tuple) or len(expected) != 4
                or any(type(value) is not int or value < 0 for value in expected[:3])
                or not isinstance(expected[3], str) or not _HEX64.fullmatch(expected[3])
                or expected != (
                    record.device, record.inode, len(record.content), record.sha256,
                )
            ):
                _fail("selected source identity changed before consumption")
    paths = {str(record.path) for record in records.values()}
    inodes = {(record.device, record.inode) for record in records.values()}
    if len(paths) != 6 or len(inodes) != 6:
        _fail("all six explicit sources must have distinct canonical paths and inodes")
    generated = generated_at.astimezone(timezone.utc).isoformat()
    plan = {
        "schema_version": "phase4-semantic-publication-plan/v1",
        "classification": CLASSIFICATION,
        "command": COMMAND,
        "destination_root": str(destination_root),
        "active_authority_path": str(manifest_path),
        "input_parent_attestation": input_attestation,
        "authority_parent_attestation": authority_attestation,
        "manifest_version": manifest_version,
        "backend_commit": backend_commit,
        "runtime_uid": runtime_uid,
        "runtime_gid": runtime_gid,
        "generated_at": generated,
        "validity_minutes": validity_minutes,
        "sources": {
            name: {
                "path": str(record.path), "device": record.device,
                "runtime_path": LOGICAL_DESTINATIONS[name],
                "inode": record.inode, "size": len(record.content), "sha256": record.sha256,
            }
            for name, record in records.items()
        },
    }
    plan_digest = hashlib.sha256(_canonical_bytes(plan)).hexdigest()
    version_name = f"snapshot-{manifest_version}-{plan_digest[:16]}"
    return records, plan, plan_digest, version_name


def build_semantic_manifest(
    *, sources: Mapping[str, Path], destination_root: Path, manifest_path: Path,
    manifest_version: str, backend_commit: str, runtime_uid: int, runtime_gid: int,
    generated_at: datetime, validity_minutes: int = 30, apply: bool = False,
    approved_plan_digest: str | None = None,
    expected_source_attestations: Mapping[str, tuple[int, int, int, str]] | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> SemanticManifestBuildResult:
    """Plan a publication, or apply only an exact previously approved plan."""
    records, plan, plan_digest, version_name = _prepare_plan(
        sources=sources, destination_root=destination_root, manifest_path=manifest_path,
        manifest_version=manifest_version, backend_commit=backend_commit,
        runtime_uid=runtime_uid, runtime_gid=runtime_gid,
        generated_at=generated_at, validity_minutes=validity_minutes,
        expected_source_attestations=expected_source_attestations,
    )
    result = SemanticManifestBuildResult(
        applied=apply, plan_digest=plan_digest, generated_at=plan["generated_at"],
        manifest_version=manifest_version,
    )
    if not apply:
        return result
    if os.geteuid() != 0:
        _fail("apply requires root authority")
    if approved_plan_digest is None or not _HEX64.fullmatch(approved_plan_digest):
        _fail("apply requires an approved dry-run plan digest")
    if not hmac.compare_digest(plan_digest, approved_plan_digest):
        _fail("approved plan digest does not match current inputs")
    destination_root = Path(destination_root)
    manifest_path = Path(manifest_path)
    stem = manifest_path.name[:-5] if manifest_path.name.endswith(".json") else manifest_path.name
    plan_raw = _canonical_bytes(plan)
    plan_name = f"{stem}.{manifest_version}.{plan_digest[:16]}.plan.json"
    generated = datetime.fromisoformat(plan["generated_at"])
    manifest = {
        "schema_version": 1,
        "manifest_version": manifest_version,
        "classification": CLASSIFICATION,
        "command": COMMAND,
        "backend_commit": backend_commit,
        "approved_root": str(destination_root / version_name),
        "generated_at": generated.isoformat(),
        "valid_until": (generated + timedelta(minutes=validity_minutes)).isoformat(),
        "plan_digest": plan_digest,
        "plan_path": plan_name,
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
        "files": {
            logical_name: {
                "path": LOGICAL_DESTINATIONS[logical_name],
                "sha256": records[logical_name].sha256,
                "required": True,
                "read_only": True,
            }
            for logical_name in LOGICAL_DESTINATIONS
        },
    }
    manifest_raw = _canonical_bytes(manifest)
    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    manifest_name = f"{stem}.{manifest_version}.{plan_digest[:16]}.manifest.json"
    expected_active = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "generated_at": plan["generated_at"],
        "manifest_version": manifest_version,
        "manifest_path": manifest_name,
        "manifest_sha256": manifest_digest,
        "input_directory": version_name,
        "plan_digest": plan_digest,
        "plan_path": plan_name,
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
    }
    input_fd = _open_directory_path(destination_root, trusted_final=True)
    authority_fd = _open_directory_path(manifest_path.parent, trusted_final=True)
    lock_fd: int | None = None
    version_created = False
    plan_created = False
    manifest_created = False
    active_temp: str | None = None
    active_published = False
    try:
        _assert_directory_identity(destination_root, input_fd)
        _assert_directory_identity(manifest_path.parent, authority_fd)
        lock_fd = _open_publication_lock(authority_fd, manifest_path.name)
        _assert_directory_identity(destination_root, input_fd)
        _assert_directory_identity(manifest_path.parent, authority_fd)
        current = _read_existing_active(authority_fd, manifest_path.name)
        idempotent = _activation_decision(current, expected_active)
        _validate_apply_clock(plan, clock)
        if idempotent:
            _validate_idempotent_publication(
                authority_fd=authority_fd,
                input_fd=input_fd,
                active_name=manifest_path.name,
                active=expected_active,
                plan_raw=plan_raw,
                manifest_raw=manifest_raw,
                records=records,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
            )
            return SemanticManifestBuildResult(
                applied=False,
                plan_digest=plan_digest,
                generated_at=plan["generated_at"],
                manifest_version=manifest_version,
                idempotent=True,
            )
        version_fd = _create_directory(input_fd, version_name)
        version_created = True
        reports_fd = memory_fd = macro_fd = None
        try:
            reports_fd = _create_directory(version_fd, "reports")
            memory_fd = _create_directory(version_fd, "memory")
            macro_fd = _create_directory(memory_fd, "macro")
            parent_fds = {"reports": reports_fd, "memory/macro": macro_fd}
            for logical_name, relative in LOGICAL_DESTINATIONS.items():
                pure = PurePosixPath(relative)
                _write_runtime_file(
                    parent_fds["/".join(pure.parts[:-1])], pure.name,
                    records[logical_name].content, runtime_uid, runtime_gid,
                )
            for fd in (macro_fd, memory_fd, reports_fd):
                _seal_directory(fd, runtime_uid, runtime_gid)
            _seal_directory(version_fd, runtime_uid, runtime_gid)
            os.fsync(version_fd)
        finally:
            for fd in (macro_fd, memory_fd, reports_fd):
                if fd is not None:
                    os.close(fd)
            os.close(version_fd)

        _write_authority_file(authority_fd, plan_name, plan_raw, exclusive=True)
        plan_created = True
        _write_authority_file(authority_fd, manifest_name, manifest_raw, exclusive=True)
        manifest_created = True
        active_temp = f".{manifest_path.name}.{secrets.token_hex(12)}.tmp"
        _write_authority_file(
            authority_fd, active_temp, _canonical_bytes(expected_active), exclusive=True,
        )
        _assert_directory_identity(destination_root, input_fd)
        _assert_directory_identity(manifest_path.parent, authority_fd)
        os.replace(
            active_temp, manifest_path.name,
            src_dir_fd=authority_fd, dst_dir_fd=authority_fd,
        )
        active_temp = None
        active_published = True
        os.fsync(authority_fd)
        os.fsync(input_fd)
    except Exception:
        if active_temp is not None:
            try:
                os.unlink(active_temp, dir_fd=authority_fd)
            except OSError:
                pass
        if not active_published and manifest_created:
            try:
                os.unlink(manifest_name, dir_fd=authority_fd)
            except OSError:
                pass
        if not active_published and plan_created:
            try:
                os.unlink(plan_name, dir_fd=authority_fd)
            except OSError:
                pass
        if not active_published and version_created:
            _cleanup_version(input_fd, version_name)
        raise
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(authority_fd)
        os.close(input_fd)
    return result


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("must include a timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    for logical_name in LOGICAL_DESTINATIONS:
        parser.add_argument(f"--{logical_name.replace('_', '-')}", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    parser.add_argument("--manifest-path", required=True, type=Path)
    parser.add_argument("--manifest-version", required=True)
    parser.add_argument("--backend-commit", required=True)
    parser.add_argument("--runtime-uid", required=True, type=int)
    parser.add_argument("--runtime-gid", required=True, type=int)
    parser.add_argument("--generated-at", required=True, type=_parse_time)
    parser.add_argument("--validity-minutes", type=int, default=30)
    parser.add_argument("--approved-plan-digest")
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = build_semantic_manifest(
            sources={name: getattr(args, name) for name in LOGICAL_DESTINATIONS},
            destination_root=args.destination_root, manifest_path=args.manifest_path,
            manifest_version=args.manifest_version, backend_commit=args.backend_commit,
            runtime_uid=args.runtime_uid, runtime_gid=args.runtime_gid,
            generated_at=args.generated_at, validity_minutes=args.validity_minutes,
            apply=args.apply, approved_plan_digest=args.approved_plan_digest,
        )
    except SemanticManifestBuildError:
        print("semantic input publication rejected", file=sys.stderr)
        return 2
    print(_canonical_bytes({
        "applied": result.applied,
        "classification": result.destination_classification,
        "generated_at": result.generated_at,
        "manifest_version": result.manifest_version,
        "plan_digest": result.plan_digest,
    }).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
