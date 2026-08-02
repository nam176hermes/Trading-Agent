"""Immutable final Package 6 evidence bundle and strict verifier."""

from __future__ import annotations

import base64
import ctypes
from dataclasses import asdict, dataclass
import errno
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import sysconfig
import time
from types import BuiltinFunctionType, MappingProxyType, ModuleType
import weakref
from typing import Any, Callable, Mapping
from uuid import uuid4

from scripts.validate_package6_runtime_approval import (
    PACKAGE6_CUSTODIAN_OPERATIONS,
    PACKAGE6_CUSTODIAN_SOURCE_PATHS,
    PACKAGE6_JOB_API_ENVIRONMENT_KEYS,
    PACKAGE6_JOB_API_PORT,
    PACKAGE6_WORKER_ENVIRONMENT_KEYS,
    ValidatedPackage6Capability,
    is_issued_capability,
    package6_authority_digests,
)

from .controller import (
    EvidenceIncomplete,
    RuntimeChildAuthorities,
)
from .integration import RuntimeChainEvidence


VERDICT = "PENDING_CONTROLLER_RUNTIME_VERIFICATION"
EVIDENCE_DOCUMENTS = (
    "docs/implementation/foundation-paper-runtime-dashboard.md",
    "docs/implementation/foundation-paper-runtime-db.md",
    "docs/implementation/foundation-paper-runtime-final.md",
    "docs/implementation/foundation-paper-runtime-request.md",
    "docs/implementation/foundation-paper-runtime-result.md",
    "docs/implementation/foundation-paper-runtime-rollback.md",
    "docs/implementation/foundation-paper-runtime-worker.md",
)
_SHA256 = frozenset("0123456789abcdef")
_MAX_PID = 2_147_483_647
_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_MAX_UNSIGNED_64 = 18_446_744_073_709_551_615
_MAX_RUNTIME_EVIDENCE_CONTAINER_BYTES = 16 * 1024 * 1024
_MAX_CONTROLLER_FINAL_CONTAINER_BYTES = 16 * 1024 * 1024
_CONTROLLER_FINAL_NAME = "package6-controller-final.json"
_CONTROLLER_FINAL_KIND = "PACKAGE6_CONTROLLER_FINAL_PUBLICATION"
_NATIVE_TRANSCRIPT_FIELDS = {
    "sha256",
    "size",
    "observed_size",
    "truncated",
    "eof",
}
_DIAGNOSTIC_TRANSCRIPT_FIELDS = {"path", "sha256", "size", "truncated"}
_STOP_FIELDS = {
    "operation_id",
    "component",
    "native_operation_id",
    "recovery_token",
    "state",
    "exit_code",
    "cleanup_proven",
    "stdout",
    "stderr",
}
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_GO_VERDICT = "GO - PAPER FOUNDATION RUNTIME VERIFIED"
_PATCH_ALGORITHM = "PACKAGE6_GOAL2_PATCH_V1"
_REQUIRED_REVIEWED_PATHS = (
    "Makefile",
    "docs/implementation/package6-descriptor-custody-fix.md",
    "docs/implementation/package6-single-container-publication.md",
    "docs/plans/trading-agent-foundation-upgrade-2026-07-22/"
    "06-paper-runtime-foundation-validation.md",
    "docs/plans/trading-agent-foundation-upgrade-2026-07-22/"
    "06b-native-custody-authority-r11-design.md",
    "docs/plans/trading-agent-foundation-upgrade-2026-07-22/"
    "06c-package6-release-authority-v2-closure-plan.md",
    "native/package6_custodian/Makefile",
    "native/package6_custodian/include/p6c_protocol.h",
    "native/package6_custodian/include/p6c_types.h",
    "native/package6_custodian/src/cgroup.c",
    "native/package6_custodian/src/journal.c",
    "native/package6_custodian/src/linux_authority.c",
    "native/package6_custodian/src/main.c",
    "native/package6_custodian/src/process.c",
    "native/package6_custodian/src/protocol.c",
    "native/package6_custodian/src/publication.c",
    "native/package6_custodian/src/python_fd_custody.c",
    "native/package6_custodian/src/sha256.c",
    "native/package6_custodian/src/transcript.c",
    "native/package6_custodian/tests/test_authority.c",
    "native/package6_custodian/tests/test_protocol.c",
    "native/package6_custodian/tests/test_publication.c",
    "native/package6_custodian/tests/test_service_main.c",
    "packages/runtime_release/supervisor_v2.py",
    "schemas/package6-paper-runtime-approval.schema.json",
    "scripts/check_test_governance.py",
    "scripts/finalize_package6_controller_evidence.py",
    "scripts/validate_package6_runtime_approval.py",
    "services/paper_runtime/__init__.py",
    "services/paper_runtime/controller.py",
    "services/paper_runtime/custodian_client.py",
    "services/paper_runtime/evidence.py",
    "services/paper_runtime/integration.py",
    "tests/foundation/test_package6_controller_closure.py",
    "tests/foundation/test_package6_custodian_contract.py",
    "tests/foundation/test_package6_runtime_approval.py",
    "tests/foundation/test_package6_runtime_controller.py",
    "tests/foundation/test_package6_runtime_integration.py",
    "tests/governance/test_test_governance.py",
    "tests/native/test_package6_custodian.py",
    "tests/runtime_release/test_supervisor_v2.py",
    "tests/skip-allowlist.yaml",
)
_REVIEW_FIELDS = {
    "schema_version", "verdict", "reviewed_base_commit", "patch_algorithm",
    "reviewed_patch_sha256", "reviewed_patch_bytes", "reviewed_paths",
    "source_diff_sha256", "findings", "scope_integrity", "test_adequacy",
    "seal_manifest_sha256", "seal_integrity", "production_authority_status",
    "live_execution_approved", "live_trading_approved",
}
_DIAGNOSTIC_FIELDS = {
    "schema_version", "verdict", "candidate_commit", "candidate_tree",
    "source_diff_sha256", "runtime_attempt", "test_nodeid", "exit_code",
    "passed", "failed", "transcript_metadata", "live_execution_approved",
    "live_trading_approved",
}
_DIAGNOSTIC_NODEID = (
    "tests/foundation/test_package6_runtime_integration.py::"
    "test_complete_package6_runtime_chain"
)
_MAX_DIAGNOSTIC_TRANSCRIPT_BYTES = 1_048_576
_CLEANUP_FIELDS = {
    "schema_version", "candidate_commit", "candidate_tree",
    "source_diff_sha256", "process_refs", "surviving_processes",
    "surviving_listener_ports", "candidate_root", "candidate_root_exists",
    "postgres_root", "postgres_root_exists",
    "evidence_preserved_outside_disposable_root",
    "live_execution_approved", "live_trading_approved",
}
_CUSTODIAN_CLOSURE_FIELDS = {
    "authority_mode",
    "helper_binary_sha256",
    "native_source_set",
    "native_source_set_sha256",
    "protocol_version",
    "protocol_features",
    "endpoint_authority",
    "production_socket_activation",
    "operations",
    "candidate_commit",
    "candidate_tree",
    "stage_sha256",
    "fixture_identity",
    "child_environment_contract",
    "mode",
    "live_execution_approved",
    "live_trading_approved",
}
_CANONICAL_JOB_FIELDS = (
    "job_id",
    "state",
    "attempt_count",
    "reason_code",
    "result_hash",
)
_ATTEMPT_FIELDS = {
    "attempt_id",
    "outcome",
    "claimed_at",
    "started_at",
    "finished_at",
    "exit_code",
    "heartbeat_at",
    "lease_expires_at",
    "termination_reason",
}
_EVENT_FIELDS = {
    "sequence",
    "from_state",
    "to_state",
    "reason_code",
    "attempt_id",
    "metadata",
}
_ARTIFACT_FIELDS = {
    "artifact_id",
    "attempt_id",
    "artifact_type",
    "relative_ref",
    "validator_id",
    "sha256",
    "size_bytes",
    "media_type",
    "truncated",
    "validation_metadata",
}
_SEMANTIC_REJECTION = (
    "runtime state, event, or sealed result proof is invalid"
)


_LIBC = ctypes.CDLL(None, use_errno=True)
_NATIVE_FD_CUSTODY_MODULE_NAME = "_package6_fd_custody"
_NATIVE_FD_CUSTODY_DEFAULT_ROOT = Path("/tmp/package6-custodian-build/python")
_NATIVE_FD_CUSTODY_EXPECTED_SHA256_ENV = (
    "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256"
)


def _native_fd_custody_expected_sha256() -> str | None:
    expected = os.environ.get(_NATIVE_FD_CUSTODY_EXPECTED_SHA256_ENV)
    if expected is None:
        return None
    if _HEX64.fullmatch(expected) is None:
        raise RuntimeError(
            "native descriptor custody expected SHA-256 is malformed"
        )
    return expected


def _native_fd_custody_extension_path() -> Path | None:
    configured = os.environ.get("PACKAGE6_FD_CUSTODY_EXTENSION_PATH")
    if configured:
        candidate = Path(configured)
    else:
        suffix = sysconfig.get_config_var("EXT_SUFFIX")
        if not isinstance(suffix, str) or not suffix:
            return None
        candidate = _NATIVE_FD_CUSTODY_DEFAULT_ROOT / (
            f"{_NATIVE_FD_CUSTODY_MODULE_NAME}{suffix}"
        )
    if not candidate.is_absolute():
        return None
    candidate = Path(os.path.normpath(candidate))
    try:
        initial = candidate.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(initial.st_mode):
        raise RuntimeError("native descriptor custody extension cannot be a symlink")
    info = candidate.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise RuntimeError("native descriptor custody extension policy is invalid")
    direct_parent = candidate.parent
    for ancestor in reversed(direct_parent.parents):
        ancestor_info = ancestor.lstat()
        if stat.S_ISLNK(ancestor_info.st_mode) or not stat.S_ISDIR(
            ancestor_info.st_mode
        ):
            raise RuntimeError(
                "native descriptor custody extension parent is invalid"
            )
        writable = stat.S_IMODE(ancestor_info.st_mode) & 0o022
        trusted_sticky_root = (
            ancestor == Path("/tmp")
            and ancestor_info.st_uid == 0
            and bool(ancestor_info.st_mode & stat.S_ISVTX)
        )
        if writable and not trusted_sticky_root:
            raise RuntimeError(
                "native descriptor custody extension parent is writable"
            )
    parent_info = direct_parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise RuntimeError(
            "native descriptor custody extension parent must be private"
        )
    if candidate.parent == _NATIVE_FD_CUSTODY_DEFAULT_ROOT:
        build_info = candidate.parent.parent.lstat()
        if (
            not stat.S_ISDIR(build_info.st_mode)
            or build_info.st_uid != os.geteuid()
            or stat.S_IMODE(build_info.st_mode) != 0o700
        ):
            raise RuntimeError(
                "native descriptor custody build parent must be private"
            )
    return candidate


def _native_fd_custody_artifact_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        stat.S_IFMT(info.st_mode),
        stat.S_IMODE(info.st_mode),
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _validate_native_fd_custody_directory(
    path: Path,
    info: os.stat_result,
    *,
    direct_parent: bool,
) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("native descriptor custody extension parent is invalid")
    mode = stat.S_IMODE(info.st_mode)
    if direct_parent:
        if info.st_uid != os.geteuid() or mode != 0o700:
            raise RuntimeError(
                "native descriptor custody extension parent must be private"
            )
        return
    trusted_sticky_root = (
        path == Path("/tmp")
        and info.st_uid == 0
        and bool(info.st_mode & stat.S_ISVTX)
    )
    if mode & 0o022 and not trusted_sticky_root:
        raise RuntimeError(
            "native descriptor custody extension parent is writable"
        )


