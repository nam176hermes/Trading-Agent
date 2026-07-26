"""Release Authority v2 static-stage and activation documents.

V2 is deliberately separate from the immutable Phase 4B/v1 schema.  A static
document attests one sealed build from one canonical Git object.  A later,
create-only activation document binds that static digest to fresh safety and
semantic evidence.  Building a static document never creates an activation.

This module uses only the standard library so the builder can run it in an
isolated interpreter before the stage is sealed.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Mapping, Sequence, cast


SCHEMA_VERSION = 3
STATIC_KIND = "STATIC_RELEASE"
SEAL_VERSION = 3
EXPECTED_DATABASE_REVISION = "0006_job_transition_database_authority"
EXPECTED_DB_ROLES = {
    "api_role": "trading_job_api",
    "worker_role": "trading_job_worker",
}
JOB_PLANE_POLICY = {
    "allowed_job_types": ["SNAPSHOT"],
    "scheduler_timer_enabled": False,
    "worker_concurrency": 1,
    "worker_lease_seconds": 600,
}
COMPONENT_PREFIXES = {
    "application": ".",
    "backend": ".",
}
COMPONENT_ARTIFACT_ROOTS = {
    "application": "application",
    "backend": "backend",
}
LOCK_PATHS = {
    "application": "application/uv.lock",
    "backend": "backend/paper_runtime_manifest.json",
}
PAPER_ARTIFACT_CLASS = "CANONICAL_PAPER_V1"
PAPER_BACKEND_ENTRYPOINT = "paper_main.py"
PAPER_BACKEND_SOURCE_MAPPING = (
    (
        "job_attribution.py",
        "packages/runtime_release/paper_backend/job_attribution.py",
    ),
    ("paper_main.py", "packages/runtime_release/paper_backend/paper_main.py"),
    (
        "paper_runtime_manifest.json",
        "packages/runtime_release/paper_backend/paper_runtime_manifest.json",
    ),
    (
        "provider_free_fixture.py",
        "packages/runtime_release/paper_backend/provider_free_fixture.py",
    ),
    (
        "research_semantics.py",
        "packages/runtime_release/paper_backend/research_semantics.py",
    ),
)
PAPER_BACKEND_SOURCE_PATHS = tuple(
    artifact_path for artifact_path, _ in PAPER_BACKEND_SOURCE_MAPPING
)
PAPER_APPLICATION_SOURCE_MAPPING = (
    ("apps/job_api/__init__.py", "apps/job_api/__init__.py"),
    ("apps/job_api/app.py", "apps/job_api/app.py"),
    ("apps/job_api/auth.py", "apps/job_api/auth.py"),
    ("apps/job_api/config.py", "apps/job_api/config.py"),
    ("apps/job_api/contracts.py", "apps/job_api/contracts.py"),
    ("apps/job_api/errors.py", "apps/job_api/errors.py"),
    ("apps/job_api/main.py", "apps/job_api/main.py"),
    ("packages/__init__.py", "packages/__init__.py"),
    (
        "packages/job_contracts/__init__.py",
        "packages/runtime_release/paper_application/job_contracts_init.py",
    ),
    (
        "packages/job_contracts/api.py",
        "packages/runtime_release/paper_application/job_contracts_api.py",
    ),
    (
        "packages/job_contracts/enums.py",
        "packages/runtime_release/paper_application/job_contracts_enums.py",
    ),
    ("packages/job_contracts/fingerprint.py", "packages/job_contracts/fingerprint.py"),
    (
        "packages/job_contracts/payloads.py",
        "packages/runtime_release/paper_application/job_contracts_payloads.py",
    ),
    ("packages/job_contracts/transitions.py", "packages/job_contracts/transitions.py"),
    (
        "packages/runtime_release/__init__.py",
        "packages/runtime_release/paper_application/runtime_release_init.py",
    ),
    (
        "packages/runtime_release/config.py",
        "packages/runtime_release/paper_application/runtime_release_config.py",
    ),
    (
        "packages/runtime_release/job_plane.py",
        "packages/runtime_release/paper_application/runtime_release_job_plane.py",
    ),
    (
        "packages/runtime_release/semantic.py",
        "packages/runtime_release/paper_application/runtime_release_semantic.py",
    ),
    (
        "packages/runtime_release/staging_v2.py",
        "packages/runtime_release/staging_v2.py",
    ),
    ("packages/safety_evidence.py", "packages/safety_evidence.py"),
    ("pyproject.toml", "packages/runtime_release/paper_application/pyproject.toml"),
    ("services/__init__.py", "services/__init__.py"),
    ("services/job_store/__init__.py", "services/job_store/__init__.py"),
    ("services/job_store/config.py", "services/job_store/config.py"),
    ("services/job_store/errors.py", "services/job_store/errors.py"),
    ("services/job_store/records.py", "services/job_store/records.py"),
    ("services/job_store/repository.py", "services/job_store/repository.py"),
    (
        "services/job_store/worker_repository.py",
        "services/job_store/worker_repository.py",
    ),
    ("services/job_worker/__init__.py", "services/job_worker/__init__.py"),
    ("services/job_worker/artifacts.py", "services/job_worker/artifacts.py"),
    (
        "services/job_worker/command_registry.py",
        "packages/runtime_release/paper_application/command_registry.py",
    ),
    (
        "services/job_worker/environment.py",
        "packages/runtime_release/paper_application/environment.py",
    ),
    ("services/job_worker/errors.py", "services/job_worker/errors.py"),
    ("services/job_worker/main.py", "services/job_worker/main.py"),
    ("services/job_worker/process_runner.py", "services/job_worker/process_runner.py"),
    ("services/job_worker/recovery.py", "services/job_worker/recovery.py"),
    (
        "services/job_worker/results.py",
        "packages/runtime_release/paper_application/results.py",
    ),
    (
        "services/job_worker/safety.py",
        "packages/runtime_release/paper_application/safety.py",
    ),
    ("services/job_worker/safety_state.py", "services/job_worker/safety_state.py"),
    ("services/job_worker/worker.py", "services/job_worker/worker.py"),
    (
        "services/safety_state_exporter/__init__.py",
        "services/safety_state_exporter/__init__.py",
    ),
    (
        "services/safety_state_exporter/exporter.py",
        "packages/runtime_release/paper_application/safety_exporter.py",
    ),
    ("uv.lock", "packages/runtime_release/paper_application/uv.lock"),
)
PAPER_APPLICATION_SOURCE_PATHS = tuple(
    artifact_path for artifact_path, _ in PAPER_APPLICATION_SOURCE_MAPPING
)
PAPER_APPLICATION_IMPORT_PATH = (
    ".venv/lib/python3.11/site-packages/trading-agent-paper-application.pth"
)
PAPER_APPLICATION_IMPORT_PATH_BYTES = b"../../../..\n"
PAPER_FORCED_ENVIRONMENT = {
    "LIVE_EXECUTION_ENABLED": "false",
    "LIVE_TRADING_APPROVED": "false",
    "LIVE_TRADING_ENABLED": "false",
    "TRADING_MODE": "paper",
}
PAPER_FORBIDDEN_CAPABILITIES = (
    "BROKER_ADAPTER",
    "CREDENTIAL_LOADER",
    "EXCHANGE_ADAPTER_REGISTRY",
    "LIVE_EXECUTION",
    "MODE_TRANSITION",
    "REAL_ORDER_SUBMISSION",
    "WITHDRAWAL",
)
PAPER_STDLIB_IMPORTS = (
    "__future__",
    "dataclasses",
    "datetime",
    "hashlib",
    "hmac",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "secrets",
    "stat",
    "sys",
    "types",
    "typing",
    "urllib",
)
PAPER_PYTHON_RUNTIME_PROVENANCE = {
    "identity": "CPython 3.11.15",
    "normalized_core_sha256": (
        "39632162b32a97b4ccd3f3dd5f79d0735137f9247401835d1287b433dc83dcf7"
    ),
    "upstream_archive": (
        "cpython-3.11.15+20260414-x86_64-unknown-linux-gnu-"
        "install_only_stripped.tar.gz"
    ),
    "upstream_archive_sha256": (
        "b702a19b26cbd007abf9ccbaa45dfdff99e9dbd646d89c9f3c9bb7b501aea44f"
    ),
}
PAPER_UV_PROVENANCE = {
    "identity": "uv 0.11.7 (x86_64-unknown-linux-gnu)",
    "sha256": "cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4",
}
PAPER_APPLICATION_DEPENDENCY_PROVENANCE = {
    "file_count": 546,
    "installed_file_set_sha256": (
        "d5e97e6843205315334f0665badfd75e58ef6893af033ca9cbdd7155df89b1aa"
    ),
    "lock_sha256": "a4fac2d6f0587c534555e6d8c3ca9c22460ba18b09e5eb684c7b38409ce2d759",
    "manifest_sha256": "a98d670fe49964f71aabb9be3daaeb062412452329a72a6616e0f4f40681cba6",
    "provenance_file_set_sha256": (
        "687b409c91b40ed2293e09bca8bab1d53779fb58425c8ab56d29e459ec209603"
    ),
    "schema_version": 1,
    "uv_sha256": PAPER_UV_PROVENANCE["sha256"],
    "wheel_count": 16,
    "wheelhouse_aggregate_sha256": (
        "6871c43d484d58d6fd3b17c10357830fa4284cdcb6489968eaf3d4e348fc311d"
    ),
}
PAPER_FORBIDDEN_ARTIFACT_PATHS = frozenset({
    "main.py",
    "live_execution_policy.py",
    "execute_live.py",
    "broker.py",
    "trading_agent.py",
    "exchange/adapter.py",
    "exchange/ccxt_bridge.py",
    "exchange/executor.py",
    "exchange/secrets.py",
})
PAPER_RUNTIME_MANIFEST = {
    "artifact_class": PAPER_ARTIFACT_CLASS,
    "command_catalog": [
        {"entrypoint": PAPER_BACKEND_ENTRYPOINT, "job_type": "SNAPSHOT", "shell": False},
    ],
    "dependency_policy": "PYTHON_STDLIB_ONLY",
    "entrypoint": PAPER_BACKEND_ENTRYPOINT,
    "forbidden_capabilities": list(PAPER_FORBIDDEN_CAPABILITIES),
    "forced_environment": PAPER_FORCED_ENVIRONMENT,
    "python_runtime": PAPER_PYTHON_RUNTIME_PROVENANCE,
    "schema_version": 1,
    "source_allowlist": list(PAPER_BACKEND_SOURCE_PATHS),
    "stdlib_import_allowlist": list(PAPER_STDLIB_IMPORTS),
}
PAPER_ARTIFACT_POLICY = {
    "artifact_class": PAPER_ARTIFACT_CLASS,
    "backend_entrypoint": PAPER_BACKEND_ENTRYPOINT,
    "backend_manifest": "backend/paper_runtime_manifest.json",
    "backend_source_allowlist": list(PAPER_BACKEND_SOURCE_PATHS),
    "dependency_policy": "PYTHON_STDLIB_ONLY",
    "forced_child_environment": PAPER_FORCED_ENVIRONMENT,
    "permitted_job_types": ["SNAPSHOT"],
    "python_runtime": PAPER_PYTHON_RUNTIME_PROVENANCE,
    "stdlib_import_allowlist": list(PAPER_STDLIB_IMPORTS),
}
UNIT_NAMES = ("trading-job-api.service", "trading-job-worker.service")
EXTERNAL_VERIFIER_INSTALLATION_PATH = Path("/usr/libexec/trading-agent-v2/verify-stage.py")
RUNTIME_PATHS = {
    "activation": "/etc/trading-agent-v2/release-activation-v2.json",
    "job_artifacts_root": "/var/lib/trading-agent-v2/job-artifacts",
    "reports_root": "/var/lib/trading-agent-v2/research-output/reports",
    "safety_snapshot": "/run/trading-agent-v2/safety-state.json",
    "scratch_root": "/var/lib/trading-agent-v2/research-output/scratch",
    "semantic_active": "/etc/trading-agent-v2/research-input-manifests/active.json",
    "semantic_input_root": "/var/lib/trading-agent-v2/research-input",
    "signals_root": "/var/lib/trading-agent-v2/research-output/signals",
    "static_authority": "/etc/trading-agent-v2/release-authority-v2.json",
}
RUNTIME_DOCUMENT_POLICY = {
    "activation": {"gid": 0, "mode": "0444", "publication": "CREATE_ONLY", "uid": 0},
    "safety_snapshot": {"gid": 0, "mode": "0444", "publication": "ATOMIC_ROTATING", "uid": 0},
    "semantic_active": {"gid": 0, "mode": "0444", "publication": "ATOMIC_ROTATING", "uid": 0},
    "static_authority": {"gid": 0, "mode": "0444", "publication": "CREATE_ONLY", "uid": 0},
}
SAFETY_SOURCE_FINGERPRINT = "7e22249151c4e86661dae78d907d21818619ca0bed272f34725d33425d8bdb61"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
_PYTHON_ID = re.compile(r"CPython 3\.11\.\d+\Z")
_MAX_AUTHORITY_BYTES = 64 * 1024 * 1024
_MAX_COMMIT_OBJECT_BYTES = 4 * 1024 * 1024
_GIT_FILE_MODES = {"100644": "0444", "100755": "0555"}
_ALLOWED_STAGE_ADDITION_ROOTS = (
    "application/.venv",
    "backend/.venv",
)
_ALLOWED_UNIT_PATHS = {"units", *(f"units/{name}" for name in UNIT_NAMES)}


class ReleaseAuthorityV2Error(RuntimeError):
    """Sanitized rejection safe for build/provision logs."""

    def __init__(self) -> None:
        super().__init__("release authority v2 rejected")


@dataclass(frozen=True, slots=True, repr=False)
class StaticReleaseAuthorityV2:
    digest: str
    source_commit: str
    source_tree: str
    stage_path: Path
    installation_root: Path

    def __repr__(self) -> str:
        return "StaticReleaseAuthorityV2(validated=True)"


def canonical_json_bytes(document: object) -> bytes:
    """Return the sole accepted v2 JSON encoding."""

    try:
        return (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _fragment(value: object) -> bytes:
    return canonical_json_bytes(value)[:-1]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError
        return digest.hexdigest()
    except Exception:
        raise ReleaseAuthorityV2Error() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _load_canonical(raw: bytes) -> dict[str, Any]:
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_AUTHORITY_BYTES:
            raise ValueError
        document = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(document, dict) or raw != canonical_json_bytes(document):
            raise ValueError
        return document
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _exact(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError
    return value


def _git_id(value: object) -> str:
    if not isinstance(value, str) or _GIT_ID.fullmatch(value) is None:
        raise ValueError
    return value


def _git_object_id(kind: str, raw: bytes) -> str:
    return hashlib.sha1(
        f"{kind} {len(raw)}\0".encode("ascii") + raw,
        usedforsecurity=False,
    ).hexdigest()


def _absolute(value: object) -> Path:
    if not isinstance(value, str):
        raise ValueError
    pure = PurePosixPath(value)
    if (
        not pure.is_absolute()
        or value.startswith("//")
        or ".." in pure.parts
        or pure.as_posix() != value
    ):
        raise ValueError
    return Path(value)


def _relative(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts or pure.as_posix() != value:
        raise ValueError
    return value


def _no_xattrs(path: Path) -> None:
    try:
        if os.listxattr(path, follow_symlinks=False):
            raise ValueError
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _safe_external_file(path: Path, *, executable: bool) -> tuple[os.stat_result, str]:
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError
        info = path.lstat()
        allowed_modes = {0o555, 0o755} if executable else {0o444}
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) not in allowed_modes
            or info.st_mode & 0o7000
        ):
            raise ValueError
        _no_xattrs(path)
        return info, _sha256_file(path)
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _safe_root_executable(path: Path) -> tuple[os.stat_result, str]:
    info, digest = _safe_external_file(path, executable=True)
    try:
        if info.st_uid != 0 or info.st_gid != 0:
            raise ValueError
        current = Path(path.anchor)
        for part in path.parts[1:-1]:
            current /= part
            ancestor = current.lstat()
            if (
                not stat.S_ISDIR(ancestor.st_mode)
                or ancestor.st_uid != 0
                or ancestor.st_gid != 0
                or ancestor.st_mode & (0o022 | 0o7000)
            ):
                raise ValueError
            _no_xattrs(current)
        return info, digest
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _safe_root_ancestors(path: Path) -> None:
    try:
        current = Path(path.anchor)
        for part in path.parts[1:-1]:
            current /= part
            info = current.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or info.st_gid != 0
                or info.st_mode & (0o022 | 0o7000)
            ):
                raise ValueError
            _no_xattrs(current)
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _walk_sealed_stage(root: Path) -> tuple[int, int, list[dict[str, object]]]:
    try:
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise ValueError
        root_info = root.lstat()
        if not stat.S_ISDIR(root_info.st_mode) or stat.S_IMODE(root_info.st_mode) != 0o555:
            raise ValueError
        _no_xattrs(root)
        entries: list[dict[str, object]] = []
        regular_inodes: set[tuple[int, int]] = set()
        pending = [root]
        while pending:
            directory = pending.pop()
            children = sorted(directory.iterdir(), key=lambda item: os.fsencode(item.name))
            for child in children:
                relative = child.relative_to(root).as_posix()
                _relative(relative)
                if "__pycache__" in PurePosixPath(relative).parts or relative.endswith((".pyc", ".pyo")):
                    raise ValueError
                info = child.lstat()
                if info.st_uid != root_info.st_uid or info.st_gid != root_info.st_gid:
                    raise ValueError
                _no_xattrs(child)
                if stat.S_ISDIR(info.st_mode):
                    if stat.S_IMODE(info.st_mode) != 0o555:
                        raise ValueError
                    entries.append(
                        {"mode": "0555", "path": relative, "sha256": _sha256_bytes(b""), "size": 0, "type": "directory"}
                    )
                    pending.append(child)
                elif stat.S_ISREG(info.st_mode):
                    mode = stat.S_IMODE(info.st_mode)
                    if mode not in {0o444, 0o555} or info.st_nlink != 1:
                        raise ValueError
                    identity = (info.st_dev, info.st_ino)
                    if identity in regular_inodes:
                        raise ValueError
                    regular_inodes.add(identity)
                    entries.append(
                        {
                            "mode": f"{mode:04o}",
                            "path": relative,
                            "sha256": _sha256_file(child),
                            "size": info.st_size,
                            "type": "file",
                        }
                    )
                else:
                    raise ValueError
        entries.sort(key=lambda item: os.fsencode(str(item["path"])))
        return root_info.st_uid, root_info.st_gid, entries
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _entry_map(entries: Sequence[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(item["path"]): item for item in entries}


def _source_stage_path(source_path: str) -> str | None:
    for artifact_path, repository_path in PAPER_BACKEND_SOURCE_MAPPING:
        if source_path == repository_path:
            return "backend/" + artifact_path
    for artifact_path, repository_path in PAPER_APPLICATION_SOURCE_MAPPING:
        if source_path == repository_path:
            return "application/" + artifact_path
    return None

_PYTHON_RUNTIME_CONFIG = (
    b"include-system-site-packages = false\n"
    b"version = 3.11.15\n"
)
_PYTHON_RUNTIME_PROVENANCE_PATH = "runtime-provenance.json"


def _excluded_python_runtime_core_path(relative: str) -> bool:
    pure = PurePosixPath(relative)
    return (
        relative in {"pyvenv.cfg", _PYTHON_RUNTIME_PROVENANCE_PATH}
        or "__pycache__" in pure.parts
        or "site-packages" in pure.parts
        or pure.suffix in {".pyc", ".pyo"}
        or (pure.parts[0] == "bin" and relative != "bin/python3.11")
    )


def _python_runtime_core_entries(
    runtime_root: Path,
    *,
    allow_internal_source_links: bool,
) -> list[list[object]]:
    root = Path(runtime_root)
    if (
        not root.is_absolute()
        or root.resolve(strict=True) != root
        or not root.is_dir()
        or root.is_symlink()
    ):
        raise ValueError
    entries: list[list[object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: os.fsencode(item.relative_to(root).as_posix())):
        relative = path.relative_to(root).as_posix()
        if _excluded_python_runtime_core_path(relative):
            continue
        logical_info = path.lstat()
        if stat.S_ISDIR(logical_info.st_mode):
            continue
        if stat.S_ISLNK(logical_info.st_mode):
            if not allow_internal_source_links:
                raise ValueError
            resolved = path.resolve(strict=True)
            if root not in resolved.parents:
                raise ValueError
        elif stat.S_ISREG(logical_info.st_mode):
            resolved = path
        else:
            raise ValueError
        before = resolved.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError
        raw = resolved.read_bytes()
        after = resolved.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError
        entries.append([
            relative,
            len(raw),
            _sha256_bytes(raw),
            bool(before.st_mode & 0o111),
        ])
    if not entries or not any(entry[0] == "bin/python3.11" for entry in entries):
        raise ValueError
    return entries


def python_runtime_core_sha256(
    runtime_root: Path,
    *,
    allow_internal_source_links: bool = False,
) -> str:
    entries = _python_runtime_core_entries(
        runtime_root,
        allow_internal_source_links=allow_internal_source_links,
    )
    return _sha256_bytes(_fragment(entries))


def inspect_python_runtime(
    runtime_root: Path,
    *,
    expected_core_sha256: str | None = None,
    require_empty_site_packages: bool,
) -> dict[str, object]:
    try:
        root = Path(runtime_root)
        expected = _digest(
            expected_core_sha256
            or str(PAPER_PYTHON_RUNTIME_PROVENANCE["normalized_core_sha256"])
        )
        for path in root.rglob("*"):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                resolved = path.resolve(strict=True)
                if root not in resolved.parents or not resolved.is_file():
                    raise ValueError
            elif not stat.S_ISDIR(info.st_mode) and (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            ):
                raise ValueError
            pure = PurePosixPath(path.relative_to(root).as_posix())
            if "__pycache__" in pure.parts or pure.suffix in {".pyc", ".pyo"}:
                raise ValueError
        executable = root / "bin/python3.11"
        config = root / "pyvenv.cfg"
        provenance_path = root / _PYTHON_RUNTIME_PROVENANCE_PATH
        for required_file in (executable, config, provenance_path):
            required_info = required_file.lstat()
            if not stat.S_ISREG(required_info.st_mode) or required_info.st_nlink != 1:
                raise ValueError
        bin_files = {
            path.name
            for path in (root / "bin").iterdir()
            if path.is_file()
        }
        if bin_files != {"python3.11"}:
            raise ValueError
        if config.read_bytes() != _PYTHON_RUNTIME_CONFIG:
            raise ValueError
        provenance = _load_canonical(provenance_path.read_bytes())
        if provenance != PAPER_PYTHON_RUNTIME_PROVENANCE:
            raise ValueError
        site_packages = root / "lib/python3.11/site-packages"
        if not site_packages.is_dir() or site_packages.is_symlink():
            raise ValueError
        if require_empty_site_packages and any(site_packages.iterdir()):
            raise ValueError
        observed = python_runtime_core_sha256(
            root,
            allow_internal_source_links=True,
        )
        if observed != expected:
            raise ValueError
        return {
            "core_sha256": observed,
            "identity": PAPER_PYTHON_RUNTIME_PROVENANCE["identity"],
            "provenance": deepcopy(PAPER_PYTHON_RUNTIME_PROVENANCE),
        }
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def construct_python_runtime(
    source: Path,
    destination: Path,
    *,
    expected_core_sha256: str | None = None,
) -> dict[str, object]:
    created = False
    try:
        source_root = Path(source)
        runtime_root = Path(destination)
        expected = _digest(
            expected_core_sha256
            or str(PAPER_PYTHON_RUNTIME_PROVENANCE["normalized_core_sha256"])
        )
        entries = _python_runtime_core_entries(
            source_root,
            allow_internal_source_links=True,
        )
        if _sha256_bytes(_fragment(entries)) != expected:
            raise ValueError
        if (
            not runtime_root.is_absolute()
            or runtime_root.exists()
            or runtime_root.is_symlink()
        ):
            raise ValueError
        runtime_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        created = True
        for relative, _, _, executable in entries:
            source_path = (source_root / str(relative)).resolve(strict=True)
            if source_root not in source_path.parents:
                raise ValueError
            destination_path = runtime_root / str(relative)
            destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_exclusive(
                destination_path,
                source_path.read_bytes(),
                0o755 if executable else 0o644,
            )
        site_packages = runtime_root / "lib/python3.11/site-packages"
        site_packages.mkdir(mode=0o700, parents=True, exist_ok=False)
        _write_exclusive(runtime_root / "pyvenv.cfg", _PYTHON_RUNTIME_CONFIG, 0o644)
        _write_exclusive(
            runtime_root / _PYTHON_RUNTIME_PROVENANCE_PATH,
            canonical_json_bytes(PAPER_PYTHON_RUNTIME_PROVENANCE),
            0o644,
        )
        _normalize_private_directories(runtime_root)
        return inspect_python_runtime(
            runtime_root,
            expected_core_sha256=expected,
            require_empty_site_packages=True,
        )
    except Exception:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise ReleaseAuthorityV2Error() from None


def construct_python_runtime_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_archive_sha256: str | None = None,
    expected_core_sha256: str | None = None,
) -> dict[str, object]:
    """Verify and safely project the code-owned CPython archive."""

    try:
        source_archive = Path(archive_path)
        runtime_root = Path(destination)
        expected_archive = _digest(
            expected_archive_sha256
            or str(PAPER_PYTHON_RUNTIME_PROVENANCE["upstream_archive_sha256"])
        )
        expected_core = _digest(
            expected_core_sha256
            or str(PAPER_PYTHON_RUNTIME_PROVENANCE["normalized_core_sha256"])
        )
        if (
            not source_archive.is_absolute()
            or source_archive.resolve(strict=True) != source_archive
            or not runtime_root.is_absolute()
            or runtime_root.exists()
            or runtime_root.is_symlink()
            or runtime_root.parent.resolve(strict=True) != runtime_root.parent
        ):
            raise ValueError

        descriptor = os.open(
            source_archive,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size <= 0
                or before.st_size > 512 * 1024 * 1024
            ):
                raise ValueError
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or digest.hexdigest() != expected_archive:
                raise ValueError
            archive_raw = b"".join(chunks)
        finally:
            os.close(descriptor)

        with tempfile.TemporaryDirectory(
            prefix=".paper-python-runtime-",
            dir=runtime_root.parent,
        ) as temporary_raw:
            temporary = Path(temporary_raw)
            extracted_root = temporary / "extracted"
            extracted_root.mkdir(mode=0o700)
            members: dict[PurePosixPath, tarfile.TarInfo] = {}
            total_size = 0
            with tarfile.open(fileobj=io.BytesIO(archive_raw), mode="r:gz") as source:
                archive_members = source.getmembers()
                if not 0 < len(archive_members) <= 100_000:
                    raise ValueError
                for member in archive_members:
                    name = member.name
                    pure = PurePosixPath(name)
                    if (
                        not name
                        or "\\" in name
                        or pure.is_absolute()
                        or pure.as_posix() != name
                        or len(pure.parts) < 2
                        or pure.parts[0] != "python"
                        or any(part in {"", ".", ".."} for part in pure.parts)
                        or not (member.isreg() or member.issym())
                        or bool(member.pax_headers)
                        or bool(getattr(member, "sparse", None))
                        or member.mode & 0o7000
                    ):
                        raise ValueError
                    relative = PurePosixPath(*pure.parts[1:])
                    if relative in members:
                        raise ValueError
                    if member.isreg():
                        if member.size < 0 or member.size > 256 * 1024 * 1024:
                            raise ValueError
                        total_size += member.size
                        if total_size > 1024 * 1024 * 1024:
                            raise ValueError
                    members[relative] = member

                symlink_targets: dict[PurePosixPath, PurePosixPath] = {}
                for relative, member in members.items():
                    if not member.issym():
                        continue
                    link = PurePosixPath(member.linkname)
                    if (
                        not member.linkname
                        or "\\" in member.linkname
                        or link.is_absolute()
                    ):
                        raise ValueError
                    stack = list(relative.parent.parts)
                    for part in link.parts:
                        if part in {"", "."}:
                            continue
                        if part == "..":
                            if not stack:
                                raise ValueError
                            stack.pop()
                        else:
                            stack.append(part)
                    target = PurePosixPath(*stack)
                    if target not in members:
                        raise ValueError
                    symlink_targets[relative] = target
                for link_path in symlink_targets:
                    if any(
                        len(other.parts) > len(link_path.parts)
                        and other.parts[: len(link_path.parts)] == link_path.parts
                        for other in members
                    ):
                        raise ValueError

                for relative in sorted(members, key=lambda item: os.fsencode(str(item))):
                    member = members[relative]
                    if not member.isreg():
                        continue
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise ValueError
                    raw = extracted.read(member.size + 1)
                    if len(raw) != member.size:
                        raise ValueError
                    target = extracted_root / str(relative)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    _write_exclusive(target, raw, 0o755 if member.mode & 0o111 else 0o644)

                for relative in sorted(symlink_targets, key=lambda item: os.fsencode(str(item))):
                    member = members[relative]
                    target = extracted_root / str(relative)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.symlink(member.linkname, target)

            projected = temporary / "projected"
            evidence = construct_python_runtime(
                extracted_root,
                projected,
                expected_core_sha256=expected_core,
            )
            os.rename(projected, runtime_root)
        return {
            **evidence,
            "archive_sha256": expected_archive,
            "normalized_core_sha256": expected_core,
        }
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def verify_python_runtime_execution(runtime_root: Path) -> dict[str, object]:
    evidence = inspect_python_runtime(
        runtime_root,
        require_empty_site_packages=False,
    )
    root = Path(runtime_root).resolve(strict=True)
    probe = (
        "import json,platform,pathlib,sys,sysconfig;"
        "print(json.dumps({"
        "'identity':f'{platform.python_implementation()} {platform.python_version()}',"
        "'prefixes':[sys.prefix,sys.exec_prefix,sys.base_prefix,sys.base_exec_prefix,"
        "sysconfig.get_path('stdlib')]},sort_keys=True))"
    )
    completed = subprocess.run(
        [root / "bin/python3.11", "-I", "-B", "-c", probe],
        cwd=root,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    observed = json.loads(completed.stdout)
    if (
        observed.get("identity") != PAPER_PYTHON_RUNTIME_PROVENANCE["identity"]
        or not isinstance(observed.get("prefixes"), list)
        or len(observed["prefixes"]) != 5
    ):
        raise ReleaseAuthorityV2Error()
    for value in observed["prefixes"]:
        resolved = Path(value).resolve(strict=True)
        if resolved != root and root not in resolved.parents:
            raise ReleaseAuthorityV2Error()
    return {**evidence, "prefixes": observed["prefixes"]}


def construct_paper_application_artifact(source: Path, destination: Path) -> None:
    """Project the exact paper application allowlist into a new root."""

    created = False
    try:
        repository_root = Path(source)
        artifact_root = Path(destination)
        if (
            not repository_root.is_absolute()
            or repository_root.resolve(strict=True) != repository_root
            or not repository_root.is_dir()
            or not artifact_root.is_absolute()
            or artifact_root.exists()
            or artifact_root.is_symlink()
        ):
            raise ValueError
        artifact_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        created = True
        for artifact_relative, source_relative in PAPER_APPLICATION_SOURCE_MAPPING:
            source_path = repository_root / source_relative
            info = source_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or source_path.is_symlink()
                or source_path.resolve(strict=True) != source_path
                or repository_root not in source_path.parents
            ):
                raise ValueError
            destination_path = artifact_root / artifact_relative
            destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with source_path.open("rb") as source_file, destination_path.open("xb") as output:
                shutil.copyfileobj(source_file, output, 1024 * 1024)
            destination_path.chmod(0o755 if info.st_mode & 0o111 else 0o644)
        _normalize_private_directories(artifact_root)
        inspect_paper_application_artifact(artifact_root)
    except Exception:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise ReleaseAuthorityV2Error() from None


def install_paper_application_import_path(application_root: Path) -> Path:
    """Make the projected application importable by its isolated interpreter."""

    try:
        root = Path(application_root)
        if (
            not root.is_absolute()
            or root.is_symlink()
            or root.resolve(strict=True) != root
            or not root.is_dir()
        ):
            raise ValueError
        import_path = root / PAPER_APPLICATION_IMPORT_PATH
        site_packages = import_path.parent
        if (
            not site_packages.is_dir()
            or site_packages.is_symlink()
            or site_packages.resolve(strict=True) != site_packages
            or root not in site_packages.parents
            or import_path.exists()
            or import_path.is_symlink()
        ):
            raise ValueError
        _write_exclusive(import_path, PAPER_APPLICATION_IMPORT_PATH_BYTES, 0o644)
        return import_path
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def inspect_paper_application_artifact(
    artifact_root: Path,
    *,
    test_expected_python_runtime_core_sha256: str | None = None,
) -> dict[str, object]:
    """Reject broad, live-capable, or dynamically extensible application bytes."""

    try:
        root = Path(artifact_root)
        if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
            raise ValueError
        source_files: set[str] = set()
        venv_files: set[str] = set()
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if relative == ".venv":
                if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
                    raise ValueError
                continue
            if relative.startswith(".venv/"):
                if stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1:
                        raise ValueError
                    venv_files.add(relative)
                elif not stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                    raise ValueError
                continue
            if path.is_symlink() or info.st_mode & 0o7022:
                raise ValueError
            if path.is_file():
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError
                source_files.add(relative)
            elif not path.is_dir():
                raise ValueError
        if source_files != set(PAPER_APPLICATION_SOURCE_PATHS):
            raise ValueError
        if venv_files:
            inspect_python_runtime(
                root / ".venv",
                expected_core_sha256=test_expected_python_runtime_core_sha256,
                require_empty_site_packages=False,
            )
            forbidden_site_packages = {"alembic", "greenlet", "mako", "sqlalchemy"}
            site_prefix = ".venv/lib/python3.11/site-packages/"
            site_files = {
                relative for relative in venv_files if relative.startswith(site_prefix)
            }
            import_path_files = {
                relative for relative in site_files if relative.endswith(".pth")
            }
            if site_files:
                import_path = root / PAPER_APPLICATION_IMPORT_PATH
                info = import_path.lstat()
                if (
                    import_path_files != {PAPER_APPLICATION_IMPORT_PATH}
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) not in {0o444, 0o644}
                    or import_path.read_bytes() != PAPER_APPLICATION_IMPORT_PATH_BYTES
                ):
                    raise ValueError
            for relative in venv_files:
                if not relative.startswith(site_prefix):
                    continue
                top_level = relative.removeprefix(site_prefix).split("/", 1)[0].casefold().replace("_", "-")
                if any(
                    top_level == package or top_level.startswith(package + "-")
                    for package in forbidden_site_packages
                ):
                    raise ValueError
        elif (root / ".venv").exists():
            raise ValueError

        forbidden_fragments = (
            "ReplayPayload",
            "SafetyMode.LIVE",
            "_SafetyMode.LIVE",
            "JobType.REPLAY",
            "JobType.EXECUTE",
            "_RESEARCH_KEYS",
            "API_KEY",
            "load_dotenv",
            "apps.control_api",
            "control_api.",
            "services.job_scheduler",
            "asset_registry",
            "ccxt",
            "alpaca",
            "create_order",
            "place_order",
            "--apply",
            "shell=True",
            "os.system",
            "load_runtime_authority as",
            "RuntimeAuthority,",
        )
        forbidden_calls = {"__import__", "compile", "eval", "exec", "import_module", "system"}
        for relative in sorted(source_files, key=os.fsencode):
            path = root / relative
            if relative == "uv.lock":
                continue
            raw = path.read_text(encoding="utf-8")
            if any(fragment.casefold() in raw.casefold() for fragment in forbidden_fragments):
                raise ValueError
            if not relative.endswith(".py"):
                continue
            tree = ast.parse(raw, filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    called = (
                        node.func.id
                        if isinstance(node.func, ast.Name)
                        else node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else ""
                    )
                    if isinstance(node.func, ast.Name) and called in {
                        "__import__",
                        "compile",
                        "eval",
                        "exec",
                    }:
                        raise ValueError
                    if (
                        isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "importlib"
                        and node.func.attr == "import_module"
                    ):
                        raise ValueError
                    if (
                        isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                        and node.func.attr == "system"
                    ):
                        raise ValueError
                    if (
                        isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"
                        and node.func.attr in {"Popen", "run", "call"}
                    ):
                        if relative != "services/job_worker/process_runner.py" or node.func.attr != "Popen":
                            raise ValueError
                        for keyword in node.keywords:
                            if keyword.arg == "shell" and not (
                                isinstance(keyword.value, ast.Constant)
                                and keyword.value.value is False
                            ):
                                raise ValueError
        return {
            "artifact_class": PAPER_ARTIFACT_CLASS,
            "decision": "GO",
            "source_files": sorted(source_files, key=os.fsencode),
        }
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def construct_paper_backend_artifact(source: Path, destination: Path) -> None:
    """Project the exact paper runtime allowlist into a new artifact root."""

    created = False
    try:
        repository_root = Path(source)
        artifact_root = Path(destination)
        if (
            not repository_root.is_absolute()
            or repository_root.resolve(strict=True) != repository_root
            or not repository_root.is_dir()
            or not artifact_root.is_absolute()
            or artifact_root.exists()
            or artifact_root.is_symlink()
        ):
            raise ValueError
        artifact_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        created = True
        for artifact_relative, source_relative in PAPER_BACKEND_SOURCE_MAPPING:
            source_path = repository_root / source_relative
            info = source_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or source_path.is_symlink()
                or source_path.resolve(strict=True) != source_path
                or repository_root not in source_path.parents
            ):
                raise ValueError
            destination_path = artifact_root / artifact_relative
            with source_path.open("rb") as source_file, destination_path.open("xb") as output:
                shutil.copyfileobj(source_file, output, 1024 * 1024)
            destination_path.chmod(0o755 if info.st_mode & 0o111 else 0o644)
        _normalize_private_directories(artifact_root)
        inspect_paper_backend_artifact(
            artifact_root,
            paper_command_manifest(Path("/opt/trading-agent-v2/releases/" + "0" * 40)),
        )
    except Exception:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise ReleaseAuthorityV2Error() from None


def paper_command_manifest(install_root: Path) -> dict[str, object]:
    root = _absolute(str(install_root))
    backend = root / "backend"
    executable = backend / ".venv/bin/python3.11"
    commands: list[dict[str, object]] = [
        {
            "argv": [str(executable), "-I", "-B", PAPER_BACKEND_ENTRYPOINT],
            "cwd": str(backend),
            "environment_policy": "CANONICAL_PAPER_CHILD_V1",
            "executable": str(executable),
            "job_type": "SNAPSHOT",
            "shell": False,
        }
    ]
    return {
        "commands": commands,
        "manifest_sha256": _sha256_bytes(_fragment(commands)),
        "schema_version": 3,
    }


def _validate_paper_venv(
    root: Path,
    venv_files: set[str],
    *,
    test_expected_python_runtime_core_sha256: str | None = None,
) -> None:
    if not (root / ".venv").exists():
        if venv_files:
            raise ValueError
        return
    if not venv_files:
        raise ValueError
    inspect_python_runtime(
        root / ".venv",
        expected_core_sha256=test_expected_python_runtime_core_sha256,
        require_empty_site_packages=True,
    )


def inspect_paper_backend_artifact(
    artifact_root: Path,
    command_manifest: Mapping[str, object],
    *,
    test_expected_python_runtime_core_sha256: str | None = None,
) -> dict[str, object]:
    """Reject any paper artifact or command catalog with live authority surface."""

    try:
        root = Path(artifact_root)
        if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
            raise ValueError
        source_files: set[str] = set()
        venv_files: set[str] = set()
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError
            if path.is_file():
                if relative.startswith(".venv/"):
                    venv_files.add(relative)
                else:
                    source_files.add(relative)
            elif not path.is_dir() and not path.is_file():
                raise ValueError
        _validate_paper_venv(
            root,
            venv_files,
            test_expected_python_runtime_core_sha256=(
                test_expected_python_runtime_core_sha256
            ),
        )
        if source_files != set(PAPER_BACKEND_SOURCE_PATHS):
            raise ValueError
        if source_files.intersection(PAPER_FORBIDDEN_ARTIFACT_PATHS):
            raise ValueError
        manifest_path = root / "paper_runtime_manifest.json"
        manifest = json.loads(manifest_path.read_bytes(), object_pairs_hook=_pairs)
        if manifest != PAPER_RUNTIME_MANIFEST:
            raise ValueError

        command = _exact(
            command_manifest,
            {"commands", "manifest_sha256", "schema_version"},
        )
        commands = command["commands"]
        if command["schema_version"] != 3 or not isinstance(commands, list) or len(commands) != 1:
            raise ValueError
        snapshot = _exact(
            commands[0],
            {"argv", "cwd", "environment_policy", "executable", "job_type", "shell"},
        )
        cwd = _absolute(snapshot["cwd"])
        if command != paper_command_manifest(cwd.parent):
            raise ValueError

        forbidden_symbols = {
            "create_order", "place_order", "execute_live", "get_exchange",
            "get_exchange_credentials", "load_dotenv", "mode_file", "set_mode",
        }
        forbidden_dynamic_calls = {"__import__", "compile", "eval", "exec"}
        local_modules = {
            Path(path).stem
            for path in PAPER_BACKEND_SOURCE_PATHS
            if path.endswith(".py")
        }
        allowed_imports = set(PAPER_STDLIB_IMPORTS).union(local_modules)
        for relative in sorted(source_files):
            if not relative.endswith(".py"):
                continue
            tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports = [node.module]
                else:
                    imports = []
                if any(name.split(".")[0] not in allowed_imports for name in imports):
                    raise ValueError
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in forbidden_symbols:
                        raise ValueError
                if isinstance(node, ast.Call):
                    called = (
                        node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr if isinstance(node.func, ast.Attribute)
                        else ""
                    )
                    if called in forbidden_symbols:
                        raise ValueError
                    if isinstance(node.func, ast.Name) and called in forbidden_dynamic_calls:
                        raise ValueError
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                        raise ValueError
        return {
            "artifact_class": PAPER_ARTIFACT_CLASS,
            "decision": "GO",
            "entrypoint": PAPER_BACKEND_ENTRYPOINT,
            "forced_environment": dict(PAPER_FORCED_ENVIRONMENT),
            "source_files": sorted(source_files),
        }
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _allowed_stage_addition(path: str) -> bool:
    return path in _ALLOWED_UNIT_PATHS or any(
        path == root or path.startswith(root + "/")
        for root in _ALLOWED_STAGE_ADDITION_ROOTS
    )


def _git_tree_ids(entries: Sequence[dict[str, object]]) -> dict[str, str]:
    tree: dict[str, object] = {}
    for entry in entries:
        source_path = str(entry["source_path"])
        current = tree
        parts = source_path.split("/")
        for part in parts[:-1]:
            existing = current.get(part)
            if existing is None:
                child: dict[str, object] = {}
                current[part] = child
                current = child
            elif isinstance(existing, dict):
                current = existing
            else:
                raise ValueError
        if parts[-1] in current:
            raise ValueError
        current[parts[-1]] = (entry["mode"], entry["git_blob"])

    result: dict[str, str] = {}

    def seal(node: dict[str, object], prefix: str) -> str:
        material = bytearray()
        ordered = sorted(
            node.items(),
            key=lambda item: os.fsencode(item[0] + ("/" if isinstance(item[1], dict) else "")),
        )
        for name, value in ordered:
            name_raw = name.encode("utf-8")
            if not name_raw or b"\0" in name_raw or b"/" in name_raw:
                raise ValueError
            if isinstance(value, dict):
                object_id = seal(value, f"{prefix}/{name}".strip("/"))
                mode = "40000"
            else:
                mode, object_id = cast(tuple[str, str], value)
            material.extend(f"{mode} ".encode("ascii"))
            material.extend(name_raw)
            material.append(0)
            material.extend(bytes.fromhex(str(object_id)))
        object_id = _git_object_id("tree", bytes(material))
        result[prefix] = object_id
        return object_id

    seal(tree, "")
    return result


def _validated_source_proof(
    value: object,
    by_path: Mapping[str, dict[str, object]],
    *,
    expect_binding: bool,
) -> tuple[dict[str, object], dict[str, str]]:
    keys = {"commit", "commit_object_hex", "entries", "tree"}
    source = _exact(value, keys | ({"binding_sha256"} if expect_binding else set()))
    commit = _git_id(source["commit"])
    declared_tree = _git_id(source["tree"])
    commit_hex = source["commit_object_hex"]
    if not isinstance(commit_hex, str) or len(commit_hex) > _MAX_COMMIT_OBJECT_BYTES * 2:
        raise ValueError
    commit_object = bytes.fromhex(commit_hex)
    if commit_object.hex() != commit_hex or _git_object_id("commit", commit_object) != commit:
        raise ValueError
    first_line = commit_object.partition(b"\n")[0]
    if first_line != f"tree {declared_tree}".encode("ascii"):
        raise ValueError

    raw_entries = source["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError
    normalized: list[dict[str, object]] = []
    previous: bytes | None = None
    stage_paths: set[str] = set()
    source_directories: set[str] = {"application", "backend"}
    for raw_entry in raw_entries:
        entry = _exact(
            raw_entry,
            {"git_blob", "mode", "sha256", "size", "source_path", "stage_path"},
        )
        source_path = _relative(entry["source_path"])
        expected_stage_path = _source_stage_path(source_path)
        stage_path = (
            None if entry["stage_path"] is None else _relative(entry["stage_path"])
        )
        encoded = os.fsencode(source_path)
        if (
            source_path == "."
            or stage_path != expected_stage_path
            or (previous is not None and encoded <= previous)
            or (stage_path is not None and stage_path in stage_paths)
            or entry["mode"] not in _GIT_FILE_MODES
            or type(entry["size"]) is not int
            or entry["size"] < 0
        ):
            raise ValueError
        previous = encoded
        git_blob = _git_id(entry["git_blob"])
        sha256 = _digest(entry["sha256"])
        if stage_path is not None:
            stage_paths.add(stage_path)
            staged = by_path.get(stage_path)
            if staged != {
                "mode": _GIT_FILE_MODES[str(entry["mode"])],
                "path": stage_path,
                "sha256": sha256,
                "size": entry["size"],
                "type": "file",
            }:
                raise ValueError
            parent = PurePosixPath(stage_path).parent
            while parent.as_posix() != ".":
                source_directories.add(parent.as_posix())
                parent = parent.parent
        normalized.append(
            {
                "git_blob": git_blob,
                "mode": entry["mode"],
                "sha256": sha256,
                "size": entry["size"],
                "source_path": source_path,
                "stage_path": stage_path,
            }
        )

    for path, staged in by_path.items():
        if staged["type"] == "file" and path not in stage_paths and not _allowed_stage_addition(path):
            raise ValueError
        if staged["type"] == "directory" and path not in source_directories and not _allowed_stage_addition(path):
            raise ValueError

    tree_ids = _git_tree_ids(normalized)
    if tree_ids.get("") != declared_tree:
        raise ValueError
    for required in (
        "legacy/research-backend",
        "packages/runtime_release/paper_application",
        "packages/runtime_release/paper_backend",
    ):
        if required not in tree_ids:
            raise ValueError
    proof: dict[str, object] = {
        "commit": commit,
        "commit_object_hex": commit_hex,
        "entries": normalized,
        "tree": declared_tree,
    }
    binding = _sha256_bytes(_fragment(proof))
    if expect_binding:
        if source["binding_sha256"] != binding:
            raise ValueError
        proof["binding_sha256"] = binding
    return proof, tree_ids


def _verify_source_blobs(stage: Path, source: Mapping[str, object]) -> None:
    for entry in cast(list[dict[str, object]], source["entries"]):
        stage_path = entry["stage_path"]
        if stage_path is None:
            continue
        raw = (stage / str(stage_path)).read_bytes()
        if (
            len(raw) != entry["size"]
            or _sha256_bytes(raw) != entry["sha256"]
            or _git_object_id("blob", raw) != entry["git_blob"]
        ):
            raise ValueError


def _git_read(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "-C", os.fspath(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    return result.stdout


def capture_source_proof_v2(repository: Path, commit: str) -> dict[str, object]:
    """Capture a self-verifying proof from exact Git objects without a checkout."""

    try:
        repo = Path(repository)
        commit_id = _git_id(commit)
        if (
            not repo.is_absolute()
            or repo.resolve(strict=True) != repo
            or not repo.is_dir()
            or not (repo / ".git").exists()
            or _git_read(repo, "rev-parse", "--show-toplevel").rstrip(b"\n").decode("utf-8") != str(repo)
            or _git_read(repo, "rev-parse", "--verify", f"{commit_id}^{{commit}}").rstrip(b"\n").decode("ascii")
            != commit_id
        ):
            raise ValueError
        commit_object = _git_read(repo, "cat-file", "commit", commit_id)
        if _git_object_id("commit", commit_object) != commit_id:
            raise ValueError
        first_line = commit_object.partition(b"\n")[0]
        if not first_line.startswith(b"tree "):
            raise ValueError
        tree_id = _git_id(first_line.removeprefix(b"tree ").decode("ascii"))
        records = _git_read(repo, "ls-tree", "-r", "-z", "--full-tree", commit_id)
        entries: list[dict[str, object]] = []
        for record in records.split(b"\0"):
            if not record:
                continue
            metadata, separator, raw_path = record.partition(b"\t")
            fields = metadata.split(b" ")
            if not separator or len(fields) != 3:
                raise ValueError
            mode_raw, object_type, blob_raw = fields
            mode = mode_raw.decode("ascii")
            if mode not in _GIT_FILE_MODES or object_type != b"blob":
                raise ValueError
            blob = _git_id(blob_raw.decode("ascii"))
            source_path = _relative(raw_path.decode("utf-8"))
            raw = _git_read(repo, "cat-file", "blob", blob)
            if _git_object_id("blob", raw) != blob:
                raise ValueError
            entries.append(
                {
                    "git_blob": blob,
                    "mode": mode,
                    "sha256": _sha256_bytes(raw),
                    "size": len(raw),
                    "source_path": source_path,
                    "stage_path": _source_stage_path(source_path),
                }
            )
        entries.sort(key=lambda item: os.fsencode(str(item["source_path"])))
        tree_ids = _git_tree_ids(entries)
        if (
            tree_ids.get("") != tree_id
            or "legacy/research-backend" not in tree_ids
            or "packages/runtime_release/paper_application" not in tree_ids
            or "packages/runtime_release/paper_backend" not in tree_ids
        ):
            raise ValueError
        return {
            "commit": commit_id,
            "commit_object_hex": commit_object.hex(),
            "entries": entries,
            "tree": tree_id,
        }
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _migration_identity(path: Path) -> tuple[str, str | None]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        values: dict[str, object] = {}
        for statement in tree.body:
            name: str | None = None
            value: ast.expr | None = None
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                name, value = statement.targets[0].id, statement.value
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                name, value = statement.target.id, statement.value
            if name in {"revision", "down_revision"} and value is not None:
                values[name] = ast.literal_eval(value)
        revision, down_revision = values.get("revision"), values.get("down_revision")
        if (
            not isinstance(revision, str)
            or re.fullmatch(r"[0-9A-Za-z_]+", revision) is None
            or (down_revision is not None and not isinstance(down_revision, str))
        ):
            raise ValueError
        return revision, down_revision
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _artifact_digest(entries: Sequence[dict[str, object]], root: str) -> str:
    selected = [
        item
        for item in entries
        if item["path"] == root or str(item["path"]).startswith(root + "/")
    ]
    if not selected:
        raise ReleaseAuthorityV2Error()
    return _sha256_bytes(_fragment(selected))


def _installation_root(commit: str) -> Path:
    return Path(f"/opt/trading-agent-v2/releases/{commit}")


def _producer_bindings(commit: str) -> dict[str, str]:
    semantic_material = {
        "active_authority_path": RUNTIME_PATHS["semantic_active"],
        "backend_commit": commit,
        "classification": "READ_ONLY_EXTERNAL_INPUT",
        "command": "SNAPSHOT",
        "input_root": RUNTIME_PATHS["semantic_input_root"],
        "schema_version": "release-v2-semantic-policy/v1",
    }
    return {
        "safety_exporter_commit": commit,
        "safety_source_fingerprint": SAFETY_SOURCE_FINGERPRINT,
        "semantic_backend_commit": commit,
        "semantic_policy_sha256": _sha256_bytes(_fragment(semantic_material)),
    }


def _command_manifest(install_root: Path) -> dict[str, object]:
    return paper_command_manifest(install_root)


def _credential_references(service: str) -> dict[str, str]:
    if service == "trading-job-api.service":
        root = "/etc/trading-agent-v2/credentials/job-api"
        return {
            "database-host": f"{root}/database-host",
            "database-name": f"{root}/database-name",
            "database-password": f"{root}/database-password",
            "database-port": f"{root}/database-port",
            "job-api-principal-id": f"{root}/principal-id",
            "job-api-principal-type": f"{root}/principal-type",
            "job-api-token": f"{root}/token",
        }
    if service == "trading-job-worker.service":
        root = "/etc/trading-agent-v2/credentials/job-worker"
        return {
            "database-host": f"{root}/database-host",
            "database-name": f"{root}/database-name",
            "database-password": f"{root}/database-password",
            "database-port": f"{root}/database-port",
        }
    raise ReleaseAuthorityV2Error()


def _unit_specs(install_root: Path) -> dict[str, dict[str, object]]:
    application = install_root / "application"
    executable = application / ".venv/bin/python3.11"
    return {
        "trading-job-api.service": {
            "argv": [str(executable), "-I", "-m", "apps.job_api.main"],
            "credential_references": _credential_references(
                "trading-job-api.service"
            ),
            "database_role": "trading_job_api",
            "service_group": "trading-job-api",
            "service_user": "trading-job-api",
            "working_directory": str(application),
        },
        "trading-job-worker.service": {
            "argv": [str(executable), "-I", "-m", "services.job_worker.main"],
            "credential_references": _credential_references(
                "trading-job-worker.service"
            ),
            "database_role": "trading_job_worker",
            "service_group": "trading-job-worker",
            "service_user": "trading-job-worker",
            "working_directory": str(application),
        },
    }


def _render_unit(name: str, spec: Mapping[str, object]) -> bytes:
    description = (
        "Trading Agent v2 localhost Job Command API"
        if name == "trading-job-api.service"
        else "Trading Agent v2 single SNAPSHOT research worker"
    )
    extra = (
        "Environment=TRADING_JOB_API_HOST=127.0.0.1\nEnvironment=TRADING_JOB_API_PORT=8401\n"
        if name == "trading-job-api.service"
        else "Environment=TRADING_ALLOWED_JOB_TYPES=SNAPSHOT\n"
    )
    argv = " ".join(str(item) for item in cast(list[object], spec["argv"]))
    credential_references = _credential_references(name)
    if spec.get("credential_references") != credential_references:
        raise ReleaseAuthorityV2Error()
    credential_lines = "".join(
        f"LoadCredential={credential_name}:{credential_path}\n"
        for credential_name, credential_path in sorted(credential_references.items())
    )
    document = (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={spec['service_user']}\n"
        f"Group={spec['service_group']}\n"
        f"WorkingDirectory={spec['working_directory']}\n"
        f"{credential_lines}"
        "UnsetEnvironment=LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH\n"
        "Environment=PYTHONDONTWRITEBYTECODE=1\n"
        "Environment=TRADING_MODE=paper\n"
        "Environment=LIVE_EXECUTION_ENABLED=false\n"
        "Environment=LIVE_TRADING_APPROVED=false\n"
        "Environment=LIVE_TRADING_ENABLED=false\n"
        f"Environment=TRADING_DATABASE_USER={spec['database_role']}\n"
        f"{extra}"
        f"ExecStart={argv}\n"
        "Restart=on-failure\n"
        "RestartSec=5s\n"
        "KillMode=control-group\n"
        "UMask=0077\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
        "PrivateDevices=true\n"
        "ProtectSystem=strict\n"
        "ProtectKernelTunables=true\n"
        "ProtectKernelModules=true\n"
        "ProtectControlGroups=true\n"
        "RestrictNamespaces=true\n"
        "RestrictSUIDSGID=true\n"
        "RestrictRealtime=true\n"
        "LockPersonality=true\n"
        "CapabilityBoundingSet=\n"
        "AmbientCapabilities=\n"
    )
    return document.encode("utf-8")


def render_candidate_units(installation_root: Path) -> dict[str, bytes]:
    """Render disabled-by-default v2 units; intentionally emits no timer."""

    try:
        root = _absolute(str(installation_root))
        specs = _unit_specs(root)
        return {name: _render_unit(name, specs[name]) for name in UNIT_NAMES}
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _unit_authority(stage: Path, install_root: Path) -> dict[str, object]:
    expected = render_candidate_units(install_root)
    specs = _unit_specs(install_root)
    result: dict[str, object] = {}
    for name in UNIT_NAMES:
        path = stage / "units" / name
        try:
            if path.read_bytes() != expected[name]:
                raise ValueError
        except Exception:
            raise ReleaseAuthorityV2Error() from None
        result[name] = {
            **specs[name],
            "enabled_by_default": False,
            "path": f"units/{name}",
            "sha256": _sha256_bytes(expected[name]),
        }
    return result


def _authority_binding(document: Mapping[str, object]) -> str:
    material = deepcopy(dict(document))
    material.pop("binding_sha256", None)
    return _sha256_bytes(_fragment(material))


def _installed_site_package_entries(
    by_path: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    prefix = "application/.venv/lib/python3.11/site-packages/"
    return [
        {
            "path": path.removeprefix(prefix),
            "sha256": entry["sha256"],
            "size": entry["size"],
        }
        for path, entry in sorted(by_path.items(), key=lambda item: os.fsencode(item[0]))
        if path.startswith(prefix)
        and path != "application/" + PAPER_APPLICATION_IMPORT_PATH
        and entry["type"] == "file"
    ]


def _validate_dependency_provenance(value: object) -> dict[str, object]:
    item = _exact(
        value,
        {
            "file_count", "installed_file_set_sha256", "lock_sha256", "manifest_sha256",
            "provenance_file_set_sha256", "schema_version", "uv_sha256", "wheel_count",
            "wheelhouse_aggregate_sha256",
        },
    )
    if (
        item["schema_version"] != 1
        or type(item["file_count"]) is not int
        or item["file_count"] < 0
        or type(item["wheel_count"]) is not int
        or item["wheel_count"] < 0
    ):
        raise ValueError
    for key in (
        "installed_file_set_sha256", "lock_sha256", "manifest_sha256",
        "provenance_file_set_sha256", "uv_sha256", "wheelhouse_aggregate_sha256",
    ):
        _digest(item[key])
    if item["uv_sha256"] != PAPER_UV_PROVENANCE["sha256"]:
        raise ValueError
    return dict(item)


def _load_application_dependency_manifest(
    path: Path,
    by_path: Mapping[str, Mapping[str, object]],
    *,
    production: bool,
) -> dict[str, object]:
    info, measured_sha256 = _safe_external_file(path, executable=False)
    if stat.S_IMODE(info.st_mode) != 0o444:
        raise ValueError
    raw = path.read_bytes()
    if _sha256_bytes(raw) != measured_sha256:
        raise ValueError
    document = _exact(
        _load_canonical(raw),
        {
            "files", "installed_file_set_sha256", "lock_sha256",
            "provenance_file_set_sha256", "schema_version", "uv",
            "wheelhouse_aggregate_sha256", "wheels",
        },
    )
    if document["schema_version"] != 1:
        raise ValueError
    uv = _exact(document["uv"], {"identity", "sha256"})
    if uv != PAPER_UV_PROVENANCE:
        raise ValueError
    application_lock = by_path.get("application/uv.lock")
    if (
        application_lock is None
        or application_lock["type"] != "file"
        or document["lock_sha256"] != application_lock["sha256"]
    ):
        raise ValueError
    _digest(document["lock_sha256"])
    _digest(document["wheelhouse_aggregate_sha256"])

    wheels = document["wheels"]
    if not isinstance(wheels, list) or (production and not wheels):
        raise ValueError
    wheel_digests: set[str] = set()
    previous_wheel: bytes | None = None
    for value in wheels:
        wheel = _exact(value, {"filename", "sha256"})
        filename = _relative(wheel["filename"])
        if PurePosixPath(filename).name != filename:
            raise ValueError
        encoded = os.fsencode(filename)
        if previous_wheel is not None and encoded <= previous_wheel:
            raise ValueError
        previous_wheel = encoded
        digest = _digest(wheel["sha256"])
        if digest in wheel_digests:
            raise ValueError
        wheel_digests.add(digest)

    files = document["files"]
    if not isinstance(files, list) or (production and not files):
        raise ValueError
    normalized_files: list[dict[str, object]] = []
    installed_files: list[dict[str, object]] = []
    previous_path: bytes | None = None
    for value in files:
        entry = _exact(value, {"path", "sha256", "size", "wheel_sha256"})
        relative = _relative(entry["path"])
        if relative.startswith("."):
            raise ValueError
        encoded = os.fsencode(relative)
        if previous_path is not None and encoded <= previous_path:
            raise ValueError
        previous_path = encoded
        sha256 = _digest(entry["sha256"])
        wheel_sha256 = _digest(entry["wheel_sha256"])
        if wheel_sha256 not in wheel_digests or type(entry["size"]) is not int or entry["size"] < 0:
            raise ValueError
        normalized = {
            "path": relative,
            "sha256": sha256,
            "size": entry["size"],
            "wheel_sha256": wheel_sha256,
        }
        normalized_files.append(normalized)
        installed_files.append({key: normalized[key] for key in ("path", "sha256", "size")})
    if (
        document["installed_file_set_sha256"] != _sha256_bytes(_fragment(installed_files))
        or document["provenance_file_set_sha256"] != _sha256_bytes(_fragment(normalized_files))
        or installed_files != _installed_site_package_entries(by_path)
    ):
        raise ValueError
    summary = _validate_dependency_provenance(
        {
            "file_count": len(installed_files),
            "installed_file_set_sha256": document["installed_file_set_sha256"],
            "lock_sha256": document["lock_sha256"],
            "manifest_sha256": measured_sha256,
            "provenance_file_set_sha256": document["provenance_file_set_sha256"],
            "schema_version": document["schema_version"],
            "uv_sha256": uv["sha256"],
            "wheel_count": len(wheels),
            "wheelhouse_aggregate_sha256": document["wheelhouse_aggregate_sha256"],
        }
    )
    if production and summary != PAPER_APPLICATION_DEPENDENCY_PROVENANCE:
        raise ValueError
    return summary


def build_static_release_authority_v2(
    stage: Path,
    *,
    source_proof: Mapping[str, object],
    application_python_identity: str,
    backend_python_identity: str,
    external_verifier: Path,
    application_dependency_manifest: Path,
    prior_release_sha256: str,
    test_expected_python_runtime_core_sha256: str | None = None,
) -> tuple[dict[str, object], str]:
    """Compose (but do not publish or activate) a static v2 authority."""

    try:
        if _PYTHON_ID.fullmatch(application_python_identity) is None:
            raise ValueError
        if _PYTHON_ID.fullmatch(backend_python_identity) is None:
            raise ValueError
        prior = _digest(prior_release_sha256)
        uid, gid, entries = _walk_sealed_stage(stage)
        by_path = _entry_map(entries)
        source, tree_ids = _validated_source_proof(source_proof, by_path, expect_binding=False)
        _verify_source_blobs(stage, source)
        commit = str(source["commit"])
        source["binding_sha256"] = _sha256_bytes(_fragment(source))
        install_root = _installation_root(commit)
        verifier_info, verifier_sha = _safe_external_file(Path(external_verifier), executable=True)
        if stat.S_IMODE(verifier_info.st_mode) != 0o555:
            raise ValueError

        components = {
            name: {
                "artifact_root": COMPONENT_ARTIFACT_ROOTS[name],
                "artifact_set_sha256": _artifact_digest(entries, COMPONENT_ARTIFACT_ROOTS[name]),
                "source_prefix": COMPONENT_PREFIXES[name],
                "source_tree": (
                    tree_ids[""]
                    if COMPONENT_PREFIXES[name] == "."
                    else tree_ids[COMPONENT_PREFIXES[name]]
                ),
            }
            for name in sorted(COMPONENT_PREFIXES)
        }
        lockfiles: dict[str, object] = {}
        for name, path in LOCK_PATHS.items():
            entry = by_path.get(path)
            if entry is None or entry["type"] != "file":
                raise ValueError
            lockfiles[name] = {"path": path, "sha256": entry["sha256"]}
        dependency_provenance = _load_application_dependency_manifest(
            Path(application_dependency_manifest),
            by_path,
            production=test_expected_python_runtime_core_sha256 is None,
        )

        app_python = "application/.venv/bin/python3.11"
        backend_python = "backend/.venv/bin/python3.11"
        for path in (app_python, backend_python):
            if path not in by_path or by_path[path]["mode"] != "0555":
                raise ValueError
        command_manifest = _command_manifest(install_root)
        inspect_python_runtime(
            stage / "application/.venv",
            expected_core_sha256=test_expected_python_runtime_core_sha256,
            require_empty_site_packages=False,
        )
        inspect_paper_application_artifact(
            stage / "application",
            test_expected_python_runtime_core_sha256=(
                test_expected_python_runtime_core_sha256
            ),
        )
        inspect_paper_backend_artifact(
            stage / "backend",
            command_manifest,
            test_expected_python_runtime_core_sha256=(
                test_expected_python_runtime_core_sha256
            ),
        )
        document: dict[str, object] = {
            "artifact_policy": deepcopy(PAPER_ARTIFACT_POLICY),
            "authority_kind": STATIC_KIND,
            "build_tools": {"uv": deepcopy(PAPER_UV_PROVENANCE)},
            "components": components,
            "command_manifest": command_manifest,
            "database": {
                "expected_revision": EXPECTED_DATABASE_REVISION,
                **EXPECTED_DB_ROLES,
            },
            "dependency_manifests": {"application": dependency_provenance},
            "external_verifier": {
                "gid": 0,
                "installation_path": str(EXTERNAL_VERIFIER_INSTALLATION_PATH),
                "mode": "0555",
                "sha256": verifier_sha,
                "source_path": str(Path(external_verifier)),
                "uid": 0,
            },
            "installation_root": str(install_root),
            "interpreters": {
                "application_python": {
                    "identity": application_python_identity,
                    "path": app_python,
                    "runtime_core_sha256": PAPER_PYTHON_RUNTIME_PROVENANCE[
                        "normalized_core_sha256"
                    ],
                    "sha256": by_path[app_python]["sha256"],
                },
                "backend_python": {
                    "identity": backend_python_identity,
                    "path": backend_python,
                    "runtime_core_sha256": PAPER_PYTHON_RUNTIME_PROVENANCE[
                        "normalized_core_sha256"
                    ],
                    "sha256": by_path[backend_python]["sha256"],
                },
            },
            "job_plane_policy": deepcopy(JOB_PLANE_POLICY),
            "lockfiles": lockfiles,
            "prior_release_sha256": prior,
            "producer_bindings": _producer_bindings(commit),
            "runtime_document_policy": deepcopy(RUNTIME_DOCUMENT_POLICY),
            "runtime_paths": deepcopy(RUNTIME_PATHS),
            "schema_version": SCHEMA_VERSION,
            "seal_version": SEAL_VERSION,
            "source": source,
            "stage": {
                "entries": entries,
                "file_set_sha256": _sha256_bytes(_fragment(entries)),
                "gid": gid,
                "installation_gid": 0,
                "installation_root_mode": "0555",
                "installation_uid": 0,
                "path": str(stage),
                "root_mode": "0555",
                "uid": uid,
            },
            "units": _unit_authority(stage, install_root),
        }
        document["binding_sha256"] = _authority_binding(document)
        _validate_static_document(
            document,
            expected_application_dependency_provenance=dependency_provenance,
        )
        raw = canonical_json_bytes(document)
        return document, _sha256_bytes(raw)
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _validate_entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError
    result: list[dict[str, object]] = []
    previous: bytes | None = None
    for item in value:
        entry = _exact(item, {"mode", "path", "sha256", "size", "type"})
        path = _relative(entry["path"])
        encoded = os.fsencode(path)
        if previous is not None and encoded <= previous:
            raise ValueError
        previous = encoded
        if entry["type"] not in {"directory", "file"}:
            raise ValueError
        if entry["mode"] not in {"0444", "0555"}:
            raise ValueError
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise ValueError
        _digest(entry["sha256"])
        if entry["type"] == "directory" and (
            entry["mode"] != "0555" or entry["size"] != 0 or entry["sha256"] != _sha256_bytes(b"")
        ):
            raise ValueError
        result.append(dict(entry))
    return result


def _validate_static_document(
    document: object,
    *,
    expected_application_dependency_provenance: Mapping[str, object] = (
        PAPER_APPLICATION_DEPENDENCY_PROVENANCE
    ),
) -> dict[str, Any]:
    root = _exact(
        document,
        {
            "artifact_policy", "authority_kind", "binding_sha256", "build_tools",
            "command_manifest", "components", "database", "dependency_manifests",
            "external_verifier", "installation_root", "interpreters", "job_plane_policy",
            "lockfiles", "prior_release_sha256", "schema_version", "producer_bindings",
            "runtime_document_policy", "runtime_paths", "seal_version", "source", "stage",
            "units",
        },
    )
    if (
        root["schema_version"] != SCHEMA_VERSION
        or root["authority_kind"] != STATIC_KIND
        or root["seal_version"] != SEAL_VERSION
    ):
        raise ValueError
    if root["binding_sha256"] != _authority_binding(root):
        raise ValueError
    if root["artifact_policy"] != PAPER_ARTIFACT_POLICY:
        raise ValueError
    build_tools = _exact(root["build_tools"], {"uv"})
    if _exact(build_tools["uv"], {"identity", "sha256"}) != PAPER_UV_PROVENANCE:
        raise ValueError
    dependency_manifests = _exact(root["dependency_manifests"], {"application"})
    dependency_provenance = _validate_dependency_provenance(
        dependency_manifests["application"]
    )
    if dependency_provenance != expected_application_dependency_provenance:
        raise ValueError
    _digest(root["prior_release_sha256"])
    source = _exact(
        root["source"],
        {"binding_sha256", "commit", "commit_object_hex", "entries", "tree"},
    )
    commit, tree = _git_id(source["commit"]), _git_id(source["tree"])
    installation_root = _absolute(root["installation_root"])
    if installation_root != _installation_root(commit):
        raise ValueError
    if (
        root["runtime_paths"] != RUNTIME_PATHS
        or root["runtime_document_policy"] != RUNTIME_DOCUMENT_POLICY
        or root["producer_bindings"] != _producer_bindings(commit)
    ):
        raise ValueError

    components = _exact(root["components"], set(COMPONENT_PREFIXES))
    component_trees: dict[str, str] = {}
    for name in COMPONENT_PREFIXES:
        item = _exact(
            components[name],
            {"artifact_root", "artifact_set_sha256", "source_prefix", "source_tree"},
        )
        if (
            item["source_prefix"] != COMPONENT_PREFIXES[name]
            or item["artifact_root"] != COMPONENT_ARTIFACT_ROOTS[name]
        ):
            raise ValueError
        component_trees[name] = _git_id(item["source_tree"])
        _digest(item["artifact_set_sha256"])
    stage = _exact(
        root["stage"],
        {
            "entries", "file_set_sha256", "gid", "installation_gid",
            "installation_root_mode", "installation_uid", "path", "root_mode", "uid",
        },
    )
    _absolute(stage["path"])
    if (
        stage["root_mode"] != "0555"
        or type(stage["uid"]) is not int
        or type(stage["gid"]) is not int
        or stage["uid"] < 0
        or stage["gid"] < 0
        or stage["installation_uid"] != 0
        or stage["installation_gid"] != 0
        or stage["installation_root_mode"] != "0555"
    ):
        raise ValueError
    entries = _validate_entries(stage["entries"])
    if stage["file_set_sha256"] != _sha256_bytes(_fragment(entries)):
        raise ValueError
    by_path = _entry_map(entries)
    installed_dependencies = _installed_site_package_entries(by_path)
    if (
        dependency_provenance["file_count"] != len(installed_dependencies)
        or dependency_provenance["installed_file_set_sha256"]
        != _sha256_bytes(_fragment(installed_dependencies))
        or by_path.get("application/uv.lock", {}).get("sha256")
        != dependency_provenance["lock_sha256"]
    ):
        raise ValueError
    if any(
        path.startswith("units/") and (path.endswith(".timer") or ".wants/" in path)
        for path in by_path
    ):
        raise ValueError
    normalized_source, tree_ids = _validated_source_proof(source, by_path, expect_binding=True)
    if normalized_source != source:
        raise ValueError
    expected_component_trees = {
        name: tree_ids[""] if prefix == "." else tree_ids[prefix]
        for name, prefix in COMPONENT_PREFIXES.items()
    }
    if component_trees != expected_component_trees:
        raise ValueError
    for name in COMPONENT_PREFIXES:
        if components[name]["artifact_set_sha256"] != _artifact_digest(entries, COMPONENT_ARTIFACT_ROOTS[name]):
            raise ValueError

    lockfiles = _exact(root["lockfiles"], set(LOCK_PATHS))
    for name, expected_path in LOCK_PATHS.items():
        item = _exact(lockfiles[name], {"path", "sha256"})
        if item["path"] != expected_path or by_path.get(expected_path, {}).get("sha256") != _digest(item["sha256"]):
            raise ValueError

    interpreters = _exact(root["interpreters"], {"application_python", "backend_python"})
    for name, expected_path in (
        ("application_python", "application/.venv/bin/python3.11"),
        ("backend_python", "backend/.venv/bin/python3.11"),
    ):
        item = _exact(
            interpreters[name],
            {"identity", "path", "runtime_core_sha256", "sha256"},
        )
        if (
            item["path"] != expected_path
            or _PYTHON_ID.fullmatch(item["identity"] if isinstance(item["identity"], str) else "") is None
            or item["runtime_core_sha256"]
            != PAPER_PYTHON_RUNTIME_PROVENANCE["normalized_core_sha256"]
            or by_path.get(expected_path, {}).get("sha256") != _digest(item["sha256"])
            or by_path.get(expected_path, {}).get("mode") != "0555"
        ):
            raise ValueError
    if root["job_plane_policy"] != JOB_PLANE_POLICY:
        raise ValueError

    database = _exact(
        root["database"],
        {"expected_revision", *EXPECTED_DB_ROLES},
    )
    if database != {
        "expected_revision": EXPECTED_DATABASE_REVISION,
        **EXPECTED_DB_ROLES,
    }:
        raise ValueError

    if root["command_manifest"] != _command_manifest(installation_root):
        raise ValueError

    units = _exact(root["units"], set(UNIT_NAMES))
    expected_specs = _unit_specs(installation_root)
    expected_units = render_candidate_units(installation_root)
    for name in UNIT_NAMES:
        item = _exact(
            units[name],
            {
                "argv", "credential_references", "database_role", "enabled_by_default",
                "path", "service_group", "service_user", "sha256", "working_directory",
            },
        )
        expected = {
            **expected_specs[name],
            "enabled_by_default": False,
            "path": f"units/{name}",
            "sha256": _sha256_bytes(expected_units[name]),
        }
        if item != expected or by_path.get(expected["path"], {}).get("sha256") != expected["sha256"]:
            raise ValueError

    verifier = _exact(
        root["external_verifier"],
        {"gid", "installation_path", "mode", "sha256", "source_path", "uid"},
    )
    _absolute(verifier["source_path"])
    installation_verifier = _absolute(verifier["installation_path"])
    if (
        type(verifier["uid"]) is not int
        or type(verifier["gid"]) is not int
        or verifier["uid"] != 0
        or verifier["gid"] != 0
        or verifier["mode"] != "0555"
        or installation_verifier != EXTERNAL_VERIFIER_INSTALLATION_PATH
    ):
        raise ValueError
    _digest(verifier["sha256"])
    return root


def parse_static_release_authority_v2(
    raw: bytes,
    *,
    test_expected_application_dependency_provenance: Mapping[str, object] | None = None,
) -> StaticReleaseAuthorityV2:
    try:
        document = _validate_static_document(
            _load_canonical(raw),
            expected_application_dependency_provenance=(
                test_expected_application_dependency_provenance
                or PAPER_APPLICATION_DEPENDENCY_PROVENANCE
            ),
        )
        return StaticReleaseAuthorityV2(
            digest=_sha256_bytes(raw),
            source_commit=document["source"]["commit"],
            source_tree=document["source"]["tree"],
            stage_path=Path(document["stage"]["path"]),
            installation_root=Path(document["installation_root"]),
        )
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def verify_static_release_authority_v2(
    stage: Path,
    authority_raw: bytes,
    *,
    expected_digest: str,
    verifier_path: Path,
    content_copy: bool = False,
    test_fake_root_copy: bool = False,
    test_expected_python_runtime_core_sha256: str | None = None,
) -> bool:
    """Verify one complete sealed tree without executing any staged file."""

    try:
        expected = _digest(expected_digest)
        if _sha256_bytes(authority_raw) != expected:
            raise ValueError
        loaded_document = _load_canonical(authority_raw)
        expected_dependency_provenance: Mapping[str, object] = (
            PAPER_APPLICATION_DEPENDENCY_PROVENANCE
        )
        if test_expected_python_runtime_core_sha256 is not None:
            if not isinstance(loaded_document, Mapping):
                raise ValueError
            dependency_manifests = loaded_document.get("dependency_manifests")
            if not isinstance(dependency_manifests, Mapping):
                raise ValueError
            application_dependency = dependency_manifests.get("application")
            if not isinstance(application_dependency, Mapping):
                raise ValueError
            expected_dependency_provenance = application_dependency
        document = _validate_static_document(
            loaded_document,
            expected_application_dependency_provenance=expected_dependency_provenance,
        )
        if test_fake_root_copy and not content_copy:
            raise ValueError
        uid, gid, entries = _walk_sealed_stage(stage)
        installation_root = Path(document["installation_root"])
        if not content_copy:
            if Path(document["stage"]["path"]) != stage:
                raise ValueError
            expected_uid, expected_gid = document["stage"]["uid"], document["stage"]["gid"]
        elif test_fake_root_copy:
            if os.geteuid() == 0 or stage == installation_root:
                raise ValueError
            expected_uid, expected_gid = os.geteuid(), os.getegid()
        else:
            if stage != installation_root:
                raise ValueError
            _safe_root_ancestors(stage)
            expected_uid = document["stage"]["installation_uid"]
            expected_gid = document["stage"]["installation_gid"]
        if (
            entries != document["stage"]["entries"]
            or uid != expected_uid
            or gid != expected_gid
        ):
            raise ValueError
        verifier = document["external_verifier"]
        source_verifier = Path(verifier["source_path"])
        installed_verifier = Path(verifier["installation_path"])
        if verifier_path == source_verifier:
            verifier_info, verifier_sha = _safe_external_file(source_verifier, executable=True)
        elif verifier_path == installed_verifier:
            verifier_info, verifier_sha = _safe_root_executable(installed_verifier)
            if verifier_info.st_uid != verifier["uid"] or verifier_info.st_gid != verifier["gid"]:
                raise ValueError
        else:
            raise ValueError
        if stat.S_IMODE(verifier_info.st_mode) != 0o555 or verifier_sha != verifier["sha256"]:
            raise ValueError
        if document["database"] != {
            "expected_revision": EXPECTED_DATABASE_REVISION,
            **EXPECTED_DB_ROLES,
        }:
            raise ValueError
        _verify_source_blobs(stage, document["source"])
        inspect_python_runtime(
            stage / "application/.venv",
            expected_core_sha256=test_expected_python_runtime_core_sha256,
            require_empty_site_packages=False,
        )
        inspect_paper_application_artifact(
            stage / "application",
            test_expected_python_runtime_core_sha256=(
                test_expected_python_runtime_core_sha256
            ),
        )
        inspect_paper_backend_artifact(
            stage / "backend",
            document["command_manifest"],
            test_expected_python_runtime_core_sha256=(
                test_expected_python_runtime_core_sha256
            ),
        )
        expected_units = render_candidate_units(Path(document["installation_root"]))
        for name, raw in expected_units.items():
            if (stage / "units" / name).read_bytes() != raw:
                raise ValueError
        return True
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def build_release_activation_v2(*_args: object, **_kwargs: object) -> None:
    """Fail closed until immutable promotion and rotating evidence are separate."""

    raise ReleaseAuthorityV2Error()


def parse_release_activation_v2(*_args: object, **_kwargs: object) -> None:
    """Fail closed: the legacy combined activation schema is not runtime authority."""

    raise ReleaseAuthorityV2Error()


def _normalize_private_directories(root: Path) -> None:
    for path in (root, *(candidate for candidate in root.rglob("*") if candidate.is_dir())):
        if not path.is_symlink():
            path.chmod(0o700)


def _write_exclusive(path: Path, raw: bytes, mode: int) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        os.fchmod(descriptor, mode)
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ValueError
            remaining = remaining[written:]
        os.fsync(descriptor)
    except Exception:
        raise ReleaseAuthorityV2Error() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def construct_pinned_uv_tool(source: Path, destination: Path) -> None:
    """Copy only the code-owned uv bytes into a private build directory."""

    try:
        info, _ = _safe_external_file(source, executable=True)
        if info.st_uid not in {0, os.geteuid()} or info.st_gid not in {0, os.getegid()}:
            raise ValueError
        parent = destination.parent
        parent_info = parent.lstat()
        if (
            not destination.is_absolute()
            or destination.exists()
            or destination.is_symlink()
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid()
            or parent_info.st_gid != os.getegid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise ValueError
        _no_xattrs(parent)
        raw = source.read_bytes()
        if _sha256_bytes(raw) != PAPER_UV_PROVENANCE["sha256"]:
            raise ValueError
        _write_exclusive(destination, raw, 0o555)
        copied_info, copied_sha256 = _safe_external_file(destination, executable=True)
        if stat.S_IMODE(copied_info.st_mode) != 0o555 or copied_sha256 != PAPER_UV_PROVENANCE["sha256"]:
            raise ValueError
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _cli() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture-source-proof")
    capture.add_argument("--repo", type=Path, required=True)
    capture.add_argument("--commit", required=True)
    capture.add_argument("--output", type=Path, required=True)
    units = subparsers.add_parser("render-units")
    units.add_argument("--stage", type=Path, required=True)
    units.add_argument("--commit", required=True)
    project = subparsers.add_parser("project-paper-backend")
    project.add_argument("--source", type=Path, required=True)
    project.add_argument("--destination", type=Path, required=True)
    project_application = subparsers.add_parser("project-paper-application")
    project_application.add_argument("--source", type=Path, required=True)
    project_application.add_argument("--destination", type=Path, required=True)
    install_application_path = subparsers.add_parser(
        "install-paper-application-import-path"
    )
    install_application_path.add_argument("--application", type=Path, required=True)
    project_runtime = subparsers.add_parser("project-python-runtime-archive")
    project_runtime.add_argument("--archive", type=Path, required=True)
    project_runtime.add_argument("--destination", type=Path, required=True)
    project_uv = subparsers.add_parser("project-pinned-uv")
    project_uv.add_argument("--source", type=Path, required=True)
    project_uv.add_argument("--destination", type=Path, required=True)
    verify_runtime = subparsers.add_parser("verify-python-runtime")
    verify_runtime.add_argument("--runtime", type=Path, required=True)
    verify_runtime.add_argument("--allow-site-packages", action="store_true")
    compose = subparsers.add_parser("compose")
    compose.add_argument("--stage", type=Path, required=True)
    compose.add_argument("--source-proof", type=Path, required=True)
    compose.add_argument("--application-python-identity", required=True)
    compose.add_argument("--backend-python-identity", required=True)
    compose.add_argument("--external-verifier", type=Path, required=True)
    compose.add_argument("--application-dependency-manifest", type=Path, required=True)
    compose.add_argument("--prior-release-sha256", required=True)
    compose.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "capture-source-proof":
            output = _absolute(str(arguments.output))
            proof = capture_source_proof_v2(arguments.repo, arguments.commit)
            _write_exclusive(output, canonical_json_bytes(proof), 0o444)
            return 0
        if arguments.command == "render-units":
            commit = _git_id(arguments.commit)
            directory = arguments.stage / "units"
            directory.mkdir(mode=0o755, parents=True, exist_ok=False)
            for name, raw in render_candidate_units(_installation_root(commit)).items():
                _write_exclusive(directory / name, raw, 0o644)
            return 0
        if arguments.command == "project-paper-backend":
            construct_paper_backend_artifact(arguments.source, arguments.destination)
            return 0
        if arguments.command == "project-paper-application":
            construct_paper_application_artifact(arguments.source, arguments.destination)
            return 0
        if arguments.command == "install-paper-application-import-path":
            install_paper_application_import_path(arguments.application)
            return 0
        if arguments.command == "project-python-runtime-archive":
            construct_python_runtime_archive(arguments.archive, arguments.destination)
            return 0
        if arguments.command == "project-pinned-uv":
            construct_pinned_uv_tool(arguments.source, arguments.destination)
            return 0
        if arguments.command == "verify-python-runtime":
            verify_python_runtime_execution(arguments.runtime)
            inspect_python_runtime(
                arguments.runtime,
                require_empty_site_packages=not arguments.allow_site_packages,
            )
            return 0
        document, _ = build_static_release_authority_v2(
            arguments.stage,
            source_proof=_load_canonical(arguments.source_proof.read_bytes()),
            application_python_identity=arguments.application_python_identity,
            backend_python_identity=arguments.backend_python_identity,
            external_verifier=arguments.external_verifier,
            application_dependency_manifest=arguments.application_dependency_manifest,
            prior_release_sha256=arguments.prior_release_sha256,
        )
        _write_exclusive(arguments.output, canonical_json_bytes(document), 0o444)
        return 0
    except Exception:
        print("release authority v2 rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = [
    "EXPECTED_DATABASE_REVISION", "EXTERNAL_VERIFIER_INSTALLATION_PATH", "JOB_PLANE_POLICY",
    "PAPER_ARTIFACT_CLASS", "PAPER_ARTIFACT_POLICY", "PAPER_BACKEND_ENTRYPOINT",
    "PAPER_APPLICATION_SOURCE_MAPPING", "PAPER_APPLICATION_SOURCE_PATHS",
    "PAPER_BACKEND_SOURCE_MAPPING", "PAPER_BACKEND_SOURCE_PATHS", "PAPER_FORCED_ENVIRONMENT",
    "PAPER_PYTHON_RUNTIME_PROVENANCE", "PAPER_RUNTIME_MANIFEST",
    "ReleaseAuthorityV2Error", "SCHEMA_VERSION", "SEAL_VERSION", "STATIC_KIND",
    "StaticReleaseAuthorityV2", "build_static_release_authority_v2", "canonical_json_bytes",
    "capture_source_proof_v2", "construct_paper_application_artifact",
    "construct_paper_backend_artifact", "construct_python_runtime",
    "construct_python_runtime_archive",
    "inspect_paper_application_artifact", "inspect_paper_backend_artifact",
    "inspect_python_runtime", "paper_command_manifest",
    "python_runtime_core_sha256",
    "parse_static_release_authority_v2", "render_candidate_units",
    "verify_python_runtime_execution", "verify_static_release_authority_v2",
]
