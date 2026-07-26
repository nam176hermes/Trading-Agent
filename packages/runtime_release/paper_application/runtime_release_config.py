"""Canonical v2-only protected runtime authority surface."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping

from packages.runtime_release.semantic import SemanticEvidence
from packages.runtime_release.staging_v2 import (
    STAGING_SCOPE_ENV,
    StagingAuthorityMaterial,
    attest_staging_material,
    load_staging_authority_material,
)

RUNTIME_AUTHORITY_V2_PATH = Path("/etc/trading-agent-v2/release-authority-v2.json")
RELEASE_ACTIVATION_V2_PATH = Path("/etc/trading-agent-v2/release-activation-v2.json")
_EXPECTED_UID = 0
_EXPECTED_GID = 0
_MAX_BYTES = 64 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

class ProtectedAuthorityError(RuntimeError):
    """Sanitized authority failure safe for readiness and service logs."""

    def __init__(self, reason_code: str = "RUNTIME_AUTHORITY_INVALID") -> None:
        self.reason_code = reason_code
        super().__init__("protected runtime authority is unavailable")

    def __repr__(self) -> str:
        return f"ProtectedAuthorityError(reason_code={self.reason_code!r})"

@dataclass(frozen=True, slots=True, repr=False)
class RuntimePathsV2:
    safety_snapshot: Path
    semantic_authority: Path
    semantic_input_root: Path
    reports_root: Path
    signals_root: Path
    scratch_root: Path
    artifact_root: Path


@dataclass(frozen=True, slots=True, repr=False)
class SemanticAuthority:
    authority_path: Path
    policy_sha256: str
    input_root: Path | None = None


@dataclass(frozen=True, slots=True, repr=False)
class SafetyAuthority:
    exporter_commit: str
    snapshot_path: Path
    source_fingerprint: str
    expected_owner_uid: int = 0
    protected_root_owned: bool = True


@dataclass(frozen=True, repr=False)
class RuntimeAuthorityV2:
    """Factory-issued v2 authority; production loading remains unavailable."""

    source_commit: str
    source_tree: str
    installation_root: Path
    application_root: Path
    application_python: Path
    backend_root: Path
    backend_python: Path
    backend_artifact_sha256: str
    command_manifest: Mapping[str, object]
    safety: object
    semantic: object
    runtime_paths: object
    safety_evidence: object
    semantic_evidence: object
    _authority_pin: tuple[object, ...]
    _dynamic_evidence_pin: tuple[object, ...]
    scope: str = "PRODUCTION"
    package6_approval_sha256: str = ""
    production_release_authority_sha256: str = ""
    stage_file_set_sha256: str = ""
    application_artifact_sha256: str = ""
    application_python_sha256: str = ""
    backend_python_sha256: str = ""
    command_authority_sha256: str = ""
    _material: StagingAuthorityMaterial | None = None

    def recheck(self) -> "RuntimeAuthorityV2":
        current = load_runtime_authority_v2()
        if current._authority_pin != self._authority_pin:
            raise ProtectedAuthorityError("RUNTIME_AUTHORITY_CHANGED")
        return current

    def __repr__(self) -> str:
        return "RuntimeAuthorityV2(validated=True)"


def _runtime_python_path() -> Path:
    return Path(sys.executable)


def load_runtime_authority_v2() -> RuntimeAuthorityV2:
    """Load an explicit Package 6 staging activation, never production."""

    if STAGING_SCOPE_ENV not in os.environ:
        raise ProtectedAuthorityError("RUNTIME_AUTHORITY_V2_UNAVAILABLE")
    try:
        material = load_staging_authority_material()
        semantic_evidence = SemanticEvidence(
            policy_sha256=material.semantic_policy_sha256,
            active_authority_sha256=material.semantic_active_authority_sha256,
            version_manifest_sha256=material.semantic_version_manifest_sha256,
            semantic_input_fingerprint=material.semantic_input_fingerprint,
            manifest_version=material.semantic_manifest_version,
            generated_at=material.semantic_generated_at,
            expires_at=material.semantic_expires_at,
        )
        return RuntimeAuthorityV2(
            source_commit=material.source_commit,
            source_tree=material.source_tree,
            installation_root=material.installation_root,
            application_root=material.application_root,
            application_python=material.application_python,
            backend_root=material.backend_root,
            backend_python=material.backend_python,
            backend_artifact_sha256=material.backend_artifact_sha256,
            command_manifest=material.command_manifest,
            safety=SafetyAuthority(
                exporter_commit=material.safety_exporter_commit,
                snapshot_path=material.runtime_paths["safety_snapshot"],
                source_fingerprint=material.safety_source_fingerprint,
                expected_owner_uid=os.geteuid(),
                protected_root_owned=False,
            ),
            semantic=SemanticAuthority(
                authority_path=material.runtime_paths["semantic_authority"],
                policy_sha256=material.semantic_policy_sha256,
                input_root=material.runtime_paths["semantic_input_root"],
            ),
            runtime_paths=RuntimePathsV2(
                safety_snapshot=material.runtime_paths["safety_snapshot"],
                semantic_authority=material.runtime_paths["semantic_authority"],
                semantic_input_root=material.runtime_paths["semantic_input_root"],
                reports_root=material.runtime_paths["reports_root"],
                signals_root=material.runtime_paths["signals_root"],
                scratch_root=material.runtime_paths["scratch_root"],
                artifact_root=material.runtime_paths["artifact_root"],
            ),
            safety_evidence=material.safety_snapshot_sha256,
            semantic_evidence=semantic_evidence,
            _authority_pin=material.authority_pin,
            _dynamic_evidence_pin=material.dynamic_evidence_pin,
            scope=material.scope,
            package6_approval_sha256=material.package6_approval_sha256,
            production_release_authority_sha256=(
                material.production_release_authority_sha256
            ),
            stage_file_set_sha256=material.stage_file_set_sha256,
            application_artifact_sha256=material.application_artifact_sha256,
            application_python_sha256=material.application_python_sha256,
            backend_python_sha256=material.backend_python_sha256,
            command_authority_sha256=material.command_authority_sha256,
            _material=material,
        )
    except Exception:
        raise ProtectedAuthorityError("RUNTIME_AUTHORITY_V2_UNAVAILABLE") from None


def attest_application_release_v2(authority: RuntimeAuthorityV2) -> bool:
    """Recompute the staging artifact while production remains fail-closed."""

    if not isinstance(authority, RuntimeAuthorityV2):
        return False
    if authority.scope != "PACKAGE6_STAGING_ONLY":
        return False
    return attest_staging_material(
        authority._material,
        runtime_python_path=_runtime_python_path(),
    )

def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _safe_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == _EXPECTED_UID
        and metadata.st_gid == _EXPECTED_GID
        and not metadata.st_mode & (0o022 | 0o7000)
    )


def _read_protected_file(path: Path) -> tuple[bytes, tuple[int, int]]:
    if not path.is_absolute() or ".." in path.parts:
        raise ProtectedAuthorityError()
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags_file = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(path.anchor, flags_dir)
        descriptors.append(current)
        if not _safe_directory(os.fstat(current)):
            raise ProtectedAuthorityError()
        for part in path.parts[1:-1]:
            current = os.open(part, flags_dir, dir_fd=current)
            descriptors.append(current)
            if not _safe_directory(os.fstat(current)):
                raise ProtectedAuthorityError()
        file_fd = os.open(path.name, flags_file, dir_fd=current)
        descriptors.append(file_fd)
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != _EXPECTED_UID
            or metadata.st_gid != _EXPECTED_GID
            or stat.S_IMODE(metadata.st_mode) != 0o444
            or metadata.st_size > _MAX_BYTES
        ):
            raise ProtectedAuthorityError()
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(file_fd, 8192):
            total += len(chunk)
            if total > _MAX_BYTES:
                raise ProtectedAuthorityError()
            chunks.append(chunk)
        final = os.fstat(file_fd)
        if (
            (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        ):
            raise ProtectedAuthorityError("RUNTIME_AUTHORITY_CHANGED")
        return b"".join(chunks), (metadata.st_dev, metadata.st_ino)
    except ProtectedAuthorityError:
        raise
    except (OSError, ValueError):
        raise ProtectedAuthorityError() from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def read_protected_file_current(path: Path) -> bytes:
    """Read one fixed root-owned 0444 file through its complete safe path."""

    raw, _ = _read_protected_file(path)
    return raw


def read_protected_canonical_json(path: Path, expected_digest: str) -> dict[str, Any]:
    """Read another authority-bound root-owned JSON file without path disclosure."""

    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise ProtectedAuthorityError()
    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        raise ProtectedAuthorityError()
    raw, _ = _read_protected_file(path)
    try:
        document = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(document, dict) or raw != _canonical(document) + b"\n":
            raise ValueError("noncanonical")
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_digest):
            raise ValueError("digest mismatch")
        return document
    except Exception:
        raise ProtectedAuthorityError() from None


def read_protected_canonical_json_current(path: Path) -> tuple[dict[str, Any], str]:
    """Read a root-owned rotating document whose path is already protected."""

    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise ProtectedAuthorityError()
    raw, _ = _read_protected_file(path)
    try:
        document = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(document, dict) or raw != _canonical(document) + b"\n":
            raise ValueError("noncanonical")
        return document, hashlib.sha256(raw).hexdigest()
    except Exception:
        raise ProtectedAuthorityError() from None

__all__ = [
    "ProtectedAuthorityError", "RELEASE_ACTIVATION_V2_PATH",
    "RUNTIME_AUTHORITY_V2_PATH", "RuntimeAuthorityV2", "RuntimePathsV2",
    "attest_application_release_v2", "load_runtime_authority_v2",
    "read_protected_canonical_json", "read_protected_canonical_json_current",
    "read_protected_file_current",
]
