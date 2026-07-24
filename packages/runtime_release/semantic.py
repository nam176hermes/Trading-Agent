"""Stable semantic policy plus validation of the current rotating publication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable

from .config import (
    ProtectedAuthorityError,
    SemanticAuthority,
    read_protected_canonical_json,
    read_protected_canonical_json_current,
)


SEMANTIC_INPUT_ROOT = Path("/home/thenam176/.local/share/trading-agent/research-input")
CLASSIFICATION = "READ_ONLY_EXTERNAL_INPUT"
COMMAND = "SNAPSHOT"
SEMANTIC_POLICY_V1 = "phase4-semantic-policy/v1"
SEMANTIC_POLICY_V2 = "release-v2-semantic-policy/v1"
LOGICAL_FILES = {
    "macro_report": "reports/macro_report.json",
    "sentiment_report": "reports/sentiment_report.json",
    "onchain_report": "reports/onchain_report.json",
    "fred_cache": "memory/macro/fred_cache.json",
    "cross_asset_cache": "memory/macro/yf_macro_cache.json",
    "crypto_global_cache": "memory/macro/coingecko_global_cache.json",
}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,240}\.json\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_INPUT_DIRECTORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,220}\Z")
_MAX_BYTES = 4 * 1024 * 1024
_PLAN_FIELDS = {
    "schema_version", "classification", "command", "destination_root",
    "active_authority_path", "input_parent_attestation",
    "authority_parent_attestation", "manifest_version", "backend_commit",
    "runtime_uid", "runtime_gid", "generated_at", "validity_minutes", "sources",
}
_PLAN_SOURCE_FIELDS = {"path", "runtime_path", "device", "inode", "size", "sha256"}
_ATTESTATION_FIELDS = {"device", "inode", "uid", "gid", "mode"}


class SemanticAttestationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("semantic input attestation failed")


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    """Exact, non-secret identity of one validated rotating publication."""

    active_authority_sha256: str
    version_manifest_sha256: str
    semantic_input_fingerprint: str
    manifest_version: str
    generated_at: datetime
    expires_at: datetime
    policy_sha256: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def semantic_policy_digest(
    backend_commit: str,
    active_path: Path,
    *,
    input_root: Path = SEMANTIC_INPUT_ROOT,
) -> str:
    material = {
        "schema_version": "phase4-semantic-policy/v1",
        "backend_commit": backend_commit,
        "active_authority_path": str(active_path),
        "input_root": str(input_root),
        "classification": CLASSIFICATION,
        "command": COMMAND,
        "logical_files": LOGICAL_FILES,
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def semantic_policy_digest_v2(
    backend_commit: str,
    active_path: Path,
    *,
    input_root: Path,
) -> str:
    """Return the exact static Release Authority v2 producer-policy digest."""

    material = {
        "active_authority_path": str(active_path),
        "backend_commit": backend_commit,
        "classification": CLASSIFICATION,
        "command": COMMAND,
        "input_root": str(input_root),
        "schema_version": SEMANTIC_POLICY_V2,
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError
    return result.astimezone(UTC)


def _name(value: Any) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None or PurePosixPath(value).name != value:
        raise ValueError
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ValueError
    return value


def _read_input(
    path: Path,
    expected_digest: str,
    *,
    input_root: Path = SEMANTIC_INPUT_ROOT,
) -> tuple[int, str]:
    if not path.is_relative_to(input_root) or ".." in path.parts:
        raise ValueError
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | cloexec | nofollow
    file_flags = os.O_RDONLY | cloexec | nofollow
    descriptors: list[int] = []
    try:
        current = os.open(path.anchor, directory_flags)
        descriptors.append(current)
        for part in path.parts[1:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
            directory = os.fstat(current)
            if (
                not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(directory.st_mode) & 0o022
            ):
                raise ValueError
        fd = os.open(path.name, file_flags, dir_fd=current)
        descriptors.append(fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size > _MAX_BYTES
        ):
            raise ValueError
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, 65536):
            total += len(chunk)
            if total > _MAX_BYTES:
                raise ValueError
            digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected_digest):
            raise ValueError
        return info.st_size, digest.hexdigest()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _directory_attestation(path: Path, allowed_owners: set[int]) -> dict[str, int]:
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        current = os.open(path.anchor, flags)
        descriptors.append(current)
        for part in path.parts[1:]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
        info = os.fstat(current)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in allowed_owners
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise ValueError
        return {
            "device": info.st_dev,
            "inode": info.st_ino,
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode),
        }
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _current_parent_attestations(
    active_path: Path,
    *,
    input_root: Path = SEMANTIC_INPUT_ROOT,
) -> tuple[dict[str, int], dict[str, int]]:
    return (
        _directory_attestation(input_root, {0, os.geteuid()}),
        _directory_attestation(active_path.parent, {0}),
    )


def _valid_attestation(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _ATTESTATION_FIELDS
        and all(type(item) is int and item >= 0 for item in value.values())
        and value["inode"] > 0
    )


def _validate_plan(
    plan: object,
    *,
    active: dict[str, object],
    authority: SemanticAuthority,
    backend_commit: str,
    input_root: Path = SEMANTIC_INPUT_ROOT,
) -> dict[str, object]:
    if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS:
        raise ValueError
    validity = plan["validity_minutes"]
    if (
        plan["schema_version"] != "phase4-semantic-publication-plan/v1"
        or plan["classification"] != CLASSIFICATION
        or plan["command"] != COMMAND
        or plan["backend_commit"] != backend_commit
        or plan["manifest_version"] != active["manifest_version"]
        or plan["generated_at"] != active["generated_at"]
        or plan["active_authority_path"] != str(authority.authority_path)
        or plan["destination_root"] != str(input_root)
        or plan["runtime_uid"] != os.geteuid()
        or plan["runtime_gid"] != os.getegid()
        or type(validity) is not int
        or not 1 <= validity <= 30
    ):
        raise ValueError
    input_attestation, authority_attestation = (
        _current_parent_attestations(authority.authority_path)
        if input_root == SEMANTIC_INPUT_ROOT
        else _current_parent_attestations(
            authority.authority_path, input_root=input_root
        )
    )
    if (
        not _valid_attestation(plan["input_parent_attestation"])
        or not _valid_attestation(plan["authority_parent_attestation"])
        or plan["input_parent_attestation"] != input_attestation
        or plan["authority_parent_attestation"] != authority_attestation
    ):
        raise ValueError
    sources = plan["sources"]
    if not isinstance(sources, dict) or set(sources) != set(LOGICAL_FILES):
        raise ValueError
    paths: set[str] = set()
    runtime_paths: set[str] = set()
    inodes: set[tuple[int, int]] = set()
    for logical_name, expected_runtime in LOGICAL_FILES.items():
        source = sources[logical_name]
        if not isinstance(source, dict) or set(source) != _PLAN_SOURCE_FIELDS:
            raise ValueError
        source_path, runtime_path = source["path"], source["runtime_path"]
        pure_source = (
            PurePosixPath(source_path)
            if isinstance(source_path, str)
            else PurePosixPath(".")
        )
        pure_runtime = PurePosixPath(runtime_path) if isinstance(runtime_path, str) else PurePosixPath("/")
        if (
            not isinstance(source_path, str)
            or not pure_source.is_absolute()
            or source_path.startswith("//")
            or "\\" in source_path
            or any(ord(character) < 32 for character in source_path)
            or ".." in pure_source.parts
            or pure_source.as_posix() != source_path
            or not isinstance(runtime_path, str)
            or runtime_path != expected_runtime
            or pure_runtime.is_absolute()
            or ".." in pure_runtime.parts
            or pure_runtime.as_posix() != runtime_path
            or type(source["device"]) is not int
            or source["device"] < 0
            or type(source["inode"]) is not int
            or source["inode"] <= 0
            or type(source["size"]) is not int
            or not 0 <= source["size"] <= _MAX_BYTES
        ):
            raise ValueError
        _digest(source["sha256"])
        paths.add(source_path)
        runtime_paths.add(runtime_path)
        inodes.add((source["device"], source["inode"]))
    if len(paths) != 6 or len(runtime_paths) != 6 or len(inodes) != 6:
        raise ValueError
    return plan


def _attest_exact_tree(root: Path) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | nofollow
    descriptors: list[int] = []
    try:
        current = os.open(root.anchor, flags)
        descriptors.append(current)
        for part in root.parts[1:]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
            info = os.fstat(current)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise ValueError
        version_fd = current
        version_info = os.fstat(version_fd)
        if (
            version_info.st_uid != os.geteuid()
            or version_info.st_gid != os.getegid()
            or stat.S_IMODE(version_info.st_mode) != 0o500
        ):
            raise ValueError
        if set(os.listdir(version_fd)) != {"reports", "memory"}:
            raise ValueError
        reports_fd = os.open("reports", flags, dir_fd=version_fd)
        memory_fd = os.open("memory", flags, dir_fd=version_fd)
        descriptors.extend((reports_fd, memory_fd))
        macro_fd = os.open("macro", flags, dir_fd=memory_fd)
        descriptors.append(macro_fd)
        for descriptor in (reports_fd, memory_fd, macro_fd):
            info = os.fstat(descriptor)
            if (
                info.st_uid != os.geteuid()
                or info.st_gid != os.getegid()
                or stat.S_IMODE(info.st_mode) != 0o500
            ):
                raise ValueError
        expected_reports = {
            PurePosixPath(path).name for path in LOGICAL_FILES.values()
            if PurePosixPath(path).parts[0] == "reports"
        }
        expected_macro = {
            PurePosixPath(path).name for path in LOGICAL_FILES.values()
            if PurePosixPath(path).parts[:2] == ("memory", "macro")
        }
        if (
            set(os.listdir(reports_fd)) != expected_reports
            or set(os.listdir(memory_fd)) != {"macro"}
            or set(os.listdir(macro_fd)) != expected_macro
        ):
            raise ValueError
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def attest_current_semantic_inputs(
    authority: SemanticAuthority,
    backend_commit: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SemanticEvidence:
    """Validate one current publication while retaining a stable root policy."""

    try:
        input_root = authority.input_root or SEMANTIC_INPUT_ROOT
        if authority.policy_schema == SEMANTIC_POLICY_V1:
            expected_policy = semantic_policy_digest(
                backend_commit,
                authority.authority_path,
                input_root=input_root,
            )
        elif authority.policy_schema == SEMANTIC_POLICY_V2:
            expected_policy = semantic_policy_digest_v2(
                backend_commit,
                authority.authority_path,
                input_root=input_root,
            )
        else:
            raise ValueError
        if not hmac.compare_digest(authority.policy_sha256, expected_policy):
            raise ValueError
        active, active_authority_sha256 = read_protected_canonical_json_current(
            authority.authority_path
        )
        active_keys = {
            "schema_version", "classification", "generated_at", "manifest_version",
            "manifest_path", "manifest_sha256", "input_directory", "plan_digest",
            "plan_path", "plan_sha256",
        }
        if set(active) != active_keys or active["schema_version"] != 1 or active["classification"] != CLASSIFICATION:
            raise ValueError
        version = active["manifest_version"]
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            raise ValueError
        plan_digest = _digest(active["plan_digest"])
        if not hmac.compare_digest(plan_digest, _digest(active["plan_sha256"])):
            raise ValueError
        parent = authority.authority_path.parent
        raw_plan = read_protected_canonical_json(parent / _name(active["plan_path"]), plan_digest)
        plan = _validate_plan(
            raw_plan,
            active=active,
            authority=authority,
            backend_commit=backend_commit,
            input_root=input_root,
        )
        manifest = read_protected_canonical_json(
            parent / _name(active["manifest_path"]), _digest(active["manifest_sha256"])
        )
        if (
            plan.get("schema_version") != "phase4-semantic-publication-plan/v1"
            or plan.get("classification") != CLASSIFICATION
            or plan.get("command") != COMMAND
            or plan.get("backend_commit") != backend_commit
            or plan.get("manifest_version") != version
            or plan.get("active_authority_path") != str(authority.authority_path)
            or plan.get("destination_root") != str(input_root)
            or plan.get("runtime_uid") != os.geteuid()
            or plan.get("runtime_gid") != os.getegid()
        ):
            raise ValueError
        manifest_keys = {
            "schema_version", "manifest_version", "classification", "command",
            "backend_commit", "approved_root", "generated_at", "valid_until",
            "plan_digest", "plan_path", "plan_sha256", "files",
        }
        input_directory = active["input_directory"]
        if (
            not isinstance(input_directory, str)
            or _INPUT_DIRECTORY.fullmatch(input_directory) is None
            or PurePosixPath(input_directory).name != input_directory
        ):
            raise ValueError
        approved_root = input_root / input_directory
        if (
            set(manifest) != manifest_keys
            or manifest["schema_version"] != 1
            or manifest["manifest_version"] != version
            or manifest["classification"] != CLASSIFICATION
            or manifest["command"] != COMMAND
            or manifest["backend_commit"] != backend_commit
            or manifest["approved_root"] != str(approved_root)
            or manifest["plan_digest"] != plan_digest
            or manifest["plan_path"] != active["plan_path"]
            or manifest["plan_sha256"] != plan_digest
            or _time(manifest["generated_at"]).isoformat() != _time(active["generated_at"]).isoformat()
        ):
            raise ValueError
        now = clock().astimezone(UTC)
        generated, valid_until = _time(manifest["generated_at"]), _time(manifest["valid_until"])
        if (
            generated > now + timedelta(seconds=30)
            or now >= valid_until
            or generated >= valid_until
            or valid_until - generated != timedelta(minutes=plan["validity_minutes"])
        ):
            raise ValueError
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) != set(LOGICAL_FILES):
            raise ValueError
        _attest_exact_tree(approved_root)
        for logical_name, relative in LOGICAL_FILES.items():
            entry = files[logical_name]
            if (
                not isinstance(entry, dict)
                or set(entry) != {"path", "sha256", "required", "read_only"}
                or entry["path"] != relative
                or entry["required"] is not True
                or entry["read_only"] is not True
            ):
                raise ValueError
            expected_digest = _digest(entry["sha256"])
            source = plan["sources"][logical_name]
            if source["runtime_path"] != relative or source["sha256"] != expected_digest:
                raise ValueError
            size, actual_digest = (
                _read_input(approved_root / relative, expected_digest)
                if input_root == SEMANTIC_INPUT_ROOT
                else _read_input(
                    approved_root / relative,
                    expected_digest,
                    input_root=input_root,
                )
            )
            if size != source["size"] or not hmac.compare_digest(actual_digest, source["sha256"]):
                raise ValueError
        semantic_input_fingerprint = hashlib.sha256(
            _canonical(
                {
                    "manifest_sha256": active["manifest_sha256"],
                    "input_version": version,
                    "files": files,
                }
            )
        ).hexdigest()
        return SemanticEvidence(
            active_authority_sha256=active_authority_sha256,
            version_manifest_sha256=active["manifest_sha256"],
            semantic_input_fingerprint=semantic_input_fingerprint,
            manifest_version=version,
            generated_at=generated,
            expires_at=valid_until,
            policy_sha256=authority.policy_sha256,
        )
    except (ProtectedAuthorityError, OSError, ValueError, TypeError, KeyError):
        raise SemanticAttestationError() from None


__all__ = [
    "CLASSIFICATION", "COMMAND", "LOGICAL_FILES", "SEMANTIC_INPUT_ROOT",
    "SemanticAttestationError", "SemanticEvidence", "attest_current_semantic_inputs",
    "semantic_policy_digest", "semantic_policy_digest_v2",
]
