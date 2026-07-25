"""Externally attested, read-only semantic inputs for Phase 4 workers.

Research inputs are data, not authority. Authority is the root-owned active
record at a fixed protected path. It selects one immutable version manifest
and input tree; an absent or partially rotated authority fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


class ResearchSemanticInputError(RuntimeError):
    """The approved read-only input snapshot is absent or invalid."""


APPROVED_RESEARCH_INPUT_ROOT = Path(
    "/home/thenam176/.local/share/trading-agent/research-input"
)
APPROVED_MANIFEST_PATH = Path(
    "/etc/trading-agent/research-input-manifests/phase4-v1.json"
)
TRUSTED_MANIFEST_OWNER_UID = 0
TRUSTED_INPUT_PARENT_OWNER_UID = 0
EXPECTED_INPUT_OWNER_UID = os.geteuid()
EXPECTED_INPUT_OWNER_GID = os.getegid()

_MAX_INPUT_BYTES = 4 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_MANIFEST_AGE = timedelta(hours=1)
_MAX_VALIDITY_WINDOW = timedelta(minutes=30)
_MAX_CLOCK_SKEW = timedelta(seconds=30)
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_AUTHORITY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,240}\.json\Z")
_INPUT_DIRECTORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,220}\Z")
_CLASSIFICATION = "READ_ONLY_EXTERNAL_INPUT"
_COMMAND = "SNAPSHOT"
_MANIFEST_FIELDS = {
    "schema_version", "manifest_version", "classification", "command",
    "backend_commit", "approved_root", "generated_at", "valid_until",
    "plan_digest", "plan_path", "plan_sha256", "files",
}
_FILE_FIELDS = {"path", "sha256", "required", "read_only"}
_ACTIVE_FIELDS = {
    "schema_version", "classification", "generated_at", "manifest_version",
    "manifest_path", "manifest_sha256", "input_directory", "plan_digest",
    "plan_path", "plan_sha256",
}
_PLAN_FIELDS = {
    "schema_version", "classification", "command", "destination_root",
    "active_authority_path", "input_parent_attestation",
    "authority_parent_attestation", "manifest_version", "backend_commit",
    "runtime_uid", "runtime_gid", "generated_at", "validity_minutes", "sources",
}
_PLAN_SOURCE_FIELDS = {
    "path", "runtime_path", "device", "inode", "size", "sha256",
}
_ATTESTATION_FIELDS = {"device", "inode", "uid", "gid", "mode"}
_LOGICAL_FILES = (
    "macro_report", "sentiment_report", "onchain_report",
    "fred_cache", "cross_asset_cache", "crypto_global_cache",
)
_RUNTIME_PATHS = {
    "macro_report": "reports/macro_report.json",
    "sentiment_report": "reports/sentiment_report.json",
    "onchain_report": "reports/onchain_report.json",
    "fred_cache": "memory/macro/fred_cache.json",
    "cross_asset_cache": "memory/macro/yf_macro_cache.json",
    "crypto_global_cache": "memory/macro/coingecko_global_cache.json",
}
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _fail(message: str, exc: BaseException | None = None) -> None:
    error = ResearchSemanticInputError(message)
    if exc is None:
        raise error
    raise error from exc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _open_absolute_directory(path: Path, *, allowed_owners: set[int]) -> int:
    """Walk from ``/`` with retained dirfds; never validate then reopen a path."""
    if not path.is_absolute() or ".." in path.parts:
        _fail("trusted path is not canonical")
    current_fd = None
    try:
        current_fd = os.open(path.anchor, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        root_info = os.fstat(current_fd)
        if root_info.st_uid not in allowed_owners or stat.S_IMODE(root_info.st_mode) & 0o022:
            _fail("trusted filesystem root ownership or mode is unsafe")
        for part in path.parts[1:]:
            next_fd = os.open(
                part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=current_fd,
            )
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_fd)
                _fail(f"trusted path component is not a directory: {part}")
            if info.st_uid not in allowed_owners:
                os.close(next_fd)
                _fail(f"trusted path owner is unsafe: {part}")
            if stat.S_IMODE(info.st_mode) & 0o022:
                os.close(next_fd)
                _fail(f"trusted directory mode is unsafe: {part}")
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except ResearchSemanticInputError:
        if current_fd is not None:
            os.close(current_fd)
        raise
    except OSError as exc:
        if current_fd is not None:
            os.close(current_fd)
        _fail("trusted directory path cannot be opened safely", exc)


def _read_fd(fd: int, *, maximum: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, maximum + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            _fail(f"{label} is oversized")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_authority_file(parent_fd: int, name: str, label: str) -> bytes:
    file_fd = None
    try:
        file_fd = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            _fail(f"{label} is not a regular file")
        if info.st_uid != TRUSTED_MANIFEST_OWNER_UID:
            _fail(f"{label} owner is unsafe")
        if stat.S_IMODE(info.st_mode) != 0o444:
            _fail(f"{label} mode must be exactly 0444")
        return _read_fd(file_fd, maximum=_MAX_MANIFEST_BYTES, label=label)
    except ResearchSemanticInputError:
        raise
    except OSError as exc:
        _fail(f"{label} cannot be opened safely", exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _attestation(info: os.stat_result) -> dict[str, int]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def _valid_attestation(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _ATTESTATION_FIELDS
        and all(type(item) is int and item >= 0 for item in value.values())
        and value["inode"] > 0
    )


def _validate_plan_document(
    plan: Any,
    *, active_version: str, active_generated: str,
    authority_info: os.stat_result,
) -> Mapping[str, Any]:
    if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS:
        _fail("approved semantic plan schema is invalid")
    expected_backend = os.environ.get("TRADING_RESEARCH_BACKEND_COMMIT")
    if (
        plan["schema_version"] != "phase4-semantic-publication-plan/v1"
        or plan["classification"] != _CLASSIFICATION
        or plan["command"] != _COMMAND
        or plan["manifest_version"] != active_version
        or plan["generated_at"] != active_generated
        or not isinstance(expected_backend, str)
        or not _COMMIT.fullmatch(expected_backend)
        or plan["backend_commit"] != expected_backend
        or plan["destination_root"] != str(APPROVED_RESEARCH_INPUT_ROOT)
        or plan["active_authority_path"] != str(APPROVED_MANIFEST_PATH)
        or plan["runtime_uid"] != EXPECTED_INPUT_OWNER_UID
        or plan["runtime_gid"] != EXPECTED_INPUT_OWNER_GID
        or type(plan["validity_minutes"]) is not int
        or not 1 <= plan["validity_minutes"] <= 30
    ):
        _fail("approved semantic plan policy is invalid")
    if (
        not _valid_attestation(plan["input_parent_attestation"])
        or not _valid_attestation(plan["authority_parent_attestation"])
        or plan["authority_parent_attestation"] != _attestation(authority_info)
    ):
        _fail("approved semantic plan parent attestation is invalid")
    sources = plan["sources"]
    if not isinstance(sources, dict) or set(sources) != set(_LOGICAL_FILES):
        _fail("approved semantic plan source set is invalid")
    paths: set[str] = set()
    runtime_paths: set[str] = set()
    inodes: set[tuple[int, int]] = set()
    for logical_name in _LOGICAL_FILES:
        source = sources[logical_name]
        if not isinstance(source, dict) or set(source) != _PLAN_SOURCE_FIELDS:
            _fail("approved semantic plan source schema is invalid")
        source_path = source["path"]
        runtime_path = source["runtime_path"]
        if (
            not isinstance(source_path, str)
            or not Path(source_path).is_absolute()
            or ".." in Path(source_path).parts
            or str(Path(source_path)) != os.path.normpath(source_path)
            or not isinstance(runtime_path, str)
            or runtime_path != _RUNTIME_PATHS[logical_name]
        ):
            _fail("approved semantic plan source policy is invalid")
        pure_runtime = PurePosixPath(runtime_path)
        if (
            pure_runtime.is_absolute()
            or ".." in pure_runtime.parts
            or not pure_runtime.parts
            or pure_runtime.as_posix() != runtime_path
            or type(source["device"]) is not int
            or source["device"] < 0
            or type(source["inode"]) is not int
            or source["inode"] <= 0
            or type(source["size"]) is not int
            or not 0 <= source["size"] <= _MAX_INPUT_BYTES
            or not isinstance(source["sha256"], str)
            or not _HEX64.fullmatch(source["sha256"])
        ):
            _fail("approved semantic plan source policy is invalid")
        paths.add(source_path)
        runtime_paths.add(runtime_path)
        inodes.add((source["device"], source["inode"]))
    if len(paths) != 6 or len(runtime_paths) != 6 or len(inodes) != 6:
        _fail("approved semantic plan sources are not distinct")
    return MappingProxyType(plan)


def _open_external_manifest(
) -> tuple[bytes, str, str, str, str, str, str, Mapping[str, Any]]:
    parent_fd = _open_absolute_directory(
        APPROVED_MANIFEST_PATH.parent,
        allowed_owners={0, TRUSTED_MANIFEST_OWNER_UID},
    )
    try:
        active_raw = _read_authority_file(
            parent_fd, APPROVED_MANIFEST_PATH.name, "active semantic authority",
        )
        try:
            active = json.loads(active_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail("active semantic authority JSON is invalid", exc)
        if not isinstance(active, dict) or set(active) != _ACTIVE_FIELDS:
            _fail("active semantic authority fields are invalid")
        if active["schema_version"] != 1 or active["classification"] != _CLASSIFICATION:
            _fail("active semantic authority policy is invalid")
        version = active["manifest_version"]
        manifest_name = active["manifest_path"]
        input_directory = active["input_directory"]
        expected_digest = active["manifest_sha256"]
        plan_name = active["plan_path"]
        plan_digest = active["plan_digest"]
        plan_sha256 = active["plan_sha256"]
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            _fail("active semantic authority version is invalid")
        if (
            not isinstance(manifest_name, str)
            or not _AUTHORITY_NAME.fullmatch(manifest_name)
            or PurePosixPath(manifest_name).name != manifest_name
        ):
            _fail("active semantic authority manifest name is invalid")
        if (
            not isinstance(input_directory, str)
            or not _INPUT_DIRECTORY.fullmatch(input_directory)
            or PurePosixPath(input_directory).name != input_directory
        ):
            _fail("active semantic authority input directory is invalid")
        if not isinstance(expected_digest, str) or not _HEX64.fullmatch(expected_digest):
            _fail("active semantic authority digest is invalid")
        if not isinstance(plan_digest, str) or not _HEX64.fullmatch(plan_digest):
            _fail("active semantic authority plan digest is invalid")
        if (
            not isinstance(plan_name, str)
            or not _AUTHORITY_NAME.fullmatch(plan_name)
            or PurePosixPath(plan_name).name != plan_name
        ):
            _fail("active semantic authority plan name is invalid")
        if (
            not isinstance(plan_sha256, str)
            or not _HEX64.fullmatch(plan_sha256)
            or not hmac.compare_digest(plan_sha256, plan_digest)
        ):
            _fail("active semantic authority plan binding is invalid")
        active_generated = _parse_time(active["generated_at"], "active generated_at").isoformat()
        plan_raw = _read_authority_file(
            parent_fd, plan_name, "approved semantic plan",
        )
        actual_plan_digest = hashlib.sha256(plan_raw).hexdigest()
        if not hmac.compare_digest(actual_plan_digest, plan_digest):
            _fail("approved semantic plan digest mismatch")
        try:
            plan_document = json.loads(plan_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail("approved semantic plan JSON is invalid", exc)
        if not isinstance(plan_document, dict) or (
            json.dumps(plan_document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode() != plan_raw:
            _fail("approved semantic plan is not canonical")
        approved_plan = _validate_plan_document(
            plan_document,
            active_version=version,
            active_generated=active_generated,
            authority_info=os.fstat(parent_fd),
        )
        raw = _read_authority_file(
            parent_fd, manifest_name, "version semantic manifest",
        )
    finally:
        os.close(parent_fd)
    actual = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(actual, expected_digest):
        _fail("version semantic manifest digest mismatch")
    return (
        raw, actual, version, input_directory,
        plan_name, plan_digest, active_generated, approved_plan,
    )


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"external manifest {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail(f"external manifest {field} is invalid", exc)
    if parsed.tzinfo is None:
        _fail(f"external manifest {field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _parse_manifest(
    raw: bytes, root: Path, active_version: str,
    active_plan_path: str, active_plan_digest: str, active_generated_at: str,
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("external manifest JSON is invalid", exc)
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
        _fail("external manifest fields are invalid")
    if manifest["schema_version"] != 1:
        _fail("external manifest schema version is unsupported")
    version = manifest["manifest_version"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        _fail("external manifest version is invalid")
    if version != active_version:
        _fail("external manifest version does not match active authority")
    if manifest["classification"] != _CLASSIFICATION or manifest["command"] != _COMMAND:
        _fail("external manifest classification or command is invalid")
    expected_backend = os.environ.get("TRADING_RESEARCH_BACKEND_COMMIT")
    if not isinstance(expected_backend, str) or not _COMMIT.fullmatch(expected_backend):
        _fail("protected backend commit is not configured")
    if manifest["backend_commit"] != expected_backend:
        _fail("external manifest backend commit mismatch")
    if manifest["backend_commit"] != plan["backend_commit"]:
        _fail("external manifest plan lineage mismatch")
    if (
        manifest["plan_path"] != active_plan_path
        or manifest["plan_digest"] != active_plan_digest
        or manifest["plan_sha256"] != active_plan_digest
    ):
        _fail("external manifest plan binding mismatch")
    if _parse_time(manifest["generated_at"], "generated_at").isoformat() != active_generated_at:
        _fail("external manifest generated_at does not match active authority")
    if manifest["approved_root"] != str(root):
        _fail("external manifest approved root mismatch")
    generated = _parse_time(manifest["generated_at"], "generated_at")
    valid_until = _parse_time(manifest["valid_until"], "valid_until")
    now = _utc_now()
    if generated > now + _MAX_CLOCK_SKEW:
        _fail("external manifest generated_at is in the future")
    if generated >= valid_until or valid_until - generated > _MAX_VALIDITY_WINDOW:
        _fail("external manifest validity window is unsafe")
    if valid_until - generated != timedelta(minutes=plan["validity_minutes"]):
        _fail("external manifest validity does not match approved plan")
    if now - generated > _MAX_MANIFEST_AGE or now >= valid_until:
        _fail("external manifest is expired")
    files = manifest["files"]
    if not isinstance(files, dict) or set(files) != set(_LOGICAL_FILES):
        _fail("external manifest file set is invalid")
    seen_paths: set[str] = set()
    for logical_name in _LOGICAL_FILES:
        entry = files[logical_name]
        if not isinstance(entry, dict) or set(entry) != _FILE_FIELDS:
            _fail(f"external manifest file fields are invalid: {logical_name}")
        relative = entry["path"]
        digest = entry["sha256"]
        if not isinstance(relative, str):
            _fail(f"external manifest file path is invalid: {logical_name}")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            _fail(f"external manifest file path escapes root: {logical_name}")
        if relative in seen_paths:
            _fail("external manifest contains duplicate file paths")
        seen_paths.add(relative)
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            _fail(f"external manifest file digest is invalid: {logical_name}")
        if entry["required"] is not True or entry["read_only"] is not True:
            _fail(f"external manifest file policy is invalid: {logical_name}")
        source = plan["sources"][logical_name]
        if source["runtime_path"] != relative or source["sha256"] != digest:
            _fail("external manifest file does not match approved plan")
    return MappingProxyType(manifest), version


def _validate_input_directory(info: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(info.st_mode):
        _fail(f"semantic input directory is invalid: {label}")
    if info.st_uid != EXPECTED_INPUT_OWNER_UID:
        _fail(f"semantic input directory owner is unsafe: {label}")
    if info.st_gid != EXPECTED_INPUT_OWNER_GID:
        _fail(f"semantic input directory group is unsafe: {label}")
    if stat.S_IMODE(info.st_mode) != 0o500:
        _fail(f"semantic input directory mode is unsafe: {label}")


def _validate_input_parent(info: os.stat_result) -> None:
    if not stat.S_ISDIR(info.st_mode):
        _fail("semantic input parent is invalid")
    if info.st_uid != TRUSTED_INPUT_PARENT_OWNER_UID:
        _fail("semantic input parent owner is unsafe")
    if stat.S_IMODE(info.st_mode) & 0o022:
        _fail("semantic input parent mode is unsafe")


def _read_anchored_json(
    root_fd: int, relative: str, expected_hash: str, expected_size: int,
) -> Mapping[str, Any]:
    parts = PurePosixPath(relative).parts
    current_fd = os.dup(root_fd)
    file_fd = None
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
            _validate_input_directory(os.fstat(current_fd), part)
        file_fd = os.open(parts[-1], os.O_RDONLY | _NOFOLLOW, dir_fd=current_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            _fail(f"semantic input is not a regular file: {parts[-1]}")
        if info.st_uid != EXPECTED_INPUT_OWNER_UID:
            _fail(f"semantic input owner is unsafe: {parts[-1]}")
        if info.st_gid != EXPECTED_INPUT_OWNER_GID:
            _fail(f"semantic input group is unsafe: {parts[-1]}")
        if stat.S_IMODE(info.st_mode) != 0o400:
            _fail(f"semantic input mode is not private: {parts[-1]}")
        if info.st_size != expected_size:
            _fail("semantic input size does not match approved plan")
        raw = _read_fd(file_fd, maximum=_MAX_INPUT_BYTES, label=parts[-1])
    except ResearchSemanticInputError:
        raise
    except OSError as exc:
        _fail(f"semantic input cannot be opened safely: {relative}", exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(current_fd)
    actual = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(actual, expected_hash):
        _fail(f"semantic input hash mismatch: {relative}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"semantic input JSON is invalid: {relative}", exc)
    if not isinstance(value, dict):
        _fail(f"semantic input must be an object: {relative}")
    return MappingProxyType(value)


@dataclass(frozen=True, slots=True)
class SnapshotSemanticInputs:
    macro_report: Mapping[str, Any]
    sentiment_report: Mapping[str, Any]
    onchain_report: Mapping[str, Any]
    macro_snapshot: Mapping[str, Any]
    source_fingerprint: str
    input_version: str

    @property
    def macro_regime(self) -> tuple[str, float]:
        return (
            str(self.macro_report.get("regime", "neutral")),
            float(self.macro_report.get("regime_confidence", 0.5)),
        )

    def sentiment_for(self, symbol: str) -> dict[str, Any]:
        asset = self.sentiment_report.get("assets", {}).get(symbol.upper(), {})
        if not isinstance(asset, dict) or not asset:
            return {"sentiment": None, "sentiment_score": None}
        return {
            "sentiment": asset.get("sentiment"),
            "sentiment_score": asset.get("avg_score"),
            "sentiment_source": f"vader/{self.sentiment_report.get('source', 'unknown')}",
            "sentiment_distribution": {
                "positive": asset.get("positive_count", 0),
                "negative": asset.get("negative_count", 0),
                "neutral": asset.get("neutral_count", 0),
            },
            "sentiment_summary": (
                f"Sentiment: {asset.get('sentiment', 'neutral')} "
                f"({asset.get('avg_score', 0):+.2f})"
            ),
            "articles_found": asset.get("articles_found", 0),
            "articles_scored": asset.get("articles_found", 0),
            "articles_filtered": 0,
        }

    def onchain_for(self, symbol: str) -> dict[str, Any]:
        asset = self.onchain_report.get("assets", {}).get(symbol.upper(), {})
        if not isinstance(asset, dict) or not asset:
            return {"onchain_risk": None, "onchain_source": "unavailable — not found"}
        return {
            "onchain_risk": asset.get("onchain_risk"),
            "onchain_source": asset.get("onchain_source", "onchain_collector"),
        }


def load_snapshot_semantic_inputs(data_root: Path) -> SnapshotSemanticInputs:
    """Load one externally attested snapshot with descriptor-anchored reads."""
    root = Path(data_root)
    if root != APPROVED_RESEARCH_INPUT_ROOT:
        _fail("semantic input root is not the code-approved root")
    root_fd = _open_absolute_directory(
        root, allowed_owners={
            0, TRUSTED_INPUT_PARENT_OWNER_UID, EXPECTED_INPUT_OWNER_UID,
        },
    )
    version_fd = None
    try:
        _validate_input_parent(os.fstat(root_fd))
        (
            manifest_raw, manifest_digest, active_version, input_directory,
            active_plan_path, active_plan_digest, active_generated_at, approved_plan,
        ) = _open_external_manifest()
        if approved_plan["input_parent_attestation"] != _attestation(os.fstat(root_fd)):
            _fail("approved semantic plan input parent attestation mismatch")
        version_fd = os.open(
            input_directory, os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=root_fd,
        )
        _validate_input_directory(os.fstat(version_fd), input_directory)
        version_root = root / input_directory
        manifest, input_version = _parse_manifest(
            manifest_raw, version_root, active_version,
            active_plan_path, active_plan_digest, active_generated_at,
            approved_plan,
        )
        values = {
            logical_name: _read_anchored_json(
                version_fd,
                manifest["files"][logical_name]["path"],
                manifest["files"][logical_name]["sha256"],
                approved_plan["sources"][logical_name]["size"],
            )
            for logical_name in _LOGICAL_FILES
        }
    except ResearchSemanticInputError:
        raise
    except OSError as exc:
        _fail("semantic input root cannot be opened safely", exc)
    finally:
        if version_fd is not None:
            os.close(version_fd)
        os.close(root_fd)

    cache = MappingProxyType({
        "fred": values["fred_cache"],
        "cross_asset": values["cross_asset_cache"],
        "crypto_global": values["crypto_global_cache"],
    })
    fingerprint_material = json.dumps(
        {
            "manifest_sha256": manifest_digest,
            "input_version": input_version,
            "files": manifest["files"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return SnapshotSemanticInputs(
        macro_report=values["macro_report"],
        sentiment_report=values["sentiment_report"],
        onchain_report=values["onchain_report"],
        macro_snapshot=cache,
        source_fingerprint=hashlib.sha256(fingerprint_material).hexdigest(),
        input_version=input_version,
    )


__all__ = [
    "APPROVED_MANIFEST_PATH", "APPROVED_RESEARCH_INPUT_ROOT",
    "EXPECTED_INPUT_OWNER_GID", "EXPECTED_INPUT_OWNER_UID",
    "ResearchSemanticInputError", "SnapshotSemanticInputs",
    "TRUSTED_INPUT_PARENT_OWNER_UID", "TRUSTED_MANIFEST_OWNER_UID",
    "load_snapshot_semantic_inputs",
]
