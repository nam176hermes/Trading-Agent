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
import sys
from typing import Any


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
_PYTHON_ID = re.compile(r"CPython 3\.11\.\d+\Z")
_NODE_ID = re.compile(r"Node\.js v\d+\.\d+\.\d+\Z")
_ROOT_KEYS = {
    "authority_kind", "binding_sha256", "command_manifest", "components",
    "contracts", "database", "external_verifier", "installation_root",
    "interpreters", "job_plane_policy", "lockfiles", "prior_release_sha256", "schema_version",
    "producer_bindings", "runtime_document_policy", "runtime_paths", "seal_version",
    "source", "stage", "units",
}
_COMPONENTS = {
    "application": (".", "application"),
    "backend": ("legacy/research-backend", "backend"),
    "dashboard": ("apps/dashboard", "dashboard"),
}
_LOCKS = {
    "application": "application/uv.lock",
    "backend": "backend/uv.lock",
    "dashboard": "dashboard/package-lock.json",
}
_UNITS = {"trading-job-api.service", "trading-job-worker.service"}
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
    "application/.venv", "application/generated", "backend/.venv",
    "dashboard/.next", "dashboard/node_modules",
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


def _source_stage_path(source_path: str) -> str:
    if source_path.startswith("legacy/research-backend/"):
        return "backend/" + source_path.removeprefix("legacy/research-backend/")
    if source_path.startswith("apps/dashboard/"):
        return "dashboard/" + source_path.removeprefix("apps/dashboard/")
    return "application/" + source_path


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
                mode, object_id = value
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
    source_directories = {"application", "backend", "dashboard"}
    for raw_entry in source["entries"]:
        entry = _exact(
            raw_entry, {"git_blob", "mode", "sha256", "size", "source_path", "stage_path"},
        )
        source_path = _relative(entry["source_path"])
        stage_path = _relative(entry["stage_path"])
        encoded = os.fsencode(source_path)
        if (
            source_path == "."
            or stage_path != _source_stage_path(source_path)
            or (previous is not None and encoded <= previous)
            or stage_path in stage_paths
            or entry["mode"] not in _GIT_FILE_MODES
            or type(entry["size"]) is not int
            or entry["size"] < 0
        ):
            raise Rejected
        previous = encoded
        stage_paths.add(stage_path)
        blob = _git_id(entry["git_blob"])
        digest = _digest(entry["sha256"])
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
    if tree_ids.get("") != tree or any(path not in tree_ids for path in ("legacy/research-backend", "apps/dashboard")):
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


def _migration_identity(path: Path) -> tuple[str, str | None]:
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
        raise Rejected
    return revision, down_revision


