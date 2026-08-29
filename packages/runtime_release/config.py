"""Fail-closed reader for the single Phase 4B protected authority document."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping

from packages.safety_evidence import (
    CANONICAL_SAFETY_SOURCE_ROOT,
    safety_source_fingerprint,
)

from .manifest import ReleasePolicy, verify_release
from .staging_v2 import (
    STAGING_SCOPE_ENV,
    StagingAuthorityMaterial,
    attest_staging_material,
    load_staging_authority_material,
    refresh_staging_dynamic_material,
)


AUTHORITY_PATH = Path("/etc/trading-agent/phase4-runtime-authority.json")
RUNTIME_AUTHORITY_V2_PATH = Path(
    "/etc/trading-agent-v2/release-authority-v2.json"
)
RELEASE_ACTIVATION_V2_PATH = Path(
    "/etc/trading-agent-v2/release-activation-v2.json"
)
_EXPECTED_UID = 0
_EXPECTED_GID = 0
_MAX_BYTES = 64 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_PYTHON = re.compile(r"CPython 3\.11\.\d+\Z")
_ROOT_KEYS = (
    "manifest_version", "application", "backend", "command_manifest",
    "semantic", "safety",
)
_RELEASE_KEYS = (
    "git_commit", "release_root", "manifest_path", "manifest_sha256",
    "python_path", "python_identity",
)


class ProtectedAuthorityError(RuntimeError):
    """Sanitized authority failure safe for readiness and service logs."""

    def __init__(self, reason_code: str = "RUNTIME_AUTHORITY_INVALID") -> None:
        self.reason_code = reason_code
        super().__init__("protected runtime authority is unavailable")

    def __repr__(self) -> str:
        return f"ProtectedAuthorityError(reason_code={self.reason_code!r})"


@dataclass(frozen=True, slots=True, repr=False)
class ReleaseAuthority:
    git_commit: str
    release_root: Path
    manifest_path: Path
    manifest_sha256: str
    python_path: Path
    python_identity: str


@dataclass(frozen=True, slots=True, repr=False)
class CommandManifestAuthority:
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class SemanticAuthority:
    authority_path: Path
    policy_sha256: str
    input_root: Path | None = None
    policy_schema: str = "phase4-semantic-policy/v1"


@dataclass(frozen=True, slots=True, repr=False)
class SafetyAuthority:
    exporter_commit: str
    snapshot_path: Path
    source_fingerprint: str
    expected_owner_uid: int = 0
    protected_root_owned: bool = True


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeAuthority:
    application: ReleaseAuthority
    backend: ReleaseAuthority
    command_manifest: CommandManifestAuthority
    semantic: SemanticAuthority
    safety: SafetyAuthority
    _identity: tuple[int, int]
    _document_sha256: str

    def recheck(self) -> "RuntimeAuthority":
        current = load_runtime_authority()
        if current._identity != self._identity or not hmac.compare_digest(
            current._document_sha256, self._document_sha256
        ):
            raise ProtectedAuthorityError("RUNTIME_AUTHORITY_CHANGED")
        return self

    def __repr__(self) -> str:
        return "RuntimeAuthority(validated=True)"


@dataclass(frozen=True, slots=True, repr=False)
class RuntimePathsV2:
    safety_snapshot: Path
    semantic_authority: Path
    semantic_input_root: Path
    reports_root: Path
    signals_root: Path
    scratch_root: Path
    artifact_root: Path


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


def _authority_v2_from_material(
    material: StagingAuthorityMaterial,
) -> RuntimeAuthorityV2:
    from .semantic import SemanticEvidence

    semantic_evidence = SemanticEvidence(
            policy_sha256=material.semantic_policy_sha256,
            active_authority_sha256=material.semantic_active_authority_sha256,
            version_manifest_sha256=material.semantic_version_manifest_sha256,
            semantic_input_fingerprint=material.semantic_input_fingerprint,
            manifest_version=material.semantic_manifest_version,
            generated_at=material.semantic_generated_at,
            expires_at=material.semantic_expires_at,
        )
    authority = RuntimeAuthorityV2(
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
    return authority


def load_runtime_authority_v2() -> RuntimeAuthorityV2:
    """Load an explicit Package 6 staging activation, never production."""

    if STAGING_SCOPE_ENV not in os.environ:
        raise ProtectedAuthorityError("RUNTIME_AUTHORITY_V2_UNAVAILABLE")
    try:
        return _authority_v2_from_material(load_staging_authority_material())
    except Exception:
        raise ProtectedAuthorityError("RUNTIME_AUTHORITY_V2_UNAVAILABLE") from None


def refresh_runtime_authority_v2(
    authority: RuntimeAuthorityV2,
) -> RuntimeAuthorityV2:
    """Refresh rotating evidence without rewalking an attested sealed stage."""

    try:
        if not isinstance(authority, RuntimeAuthorityV2) or authority._material is None:
            raise ValueError
        return _authority_v2_from_material(
            refresh_staging_dynamic_material(authority._material)
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


def _exact_dict(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value.keys()) != keys:
        raise ValueError("invalid schema")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("invalid digest")
    return value


def _commit(value: Any) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise ValueError("invalid commit")
    return value


def _absolute(value: Any) -> Path:
    if not isinstance(value, str):
        raise ValueError("invalid path")
    pure = PurePosixPath(value)
    if not pure.is_absolute() or ".." in pure.parts or pure.as_posix() != value:
        raise ValueError("invalid path")
    return Path(value)


def _release(value: Any, kind: str) -> ReleaseAuthority:
    item = _exact_dict(value, _RELEASE_KEYS)
    commit = _commit(item["git_commit"])
    expected_root = Path(f"/opt/trading-agent-phase4/releases/{kind}-{commit}")
    expected_manifest = Path(f"/opt/trading-agent-phase4/manifests/{kind}-{commit}.manifest.json")
    root = _absolute(item["release_root"])
    manifest = _absolute(item["manifest_path"])
    python = _absolute(item["python_path"])
    if root != expected_root or manifest != expected_manifest or python != root / ".venv/bin/python3.11":
        raise ValueError("invalid release binding")
    identity = item["python_identity"]
    if not isinstance(identity, str) or _PYTHON.fullmatch(identity) is None:
        raise ValueError("invalid interpreter")
    return ReleaseAuthority(commit, root, manifest, _digest(item["manifest_sha256"]), python, identity)


def _parse(raw: bytes, identity: tuple[int, int]) -> RuntimeAuthority:
    try:
        document = json.loads(raw, object_pairs_hook=_pairs)
        root = _exact_dict(document, _ROOT_KEYS)
        if root["manifest_version"] != 1 or raw != _canonical(document) + b"\n":
            raise ValueError("noncanonical")
        application = _release(root["application"], "app")
        backend = _release(root["backend"], "backend")
        command = _exact_dict(root["command_manifest"], ("path", "sha256"))
        expected_command = Path(
            f"/opt/trading-agent-phase4/manifests/commands-{backend.git_commit}.json"
        )
        command_path = _absolute(command["path"])
        if command_path != expected_command:
            raise ValueError("invalid command binding")
        semantic = _exact_dict(root["semantic"], ("authority_path", "policy_sha256"))
        semantic_path = _absolute(semantic["authority_path"])
        if semantic_path != Path("/etc/trading-agent/research-input-manifests/phase4-v1.json"):
            raise ValueError("invalid semantic binding")
        safety = _exact_dict(root["safety"], ("exporter_commit", "snapshot_path", "source_fingerprint"))
        snapshot_path = _absolute(safety["snapshot_path"])
        expected_snapshot = Path(f"/run/user/{os.geteuid()}/trading-agent/safety-state.json")
        expected_fingerprint = safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT)
        if (
            snapshot_path != expected_snapshot
            or safety["exporter_commit"] != application.git_commit
            or safety["source_fingerprint"] != expected_fingerprint
        ):
            raise ValueError("invalid safety path")
        authority = RuntimeAuthority(
            application=application,
            backend=backend,
            command_manifest=CommandManifestAuthority(command_path, _digest(command["sha256"])),
            semantic=SemanticAuthority(semantic_path, _digest(semantic["policy_sha256"])),
            safety=SafetyAuthority(
                _commit(safety["exporter_commit"]), snapshot_path, _digest(safety["source_fingerprint"])
            ),
            _identity=identity,
            _document_sha256=hashlib.sha256(raw).hexdigest(),
        )
        return authority
    except ProtectedAuthorityError:
        raise
    except Exception:
        raise ProtectedAuthorityError() from None


def load_runtime_authority() -> RuntimeAuthority:
    raw, identity = _read_protected_file(AUTHORITY_PATH)
    return _parse(raw, identity)


def _runtime_python_path() -> Path:
    return Path(sys.executable)


def attest_application_release(authority: RuntimeAuthority) -> bool:
    """Verify the application release bound by this exact loaded authority."""

    if not isinstance(authority, RuntimeAuthority):
        raise ProtectedAuthorityError("APPLICATION_RELEASE_INVALID") from None
    release = authority.application
    try:
        if _runtime_python_path() != release.python_path:
            raise ValueError("interpreter path mismatch")
        return verify_release(
            release.release_root,
            release.manifest_path,
            release.manifest_sha256,
            ReleasePolicy(
                release_type="phase4-app",
                expected_git_commit=release.git_commit,
                expected_python_identity=release.python_identity,
            ),
        )
    except Exception:
        raise ProtectedAuthorityError("APPLICATION_RELEASE_INVALID") from None


def attest_application_authority() -> bool:
    """Load the fixed authority and verify its complete application release."""

    return attest_application_release(load_runtime_authority())


__all__ = [
    "AUTHORITY_PATH", "CommandManifestAuthority", "ProtectedAuthorityError",
    "ReleaseAuthority", "RuntimeAuthority", "SafetyAuthority", "SemanticAuthority",
    "attest_application_authority", "attest_application_release",
    "load_runtime_authority", "read_protected_canonical_json",
    "read_protected_canonical_json_current", "read_protected_file_current",
    "RELEASE_ACTIVATION_V2_PATH",
    "RUNTIME_AUTHORITY_V2_PATH", "RuntimeAuthorityV2",
    "RuntimePathsV2",
    "attest_application_release_v2", "load_runtime_authority_v2",
    "refresh_runtime_authority_v2",
]
