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
    descriptor: int
    identity: tuple[int, int, int, int, int]
    entries: tuple[str, ...]


@dataclass
class _Snapshot:
    root_path: Path
    root_descriptor: int
    directories: dict[str, _Directory]
    leaves: dict[str, _Leaf]

    def postcheck(self, *, named_root: Path | None = None) -> None:
        root_path = self.root_path if named_root is None else named_root
        try:
            named = root_path.lstat()
            if _stable_identity(named) != _stable_identity(os.fstat(self.root_descriptor)):
                raise OSError
            for directory in self.directories.values():
                if tuple(sorted(os.listdir(directory.descriptor))) != directory.entries:
                    raise OSError
                held = os.fstat(directory.descriptor)
                if _stable_identity(held) != directory.identity:
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


def _validate_lineage(path: Path, *, create: bool) -> None:
    absolute = path.absolute()
    lineage = list(reversed((absolute, *absolute.parents)))
    for index, ancestor in enumerate(lineage):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            if not create:
                raise _reject("artifact path ancestor is absent")
            try:
                ancestor.mkdir(mode=0o700)
                info = ancestor.lstat()
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
        if index == len(lineage) - 1 and info.st_uid != os.geteuid():
            raise _reject("artifact root is not current-user owned")


@contextmanager
def _snapshot_tree(
    root: Path, *, directory_mode: int, file_mode: int,
) -> Iterator[_Snapshot]:
    _validate_lineage(root, create=False)
    root_descriptor = -1
    directories: dict[str, _Directory] = {}
    leaves: dict[str, _Leaf] = {}
    try:
        before = root.lstat()
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_IMODE(before.st_mode) != directory_mode
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
        ):
            raise _reject("artifact root is not a private governed directory")
        root_descriptor = os.open(root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
        if _identity(os.fstat(root_descriptor)) != _identity(before):
            raise _reject("artifact root identity changed before snapshot")

        def visit(descriptor: int, relative: str) -> None:
            entries = tuple(sorted(os.listdir(descriptor)))
            directories[relative] = _Directory(
                descriptor, _stable_identity(os.fstat(descriptor)), entries,
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
                    if _identity(os.fstat(child)) != _identity(info):
                        os.close(child)
                        raise _reject("artifact directory identity changed")
                    visit(child, child_relative)
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
                opened = os.fstat(leaf_descriptor)
                if _identity(opened) != _identity(info):
                    os.close(leaf_descriptor)
                    raise _reject("artifact leaf identity changed before read")
                raw = _read_fd(leaf_descriptor)
                if _identity(os.fstat(leaf_descriptor)) != _identity(opened):
                    os.close(leaf_descriptor)
                    raise _reject("artifact leaf changed during read")
                leaves[child_relative] = _Leaf(
                    descriptor, name, leaf_descriptor, _identity(opened), raw,
                )

        visit(root_descriptor, "")
        snapshot = _Snapshot(root, root_descriptor, directories, leaves)
        snapshot.postcheck()
        yield snapshot
    except FirewallError:
        raise
    except OSError as exc:
        raise _reject("artifact tree cannot be retained safely") from exc
    finally:
        if root_descriptor >= 0:
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
            try:
                os.close(root_descriptor)
            except OSError:
                pass


def _validate_projection_layout(snapshot: _Snapshot) -> None:
    root_entries = set(snapshot.directories[""].entries) - {"manifest.json", "SHA256SUMS"}
    if root_entries not in (
        {"capability-topology", "test-governance"},
        {"capability-topology", "test-governance", "phase-evidence"},
    ):
        raise _reject("projection root contains an extra or missing entry")
    if "test-governance/summary.json" not in snapshot.leaves:
        raise _reject("projection lacks governed summary")
    governance_entries = set(snapshot.directories["test-governance"].entries)
    if not governance_entries <= {"summary.json", "error.json"}:
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
    if not direct <= allowed:
        raise _reject("capability topology contains an unmanifested entry")
    markers = {
        code for code in _CODES
        if f"capability-topology/{code}.json" in snapshot.leaves
    }
    bundles = {
        code for code in _CODES
        if f"capability-topology/{code}.artifacts" in snapshot.directories
    }
    if markers != bundles or not markers:
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
    for relative, leaf in snapshot.leaves.items():
        scan_artifact_bytes(relative, leaf.raw)


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


def _seal_candidate_modes(candidate: Path) -> None:
    with _snapshot_tree(candidate, directory_mode=0o700, file_mode=0o600) as snapshot:
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
        "semantic_projection": dict(semantic_projection),
        "semantic_result_sha256": semantic_result_sha256(semantic_projection),
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
    _validate_lineage(staging_root, create=False)
    _validate_lineage(destination.parent, create=True)
    parent_descriptor = os.open(
        destination.parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
    )
    candidate_name = f".{destination.name}.staging-{secrets.token_hex(16)}"
    try:
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
        candidate = destination.parent / candidate_name
        _seal_candidate_modes(candidate)
        with _snapshot_tree(candidate, directory_mode=0o500, file_mode=0o400) as sealed:
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
            sealed.postcheck(named_root=destination)
        validate_published_evidence(
            destination, expected_head_sha=head_sha,
            expected_source_tree_sha256=source_tree_sha256,
            expected_semantic_projection=semantic_projection,
        )
        return manifest
    except FirewallError:
        raise
    except OSError as exc:
        raise _reject("artifact publication failed closed") from exc
    finally:
        os.close(parent_descriptor)


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
    expected_report_entries = {
        "dashboard-raw.json", "dashboard.log", "legacy-raw.json", "legacy.log",
        "test-governance.json",
    }
    if report_directory is None or set(report_directory.entries) != expected_report_entries:
        raise _reject("raw governed report inventory is not exact")
    report = _parse_json_object(
        raw.leaves["test-governance-topology/test-governance.json"].raw,
        label="governed report",
    )
    if report.get("capability_topology") != disclosure:
        raise _reject("governed report topology binding mismatch")
    summary_raw, selected_tests = governance.build_final_governed_summary(report)
    semantic = topology.build_final_semantic_projection(
        context, baseline, disclosure, receipts,
    )
    semantic["selected_tests"] = selected_tests
    payloads: dict[str, bytes] = {
        "capability-topology/t-g03a-hosted-failure-inventory.tsv":
            raw.leaves["t-g03a-hosted-failure-inventory.tsv"].raw,
        f"capability-topology/{topology.CLOSURE_RELATIVE_PATH.name}":
            raw.leaves[topology.CLOSURE_RELATIVE_PATH.name].raw,
        "capability-topology/aggregate.json": _canonical_json_bytes(disclosure),
        "test-governance/summary.json": summary_raw,
    }
    for relative, leaf in raw.leaves.items():
        if relative.startswith("capability-topology/"):
            payloads[relative] = leaf.raw
    run_metadata = {
        "run_id": run_id,
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "generated_at_utc": str(report["generated_at_utc"]),
    }
    raw.postcheck()
    return payloads, semantic, run_metadata


def publish_final_evidence(
    *, raw_root: Path, destination: Path, inventory: Path,
    foundation_context_path: Path, repository_root: Path,
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
        )
    projection_name = f".final-projection-{secrets.token_hex(16)}"
    raw_descriptor = os.open(
        raw_root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
    )
    try:
        _build_candidate(raw_descriptor, projection_name, payloads)
    finally:
        os.close(raw_descriptor)
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
    _validate_projection_layout(snapshot)
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
) -> dict[str, object]:
    with _snapshot_tree(
        destination.absolute(), directory_mode=0o500, file_mode=0o400,
    ) as snapshot:
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
            and manifest["semantic_result_sha256"]
            != semantic_result_sha256(expected_semantic_projection)
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
    parser.add_argument("action", choices=("publish", "publish-projection", "validate"))
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