def _alembic_graph(stage: Path) -> dict[str, object]:
    files = sorted(
        (
            path for path in (stage / "application/alembic/versions").glob("*.py")
            if path.name != "__init__.py"
        ),
        key=lambda path: os.fsencode(path.name),
    )
    if not files:
        raise Rejected
    entries: list[dict[str, object]] = []
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in files:
        revision, down_revision = _migration_identity(path)
        if revision in revisions:
            raise Rejected
        revisions.add(revision)
        if down_revision is not None:
            parents.add(down_revision)
        entries.append(
            {
                "down_revision": down_revision,
                "path": path.relative_to(stage).as_posix(),
                "revision": revision,
                "sha256": _sha_file(path),
            }
        )
    by_revision = {str(item["revision"]): item for item in entries}
    if (
        not parents.issubset(revisions)
        or revisions - parents != {"0006_job_transition_database_authority"}
        or by_revision["0006_job_transition_database_authority"]["down_revision"]
        != "0005_job_plane_role_split"
        or by_revision["0005_job_plane_role_split"]["down_revision"]
        != "0004_durable_research_jobs"
    ):
        raise Rejected
    visited: set[str] = set()
    current: str | None = "0006_job_transition_database_authority"
    while current is not None:
        if current in visited or current not in by_revision:
            raise Rejected
        visited.add(current)
        parent = by_revision[current]["down_revision"]
        current = parent if isinstance(parent, str) else None
    if visited != revisions:
        raise Rejected
    return {
        "alembic_graph_sha256": _sha_bytes(_fragment(entries)),
        "alembic_head": "0006_job_transition_database_authority",
        "alembic_revisions": entries,
    }


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
    argv = " ".join(str(item) for item in spec["argv"])
    return (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={spec['service_user']}\n"
        f"Group={spec['service_group']}\n"
        f"WorkingDirectory={spec['working_directory']}\n"
        f"EnvironmentFile={spec['credential_reference']}\n"
        "Environment=PYTHONDONTWRITEBYTECODE=1\n"
        "Environment=TRADING_MODE=paper\n"
        "Environment=LIVE_EXECUTION_ENABLED=false\n"
        "Environment=LIVE_TRADING_APPROVED=false\n"
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
        root["schema_version"] != 2
        or root["authority_kind"] != "STATIC_RELEASE"
        or root["seal_version"] != 2
        or root["binding_sha256"] != _binding(root)
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
        "application": source_tree_ids[""],
        "backend": source_tree_ids["legacy/research-backend"],
        "dashboard": source_tree_ids["apps/dashboard"],
    }:
        raise Rejected

    locks = _exact(root["lockfiles"], set(_LOCKS))
    for name, path in _LOCKS.items():
        item = _exact(locks[name], {"path", "sha256"})
        if item["path"] != path or by_path.get(path, {}).get("sha256") != _digest(item["sha256"]):
            raise Rejected
    database = _exact(
        root["database"],
        {
            "alembic_graph_sha256", "alembic_head", "alembic_revisions",
            "api_role", "worker_role", "scheduler_role",
        },
    )
    if database != {
        **_alembic_graph(stage),
        "api_role": "trading_job_api",
        "worker_role": "trading_job_worker",
        "scheduler_role": "trading_job_scheduler",
    }:
        raise Rejected
    if root["job_plane_policy"] != {
        "allowed_job_types": ["SNAPSHOT"],
        "scheduler_timer_enabled": False,
        "worker_concurrency": 1,
        "worker_lease_seconds": 600,
    }:
        raise Rejected
    interpreters = _exact(root["interpreters"], {"application_python", "backend_python", "dashboard_node"})
    for name, path in (
        ("application_python", "application/.venv/bin/python3.11"),
        ("backend_python", "backend/.venv/bin/python3.11"),
    ):
        item = _exact(interpreters[name], {"identity", "path", "sha256"})
        if (
            item["path"] != path
            or _PYTHON_ID.fullmatch(item["identity"] if isinstance(item["identity"], str) else "") is None
            or by_path.get(path, {}).get("mode") != "0555"
            or by_path.get(path, {}).get("sha256") != _digest(item["sha256"])
        ):
            raise Rejected
    contracts = _exact(root["contracts"], {"aggregate_sha256", "entries", "root"})
    if contracts["root"] != "application/generated" or not isinstance(contracts["entries"], list):
        raise Rejected
    observed_contracts: list[dict[str, object]] = []
    previous: bytes | None = None
    for raw_entry in contracts["entries"]:
        entry = _exact(raw_entry, {"path", "sha256", "size"})
        path = _relative(entry["path"])
        encoded = os.fsencode(path)
        if (
            not path.startswith("application/generated/")
            or (previous is not None and encoded <= previous)
            or type(entry["size"]) is not int
            or entry["size"] < 0
            or by_path.get(path, {}).get("type") != "file"
            or by_path[path]["size"] != entry["size"]
            or by_path[path]["sha256"] != _digest(entry["sha256"])
        ):
            raise Rejected
        previous = encoded
        observed_contracts.append(dict(entry))
    actual_contract_paths = {
        path for path, entry in by_path.items()
        if entry["type"] == "file" and path.startswith("application/generated/")
    }
    if actual_contract_paths != {str(item["path"]) for item in observed_contracts}:
        raise Rejected
    required_contracts = {
        "application/generated/openapi/openapi.json",
        "application/generated/job-api/openapi/openapi.json",
        "application/generated/dashboard/api-schemas.ts",
        "application/generated/dashboard/api-types.ts",
    }
    if (
        not required_contracts.issubset(actual_contract_paths)
        or contracts["aggregate_sha256"] != _sha_bytes(_fragment(observed_contracts))
        or by_path.get("application/alembic/versions/0005_job_plane_role_split.py", {}).get("type") != "file"
        or by_path.get(
            "application/alembic/versions/0006_job_transition_database_authority.py",
            {},
        ).get("type")
        != "file"
        or by_path.get("dashboard/.next/BUILD_ID", {}).get("type") != "file"
    ):
        raise Rejected
    command = _exact(root["command_manifest"], {"commands", "manifest_sha256", "schema_version"})
    if command["schema_version"] != 2 or not isinstance(command["commands"], list) or len(command["commands"]) != 1:
        raise Rejected
    snapshot = _exact(command["commands"][0], {"argv", "cwd", "environment_policy", "executable", "job_type", "shell"})
    backend = install_root / "backend"
    executable = backend / ".venv/bin/python3.11"
    if snapshot != {
        "argv": [str(executable), "-I", "-B", "main.py", "--mode", "snapshot", "--research-only"],
        "cwd": str(backend),
        "environment_policy": "EMPTY_ALLOWLIST_RESEARCH_ONLY_V1",
        "executable": str(executable),
        "job_type": "SNAPSHOT",
        "shell": False,
    } or command["manifest_sha256"] != _sha_bytes(_fragment(command["commands"])):
        raise Rejected
    units = _exact(root["units"], _UNITS)
    application = install_root / "application"
    app_python = application / ".venv/bin/python3.11"
    expected_unit_fields = {
        "trading-job-api.service": {
            "argv": [str(app_python), "-I", "-m", "apps.job_api.main"],
            "credential_reference": "/etc/trading-agent/job-api.env",
            "database_role": "trading_job_api",
            "service_group": "trading-job-api",
            "service_user": "trading-job-api",
            "working_directory": str(application),
        },
        "trading-job-worker.service": {
            "argv": [str(app_python), "-I", "-m", "services.job_worker.main"],
            "credential_reference": "/etc/trading-agent/job-worker.env",
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
                "argv", "credential_reference", "database_role", "enabled_by_default", "path",
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
    node = _exact(interpreters["dashboard_node"], {"gid", "identity", "path", "sha256", "uid"})
    if (
        _NODE_ID.fullmatch(node["identity"] if isinstance(node["identity"], str) else "") is None
        or type(node["uid"]) is not int
        or type(node["gid"]) is not int
        or node["uid"] != 0
        or node["gid"] != 0
    ):
        raise Rejected
    _safe_root_executable(_absolute(node["path"]), _digest(node["sha256"]))


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
