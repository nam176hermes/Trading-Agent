#!/usr/bin/python3
"""Standalone standard-library verifier for a sealed Release Authority v2 stage."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, cast


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
_PYTHON_ID = re.compile(r"CPython 3\.11\.\d+\Z")
_ROOT_KEYS = {
    "artifact_policy", "authority_kind", "binding_sha256", "build_tools", "command_manifest",
    "components", "database", "dependency_manifests", "external_verifier",
    "installation_root", "interpreters", "job_plane_policy", "lockfiles",
    "prior_release_sha256", "schema_version",
    "producer_bindings", "runtime_document_policy", "runtime_paths", "seal_version",
    "source", "stage", "units",
}
_COMPONENTS = {
    "application": (".", "application"),
    "backend": (".", "backend"),
}
_LOCKS = {
    "application": "application/uv.lock",
    "backend": "backend/paper_runtime_manifest.json",
}
_PAPER_PATHS = (
    "job_attribution.py", "paper_main.py", "paper_runtime_manifest.json",
    "provider_free_fixture.py", "research_semantics.py",
)
_PAPER_SOURCE_MAPPING = {
    "packages/runtime_release/paper_backend/job_attribution.py": "job_attribution.py",
    "packages/runtime_release/paper_backend/paper_main.py": "paper_main.py",
    "packages/runtime_release/paper_backend/paper_runtime_manifest.json": (
        "paper_runtime_manifest.json"
    ),
    "packages/runtime_release/paper_backend/provider_free_fixture.py": (
        "provider_free_fixture.py"
    ),
    "packages/runtime_release/paper_backend/research_semantics.py": (
        "research_semantics.py"
    ),
}
_PAPER_APPLICATION_SOURCE_MAPPING = {
    'apps/job_api/__init__.py': 'apps/job_api/__init__.py',
    'apps/job_api/app.py': 'apps/job_api/app.py',
    'apps/job_api/auth.py': 'apps/job_api/auth.py',
    'apps/job_api/config.py': 'apps/job_api/config.py',
    'apps/job_api/contracts.py': 'apps/job_api/contracts.py',
    'apps/job_api/errors.py': 'apps/job_api/errors.py',
    'apps/job_api/main.py': 'apps/job_api/main.py',
    'packages/__init__.py': 'packages/__init__.py',
    'packages/runtime_release/paper_application/job_contracts_init.py': 'packages/job_contracts/__init__.py',
    'packages/runtime_release/paper_application/job_contracts_api.py': 'packages/job_contracts/api.py',
    'packages/runtime_release/paper_application/job_contracts_enums.py': 'packages/job_contracts/enums.py',
    'packages/job_contracts/fingerprint.py': 'packages/job_contracts/fingerprint.py',
    'packages/runtime_release/paper_application/job_contracts_payloads.py': 'packages/job_contracts/payloads.py',
    'packages/job_contracts/transitions.py': 'packages/job_contracts/transitions.py',
    'packages/runtime_release/paper_application/runtime_release_init.py': 'packages/runtime_release/__init__.py',
    'packages/runtime_release/paper_application/runtime_release_config.py': 'packages/runtime_release/config.py',
    'packages/runtime_release/paper_application/runtime_release_job_plane.py': 'packages/runtime_release/job_plane.py',
    'packages/runtime_release/paper_application/runtime_release_semantic.py': 'packages/runtime_release/semantic.py',
    'packages/runtime_release/staging_v2.py': 'packages/runtime_release/staging_v2.py',
    'packages/safety_evidence.py': 'packages/safety_evidence.py',
    'packages/runtime_release/paper_application/pyproject.toml': 'pyproject.toml',
    'services/__init__.py': 'services/__init__.py',
    'services/job_store/__init__.py': 'services/job_store/__init__.py',
    'services/job_store/config.py': 'services/job_store/config.py',
    'services/job_store/errors.py': 'services/job_store/errors.py',
    'services/job_store/records.py': 'services/job_store/records.py',
    'services/job_store/repository.py': 'services/job_store/repository.py',
    'services/job_store/worker_repository.py': 'services/job_store/worker_repository.py',
    'services/job_worker/__init__.py': 'services/job_worker/__init__.py',
    'services/job_worker/artifacts.py': 'services/job_worker/artifacts.py',
    'packages/runtime_release/paper_application/command_registry.py': 'services/job_worker/command_registry.py',
    'services/job_worker/engine_spawn_interface.py': 'services/job_worker/engine_spawn_interface.py',
    'packages/runtime_release/paper_application/environment.py': 'services/job_worker/environment.py',
    'services/job_worker/errors.py': 'services/job_worker/errors.py',
    'services/job_worker/main.py': 'services/job_worker/main.py',
    'services/job_worker/process_runner.py': 'services/job_worker/process_runner.py',
    'services/job_worker/recovery.py': 'services/job_worker/recovery.py',
    'packages/runtime_release/paper_application/results.py': 'services/job_worker/results.py',
    'packages/runtime_release/paper_application/safety.py': 'services/job_worker/safety.py',
    'services/job_worker/safety_state.py': 'services/job_worker/safety_state.py',
    'services/job_worker/worker.py': 'services/job_worker/worker.py',
    'services/safety_state_exporter/__init__.py': 'services/safety_state_exporter/__init__.py',
    'packages/runtime_release/paper_application/safety_exporter.py': 'services/safety_state_exporter/exporter.py',
    'packages/runtime_release/paper_application/uv.lock': 'uv.lock',
}
_PAPER_APPLICATION_PATHS = tuple(sorted(_PAPER_APPLICATION_SOURCE_MAPPING.values()))
_PAPER_FORCED_ENVIRONMENT = {
    "LIVE_EXECUTION_ENABLED": "false",
    "LIVE_TRADING_APPROVED": "false",
    "LIVE_TRADING_ENABLED": "false",
    "TRADING_MODE": "paper",
}
_PAPER_STDLIB_IMPORTS = (
    "__future__", "dataclasses", "datetime", "hashlib", "hmac", "json", "math", "os",
    "pathlib", "re", "secrets", "stat", "sys", "types", "typing", "urllib",
)
_PAPER_PYTHON_RUNTIME_PROVENANCE = {
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
_EXPECTED_PYTHON_RUNTIME_CORE_SHA256 = (
    "39632162b32a97b4ccd3f3dd5f79d0735137f9247401835d1287b433dc83dcf7"
)
_PAPER_UV_PROVENANCE = {"identity": "uv 0.11.7 (x86_64-unknown-linux-gnu)", "sha256": "cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4"}
_APPLICATION_IMPORT_PATH = "application/.venv/lib/python3.11/site-packages/trading-agent-paper-application.pth"
_APPLICATION_IMPORT_PATH_BYTES = b"../../../..\n"
_EXPECTED_APPLICATION_DEPENDENCY_PROVENANCE = {"file_count": 546, "installed_file_set_sha256": "d5e97e6843205315334f0665badfd75e58ef6893af033ca9cbdd7155df89b1aa", "lock_sha256": "a4fac2d6f0587c534555e6d8c3ca9c22460ba18b09e5eb684c7b38409ce2d759", "manifest_sha256": "a98d670fe49964f71aabb9be3daaeb062412452329a72a6616e0f4f40681cba6", "provenance_file_set_sha256": "687b409c91b40ed2293e09bca8bab1d53779fb58425c8ab56d29e459ec209603", "schema_version": 1, "uv_sha256": "cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4", "wheel_count": 16, "wheelhouse_aggregate_sha256": "6871c43d484d58d6fd3b17c10357830fa4284cdcb6489968eaf3d4e348fc311d"}

_PYTHON_RUNTIME_CONFIG = (
    b"include-system-site-packages = false\n"
    b"version = 3.11.15\n"
)
_PAPER_POLICY = {
    "artifact_class": "CANONICAL_PAPER_V1",
    "backend_entrypoint": "paper_main.py",
    "backend_manifest": "backend/paper_runtime_manifest.json",
    "backend_source_allowlist": list(_PAPER_PATHS),
    "dependency_policy": "PYTHON_STDLIB_ONLY",
    "forced_child_environment": _PAPER_FORCED_ENVIRONMENT,
    "permitted_job_types": ["SNAPSHOT"],
    "python_runtime": _PAPER_PYTHON_RUNTIME_PROVENANCE,
    "stdlib_import_allowlist": list(_PAPER_STDLIB_IMPORTS),
}
_PAPER_RUNTIME_MANIFEST = {
    "artifact_class": "CANONICAL_PAPER_V1",
    "command_catalog": [{"entrypoint": "paper_main.py", "job_type": "SNAPSHOT", "shell": False}],
    "dependency_policy": "PYTHON_STDLIB_ONLY",
    "entrypoint": "paper_main.py",
    "forbidden_capabilities": [
        "BROKER_ADAPTER", "CREDENTIAL_LOADER", "EXCHANGE_ADAPTER_REGISTRY",
        "LIVE_EXECUTION", "MODE_TRANSITION", "REAL_ORDER_SUBMISSION", "WITHDRAWAL",
    ],
    "forced_environment": _PAPER_FORCED_ENVIRONMENT,
    "python_runtime": _PAPER_PYTHON_RUNTIME_PROVENANCE,
    "schema_version": 1,
    "source_allowlist": list(_PAPER_PATHS),
    "stdlib_import_allowlist": list(_PAPER_STDLIB_IMPORTS),
}
_UNITS = {"trading-job-api.service", "trading-job-worker.service"}
# Standalone-verifier pin. Tests require parity with the runtime canonical head.
_EXPECTED_DATABASE_REVISION = "0011_engine_backtest_worker_authority"
_EXPECTED_DB_ROLES = {
    "api_role": "trading_job_api",
    "worker_role": "trading_job_worker",
}
_VERIFIER_INSTALLATION_PATH = Path("/usr/libexec/trading-agent-v2/verify-stage.py")
_RUNTIME_PATHS = {
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
_RUNTIME_DOCUMENT_POLICY = {
    "activation": {"gid": 0, "mode": "0444", "publication": "CREATE_ONLY", "uid": 0},
    "safety_snapshot": {"gid": 0, "mode": "0444", "publication": "ATOMIC_ROTATING", "uid": 0},
    "semantic_active": {"gid": 0, "mode": "0444", "publication": "ATOMIC_ROTATING", "uid": 0},
    "static_authority": {"gid": 0, "mode": "0444", "publication": "CREATE_ONLY", "uid": 0},
}
_SAFETY_SOURCE_FINGERPRINT = "7e22249151c4e86661dae78d907d21818619ca0bed272f34725d33425d8bdb61"
_GIT_FILE_MODES = {"100644": "0444", "100755": "0555"}
_ALLOWED_STAGE_ADDITION_ROOTS = (
    "application/.venv", "backend/.venv",
)
_ALLOWED_UNIT_PATHS = {"units", *(f"units/{name}" for name in _UNITS)}


class Rejected(Exception):
    pass


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _fragment(value: object) -> bytes:
    return _canonical(value)[:-1]


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise Rejected
        result[key] = value
    return result


def _exact(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Rejected
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise Rejected
    return value


def _git_id(value: object) -> str:
    if not isinstance(value, str) or _GIT_ID.fullmatch(value) is None:
        raise Rejected
    return value


def _git_object_id(kind: str, raw: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest()


def _source_stage_path(source_path: str) -> str | None:
    if source_path in _PAPER_APPLICATION_SOURCE_MAPPING:
        return "application/" + _PAPER_APPLICATION_SOURCE_MAPPING[source_path]
    if source_path in _PAPER_SOURCE_MAPPING:
        return "backend/" + _PAPER_SOURCE_MAPPING[source_path]
    return None


def _allowed_stage_addition(path: str) -> bool:
    return path in _ALLOWED_UNIT_PATHS or any(
        path == root or path.startswith(root + "/")
        for root in _ALLOWED_STAGE_ADDITION_ROOTS
    )


def _git_tree_ids(entries: list[dict[str, object]]) -> dict[str, str]:
    tree: dict[str, object] = {}
    for entry in entries:
        current = tree
        parts = str(entry["source_path"]).split("/")
        for part in parts[:-1]:
            existing = current.get(part)
            if existing is None:
                child: dict[str, object] = {}
                current[part] = child
                current = child
            elif isinstance(existing, dict):
                current = existing
            else:
                raise Rejected
        if parts[-1] in current:
            raise Rejected
        current[parts[-1]] = (entry["mode"], entry["git_blob"])
    result: dict[str, str] = {}

    def seal(node: dict[str, object], prefix: str) -> str:
        material = bytearray()
        for name, value in sorted(
            node.items(), key=lambda item: os.fsencode(item[0] + ("/" if isinstance(item[1], dict) else "")),
        ):
            name_raw = name.encode("utf-8")
            if not name_raw or b"\0" in name_raw or b"/" in name_raw:
                raise Rejected
            if isinstance(value, dict):
                object_id = seal(value, f"{prefix}/{name}".strip("/"))
                mode = "40000"
            else:
                mode, object_id = cast(tuple[str, str], value)
            material.extend(f"{mode} ".encode("ascii") + name_raw + b"\0")
            material.extend(bytes.fromhex(str(object_id)))
        object_id = _git_object_id("tree", bytes(material))
        result[prefix] = object_id
        return object_id

    seal(tree, "")
    return result


def _validate_source(
    source_value: object,
    by_path: dict[str, dict[str, object]],
    stage: Path,
) -> tuple[dict[str, object], dict[str, str]]:
    source = _exact(
        source_value, {"binding_sha256", "commit", "commit_object_hex", "entries", "tree"},
    )
    commit = _git_id(source["commit"])
    tree = _git_id(source["tree"])
    commit_hex = source["commit_object_hex"]
    if not isinstance(commit_hex, str) or len(commit_hex) > 8 * 1024 * 1024:
        raise Rejected
    commit_object = bytes.fromhex(commit_hex)
    if (
        commit_object.hex() != commit_hex
        or _git_object_id("commit", commit_object) != commit
        or commit_object.partition(b"\n")[0] != f"tree {tree}".encode("ascii")
    ):
        raise Rejected
    if not isinstance(source["entries"], list) or not source["entries"]:
        raise Rejected
    entries: list[dict[str, object]] = []
    previous: bytes | None = None
    stage_paths: set[str] = set()
    source_directories = {"application", "backend"}
    for raw_entry in source["entries"]:
        entry = _exact(
            raw_entry, {"git_blob", "mode", "sha256", "size", "source_path", "stage_path"},
        )
        source_path = _relative(entry["source_path"])
        expected_stage_path = _source_stage_path(source_path)
        stage_path = None if entry["stage_path"] is None else _relative(entry["stage_path"])
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
            raise Rejected
        previous = encoded
        blob = _git_id(entry["git_blob"])
        digest = _digest(entry["sha256"])
        if stage_path is not None:
            stage_paths.add(stage_path)
            if by_path.get(stage_path) != {
                "mode": _GIT_FILE_MODES[str(entry["mode"])], "path": stage_path,
                "sha256": digest, "size": entry["size"], "type": "file",
            }:
                raise Rejected
            raw = (stage / stage_path).read_bytes()
            if len(raw) != entry["size"] or _sha_bytes(raw) != digest or _git_object_id("blob", raw) != blob:
                raise Rejected
            parent = PurePosixPath(stage_path).parent
            while parent.as_posix() != ".":
                source_directories.add(parent.as_posix())
                parent = parent.parent
        entries.append(
            {"git_blob": blob, "mode": entry["mode"], "sha256": digest, "size": entry["size"],
             "source_path": source_path, "stage_path": stage_path}
        )
    for path, entry in by_path.items():
        if entry["type"] == "file" and path not in stage_paths and not _allowed_stage_addition(path):
            raise Rejected
        if entry["type"] == "directory" and path not in source_directories and not _allowed_stage_addition(path):
            raise Rejected
    tree_ids = _git_tree_ids(entries)
    if tree_ids.get("") != tree or any(
        path not in tree_ids
        for path in (
            "legacy/research-backend",
            "packages/runtime_release/paper_application",
            "packages/runtime_release/paper_backend",
        )
    ):
        raise Rejected
    proof = {"commit": commit, "commit_object_hex": commit_hex, "entries": entries, "tree": tree}
    if source["binding_sha256"] != _sha_bytes(_fragment(proof)):
        raise Rejected
    return proof, tree_ids


def _absolute(value: object) -> Path:
    if not isinstance(value, str):
        raise Rejected
    pure = PurePosixPath(value)
    if not pure.is_absolute() or value.startswith("//") or ".." in pure.parts or pure.as_posix() != value:
        raise Rejected
    return Path(value)


def _relative(value: object) -> str:
    if not isinstance(value, str):
        raise Rejected
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts or pure.as_posix() != value:
        raise Rejected
    return value


def _sha_file(path: Path) -> str:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Rejected
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        ):
            raise Rejected
        return digest.hexdigest()
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _no_xattrs(path: Path) -> None:
    if os.listxattr(path, follow_symlinks=False):
        raise Rejected


def _safe_external(path: Path, uid: int, gid: int, digest: str) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise Rejected
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) not in {0o555, 0o755}
        or info.st_uid != uid
        or info.st_gid != gid
    ):
        raise Rejected
    _no_xattrs(path)
    if _sha_file(path) != digest:
        raise Rejected


def _safe_root_executable(path: Path, digest: str) -> None:
    _safe_external(path, 0, 0, digest)
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
            raise Rejected
        _no_xattrs(current)


def _safe_root_ancestors(path: Path) -> None:
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
            raise Rejected
        _no_xattrs(current)


def _walk(root: Path) -> tuple[int, int, list[dict[str, object]]]:
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise Rejected
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_IMODE(root_info.st_mode) != 0o555:
        raise Rejected
    _no_xattrs(root)
    entries: list[dict[str, object]] = []
    inodes: set[tuple[int, int]] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir(), key=lambda item: os.fsencode(item.name)):
            relative = _relative(child.relative_to(root).as_posix())
            if "__pycache__" in PurePosixPath(relative).parts or relative.endswith((".pyc", ".pyo")):
                raise Rejected
            info = child.lstat()
            if info.st_uid != root_info.st_uid or info.st_gid != root_info.st_gid:
                raise Rejected
            _no_xattrs(child)
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o555:
                    raise Rejected
                entries.append(
                    {"mode": "0555", "path": relative, "sha256": _sha_bytes(b""), "size": 0, "type": "directory"}
                )
                pending.append(child)
            elif stat.S_ISREG(info.st_mode):
                mode = stat.S_IMODE(info.st_mode)
                identity = (info.st_dev, info.st_ino)
                if mode not in {0o444, 0o555} or info.st_nlink != 1 or identity in inodes:
                    raise Rejected
                inodes.add(identity)
                entries.append(
                    {
                        "mode": f"{mode:04o}", "path": relative, "sha256": _sha_file(child),
                        "size": info.st_size, "type": "file",
                    }
                )
            else:
                raise Rejected
    entries.sort(key=lambda item: os.fsencode(str(item["path"])))
    return root_info.st_uid, root_info.st_gid, entries


def _excluded_python_runtime_core_path(relative: str) -> bool:
    pure = PurePosixPath(relative)
    return (
        relative in {"pyvenv.cfg", "runtime-provenance.json"}
        or "__pycache__" in pure.parts
        or "site-packages" in pure.parts
        or pure.suffix in {".pyc", ".pyo"}
        or (pure.parts[0] == "bin" and relative != "bin/python3.11")
    )


def _python_runtime_core_sha256(runtime_root: Path) -> str:
    root = runtime_root.resolve(strict=True)
    if root != runtime_root or not root.is_dir() or root.is_symlink():
        raise Rejected
    entries: list[list[object]] = []
    for path in sorted(
        root.rglob("*"),
        key=lambda item: os.fsencode(item.relative_to(root).as_posix()),
    ):
        relative = path.relative_to(root).as_posix()
        if _excluded_python_runtime_core_path(relative):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Rejected
        entries.append([
            relative,
            info.st_size,
            _sha_file(path),
            bool(info.st_mode & 0o111),
        ])
    if not entries or not any(item[0] == "bin/python3.11" for item in entries):
        raise Rejected
    return _sha_bytes(_fragment(entries))


def _inspect_python_runtime(
    root: Path,
    *,
    require_empty_site_packages: bool,
) -> None:
    root = root.resolve(strict=True)
    for path in root.rglob("*"):
        info = path.lstat()
        pure = PurePosixPath(path.relative_to(root).as_posix())
        if (
            stat.S_ISLNK(info.st_mode)
            or (
                not stat.S_ISDIR(info.st_mode)
                and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
            )
            or "__pycache__" in pure.parts
            or pure.suffix in {".pyc", ".pyo"}
        ):
            raise Rejected
    if {path.name for path in (root / "bin").iterdir()} != {"python3.11"}:
        raise Rejected
    if (root / "pyvenv.cfg").read_bytes() != _PYTHON_RUNTIME_CONFIG:
        raise Rejected
    provenance_raw = (root / "runtime-provenance.json").read_bytes()
    provenance = json.loads(provenance_raw, object_pairs_hook=_pairs)
    if (
        not isinstance(provenance, dict)
        or provenance_raw != _canonical(provenance)
        or provenance != _PAPER_PYTHON_RUNTIME_PROVENANCE
    ):
        raise Rejected
    site_packages = root / "lib/python3.11/site-packages"
    if not site_packages.is_dir() or site_packages.is_symlink():
        raise Rejected
    if require_empty_site_packages and any(site_packages.iterdir()):
        raise Rejected
    if _python_runtime_core_sha256(root) != _EXPECTED_PYTHON_RUNTIME_CORE_SHA256:
        raise Rejected


def _execute_python_runtime_probe(runtime_root: Path) -> None:
    root = runtime_root.resolve(strict=True)
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
        observed.get("identity") != _PAPER_PYTHON_RUNTIME_PROVENANCE["identity"]
        or not isinstance(observed.get("prefixes"), list)
        or len(observed["prefixes"]) != 5
    ):
        raise Rejected
    for value in observed["prefixes"]:
        resolved = Path(value).resolve(strict=True)
        if resolved != root and root not in resolved.parents:
            raise Rejected


def _inspect_paper_application(
    stage: Path,
    by_path: dict[str, dict[str, object]],
) -> None:
    forbidden_site_packages = {"alembic", "greenlet", "mako", "sqlalchemy"}
    site_prefix = "application/.venv/lib/python3.11/site-packages/"
    import_path_entry = by_path.get(_APPLICATION_IMPORT_PATH)
    import_path_files = {
        path
        for path, entry in by_path.items()
        if path.startswith(site_prefix)
        and path.endswith(".pth")
        and entry["type"] == "file"
    }
    if (
        import_path_files != {_APPLICATION_IMPORT_PATH}
        or import_path_entry
        != {
            "mode": "0444",
            "path": _APPLICATION_IMPORT_PATH,
            "sha256": _sha_bytes(_APPLICATION_IMPORT_PATH_BYTES),
            "size": len(_APPLICATION_IMPORT_PATH_BYTES),
            "type": "file",
        }
        or (stage / _APPLICATION_IMPORT_PATH).read_bytes()
        != _APPLICATION_IMPORT_PATH_BYTES
    ):
        raise Rejected
    for path in by_path:
        if not path.startswith(site_prefix):
            continue
        top_level = path.removeprefix(site_prefix).split("/", 1)[0].casefold().replace("_", "-")
        if any(
            top_level == package or top_level.startswith(package + "-")
            for package in forbidden_site_packages
        ):
            raise Rejected
    application_files = {
        path.removeprefix("application/")
        for path, entry in by_path.items()
        if entry["type"] == "file"
        and path.startswith("application/")
        and not path.startswith("application/.venv/")
    }
    if application_files != set(_PAPER_APPLICATION_PATHS):
        raise Rejected
    forbidden_fragments = (
        b"ReplayPayload",
        b"SafetyMode.LIVE",
        b"_MODE_LIVE",
        b"scheduler_timer",
        b"TRADING_RESEARCH_OPENAI_API_KEY",
        b"TRADING_RESEARCH_ANTHROPIC_API_KEY",
        b"alpaca",
        b"ccxt",
        b"create_order",
        b"fetch_balance",
        b"fetch_my_trades",
        b"withdraw(",
        b"--apply",
        b"shell=True",
    )
    forbidden_symbols = {
        "create_order",
        "execute_live",
        "get_exchange",
        "get_exchange_credentials",
        "load_dotenv",
        "mode_file",
        "place_order",
        "set_mode",
    }
    for relative in sorted(application_files, key=os.fsencode):
        path = stage / "application" / relative
        raw = path.read_bytes()
        lowered = raw.lower()
        if any(fragment.lower() in lowered for fragment in forbidden_fragments):
            raise Rejected
        if not relative.endswith(".py"):
            continue
        tree = ast.parse(raw.decode("utf-8"), filename=relative)
        for node in ast.walk(tree):
            imports = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else []
            )
            if any(name.split(".")[0] in {"alpaca", "ccxt", "dotenv"} for name in imports):
                raise Rejected
            if (
                relative != "services/job_worker/process_runner.py"
                and any(name.split(".")[0] == "subprocess" for name in imports)
            ):
                raise Rejected
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in forbidden_symbols
            ):
                raise Rejected
            if not isinstance(node, ast.Call):
                continue
            called = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if called in forbidden_symbols:
                raise Rejected
            if isinstance(node.func, ast.Name) and called in {
                "__import__",
                "compile",
                "eval",
                "exec",
            }:
                raise Rejected
            if isinstance(node.func, ast.Attribute):
                owner = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                if (owner, node.func.attr) in {
                    ("importlib", "import_module"),
                    ("os", "system"),
                }:
                    raise Rejected
                if owner == "subprocess" and node.func.attr in {"Popen", "call", "run"}:
                    if (
                        relative != "services/job_worker/process_runner.py"
                        or node.func.attr != "Popen"
                    ):
                        raise Rejected
            if any(
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            ):
                raise Rejected


def _artifact_digest(entries: list[dict[str, object]], root: str) -> str:
    selected = [item for item in entries if item["path"] == root or str(item["path"]).startswith(root + "/")]
    if not selected:
        raise Rejected
    return _sha_bytes(_fragment(selected))


def _binding(document: dict[str, Any]) -> str:
    material = deepcopy(document)
    material.pop("binding_sha256", None)
    return _sha_bytes(_fragment(material))


def _producer_bindings(commit: str) -> dict[str, str]:
    material = {
        "active_authority_path": _RUNTIME_PATHS["semantic_active"],
        "backend_commit": commit,
        "classification": "READ_ONLY_EXTERNAL_INPUT",
        "command": "SNAPSHOT",
        "input_root": _RUNTIME_PATHS["semantic_input_root"],
        "schema_version": "release-v2-semantic-policy/v1",
    }
    return {
        "safety_exporter_commit": commit,
        "safety_source_fingerprint": _SAFETY_SOURCE_FINGERPRINT,
        "semantic_backend_commit": commit,
        "semantic_policy_sha256": _sha_bytes(_fragment(material)),
    }


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
    raise Rejected


def _render_unit(name: str, spec: dict[str, object]) -> bytes:
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
        raise Rejected
    credential_lines = "".join(
        f"LoadCredential={credential_name}:{credential_path}\n"
        for credential_name, credential_path in sorted(credential_references.items())
    )
    return (
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
    ).encode("utf-8")


def _load(path: Path, expected_digest: str) -> tuple[bytes, dict[str, Any]]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o444
        or info.st_size > 64 * 1024 * 1024
    ):
        raise Rejected
    _no_xattrs(path)
    raw = path.read_bytes()
    if _sha_bytes(raw) != expected_digest:
        raise Rejected
    document = json.loads(raw, object_pairs_hook=_pairs)
    if not isinstance(document, dict) or raw != _canonical(document):
        raise Rejected
    return raw, document


def verify(
    stage: Path,
    authority: Path,
    expected_digest: str,
    *,
    content_copy: bool,
    test_fake_root_copy: bool,
    verifier_copy_of: Path | None,
) -> None:
    _digest(expected_digest)
    _, document = _load(authority, expected_digest)
    root = _exact(document, _ROOT_KEYS)
    if (
        root["schema_version"] != 3
        or root["authority_kind"] != "STATIC_RELEASE"
        or root["seal_version"] != 3
        or root["binding_sha256"] != _binding(root)
        or root["artifact_policy"] != _PAPER_POLICY
    ):
        raise Rejected
    _digest(root["prior_release_sha256"])
    source = _exact(
        root["source"], {"binding_sha256", "commit", "commit_object_hex", "entries", "tree"},
    )
    commit = _git_id(source["commit"])
    _git_id(source["tree"])
    install_root = _absolute(root["installation_root"])
    if install_root != Path(f"/opt/trading-agent-v2/releases/{commit}"):
        raise Rejected
    if (
        root["runtime_paths"] != _RUNTIME_PATHS
        or root["runtime_document_policy"] != _RUNTIME_DOCUMENT_POLICY
        or root["producer_bindings"] != _producer_bindings(commit)
    ):
        raise Rejected
    stage_doc = _exact(
        root["stage"],
        {
            "entries", "file_set_sha256", "gid", "installation_gid",
            "installation_root_mode", "installation_uid", "path", "root_mode", "uid",
        },
    )
    stage_path = _absolute(stage_doc["path"])
    if test_fake_root_copy and not content_copy:
        raise Rejected

    if (
        stage_doc["root_mode"] != "0555"
        or type(stage_doc["uid"]) is not int
        or type(stage_doc["gid"]) is not int
        or stage_doc["installation_uid"] != 0
        or stage_doc["installation_gid"] != 0
        or stage_doc["installation_root_mode"] != "0555"
    ):
        raise Rejected
    uid, gid, entries = _walk(stage)
    if not content_copy:
        if stage_path != stage:
            raise Rejected
        expected_uid, expected_gid = stage_doc["uid"], stage_doc["gid"]
    elif test_fake_root_copy:
        if os.geteuid() == 0 or stage == install_root:
            raise Rejected
        expected_uid, expected_gid = os.geteuid(), os.getegid()
    else:
        if stage != install_root:
            raise Rejected
        _safe_root_ancestors(stage)
        expected_uid, expected_gid = stage_doc["installation_uid"], stage_doc["installation_gid"]
    if uid != expected_uid or gid != expected_gid or entries != stage_doc["entries"]:
        raise Rejected
    if stage_doc["file_set_sha256"] != _sha_bytes(_fragment(entries)):
        raise Rejected
    by_path = {str(item["path"]): item for item in entries}
    build_tools = _exact(root["build_tools"], {"uv"})
    if _exact(build_tools["uv"], {"identity", "sha256"}) != _PAPER_UV_PROVENANCE:
        raise Rejected
    dependency_manifests = _exact(root["dependency_manifests"], {"application"})
    dependency = _exact(
        dependency_manifests["application"],
        {
            "file_count", "installed_file_set_sha256", "lock_sha256", "manifest_sha256",
            "provenance_file_set_sha256", "schema_version", "uv_sha256", "wheel_count",
            "wheelhouse_aggregate_sha256",
        },
    )
    if dependency != _EXPECTED_APPLICATION_DEPENDENCY_PROVENANCE:
        raise Rejected
    installed_prefix = "application/.venv/lib/python3.11/site-packages/"
    installed_dependencies = [
        {
            "path": path.removeprefix(installed_prefix),
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for path, item in sorted(by_path.items(), key=lambda pair: os.fsencode(pair[0]))
        if path.startswith(installed_prefix)
        and path != _APPLICATION_IMPORT_PATH
        and item["type"] == "file"
    ]
    if (
        dependency["file_count"] != len(installed_dependencies)
        or dependency["installed_file_set_sha256"] != _sha_bytes(_fragment(installed_dependencies))
        or by_path.get("application/uv.lock", {}).get("sha256") != dependency["lock_sha256"]
    ):
        raise Rejected
    _, source_tree_ids = _validate_source(source, by_path, stage)
    authority_info = authority.lstat()
    expected_authority_owner = (expected_uid, expected_gid)
    if (authority_info.st_uid, authority_info.st_gid) != expected_authority_owner:
        raise Rejected

    components = _exact(root["components"], set(_COMPONENTS))
    source_components: dict[str, str] = {}
    for name, (prefix, artifact_root) in _COMPONENTS.items():
        item = _exact(components[name], {"artifact_root", "artifact_set_sha256", "source_prefix", "source_tree"})
        if (
            item["source_prefix"] != prefix
            or item["artifact_root"] != artifact_root
            or item["artifact_set_sha256"] != _artifact_digest(entries, artifact_root)
        ):
            raise Rejected
        source_components[name] = _git_id(item["source_tree"])
    if source_components != {
        name: source_tree_ids[""] if prefix == "." else source_tree_ids[prefix]
        for name, (prefix, _) in _COMPONENTS.items()
    }:
        raise Rejected

    locks = _exact(root["lockfiles"], set(_LOCKS))
    for name, path in _LOCKS.items():
        item = _exact(locks[name], {"path", "sha256"})
        if item["path"] != path or by_path.get(path, {}).get("sha256") != _digest(item["sha256"]):
            raise Rejected
    database = _exact(
        root["database"],
        {"expected_revision", *_EXPECTED_DB_ROLES},
    )
    if database != {
        "expected_revision": _EXPECTED_DATABASE_REVISION,
        **_EXPECTED_DB_ROLES,
    }:
        raise Rejected
    if root["job_plane_policy"] != {
        "allowed_job_types": ["SNAPSHOT"],
        "scheduler_timer_enabled": False,
        "worker_concurrency": 1,
        "worker_lease_seconds": 600,
    }:
        raise Rejected
    interpreters = _exact(root["interpreters"], {"application_python", "backend_python"})
    for name, path in (
        ("application_python", "application/.venv/bin/python3.11"),
        ("backend_python", "backend/.venv/bin/python3.11"),
    ):
        item = _exact(
            interpreters[name],
            {"identity", "path", "runtime_core_sha256", "sha256"},
        )
        if (
            item["path"] != path
            or _PYTHON_ID.fullmatch(item["identity"] if isinstance(item["identity"], str) else "") is None
            or item["runtime_core_sha256"]
            != _PAPER_PYTHON_RUNTIME_PROVENANCE["normalized_core_sha256"]
            or by_path.get(path, {}).get("mode") != "0555"
            or by_path.get(path, {}).get("sha256") != _digest(item["sha256"])
        ):
            raise Rejected
    _inspect_python_runtime(
        stage / "application/.venv",
        require_empty_site_packages=False,
    )
    _inspect_python_runtime(
        stage / "backend/.venv",
        require_empty_site_packages=True,
    )
    _inspect_paper_application(stage, by_path)
    command = _exact(root["command_manifest"], {"commands", "manifest_sha256", "schema_version"})
    if command["schema_version"] != 3 or not isinstance(command["commands"], list) or len(command["commands"]) != 1:
        raise Rejected
    snapshot = _exact(command["commands"][0], {"argv", "cwd", "environment_policy", "executable", "job_type", "shell"})
    backend = install_root / "backend"
    executable = backend / ".venv/bin/python3.11"
    if snapshot != {
        "argv": [str(executable), "-I", "-B", "paper_main.py"],
        "cwd": str(backend),
        "environment_policy": "CANONICAL_PAPER_CHILD_V1",
        "executable": str(executable),
        "job_type": "SNAPSHOT",
        "shell": False,
    } or command["manifest_sha256"] != _sha_bytes(_fragment(command["commands"])):
        raise Rejected
    paper_files = {
        path.removeprefix("backend/")
        for path, entry in by_path.items()
        if entry["type"] == "file"
        and path.startswith("backend/")
        and not path.startswith("backend/.venv/")
    }
    if paper_files != set(_PAPER_PATHS):
        raise Rejected
    manifest_path = stage / "backend/paper_runtime_manifest.json"
    manifest = json.loads(
        manifest_path.read_bytes(),
        object_pairs_hook=_pairs,
    )
    if manifest != _PAPER_RUNTIME_MANIFEST:
        raise Rejected
    forbidden_symbols = {
        "create_order", "execute_live", "get_exchange", "get_exchange_credentials",
        "load_dotenv", "mode_file", "place_order", "set_mode",
    }
    forbidden_dynamic_calls = {"__import__", "compile", "eval", "exec"}
    local_modules = {
        PurePosixPath(path).stem
        for path in _PAPER_PATHS
        if path.endswith(".py")
    }
    allowed_imports = set(_PAPER_STDLIB_IMPORTS).union(local_modules)
    for relative in paper_files:
        if not relative.endswith(".py"):
            continue
        tree = ast.parse((stage / "backend" / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            imports = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else []
            )
            if any(name.split(".")[0] not in allowed_imports for name in imports):
                raise Rejected
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in forbidden_symbols:
                raise Rejected
            if isinstance(node, ast.Call):
                called = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if called in forbidden_symbols:
                    raise Rejected
                if isinstance(node.func, ast.Name) and called in forbidden_dynamic_calls:
                    raise Rejected
                if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                    raise Rejected
    units = _exact(root["units"], _UNITS)
    application = install_root / "application"
    app_python = application / ".venv/bin/python3.11"
    expected_unit_fields = {
        "trading-job-api.service": {
            "argv": [str(app_python), "-I", "-m", "apps.job_api.main"],
            "credential_references": _credential_references(
                "trading-job-api.service"
            ),
            "database_role": "trading_job_api",
            "service_group": "trading-job-api",
            "service_user": "trading-job-api",
            "working_directory": str(application),
        },
        "trading-job-worker.service": {
            "argv": [str(app_python), "-I", "-m", "services.job_worker.main"],
            "credential_references": _credential_references(
                "trading-job-worker.service"
            ),
            "database_role": "trading_job_worker",
            "service_group": "trading-job-worker",
            "service_user": "trading-job-worker",
            "working_directory": str(application),
        },
    }
    for name in _UNITS:
        item = _exact(
            units[name],
            {
                "argv", "credential_references", "database_role", "enabled_by_default", "path",
                "service_group", "service_user", "sha256", "working_directory",
            },
        )
        if (
            item["enabled_by_default"] is not False
            or item["path"] != f"units/{name}"
            or any(item[key] != value for key, value in expected_unit_fields[name].items())
        ):
            raise Rejected
        expected_raw = _render_unit(name, expected_unit_fields[name])
        if (
            item["sha256"] != _sha_bytes(expected_raw)
            or by_path.get(item["path"], {}).get("sha256") != _digest(item["sha256"])
            or (stage / str(item["path"])).read_bytes() != expected_raw
        ):
            raise Rejected
    if any(
        path.startswith("units/") and (path.endswith(".timer") or ".wants/" in path)
        for path in by_path
    ):
        raise Rejected

    verifier = _exact(
        root["external_verifier"],
        {"gid", "installation_path", "mode", "sha256", "source_path", "uid"},
    )
    source_verifier = _absolute(verifier["source_path"])
    installed_verifier = _absolute(verifier["installation_path"])
    if (
        installed_verifier != _VERIFIER_INSTALLATION_PATH
        or verifier["uid"] != 0
        or verifier["gid"] != 0
        or verifier["mode"] != "0555"
    ):
        raise Rejected
    running_verifier = Path(__file__).resolve()
    if verifier_copy_of is None:
        if running_verifier == source_verifier:
            info = running_verifier.lstat()
            _safe_external(running_verifier, info.st_uid, info.st_gid, _digest(verifier["sha256"]))
            if stat.S_IMODE(info.st_mode) != 0o555:
                raise Rejected
        elif running_verifier == installed_verifier:
            _safe_root_executable(running_verifier, _digest(verifier["sha256"]))
            if stat.S_IMODE(running_verifier.lstat().st_mode) != 0o555:
                raise Rejected
        else:
            raise Rejected
    else:
        if verifier_copy_of not in {source_verifier, installed_verifier}:
            raise Rejected
        _safe_external(running_verifier, os.geteuid(), os.getegid(), _digest(verifier["sha256"]))
        if stat.S_IMODE(running_verifier.lstat().st_mode) != 0o555:
            raise Rejected
    _execute_python_runtime_probe(stage / "application/.venv")
    _execute_python_runtime_probe(stage / "backend/.venv")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", type=Path)
    parser.add_argument("authority", type=Path)
    parser.add_argument("--expected-authority-sha256", required=True)
    parser.add_argument("--content-copy", action="store_true")
    parser.add_argument("--test-fake-root-copy", action="store_true")

    parser.add_argument("--verifier-copy-of", type=Path)
    args = parser.parse_args()
    try:
        verify(
            args.stage,
            args.authority,
            args.expected_authority_sha256,
            content_copy=args.content_copy,
            test_fake_root_copy=args.test_fake_root_copy,

            verifier_copy_of=args.verifier_copy_of,
        )
        print("release authority v2 stage verified")
        return 0
    except Exception:
        print("release authority v2 stage rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
