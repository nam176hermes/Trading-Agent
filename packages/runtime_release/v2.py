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
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 2
STATIC_KIND = "STATIC_RELEASE"
SEAL_VERSION = 2
EXPECTED_ALEMBIC_HEAD = "0006_job_transition_database_authority"
EXPECTED_ALEMBIC_PARENT = "0005_job_plane_role_split"
EXPECTED_ALEMBIC_GRANDPARENT = "0004_durable_research_jobs"
EXPECTED_DB_ROLES = {
    "api_role": "trading_job_api",
    "worker_role": "trading_job_worker",
    "scheduler_role": "trading_job_scheduler",
}
JOB_PLANE_POLICY = {
    "allowed_job_types": ["SNAPSHOT"],
    "scheduler_timer_enabled": False,
    "worker_concurrency": 1,
    "worker_lease_seconds": 600,
}
COMPONENT_PREFIXES = {
    "application": ".",
    "backend": "legacy/research-backend",
    "dashboard": "apps/dashboard",
}
COMPONENT_ARTIFACT_ROOTS = {
    "application": "application",
    "backend": "backend",
    "dashboard": "dashboard",
}
LOCK_PATHS = {
    "application": "application/uv.lock",
    "backend": "backend/uv.lock",
    "dashboard": "dashboard/package-lock.json",
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
_NODE_ID = re.compile(r"Node\.js v\d+\.\d+\.\d+\Z")
_MAX_AUTHORITY_BYTES = 64 * 1024 * 1024
_MAX_COMMIT_OBJECT_BYTES = 4 * 1024 * 1024
_GIT_FILE_MODES = {"100644": "0444", "100755": "0555"}
_ALLOWED_STAGE_ADDITION_ROOTS = (
    "application/.venv",
    "application/generated",
    "backend/.venv",
    "dashboard/.next",
    "dashboard/node_modules",
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
                mode, object_id = value
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
    source_directories: set[str] = {"application", "backend", "dashboard"}
    for raw_entry in raw_entries:
        entry = _exact(
            raw_entry,
            {"git_blob", "mode", "sha256", "size", "source_path", "stage_path"},
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
            raise ValueError
        previous = encoded
        stage_paths.add(stage_path)
        git_blob = _git_id(entry["git_blob"])
        sha256 = _digest(entry["sha256"])
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
    for required in ("legacy/research-backend", "apps/dashboard"):
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
    for entry in source["entries"]:
        raw = (stage / str(entry["stage_path"])).read_bytes()
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
            or "apps/dashboard" not in tree_ids
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


def _alembic_graph(stage: Path) -> dict[str, object]:
    try:
        versions = stage / "application/alembic/versions"
        files = sorted(
            (path for path in versions.glob("*.py") if path.name != "__init__.py"),
            key=lambda path: os.fsencode(path.name),
        )
        if not files:
            raise ValueError
        entries: list[dict[str, object]] = []
        revisions: set[str] = set()
        parents: set[str] = set()
        for path in files:
            revision, down_revision = _migration_identity(path)
            if revision in revisions:
                raise ValueError
            revisions.add(revision)
            if down_revision is not None:
                parents.add(down_revision)
            entries.append(
                {
                    "down_revision": down_revision,
                    "path": path.relative_to(stage).as_posix(),
                    "revision": revision,
                    "sha256": _sha256_file(path),
                }
            )
        if not parents.issubset(revisions) or revisions - parents != {EXPECTED_ALEMBIC_HEAD}:
            raise ValueError
        by_revision = {str(item["revision"]): item for item in entries}
        if (
            by_revision[EXPECTED_ALEMBIC_HEAD]["down_revision"]
            != EXPECTED_ALEMBIC_PARENT
            or by_revision[EXPECTED_ALEMBIC_PARENT]["down_revision"]
            != EXPECTED_ALEMBIC_GRANDPARENT
        ):
            raise ValueError
        visited: set[str] = set()
        current: str | None = EXPECTED_ALEMBIC_HEAD
        while current is not None:
            if current in visited or current not in by_revision:
                raise ValueError
            visited.add(current)
            parent = by_revision[current]["down_revision"]
            current = parent if isinstance(parent, str) else None
        if visited != revisions:
            raise ValueError
        return {
            "alembic_graph_sha256": _sha256_bytes(_fragment(entries)),
            "alembic_head": EXPECTED_ALEMBIC_HEAD,
            "alembic_revisions": entries,
        }
    except ReleaseAuthorityV2Error:
        raise
    except Exception:
        raise ReleaseAuthorityV2Error() from None


def _artifact_digest(entries: Sequence[dict[str, object]], root: str) -> str:
    selected = [
        item for item in entries
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
    backend = install_root / "backend"
    executable = backend / ".venv/bin/python3.11"
    commands: list[dict[str, object]] = [
        {
            "argv": [
                str(executable), "-I", "-B", "main.py", "--mode", "snapshot", "--research-only",
            ],
            "cwd": str(backend),
            "environment_policy": "EMPTY_ALLOWLIST_RESEARCH_ONLY_V1",
            "executable": str(executable),
            "job_type": "SNAPSHOT",
            "shell": False,
        }
    ]
    return {
        "commands": commands,
        "manifest_sha256": _sha256_bytes(_fragment(commands)),
        "schema_version": 2,
    }


def _unit_specs(install_root: Path) -> dict[str, dict[str, object]]:
    application = install_root / "application"
    executable = application / ".venv/bin/python3.11"
    return {
        "trading-job-api.service": {
            "argv": [str(executable), "-I", "-m", "apps.job_api.main"],
            "credential_reference": "/etc/trading-agent/job-api.env",
            "database_role": "trading_job_api",
            "service_group": "trading-job-api",
            "service_user": "trading-job-api",
            "working_directory": str(application),
        },
        "trading-job-worker.service": {
            "argv": [str(executable), "-I", "-m", "services.job_worker.main"],
            "credential_reference": "/etc/trading-agent/job-worker.env",
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
    argv = " ".join(str(item) for item in spec["argv"])
    document = (
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


def build_static_release_authority_v2(
    stage: Path,
    *,
    source_proof: Mapping[str, object],
    application_python_identity: str,
    backend_python_identity: str,
    node_executable: Path,
    node_identity: str,
    external_verifier: Path,
    prior_release_sha256: str,
) -> tuple[dict[str, object], str]:
    """Compose (but do not publish or activate) a static v2 authority."""

    try:
        if _PYTHON_ID.fullmatch(application_python_identity) is None:
            raise ValueError
        if _PYTHON_ID.fullmatch(backend_python_identity) is None:
            raise ValueError
        if _NODE_ID.fullmatch(node_identity) is None:
            raise ValueError
        prior = _digest(prior_release_sha256)
        uid, gid, entries = _walk_sealed_stage(stage)
        by_path = _entry_map(entries)
        source, tree_ids = _validated_source_proof(source_proof, by_path, expect_binding=False)
        _verify_source_blobs(stage, source)
        commit = str(source["commit"])
        source["binding_sha256"] = _sha256_bytes(_fragment(source))
        install_root = _installation_root(commit)
        node_info, node_sha = _safe_root_executable(Path(node_executable))
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
                    if name == "application"
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

        contract_entries = [
            {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
            for item in entries
            if item["type"] == "file" and str(item["path"]).startswith("application/generated/")
        ]
        required_contracts = {
            "application/generated/openapi/openapi.json",
            "application/generated/job-api/openapi/openapi.json",
            "application/generated/dashboard/api-schemas.ts",
            "application/generated/dashboard/api-types.ts",
        }
        if not required_contracts.issubset({str(item["path"]) for item in contract_entries}):
            raise ValueError

        app_python = "application/.venv/bin/python3.11"
        backend_python = "backend/.venv/bin/python3.11"
        for path in (app_python, backend_python):
            if path not in by_path or by_path[path]["mode"] != "0555":
                raise ValueError
        document: dict[str, object] = {
            "authority_kind": STATIC_KIND,
            "components": components,
            "command_manifest": _command_manifest(install_root),
            "contracts": {
                "aggregate_sha256": _sha256_bytes(_fragment(contract_entries)),
                "entries": contract_entries,
                "root": "application/generated",
            },
            "database": {**_alembic_graph(stage), **EXPECTED_DB_ROLES},
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
                    "sha256": by_path[app_python]["sha256"],
                },
                "backend_python": {
                    "identity": backend_python_identity,
                    "path": backend_python,
                    "sha256": by_path[backend_python]["sha256"],
                },
                "dashboard_node": {
                    "gid": node_info.st_gid,
                    "identity": node_identity,
                    "path": str(Path(node_executable)),
                    "sha256": node_sha,
                    "uid": node_info.st_uid,
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
        _validate_static_document(document)
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


def _validate_static_document(document: object) -> dict[str, Any]:
    root = _exact(
        document,
        {
            "authority_kind", "binding_sha256", "command_manifest", "components",
            "contracts", "database", "external_verifier", "installation_root",
            "interpreters", "job_plane_policy", "lockfiles", "prior_release_sha256", "schema_version",
            "producer_bindings", "runtime_document_policy", "runtime_paths", "seal_version",
            "source", "stage", "units",
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
    if any(
        path.startswith("units/") and (path.endswith(".timer") or ".wants/" in path)
        for path in by_path
    ):
        raise ValueError
    normalized_source, tree_ids = _validated_source_proof(source, by_path, expect_binding=True)
    if normalized_source != source:
        raise ValueError
    expected_component_trees = {
        "application": tree_ids[""],
        "backend": tree_ids["legacy/research-backend"],
        "dashboard": tree_ids["apps/dashboard"],
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

    interpreters = _exact(root["interpreters"], {"application_python", "backend_python", "dashboard_node"})
    for name, expected_path in (
        ("application_python", "application/.venv/bin/python3.11"),
        ("backend_python", "backend/.venv/bin/python3.11"),
    ):
        item = _exact(interpreters[name], {"identity", "path", "sha256"})
        if (
            item["path"] != expected_path
            or _PYTHON_ID.fullmatch(item["identity"] if isinstance(item["identity"], str) else "") is None
            or by_path.get(expected_path, {}).get("sha256") != _digest(item["sha256"])
            or by_path.get(expected_path, {}).get("mode") != "0555"
        ):
            raise ValueError
    node = _exact(interpreters["dashboard_node"], {"gid", "identity", "path", "sha256", "uid"})
    _absolute(node["path"])
    if (
        _NODE_ID.fullmatch(node["identity"] if isinstance(node["identity"], str) else "") is None
        or type(node["uid"]) is not int
        or type(node["gid"]) is not int
        or node["uid"] != 0
        or node["gid"] != 0
    ):
        raise ValueError
    _digest(node["sha256"])
    if root["job_plane_policy"] != JOB_PLANE_POLICY:
        raise ValueError

    contracts = _exact(root["contracts"], {"aggregate_sha256", "entries", "root"})
    if contracts["root"] != "application/generated" or not isinstance(contracts["entries"], list):
        raise ValueError
    contract_entries: list[dict[str, object]] = []
    previous: bytes | None = None
    for item in contracts["entries"]:
        entry = _exact(item, {"path", "sha256", "size"})
        path = _relative(entry["path"])
        if not path.startswith("application/generated/"):
            raise ValueError
        encoded = os.fsencode(path)
        if previous is not None and encoded <= previous:
            raise ValueError
        previous = encoded
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise ValueError
        if by_path.get(path) != {
            "mode": by_path.get(path, {}).get("mode"),
            "path": path,
            "sha256": _digest(entry["sha256"]),
            "size": entry["size"],
            "type": "file",
        }:
            raise ValueError
        contract_entries.append(dict(entry))
    actual_contract_paths = {
        path for path, entry in by_path.items()
        if entry["type"] == "file" and path.startswith("application/generated/")
    }
    if actual_contract_paths != {str(item["path"]) for item in contract_entries}:
        raise ValueError
    required_contracts = {
        "application/generated/openapi/openapi.json",
        "application/generated/job-api/openapi/openapi.json",
        "application/generated/dashboard/api-schemas.ts",
        "application/generated/dashboard/api-types.ts",
    }
    if not required_contracts.issubset(actual_contract_paths):
        raise ValueError
    if contracts["aggregate_sha256"] != _sha256_bytes(_fragment(contract_entries)):
        raise ValueError

    required_artifacts = {"dashboard/.next/BUILD_ID"}
    if not all(by_path.get(path, {}).get("type") == "file" for path in required_artifacts):
        raise ValueError

    database = _exact(
        root["database"],
        {"alembic_graph_sha256", "alembic_head", "alembic_revisions", *EXPECTED_DB_ROLES},
    )
    if (
        database["alembic_head"] != EXPECTED_ALEMBIC_HEAD
        or any(database[key] != value for key, value in EXPECTED_DB_ROLES.items())
        or not isinstance(database["alembic_revisions"], list)
    ):
        raise ValueError
    revisions: set[str] = set()
    parents: set[str] = set()
    normalized_revisions: list[dict[str, object]] = []
    previous_migration: bytes | None = None
    for raw_revision in database["alembic_revisions"]:
        revision = _exact(raw_revision, {"down_revision", "path", "revision", "sha256"})
        path = _relative(revision["path"])
        encoded = os.fsencode(path)
        if (
            not path.startswith("application/alembic/versions/")
            or not path.endswith(".py")
            or (previous_migration is not None and encoded <= previous_migration)
            or not isinstance(revision["revision"], str)
            or re.fullmatch(r"[0-9A-Za-z_]+", revision["revision"]) is None
            or revision["revision"] in revisions
            or (revision["down_revision"] is not None and not isinstance(revision["down_revision"], str))
            or by_path.get(path, {}).get("sha256") != _digest(revision["sha256"])
        ):
            raise ValueError
        previous_migration = encoded
        revisions.add(revision["revision"])
        if revision["down_revision"] is not None:
            parents.add(revision["down_revision"])
        normalized_revisions.append(dict(revision))
    head_entries = [
        item for item in normalized_revisions if item["revision"] == EXPECTED_ALEMBIC_HEAD
    ]
    by_revision = {str(item["revision"]): item for item in normalized_revisions}
    if (
        not parents.issubset(revisions)
        or revisions - parents != {EXPECTED_ALEMBIC_HEAD}
        or len(head_entries) != 1
        or head_entries[0]["down_revision"] != EXPECTED_ALEMBIC_PARENT
        or by_revision[EXPECTED_ALEMBIC_PARENT]["down_revision"]
        != EXPECTED_ALEMBIC_GRANDPARENT
        or database["alembic_graph_sha256"] != _sha256_bytes(_fragment(normalized_revisions))
    ):
        raise ValueError
    visited: set[str] = set()
    current_revision: str | None = EXPECTED_ALEMBIC_HEAD
    while current_revision is not None:
        if current_revision in visited or current_revision not in by_revision:
            raise ValueError
        visited.add(current_revision)
        parent = by_revision[current_revision]["down_revision"]
        current_revision = parent if isinstance(parent, str) else None
    if visited != revisions:
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
                "argv", "credential_reference", "database_role", "enabled_by_default",
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


def parse_static_release_authority_v2(raw: bytes) -> StaticReleaseAuthorityV2:
    try:
        document = _validate_static_document(_load_canonical(raw))
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
) -> bool:
    """Verify one complete sealed tree without executing any staged file."""

    try:
        expected = _digest(expected_digest)
        if _sha256_bytes(authority_raw) != expected:
            raise ValueError
        document = _validate_static_document(_load_canonical(authority_raw))
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
        node = document["interpreters"]["dashboard_node"]
        node_info, node_sha = _safe_root_executable(Path(node["path"]))
        if node_sha != node["sha256"] or node_info.st_uid != node["uid"] or node_info.st_gid != node["gid"]:
            raise ValueError
        if document["database"] != {**_alembic_graph(stage), **EXPECTED_DB_ROLES}:
            raise ValueError
        _verify_source_blobs(stage, document["source"])
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
    compose = subparsers.add_parser("compose")
    compose.add_argument("--stage", type=Path, required=True)
    compose.add_argument("--source-proof", type=Path, required=True)
    compose.add_argument("--application-python-identity", required=True)
    compose.add_argument("--backend-python-identity", required=True)
    compose.add_argument("--node-executable", type=Path, required=True)
    compose.add_argument("--node-identity", required=True)
    compose.add_argument("--external-verifier", type=Path, required=True)
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
        document, _ = build_static_release_authority_v2(
            arguments.stage,
            source_proof=_load_canonical(arguments.source_proof.read_bytes()),
            application_python_identity=arguments.application_python_identity,
            backend_python_identity=arguments.backend_python_identity,
            node_executable=arguments.node_executable,
            node_identity=arguments.node_identity,
            external_verifier=arguments.external_verifier,
            prior_release_sha256=arguments.prior_release_sha256,
        )
        _write_exclusive(arguments.output, canonical_json_bytes(document), 0o444)
        return 0
    except Exception:
        print("release authority v2 rejected", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = [
    "EXPECTED_ALEMBIC_HEAD", "EXTERNAL_VERIFIER_INSTALLATION_PATH", "JOB_PLANE_POLICY",
    "ReleaseAuthorityV2Error", "SCHEMA_VERSION", "SEAL_VERSION", "STATIC_KIND",
    "StaticReleaseAuthorityV2", "build_static_release_authority_v2", "canonical_json_bytes",
    "capture_source_proof_v2", "parse_static_release_authority_v2",
    "render_candidate_units", "verify_static_release_authority_v2",
]
