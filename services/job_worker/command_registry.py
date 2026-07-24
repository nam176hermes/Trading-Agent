"""Code-owned, manifest-attested commands for the research worker.

This module never starts a process. Task 9 must call
``prepare_immediate_spawn`` in the spawn path; it attests the complete release,
issues a short-lived capability, consumes it, and re-attests before returning.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import sys
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from packages.job_contracts import JobType, SnapshotPayload, parse_payload
from packages.runtime_release import (
    ReleasePolicy,
    load_runtime_authority as _load_runtime_authority,
    read_protected_canonical_json,
    verify_release as _verify_release,
)
from packages.runtime_release.config import (
    RuntimeAuthority,
    RuntimeAuthorityV2,
    RuntimePathsV2,
    attest_application_release_v2 as _attest_application_release_v2,
    load_runtime_authority_v2 as _load_runtime_authority_v2,
)
from packages.runtime_release.semantic import SemanticEvidence, attest_current_semantic_inputs
from packages.runtime_release.backend_policy import APPROVED_PHASE4_BACKEND_COMMIT

from .errors import CommandRegistryError

APPROVED_BACKEND_REVISION = APPROVED_PHASE4_BACKEND_COMMIT
APPROVED_BACKEND_CWD = Path("/opt/trading-agent-phase4/releases") / f"backend-{APPROVED_BACKEND_REVISION}"
APPROVED_BACKEND_PYTHON = APPROVED_BACKEND_CWD / ".venv/bin/python3.11"
APPROVED_RELEASE_MANIFEST_PATH = Path("/opt/trading-agent-phase4/manifests") / f"backend-{APPROVED_BACKEND_REVISION}.manifest.json"
# Both one-use authorities enclose one complete application/backend/semantic
# re-attestation.  The reviewed legacy backend is roughly 5 GiB / 28k files,
# so sub-second lifetimes are operationally impossible.  Five minutes remains
# bounded while leaving cold-cache scan headroom; rollout must benchmark the
# exact v2 candidate and stop if its full scan p99 exceeds two minutes.
FULL_REATTESTATION_ROLLOUT_LIMIT_SECONDS = 120
PRESPAWN_FULL_REATTESTATION_COUNT = 3
_FULL_REATTESTATION_AUTHORITY_TTL_NS = 5 * 60 * 1_000_000_000
_CAPABILITY_TTL_NS = _FULL_REATTESTATION_AUTHORITY_TTL_NS
_PREPARED_SPAWN_TTL_NS = _FULL_REATTESTATION_AUTHORITY_TTL_NS


def _blocked(reason: str, message: str) -> None:
    raise CommandRegistryError(reason, message)


def _lstat(path: Path) -> os.stat_result:
    """Private seam used only so unit fixtures can model uid-0 artifacts."""

    return path.lstat()


def _listxattr(path: Path) -> tuple[str, ...]:
    """List attributes without following links; private for test fixtures."""

    return tuple(os.listxattr(path, follow_symlinks=False))


def _validate_no_xattrs(path: Path, reason: str) -> None:
    try:
        attributes = _listxattr(path)
    except OSError as exc:
        raise CommandRegistryError(reason, "protected path extended attributes cannot be inspected") from exc
    if attributes:
        _blocked(reason, "protected path has extended attributes")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CommandRegistryError("COMMAND_RELEASE_FILE_UNREADABLE", "release artifact cannot be read") from exc
    return digest.hexdigest()


def _canonical_absolute(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        _blocked("COMMAND_PATH_NOT_CANONICAL", "release authority path is not canonical")


def _validate_ancestor_chain(path: Path) -> None:
    _canonical_absolute(path)
    current = Path(path.anchor)
    for part in (None, *path.parts[1:-1]):
        if part is not None:
            current /= part
        try:
            info = _lstat(current)
        except FileNotFoundError:
            _blocked("COMMAND_ANCESTOR_MISSING", "release authority ancestor is missing")
        except OSError as exc:
            raise CommandRegistryError("COMMAND_PATH_UNREADABLE", "release authority ancestor cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            _blocked("COMMAND_ANCESTOR_SYMLINK", "release authority ancestor is a symlink")
        if not stat.S_ISDIR(info.st_mode):
            _blocked("COMMAND_ANCESTOR_MISSING", "release authority ancestor is not a directory")
        if info.st_uid != 0:
            _blocked("COMMAND_ANCESTOR_OWNER_UNSAFE", "release authority ancestor is not root-owned")
        if info.st_mode & (0o022 | 0o7000):
            _blocked("COMMAND_ANCESTOR_MODE_UNSAFE", "release authority ancestor has unsafe mode bits")
        _validate_no_xattrs(current, "COMMAND_ANCESTOR_XATTR_UNSAFE")


def _validate_artifact(path: Path, *, directory: bool, executable: bool = False) -> os.stat_result:
    _validate_ancestor_chain(path)
    try:
        info = _lstat(path)
    except FileNotFoundError:
        _blocked("COMMAND_CWD_MISSING" if directory else "COMMAND_ARTIFACT_MISSING", "release authority is missing")
    except OSError as exc:
        raise CommandRegistryError("COMMAND_PATH_UNREADABLE", "release authority cannot be inspected") from exc
    if stat.S_ISLNK(info.st_mode):
        _blocked("COMMAND_RELEASE_SYMLINK", "release authority is a symlink")
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct_type:
        _blocked("COMMAND_ARTIFACT_MISSING", "release authority has the wrong type")
    if info.st_uid != 0:
        _blocked("COMMAND_PATH_OWNER_UNSAFE", "release authority is not root-owned")
    if info.st_mode & (0o222 | 0o7000):
        _blocked("COMMAND_PATH_MODE_UNSAFE", "release authority must be read-only without special mode bits")
    _validate_no_xattrs(path, "COMMAND_PATH_XATTR_UNSAFE")
    if executable and not info.st_mode & 0o111:
        _blocked("COMMAND_EXECUTABLE_MISSING", "release interpreter is not executable")
    return info


def _walk_release(root: Path) -> dict[str, os.stat_result]:
    observed: dict[str, os.stat_result] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise CommandRegistryError("COMMAND_RELEASE_UNREADABLE", "release directory cannot be read") from exc
        for child in children:
            relative = child.relative_to(root).as_posix()
            try:
                info = _lstat(child)
            except OSError as exc:
                raise CommandRegistryError("COMMAND_RELEASE_UNREADABLE", "release entry cannot be inspected") from exc
            if stat.S_ISLNK(info.st_mode):
                _blocked("COMMAND_RELEASE_SYMLINK", "release contains a symlink")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                _blocked("COMMAND_RELEASE_TYPE_UNSAFE", "release contains an unsupported file type")
            _validate_no_xattrs(child, "COMMAND_PATH_XATTR_UNSAFE")
            observed[relative] = info
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
    return observed


@dataclass(frozen=True, slots=True, repr=False)
class _Attestation:
    application_revision: str
    backend_revision: str
    manifest_digest: str
    root_identity: tuple[int, int]
    interpreter_identity: tuple[int, int]
    manifest_identity: tuple[int, int]
    backend_cwd: Path
    backend_python: Path
    authority_identity: tuple[int, int]
    authority_document_sha256: str
    safety_snapshot_path: Path
    safety_exporter_commit: str
    safety_source_fingerprint: str
    semantic_evidence: SemanticEvidence
    authority_pin: tuple[object, ...] = ()
    runtime_paths: RuntimePathsV2 | None = None
    runtime_authority: RuntimeAuthorityV2 | None = field(
        default=None, compare=False, repr=False
    )


@dataclass(frozen=True, slots=True, repr=False)
class WorkerRuntimeAuthority:
    application_revision: str
    backend_revision: str
    safety_snapshot_path: Path
    safety_exporter_commit: str
    safety_source_fingerprint: str
    semantic_evidence: SemanticEvidence
    authority_identity: tuple[int, int]
    authority_document_sha256: str
    authority_pin: tuple[object, ...]
    runtime_paths: RuntimePathsV2
    runtime_authority: RuntimeAuthorityV2 = field(repr=False, compare=False)


def _expected_command_document(authority: RuntimeAuthority) -> dict[str, object]:
    commands = {
        job_type.value: {
            "executable": str(authority.backend.python_path),
            "cwd": str(authority.backend.release_root),
            "argv_prefix": list(spec.argv_prefix),
            "timeout_seconds": spec.timeout_seconds,
            "max_attempts": spec.max_attempts,
            "result_validator": spec.result_validator_id,
            "shell": False,
        }
        for job_type, spec in sorted(COMMAND_REGISTRY.items(), key=lambda item: item[0].value)
    }
    aggregate = hashlib.sha256(
        json.dumps(commands, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "manifest_version": 1,
        "backend_commit": authority.backend.git_commit,
        "commands": commands,
        "aggregate_sha256": aggregate,
    }


def _read_command_manifest(authority: RuntimeAuthority) -> dict[str, object]:
    try:
        return read_protected_canonical_json(
            authority.command_manifest.path, authority.command_manifest.sha256
        )
    except Exception:
        raise CommandRegistryError(
            "COMMAND_MANIFEST_INVALID", "external command authority is unavailable"
        ) from None


def _attest_semantic_authority(authority: RuntimeAuthority) -> SemanticEvidence:
    try:
        evidence = attest_current_semantic_inputs(
            authority.semantic,
            authority.backend.git_commit,
        )
        if not isinstance(evidence, SemanticEvidence):
            raise TypeError
        return evidence
    except Exception:
        raise CommandRegistryError(
            "SEMANTIC_INPUT_MISMATCH", "semantic input authority is unavailable"
        ) from None


def _verify_authority_release(release, release_type: str) -> None:
    try:
        valid = _verify_release(
            release.release_root,
            release.manifest_path,
            release.manifest_sha256,
            ReleasePolicy(
                release_type=release_type,
                expected_git_commit=release.git_commit,
                expected_python_identity=release.python_identity,
            ),
        )
    except Exception:
        valid = False
    if valid is not True:
        _blocked("COMMAND_RELEASE_NOT_APPROVED", "immutable release attestation failed")


def _runtime_python_path() -> Path:
    return Path(sys.executable)


def _attest_release_v1() -> _Attestation:
    try:
        authority = _load_runtime_authority()
    except Exception:
        raise CommandRegistryError(
            "RUNTIME_AUTHORITY_INVALID", "protected runtime authority is unavailable"
        ) from None
    if authority.backend.git_commit != APPROVED_BACKEND_REVISION:
        _blocked("COMMAND_BACKEND_COMMIT_MISMATCH", "backend authority does not match reviewed code")
    if _runtime_python_path() != authority.application.python_path:
        _blocked("APPLICATION_INTERPRETER_MISMATCH", "worker is not running from the attested application")
    _verify_authority_release(authority.application, "phase4-app")
    _verify_authority_release(authority.backend, "phase4-backend")
    observed_command = json.dumps(
        _read_command_manifest(authority), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    expected_command = json.dumps(
        _expected_command_document(authority), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if not hmac.compare_digest(observed_command, expected_command):
        _blocked("COMMAND_MANIFEST_MISMATCH", "external command manifest differs from code policy")
    semantic_evidence = _attest_semantic_authority(authority)
    try:
        authority.recheck()
        root_info = authority.backend.release_root.stat()
        interpreter_info = authority.backend.python_path.stat()
        manifest_info = authority.backend.manifest_path.stat()
    except Exception:
        raise CommandRegistryError(
            "RUNTIME_AUTHORITY_CHANGED", "protected runtime authority changed during attestation"
        ) from None
    return _Attestation(
        authority.application.git_commit,
        authority.backend.git_commit,
        authority.backend.manifest_sha256,
        (root_info.st_dev, root_info.st_ino),
        (interpreter_info.st_dev, interpreter_info.st_ino),
        (manifest_info.st_dev, manifest_info.st_ino),
        authority.backend.release_root,
        authority.backend.python_path,
        authority._identity,
        authority._document_sha256,
        authority.safety.snapshot_path,
        authority.safety.exporter_commit,
        authority.safety.source_fingerprint,
        semantic_evidence,
    )


def _expected_v2_command_document(
    authority: RuntimeAuthorityV2,
) -> dict[str, object]:
    commands = [
        {
            "argv": [
                str(authority.backend_python),
                "-I",
                "-B",
                "main.py",
                "--mode",
                "snapshot",
                "--research-only",
            ],
            "cwd": str(authority.backend_root),
            "environment_policy": "EMPTY_ALLOWLIST_RESEARCH_ONLY_V1",
            "executable": str(authority.backend_python),
            "job_type": "SNAPSHOT",
            "shell": False,
        }
    ]
    encoded = json.dumps(
        commands, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "commands": commands,
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "schema_version": 2,
    }


def _attest_release_v2() -> _Attestation:
    try:
        authority = _load_runtime_authority_v2()
    except Exception:
        raise CommandRegistryError(
            "RUNTIME_AUTHORITY_INVALID", "protected runtime authority is unavailable"
        ) from None
    if not isinstance(authority, RuntimeAuthorityV2):
        _blocked("RUNTIME_AUTHORITY_INVALID", "protected runtime authority is unavailable")
    if _runtime_python_path() != authority.application_python:
        _blocked(
            "APPLICATION_INTERPRETER_MISMATCH",
            "worker is not running from the attested application",
        )
    try:
        release_valid = _attest_application_release_v2(authority)
    except Exception:
        release_valid = False
    if release_valid is not True:
        _blocked("COMMAND_RELEASE_NOT_APPROVED", "immutable release attestation failed")
    observed = json.dumps(
        authority.command_manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = json.dumps(
        _expected_v2_command_document(authority),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not hmac.compare_digest(observed, expected):
        _blocked(
            "COMMAND_MANIFEST_MISMATCH",
            "external command manifest differs from code policy",
        )
    if not isinstance(authority.semantic_evidence, SemanticEvidence):
        _blocked(
            "SEMANTIC_INPUT_MISMATCH",
            "semantic input authority is unavailable",
        )
    try:
        current = authority.recheck()
        if (
            current._authority_pin != authority._authority_pin
            or current._dynamic_evidence_pin != authority._dynamic_evidence_pin
        ):
            raise ValueError
    except Exception:
        raise CommandRegistryError(
            "RUNTIME_AUTHORITY_CHANGED",
            "protected runtime authority changed during attestation",
        ) from None
    identity = next(
        (
            item
            for item in authority._authority_pin
            if isinstance(item, tuple)
            and len(item) == 2
            and all(type(part) is int for part in item)
        ),
        (0, 0),
    )
    return _Attestation(
        application_revision=authority.source_commit,
        backend_revision=authority.source_commit,
        manifest_digest=authority.backend_artifact_sha256,
        root_identity=identity,
        interpreter_identity=identity,
        manifest_identity=identity,
        backend_cwd=authority.backend_root,
        backend_python=authority.backend_python,
        authority_identity=identity,
        authority_document_sha256=next(
            (
                item
                for item in authority._authority_pin
                if isinstance(item, str) and len(item) == 64
            ),
            "0" * 64,
        ),
        safety_snapshot_path=authority.safety.snapshot_path,
        safety_exporter_commit=authority.safety.exporter_commit,
        safety_source_fingerprint=authority.safety.source_fingerprint,
        semantic_evidence=authority.semantic_evidence,
        authority_pin=authority._authority_pin,
        runtime_paths=authority.runtime_paths,
        runtime_authority=authority,
    )


def _attest_release() -> _Attestation:
    """Production attestation path. V1 remains explicit fixture code only."""

    return _attest_release_v2()


def attest_worker_runtime_authority() -> WorkerRuntimeAuthority:
    """Attest all immutable inputs before recovery/claim and reveal safety bindings."""

    attestation = _attest_release()
    return WorkerRuntimeAuthority(
        attestation.application_revision,
        attestation.backend_revision,
        attestation.safety_snapshot_path,
        attestation.safety_exporter_commit,
        attestation.safety_source_fingerprint,
        attestation.semantic_evidence,
        attestation.authority_identity,
        attestation.authority_document_sha256,
        attestation.authority_pin,
        attestation.runtime_paths,
        attestation.runtime_authority,
    )


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class ValidatedCommandCapability:
    _attestation: _Attestation
    _issued_at_ns: int
    _fingerprint: str

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def __repr__(self) -> str:
        return "ValidatedCommandCapability(validated=True)"


_ISSUED_CAPABILITIES: weakref.WeakSet[ValidatedCommandCapability] = weakref.WeakSet()


def attest_command_capability() -> ValidatedCommandCapability:
    """Attest the complete immutable release and issue one short-lived use."""

    attestation = _attest_release()
    issued_at = time.monotonic_ns()
    material = f"{attestation!r}|{issued_at}".encode()
    capability = ValidatedCommandCapability()
    object.__setattr__(capability, "_attestation", attestation)
    object.__setattr__(capability, "_issued_at_ns", issued_at)
    object.__setattr__(capability, "_fingerprint", hashlib.sha256(material).hexdigest())
    _ISSUED_CAPABILITIES.add(capability)
    return capability


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv_prefix: tuple[str, ...]
    timeout_seconds: int
    max_attempts: int
    result_validator_id: str
    shell: bool = False


@dataclass(frozen=True, slots=True)
class BuiltCommand:
    executable: Path
    cwd: Path
    argv: tuple[str, ...]
    timeout_seconds: int
    max_attempts: int
    result_validator_id: str
    capability_fingerprint: str
    backend_revision: str
    lineage: "CommandLineage"
    shell: bool = False


@dataclass(frozen=True, slots=True)
class CommandLineage:
    authority_document_sha256: str
    backend_manifest_sha256: str
    semantic_policy_sha256: str
    semantic_active_authority_sha256: str
    semantic_version_manifest_sha256: str
    semantic_input_fingerprint: str
    semantic_manifest_version: str
    semantic_generated_at: str
    semantic_expires_at: str

    def as_metadata(self) -> dict[str, str]:
        return {
            "authority_document_sha256": self.authority_document_sha256,
            "backend_manifest_sha256": self.backend_manifest_sha256,
            "semantic_policy_sha256": self.semantic_policy_sha256,
            "semantic_active_authority_sha256": self.semantic_active_authority_sha256,
            "semantic_version_manifest_sha256": self.semantic_version_manifest_sha256,
            "semantic_input_fingerprint": self.semantic_input_fingerprint,
            "semantic_manifest_version": self.semantic_manifest_version,
            "semantic_generated_at": self.semantic_generated_at,
            "semantic_expires_at": self.semantic_expires_at,
        }


def _command_lineage(attestation: _Attestation) -> CommandLineage:
    semantic = attestation.semantic_evidence
    return CommandLineage(
        authority_document_sha256=attestation.authority_document_sha256,
        backend_manifest_sha256=attestation.manifest_digest,
        semantic_policy_sha256=semantic.policy_sha256,
        semantic_active_authority_sha256=semantic.active_authority_sha256,
        semantic_version_manifest_sha256=semantic.version_manifest_sha256,
        semantic_input_fingerprint=semantic.semantic_input_fingerprint,
        semantic_manifest_version=semantic.manifest_version,
        semantic_generated_at=semantic.generated_at.isoformat(),
        semantic_expires_at=semantic.expires_at.isoformat(),
    )


def _spec(timeout: int, attempts: int, validator: str) -> CommandSpec:
    return CommandSpec(
        ("-I", "-B", "main.py", "--mode", "snapshot", "--research-only"),
        timeout,
        attempts,
        validator,
    )


COMMAND_REGISTRY: Mapping[JobType, CommandSpec] = MappingProxyType({
    JobType.SNAPSHOT: _spec(900, 2, "legacy-report-v1"),
})


def _validated_payload(job_type: JobType, value: object):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        _blocked("COMMAND_PAYLOAD_INVALID", "job payload is invalid")
    try:
        return parse_payload(job_type, value)
    except (TypeError, ValueError) as exc:
        raise CommandRegistryError("COMMAND_PAYLOAD_INVALID", "job payload is invalid") from exc


def build_command(job: object, capability: ValidatedCommandCapability) -> BuiltCommand:
    """Consume one capability and re-attest release identities and manifest."""

    if not isinstance(capability, ValidatedCommandCapability) or capability not in _ISSUED_CAPABILITIES:
        _blocked("COMMAND_CAPABILITY_INVALID", "a current validated command capability is required")
    _ISSUED_CAPABILITIES.discard(capability)
    if time.monotonic_ns() - capability._issued_at_ns > _CAPABILITY_TTL_NS:
        _blocked("COMMAND_CAPABILITY_EXPIRED", "command capability expired before use")
    if _attest_release() != capability._attestation:
        _blocked("COMMAND_CAPABILITY_STALE", "release identity changed after attestation")
    if time.monotonic_ns() - capability._issued_at_ns > _CAPABILITY_TTL_NS:
        _blocked("COMMAND_CAPABILITY_EXPIRED", "command capability expired during release re-attestation")
    try:
        job_type = JobType(getattr(job, "job_type"))
        spec = COMMAND_REGISTRY[job_type]
    except (AttributeError, TypeError, ValueError, KeyError) as exc:
        raise CommandRegistryError("COMMAND_TYPE_INVALID", "job type is invalid or not allowlisted") from exc
    payload = _validated_payload(job_type, getattr(job, "payload", None))
    if not isinstance(payload, SnapshotPayload):  # pragma: no cover
        _blocked("COMMAND_PAYLOAD_INVALID", "job payload is invalid")
    return BuiltCommand(
        executable=capability._attestation.backend_python,
        cwd=capability._attestation.backend_cwd,
        argv=(str(capability._attestation.backend_python), *spec.argv_prefix),
        timeout_seconds=spec.timeout_seconds,
        max_attempts=spec.max_attempts,
        result_validator_id=spec.result_validator_id,
        capability_fingerprint=capability.fingerprint,
        backend_revision=capability._attestation.backend_revision,
        lineage=_command_lineage(capability._attestation),
        shell=spec.shell,
    )


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class PreparedSpawn:
    """Opaque, short-lived authority to reveal one built command at Popen."""

    _command: BuiltCommand
    _attestation: _Attestation
    _deadline_ns: int

    def __repr__(self) -> str:
        return "PreparedSpawn(validated=True)"


_ISSUED_PREPARED_SPAWNS: weakref.WeakSet[PreparedSpawn] = weakref.WeakSet()


def prepare_immediate_spawn(job: object) -> PreparedSpawn:
    """Attest/build now, but keep command fields opaque until Task 9 Popen."""

    capability = attest_command_capability()
    command = build_command(job, capability)
    prepared = PreparedSpawn()
    object.__setattr__(prepared, "_command", command)
    object.__setattr__(prepared, "_attestation", capability._attestation)
    object.__setattr__(prepared, "_deadline_ns", time.monotonic_ns() + _PREPARED_SPAWN_TTL_NS)
    _ISSUED_PREPARED_SPAWNS.add(prepared)
    return prepared


def consume_prepared_spawn(prepared: PreparedSpawn) -> BuiltCommand:
    """Consume once at the immediate process-creation boundary."""

    if not isinstance(prepared, PreparedSpawn) or prepared not in _ISSUED_PREPARED_SPAWNS:
        _blocked("COMMAND_PREPARED_SPAWN_INVALID", "a current prepared spawn token is required")
    _ISSUED_PREPARED_SPAWNS.discard(prepared)
    if time.monotonic_ns() > prepared._deadline_ns:
        _blocked("COMMAND_PREPARED_SPAWN_EXPIRED", "prepared spawn token expired before process creation")
    if _attest_release() != prepared._attestation:
        _blocked("COMMAND_PREPARED_SPAWN_STALE", "runtime authority changed before process creation")
    if time.monotonic_ns() > prepared._deadline_ns:
        _blocked(
            "COMMAND_PREPARED_SPAWN_EXPIRED",
            "prepared spawn token expired during final authority attestation",
        )
    return prepared._command


__all__ = [
    "APPROVED_BACKEND_CWD", "APPROVED_BACKEND_PYTHON", "APPROVED_BACKEND_REVISION",
    "APPROVED_RELEASE_MANIFEST_PATH",
    "BuiltCommand", "COMMAND_REGISTRY", "CommandLineage", "CommandRegistryError",
    "CommandSpec", "FULL_REATTESTATION_ROLLOUT_LIMIT_SECONDS",
    "PRESPAWN_FULL_REATTESTATION_COUNT", "PreparedSpawn",
    "ValidatedCommandCapability", "WorkerRuntimeAuthority", "attest_command_capability",
    "attest_worker_runtime_authority", "build_command",
    "consume_prepared_spawn", "prepare_immediate_spawn",
]