def _validate_native_fd_custody_artifact(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise RuntimeError("native descriptor custody artifact policy is invalid")


def _open_native_fd_custody_artifact(
    extension: Path,
) -> tuple[int, tuple[int, ...]]:
    """Open the extension below a retained no-follow ancestry chain."""

    if not extension.is_absolute() or extension.name in {"", ".", ".."}:
        raise RuntimeError("native descriptor custody extension path is invalid")
    directories: list[int] = []
    artifact = -1
    current_path = Path("/")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        current = os.open(current_path, flags)
        directories.append(current)
        _validate_native_fd_custody_directory(
            current_path,
            os.fstat(current),
            direct_parent=extension.parent == current_path,
        )
        for part in extension.parent.parts[1:]:
            current_path /= part
            current = os.open(part, flags, dir_fd=current)
            directories.append(current)
            _validate_native_fd_custody_directory(
                current_path,
                os.fstat(current),
                direct_parent=current_path == extension.parent,
            )
        artifact = os.open(
            extension.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directories[-1],
        )
        artifact_info = os.fstat(artifact)
        _validate_native_fd_custody_artifact(artifact_info)
        named_info = os.stat(
            extension.name,
            dir_fd=directories[-1],
            follow_symlinks=False,
        )
        if (
            _native_fd_custody_artifact_identity(named_info)
            != _native_fd_custody_artifact_identity(artifact_info)
        ):
            raise RuntimeError(
                "native descriptor custody extension changed during acquisition"
            )
        return artifact, tuple(directories)
    except BaseException:
        if artifact >= 0:
            os.close(artifact)
        for descriptor in reversed(directories):
            os.close(descriptor)
        raise


def _hash_native_fd_custody_artifact(descriptor: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(size - offset, 1024 * 1024), offset)
        if not chunk:
            break
        digest.update(chunk)
        offset += len(chunk)
    if offset != size:
        raise RuntimeError("native descriptor custody extension read is incomplete")
    return digest.hexdigest()


def _validate_native_fd_custody_module(
    module: Any,
    extension: Path,
    *,
    artifact_sha256: str | None = None,
) -> Any:
    if not isinstance(module, ModuleType):
        raise RuntimeError("native descriptor custody extension cache is invalid")
    specification = getattr(module, "__spec__", None)
    loader = getattr(specification, "loader", None)
    if (
        getattr(specification, "name", None) != _NATIVE_FD_CUSTODY_MODULE_NAME
        or not isinstance(loader, importlib.machinery.ExtensionFileLoader)
    ):
        raise RuntimeError("native descriptor custody extension loader is invalid")
    for origin in (getattr(module, "__file__", None), getattr(specification, "origin", None)):
        if not isinstance(origin, str) or Path(origin) != extension:
            raise RuntimeError("native descriptor custody extension origin is invalid")
    try:
        extension_info = extension.stat(follow_symlinks=False)
    except OSError as error:
        raise RuntimeError(
            "native descriptor custody extension origin is unavailable"
        ) from error
    if getattr(module, "_PACKAGE6_ARTIFACT_IDENTITY", None) != (
        _native_fd_custody_artifact_identity(extension_info)
    ):
        raise RuntimeError("native descriptor custody extension identity is invalid")
    module_sha256 = getattr(module, "_PACKAGE6_ARTIFACT_SHA256", None)
    if (
        not isinstance(module_sha256, str)
        or _HEX64.fullmatch(module_sha256) is None
        or (
            artifact_sha256 is not None
            and module_sha256 != artifact_sha256
        )
    ):
        raise RuntimeError("native descriptor custody extension digest is invalid")
    if getattr(module, "OWNERSHIP_MODEL", None) != "NATIVE_OBJECT_V1":
        raise RuntimeError("native descriptor custody extension contract is invalid")
    owner_type = getattr(module, "FdOwner", None)
    owner_flags = getattr(owner_type, "__flags__", None)
    if (
        not isinstance(owner_type, type)
        or owner_type.__module__ != _NATIVE_FD_CUSTODY_MODULE_NAME
        or owner_type.__name__ != "FdOwner"
        or not isinstance(owner_flags, int)
        or owner_flags & (1 << 9)
    ):
        raise RuntimeError("native descriptor custody owner type is invalid")
    for function_name in ("open", "openat"):
        function = getattr(module, function_name, None)
        if (
            not isinstance(function, BuiltinFunctionType)
            or getattr(function, "__self__", None) is not module
        ):
            raise RuntimeError("native descriptor custody entry point is invalid")
    return module


def _load_native_fd_custody() -> Any | None:
    expected_sha256 = _native_fd_custody_expected_sha256()
    extension = _native_fd_custody_extension_path()
    if extension is None:
        if expected_sha256 is not None:
            raise RuntimeError(
                "native descriptor custody expected artifact is unavailable"
            )
        return None
    existing = sys.modules.get(_NATIVE_FD_CUSTODY_MODULE_NAME)
    if existing is not None and expected_sha256 is None:
        return _validate_native_fd_custody_module(existing, extension)
    descriptor, ancestry = _open_native_fd_custody_artifact(extension)
    try:
        before = os.fstat(descriptor)
        _validate_native_fd_custody_artifact(before)
        before_digest = _hash_native_fd_custody_artifact(
            descriptor,
            before.st_size,
        )
        if (
            expected_sha256 is not None
            and before_digest != expected_sha256
        ):
            raise RuntimeError(
                "native descriptor custody extension digest does not match "
                "expected SHA-256"
            )
        if existing is not None:
            return _validate_native_fd_custody_module(
                existing,
                extension,
                artifact_sha256=before_digest,
            )
        retained_path = f"/proc/self/fd/{descriptor}"
        loader = importlib.machinery.ExtensionFileLoader(
            _NATIVE_FD_CUSTODY_MODULE_NAME,
            retained_path,
        )
        specification = importlib.util.spec_from_file_location(
            _NATIVE_FD_CUSTODY_MODULE_NAME,
            retained_path,
            loader=loader,
        )
        if specification is None:
            raise RuntimeError("native descriptor custody extension cannot be loaded")
        module = importlib.util.module_from_spec(specification)
        loader.exec_module(module)
        after = os.fstat(descriptor)
        if (
            _native_fd_custody_artifact_identity(after)
            != _native_fd_custody_artifact_identity(before)
            or _hash_native_fd_custody_artifact(descriptor, after.st_size)
            != before_digest
            or _native_fd_custody_artifact_identity(
                os.stat(
                    extension.name,
                    dir_fd=ancestry[-1],
                    follow_symlinks=False,
                )
            )
            != _native_fd_custody_artifact_identity(after)
        ):
            raise RuntimeError(
                "native descriptor custody extension changed during load"
            )
        module.__file__ = str(extension)
        specification.origin = str(extension)
        setattr(
            module,
            "_PACKAGE6_ARTIFACT_IDENTITY",
            _native_fd_custody_artifact_identity(after),
        )
        setattr(module, "_PACKAGE6_ARTIFACT_SHA256", before_digest)
        _validate_native_fd_custody_module(
            module,
            extension,
            artifact_sha256=before_digest,
        )
        sys.modules[_NATIVE_FD_CUSTODY_MODULE_NAME] = module
        return module
    finally:
        os.close(descriptor)
        for ancestor in reversed(ancestry):
            os.close(ancestor)


_NATIVE_FD_CUSTODY = _load_native_fd_custody()


def _require_native_fd_custody() -> Any:
    if _NATIVE_FD_CUSTODY is None:
        raise EvidenceIncomplete(
            "native descriptor custody extension is unavailable; "
            "run make build-package6-custodian"
        )
    return _NATIVE_FD_CUSTODY


class _DescriptorAuthority:
    """Python authority over a descriptor already owned by a native object."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.generation_uncertain = False
        identity = owner.identity
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
            or any(type(item) is not int or item < 0 for item in identity)
        ):
            raise RuntimeError("native descriptor identity is invalid")
        self.identity = identity

    @property
    def descriptor(self) -> int:
        descriptor = self.owner.descriptor
        if type(descriptor) is not int:
            raise RuntimeError("native descriptor number is invalid")
        return descriptor

    def abandon_uncertain_generation(self) -> None:
        if not self.generation_uncertain:
            self.owner.abandon_uncertain_generation()
            self.generation_uncertain = True

    def call(self, operation: Callable[[int], Any]) -> Any:
        descriptor = self.descriptor
        try:
            return operation(descriptor)
        except OSError as error:
            if error.errno == errno.EBADF:
                self.abandon_uncertain_generation()
            raise
        except BaseException:
            self.abandon_uncertain_generation()
            raise

    def prove_closed(self) -> bool:
        if self.generation_uncertain:
            return False
        return bool(self.owner.close())

    def __del__(self) -> None:
        try:
            self.owner.close()
        except BaseException:
            pass


class FinalPublicationAuthority:
    """Retained descriptor authority for one committed controller result."""

    def __init__(
        self,
        directory_authority: Any,
        canonical_authority: _DescriptorAuthority,
        *,
        final_name: str,
        canonical_sha256: str,
        canonical_size: int,
        stable_metadata: tuple[int, ...],
    ) -> None:
        self.output_directory_authority = directory_authority
        self.canonical_descriptor_authority = canonical_authority
        self.device, self.inode = canonical_authority.identity
        self.canonical_sha256 = canonical_sha256
        self.size = canonical_size
        self.canonical_byte_size = canonical_size
        self.final_name = final_name
        self.publication_committed = False
        self.publication_commit_uncertain = False
        self.identity_confirmed = False
        self.close_proven = False
        self.recovery_required = False
        self._stable_metadata = stable_metadata

    @property
    def path(self) -> Path:
        return self.output_directory_authority.path / self.final_name

    def read_canonical_bytes(self) -> bytes:
        if self.close_proven:
            raise RuntimeError("final publication authority is closed")
        try:
            raw, metadata = _read_final_publication(
                self.canonical_descriptor_authority,
                expected_link_count=1,
            )
        except BaseException:
            self.recovery_required = True
            raise
        if (
            (self.identity_confirmed and metadata != self._stable_metadata)
            or len(raw) != self.canonical_byte_size
            or hashlib.sha256(raw).hexdigest() != self.canonical_sha256
        ):
            self.recovery_required = True
            raise EvidenceIncomplete("retained final publication bytes changed")
        return raw

    def read_logical_entries(self) -> Mapping[str, bytes]:
        return MappingProxyType(
            _verify_controller_final_container(self.read_canonical_bytes())
        )

    def revalidate_identity(self) -> bool:
        if self.close_proven or not self.publication_committed:
            return False
        _confirm_final_publication_identity(self)
        return True

    def recover(self) -> bool:
        """Retry durability and identity proof without reopening the final path."""
        if self.close_proven:
            return False
        committed = _probe_attempted_publication(self)
        if committed is not True:
            self.publication_commit_uncertain = committed is None
            self.recovery_required = True
            return False
        self.publication_committed = True
        self.publication_commit_uncertain = False
        try:
            self.output_directory_authority.descriptor_authority.call(os.fsync)
            self.read_canonical_bytes()
            _confirm_final_publication_identity(self)
        except (EvidenceIncomplete, OSError, RuntimeError):
            self.recovery_required = True
            return False
        self.recovery_required = False
        return True

    def close(self) -> bool:
        if self.close_proven:
            return True
        file_closed = self.canonical_descriptor_authority.prove_closed()
        directory_closed = self.output_directory_authority.prove_closed()
        self.close_proven = file_closed and directory_closed
        if not self.close_proven:
            self.recovery_required = True
        return self.close_proven

    def __str__(self) -> str:
        return str(self.path)

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


class FinalPublicationFailure(EvidenceIncomplete):
    """A complete linked result exists and retains recovery authority."""

    def __init__(self, authority: FinalPublicationAuthority) -> None:
        super().__init__("final publication durability or identity proof is incomplete")
        self.authority = authority


def _native_open(
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    flags: int,
    mode: int = 0,
    *,
    dir_fd: int | None = None,
    publication: Any | None = None,
    publication_attribute: str | None = None,
) -> _DescriptorAuthority:
    raw_path = os.fsencode(path)
    if b"\0" in raw_path:
        raise ValueError("descriptor path contains NUL")
    native = _require_native_fd_custody()
    if dir_fd is None:
        owner = native.open(raw_path, flags, mode)
    else:
        owner = native.openat(dir_fd, raw_path, flags, mode)
    try:
        authority = _DescriptorAuthority(owner)
        if publication is not None:
            if publication_attribute is None:
                publication.descriptor_authority = authority
                publication.descriptor_identity = authority.identity
            else:
                setattr(publication, publication_attribute, authority)
        return authority
    except BaseException:
        if publication is not None and publication_attribute is None:
            publication.descriptor_identity = getattr(owner, "identity", None)
        owner.close()
        raise


_LIBC_LINKAT = _LIBC.linkat
_LIBC_LINKAT.argtypes = (
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
)
_LIBC_LINKAT.restype = ctypes.c_int
_AT_EMPTY_PATH = 0x1000


def _link_owned_tmpfile(
    descriptor: int,
    directory: int,
    name: str,
) -> None:
    raw_name = os.fsencode(name)
    if b"\0" in raw_name:
        raise ValueError("publication name contains NUL")
    ctypes.set_errno(0)
    result = _LIBC_LINKAT(
        descriptor,
        b"",
        directory,
        raw_name,
        _AT_EMPTY_PATH,
    )
    if result == -1:
        observed_errno = ctypes.get_errno() or errno.EIO
        raise OSError(observed_errno, os.strerror(observed_errno), name)


@dataclass(slots=True)
class _EvidenceDirectoryAuthority:
    path: Path
    descriptor_authority: _DescriptorAuthority
    device: int
    inode: int
    owner: int
    mode: int

    @property
    def descriptor(self) -> int:
        return self.descriptor_authority.descriptor

    def validate_path(self) -> None:
        try:
            path_info = self.path.lstat()
            resolved = self.path.resolve(strict=True)
            descriptor_info = self.descriptor_authority.call(os.fstat)
        except OSError as error:
            raise EvidenceIncomplete(
                "controller output directory is unavailable"
            ) from error
        expected = (self.device, self.inode, self.owner, self.mode)
        if (
            resolved != self.path
            or stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISDIR(path_info.st_mode)
            or not stat.S_ISDIR(descriptor_info.st_mode)
            or (
                path_info.st_dev,
                path_info.st_ino,
                path_info.st_uid,
                stat.S_IMODE(path_info.st_mode),
            )
            != expected
            or (
                descriptor_info.st_dev,
                descriptor_info.st_ino,
                descriptor_info.st_uid,
                stat.S_IMODE(descriptor_info.st_mode),
            )
            != expected
        ):
            raise EvidenceIncomplete(
                "controller output directory identity changed"
            )

    def prove_closed(self) -> bool:
        return self.descriptor_authority.prove_closed()


@dataclass(slots=True)
class _CreatedEvidenceFile:
    name: str
    descriptor_authority: _DescriptorAuthority | None = None
    descriptor_identity: tuple[int, int] | None = None

    @property
    def descriptor(self) -> int:
        if self.descriptor_authority is None:
            return -1
        return self.descriptor_authority.descriptor

    def prove_closed(self) -> bool:
        if self.descriptor_authority is None:
            return True
        return self.descriptor_authority.prove_closed()


@dataclass(slots=True)
class _EvidencePublicationRecovery:
    directory: _EvidenceDirectoryAuthority
    created: tuple[_CreatedEvidenceFile, ...]

    def retry_cleanup(self) -> bool:
        files_closed = all(item.prove_closed() for item in self.created)
        directory_closed = self.directory.prove_closed()
        return files_closed and directory_closed


class _EvidencePublicationFailure(EvidenceIncomplete):
    """Rollback could not prove cleanup; carries usable descriptor custody."""

    def __init__(self, recovery: Any) -> None:
        super().__init__("evidence publication cleanup proof is incomplete")
        self.recovery = recovery


def child_environment_key_sets() -> dict[str, tuple[str, ...]]:
    """Return the code-owned allowlist for both runtime children."""

    return {
        "job_api": PACKAGE6_JOB_API_ENVIRONMENT_KEYS,
        "worker": PACKAGE6_WORKER_ENVIRONMENT_KEYS,
    }


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class PostgresCleanupEvidence:
    approval_sha256: str
    listener_alive: bool
    listener_negative_probes: int
    process_alive: bool
    process_group_alive: bool
    process_pid: int
    process_group: int
    start_ticks: int
    exit_code: int
    pgdata_exists: bool
    cleanup_complete: bool


_ISSUED_CLEANUP: weakref.WeakSet[PostgresCleanupEvidence] = weakref.WeakSet()


def issue_postgres_cleanup_evidence(
    capability: ValidatedPackage6Capability,
    document: dict[str, object],
) -> PostgresCleanupEvidence:
    if not is_issued_capability(capability):
        raise TypeError("validated Package 6 capability is required")
    fields = {
        "approval_sha256",
        "listener_alive",
        "listener_negative_probes",
        "process_alive",
        "process_group_alive",
        "process_pid",
        "process_group",
        "start_ticks",
        "exit_code",
        "pgdata_exists",
        "cleanup_complete",
    }
    if not isinstance(document, dict) or set(document) != fields:
        raise EvidenceIncomplete("PostgreSQL cleanup evidence fields are invalid")
    if (
        document["approval_sha256"] != capability.postgres.approval_sha256
        or document["listener_alive"] is not False
        or type(document["listener_negative_probes"]) is not int
        or document["listener_negative_probes"] != 3
        or document["process_alive"] is not False
        or document["process_group_alive"] is not False
        or type(document["process_pid"]) is not int
        or not 2 <= document["process_pid"] <= _MAX_PID
        or type(document["process_group"]) is not int
        or not 2 <= document["process_group"] <= _MAX_PID
        or document["process_group"] != document["process_pid"]
        or type(document["start_ticks"]) is not int
        or not 1 <= document["start_ticks"] <= _MAX_UNSIGNED_64
        or type(document["exit_code"]) is not int
        or document["exit_code"] != 0
        or document["pgdata_exists"] is not False
        or document["cleanup_complete"] is not True
    ):
        raise EvidenceIncomplete("PostgreSQL cleanup is incomplete")
    value = PostgresCleanupEvidence()
    for name in fields:
        object.__setattr__(value, name, document[name])
    _ISSUED_CLEANUP.add(value)
    return value


def request_and_wait_for_postgres_cleanup(
    capability: ValidatedPackage6Capability,
) -> PostgresCleanupEvidence:
    """Handshake with the separately approved PostgreSQL lifecycle controller."""

    if not is_issued_capability(capability):
        raise TypeError("validated Package 6 capability is required")
    root = capability.evidence_root
    request = _canonical(
        {
            "package6_approval_sha256": capability.approval_sha256,
            "postgres_approval_sha256": capability.postgres.approval_sha256,
            "action": "STOP_AND_REMOVE_DISPOSABLE_POSTGRES",
        }
    )
    _write_files(root, {"postgres-cleanup-request.json": request})
    deadline = time.monotonic() + capability.cleanup_timeout_seconds
    while time.monotonic() < deadline:
        try:
            raw = _safe_read(root, "postgres-cleanup-evidence.json")
        except EvidenceIncomplete:
            time.sleep(0.05)
            continue
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as error:
            raise EvidenceIncomplete("PostgreSQL cleanup JSON is invalid") from error
        return issue_postgres_cleanup_evidence(capability, document)
    raise EvidenceIncomplete("PostgreSQL cleanup controller did not complete")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise EvidenceIncomplete(f"{label} is not an object")
    return value


def _mapping_list(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise EvidenceIncomplete(f"{label} is not a list")
    return [_mapping(item, f"{label} item") for item in value]


def _validate_stop_transcripts(
    stop: object,
    *,
    component: str,
    max_output_bytes: int,
) -> tuple[str, str]:
    record = _mapping(stop, f"{component} stop evidence")
    if set(record) != _STOP_FIELDS:
        raise EvidenceIncomplete("runtime stop evidence fields are invalid")
    if (
        record["component"] != component.upper()
        or record["operation_id"]
        != ("job-api.stop" if component == "job_api" else "worker.stop")
        or not isinstance(record["native_operation_id"], str)
        or re.fullmatch(r"[0-9a-f]{32}", record["native_operation_id"]) is None
        or record["native_operation_id"] == "0" * 32
        or not isinstance(record["recovery_token"], str)
        or re.fullmatch(r"[0-9a-f]{32}", record["recovery_token"]) is None
        or record["recovery_token"] == "0" * 32
        or record["state"] != "RESULT_RETAINED"
        or type(record["exit_code"]) is not int
        or not -(2**31) <= record["exit_code"] <= 2**31 - 1
        or record["cleanup_proven"] is not True
        or type(max_output_bytes) is not int
        or not 1 <= max_output_bytes <= 1_048_576
    ):
        raise EvidenceIncomplete("native STOP evidence is invalid")
    stream_identities: list[str] = []
    for stream_name in ("stdout", "stderr"):
        metadata = _mapping(
            record.get(stream_name), f"{component} {stream_name} transcript"
        )
        digest = metadata.get("sha256")
        size = metadata.get("size")
        observed_size = metadata.get("observed_size")
        truncated = metadata.get("truncated")
        eof = metadata.get("eof")
        if (
            set(metadata) != _NATIVE_TRANSCRIPT_FIELDS
            or not isinstance(digest, str)
            or _HEX64.fullmatch(digest) is None
            or type(size) is not int
            or not 0 <= size <= max_output_bytes
            or type(observed_size) is not int
            or not size <= observed_size <= _MAX_UNSIGNED_64
            or type(truncated) is not bool
            or truncated is not (size < observed_size)
            or eof is not True
        ):
            raise EvidenceIncomplete(
                "native transcript metadata is invalid"
            )
        stream_identities.append(
            f"{record['native_operation_id']}:{stream_name}"
        )
    return stream_identities[0], stream_identities[1]


def _runtime_evidence_required_names() -> frozenset[str]:
    return frozenset(
        {
            "index.json",
            "approval.json",
            "postgres-approval.json",
            "runtime.json",
            *(Path(path).name for path in EVIDENCE_DOCUMENTS),
        }
    )


def _encode_runtime_evidence_container(files: Mapping[str, bytes]) -> bytes:
    required = _runtime_evidence_required_names()
    if set(files) != required:
        raise EvidenceIncomplete("runtime evidence inventory is not exact")
    entries = []
    for name in sorted(required):
        raw = files[name]
        if not isinstance(raw, bytes):
            raise TypeError("runtime evidence entries must be bytes")
        entries.append(
            {
                "path": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }
        )
    encoded = _canonical(
        {
            "schema_version": "1",
            "container_kind": "PACKAGE6_RUNTIME_EVIDENCE_CONTAINER",
            "entries": entries,
        }
    )
    if len(encoded) > _MAX_RUNTIME_EVIDENCE_CONTAINER_BYTES:
        raise EvidenceIncomplete("runtime evidence container is oversized")
    return encoded


def _decode_runtime_evidence_container(raw: bytes) -> Mapping[str, bytes]:
    if len(raw) > _MAX_RUNTIME_EVIDENCE_CONTAINER_BYTES:
        raise EvidenceIncomplete("runtime evidence container is oversized")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceIncomplete("runtime evidence container is invalid") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "container_kind", "entries"}
        or document["schema_version"] != "1"
        or document["container_kind"]
        != "PACKAGE6_RUNTIME_EVIDENCE_CONTAINER"
        or not isinstance(document["entries"], list)
        or raw != _canonical(document)
    ):
        raise EvidenceIncomplete("runtime evidence container schema is invalid")
    required = _runtime_evidence_required_names()
    entry_paths = [
        entry.get("path") if isinstance(entry, dict) else None
        for entry in document["entries"]
    ]
    if entry_paths != sorted(required):
        raise EvidenceIncomplete("runtime evidence container schema is invalid")
    snapshot: dict[str, bytes] = {}
    for entry in document["entries"]:
        if (
            not isinstance(entry, dict)
            or set(entry)
            != {"path", "sha256", "size_bytes", "content_base64"}
            or not isinstance(entry["path"], str)
            or PurePosixPath(entry["path"]).name != entry["path"]
            or entry["path"] in snapshot
            or not isinstance(entry["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
            or type(entry["size_bytes"]) is not int
            or not 0 <= entry["size_bytes"] <= _MAX_SIGNED_64
            or not isinstance(entry["content_base64"], str)
        ):
            raise EvidenceIncomplete("runtime evidence container entry is invalid")
        try:
            content = base64.b64decode(
                entry["content_base64"].encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, ValueError) as error:
            raise EvidenceIncomplete(
                "runtime evidence container content is invalid"
            ) from error
        if (
            base64.b64encode(content).decode("ascii")
            != entry["content_base64"]
        ):
            raise EvidenceIncomplete(
                "runtime evidence container content is noncanonical"
            )
        if (
            len(content) != entry["size_bytes"]
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
            raise EvidenceIncomplete(
                "runtime evidence container digest or size does not match"
            )
        snapshot[entry["path"]] = content
    if set(snapshot) != required:
        raise EvidenceIncomplete("runtime evidence inventory is not exact")
    return MappingProxyType(snapshot)


def _stable_file_metadata(info: os.stat_result) -> tuple[int, ...]:
    return (
        stat.S_IFMT(info.st_mode),
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


class _RuntimeEvidenceSnapshot(Mapping[str, bytes]):
    """Immutable bytes plus retained path and descriptor authority."""

    def __init__(
        self,
        *,
        directory: _EvidenceDirectoryAuthority,
        descriptor: _DescriptorAuthority,
        name: str,
        raw: bytes,
        snapshot: Mapping[str, bytes],
        metadata: tuple[int, ...],
    ) -> None:
        self._directory = directory
        self._descriptor = descriptor
        self._name = name
        self._raw = raw
        self._raw_sha256 = hashlib.sha256(raw).hexdigest()
        self._snapshot = snapshot
        self._metadata = metadata

    def __getitem__(self, key: str) -> bytes:
        return self._snapshot[key]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._snapshot)

    def __len__(self) -> int:
        return len(self._snapshot)

    def revalidate(self) -> None:
        self._directory.validate_path()
        raw = _read_owned_evidence_container(self._descriptor.descriptor)
        try:
            descriptor_info = os.fstat(self._descriptor.descriptor)
            path_info = os.stat(
                self._name,
                dir_fd=self._directory.descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise EvidenceIncomplete(
                "runtime evidence container identity is unavailable"
            ) from error
        if (
            _stable_file_metadata(descriptor_info) != self._metadata
            or _stable_file_metadata(path_info) != self._metadata
            or raw != self._raw
            or hashlib.sha256(raw).hexdigest() != self._raw_sha256
        ):
            raise EvidenceIncomplete(
                "runtime evidence container identity or bytes changed"
            )

    def close(self) -> bool:
        file_closed = self._descriptor.prove_closed()
        directory_closed = self._directory.prove_closed()
        return file_closed and directory_closed

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


def _open_runtime_evidence_snapshot(root: Path) -> _RuntimeEvidenceSnapshot:
    if not root.is_absolute() or root != Path(os.path.normpath(root)):
        raise EvidenceIncomplete("runtime evidence container path is unsafe")
    try:
        resolved_parent = root.parent.resolve(strict=True)
    except OSError as error:
        raise EvidenceIncomplete("runtime evidence container is unavailable") from error
    if resolved_parent != root.parent:
        raise EvidenceIncomplete("runtime evidence container path is unsafe")
    directory = _strict_output_directory(root.parent)
    descriptor: _DescriptorAuthority | None = None
    try:
        descriptor = _native_open(
            root.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory.descriptor,
        )
        raw = _read_owned_evidence_container(descriptor.descriptor)
        descriptor_info = os.fstat(descriptor.descriptor)
        path_info = os.stat(
            root.name,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
        metadata = _stable_file_metadata(descriptor_info)
        if _stable_file_metadata(path_info) != metadata:
            raise EvidenceIncomplete(
                "runtime evidence container path identity changed"
            )
        snapshot = _decode_runtime_evidence_container(raw)
        return _RuntimeEvidenceSnapshot(
            directory=directory,
            descriptor=descriptor,
            name=root.name,
            raw=raw,
            snapshot=snapshot,
            metadata=metadata,
        )
    except BaseException:
        file_closed = descriptor is None or descriptor.prove_closed()
        directory_closed = directory.prove_closed()
        if not file_closed or not directory_closed:
            raise EvidenceIncomplete(
                "runtime evidence descriptor cleanup is incomplete"
            )
        raise


def _load_runtime_evidence_snapshot(root: Path) -> Mapping[str, bytes]:
    authority = _open_runtime_evidence_snapshot(root)
    try:
        return MappingProxyType(dict(authority))
    finally:
        if not authority.close():
            raise EvidenceIncomplete(
                "runtime evidence descriptor cleanup is incomplete"
            )


def _snapshot_read(snapshot: Mapping[str, bytes], relative: str) -> bytes:
    try:
        return snapshot[relative]
    except KeyError as error:
        raise EvidenceIncomplete("runtime evidence snapshot is incomplete") from error


def _open_publication_tmpfile(
    root: _EvidenceDirectoryAuthority,
    publication: _CreatedEvidenceFile,
) -> _DescriptorAuthority:
    writer = _native_open(
        ".",
        os.O_RDWR | os.O_TMPFILE | os.O_CLOEXEC,
        0o600,
        dir_fd=root.descriptor,
    )
    try:
        custody = _native_open(
            f"/proc/self/fd/{writer.descriptor}",
            os.O_PATH | os.O_CLOEXEC,
            publication=publication,
        )
        if custody.identity != writer.identity:
            raise EvidenceIncomplete(
                "publication descriptor custody identity is invalid"
            )
        return writer
    except BaseException:
        writer.prove_closed()
        raise


def _safe_read(
    root: Path,
    relative: str,
    *,
    maximum: int = 2 * 1024 * 1024,
    strict_root: bool = True,
    private_file: bool = False,
) -> bytes:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != relative:
        raise EvidenceIncomplete("evidence source path is invalid")
    try:
        root_fd = _native_open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise EvidenceIncomplete("evidence directory is unavailable") from error
    descriptor: _DescriptorAuthority | None = None
    try:
        root_info = os.fstat(root_fd.descriptor)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or (
                strict_root
                and stat.S_IMODE(root_info.st_mode) != 0o700
            )
            or (
                not strict_root
                and root_info.st_mode & 0o022
            )
        ):
            raise EvidenceIncomplete("evidence directory policy is invalid")
        descriptor = _native_open(
            relative,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root_fd.descriptor,
        )
        info = os.fstat(descriptor.descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or (
                private_file
                and stat.S_IMODE(info.st_mode) != 0o600
            )
            or (
                not private_file
                and info.st_mode & 0o022
            )
            or info.st_size > maximum
        ):
            raise EvidenceIncomplete("evidence source file policy is invalid")
        raw = os.read(descriptor.descriptor, maximum + 1)
        if len(raw) != info.st_size:
            raise EvidenceIncomplete("evidence source file changed while reading")
        return raw
    except OSError as error:
        raise EvidenceIncomplete("evidence source file is unavailable") from error
    finally:
        file_closed = descriptor is None or descriptor.prove_closed()
        root_closed = root_fd.prove_closed()
        if not file_closed or not root_closed:
            raise EvidenceIncomplete(
                "evidence source descriptor cleanup is incomplete"
            )


def _write_files(
    root: Path | _EvidenceDirectoryAuthority,
    files: dict[str, bytes],
    *,
    post_write_check: Callable[[], None] | None = None,
) -> tuple[_CreatedEvidenceFile, ...]:
    """Publish exactly one complete private file through one final link."""

    if len(files) != 1:
        raise EvidenceIncomplete("multi-file authoritative publication is forbidden")
    name, raw = next(iter(files.items()))
    if (
        not isinstance(name, str)
        or PurePosixPath(name).name != name
        or name in {"", ".", ".."}
        or not isinstance(raw, bytes)
    ):
        raise EvidenceIncomplete("evidence publication input is invalid")
    borrowed = isinstance(root, _EvidenceDirectoryAuthority)
    root_fd = root if borrowed else _strict_output_directory(root)
    publication = _CreatedEvidenceFile(name)
    descriptor: _DescriptorAuthority | None = None
    committed = False
    try:
        root_fd.validate_path()
        descriptor = _open_publication_tmpfile(root_fd, publication)
        created_info = os.fstat(descriptor.descriptor)
        if publication.descriptor_identity != (
            created_info.st_dev,
            created_info.st_ino,
        ):
            raise EvidenceIncomplete("created evidence file identity is invalid")
        os.fchmod(descriptor.descriptor, 0o600)
        view = memoryview(raw)
        while view:
            view = view[os.write(descriptor.descriptor, view) :]
        try:
            os.fsync(descriptor.descriptor)
        except OSError as error:
            if error.errno == errno.EBADF:
                descriptor.owner.abandon_uncertain_generation()
            raise
        except BaseException:
            descriptor.owner.abandon_uncertain_generation()
            raise
        if not descriptor.prove_closed():
            raise OSError("evidence writer descriptor cleanup is incomplete")
        descriptor = None
        if post_write_check is not None:
            post_write_check()
        root_fd.validate_path()
        _link_owned_tmpfile(
            publication.descriptor,
            root_fd.descriptor,
            publication.name,
        )
        committed = True
        os.fsync(root_fd.descriptor)
        path_info = os.stat(
            publication.name,
            dir_fd=root_fd.descriptor,
            follow_symlinks=False,
        )
        if (
            publication.descriptor_identity
            != (path_info.st_dev, path_info.st_ino)
            or not stat.S_ISREG(path_info.st_mode)
            or path_info.st_nlink != 1
            or path_info.st_uid != os.geteuid()
            or stat.S_IMODE(path_info.st_mode) != 0o600
            or path_info.st_size != len(raw)
        ):
            raise EvidenceIncomplete("published evidence file identity is invalid")
        if not publication.prove_closed():
            raise OSError("evidence publication descriptor cleanup is incomplete")
        return (publication,)
    except BaseException as error:
        file_closed = descriptor is None or descriptor.prove_closed()
        publication_closed = publication.prove_closed()
        if not file_closed or not publication_closed:
            raise _EvidencePublicationFailure(
                _EvidencePublicationRecovery(root_fd, (publication,))
            ) from error
        # A post-link error preserves one complete file. A pre-link error has
        # no pathname and the unnamed inode disappears when custody closes.
        raise
    finally:
        if not borrowed and not root_fd.prove_closed():
            recovery = _EvidencePublicationRecovery(root_fd, (publication,))
            if committed:
                raise _EvidencePublicationFailure(recovery)
            raise _EvidencePublicationFailure(recovery)


def _read_owned_evidence_container(
    descriptor: int,
    *,
    expected_link_count: int = 1,
) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != expected_link_count
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 <= before.st_size <= _MAX_RUNTIME_EVIDENCE_CONTAINER_BYTES
        ):
            raise EvidenceIncomplete("runtime evidence container policy is invalid")
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as error:
        raise EvidenceIncomplete("runtime evidence container is unavailable") from error
    if (
        remaining != 0
        or len(raw) != before.st_size
        or (
            stat.S_IFMT(after.st_mode),
            after.st_uid,
            after.st_gid,
            stat.S_IMODE(after.st_mode),
            after.st_nlink,
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        != (
            stat.S_IFMT(before.st_mode),
            before.st_uid,
            before.st_gid,
            stat.S_IMODE(before.st_mode),
            before.st_nlink,
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
    ):
        raise EvidenceIncomplete("runtime evidence container changed while reading")
    return raw


def _publish_evidence_container(
    evidence_root: Path,
    bundle: Path,
    files: Mapping[str, bytes],
    verifier: Callable[[Mapping[str, bytes]], object],
) -> Path:
    """Verify unnamed bytes and publish one complete runtime container."""

    if bundle.parent != evidence_root or bundle.name in {"", ".", ".."}:
        raise EvidenceIncomplete("runtime evidence container path is invalid")
    container_raw = _encode_runtime_evidence_container(files)
    root = _strict_output_directory(evidence_root)
    publication = _CreatedEvidenceFile(bundle.name)
    descriptor: _DescriptorAuthority | None = None
    try:
        descriptor = _open_publication_tmpfile(root, publication)
        os.fchmod(descriptor.descriptor, 0o600)
        view = memoryview(container_raw)
        while view:
            view = view[os.write(descriptor.descriptor, view) :]
        try:
            os.fsync(descriptor.descriptor)
        except OSError as error:
            if error.errno == errno.EBADF:
                descriptor.owner.abandon_uncertain_generation()
            raise
        except BaseException:
            descriptor.owner.abandon_uncertain_generation()
            raise
        verified_raw = _read_owned_evidence_container(
            descriptor.descriptor,
            expected_link_count=0,
        )
        verified_snapshot = _decode_runtime_evidence_container(verified_raw)
        verifier(verified_snapshot)
        committed_raw = _read_owned_evidence_container(
            descriptor.descriptor,
            expected_link_count=0,
        )
        if committed_raw != verified_raw:
            raise EvidenceIncomplete(
                "verified evidence bytes changed before publication commit"
            )
        root.validate_path()
        _link_owned_tmpfile(
            publication.descriptor,
            root.descriptor,
            bundle.name,
        )
        os.fsync(root.descriptor)
        current = os.stat(
            bundle.name,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
        if (
            publication.descriptor_identity
            != (current.st_dev, current.st_ino)
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(current.st_mode) != 0o600
            or current.st_size != len(committed_raw)
        ):
            raise EvidenceIncomplete(
                "runtime evidence container identity changed after commit"
            )
    except BaseException as error:
        file_closed = descriptor is None or descriptor.prove_closed()
        publication_closed = publication.prove_closed()
        directory_closed = root.prove_closed()
        if not file_closed or not publication_closed or not directory_closed:
            raise _EvidencePublicationFailure(
                _EvidencePublicationRecovery(root, (publication,))
            ) from error
        raise
    if (
        descriptor is None
        or not descriptor.prove_closed()
        or not publication.prove_closed()
        or not root.prove_closed()
    ):
        raise _EvidencePublicationFailure(
            _EvidencePublicationRecovery(root, (publication,))
        )
    return bundle


def _authority_path(value: object, label: str) -> Path:
    if not isinstance(value, str):
        raise EvidenceIncomplete(f"{label} authority is invalid")
    path = Path(value)
    if not path.is_absolute() or path != Path(os.path.normpath(path)):
        raise EvidenceIncomplete(f"{label} authority is invalid")
    return path


def _assert_paths_outside_cleanup_roots(
    paths: tuple[Path, ...],
    roots: tuple[Path, Path],
) -> None:
    if any(
        path == root or root in path.parents
        for path in paths
        for root in roots
    ):
        raise EvidenceIncomplete(
            "controller evidence and output must remain outside cleanup roots"
        )


def _assert_cleanup_roots_absent(
    candidate_root: Path,
    postgres_root: Path,
) -> None:
    remaining: list[str] = []
    for label, root in (
        ("candidate", candidate_root),
        ("postgres", postgres_root),
    ):
        try:
            root.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise EvidenceIncomplete(
                "cleanup root absence could not be proven"
            ) from error
        remaining.append(label)
    if remaining:
        joined = " and ".join(remaining)
        raise EvidenceIncomplete(f"{joined} cleanup roots remain")


def _private_input(path: Path, label: str) -> bytes:
    if not path.is_absolute() or path != Path(os.path.normpath(path)):
        raise EvidenceIncomplete(f"{label} path is unsafe")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise EvidenceIncomplete(f"{label} is unavailable") from error
    if parent != path.parent:
        raise EvidenceIncomplete(f"{label} path is unsafe")
    return _safe_read(parent, path.name, private_file=True)


def _reject_sensitive_fields(value: object) -> None:
    secret_fragments = ("credential", "password", "secret", "token")
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower()
            stream_metadata = (
                normalized in {"stdout", "stderr"}
                and isinstance(child, dict)
                and set(child)
                in (_NATIVE_TRANSCRIPT_FIELDS, _DIAGNOSTIC_TRANSCRIPT_FIELDS)
            )
            if (
                any(part in normalized for part in secret_fragments)
                or "content" in normalized
                or normalized == "transcript"
                or normalized == "raw"
                or normalized.startswith("raw_")
                or (
                    normalized in {"stdout", "stderr"}
                    and not stream_metadata
                )
            ):
                raise EvidenceIncomplete("evidence contains forbidden fields")
            _reject_sensitive_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_fields(child)


def _validate_diagnostic_transcript_metadata(value: object) -> None:
    components = _mapping(value, "diagnostic transcript metadata")
    if set(components) != {"worker", "job_api"}:
        raise EvidenceIncomplete("diagnostic transcript metadata is invalid")
    observed_paths: list[Path] = []
    for component in ("worker", "job_api"):
        streams = _mapping(
            components[component],
            f"diagnostic {component} transcript metadata",
        )
        if set(streams) != {"stdout", "stderr"}:
            raise EvidenceIncomplete("diagnostic transcript metadata is invalid")
        for stream_name in ("stdout", "stderr"):
            metadata = _mapping(
                streams[stream_name],
                f"diagnostic {component} {stream_name} transcript",
            )
            path_value = metadata.get("path")
            digest = metadata.get("sha256")
            size = metadata.get("size")
            truncated = metadata.get("truncated")
            if (
                set(metadata) != _DIAGNOSTIC_TRANSCRIPT_FIELDS
                or not isinstance(path_value, (str, Path))
                or not isinstance(digest, str)
                or _HEX64.fullmatch(digest) is None
                or type(size) is not int
                or not 0 <= size <= _MAX_DIAGNOSTIC_TRANSCRIPT_BYTES
                or type(truncated) is not bool
            ):
                raise EvidenceIncomplete("diagnostic transcript metadata is invalid")
            path = Path(path_value)
            expected_name = re.fullmatch(
                rf"package6-{re.escape(component)}-[0-9a-f]{{32}}"
                rf"\.{stream_name}\.transcript",
                path.name,
            )
            if (
                not path.is_absolute()
                or path != Path(os.path.normpath(path))
                or expected_name is None
            ):
                raise EvidenceIncomplete("diagnostic transcript metadata is invalid")
            observed_paths.append(path)
    if len(set(observed_paths)) != 4:
        raise EvidenceIncomplete("diagnostic transcript metadata is invalid")


def _validate_diagnostic_index(
    value: object,
    *,
    candidate_commit: str,
    candidate_tree: str,
    source_diff_sha256: str,
) -> None:
    diagnostic = _mapping(value, "diagnostic index")
    if (
        set(diagnostic) != _DIAGNOSTIC_FIELDS
        or type(diagnostic["schema_version"]) is not int
        or diagnostic["schema_version"] != 1
        or diagnostic["verdict"] != "PASS"
        or diagnostic["candidate_commit"] != candidate_commit
        or diagnostic["candidate_tree"] != candidate_tree
        or diagnostic["source_diff_sha256"] != source_diff_sha256
        or diagnostic["runtime_attempt"] != "D1"
        or diagnostic["test_nodeid"] != _DIAGNOSTIC_NODEID
        or type(diagnostic["exit_code"]) is not int
        or diagnostic["exit_code"] != 0
        or type(diagnostic["passed"]) is not int
        or diagnostic["passed"] != 1
        or type(diagnostic["failed"]) is not int
        or diagnostic["failed"] != 0
        or diagnostic["live_execution_approved"] is not False
        or diagnostic["live_trading_approved"] is not False
    ):
        raise EvidenceIncomplete("diagnostic index schema is invalid")
    _validate_diagnostic_transcript_metadata(diagnostic["transcript_metadata"])


def _strict_output_directory(path: Path) -> _EvidenceDirectoryAuthority:
    if not path.is_absolute() or path != Path(os.path.normpath(path)):
        raise EvidenceIncomplete("controller output directory path is unsafe")
    descriptor: _DescriptorAuthority | None = None
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        descriptor = _native_open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        descriptor_info = os.fstat(descriptor.descriptor)
    except OSError as error:
        raise EvidenceIncomplete("controller output directory is unavailable") from error
    if (
        resolved != path
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or not stat.S_ISDIR(descriptor_info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or (
            descriptor_info.st_dev,
            descriptor_info.st_ino,
            descriptor_info.st_uid,
            stat.S_IMODE(descriptor_info.st_mode),
        )
        != (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            stat.S_IMODE(info.st_mode),
        )
    ):
        descriptor.prove_closed()
        raise EvidenceIncomplete("controller output directory policy is invalid")
    return _EvidenceDirectoryAuthority(
        path=path,
        descriptor_authority=descriptor,
        device=info.st_dev,
        inode=info.st_ino,
        owner=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
    )


def _validate_closure_custodian_authority(
    *,
    approval: Mapping[str, object],
    runtime: Mapping[str, object],
    candidate_commit: str,
    candidate_tree: str,
    custodian_helper_binary_sha256: str,
    custodian_native_source_set_sha256: str,
    custodian_protocol_version: object,
    custodian_protocol_features: list[str] | tuple[str, ...],
    custodian_endpoint_authority: str,
    custodian_operations: list[str] | tuple[str, ...],
    custodian_stage_sha256: str,
    custodian_fixture_sha256: str,
    custodian_publications: list[str] | tuple[str, ...],
) -> dict[str, object]:
    """Bind one final record to exact approval and native publication facts."""

    try:
        authority = _mapping(
            approval.get("custodian_authority"),
            "custodian closure authority",
        )
        fixture = _mapping(
            authority.get("fixture_identity"),
            "custodian closure fixture",
        )
        child_environment = _mapping(
            authority.get("child_environment_contract"),
            "custodian closure child environment",
        )
        raw_source_set = authority.get("native_source_set")
        if not isinstance(raw_source_set, list):
            raise EvidenceIncomplete(
                "custodian closure source authority is invalid"
            )
        source_set = [
            _mapping(item, "custodian closure source binding")
            for item in raw_source_set
        ]
        if (
            set(authority) != _CUSTODIAN_CLOSURE_FIELDS
            or set(fixture) != {"sha256", "provenance"}
            or child_environment
            != {
                "job_api": list(PACKAGE6_JOB_API_ENVIRONMENT_KEYS),
                "worker": list(PACKAGE6_WORKER_ENVIRONMENT_KEYS),
            }
            or len(source_set) != len(PACKAGE6_CUSTODIAN_SOURCE_PATHS)
            or any(set(item) != {"path", "sha256"} for item in source_set)
            or tuple(item["path"] for item in source_set)
            != PACKAGE6_CUSTODIAN_SOURCE_PATHS
            or any(
                not isinstance(item["sha256"], str)
                or _HEX64.fullmatch(item["sha256"]) is None
                for item in source_set
            )
        ):
            raise EvidenceIncomplete(
                "custodian closure source authority is invalid"
            )
        source_set_raw = json.dumps(
            source_set,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if (
            authority["native_source_set_sha256"]
            != hashlib.sha256(source_set_raw).hexdigest()
        ):
            raise EvidenceIncomplete(
                "custodian closure source authority is invalid"
            )
        runtime_source = _mapping(
            runtime.get("source"), "custodian closure runtime source"
        )
        if (
            not {"commit", "tree"} <= set(runtime_source)
            or runtime_source["commit"] != candidate_commit
            or runtime_source["tree"] != candidate_tree
        ):
            raise EvidenceIncomplete(
                "custodian closure candidate authority is invalid"
            )
        if type(custodian_protocol_version) is int:
            protocol_version = (
                "1" if custodian_protocol_version == 1 else ""
            )
        elif isinstance(custodian_protocol_version, str):
            protocol_version = custodian_protocol_version
        else:
            protocol_version = ""
        expected = {
            "authority_mode": "DISPOSABLE_TEST_NATIVE_ONLY",
            "helper_binary_sha256": custodian_helper_binary_sha256,
            "native_source_set_sha256": (
                custodian_native_source_set_sha256
            ),
            "protocol_version": protocol_version,
            "protocol_features": list(custodian_protocol_features),
            "endpoint_authority": custodian_endpoint_authority,
            "operations": list(custodian_operations),
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
            "stage_sha256": custodian_stage_sha256,
            "child_environment_contract": {
                "job_api": list(PACKAGE6_JOB_API_ENVIRONMENT_KEYS),
                "worker": list(PACKAGE6_WORKER_ENVIRONMENT_KEYS),
            },
            "mode": "PAPER",
            "live_execution_approved": False,
            "live_trading_approved": False,
        }
        if (
            any(authority.get(key) != value for key, value in expected.items())
            or authority["production_socket_activation"] is not False
            or fixture
            != {
                "sha256": custodian_fixture_sha256,
                "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            }
            or tuple(custodian_operations)
            != PACKAGE6_CUSTODIAN_OPERATIONS
            or protocol_version != "1"
            or tuple(custodian_protocol_features) != ()
            or any(
                not isinstance(value, str)
                or _HEX64.fullmatch(value) is None
                for value in (
                    custodian_helper_binary_sha256,
                    custodian_native_source_set_sha256,
                    custodian_stage_sha256,
                    custodian_fixture_sha256,
                )
            )
        ):
            raise EvidenceIncomplete(
                "custodian closure authority does not match approval"
            )
        if (
            type(custodian_publications) not in (list, tuple)
            or len(custodian_publications) != 2
        ):
            raise EvidenceIncomplete(
                "custodian closure publication authority is invalid"
            )
        provided_publications: dict[str, str] = {}
        for item in custodian_publications:
            if not isinstance(item, str) or item.count("=") != 1:
                raise EvidenceIncomplete(
                    "custodian closure publication authority is invalid"
                )
            component, digest = item.split("=", 1)
            if (
                component not in {"job_api", "worker"}
                or component in provided_publications
                or _HEX64.fullmatch(digest) is None
            ):
                raise EvidenceIncomplete(
                    "custodian closure publication authority is invalid"
                )
            provided_publications[component] = digest
        if set(provided_publications) != {"job_api", "worker"}:
            raise EvidenceIncomplete(
                "custodian closure publication authority is invalid"
            )
        chain = _mapping(
            runtime.get("chain"), "custodian closure runtime chain"
        )
        native_publications = _mapping(
            chain.get("native_publications"),
            "custodian closure native publications",
        )
        if set(native_publications) != {"job_api", "worker"}:
            raise EvidenceIncomplete(
                "custodian closure publication authority is invalid"
            )
        observed_publications: dict[str, str] = {}
        observed_receipts: dict[str, dict[str, str]] = {}
        for component in ("job_api", "worker"):
            publication = _mapping(
                native_publications.get(component),
                "custodian closure native publication",
            )
            stop = _mapping(
                chain.get(f"{component}_stop"),
                "custodian closure native STOP",
            )
            if (
                set(publication)
                != {"operation_id", "manifest_sha256"}
                or not isinstance(publication["operation_id"], str)
                or re.fullmatch(
                    r"[0-9a-f]{32}", publication["operation_id"]
                )
                is None
                or publication["operation_id"] == "0" * 32
                or stop.get("native_operation_id")
                != publication["operation_id"]
                or not isinstance(publication["manifest_sha256"], str)
                or _HEX64.fullmatch(publication["manifest_sha256"]) is None
            ):
                raise EvidenceIncomplete(
                    "custodian closure publication authority is invalid"
                )
            observed_publications[component] = publication[
                "manifest_sha256"
            ]
            observed_receipts[component] = {
                "operation_id": publication["operation_id"],
                "manifest_sha256": publication["manifest_sha256"],
            }
        if len(
            {
                receipt["operation_id"]
                for receipt in observed_receipts.values()
            }
        ) != 2:
            raise EvidenceIncomplete(
                "custodian closure publication authority is invalid"
            )
        if provided_publications != observed_publications:
            raise EvidenceIncomplete(
                "custodian closure publication authority does not match"
            )
    except (KeyError, TypeError, ValueError) as error:
        raise EvidenceIncomplete(
            "custodian closure authority is invalid"
        ) from error
    normalized = {
        key: (
            [dict(item) for item in source_set]
            if key == "native_source_set"
            else dict(fixture)
            if key == "fixture_identity"
            else list(authority[key])
            if key in {"protocol_features", "operations"}
            else authority[key]
        )
        for key in _CUSTODIAN_CLOSURE_FIELDS
    }
    normalized["publications"] = {
        component: observed_receipts[component]
        for component in ("job_api", "worker")
    }
    return normalized


def _encode_controller_final_container(files: Mapping[str, bytes]) -> bytes:
    required = {"controller-final-decision.json", "index.json"}
    if set(files) != required:
        raise EvidenceIncomplete("controller final inventory is not exact")
    entries = []
    for path in sorted(required):
        raw = files[path]
        if not isinstance(raw, bytes):
            raise EvidenceIncomplete("controller final entry is invalid")
        entries.append(
            {
                "content_base64": base64.b64encode(raw).decode("ascii"),
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }
        )
    encoded = _canonical(
        {
            "container_kind": _CONTROLLER_FINAL_KIND,
            "entries": entries,
            "schema_version": "1",
        }
    )
    if len(encoded) > _MAX_CONTROLLER_FINAL_CONTAINER_BYTES:
        raise EvidenceIncomplete("controller final container is oversized")
    return encoded


def _verify_controller_final_container(raw: bytes) -> dict[str, bytes]:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_CONTROLLER_FINAL_CONTAINER_BYTES:
        raise EvidenceIncomplete("controller final container size is invalid")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceIncomplete("controller final container JSON is invalid") from error
    if _canonical(document) != raw:
        raise EvidenceIncomplete("controller final container JSON is noncanonical")
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "container_kind", "entries"}
        or document["schema_version"] != "1"
        or document["container_kind"] != _CONTROLLER_FINAL_KIND
        or not isinstance(document["entries"], list)
        or len(document["entries"]) != 2
    ):
        raise EvidenceIncomplete("controller final container schema is invalid")
    result: dict[str, bytes] = {}
    expected_paths = ["controller-final-decision.json", "index.json"]
    for expected_path, entry in zip(expected_paths, document["entries"], strict=True):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"content_base64", "path", "sha256", "size_bytes"}
            or entry.get("path") != expected_path
            or not isinstance(entry.get("content_base64"), str)
            or not isinstance(entry.get("sha256"), str)
            or _HEX64.fullmatch(entry["sha256"]) is None
            or type(entry.get("size_bytes")) is not int
            or not 0 <= entry["size_bytes"] <= _MAX_CONTROLLER_FINAL_CONTAINER_BYTES
        ):
            raise EvidenceIncomplete("controller final entry schema is invalid")
        try:
            content = base64.b64decode(entry["content_base64"], validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise EvidenceIncomplete("controller final Base64 is invalid") from error
        if base64.b64encode(content).decode("ascii") != entry["content_base64"]:
            raise EvidenceIncomplete("controller final Base64 is noncanonical")
        if (
            len(content) != entry["size_bytes"]
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
            raise EvidenceIncomplete("controller final entry binding is invalid")
        result[expected_path] = content
    try:
        decision = json.loads(result["controller-final-decision.json"])
        index = json.loads(result["index.json"])
    except json.JSONDecodeError as error:
        raise EvidenceIncomplete("controller final logical JSON is invalid") from error
    decision_raw = result["controller-final-decision.json"]
    index_raw = result["index.json"]
    if _canonical(decision) != decision_raw or _canonical(index) != index_raw:
        raise EvidenceIncomplete("controller final logical JSON is noncanonical")
    if (
        not isinstance(decision, dict)
        or decision.get("verdict") != _GO_VERDICT
        or decision.get("production_authority_status") != "TEST_ONLY"
        or decision.get("live_execution_approved") is not False
        or decision.get("live_trading_approved") is not False
        or index
        != {
            "entries": [{
                "path": "controller-final-decision.json",
                "sha256": hashlib.sha256(decision_raw).hexdigest(),
                "size_bytes": len(decision_raw),
            }],
            "schema_version": 2,
            "verdict": _GO_VERDICT,
        }
    ):
        raise EvidenceIncomplete("controller final semantics are invalid")
    return result


def _final_metadata(info: os.stat_result) -> tuple[int, ...]:
    return (
        stat.S_IFMT(info.st_mode), info.st_uid, info.st_gid,
        stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _final_descriptor_call(
    descriptor: int | _DescriptorAuthority,
    operation: Callable[[int], Any],
) -> Any:
    if isinstance(descriptor, _DescriptorAuthority):
        return descriptor.call(operation)
    return operation(descriptor)


def _read_final_publication(
    descriptor: int | _DescriptorAuthority,
    *,
    expected_link_count: int,
) -> tuple[bytes, tuple[int, ...]]:
    if expected_link_count not in (0, 1):
        raise ValueError("controller final link count must be zero or one")
    try:
        _final_descriptor_call(
            descriptor,
            lambda value: os.lseek(value, 0, os.SEEK_SET),
        )
        before = _final_descriptor_call(descriptor, os.fstat)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != expected_link_count
            or not 0 <= before.st_size <= _MAX_CONTROLLER_FINAL_CONTAINER_BYTES
        ):
            raise EvidenceIncomplete("controller final descriptor policy is invalid")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = _final_descriptor_call(
                descriptor,
                lambda value: os.read(value, min(remaining, 1024 * 1024)),
            )
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = _final_descriptor_call(descriptor, os.fstat)
    except OSError as error:
        raise EvidenceIncomplete("controller final descriptor is unavailable") from error
    raw = b"".join(chunks)
    if (
        remaining
        or len(raw) != before.st_size
        or _final_metadata(before) != _final_metadata(after)
    ):
        raise EvidenceIncomplete("controller final descriptor changed while reading")
    return raw, _final_metadata(after)


def _probe_attempted_publication(
    authority: FinalPublicationAuthority,
) -> bool | None:
    """Classify an attempted final link through retained descriptors only."""
    try:
        descriptor_info = authority.canonical_descriptor_authority.call(os.fstat)
        path_info = authority.output_directory_authority.descriptor_authority.call(
            lambda descriptor: os.stat(
                authority.final_name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
        )
    except FileNotFoundError:
        return False
    except OSError:
        return None
    expected = (authority.device, authority.inode)
    if (
        (descriptor_info.st_dev, descriptor_info.st_ino) == expected
        and (path_info.st_dev, path_info.st_ino) == expected
        and descriptor_info.st_nlink == 1
        and path_info.st_nlink == 1
        and stat.S_ISREG(path_info.st_mode)
    ):
        return True
    return None


def _confirm_final_publication_identity(authority: FinalPublicationAuthority) -> None:
    try:
        descriptor_info = authority.canonical_descriptor_authority.call(os.fstat)
        path_info = authority.output_directory_authority.descriptor_authority.call(
            lambda descriptor: os.stat(
                authority.final_name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
        )
    except OSError as error:
        authority.recovery_required = True
        raise EvidenceIncomplete("final publication identity is unavailable") from error
    descriptor_metadata = _final_metadata(descriptor_info)
    path_metadata = _final_metadata(path_info)
    if (
        (descriptor_info.st_dev, descriptor_info.st_ino)
        != (authority.device, authority.inode)
        or (path_info.st_dev, path_info.st_ino)
        != (authority.device, authority.inode)
        or descriptor_metadata != path_metadata
        or not stat.S_ISREG(path_info.st_mode)
        or path_info.st_nlink != 1
        or path_info.st_uid != os.geteuid()
        or stat.S_IMODE(path_info.st_mode) != 0o600
        or path_info.st_size != authority.canonical_byte_size
    ):
        authority.recovery_required = True
        raise EvidenceIncomplete("final publication identity does not match")
    authority._stable_metadata = descriptor_metadata
    authority.identity_confirmed = True


def _finalize_controller_evidence(
    *,
    runtime_snapshot: _RuntimeEvidenceSnapshot,
    runtime_bundle: Path,
    output_dir: Path,
    cleanup_path: Path,
    review_path: Path,
    diagnostic_index_path: Path,
    candidate_commit: str,
    candidate_tree: str,
    reviewed_base_commit: str,
    patch_algorithm: str,
    reviewed_patch_sha256: str,
    reviewed_patch_bytes: int,
    reviewed_paths: list[str] | tuple[str, ...],
    source_diff_sha256: str,
    expected_seal_manifest_sha256: str,
    review_verdict_sha256: str,
    diagnostic_index_sha256: str,
    runtime_bundle_index_sha256: str,
    cleanup_evidence_sha256: str,
    custodian_helper_binary_sha256: str,
    custodian_native_source_set_sha256: str,
    custodian_protocol_version: int,
    custodian_protocol_features: list[str] | tuple[str, ...],
    custodian_endpoint_authority: str,
    custodian_operations: list[str] | tuple[str, ...],
    custodian_stage_sha256: str,
    custodian_fixture_sha256: str,
    custodian_publications: list[str] | tuple[str, ...],
    verify_only: bool = False,
) -> FinalPublicationAuthority | None:
    """Verify a pending sealed bundle and optionally write its outer GO record."""

    _verify_runtime_evidence_snapshot(runtime_snapshot)
    try:
        runtime_root = runtime_bundle.resolve(strict=True)
    except OSError as error:
        raise EvidenceIncomplete("runtime evidence bundle is unavailable") from error
    if output_dir == runtime_root or runtime_root in output_dir.parents:
        raise EvidenceIncomplete(
            "controller output must remain separate from runtime evidence"
        )
    identities = (candidate_commit, candidate_tree, reviewed_base_commit)
    digests = (
        reviewed_patch_sha256, source_diff_sha256, review_verdict_sha256,
        diagnostic_index_sha256, runtime_bundle_index_sha256,
        cleanup_evidence_sha256, expected_seal_manifest_sha256,
    )
    if any(
        not isinstance(value, str) or _HEX40.fullmatch(value) is None
        for value in identities
    ) or any(
        not isinstance(value, str) or _HEX64.fullmatch(value) is None
        for value in digests
    ) or (
        patch_algorithm != _PATCH_ALGORITHM
        or type(reviewed_patch_bytes) is not int
        or not 1 <= reviewed_patch_bytes <= _MAX_SIGNED_64
        or type(reviewed_paths) not in (list, tuple)
        or any(
            not isinstance(path, str)
            or not path
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or PurePosixPath(path).as_posix() != path
            for path in reviewed_paths
        )
        or len(reviewed_paths) != len(set(reviewed_paths))
        or tuple(reviewed_paths) != _REQUIRED_REVIEWED_PATHS
        or source_diff_sha256 != reviewed_patch_sha256
    ):
        raise EvidenceIncomplete("controller identity or digest is malformed")

    runtime = json.loads(_snapshot_read(runtime_snapshot, "runtime.json"))
    approval = json.loads(_snapshot_read(runtime_snapshot, "approval.json"))
    if (
        runtime["source"]["commit"] != candidate_commit
        or runtime["source"]["tree"] != candidate_tree
    ):
        raise EvidenceIncomplete("runtime candidate identity does not match")
    custodian_closure = _validate_closure_custodian_authority(
        approval=_mapping(approval, "runtime approval"),
        runtime=_mapping(runtime, "runtime evidence"),
        candidate_commit=candidate_commit,
        candidate_tree=candidate_tree,
        custodian_helper_binary_sha256=(
            custodian_helper_binary_sha256
        ),
        custodian_native_source_set_sha256=(
            custodian_native_source_set_sha256
        ),
        custodian_protocol_version=custodian_protocol_version,
        custodian_protocol_features=custodian_protocol_features,
        custodian_endpoint_authority=custodian_endpoint_authority,
        custodian_operations=custodian_operations,
        custodian_stage_sha256=custodian_stage_sha256,
        custodian_fixture_sha256=custodian_fixture_sha256,
        custodian_publications=custodian_publications,
    )
    bundle_index_raw = _snapshot_read(runtime_snapshot, "index.json")
    review_raw = _private_input(review_path, "review verdict")
    diagnostic_raw = _private_input(diagnostic_index_path, "diagnostic index")
    cleanup_raw = _private_input(cleanup_path, "cleanup evidence")
    actual = (
        hashlib.sha256(review_raw).hexdigest(),
        hashlib.sha256(diagnostic_raw).hexdigest(),
        hashlib.sha256(bundle_index_raw).hexdigest(),
        hashlib.sha256(cleanup_raw).hexdigest(),
    )
    if actual != (
        review_verdict_sha256,
        diagnostic_index_sha256,
        runtime_bundle_index_sha256,
        cleanup_evidence_sha256,
    ):
        raise EvidenceIncomplete("controller evidence digest does not match")
    try:
        review = json.loads(review_raw)
        diagnostic = json.loads(diagnostic_raw)
        cleanup = json.loads(cleanup_raw)
    except json.JSONDecodeError as error:
        raise EvidenceIncomplete("controller evidence JSON is invalid") from error
    _reject_sensitive_fields(review)
    _reject_sensitive_fields(diagnostic)
    _reject_sensitive_fields(cleanup)
    if (
        not isinstance(review, dict)
        or set(review) != _REVIEW_FIELDS
        or type(review["schema_version"]) is not int
        or review["schema_version"] != 1
        or review["verdict"] != "PASS"
        or not isinstance(review["reviewed_base_commit"], str)
        or _HEX40.fullmatch(review["reviewed_base_commit"]) is None
        or review["reviewed_base_commit"] != reviewed_base_commit
        or review["patch_algorithm"] != patch_algorithm
        or not isinstance(review["reviewed_patch_sha256"], str)
        or _HEX64.fullmatch(review["reviewed_patch_sha256"]) is None
        or review["reviewed_patch_sha256"] != reviewed_patch_sha256
        or type(review["reviewed_patch_bytes"]) is not int
        or review["reviewed_patch_bytes"] != reviewed_patch_bytes
        or type(review["reviewed_paths"]) is not list
        or tuple(review["reviewed_paths"]) != tuple(reviewed_paths)
        or review["source_diff_sha256"] != source_diff_sha256
        or review["seal_manifest_sha256"]
        != expected_seal_manifest_sha256
        or review["seal_integrity"] != "PASS"
        or review["production_authority_status"] != "TEST_ONLY"
        or review["findings"] != []
        or review["scope_integrity"] != "PASS"
        or review["test_adequacy"] != "PASS"
        or review["live_execution_approved"] is not False
        or review["live_trading_approved"] is not False
    ):
        raise EvidenceIncomplete("review PASS evidence is invalid")
    _validate_diagnostic_index(
        diagnostic,
        candidate_commit=candidate_commit,
        candidate_tree=candidate_tree,
        source_diff_sha256=source_diff_sha256,
    )
    if not isinstance(cleanup, dict) or set(cleanup) != _CLEANUP_FIELDS:
        raise EvidenceIncomplete("cleanup evidence fields are invalid")
    approval_document = _mapping(approval, "runtime approval")
    postgres_authority = _mapping(
        approval_document.get("postgres_authority"),
        "runtime PostgreSQL authority",
    )
    candidate_root = _authority_path(
        runtime.get("disposable_root"),
        "runtime disposable root",
    )
    postgres_root = _authority_path(
        postgres_authority.get("pgdata"),
        "runtime PostgreSQL pgdata",
    )
    cleanup_roots = (cleanup["candidate_root"], cleanup["postgres_root"])
    if (
        type(cleanup["schema_version"]) is not int
        or cleanup["schema_version"] != 1
        or cleanup["candidate_commit"] != candidate_commit
        or cleanup["candidate_tree"] != candidate_tree
        or cleanup["source_diff_sha256"] != source_diff_sha256
        or type(cleanup["process_refs"]) is not int
        or cleanup["process_refs"] != 0
        or cleanup["surviving_processes"] != []
        or cleanup["surviving_listener_ports"] != []
        or any(
            not isinstance(root, str)
            or not Path(root).is_absolute()
            or Path(root) != Path(os.path.normpath(root))
            for root in cleanup_roots
        )
        or cleanup["candidate_root_exists"] is not False
        or cleanup["postgres_root_exists"] is not False
        or cleanup["evidence_preserved_outside_disposable_root"] is not True
        or cleanup["live_execution_approved"] is not False
        or cleanup["live_trading_approved"] is not False
    ):
        raise EvidenceIncomplete("cleanup or live approval evidence is invalid")
    if (
        cleanup["candidate_root"] != str(candidate_root)
        or cleanup["postgres_root"] != str(postgres_root)
    ):
        raise EvidenceIncomplete("cleanup root authority does not match")
    final_container_path = output_dir / _CONTROLLER_FINAL_NAME
    _assert_paths_outside_cleanup_roots(
        (
            runtime_root,
            output_dir,
            cleanup_path,
            diagnostic_index_path,
            review_path,
            final_container_path,
        ),
        (candidate_root, postgres_root),
    )
    _assert_cleanup_roots_absent(candidate_root, postgres_root)
    runtime_snapshot.revalidate()
    if verify_only:
        output_authority = _strict_output_directory(output_dir)
        try:
            _assert_cleanup_roots_absent(candidate_root, postgres_root)
            output_authority.validate_path()
        finally:
            if not output_authority.prove_closed():
                raise EvidenceIncomplete(
                    "controller output directory cleanup is incomplete"
                )
        return None

    record = {
        "schema_version": 2,
        "verdict": _GO_VERDICT,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "reviewed_base_commit": reviewed_base_commit,
        "patch_algorithm": patch_algorithm,
        "reviewed_patch_sha256": reviewed_patch_sha256,
        "reviewed_patch_bytes": reviewed_patch_bytes,
        "reviewed_paths": list(reviewed_paths),
        "source_diff_sha256": source_diff_sha256,
        "seal_manifest_sha256": expected_seal_manifest_sha256,
        "seal_integrity": "PASS",
        "production_authority_status": "TEST_ONLY",
        "review_verdict_sha256": review_verdict_sha256,
        "diagnostic_index_sha256": diagnostic_index_sha256,
        "runtime_bundle_index_sha256": runtime_bundle_index_sha256,
        "cleanup_evidence_sha256": cleanup_evidence_sha256,
        "custodian_authority": custodian_closure,
        "live_execution_approved": False,
        "live_trading_approved": False,
    }
    record_raw = _canonical(record)
    index_raw = _canonical(
        {
            "schema_version": 2,
            "verdict": _GO_VERDICT,
            "entries": [{
                "path": "controller-final-decision.json",
                "sha256": hashlib.sha256(record_raw).hexdigest(),
                "size_bytes": len(record_raw),
            }],
        }
    )
    container_raw = _encode_controller_final_container(
        {
            "controller-final-decision.json": record_raw,
            "index.json": index_raw,
        }
    )
    output_authority = _strict_output_directory(output_dir)
    publication = _CreatedEvidenceFile(_CONTROLLER_FINAL_NAME)
    canonical_authority: _DescriptorAuthority | None = None
    retained: FinalPublicationAuthority | None = None
    link_attempted = False
    try:
        canonical_authority = _open_publication_tmpfile(
            output_authority, publication
        )
        if not publication.prove_closed():
            raise EvidenceIncomplete("temporary link descriptor cleanup is incomplete")
        retained = FinalPublicationAuthority(
            output_authority,
            canonical_authority,
            final_name=_CONTROLLER_FINAL_NAME,
            canonical_sha256=hashlib.sha256(container_raw).hexdigest(),
            canonical_size=len(container_raw),
            stable_metadata=(),
        )
        canonical_authority.call(lambda descriptor: os.fchmod(descriptor, 0o600))
        view = memoryview(container_raw)
        while view:
            written = canonical_authority.call(
                lambda descriptor: os.write(descriptor, view)
            )
            view = view[written:]
        canonical_authority.call(os.fsync)
        verified_raw, verified_metadata = _read_final_publication(
            canonical_authority,
            expected_link_count=0,
        )
        _verify_controller_final_container(verified_raw)
        _assert_cleanup_roots_absent(candidate_root, postgres_root)
        runtime_snapshot.revalidate()
        committed_raw, committed_metadata = _read_final_publication(
            canonical_authority,
            expected_link_count=0,
        )
        if (
            committed_raw != verified_raw
            or hashlib.sha256(committed_raw).hexdigest()
            != hashlib.sha256(verified_raw).hexdigest()
            or len(committed_raw) != len(verified_raw)
            or committed_metadata != verified_metadata
        ):
            raise EvidenceIncomplete("controller final bytes changed before commit")
        output_authority.validate_path()
        retained._stable_metadata = committed_metadata
        link_attempted = True
        _link_owned_tmpfile(
            canonical_authority.descriptor,
            output_authority.descriptor,
            _CONTROLLER_FINAL_NAME,
        )
        retained.publication_committed = True
        output_authority.descriptor_authority.call(os.fsync)
        _confirm_final_publication_identity(retained)
        return retained
    except BaseException as error:
        uncertain_generation = (
            canonical_authority is not None
            and canonical_authority.generation_uncertain
        ) or output_authority.descriptor_authority.generation_uncertain
        if retained is not None and (link_attempted or uncertain_generation):
            retained.publication_commit_uncertain = (
                link_attempted and not retained.publication_committed
            )
            retained.recovery_required = True
            raise FinalPublicationFailure(retained) from error
        file_closed = canonical_authority is None or canonical_authority.prove_closed()
        directory_closed = output_authority.prove_closed()
        if not file_closed or not directory_closed:
            raise EvidenceIncomplete(
                "controller final descriptor cleanup is incomplete"
            ) from error
        raise


def finalize_controller_evidence(
    *,
    runtime_bundle: Path,
    output_dir: Path,
    cleanup_path: Path,
    review_path: Path,
    diagnostic_index_path: Path,
    candidate_commit: str,
    candidate_tree: str,
    reviewed_base_commit: str,
    patch_algorithm: str,
    reviewed_patch_sha256: str,
    reviewed_patch_bytes: int,
    reviewed_paths: list[str] | tuple[str, ...],
    source_diff_sha256: str,
    expected_seal_manifest_sha256: str,
    review_verdict_sha256: str,
    diagnostic_index_sha256: str,
    runtime_bundle_index_sha256: str,
    cleanup_evidence_sha256: str,
    custodian_helper_binary_sha256: str,
    custodian_native_source_set_sha256: str,
    custodian_protocol_version: int,
    custodian_protocol_features: list[str] | tuple[str, ...],
    custodian_endpoint_authority: str,
    custodian_operations: list[str] | tuple[str, ...],
    custodian_stage_sha256: str,
    custodian_fixture_sha256: str,
    custodian_publications: list[str] | tuple[str, ...],
    verify_only: bool = False,
) -> FinalPublicationAuthority | None:
    runtime_snapshot = _open_runtime_evidence_snapshot(runtime_bundle)
    try:
        result = _finalize_controller_evidence(
            runtime_snapshot=runtime_snapshot,
            runtime_bundle=runtime_bundle,
            output_dir=output_dir,
            cleanup_path=cleanup_path,
            review_path=review_path,
            diagnostic_index_path=diagnostic_index_path,
            candidate_commit=candidate_commit,
            candidate_tree=candidate_tree,
            reviewed_base_commit=reviewed_base_commit,
            patch_algorithm=patch_algorithm,
            reviewed_patch_sha256=reviewed_patch_sha256,
            reviewed_patch_bytes=reviewed_patch_bytes,
            reviewed_paths=reviewed_paths,
            source_diff_sha256=source_diff_sha256,
            expected_seal_manifest_sha256=(
                expected_seal_manifest_sha256
            ),
            review_verdict_sha256=review_verdict_sha256,
            diagnostic_index_sha256=diagnostic_index_sha256,
            runtime_bundle_index_sha256=runtime_bundle_index_sha256,
            cleanup_evidence_sha256=cleanup_evidence_sha256,
            custodian_helper_binary_sha256=(
                custodian_helper_binary_sha256
            ),
            custodian_native_source_set_sha256=(
                custodian_native_source_set_sha256
            ),
            custodian_protocol_version=custodian_protocol_version,
            custodian_protocol_features=custodian_protocol_features,
            custodian_endpoint_authority=custodian_endpoint_authority,
            custodian_operations=custodian_operations,
            custodian_stage_sha256=custodian_stage_sha256,
            custodian_fixture_sha256=custodian_fixture_sha256,
            custodian_publications=custodian_publications,
            verify_only=verify_only,
        )
    except BaseException as error:
        if not runtime_snapshot.close():
            if isinstance(error, FinalPublicationFailure):
                error.authority.recovery_required = True
                raise
            raise EvidenceIncomplete(
                "runtime evidence descriptor cleanup is incomplete"
            ) from error
        raise
    if not runtime_snapshot.close():
        if result is not None:
            result.recovery_required = True
            raise FinalPublicationFailure(result)
        raise EvidenceIncomplete(
            "runtime evidence descriptor cleanup is incomplete"
        )
    return result


def write_runtime_evidence_bundle(
    capability: ValidatedPackage6Capability,
    child_authorities: RuntimeChildAuthorities,
    chain: RuntimeChainEvidence,
    cleanup: PostgresCleanupEvidence,
    *,
    source_root: Path,
    approval_bytes: bytes,
    postgres_approval_bytes: bytes,
) -> Path:
    if not is_issued_capability(capability) or cleanup not in _ISSUED_CLEANUP:
        raise TypeError("issued runtime and cleanup capabilities are required")
    documents: dict[str, bytes] = {}
    for relative in EVIDENCE_DOCUMENTS:
        raw = _safe_read(source_root, relative, strict_root=False)
        expected = dict(capability.source_bindings).get(relative)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise EvidenceIncomplete("evidence document does not match source binding")
        documents[Path(relative).name] = raw
    detail = _mapping(chain.api_detail["data"], "API detail data")
    worker_transcripts = _validate_stop_transcripts(
        chain.worker_stop,
        component="worker",
        max_output_bytes=capability.max_output_bytes,
    )
    job_api_transcripts = _validate_stop_transcripts(
        chain.job_api_stop,
        component="job_api",
        max_output_bytes=capability.max_output_bytes,
    )
    if len(set((*worker_transcripts, *job_api_transcripts))) != 4:
        raise EvidenceIncomplete("runtime transcript paths are not distinct")
    job = _mapping(detail["job"], "API detail job")
    events = _mapping_list(chain.database["events"], "database events")
    attempts = _mapping_list(chain.database["attempts"], "database attempts")
    artifacts = _mapping_list(chain.database["artifacts"], "database artifacts")
    sequences = [item["sequence"] for item in events]
    result_artifacts = [
        item for item in artifacts if item["artifact_type"] == "RESULT"
    ]
    if (
        type(chain.database["idempotent_job_count"]) is not int
        or type(chain.database["queue_depth"]) is not int
        or any(type(sequence) is not int for sequence in sequences)
        or type(job.get("attempt_count")) is not int
        or any(
            item.get("exit_code") is not None
            and type(item.get("exit_code")) is not int
            for item in attempts
        )
        or any(type(item.get("size_bytes")) is not int for item in artifacts)
    ):
        raise EvidenceIncomplete("runtime chain numeric evidence is invalid")
    if (
        job["state"] != "SUCCEEDED"
        or chain.database["idempotent_job_count"] != 1
        or chain.database["queue_depth"] != 0
        or not events
        or sequences != list(range(1, len(sequences) + 1))
        or len(sequences) != len(set(sequences))
        or not attempts
        or not chain.database["worker_heartbeats"]
        or len(result_artifacts) != 1
        or result_artifacts[0]["truncated"] is not False
        or result_artifacts[0]["sha256"] != job["result_hash"]
        or not result_artifacts[0]["validation_metadata"]
        or _mapping(
            result_artifacts[0]["validation_metadata"],
            "result validation metadata",
        ).get(
            "market_data_provenance"
        )
        != "DETERMINISTIC_PROVIDER_FREE_V1"
        or _mapping(
            result_artifacts[0]["validation_metadata"],
            "result validation metadata",
        ).get("fixture_sha256")
        != capability.fixture.sha256
        or not chain.worker_stop["cleanup_proven"]
        or not chain.job_api_stop["cleanup_proven"]
    ):
        raise EvidenceIncomplete("runtime chain evidence is incomplete or inconsistent")
    interpreter_path = Path(capability.operations["job-api.start"].argv[0])
    interpreter_info = interpreter_path.stat(follow_symlinks=False)
    interpreter_sha256 = hashlib.sha256(interpreter_path.read_bytes()).hexdigest()
    if (
        not stat.S_ISREG(interpreter_info.st_mode)
        or interpreter_sha256
        != capability.operations["job-api.start"].executable_sha256
    ):
        raise EvidenceIncomplete("interpreter identity changed before evidence sealing")
    terminal_metadata = _mapping(
        events[-1].get("metadata", {}), "terminal event metadata"
    )
    terminal_lineage = _mapping(
        terminal_metadata.get("lineage", {}), "terminal lineage"
    )
    command_lineage = _mapping(
        terminal_lineage.get("command", {}), "command lineage"
    )
    safety = _mapping(terminal_lineage.get("safety", {}), "safety lineage")
    safety_lineage = _mapping(safety.get("final", {}), "final safety lineage")
    approved_authorities = dict(
        package6_authority_digests(
            capability.staging_material, capability.fixture_material
        )
    )
    if approved_authorities != dict(capability.authority_digests):
        raise EvidenceIncomplete("observed runtime authority digests do not match approval")
    runtime = {
        "schema_version": 2,
        "verdict": VERDICT,
        "disposable_root": str(capability.disposable_root),
        "max_output_bytes": capability.max_output_bytes,
        "approval": {
            "sha256": capability.approval_sha256,
            "postgres_approval_sha256": capability.postgres.approval_sha256,
        },
        "source": {
            "commit": capability.source_commit,
            "tree": capability.source_tree,
            "bindings": list(capability.source_bindings),
        },
        "authority_digests": approved_authorities,
        "interpreter": {
            "argv0": str(interpreter_path),
            "sha256": interpreter_sha256,
            "device": interpreter_info.st_dev,
            "inode": interpreter_info.st_ino,
            "mode": stat.S_IMODE(interpreter_info.st_mode),
        },
        "operations": {
            key: {
                "argv": list(value.argv),
                "cwd": str(value.cwd),
                "host": value.bind_host,
                "port": str(value.port) if value.port is not None else None,
                "executable_sha256": value.executable_sha256,
            }
            for key, value in capability.operations.items()
        },
        "child_environments": {
            component: _mapping(
                _mapping(process, f"{component} process").get("environment"),
                f"{component} process environment",
            )
            for component, process in _mapping(
                chain.processes, "runtime processes"
            ).items()
        },
        "chain": asdict(chain),
        "postgres_cleanup": asdict(cleanup),
        "document_sha256": {
            name: hashlib.sha256(raw).hexdigest()
            for name, raw in sorted(documents.items())
        },
    }
    _validate_runtime_exact_integers(runtime)
    files = {
        "approval.json": approval_bytes,
        "postgres-approval.json": postgres_approval_bytes,
        "runtime.json": _canonical(runtime),
        **documents,
    }
    evidence_root = capability.evidence_root
    try:
        root_info = evidence_root.lstat()
        resolved_root = evidence_root.resolve(strict=True)
    except OSError as error:
        raise EvidenceIncomplete("pre-created evidence root is unavailable") from error
    if (
        resolved_root != evidence_root
        or stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) != 0o700
    ):
        raise EvidenceIncomplete("evidence root policy is invalid")
    bundle = evidence_root / f"package6-{uuid4().hex}.json"
    entries = [
        {
            "path": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
        for name, raw in sorted(files.items())
    ]
    files["index.json"] = _canonical(
        {"schema_version": 2, "verdict": VERDICT, "entries": entries}
    )
    return _publish_evidence_container(
        evidence_root,
        bundle,
        files,
        _verify_runtime_evidence_snapshot,
    )


def _require_exact_integer_fields(
    value: object,
    fields: tuple[str, ...],
) -> Mapping[str, object]:
    record = _mapping(value, "runtime numeric evidence")
    if any(type(record.get(field)) is not int for field in fields):
        raise EvidenceIncomplete("runtime numeric evidence is invalid")
    return record


def _runtime_integer_between(
    value: object,
    minimum: int,
    maximum: int | None = None,
) -> bool:
    return (
        type(value) is int
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _runtime_exit_code(value: object, *, nullable: bool = False) -> bool:
    return (nullable and value is None) or _runtime_integer_between(
        value, -255, 255
    )


def _validate_runtime_operation_bindings(
    runtime: Mapping[str, object],
    approval: Mapping[str, object],
) -> None:
    operations = _mapping(runtime.get("operations"), "runtime operations")
    approved_operations = _mapping_list(
        approval.get("operations"), "approved runtime operations"
    )
    expected_ids = {
        "job-api.start",
        "job-api.stop",
        "worker.start",
        "worker.stop",
    }
    approved_by_id = {
        operation.get("operation_id"): operation
        for operation in approved_operations
    }
    if (
        set(operations) != expected_ids
        or set(approved_by_id) != expected_ids
        or len(approved_operations) != len(expected_ids)
    ):
        raise EvidenceIncomplete("runtime operation authority is invalid")
    for operation_id in sorted(expected_ids):
        observed = _mapping(
            operations.get(operation_id), "runtime operation"
        )
        approved = approved_by_id[operation_id]
        if (
            set(observed)
            != {"argv", "cwd", "host", "port", "executable_sha256"}
            or observed["argv"] != approved.get("argv")
            or observed["cwd"] != approved.get("cwd")
            or observed["host"] != approved.get("bind_host")
            or observed["port"] != approved.get("port")
            or observed["executable_sha256"]
            != approved.get("executable_sha256")
        ):
            raise EvidenceIncomplete("runtime operation authority is invalid")
    for operation_id in ("job-api.start", "job-api.stop"):
        operation = _mapping(
            operations[operation_id], "runtime listener operation"
        )
        if (
            operation["host"] != "127.0.0.1"
            or operation["port"] != str(PACKAGE6_JOB_API_PORT)
        ):
            raise EvidenceIncomplete("runtime listener authority is invalid")
    for operation_id in ("worker.start", "worker.stop"):
        operation = _mapping(
            operations[operation_id], "runtime worker operation"
        )
        if operation["host"] is not None or operation["port"] is not None:
            raise EvidenceIncomplete("runtime worker authority is invalid")


def _canonical_job_projection(
    value: object,
) -> tuple[object, ...]:
    if not isinstance(value, dict) or any(
        field not in value for field in _CANONICAL_JOB_FIELDS
    ):
        raise EvidenceIncomplete(_SEMANTIC_REJECTION)
    projection = tuple(value[field] for field in _CANONICAL_JOB_FIELDS)
    job_id, state, attempt_count, reason_code, result_hash = projection
    if (
        not isinstance(job_id, str)
        or not job_id
        or not isinstance(state, str)
        or not state
        or type(attempt_count) is not int
        or attempt_count != 1
        or not (
            reason_code is None
            or isinstance(reason_code, str) and bool(reason_code)
        )
        or not isinstance(result_hash, str)
        or _HEX64.fullmatch(result_hash) is None
    ):
        raise EvidenceIncomplete(_SEMANTIC_REJECTION)
    return projection


def _validate_runtime_semantic_projection(
    runtime: Mapping[str, object],
) -> None:
    try:
        chain = _mapping(runtime.get("chain"), "runtime chain")
        database = _mapping(chain.get("database"), "runtime database")
        detail = _mapping(chain.get("api_detail"), "runtime API detail")
        detail_data = _mapping(detail.get("data"), "runtime API detail data")
        first = _mapping(chain.get("first_request"), "runtime first request")
        first_body = _mapping(first.get("body"), "runtime first response")
        first_data = _mapping(first_body.get("data"), "runtime first response data")
        duplicate = _mapping(
            chain.get("duplicate_request"),
            "runtime duplicate request",
        )
        duplicate_body = _mapping(
            duplicate.get("body"),
            "runtime duplicate response",
        )
        duplicate_data = _mapping(
            duplicate_body.get("data"),
            "runtime duplicate response data",
        )
        api_list = _mapping(chain.get("api_list"), "runtime API list")
        api_list_data = _mapping(
            api_list.get("data"),
            "runtime API list data",
        )
        list_items = _mapping_list(
            api_list_data.get("items"),
            "runtime API list items",
        )
        if len(list_items) != 1:
            raise EvidenceIncomplete(_SEMANTIC_REJECTION)
        representations = (
            database.get("job"),
            detail_data.get("job"),
            first_data.get("job"),
            duplicate_data.get("job"),
            list_items[0],
            chain.get("dashboard_status"),
        )
        projections = tuple(
            _canonical_job_projection(record)
            for record in representations
        )
        if len(set(projections)) != 1:
            raise EvidenceIncomplete(_SEMANTIC_REJECTION)
        dashboard = _mapping(
            chain.get("dashboard_status"),
            "runtime dashboard status",
        )
        if set(dashboard) != set(_CANONICAL_JOB_FIELDS):
            raise EvidenceIncomplete(_SEMANTIC_REJECTION)

        attempts = _mapping_list(
            database.get("attempts"),
            "runtime attempts",
        )
        events = _mapping_list(database.get("events"), "runtime events")
        artifacts = _mapping_list(
            database.get("artifacts"),
            "runtime artifacts",
        )
        if len(attempts) != 1 or len(events) != 4 or len(artifacts) != 1:
            raise EvidenceIncomplete(_SEMANTIC_REJECTION)
        attempt = attempts[0]
        if set(attempt) != _ATTEMPT_FIELDS:
            raise EvidenceIncomplete(_SEMANTIC_REJECTION)
        attempt_id = attempt["attempt_id"]
        timestamp_fields = (
            "claimed_at",
            "started_at",
            "finished_at",
            "heartbeat_at",
            "lease_expires_at",
        )
        if (
            not isinstance(attempt_id, str)
            or not attempt_id
            or attempt["outcome"] != "SUCCEEDED"
            or attempt["exit_code"] != 0
            or attempt["termination_reason"] is not None
            or any(
                not isinstance(attempt[field], str)
                or not attempt[field]
                for field in timestamp_fields
            )
        ):
            raise EvidenceIncomplete(_SEMANTIC_REJECTION)

        expected_states = ("QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED")
        previous_state: str | None = None
        for sequence, (event, expected_state) in enumerate(
            zip(events, expected_states, strict=True),
            start=1,
        ):
            if (
                set(event) != _EVENT_FIELDS
                or event["sequence"] != sequence
                or event["from_state"] != previous_state
                or event["to_state"] != expected_state
                or not (
                    event["reason_code"] is None
                    or isinstance(event["reason_code"], str)
                    and bool(event["reason_code"])
                )
                or event["attempt_id"]
                != (None if sequence == 1 else attempt_id)
                or not isinstance(event["metadata"], dict)
            ):
                raise EvidenceIncomplete(_SEMANTIC_REJECTION)
            previous_state = expected_state

        artifact = artifacts[0]
        validation_metadata = artifact.get("validation_metadata")
        if (
            set(artifact) != _ARTIFACT_FIELDS
            or not isinstance(artifact["artifact_id"], str)
            or not artifact["artifact_id"]
            or artifact["attempt_id"] != attempt_id
            or artifact["artifact_type"] != "RESULT"
            or not isinstance(artifact["relative_ref"], str)
            or not artifact["relative_ref"]
            or not isinstance(artifact["validator_id"], str)
            or not artifact["validator_id"]
            or not isinstance(artifact["sha256"], str)
            or _HEX64.fullmatch(artifact["sha256"]) is None
            or type(artifact["size_bytes"]) is not int
            or artifact["size_bytes"] < 0
            or not isinstance(artifact["media_type"], str)
            or not artifact["media_type"]
            or artifact["truncated"] is not False
            or not isinstance(validation_metadata, dict)
            or set(validation_metadata)
            != {"market_data_provenance", "fixture_sha256"}
            or validation_metadata["market_data_provenance"]
            != "DETERMINISTIC_PROVIDER_FREE_V1"
            or not isinstance(validation_metadata["fixture_sha256"], str)
            or _HEX64.fullmatch(validation_metadata["fixture_sha256"]) is None
        ):
            raise EvidenceIncomplete(_SEMANTIC_REJECTION)
    except (KeyError, TypeError, ValueError, EvidenceIncomplete) as error:
        raise EvidenceIncomplete(_SEMANTIC_REJECTION) from error


def _validate_runtime_exact_integers(runtime: Mapping[str, object]) -> None:
    root = _require_exact_integer_fields(
        runtime, ("schema_version", "max_output_bytes")
    )
    interpreter = _require_exact_integer_fields(
        runtime.get("interpreter"),
        ("device", "inode", "mode"),
    )
    if (
        root["schema_version"] != 2
        or not _runtime_integer_between(
            root["max_output_bytes"], 1, _MAX_SIGNED_64
        )
        or not _runtime_integer_between(
            interpreter["device"], 0, _MAX_UNSIGNED_64
        )
        or not _runtime_integer_between(
            interpreter["inode"], 1, _MAX_UNSIGNED_64
        )
        or not _runtime_integer_between(interpreter["mode"], 0, 0o7777)
        or interpreter["mode"] & 0o111 == 0
        or interpreter["mode"] & 0o022
    ):
        raise EvidenceIncomplete("runtime numeric evidence is invalid")
    max_output_bytes = runtime["max_output_bytes"]
    operations = _mapping(runtime.get("operations"), "runtime operations")
    for operation in operations.values():
        record = _mapping(operation, "runtime operation")
        port = record.get("port")
        host = record.get("host")
        if (
            port is not None
            and (
                not isinstance(port, str)
                or port != str(PACKAGE6_JOB_API_PORT)
            )
        ) or ((host is None) != (port is None)):
            raise EvidenceIncomplete("runtime numeric evidence is invalid")

    chain = _mapping(runtime.get("chain"), "runtime chain")
    processes = _mapping(chain.get("processes"), "runtime processes")
    process_records: dict[str, Mapping[str, object]] = {}
    for component in ("job_api", "worker"):
        process = _mapping(
            processes.get(component), "runtime native process"
        )
        environment = _mapping(
            process.get("environment"), "runtime child environment"
        )
        if (
            set(process)
            != {
                "operation_id",
                "component",
                "native_operation_id",
                "recovery_token",
                "state",
                "environment",
            }
            or set(environment)
            != {"component", "operation_id", "keys"}
            or process["component"] != component.upper()
            or environment["component"] != component.upper()
            or process["operation_id"]
            != ("job-api.start" if component == "job_api" else "worker.start")
            or environment["operation_id"] != process["operation_id"]
            or not isinstance(process["native_operation_id"], str)
            or re.fullmatch(
                r"[0-9a-f]{32}", process["native_operation_id"]
            )
            is None
            or process["native_operation_id"] == "0" * 32
            or not isinstance(process["recovery_token"], str)
            or re.fullmatch(r"[0-9a-f]{32}", process["recovery_token"])
            is None
            or process["recovery_token"] == "0" * 32
            or process["state"]
            not in {"RUNNING", "RESULT_RETAINED", "RECOVERY_REQUIRED"}
            or type(environment["keys"]) not in (list, tuple)
            or any(
                not isinstance(key, str) or not key
                for key in environment["keys"]
            )
        ):
            raise EvidenceIncomplete("runtime native process evidence is invalid")
        process_records[component] = process
    child_environments = _mapping(
        runtime.get("child_environments"),
        "runtime child environments",
    )
    for component in ("job_api", "worker"):
        environment = _mapping(
            child_environments.get(component),
            "runtime child environment",
        )
        if environment != process_records[component]["environment"]:
            raise EvidenceIncomplete(
                "runtime child environment evidence is invalid"
            )

    readiness = _mapping(
        chain.get("readiness"), "runtime readiness evidence"
    )
    if (
        set(readiness)
        != {"operation_id", "native_operation_id", "attempts", "status"}
        or readiness["operation_id"] != "job-api.start"
        or readiness["native_operation_id"]
        != process_records["job_api"]["native_operation_id"]
        or readiness["status"] != "READY"
        or not _runtime_integer_between(
            readiness["attempts"], 1, _MAX_SIGNED_64
        )
    ):
        raise EvidenceIncomplete("runtime numeric evidence is invalid")
    for stop_name, component in (
        ("worker_stop", "worker"),
        ("job_api_stop", "job_api"),
    ):
        stop = _mapping(chain.get(stop_name), "runtime native STOP")
        _validate_stop_transcripts(
            stop,
            component=component,
            max_output_bytes=max_output_bytes,
        )
        if (
            stop["native_operation_id"]
            != process_records[component]["native_operation_id"]
        ):
            raise EvidenceIncomplete(
                "runtime native operation identity does not match"
            )

    database = _require_exact_integer_fields(
        chain.get("database"),
        ("queue_depth", "idempotent_job_count"),
    )
    if (
        not _runtime_integer_between(
            database["queue_depth"], 0, _MAX_SIGNED_64
        )
        or not _runtime_integer_between(
            database["idempotent_job_count"], 0, _MAX_SIGNED_64
        )
    ):
        raise EvidenceIncomplete("runtime numeric evidence is invalid")
    for event in _mapping_list(database.get("events"), "runtime events"):
        numeric_event = _require_exact_integer_fields(event, ("sequence",))
        if not _runtime_integer_between(
            numeric_event["sequence"], 1, _MAX_SIGNED_64
        ):
            raise EvidenceIncomplete("runtime numeric evidence is invalid")
    attempts = _mapping_list(database.get("attempts"), "runtime attempts")
    for attempt in attempts:
        if not _runtime_exit_code(attempt.get("exit_code"), nullable=True):
            raise EvidenceIncomplete("runtime numeric evidence is invalid")
        if (
            attempt.get("outcome") == "SUCCEEDED"
            and attempt.get("exit_code") != 0
        ):
            raise EvidenceIncomplete("runtime successful attempt exit is invalid")
    for artifact in _mapping_list(database.get("artifacts"), "runtime artifacts"):
        numeric_artifact = _require_exact_integer_fields(
            artifact, ("size_bytes",)
        )
        if not _runtime_integer_between(
            numeric_artifact["size_bytes"], 0, _MAX_SIGNED_64
        ):
            raise EvidenceIncomplete("runtime numeric evidence is invalid")

    jobs = [
        _mapping(database.get("job"), "runtime database job"),
        _mapping(chain.get("dashboard_status"), "runtime dashboard status"),
    ]
    for response_name in ("first_request", "duplicate_request"):
        response = _require_exact_integer_fields(
            chain.get(response_name),
            ("status",),
        )
        if not _runtime_integer_between(response["status"], 100, 599):
            raise EvidenceIncomplete("runtime numeric evidence is invalid")
        body = _mapping(response.get("body"), "runtime response body")
        data = _mapping(body.get("data"), "runtime response data")
        jobs.append(_mapping(data.get("job"), "runtime response job"))
    detail = _mapping(chain.get("api_detail"), "runtime API detail")
    jobs.append(
        _mapping(
            _mapping(detail.get("data"), "runtime API detail data").get("job"),
            "runtime API detail job",
        )
    )
    api_list = _mapping(chain.get("api_list"), "runtime API list")
    api_list_data = _mapping(api_list.get("data"), "runtime API list data")
    jobs.extend(_mapping_list(api_list_data.get("items"), "runtime API list items"))
    for job in jobs:
        numeric_job = _require_exact_integer_fields(job, ("attempt_count",))
        if not _runtime_integer_between(
            numeric_job["attempt_count"], 0, _MAX_SIGNED_64
        ) or numeric_job["attempt_count"] != len(attempts):
            raise EvidenceIncomplete("runtime numeric evidence is invalid")

    postgres_cleanup = _require_exact_integer_fields(
        runtime.get("postgres_cleanup"),
        (
            "listener_negative_probes",
            "process_pid",
            "process_group",
            "start_ticks",
            "exit_code",
        ),
    )
    if (
        postgres_cleanup["listener_negative_probes"] != 3
        or not _runtime_integer_between(
            postgres_cleanup["process_pid"], 2, _MAX_PID
        )
        or postgres_cleanup["process_group"]
        != postgres_cleanup["process_pid"]
        or not _runtime_integer_between(
            postgres_cleanup["start_ticks"], 1, _MAX_UNSIGNED_64
        )
        or not _runtime_exit_code(postgres_cleanup["exit_code"])
    ):
        raise EvidenceIncomplete("runtime numeric evidence is invalid")


def _verify_runtime_evidence_snapshot(
    snapshot: Mapping[str, bytes],
) -> bool:
    index = json.loads(_snapshot_read(snapshot, "index.json"))
    if (
        not isinstance(index, dict)
        or set(index) != {"schema_version", "verdict", "entries"}
        or type(index["schema_version"]) is not int
        or index["schema_version"] != 2
        or index["verdict"] != VERDICT
        or not isinstance(index["entries"], list)
    ):
        raise EvidenceIncomplete("runtime evidence index schema is invalid")
    names = {entry.get("path") for entry in index["entries"] if isinstance(entry, dict)}
    required = {
        "approval.json",
        "postgres-approval.json",
        "runtime.json",
        *(Path(path).name for path in EVIDENCE_DOCUMENTS),
    }
    if names != required or len(names) != len(index["entries"]):
        raise EvidenceIncomplete("runtime evidence manifest is incomplete")
    for entry in index["entries"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256", "size_bytes"}
            or type(entry["size_bytes"]) is not int
            or not 0 <= entry["size_bytes"] <= _MAX_SIGNED_64
        ):
            raise EvidenceIncomplete("runtime evidence entry fields are invalid")
        raw = _snapshot_read(snapshot, entry["path"])
        if (
            len(raw) != entry["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != entry["sha256"]
        ):
            raise EvidenceIncomplete("runtime evidence digest or size does not match")
    runtime = json.loads(_snapshot_read(snapshot, "runtime.json"))
    expected_fields = {
        "schema_version",
        "verdict",
        "disposable_root",
        "max_output_bytes",
        "approval",
        "source",
        "authority_digests",
        "interpreter",
        "operations",
        "child_environments",
        "chain",
        "postgres_cleanup",
        "document_sha256",
    }
    chain_fields = {
        "processes",
        "readiness",
        "first_request",
        "duplicate_request",
        "api_list",
        "api_detail",
        "database",
        "dashboard_status",
        "worker_stop",
        "job_api_stop",
        "native_publications",
    }
    database_fields = {
        "job",
        "events",
        "attempts",
        "artifacts",
        "worker_heartbeats",
        "queue_depth",
        "idempotent_job_count",
        "postgres_approval_sha256",
    }
    if (
        not isinstance(runtime, dict)
        or set(runtime) != expected_fields
        or type(runtime["schema_version"]) is not int
        or runtime["schema_version"] != 2
    ):
        raise EvidenceIncomplete("runtime evidence authority or cleanup is invalid")
    _validate_runtime_exact_integers(runtime)
    _validate_runtime_semantic_projection(runtime)
    if (
        runtime["verdict"] != VERDICT
        or set(runtime["authority_digests"])
        != {
            "release",
            "application",
            "backend",
            "command",
            "semantic",
            "fixture",
            "safety",
            "stage",
        }
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or not set(value) <= _SHA256
            for value in runtime["authority_digests"].values()
        )
        or set(runtime["chain"]) != chain_fields
        or set(runtime["chain"]["database"]) != database_fields
        or set(runtime["interpreter"])
        != {"argv0", "sha256", "device", "inode", "mode"}
        or runtime["operations"]["job-api.start"]["executable_sha256"]
        != runtime["interpreter"]["sha256"]
        or hashlib.sha256(_snapshot_read(snapshot, "approval.json")).hexdigest()
        != runtime["approval"]["sha256"]
        or hashlib.sha256(
            _snapshot_read(snapshot, "postgres-approval.json")
        ).hexdigest()
        != runtime["approval"]["postgres_approval_sha256"]
        or runtime["approval"]["postgres_approval_sha256"]
        != runtime["chain"]["database"]["postgres_approval_sha256"]
        or runtime["postgres_cleanup"]["cleanup_complete"] is not True
        or runtime["postgres_cleanup"]["listener_alive"] is not False
        or runtime["postgres_cleanup"]["process_alive"] is not False
        or runtime["postgres_cleanup"]["process_group_alive"] is not False
        or runtime["postgres_cleanup"]["listener_negative_probes"] < 3
        or runtime["postgres_cleanup"]["start_ticks"] < 1
        or runtime["postgres_cleanup"]["exit_code"] != 0
        or runtime["postgres_cleanup"]["pgdata_exists"] is not False
        or runtime["chain"]["database"]["idempotent_job_count"] != 1
        or runtime["chain"]["database"]["queue_depth"] != 0
        or runtime["chain"]["worker_stop"]["cleanup_proven"] is not True
        or runtime["chain"]["job_api_stop"]["cleanup_proven"] is not True
        or runtime["chain"]["processes"]["job_api"][
            "native_operation_id"
        ]
        != runtime["chain"]["job_api_stop"]["native_operation_id"]
        or runtime["chain"]["processes"]["worker"][
            "native_operation_id"
        ]
        != runtime["chain"]["worker_stop"]["native_operation_id"]
        or runtime["chain"]["readiness"]["native_operation_id"]
        != runtime["chain"]["processes"]["job_api"][
            "native_operation_id"
        ]
        or runtime["chain"]["readiness"]["status"] != "READY"
    ):
        raise EvidenceIncomplete("runtime evidence authority or cleanup is invalid")
    disposable_root = Path(runtime["disposable_root"])
    if (
        not disposable_root.is_absolute()
        or disposable_root != Path(os.path.normpath(disposable_root))
        or type(runtime["max_output_bytes"]) is not int
        or runtime["max_output_bytes"] < 1
    ):
        raise EvidenceIncomplete("runtime transcript authority is invalid")
    worker_transcripts = _validate_stop_transcripts(
        runtime["chain"]["worker_stop"],
        component="worker",
        max_output_bytes=runtime["max_output_bytes"],
    )
    job_api_transcripts = _validate_stop_transcripts(
        runtime["chain"]["job_api_stop"],
        component="job_api",
        max_output_bytes=runtime["max_output_bytes"],
    )
    if len(set((*worker_transcripts, *job_api_transcripts))) != 4:
        raise EvidenceIncomplete("runtime transcript paths are not distinct")
    result_artifacts = [
        artifact
        for artifact in runtime["chain"]["database"]["artifacts"]
        if artifact.get("artifact_type") == "RESULT"
    ]
    events = runtime["chain"]["database"]["events"]
    sequences = [event.get("sequence") for event in events]
    job = runtime["chain"]["database"]["job"]
    detail_job = runtime["chain"]["api_detail"]["data"]["job"]
    first_job = runtime["chain"]["first_request"]["body"]["data"]["job"]
    duplicate_job = runtime["chain"]["duplicate_request"]["body"]["data"]["job"]
    attempts = runtime["chain"]["database"]["attempts"]
    attempt_ids = [attempt.get("attempt_id") for attempt in attempts]
    event_attempt_ids = {
        event.get("attempt_id")
        for event in events
        if event.get("attempt_id") is not None
    }
    if not events:
        raise EvidenceIncomplete("runtime event history is missing")
    terminal_lineage = events[-1].get("metadata", {}).get("lineage", {})
    command_lineage = terminal_lineage.get("command", {})
    safety_lineage = terminal_lineage.get("safety", {}).get("final", {})
    approval = json.loads(_snapshot_read(snapshot, "approval.json"))
    approval_record = _mapping(approval, "runtime approval")
    custodian_authority = _mapping(
        approval_record.get("custodian_authority"),
        "runtime custodian authority",
    )
    custodian_fixture = _mapping(
        custodian_authority.get("fixture_identity"),
        "runtime custodian fixture",
    )
    publication_records = _mapping(
        _mapping(runtime["chain"], "runtime chain").get(
            "native_publications"
        ),
        "runtime native publications",
    )
    _validate_closure_custodian_authority(
        approval=approval_record,
        runtime=runtime,
        candidate_commit=runtime["source"]["commit"],
        candidate_tree=runtime["source"]["tree"],
        custodian_helper_binary_sha256=custodian_authority.get(
            "helper_binary_sha256"
        ),
        custodian_native_source_set_sha256=custodian_authority.get(
            "native_source_set_sha256"
        ),
        custodian_protocol_version=custodian_authority.get(
            "protocol_version"
        ),
        custodian_protocol_features=custodian_authority.get(
            "protocol_features"
        ),
        custodian_endpoint_authority=custodian_authority.get(
            "endpoint_authority"
        ),
        custodian_operations=custodian_authority.get("operations"),
        custodian_stage_sha256=custodian_authority.get("stage_sha256"),
        custodian_fixture_sha256=custodian_fixture.get("sha256"),
        custodian_publications=[
            f"{component}="
            f"{_mapping(publication_records.get(component), 'runtime native publication').get('manifest_sha256')}"
            for component in ("job_api", "worker")
        ],
    )
    _validate_runtime_operation_bindings(
        runtime,
        approval_record,
    )
    expected_environment = child_environment_key_sets()
    observed_environments = runtime["child_environments"]
    process_components = {"job_api": "JOB_API", "worker": "WORKER"}
    environment_valid = (
        isinstance(observed_environments, dict)
        and set(observed_environments) == set(process_components)
    )
    if environment_valid:
        for component, component_name in process_components.items():
            observed = observed_environments[component]
            process = runtime["chain"]["processes"][component]
            environment_valid = (
                isinstance(observed, dict)
                and set(observed)
                == {"component", "operation_id", "keys"}
                and observed["component"] == component_name
                and observed["operation_id"] == f"{component.replace('_', '-')}.start"
                and observed["keys"] == list(expected_environment[component])
                and process.get("environment") == observed
            )
            if not environment_valid:
                break
    if (
        len(result_artifacts) != 1
        or result_artifacts[0].get("truncated") is not False
        or not result_artifacts[0].get("relative_ref")
        or not result_artifacts[0].get("validator_id")
        or not result_artifacts[0].get("validation_metadata")
        or result_artifacts[0]["validation_metadata"].get(
            "market_data_provenance"
        )
        != "DETERMINISTIC_PROVIDER_FREE_V1"
        or result_artifacts[0]["validation_metadata"].get("fixture_sha256")
        != runtime["authority_digests"]["fixture"]
        or result_artifacts[0].get("sha256") != job.get("result_hash")
        or job.get("state") != "SUCCEEDED"
        or detail_job.get("state") != job.get("state")
        or len(
            {
                first_job.get("job_id"),
                duplicate_job.get("job_id"),
                detail_job.get("job_id"),
                job.get("job_id"),
            }
        )
        != 1
        or sequences != list(range(1, len(sequences) + 1))
        or len(sequences) != len(set(sequences))
        or not runtime["chain"]["database"]["attempts"]
        or not runtime["chain"]["database"]["worker_heartbeats"]
        or any(
            not isinstance(attempt_id, str) or not attempt_id
            for attempt_id in attempt_ids
        )
        or len(attempt_ids) != len(set(attempt_ids))
        or event_attempt_ids != set(attempt_ids)
        or events[-1].get("attempt_id") != attempt_ids[-1]
        or result_artifacts[0].get("attempt_id") != attempt_ids[-1]
        or [event.get("to_state") for event in events]
        != ["QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED"]
        or any(
            attempt.get("claimed_at") is None
            or attempt.get("started_at") is None
            or attempt.get("heartbeat_at") is None
            or attempt.get("finished_at") is None
            or attempt.get("lease_expires_at") is None
            or attempt.get("outcome") != "SUCCEEDED"
            or attempt.get("termination_reason") is not None
            for attempt in runtime["chain"]["database"]["attempts"]
        )
        or job.get("lease_owner") is not None
        or job.get("lease_expires_at") is not None
        or job.get("cancel_requested_at") is not None
        or len(
            [
                item
                for item in runtime["chain"]["api_list"]["data"]["items"]
                if item.get("job_id") == job.get("job_id")
            ]
        )
        != 1
        or runtime["chain"]["dashboard_status"]
        != {
            key: detail_job[key]
            for key in ("job_id", "state", "attempt_count", "reason_code", "result_hash")
        }
        or runtime["authority_digests"] != approval.get("authority_digests")
        or not environment_valid
    ):
        raise EvidenceIncomplete("runtime state, event, or sealed result proof is invalid")
    for name, digest in runtime["document_sha256"].items():
        if hashlib.sha256(_snapshot_read(snapshot, name)).hexdigest() != digest:
            raise EvidenceIncomplete("evidence document digest does not match")
    return True


def verify_runtime_evidence_bundle(root: Path) -> bool:
    """Verify one descriptor-read regular-file Package 6 evidence container."""

    return _verify_runtime_evidence_snapshot(
        _load_runtime_evidence_snapshot(root)
    )


__all__ = [
    "EVIDENCE_DOCUMENTS",
    "FinalPublicationAuthority",
    "FinalPublicationFailure",
    "PostgresCleanupEvidence",
    "child_environment_key_sets",
    "finalize_controller_evidence",
    "issue_postgres_cleanup_evidence",
    "request_and_wait_for_postgres_cleanup",
    "verify_runtime_evidence_bundle",
    "write_runtime_evidence_bundle",
]
