"""Seal and validate one deterministic portable CI evidence set."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
from typing import Callable, Iterator, Mapping, Sequence


MANIFEST_SCHEMA = "portable-ci-evidence-manifest/v1"
_RENAME_NOREPLACE = 1
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)$")
_DATABASE_CREDENTIAL_URL = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis)://[^\s/:@]+:[^\s/@]+@",
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
_LIVE_EXECUTION = re.compile(r"(?i)(?:^|\s)LIVE_EXECUTION_ENABLED\s*=\s*true(?:\s|$)")
_LIVE_TRADING = re.compile(r"(?i)(?:^|\s)LIVE_TRADING_ENABLED\s*=\s*true(?:\s|$)")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*(TRADING_MASTER_KEY|password|secret|api[_-]?key|authorization)"
    r"[ \t]*(?:=|:)[ \t]*(\S(?:[^\r\n]*\S)?)?[ \t]*$",
)
_REDACTED = frozenset({"", "redacted", "[redacted]", "<redacted>", "absent", "none", "not_configured", "not configured"})
_CODES = frozenset({
    "NATIVE-BWRAP-OS-SANDBOX",
    "NATIVE-USERNS-ROOT-PROVISION",
    "EXT-PHASE3B-CORPUS",
    "EXT-LEGACY-UV-AUTHORITY",
})
_TOPOLOGY_FIXED_LEAVES = frozenset({
    ".reservation",
    "aggregate.json",
    "foundation-context.json",
    "foundation-portable-defect-closure.tsv",
    "portable-defect-closure-proof.json",
    "portable-defect-closure.governance.json",
    "portable-root-baseline.json",
    "portable-root-candidates.txt",
    "portable-root-collection.governance.json",
    "portable-root-remainder.governance.json",
    "portable-root-remainder.json",
    "portable-root-remainder.txt",
    "t-g03a-hosted-failure-inventory.tsv",
})
_RAW_TOPOLOGY_FIXED_LEAVES = frozenset({
    ".reservation",
    "foundation-context.json",
    "portable-defect-closure-proof.json",
    "portable-defect-closure.governance.json",
    "portable-root-baseline.json",
    "portable-root-candidates.txt",
    "portable-root-collection.governance.json",
    "portable-root-remainder.governance.json",
    "portable-root-remainder.json",
    "portable-root-remainder.txt",
})
_FLAT_RECEIPTS = frozenset({
    "native-capability-receipt.json",
    "external-authority-receipt.json",
})
_MANIFEST_KEYS = frozenset({
    "schema_version", "head_sha", "source_tree_sha256", "semantic_projection",
    "semantic_result_sha256", "run_metadata", "directory_mode", "files",
    "manifest_payload_sha256",
})
_RUN_METADATA_KEYS = frozenset({"run_id", "attempt", "generated_at_utc"})
_FILE_KEYS = frozenset({"path", "sha256", "size", "mode"})


class FirewallError(RuntimeError):
    """Closed diagnostic: no evidence value is included in its message."""

    def __init__(
        self, message: str, *, code: str = "ARTIFACT_FIREWALL_REJECTED",
        category: str = "LAYOUT", relative_path: str = "", sha256: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.relative_path = relative_path
        self.sha256 = sha256


def _reject(
    message: str, *, code: str = "ARTIFACT_FIREWALL_REJECTED",
    category: str = "LAYOUT", relative_path: str = "", sha256: str = "",
) -> FirewallError:
    return FirewallError(
        message, code=code, category=category,
        relative_path=relative_path, sha256=sha256,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def semantic_result_sha256(
    semantic_projection: Mapping[str, object],
    *, run_metadata: Mapping[str, object] | None = None,
) -> str:
    """Hash semantic meaning only; run identity is intentionally ignored."""
    del run_metadata
    return hashlib.sha256(_canonical_json_bytes(semantic_projection)).hexdigest()


def manifest_payload_sha256(manifest: Mapping[str, object]) -> str:
    payload = dict(manifest)
    payload["manifest_payload_sha256"] = ""
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _stable_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _read_fd(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _safe_value(value: object) -> bool:
    if value is None or value is False:
        return True
    return isinstance(value, str) and value.strip().lower() in _REDACTED


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _secret_category(key: str, value: object) -> str | None:
    if _safe_value(value):
        return None
    if key == "trading_master_key":
        return "TRADING_MASTER_KEY"
    if key == "password":
        return "PASSWORD"
    if key == "secret":
        return "SECRET"
    if key in {"api_key", "apikey"}:
        return "API_KEY"
    if key == "authorization":
        return "AUTHORIZATION"
    if (
        ("broker" in key or "exchange" in key)
        and any(token in key for token in ("credential", "token", "key", "secret", "password"))
    ):
        return "BROKER_CREDENTIAL" if "broker" in key else "EXCHANGE_CREDENTIAL"
    return None


def _string_secret_category(value: str) -> str | None:
    if _PRIVATE_KEY.search(value):
        return "PRIVATE_KEY"
    if _DATABASE_CREDENTIAL_URL.search(value):
        return "DATABASE_URL"
    if _LIVE_EXECUTION.search(value):
        return "LIVE_EXECUTION_GATE"
    if _LIVE_TRADING.search(value):
        return "LIVE_TRADING_GATE"
    for matched in _SENSITIVE_ASSIGNMENT.finditer(value):
        assigned = matched.group(2) or ""
        if _safe_value(assigned):
            continue
        key = _normalized_key(matched.group(1))
        return {
            "trading_master_key": "TRADING_MASTER_KEY",
            "password": "PASSWORD",
            "secret": "SECRET",
            "api_key": "API_KEY",
            "apikey": "API_KEY",
            "authorization": "AUTHORIZATION",
        }[key]
    return None


def scan_json_bytes(relative_path: str, raw: bytes) -> None:
    """Reject structured secrets while exposing only category and byte identity."""
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _reject(
            "ARTIFACT_JSON_INVALID", category="JSON", relative_path=relative_path,
            sha256=digest,
        ) from exc

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, nested in value.items():
                key = _normalized_key(raw_key)
                category = _secret_category(key, nested)
                if category is not None:
                    raise _reject(
                        "ARTIFACT_SECRET_REJECTED", code="ARTIFACT_SECRET_REJECTED",
                        category=category, relative_path=relative_path, sha256=digest,
                    )
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            category = _string_secret_category(value)
            if category is not None:
                raise _reject(
                    "ARTIFACT_SECRET_REJECTED", code="ARTIFACT_SECRET_REJECTED",
                    category=category, relative_path=relative_path, sha256=digest,
                )

    walk(document)


def scan_artifact_bytes(relative_path: str, raw: bytes) -> None:
    """Scan every manifested text leaf, then apply structured JSON checks."""
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise _reject(
            "ARTIFACT_TEXT_INVALID", category="ENCODING",
            relative_path=relative_path, sha256=digest,
        ) from exc
    category = _string_secret_category(text)
    if category is not None:
        raise _reject(
            "ARTIFACT_SECRET_REJECTED", code="ARTIFACT_SECRET_REJECTED",
            category=category, relative_path=relative_path, sha256=digest,
        )
    if relative_path.endswith(".json") or relative_path.endswith("/.reservation"):
        scan_json_bytes(relative_path, raw)


@dataclass
class _Leaf:
    parent_descriptor: int
    name: str
    descriptor: int
    identity: tuple[int, ...]
    raw: bytes


@dataclass
class _Directory:
    parent_descriptor: int
    name: str
    descriptor: int
    identity: tuple[int, int, int, int, int]
    exact_identity: tuple[int, ...]
    entries: tuple[str, ...]


@dataclass
class _LineageEntry:
    parent_descriptor: int | None
    name: str
    descriptor: int
    identity: tuple[int, int, int, int, int]


@dataclass
class _RetainedLineage:
    entries: tuple[_LineageEntry, ...]

    @property
    def descriptor(self) -> int:
        return self.entries[-1].descriptor

    @property
    def parent_descriptor(self) -> int:
        parent = self.entries[-1].parent_descriptor
        if parent is None:
            raise _reject("artifact root cannot be the filesystem root")
        return parent

    @property
    def name(self) -> str:
        return self.entries[-1].name

    def postcheck(self) -> None:
        try:
            for entry in self.entries:
                held = os.fstat(entry.descriptor)
                if _stable_identity(held) != entry.identity:
                    raise OSError
                if entry.parent_descriptor is None:
                    named = os.stat(entry.name, follow_symlinks=False)
                else:
                    named = os.stat(
                        entry.name, dir_fd=entry.parent_descriptor,
                        follow_symlinks=False,
                    )
                if _identity(named) != _identity(held):
                    raise OSError
        except OSError as exc:
            raise _reject("artifact lineage identity changed") from exc

    def __enter__(self) -> _RetainedLineage:
        self.postcheck()
        return self

    def __exit__(self, *_args: object) -> None:
        for entry in reversed(self.entries):
            try:
                os.close(entry.descriptor)
            except OSError:
                pass


@dataclass
class _Snapshot:
    root_descriptor: int
    directories: dict[str, _Directory]
    leaves: dict[str, _Leaf]

    def postcheck(
        self, *, named_parent_descriptor: int | None = None,
        named_root_name: str | None = None,
        refresh_root_identity: bool = False,
    ) -> None:
        try:
            for relative, directory in self.directories.items():
                parent_descriptor = directory.parent_descriptor
                name = directory.name
                if relative == "" and named_parent_descriptor is not None:
                    parent_descriptor = named_parent_descriptor
                    name = named_root_name or ""
                held = os.fstat(directory.descriptor)
                named = os.stat(
                    name, dir_fd=parent_descriptor, follow_symlinks=False,
                )
                if (
                    _stable_identity(held) != directory.identity
                    or _identity(named) != _identity(held)
                ):
                    raise OSError
                held_identity = _identity(held)
                if held_identity != directory.exact_identity:
                    if relative == "" and refresh_root_identity:
                        directory.exact_identity = held_identity
                    else:
                        raise OSError
                if tuple(sorted(os.listdir(directory.descriptor))) != directory.entries:
                    raise OSError
            for leaf in self.leaves.values():
                named_leaf = os.stat(
                    leaf.name, dir_fd=leaf.parent_descriptor, follow_symlinks=False,
                )
                held_leaf = os.fstat(leaf.descriptor)
                if (
                    _identity(named_leaf) != leaf.identity
                    or _identity(held_leaf) != leaf.identity
                    or _read_fd(leaf.descriptor) != leaf.raw
                ):
                    raise OSError
        except OSError as exc:
            raise _reject("staging evidence changed during publication") from exc


def _validate_lineage(path: Path, *, create: bool) -> _RetainedLineage:
    absolute = path.absolute()
    if not absolute.is_absolute() or not absolute.parts or absolute.parts[0] != absolute.anchor:
        raise _reject("artifact path is not absolute")
    entries: list[_LineageEntry] = []
    root_descriptor = -1
    try:
        root_descriptor = os.open(
            absolute.anchor, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
        )
        root_info = os.fstat(root_descriptor)
        entries.append(_LineageEntry(
            None, absolute.anchor, root_descriptor, _stable_identity(root_info),
        ))
        current_descriptor = root_descriptor
        for index, name in enumerate(absolute.parts[1:], start=1):
            try:
                info = os.stat(
                    name, dir_fd=current_descriptor, follow_symlinks=False,
                )
            except FileNotFoundError:
                if not create:
                    raise _reject("artifact path ancestor is absent")
                try:
                    os.mkdir(name, mode=0o700, dir_fd=current_descriptor)
                    info = os.stat(
                        name, dir_fd=current_descriptor, follow_symlinks=False,
                    )
                except OSError as exc:
                    raise _reject("artifact path ancestor cannot be created") from exc
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise _reject("artifact path ancestor is unsafe")
            if info.st_uid not in {0, os.geteuid()}:
                raise _reject("artifact path ancestor has foreign ownership")
            writable = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            trusted_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if writable and not trusted_sticky:
                raise _reject("artifact path ancestor is writable")
            if index == len(absolute.parts) - 1 and info.st_uid != os.geteuid():
                raise _reject("artifact root is not current-user owned")
            child_descriptor = -1
            try:
                child_descriptor = os.open(
                    name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                    dir_fd=current_descriptor,
                )
                opened = os.fstat(child_descriptor)
                if _identity(opened) != _identity(info):
                    raise _reject("artifact lineage identity changed while opening")
                entries.append(_LineageEntry(
                    current_descriptor, name, child_descriptor,
                    _stable_identity(opened),
                ))
                child_descriptor = -1
                current_descriptor = entries[-1].descriptor
            finally:
                if child_descriptor >= 0:
                    os.close(child_descriptor)
        retained = _RetainedLineage(tuple(entries))
        retained.postcheck()
        return retained
    except Exception:
        for entry in reversed(entries):
            try:
                os.close(entry.descriptor)
            except OSError:
                pass
        if root_descriptor >= 0 and not entries:
            os.close(root_descriptor)
        raise


@contextmanager
def _snapshot_tree(
    root: Path, *, directory_mode: int, file_mode: int,
    retained_parent_descriptor: int | None = None,
    retained_name: str | None = None,
) -> Iterator[_Snapshot]:
    lineage: _RetainedLineage | None = None
    owns_root_descriptor = False
    if retained_parent_descriptor is None:
        lineage = _validate_lineage(root, create=False)
        root_descriptor = lineage.descriptor
        root_parent_descriptor = lineage.parent_descriptor
        root_name = lineage.name
    else:
        if not retained_name:
            raise _reject("retained artifact root name is absent")
        root_parent_descriptor = retained_parent_descriptor
        root_name = retained_name
        named = os.stat(
            root_name, dir_fd=root_parent_descriptor, follow_symlinks=False,
        )
        root_descriptor = -1
        try:
            root_descriptor = os.open(
                root_name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=root_parent_descriptor,
            )
            if _identity(os.fstat(root_descriptor)) != _identity(named):
                raise _reject("retained artifact root identity changed while opening")
            owns_root_descriptor = True
        except BaseException:
            if root_descriptor >= 0:
                os.close(root_descriptor)
            raise
    directories: dict[str, _Directory] = {}
    leaves: dict[str, _Leaf] = {}
    try:
        if lineage is not None:
            lineage.postcheck()
        before = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_IMODE(before.st_mode) != directory_mode
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
        ):
            raise _reject("artifact root is not a private governed directory")

        def visit(
            descriptor: int, relative: str,
            parent_descriptor: int, name: str,
        ) -> None:
            entries = tuple(sorted(os.listdir(descriptor)))
            directory_info = os.fstat(descriptor)
            directories[relative] = _Directory(
                parent_descriptor, name, descriptor,
                _stable_identity(directory_info),
                _identity(directory_info), entries,
            )
            for name in entries:
                child_relative = name if not relative else f"{relative}/{name}"
                if PurePosixPath(child_relative).is_absolute() or ".." in PurePosixPath(child_relative).parts:
                    raise _reject("artifact relative path is malformed")
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                    if (
                        stat.S_IMODE(info.st_mode) != directory_mode
                        or info.st_uid != os.geteuid()
                        or info.st_gid != os.getegid()
                    ):
                        raise _reject("artifact directory mode or ownership is unsafe")
                    child = os.open(
                        name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                        dir_fd=descriptor,
                    )
                    transferred = False
                    try:
                        if _identity(os.fstat(child)) != _identity(info):
                            raise _reject("artifact directory identity changed")
                        transferred = True
                        visit(child, child_relative, descriptor, name)
                    finally:
                        if not transferred:
                            os.close(child)
                    continue
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != file_mode
                    or info.st_uid != os.geteuid()
                    or info.st_gid != os.getegid()
                ):
                    raise _reject("artifact leaf type, link count, mode, or ownership is unsafe")
                leaf_descriptor = os.open(
                    name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=descriptor,
                )
                transferred = False
                try:
                    opened = os.fstat(leaf_descriptor)
                    if _identity(opened) != _identity(info):
                        raise _reject("artifact leaf identity changed before read")
                    raw = _read_fd(leaf_descriptor)
                    if _identity(os.fstat(leaf_descriptor)) != _identity(opened):
                        raise _reject("artifact leaf changed during read")
                    leaves[child_relative] = _Leaf(
                        descriptor, name, leaf_descriptor, _identity(opened), raw,
                    )
                    transferred = True
                finally:
                    if not transferred:
                        os.close(leaf_descriptor)

        visit(root_descriptor, "", root_parent_descriptor, root_name)
        snapshot = _Snapshot(root_descriptor, directories, leaves)
        snapshot.postcheck()
        yield snapshot
    except FirewallError:
        raise
    except OSError as exc:
        raise _reject("artifact tree cannot be retained safely") from exc
    finally:
        for leaf in leaves.values():
            try:
                os.close(leaf.descriptor)
            except OSError:
                pass
        for relative in sorted(directories, key=lambda item: item.count("/"), reverse=True):
            descriptor = directories[relative].descriptor
            if descriptor == root_descriptor and relative == "":
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass
        if lineage is not None:
            lineage.__exit__()
        elif owns_root_descriptor:
            try:
                os.close(root_descriptor)
            except OSError:
                pass


def _object_leaf(snapshot: _Snapshot, relative: str) -> dict[str, object]:
    leaf = snapshot.leaves.get(relative)
    if leaf is None:
        raise _reject(f"required schema leaf is missing: {relative}")
    value = _strict_json(leaf.raw, relative=relative)
    if not isinstance(value, dict):
        raise _reject(f"required schema leaf is not an object: {relative}")
    return dict(value)


def _validate_phase_manifest(snapshot: _Snapshot) -> None:
    manifest = _object_leaf(snapshot, "phase-evidence/manifest.json")
    if set(manifest) != {"schema_version", "files"} or manifest.get("schema_version") != "phase-evidence-manifest/v1":
        raise _reject("phase evidence manifest schema is invalid")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise _reject("phase evidence manifest files are invalid")
    listed: dict[str, dict[str, object]] = {}
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != _FILE_KEYS:
            raise _reject("phase evidence manifest entry is invalid")
        relative = item.get("path")
        if (
            not isinstance(relative, str) or not relative
            or relative in listed or relative == "manifest.json"
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or item.get("mode") != "0400"
        ):
            raise _reject("phase evidence manifest path is invalid")
        listed[relative] = item
    actual = {
        relative.removeprefix("phase-evidence/"): leaf
        for relative, leaf in snapshot.leaves.items()
        if relative.startswith("phase-evidence/")
        and relative != "phase-evidence/manifest.json"
    }
    if set(listed) != set(actual):
        raise _reject("phase evidence manifest does not bind every entry")
    for relative, item in listed.items():
        raw = actual[relative].raw
        if (
            item.get("sha256") != hashlib.sha256(raw).hexdigest()
            or item.get("size") != len(raw)
        ):
            raise _reject("phase evidence manifest binding mismatch")


def _validate_final_governance(
    snapshot: _Snapshot, aggregate: dict[str, object],
) -> dict[str, object]:
    from scripts import check_test_governance as governance

    if "test-governance/summary.json" in snapshot.leaves:
        summary = _object_leaf(snapshot, "test-governance/summary.json")
        required = {
            "schema_version", "status", "summary", "postgres_disclosure",
            "capability_topology", "tests", "suite_exit_codes", "allowlist",
            "generated_at_utc",
        }
        exit_codes = summary.get("suite_exit_codes")
        if (
            set(summary) != required
            or summary.get("schema_version") != "test-governance-final-summary/v1"
            or summary.get("status") != "pass"
            or summary.get("capability_topology") != aggregate
            or not isinstance(summary.get("summary"), dict)
            or not isinstance(summary.get("postgres_disclosure"), dict)
            or not isinstance(summary.get("tests"), list)
            or not isinstance(exit_codes, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, int)
                or isinstance(value, bool) or value != 0
                for key, value in exit_codes.items()
            )
            or not isinstance(summary.get("allowlist"), str)
            or not isinstance(summary.get("generated_at_utc"), str)
        ):
            raise _reject("governed summary schema or binding is invalid")
        selected_tests: list[dict[str, str]] = []
        seen_tests: set[tuple[str, str]] = set()
        for item in summary["tests"]:
            if not isinstance(item, dict):
                raise _reject("governed summary test record is invalid")
            node_id = item.get("test_node_id")
            component = item.get("component")
            phase = item.get("phase")
            outcome = item.get(
                "governed_outcome", item.get("raw_outcome", item.get("outcome")),
            )
            if not all(isinstance(value, str) and value for value in (
                node_id, component, phase, outcome,
            )) or (
                phase not in governance.OBSERVATION_PHASES
                or outcome not in {*governance.OBSERVED_OUTCOMES, "approval_blocked"}
                or (component, node_id) in seen_tests
            ):
                raise _reject("governed summary test meaning is invalid")
            seen_tests.add((component, node_id))
            selected_tests.append({
                "component": component,
                "node_id": node_id,
                "outcome": outcome,
                "phase": phase,
            })
        selected_tests.sort(key=lambda item: (item["component"], item["node_id"]))
        return {"selected_tests": selected_tests}
    error = _object_leaf(snapshot, "test-governance/error.json")
    exit_codes = error.get("suite_exit_codes")
    if (
        set(error) != {
            "schema_version", "status", "generated_at_utc", "error_code",
            "suite_exit_codes",
        }
        or error.get("schema_version") != "test-governance-final-error/v1"
        or error.get("status") != "error"
        or not isinstance(error.get("generated_at_utc"), str)
        or not isinstance(error.get("error_code"), str)
        or not re.fullmatch(r"[A-Z0-9_]+", str(error.get("error_code")))
        or not isinstance(exit_codes, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, int)
            or isinstance(value, bool)
            for key, value in exit_codes.items()
        )
    ):
        raise _reject("governed error schema is invalid")
    return {
        "governance_error": {
            "error_code": error["error_code"],
            "suite_exit_codes": dict(exit_codes),
        },
    }


def _validate_projection_schemas(snapshot: _Snapshot) -> dict[str, object]:
    from scripts import t_g03_capability_topology as topology

    prefix = "capability-topology/"
    context = _object_leaf(snapshot, prefix + "foundation-context.json")
    context_required = {
        "schema_version", "foundation_run_id", "foundation_head_sha",
        "foundation_validation_date", "foundation_context_sha256",
    }
    if (
        set(context) != context_required
        or context.get("schema_version") != topology.FOUNDATION_CONTEXT_SCHEMA
        or not isinstance(context.get("foundation_run_id"), str)
        or not topology.RUN_ID.fullmatch(str(context["foundation_run_id"]))
        or not isinstance(context.get("foundation_head_sha"), str)
        or not topology.HEAD_SHA.fullmatch(str(context["foundation_head_sha"]))
        or context.get("foundation_context_sha256") != topology._sha256({
            key: value for key, value in context.items()
            if key != "foundation_context_sha256"
        })
    ):
        raise _reject("Foundation context schema or binding is invalid")
    receipts: list[dict[str, object]] = []
    try:
        topology.parse_foundation_validation_date(context["foundation_validation_date"])
    except topology.TopologyError as exc:
        raise _reject("Foundation context date is invalid") from exc
    run_id = str(context["foundation_run_id"])
    head_sha = str(context["foundation_head_sha"])
    context_hash = str(context["foundation_context_sha256"])
    reservation = _object_leaf(snapshot, prefix + ".reservation")
    if reservation != {
        "schema_version": topology.RESERVATION_SCHEMA,
        "foundation_head_sha": head_sha,
        "foundation_run_id": run_id,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "closure_sha256": topology.LOCKED_CLOSURE_SHA256,
        "foundation_context_sha256": context_hash,
    }:
        raise _reject("Foundation reservation binding is invalid")
    inventory_raw = snapshot.leaves[prefix + "t-g03a-hosted-failure-inventory.tsv"].raw
    closure_raw = snapshot.leaves[prefix + topology.CLOSURE_RELATIVE_PATH.name].raw
    if (
        hashlib.sha256(inventory_raw).hexdigest() != topology.LOCKED_INVENTORY_SHA256
        or hashlib.sha256(closure_raw).hexdigest() != topology.LOCKED_CLOSURE_SHA256
    ):
        raise _reject("locked inventory or closure binding is invalid")
    rows = topology.load_inventory(
        topology.ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
    )
    closure_lines = closure_raw.decode("utf-8").splitlines()
    closure_columns = closure_lines[0].split("\t")
    closure_records = [
        dict(zip(closure_columns, line.split("\t"), strict=True))
        for line in closure_lines[1:]
    ]
    closure_records.sort(key=lambda item: item["test_node_id"])
    closure_nodes = tuple(item["test_node_id"] for item in closure_records)
    governed_nodes = {row.node_id for row in rows} | set(closure_nodes)
    baseline = _object_leaf(snapshot, prefix + "portable-root-baseline.json")
    baseline_required = {
        "schema_version", "foundation_run_id", "foundation_head_sha",
        "foundation_validation_date", "foundation_context_sha256",
        "inventory_sha256", "closure_sha256", "collector_policy",
        "candidate_node_ids", "candidate_file_sha256",
        "collection_report_sha256", "baseline_sha256",
    }
    candidate_raw = snapshot.leaves[prefix + "portable-root-candidates.txt"].raw
    collection_raw = snapshot.leaves[prefix + "portable-root-collection.governance.json"].raw
    candidates = baseline.get("candidate_node_ids")
    if (
        set(baseline) != baseline_required
        or baseline.get("schema_version") != topology.BASELINE_SCHEMA
        or baseline.get("foundation_run_id") != run_id
        or baseline.get("foundation_head_sha") != head_sha
        or baseline.get("foundation_validation_date") != context["foundation_validation_date"]
        or baseline.get("foundation_context_sha256") != context_hash
        or baseline.get("inventory_sha256") != topology.LOCKED_INVENTORY_SHA256
        or baseline.get("closure_sha256") != topology.LOCKED_CLOSURE_SHA256
        or baseline.get("baseline_sha256") != topology._baseline_payload_sha256(baseline)
        or not isinstance(candidates, list)
        or any(not isinstance(item, str) for item in candidates)
        or candidates != sorted(set(candidates))
        or not governed_nodes <= set(candidates)
        or candidate_raw != ("\n".join(candidates) + ("\n" if candidates else "")).encode()
        or baseline.get("candidate_file_sha256") != hashlib.sha256(candidate_raw).hexdigest()
        or baseline.get("collection_report_sha256") != hashlib.sha256(collection_raw).hexdigest()
    ):
        raise _reject("portable baseline schema or binding is invalid")
    try:
        policy = topology._validate_custody_policy(baseline.get("collector_policy"))
    except topology.TopologyError as exc:
        raise _reject("portable baseline custody policy is invalid") from exc
    collection = _object_leaf(snapshot, prefix + "portable-root-collection.governance.json")
    collection_tests = collection.get("tests")
    collected = [
        item.get("test_node_id") for item in collection_tests
        if isinstance(item, dict) and item.get("outcome") == "collected"
    ] if isinstance(collection_tests, list) else []
    if (
        set(collection) != {
            "schema_version", "component", "collection_only",
            "pytest_exit_status", "tests",
        }
        or collection.get("schema_version") != 1
        or collection.get("component") != "root"
        or collection.get("collection_only") is not True
        or collection.get("pytest_exit_status") != 0
        or not isinstance(collection_tests, list)
        or len(collection_tests) != len(candidates)
        or any(
            not isinstance(item, dict)
            or set(item) != {
                "test_node_id", "component", "outcome", "reason", "phase",
            }
            or item.get("component") != "root"
            or item.get("outcome") != "collected"
            or item.get("reason") != ""
            or item.get("phase") != "collection"
            for item in collection_tests
        )
        or sorted(collected) != candidates
    ):
        raise _reject("portable collection governance is invalid")
    remainder = _object_leaf(snapshot, prefix + "portable-root-remainder.json")
    remainder_required = {
        "schema_version", "foundation_run_id", "foundation_head_sha",
        "inventory_sha256", "closure_sha256", "baseline_sha256",
        "remainder_node_ids", "remainder_file_sha256", "remainder_sha256",
    }
    remainder_raw = snapshot.leaves[prefix + "portable-root-remainder.txt"].raw
    remainder_nodes = remainder.get("remainder_node_ids")
    if (
        set(remainder) != remainder_required
        or remainder.get("schema_version") != topology.REMAINDER_SCHEMA
        or remainder.get("foundation_run_id") != run_id
        or remainder.get("foundation_head_sha") != head_sha
        or remainder.get("inventory_sha256") != topology.LOCKED_INVENTORY_SHA256
        or remainder.get("closure_sha256") != topology.LOCKED_CLOSURE_SHA256
        or remainder.get("baseline_sha256") != baseline["baseline_sha256"]
        or not isinstance(remainder_nodes, list)
        or any(not isinstance(item, str) for item in remainder_nodes)
        or remainder_nodes != sorted(set(remainder_nodes))
        or set(remainder_nodes) != set(candidates) - governed_nodes
        or remainder_raw != ("\n".join(remainder_nodes) + ("\n" if remainder_nodes else "")).encode()
        or remainder.get("remainder_file_sha256") != hashlib.sha256(remainder_raw).hexdigest()
        or remainder.get("remainder_sha256") != topology._remainder_payload_sha256(remainder)
    ):
        raise _reject("portable remainder schema or binding is invalid")
    remainder_governance = _object_leaf(snapshot, prefix + "portable-root-remainder.governance.json")
    if (
        remainder_governance.get("component") != "root"
        or remainder_governance.get("pytest_exit_status") != 0
        or remainder_governance.get("custody_policy") != policy
        or not isinstance(remainder_governance.get("tests"), list)
    ):
        raise _reject("portable remainder governance is invalid")
    closure_governance_raw = snapshot.leaves[prefix + "portable-defect-closure.governance.json"].raw
    closure_governance = _object_leaf(snapshot, prefix + "portable-defect-closure.governance.json")
    closure_proof = _object_leaf(snapshot, prefix + "portable-defect-closure-proof.json")
    if (
        set(closure_proof) != topology.CLOSURE_PROOF_KEYS
        or closure_proof.get("schema_version") != topology.PORTABLE_CLOSURE_PROOF_SCHEMA
        or closure_proof.get("foundation_run_id") != run_id
        or closure_proof.get("foundation_head_sha") != head_sha
        or closure_proof.get("foundation_validation_date") != context["foundation_validation_date"]
        or closure_proof.get("foundation_context_sha256") != context_hash
        or closure_proof.get("inventory_sha256") != topology.LOCKED_INVENTORY_SHA256
        or closure_proof.get("closure_sha256") != topology.LOCKED_CLOSURE_SHA256
        or tuple(closure_proof.get("closure_node_ids", ())) != closure_nodes
        or closure_proof.get("closure_node_ids_sha256") != topology._ids_sha256(closure_nodes)
        or closure_proof.get("proof_command") != topology.CLOSURE_PROOF_COMMAND
        or closure_proof.get("proof_result_digests") != [
            item["proof_result_digest"] for item in closure_records
        ]
        or closure_proof.get("custody_policy") != policy
        or closure_proof.get("custody_policy_sha256") != topology._sha256(policy)
        or closure_proof.get("governance_report_sha256") != hashlib.sha256(closure_governance_raw).hexdigest()
        or closure_proof.get("outcome") != "PASS"
        or closure_proof.get("closure_proof_sha256") != topology._closure_proof_payload_sha256(closure_proof)
        or closure_governance.get("component") != "root"
        or closure_governance.get("pytest_exit_status") != 0
        or closure_governance.get("custody_policy") != policy
    ):
        raise _reject("portable closure proof or governance is invalid")
    try:
        topology._validate_exact_governance_bytes(
            closure_governance_raw, closure_nodes, policy,
        )
        topology._validate_exact_governance_bytes(
            snapshot.leaves[
                prefix + "portable-root-remainder.governance.json"
            ].raw,
            tuple(remainder_nodes), policy,
        )
    except topology.TopologyError as exc:
        raise _reject("portable topology governance schema is invalid") from exc
    try:
        for code in sorted(_CODES):
            marker_raw = snapshot.leaves[f"{prefix}{code}.json"].raw
            receipt_raw = snapshot.leaves[f"{prefix}{code}.artifacts/receipt.json"].raw
            if marker_raw != receipt_raw:
                raise _reject("Architecture-A marker/bundle receipt mismatch")
            receipt = topology.validate_receipt(
                receipt_raw, rows=rows, foundation_run_id=run_id,
                foundation_head_sha=head_sha, foundation_context=context,
            )
            if receipt.get("capability_or_authority_code") != code or receipt.get("outcome") == "FAIL":
                raise _reject("receipt filename, code, or outcome is invalid")
            receipts.append(receipt)
            bundle = f"{prefix}{code}.artifacts"
            governance_leaf = snapshot.leaves.get(f"{bundle}/governance.json")
            governance_raw = None if governance_leaf is None else governance_leaf.raw
            manifest_raw = snapshot.leaves[f"{bundle}/manifest.json"].raw
            if code.startswith("NATIVE-"):
                topology._validate_native_manifest_bytes(
                    manifest_raw, receipt, receipt_raw, governance_raw,
                )
            else:
                topology._validate_external_manifest_bytes(
                    manifest_raw, receipt, receipt_raw, governance_raw,
                )
            if (receipt.get("outcome") == "PASS") != (governance_raw is not None):
                raise _reject("receipt outcome/governance binding is invalid")
            if governance_raw is not None:
                topology._validate_exact_governance_bytes(
                    governance_raw, tuple(receipt["expected_node_ids"]), policy,
                )
    except topology.TopologyError as exc:
        raise _reject("topology receipt or bundle manifest schema is invalid") from exc
    aggregate = _object_leaf(snapshot, prefix + "aggregate.json")
    native_status = (
        "DEFERRED" if any(
            receipt["outcome"] == "DEFERRED"
            for receipt in receipts
            if str(receipt["capability_or_authority_code"]).startswith("NATIVE-")
        ) else "PASS"
    )
    external_status = (
        "DEFERRED" if any(
            receipt["outcome"] == "DEFERRED"
            for receipt in receipts
            if str(receipt["capability_or_authority_code"]).startswith("EXT-")
        ) else "PASS"
    )
    runtime_proof = (
        "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS"
        if "DEFERRED" in {native_status, external_status} else "COMPLETE"
    )
    if (
        set(aggregate) != {
            "portable_source_status", "native_capabilities_status",
            "external_authorities_status", "runtime_proof",
            "portable_root_remainder_status", "baseline_candidate_count",
        }
        or aggregate.get("portable_source_status") != "PASS"
        or aggregate.get("native_capabilities_status") != native_status
        or aggregate.get("external_authorities_status") != external_status
        or aggregate.get("runtime_proof") != runtime_proof
        or aggregate.get("portable_root_remainder_status") != "PASS"
        or aggregate.get("baseline_candidate_count") != str(len(candidates))
    ):
        raise _reject("topology aggregate schema or binding is invalid")
    semantic = topology.build_final_semantic_projection(
        context, baseline, aggregate, receipts,
    )
    semantic.update(_validate_final_governance(snapshot, aggregate))
    return semantic


def _validate_projection_layout(snapshot: _Snapshot) -> dict[str, object]:
    root_entries = set(snapshot.directories[""].entries) - {"manifest.json", "SHA256SUMS"}
    if root_entries not in (
        {"capability-topology", "test-governance"},
        {"capability-topology", "test-governance", "phase-evidence"},
    ):
        raise _reject("projection root contains an extra or missing entry")
    governance_entries = set(snapshot.directories["test-governance"].entries)
    if governance_entries not in ({"summary.json"}, {"error.json"}):
        raise _reject("test governance projection contains an unmanifested entry")
    topology = snapshot.directories.get("capability-topology")
    if topology is None:
        raise _reject("projection lacks capability topology")
    direct = set(topology.entries)
    if direct & _FLAT_RECEIPTS:
        raise _reject("flat native/external receipt fallback is rejected")
    allowed = set(_TOPOLOGY_FIXED_LEAVES)
    allowed.update(f"{code}.json" for code in _CODES)
    allowed.update(f"{code}.artifacts" for code in _CODES)
    if direct != allowed:
        raise _reject("capability topology fixed inventory is not exact")
    markers = {
        code for code in _CODES
        if f"capability-topology/{code}.json" in snapshot.leaves
    }
    bundles = {
        code for code in _CODES
        if f"capability-topology/{code}.artifacts" in snapshot.directories
    }
    if markers != _CODES or bundles != _CODES:
        raise _reject("Architecture-A marker/bundle set is incomplete")
    for code in sorted(markers):
        bundle_relative = f"capability-topology/{code}.artifacts"
        entries = set(snapshot.directories[bundle_relative].entries)
        if entries not in (
            {"receipt.json", "manifest.json"},
            {"receipt.json", "governance.json", "manifest.json"},
        ):
            raise _reject("Architecture-A bundle inventory is not exact")
        marker = snapshot.leaves[f"capability-topology/{code}.json"].raw
        receipt = snapshot.leaves[f"{bundle_relative}/receipt.json"].raw
        if marker != receipt:
            raise _reject("Architecture-A marker/bundle receipt mismatch")
    if "phase-evidence" in root_entries:
        if "phase-evidence/manifest.json" not in snapshot.leaves:
            raise _reject("phase evidence lacks its governed manifest")
        _validate_phase_manifest(snapshot)
    semantic = _validate_projection_schemas(snapshot)
    for relative, leaf in snapshot.leaves.items():
        scan_artifact_bytes(relative, leaf.raw)
    return semantic


def _validate_identity_inputs(
    head_sha: str, source_tree_sha256: str,
    semantic_projection: Mapping[str, object], run_metadata: Mapping[str, object],
) -> None:
    if not _HEAD.fullmatch(head_sha) or not _HEX64.fullmatch(source_tree_sha256):
        raise _reject("source identity binding is malformed")
    if set(run_metadata) != _RUN_METADATA_KEYS or any(
        not isinstance(run_metadata[key], str) for key in _RUN_METADATA_KEYS
    ):
        raise _reject("run metadata binding is malformed")
    if not isinstance(semantic_projection, Mapping):
        raise _reject("semantic projection binding is malformed")


def _write_leaf(directory_descriptor: int, name: str, content: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            0o600, dir_fd=directory_descriptor,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            view = view[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid() or info.st_gid != os.getegid()
            or info.st_size != len(content)
        ):
            raise OSError(errno.EPERM, "unsafe staged leaf")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _build_candidate(parent_descriptor: int, name: str, payloads: Mapping[str, bytes]) -> None:
    os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
    candidate_descriptor = os.open(
        name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
        dir_fd=parent_descriptor,
    )
    directories: dict[str, int] = {"": candidate_descriptor}
    try:
        needed = sorted(
            {
                parent.as_posix()
                for relative in payloads
                for parent in PurePosixPath(relative).parents
                if parent.as_posix() != "."
            },
            key=lambda value: (value.count("/"), value),
        )
        for relative in needed:
            path = PurePosixPath(relative)
            parent = path.parent.as_posix()
            parent = "" if parent == "." else parent
            os.mkdir(path.name, mode=0o700, dir_fd=directories[parent])
            directories[relative] = os.open(
                path.name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=directories[parent],
            )
        for relative in sorted(payloads):
            path = PurePosixPath(relative)
            parent = path.parent.as_posix()
            parent = "" if parent == "." else parent
            _write_leaf(directories[parent], path.name, payloads[relative])
        for relative in sorted(directories, key=lambda value: value.count("/"), reverse=True):
            os.fsync(directories[relative])
    finally:
        for relative, descriptor in list(directories.items())[::-1]:
            if relative:
                os.close(descriptor)
        os.close(candidate_descriptor)


def _seal_candidate_modes(
    *, parent_descriptor: int, candidate_name: str, candidate: Path,
) -> None:
    with _snapshot_tree(
        candidate, directory_mode=0o700, file_mode=0o600,
        retained_parent_descriptor=parent_descriptor,
        retained_name=candidate_name,
    ) as snapshot:
        for leaf in snapshot.leaves.values():
            os.fchmod(leaf.descriptor, 0o400)
            os.fsync(leaf.descriptor)
        for relative in sorted(
            snapshot.directories, key=lambda value: value.count("/"), reverse=True,
        ):
            directory = snapshot.directories[relative]
            os.fsync(directory.descriptor)
            os.fchmod(directory.descriptor, 0o500)


def _renameat2_noreplace(
    old_directory_descriptor: int, old_name: str,
    new_directory_descriptor: int, new_name: str,
) -> None:
    if (
        not old_name or not new_name or "/" in old_name or "/" in new_name
        or old_name in {".", ".."} or new_name in {".", ".."}
    ):
        raise _reject("atomic publication name is malformed")
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise _reject("renameat2 RENAME_NOREPLACE is unavailable") from exc
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        old_directory_descriptor, os.fsencode(old_name),
        new_directory_descriptor, os.fsencode(new_name), _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), new_name)


def _make_manifest(
    *, payloads: Mapping[str, bytes], head_sha: str, source_tree_sha256: str,
    semantic_projection: Mapping[str, object], run_metadata: Mapping[str, object],
) -> dict[str, object]:
    bound_semantic_projection = dict(semantic_projection)
    existing_tree = bound_semantic_projection.get("source_tree_sha256")
    if existing_tree is not None and existing_tree != source_tree_sha256:
        raise _reject("semantic source-tree binding mismatch")
    bound_semantic_projection["source_tree_sha256"] = source_tree_sha256
    files = [
        {
            "path": relative,
            "sha256": hashlib.sha256(payloads[relative]).hexdigest(),
            "size": len(payloads[relative]),
            "mode": "0400",
        }
        for relative in sorted(payloads)
    ]
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA,
        "head_sha": head_sha,
        "source_tree_sha256": source_tree_sha256,
        "semantic_projection": bound_semantic_projection,
        "semantic_result_sha256": semantic_result_sha256(bound_semantic_projection),
        "run_metadata": dict(run_metadata),
        "directory_mode": "0500",
        "files": files,
        "manifest_payload_sha256": "",
    }
    manifest["manifest_payload_sha256"] = manifest_payload_sha256(manifest)
    return manifest


def _checksum_bytes(payloads: Mapping[str, bytes]) -> bytes:
    return b"".join(
        f"{hashlib.sha256(payloads[relative]).hexdigest()}  {relative}\n".encode("ascii")
        for relative in sorted(payloads)
    )


def publish_evidence_set(
    *, staging_root: Path, destination: Path, head_sha: str,
    source_tree_sha256: str, semantic_projection: Mapping[str, object],
    run_metadata: Mapping[str, object],
    boundary_hook: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Seal retained staging bytes and atomically publish without replacement."""
    _validate_identity_inputs(
        head_sha, source_tree_sha256, semantic_projection, run_metadata,
    )
    destination = destination.absolute()
    staging_root = staging_root.absolute()
    # Reject an unsafe source before destination lineage creation can leave any
    # publication-side directory behind; the snapshot below then retains a
    # fresh lineage for the complete copy boundary.
    with _validate_lineage(staging_root, create=False):
        pass
    destination_lineage = _validate_lineage(destination.parent, create=True)
    parent_descriptor = destination_lineage.descriptor
    candidate_name = f".{destination.name}.staging-{secrets.token_hex(16)}"
    try:
        with destination_lineage:
            try:
                os.stat(destination.name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _reject("destination already exists")
            with _snapshot_tree(
                staging_root, directory_mode=0o700, file_mode=0o600,
            ) as source:
                _validate_projection_layout(source)
                payloads = {relative: leaf.raw for relative, leaf in source.leaves.items()}
                manifest = _make_manifest(
                    payloads=payloads, head_sha=head_sha,
                    source_tree_sha256=source_tree_sha256,
                    semantic_projection=semantic_projection, run_metadata=run_metadata,
                )
                manifest_raw = _canonical_json_bytes(manifest)
                complete_payloads = {**payloads, "manifest.json": manifest_raw}
                complete_payloads["SHA256SUMS"] = _checksum_bytes(complete_payloads)
                _build_candidate(parent_descriptor, candidate_name, complete_payloads)
                if boundary_hook is not None:
                    boundary_hook("after-manifest")
                source.postcheck()
            destination_lineage.postcheck()
            candidate = destination.parent / candidate_name
            _seal_candidate_modes(
                parent_descriptor=parent_descriptor,
                candidate_name=candidate_name,
                candidate=candidate,
            )
            with _snapshot_tree(
                candidate, directory_mode=0o500, file_mode=0o400,
                retained_parent_descriptor=parent_descriptor,
                retained_name=candidate_name,
            ) as sealed:
                _validate_complete_snapshot(sealed)
                candidate_identity = _stable_identity(os.fstat(sealed.root_descriptor))
                try:
                    _renameat2_noreplace(
                        parent_descriptor, candidate_name,
                        parent_descriptor, destination.name,
                    )
                except OSError as exc:
                    try:
                        destination_info = os.stat(
                            destination.name, dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        destination_info = None
                    try:
                        os.stat(candidate_name, dir_fd=parent_descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        source_exists = False
                    else:
                        source_exists = True
                    if (
                        destination_info is None
                        or source_exists
                        or _stable_identity(destination_info) != candidate_identity
                    ):
                        if exc.errno == errno.EEXIST:
                            raise _reject("destination already exists") from exc
                        raise _reject("atomic publication success is unresolved") from exc
                os.fsync(parent_descriptor)
                destination_lineage.postcheck()
                sealed.postcheck(
                    named_parent_descriptor=parent_descriptor,
                    named_root_name=destination.name,
                    refresh_root_identity=True,
                )
                validate_published_evidence(
                    destination, expected_head_sha=head_sha,
                    expected_source_tree_sha256=source_tree_sha256,
                    expected_semantic_projection=semantic_projection,
                    expected_root_identity=candidate_identity,
                )
                sealed.postcheck(
                    named_parent_descriptor=parent_descriptor,
                    named_root_name=destination.name,
                )
                destination_lineage.postcheck()
                return manifest
    except FirewallError:
        raise
    except OSError as exc:
        raise _reject("artifact publication failed closed") from exc


def _source_tree_identity(root: Path, head_sha: str) -> str:
    commands = (
        ["git", "diff-index", "--quiet", head_sha, "--"],
        ["git", "diff-files", "--quiet", "--"],
    )
    for command in commands:
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode != 0:
            raise _reject("source-tree binding requires a clean tracked worktree")
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if actual != head_sha:
        raise _reject("source head binding changed")
    tree = subprocess.run(
        ["git", "ls-tree", "-rz", "--full-tree", head_sha],
        cwd=root, check=True, capture_output=True,
    ).stdout
    return hashlib.sha256(tree).hexdigest()


def _parse_json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _reject(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise _reject(f"{label} is not an object")
    return value


def _final_payloads_from_raw(
    raw: _Snapshot, *, raw_root: Path, inventory: Path,
    foundation_context_path: Path, run_id: str, head_sha: str,
    governance_error: bool = False,
) -> tuple[dict[str, bytes], dict[str, object], dict[str, object]]:
    from scripts import check_test_governance as governance
    from scripts import t_g03_capability_topology as topology

    expected_root = {
        "capability-topology", "test-governance-topology",
        "t-g03a-hosted-failure-inventory.tsv",
        topology.CLOSURE_RELATIVE_PATH.name,
    }
    if set(raw.directories[""].entries) != expected_root:
        raise _reject("raw evidence root inventory is not exact")
    disclosure = topology.reconcile_portable_root_accounting(
        inventory=inventory, evidence_root=raw_root,
        run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    context = topology.load_foundation_context(
        foundation_context_path, run_id=run_id, head_sha=head_sha,
    )
    baseline = topology.load_portable_root_baseline(
        inventory=inventory, evidence_root=raw_root,
        run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    topology_directory = raw.directories.get("capability-topology")
    if topology_directory is None:
        raise _reject("raw evidence lacks capability topology")
    expected_topology = set(_RAW_TOPOLOGY_FIXED_LEAVES)
    expected_topology.update(f"{code}.json" for code in _CODES)
    expected_topology.update(f"{code}.artifacts" for code in _CODES)
    if set(topology_directory.entries) != expected_topology:
        raise _reject("raw capability topology inventory is not exact")
    receipts: list[dict[str, object]] = []
    for code in sorted(_CODES):
        marker_relative = f"capability-topology/{code}.json"
        bundle_relative = f"capability-topology/{code}.artifacts"
        marker = raw.leaves.get(marker_relative)
        bundle = raw.directories.get(bundle_relative)
        if marker is None or bundle is None:
            raise _reject("raw Architecture-A marker/bundle set is incomplete")
        receipt = topology.parse_receipt(marker.raw)
        receipts.append(receipt)
        expected_bundle = {"receipt.json", "manifest.json"}
        if receipt.get("outcome") == "PASS":
            expected_bundle.add("governance.json")
        if set(bundle.entries) != expected_bundle:
            raise _reject("raw Architecture-A bundle inventory is not exact")
        if raw.leaves[f"{bundle_relative}/receipt.json"].raw != marker.raw:
            raise _reject("raw Architecture-A marker/bundle receipt mismatch")
    report_directory = raw.directories.get("test-governance-topology")
    success_report_entries = {
        "dashboard-raw.json", "dashboard.log", "legacy-raw.json", "legacy.log",
        "test-governance.json",
    }
    allowed_error_entries = success_report_entries | {"test-governance-error.json"}
    received_report_entries = (
        set() if report_directory is None else set(report_directory.entries)
    )
    if (
        report_directory is None
        or (
            governance_error
            and (
                "test-governance-error.json" not in received_report_entries
                or not received_report_entries <= allowed_error_entries
            )
        )
        or (not governance_error and received_report_entries != success_report_entries)
    ):
        raise _reject("raw governed report inventory is not exact")
    semantic = topology.build_final_semantic_projection(
        context, baseline, disclosure, receipts,
    )
    payloads: dict[str, bytes] = {
        "capability-topology/t-g03a-hosted-failure-inventory.tsv":
            raw.leaves["t-g03a-hosted-failure-inventory.tsv"].raw,
        f"capability-topology/{topology.CLOSURE_RELATIVE_PATH.name}":
            raw.leaves[topology.CLOSURE_RELATIVE_PATH.name].raw,
        "capability-topology/aggregate.json": _canonical_json_bytes(disclosure),
    }
    if governance_error:
        error_report = _parse_json_object(
            raw.leaves[
                "test-governance-topology/test-governance-error.json"
            ].raw,
            label="governed error report",
        )
        error_raw, error_semantic = governance.build_final_governed_error(
            error_report,
        )
        payloads["test-governance/error.json"] = error_raw
        semantic["governance_error"] = error_semantic
        generated_at_utc = str(error_report["generated_at_utc"])
    else:
        report = _parse_json_object(
            raw.leaves["test-governance-topology/test-governance.json"].raw,
            label="governed report",
        )
        if report.get("capability_topology") != disclosure:
            raise _reject("governed report topology binding mismatch")
        summary_raw, selected_tests = governance.build_final_governed_summary(report)
        payloads["test-governance/summary.json"] = summary_raw
        semantic["selected_tests"] = selected_tests
        generated_at_utc = str(report["generated_at_utc"])
    for relative, leaf in raw.leaves.items():
        if relative.startswith("capability-topology/"):
            payloads[relative] = leaf.raw
    run_metadata = {
        "run_id": run_id,
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "generated_at_utc": generated_at_utc,
    }
    raw.postcheck()
    return payloads, semantic, run_metadata


def publish_final_evidence(
    *, raw_root: Path, destination: Path, inventory: Path,
    foundation_context_path: Path, repository_root: Path,
    governance_error: bool = False,
) -> dict[str, object]:
    """Validate raw P0 topology/governance evidence, then publish one final set."""
    from scripts import t_g03_capability_topology as topology

    run_id, head_sha = topology._active_foundation_identity()
    raw_root = raw_root.absolute()
    with _snapshot_tree(raw_root, directory_mode=0o700, file_mode=0o600) as raw:
        payloads, semantic, run_metadata = _final_payloads_from_raw(
            raw, raw_root=raw_root, inventory=inventory,
            foundation_context_path=foundation_context_path,
            run_id=run_id, head_sha=head_sha,
            governance_error=governance_error,
        )
    projection_name = f".final-projection-{secrets.token_hex(16)}"
    with _validate_lineage(raw_root, create=False) as raw_lineage:
        _build_candidate(raw_lineage.descriptor, projection_name, payloads)
        raw_lineage.postcheck()
    source_tree_sha256 = _source_tree_identity(repository_root, head_sha)
    return publish_evidence_set(
        staging_root=raw_root / projection_name,
        destination=destination,
        head_sha=head_sha,
        source_tree_sha256=source_tree_sha256,
        semantic_projection=semantic,
        run_metadata=run_metadata,
    )


def _strict_json(raw: bytes, *, relative: str) -> object:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _reject("published JSON is malformed") from exc
    if _canonical_json_bytes(value) != raw:
        raise _reject(f"published {relative} is not canonical")
    return value


def _validate_complete_snapshot(snapshot: _Snapshot) -> dict[str, object]:
    if "manifest.json" not in snapshot.leaves or "SHA256SUMS" not in snapshot.leaves:
        raise _reject("published evidence is missing manifest or checksum index")
    expected_semantic_projection = _validate_projection_layout(snapshot)
    manifest_value = _strict_json(snapshot.leaves["manifest.json"].raw, relative="manifest")
    if not isinstance(manifest_value, dict) or set(manifest_value) != _MANIFEST_KEYS:
        raise _reject("published manifest schema is incomplete")
    manifest = dict(manifest_value)
    if (
        manifest["schema_version"] != MANIFEST_SCHEMA
        or manifest["directory_mode"] != "0500"
        or not isinstance(manifest["head_sha"], str)
        or not _HEAD.fullmatch(manifest["head_sha"])
        or not isinstance(manifest["source_tree_sha256"], str)
        or not _HEX64.fullmatch(manifest["source_tree_sha256"])
        or not isinstance(manifest["semantic_projection"], dict)
        or not isinstance(manifest["semantic_result_sha256"], str)
        or manifest["semantic_result_sha256"]
        != semantic_result_sha256(manifest["semantic_projection"])
        or not isinstance(manifest["run_metadata"], dict)
        or set(manifest["run_metadata"]) != _RUN_METADATA_KEYS
        or manifest["manifest_payload_sha256"] != manifest_payload_sha256(manifest)
    ):
        raise _reject("published manifest binding is invalid")
    observed_semantic_projection = dict(manifest["semantic_projection"])
    if (
        observed_semantic_projection.pop("source_tree_sha256", None)
        != manifest["source_tree_sha256"]
        or observed_semantic_projection != expected_semantic_projection
    ):
        raise _reject("published semantic projection does not bind retained evidence")
    raw_files = manifest["files"]
    if not isinstance(raw_files, list):
        raise _reject("published manifest file list is malformed")
    listed: dict[str, dict[str, object]] = {}
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != _FILE_KEYS:
            raise _reject("published manifest file entry is malformed")
        relative = item["path"]
        if (
            not isinstance(relative, str) or relative in listed
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or item["mode"] != "0400"
            or not isinstance(item["sha256"], str) or not _HEX64.fullmatch(item["sha256"])
            or not isinstance(item["size"], int) or isinstance(item["size"], bool)
            or item["size"] < 0
        ):
            raise _reject("published manifest file entry binding is invalid")
        listed[relative] = item
    actual_payloads = set(snapshot.leaves) - {"manifest.json", "SHA256SUMS"}
    if set(listed) != actual_payloads:
        raise _reject("published manifest is partial or contains an extra path")
    for relative, item in listed.items():
        raw = snapshot.leaves[relative].raw
        if item["sha256"] != hashlib.sha256(raw).hexdigest() or item["size"] != len(raw):
            raise _reject("published manifest file digest or size mismatch")
        scan_artifact_bytes(relative, raw)
    checksum_raw = snapshot.leaves["SHA256SUMS"].raw
    try:
        checksum_text = checksum_raw.decode("ascii")
    except UnicodeError as exc:
        raise _reject("published checksum index is invalid") from exc
    lines = checksum_text.splitlines()
    checksum_entries: dict[str, str] = {}
    for line in lines:
        matched = _CHECKSUM.fullmatch(line)
        if matched is None or matched.group(2) in checksum_entries:
            raise _reject("published checksum index is malformed or duplicate")
        checksum_entries[matched.group(2)] = matched.group(1)
    expected_checksum_paths = set(snapshot.leaves) - {"SHA256SUMS"}
    if set(checksum_entries) != expected_checksum_paths or list(checksum_entries) != sorted(checksum_entries):
        raise _reject("published checksum index is incomplete or unsorted")
    expected_bytes = _checksum_bytes({
        relative: snapshot.leaves[relative].raw
        for relative in expected_checksum_paths
    })
    if checksum_raw != expected_bytes:
        raise _reject("published checksum mismatch")
    return manifest


def validate_published_evidence(
    destination: Path, *, expected_head_sha: str | None = None,
    expected_source_tree_sha256: str | None = None,
    expected_semantic_projection: Mapping[str, object] | None = None,
    expected_root_identity: tuple[int, int, int, int, int] | None = None,
) -> dict[str, object]:
    with _snapshot_tree(
        destination.absolute(), directory_mode=0o500, file_mode=0o400,
    ) as snapshot:
        if (
            expected_root_identity is not None
            and _stable_identity(os.fstat(snapshot.root_descriptor))
            != expected_root_identity
        ):
            raise _reject("published root identity changed before validation")
        manifest = _validate_complete_snapshot(snapshot)
        if expected_head_sha is not None and manifest["head_sha"] != expected_head_sha:
            raise _reject("published head binding mismatch")
        if (
            expected_source_tree_sha256 is not None
            and manifest["source_tree_sha256"] != expected_source_tree_sha256
        ):
            raise _reject("published source-tree binding mismatch")
        if (
            expected_semantic_projection is not None
            and manifest["semantic_result_sha256"] != semantic_result_sha256({
                **expected_semantic_projection,
                "source_tree_sha256": (
                    expected_source_tree_sha256
                    if expected_source_tree_sha256 is not None
                    else manifest["source_tree_sha256"]
                ),
            })
        ):
            raise _reject("published semantic binding mismatch")
        snapshot.postcheck()
        return manifest


def _read_object(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _reject("input document is unreadable") from exc
    if not isinstance(value, dict) or _canonical_json_bytes(value) != raw:
        raise _reject("input document is not a canonical object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("publish", "publish-error", "publish-projection", "validate"),
    )
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--foundation-context-path", type=Path)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--head-sha")
    parser.add_argument("--source-tree-sha256")
    parser.add_argument("--semantic-projection", type=Path)
    parser.add_argument("--run-metadata", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "validate":
            validate_published_evidence(args.destination)
        elif args.action == "publish-projection":
            if None in (
                args.staging_root, args.head_sha, args.source_tree_sha256,
                args.semantic_projection, args.run_metadata,
            ):
                raise _reject("publish inputs are incomplete")
            publish_evidence_set(
                staging_root=args.staging_root,
                destination=args.destination,
                head_sha=args.head_sha,
                source_tree_sha256=args.source_tree_sha256,
                semantic_projection=_read_object(args.semantic_projection),
                run_metadata=_read_object(args.run_metadata),
            )
        else:
            if None in (
                args.raw_root, args.inventory, args.foundation_context_path,
                args.repository_root,
            ):
                raise _reject("final publication inputs are incomplete")
            publish_final_evidence(
                raw_root=args.raw_root, destination=args.destination,
                inventory=args.inventory,
                foundation_context_path=args.foundation_context_path,
                repository_root=args.repository_root,
                governance_error=args.action == "publish-error",
            )
    except (FirewallError, OSError, ValueError) as exc:
        if isinstance(exc, FirewallError):
            fields = [exc.code, exc.category, exc.relative_path, exc.sha256]
            print("artifact firewall: " + " ".join(field for field in fields if field), file=sys.stderr)
        else:
            print("artifact firewall: ARTIFACT_FIREWALL_REJECTED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
