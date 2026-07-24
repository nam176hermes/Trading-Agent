"""Canonical, create-only provisioning documents for Phase 4B."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from .semantic import semantic_policy_digest


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_FORBIDDEN_KEYS = frozenset({
    "token", "password", "credential", "database_url", "environment", "env",
})


class ProvisioningDocumentError(RuntimeError):
    """Sanitized fail-closed provisioning error."""

    def __init__(self) -> None:
        super().__init__("phase 4b provisioning document rejected")


def canonical_document_bytes(document: dict[str, Any]) -> bytes:
    try:
        def validate(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str) or key.lower() in _FORBIDDEN_KEYS:
                        raise ValueError
                    validate(child)
            elif isinstance(value, list):
                for child in value:
                    validate(child)
        validate(document)
        return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    except Exception:
        raise ProvisioningDocumentError() from None


def publish_canonical_document(
    document: dict[str, Any], output: Path, *, apply: bool = False,
    expected_uid: int = 0, expected_gid: int = 0,
) -> str:
    raw = canonical_document_bytes(document)
    digest = hashlib.sha256(raw).hexdigest()
    if not apply:
        return digest
    output = Path(output)
    if not output.is_absolute() or ".." in output.parts:
        raise ProvisioningDocumentError()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = temp_fd = None
    temporary = f".{output.name}.{secrets.token_hex(12)}.tmp"
    try:
        parent_fd = os.open(output.parent, directory_flags)
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != expected_uid or parent.st_gid != expected_gid
            or parent.st_mode & (0o022 | 0o7000)
        ):
            raise ValueError
        temp_fd = os.open(temporary, file_flags, 0o600, dir_fd=parent_fd)
        remaining = memoryview(raw)
        while remaining:
            written = os.write(temp_fd, remaining)
            if written <= 0:
                raise ValueError
            remaining = remaining[written:]
        os.fsync(temp_fd)
        os.fchmod(temp_fd, 0o444)
        os.close(temp_fd)
        temp_fd = None
        os.link(temporary, output.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return digest
    except Exception:
        if temp_fd is not None:
            os.close(temp_fd)
        if parent_fd is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
        raise ProvisioningDocumentError() from None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def read_canonical_document_file(path: Path, expected_digest: str) -> bytes:
    """Read one bounded regular canonical JSON document without following links."""

    descriptor = None
    try:
        if _DIGEST.fullmatch(expected_digest) is None:
            raise ValueError
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
            raise ValueError
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 65536):
            total += len(chunk)
            if total > 1024 * 1024:
                raise ValueError
            chunks.append(chunk)
        raw = b"".join(chunks)
        document = json.loads(raw)
        if not isinstance(document, dict) or raw != canonical_document_bytes(document):
            raise ValueError
        if hashlib.sha256(raw).hexdigest() != expected_digest:
            raise ValueError
        return raw
    except Exception:
        raise ProvisioningDocumentError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def build_command_manifest_document(authority: object) -> dict[str, object]:
    try:
        # One code-owned source of equality; importing lazily avoids a package
        # initialization cycle in worker startup.
        from services.job_worker.command_registry import _expected_command_document
        return _expected_command_document(authority)  # type: ignore[arg-type]
    except Exception:
        raise ProvisioningDocumentError() from None


def _release_document(release: object) -> dict[str, object]:
    return {
        "git_commit": str(release.git_commit),
        "release_root": str(release.release_root),
        "manifest_path": str(release.manifest_path),
        "manifest_sha256": str(release.manifest_sha256),
        "python_path": str(release.python_path),
        "python_identity": str(release.python_identity),
    }


def build_runtime_authority_document(
    *, application: object, backend: object, command_manifest_path: Path,
    command_manifest_sha256: str, semantic_authority_path: Path,
    safety_snapshot_path: Path,
    exporter_commit: str, safety_source_fingerprint: str, runtime_uid: int,
) -> dict[str, object]:
    try:
        for release, kind in ((application, "app"), (backend, "backend")):
            commit = str(release.git_commit)
            root = Path(f"/opt/trading-agent-phase4/releases/{kind}-{commit}")
            manifest = Path(f"/opt/trading-agent-phase4/manifests/{kind}-{commit}.manifest.json")
            if (
                _COMMIT.fullmatch(commit) is None
                or Path(release.release_root) != root
                or Path(release.manifest_path) != manifest
                or Path(release.python_path) != root / ".venv/bin/python3.11"
                or _DIGEST.fullmatch(str(release.manifest_sha256)) is None
                or not re.fullmatch(r"CPython 3\.11\.\d+", str(release.python_identity))
            ):
                raise ValueError
        backend_commit = str(backend.git_commit)
        if command_manifest_path != Path(
            f"/opt/trading-agent-phase4/manifests/commands-{backend_commit}.json"
        ):
            raise ValueError
        if semantic_authority_path != Path(
            "/etc/trading-agent/research-input-manifests/phase4-v1.json"
        ):
            raise ValueError
        if not isinstance(runtime_uid, int) or isinstance(runtime_uid, bool) or runtime_uid <= 0:
            raise ValueError
        if safety_snapshot_path != Path(f"/run/user/{runtime_uid}/trading-agent/safety-state.json"):
            raise ValueError
        if exporter_commit != str(application.git_commit):
            raise ValueError
        semantic_policy_sha256 = semantic_policy_digest(str(backend.git_commit), semantic_authority_path)
        for value in (command_manifest_sha256, semantic_policy_sha256, safety_source_fingerprint):
            if _DIGEST.fullmatch(value) is None:
                raise ValueError
        return {
            "manifest_version": 1,
            "application": _release_document(application),
            "backend": _release_document(backend),
            "command_manifest": {"path": str(command_manifest_path), "sha256": command_manifest_sha256},
            "semantic": {"authority_path": str(semantic_authority_path), "policy_sha256": semantic_policy_sha256},
            "safety": {
                "exporter_commit": exporter_commit,
                "snapshot_path": str(safety_snapshot_path),
                "source_fingerprint": safety_source_fingerprint,
            },
        }
    except Exception:
        raise ProvisioningDocumentError() from None


__all__ = [
    "ProvisioningDocumentError", "build_command_manifest_document",
    "build_runtime_authority_document", "canonical_document_bytes",
    "publish_canonical_document",
    "read_canonical_document_file",
]
