#!/usr/bin/env python3
"""Materialize and verify the fixed P1-U04 host-native authority snapshot."""

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
import posixpath
import secrets
import stat
import sys
from collections.abc import Iterator, Sequence


SOURCE_DESTINATION_MAPPINGS = (
    ("/usr/bin/python3.12", "/usr/bin/python3.12"),
    ("/usr/lib/python3.12", "/usr/lib/python3.12"),
    ("/usr/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu"),
    ("/usr/lib/gcc/x86_64-linux-gnu/13", "/usr/lib/gcc/x86_64-linux-gnu/13"),
    ("/usr/libexec/gcc/x86_64-linux-gnu/13", "/usr/libexec/gcc/x86_64-linux-gnu/13"),
    ("/usr/include", "/usr/include"),
    ("/usr/local/include", "/usr/local/include"),
    ("/usr/bin/ar", "/usr/bin/ar"),
    ("/usr/bin/ld", "/usr/bin/ld"),
    ("/usr/bin/strip", "/usr/bin/strip"),
    ("/usr/bin/x86_64-linux-gnu-ar", "/usr/bin/x86_64-linux-gnu-ar"),
    ("/usr/bin/x86_64-linux-gnu-ld", "/usr/bin/x86_64-linux-gnu-ld"),
    ("/usr/bin/x86_64-linux-gnu-ld.bfd", "/usr/bin/x86_64-linux-gnu-ld.bfd"),
    ("/usr/bin/x86_64-linux-gnu-strip", "/usr/bin/x86_64-linux-gnu-strip"),
)
SNAPSHOT_ROOT = Path(
    "/home/thenam176/.cache/trading-agent/"
    "nautilus-v1.231-native-authority-3ceeb7a55c5d"
)
RECEIPT_PATH = SNAPSHOT_ROOT.with_name(SNAPSHOT_ROOT.name + "-receipt.json")

_SOURCE_DESTINATION_MAPPINGS = SOURCE_DESTINATION_MAPPINGS
_SNAPSHOT_ROOT = SNAPSHOT_ROOT
_RECEIPT_PATH = RECEIPT_PATH
_AUTHORITY = "P1_U04_IMMUTABLE_NATIVE_AUTHORITY_SNAPSHOT_V1"
_THREAT_MODEL = "COOPERATIVE_HOST"
_DEAD_EXTERNAL_LINKS = frozenset(
    {
        (
            "/usr/lib/python3.12/sitecustomize.py",
            "/etc/python3.12/sitecustomize.py",
        ),
        (
            "/usr/lib/x86_64-linux-gnu/libblas.so.3",
            "/etc/alternatives/libblas.so.3-x86_64-linux-gnu",
        ),
        (
            "/usr/lib/x86_64-linux-gnu/liblapack.so.3",
            "/etc/alternatives/liblapack.so.3-x86_64-linux-gnu",
        ),
    }
)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_PATH = getattr(os, "O_PATH", os.O_RDONLY)
_RENAME_NOREPLACE = 1
_RECEIPT_FIELDS = {
    "authority",
    "entries",
    "mappings",
    "payload_tree_sha256",
    "schema_version",
    "source_after_sha256",
    "source_before_sha256",
    "threat_model",
}
_POLICY_FIELDS = {
    "authority",
    "mappings",
    "payload_tree_sha256",
    "receipt_path",
    "receipt_sha256",
    "root",
    "schema_version",
    "threat_model",
}


class SnapshotError(RuntimeError):
    """The fixed snapshot authority failed closed."""


@dataclass(frozen=True, slots=True)
class SnapshotMount:
    fd: int
    destination: str


@dataclass(frozen=True, slots=True)
class VerifiedSnapshot:
    root_fd: int
    mounts: tuple[SnapshotMount, ...]
    receipt: dict[str, object]


def _canonical(document: object) -> bytes:
    return (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _mode(info: os.stat_result) -> str:
    return f"{stat.S_IMODE(info.st_mode):04o}"


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _mapping_records(
    mappings: Sequence[tuple[str, str]],
) -> list[dict[str, str]]:
    return [
        {"destination": destination, "source": source}
        for source, destination in mappings
    ]


def _validate_mappings(mappings: Sequence[tuple[str, str]]) -> None:
    if not mappings:
        raise SnapshotError("native snapshot mappings are empty")
    sources: set[str] = set()
    destinations: set[str] = set()
    for source, destination in mappings:
        for value in (source, destination):
            path = PurePosixPath(value)
            if (
                not path.is_absolute()
                or value != path.as_posix()
                or value == "/"
                or ".." in path.parts
                or "." in path.parts
            ):
                raise SnapshotError("native snapshot mapping is not canonical")
        if source in sources or destination in destinations:
            raise SnapshotError("native snapshot mapping is duplicated")
        sources.add(source)
        destinations.add(destination)


def _source_writable(info: os.stat_result) -> bool:
    permissions = stat.S_IMODE(info.st_mode)
    if info.st_uid == os.geteuid():
        return bool(permissions & stat.S_IWUSR)
    if info.st_gid in {os.getegid(), *os.getgroups()}:
        return bool(permissions & stat.S_IWGRP)
    return bool(permissions & stat.S_IWOTH)


def _source_metadata(info: os.stat_result) -> dict[str, object]:
    return {
        "source_gid": info.st_gid,
        "source_mode": _mode(info),
        "source_nlink": info.st_nlink,
        "source_uid": info.st_uid,
    }


def _open_absolute_parent(path: str) -> tuple[int, str]:
    parts = PurePosixPath(path).parts
    descriptor = os.open("/", os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
    try:
        for part in parts[1:-1]:
            child = os.open(
                part,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular(descriptor: int) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if _identity(before) != _identity(after):
        raise SnapshotError("native snapshot source entry drifted while reading")
    return b"".join(chunks), after


def _source_entry(
    parent_fd: int,
    name: str,
    namespace_path: str,
) -> list[dict[str, object]]:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"native snapshot source is unavailable: {namespace_path}") from exc
    if not stat.S_ISLNK(before.st_mode) and _source_writable(before):
        raise SnapshotError(f"native snapshot source is writable: {namespace_path}")
    metadata = _source_metadata(before)
    if stat.S_ISREG(before.st_mode):
        try:
            descriptor = os.open(
                name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=parent_fd
            )
            try:
                raw, opened = _read_regular(descriptor)
            finally:
                os.close(descriptor)
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"native snapshot source drifted: {namespace_path}") from exc
        if _identity(before) != _identity(opened) or _identity(before) != _identity(after):
            raise SnapshotError(f"native snapshot source drifted: {namespace_path}")
        return [
            {
                "path": namespace_path,
                "sha256": _sha256(raw),
                "size": len(raw),
                "snapshot_mode": "0500" if stat.S_IMODE(before.st_mode) & 0o111 else "0400",
                **metadata,
                "type": "file",
            }
        ]
    if stat.S_ISLNK(before.st_mode):
        try:
            target = os.readlink(name, dir_fd=parent_fd)
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            final_target = os.readlink(name, dir_fd=parent_fd)
        except OSError as exc:
            raise SnapshotError(f"native snapshot source symlink drifted: {namespace_path}") from exc
        if _identity(before) != _identity(after) or target != final_target:
            raise SnapshotError(f"native snapshot source symlink drifted: {namespace_path}")
        return [
            {
                "path": namespace_path,
                "snapshot_mode": "0777",
                **metadata,
                "target": target,
                "type": "symlink",
            }
        ]
    if not stat.S_ISDIR(before.st_mode):
        raise SnapshotError(f"native snapshot source special entry: {namespace_path}")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise SnapshotError(f"native snapshot source directory drifted: {namespace_path}") from exc
    records: list[dict[str, object]] = [
        {
            "path": namespace_path,
            "snapshot_mode": "0500",
            **metadata,
            "type": "directory",
        }
    ]
    try:
        if _identity(before) != _identity(os.fstat(descriptor)):
            raise SnapshotError(f"native snapshot source directory drifted: {namespace_path}")
        with os.scandir(descriptor) as iterator:
            entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        for entry in entries:
            records.extend(
                _source_entry(
                    descriptor,
                    entry.name,
                    f"{namespace_path}/{entry.name}",
                )
            )
        final_descriptor = os.fstat(descriptor)
        final_path = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _identity(before) != _identity(final_descriptor) or _identity(before) != _identity(final_path):
            raise SnapshotError(f"native snapshot source directory drifted: {namespace_path}")
    finally:
        os.close(descriptor)
    return records


def _source_inventory(
    mappings: Sequence[tuple[str, str]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for source, destination in mappings:
        parent_fd = -1
        try:
            parent_fd, name = _open_absolute_parent(source)
            records.extend(_source_entry(parent_fd, name, destination))
        except SnapshotError:
            raise
        except OSError as exc:
            raise SnapshotError(f"native snapshot source is unavailable: {source}") from exc
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
    records.sort(key=lambda record: os.fsencode(str(record["path"])))
    _validate_symlinks(records, mappings)
    return records


def _payload_records(entries: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for entry in entries:
        record = {
            "mode": entry["snapshot_mode"],
            "path": entry["path"],
            "type": entry["type"],
        }
        if entry["type"] == "file":
            record.update({"sha256": entry["sha256"], "size": entry["size"]})
        elif entry["type"] == "symlink":
            record["target"] = entry["target"]
        payload.append(record)
    return payload


def _payload_digest(
    entries: Sequence[dict[str, object]], mappings: Sequence[tuple[str, str]]
) -> str:
    return _sha256(
        _canonical(
            {
                "entries": _payload_records(entries),
                "mappings": _mapping_records(mappings),
            }
        )
    )


def _lexical_target(path: str, target: str) -> str:
    candidate = target if target.startswith("/") else posixpath.join(posixpath.dirname(path), target)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        raise SnapshotError(f"native snapshot symlink target is invalid: {path}")
    return normalized


def _within_mapping(path: str, mappings: Sequence[tuple[str, str]]) -> bool:
    return any(path == destination or path.startswith(destination + "/") for _source, destination in mappings)


def _validate_symlinks(
    entries: Sequence[dict[str, object]],
    mappings: Sequence[tuple[str, str]],
) -> None:
    records = {str(entry["path"]): entry for entry in entries}
    observed_external_links: set[tuple[str, str]] = set()
    for entry in entries:
        if entry.get("type") != "symlink":
            continue
        path = str(entry["path"])
        target = str(entry.get("target"))
        if (path, target) in _DEAD_EXTERNAL_LINKS:
            observed_external_links.add((path, target))
            continue
        current = _lexical_target(path, target)
        seen = {path}
        for _ in range(64):
            if not _within_mapping(current, mappings) or current not in records:
                raise SnapshotError(f"native snapshot symlink escapes or is broken: {path}")
            target_record = records[current]
            if target_record.get("type") != "symlink":
                break
            if current in seen:
                raise SnapshotError(f"native snapshot symlink loop: {path}")
            seen.add(current)
            current = _lexical_target(current, str(target_record.get("target")))
        else:
            raise SnapshotError(f"native snapshot symlink loop: {path}")
    if (
        tuple(mappings) == SOURCE_DESTINATION_MAPPINGS
        and observed_external_links != _DEAD_EXTERNAL_LINKS
    ):
        raise SnapshotError("native snapshot exact external symlink set drifted")


def _ensure_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise SnapshotError("native snapshot staging directory is unsafe")
        return descriptor
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _destination_parent(root_fd: int, destination: str) -> tuple[int, str]:
    parts = PurePosixPath(destination).parts[1:]
    descriptor = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = _ensure_directory(descriptor, part)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _copy_regular(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
    namespace_path: str,
    expected: dict[str, object],
) -> dict[str, object]:
    descriptor = output = -1
    try:
        descriptor = os.open(
            source_name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=source_fd
        )
        source_before = os.fstat(descriptor)
        output = os.open(
            destination_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            0o600,
            dir_fd=destination_fd,
        )
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            view = memoryview(chunk)
            while view:
                written = os.write(output, view)
                if written <= 0:
                    raise OSError(errno.EIO, "short native snapshot write")
                view = view[written:]
            digest.update(chunk)
            size += len(chunk)
        os.fchmod(output, int(str(expected["snapshot_mode"]), 8))
        os.fsync(output)
        source_after = os.fstat(descriptor)
        destination = os.fstat(output)
        if (
            _identity(source_before) != _identity(source_after)
            or source_before.st_size != expected["size"]
            or digest.hexdigest() != expected["sha256"]
            or not stat.S_ISREG(destination.st_mode)
            or destination.st_nlink != 1
            or destination.st_uid != os.geteuid()
            or _mode(destination) != expected["snapshot_mode"]
            or destination.st_size != size
        ):
            raise SnapshotError(f"native snapshot regular copy drifted: {namespace_path}")
        return {
            "mode": _mode(destination),
            "path": namespace_path,
            "sha256": digest.hexdigest(),
            "size": size,
            "type": "file",
        }
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError(f"native snapshot regular copy failed: {namespace_path}") from exc
    finally:
        if output >= 0:
            os.close(output)
        if descriptor >= 0:
            os.close(descriptor)


def _copy_entry(
    source_parent: int,
    source_name: str,
    destination_parent: int,
    destination_name: str,
    namespace_path: str,
    expected_by_path: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    expected = expected_by_path[namespace_path]
    try:
        source_before = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"native snapshot source drifted before copy: {namespace_path}") from exc
    records: list[dict[str, object]] = []
    if expected["type"] == "file":
        records.append(
            _copy_regular(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                namespace_path,
                expected,
            )
        )
    elif expected["type"] == "symlink":
        try:
            target = os.readlink(source_name, dir_fd=source_parent)
            os.symlink(target, destination_name, dir_fd=destination_parent)
            source_after = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"native snapshot symlink copy failed: {namespace_path}") from exc
        if target != expected["target"] or _identity(source_before) != _identity(source_after):
            raise SnapshotError(f"native snapshot source symlink drifted: {namespace_path}")
        records.append(
            {"mode": "0777", "path": namespace_path, "target": target, "type": "symlink"}
        )
    elif expected["type"] == "directory":
        try:
            source = os.open(
                source_name,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=source_parent,
            )
            os.mkdir(destination_name, 0o700, dir_fd=destination_parent)
            destination = os.open(
                destination_name,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=destination_parent,
            )
        except OSError as exc:
            for descriptor in (locals().get("source", -1), locals().get("destination", -1)):
                if isinstance(descriptor, int) and descriptor >= 0:
                    os.close(descriptor)
            raise SnapshotError(f"native snapshot directory copy failed: {namespace_path}") from exc
        try:
            if _identity(source_before) != _identity(os.fstat(source)):
                raise SnapshotError(f"native snapshot source directory drifted: {namespace_path}")
            records.append({"mode": "0500", "path": namespace_path, "type": "directory"})
            with os.scandir(source) as iterator:
                entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
            for entry in entries:
                records.extend(
                    _copy_entry(
                        source,
                        entry.name,
                        destination,
                        entry.name,
                        f"{namespace_path}/{entry.name}",
                        expected_by_path,
                    )
                )
            os.fchmod(destination, 0o500)
            os.fsync(destination)
            source_after = os.fstat(source)
            source_path_after = os.stat(
                source_name, dir_fd=source_parent, follow_symlinks=False
            )
            if _identity(source_before) != _identity(source_after) or _identity(source_before) != _identity(source_path_after):
                raise SnapshotError(f"native snapshot source directory drifted: {namespace_path}")
        finally:
            os.close(destination)
            os.close(source)
    else:
        raise SnapshotError(f"native snapshot source special entry: {namespace_path}")
    return records


def _copy_mappings(
    root_fd: int,
    mappings: Sequence[tuple[str, str]],
    expected_entries: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    expected_by_path = {str(entry["path"]): entry for entry in expected_entries}
    records: list[dict[str, object]] = []
    for source, destination in mappings:
        source_parent = destination_parent = -1
        try:
            source_parent, source_name = _open_absolute_parent(source)
            destination_parent, destination_name = _destination_parent(root_fd, destination)
            records.extend(
                _copy_entry(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    destination,
                    expected_by_path,
                )
            )
        finally:
            if source_parent >= 0:
                os.close(source_parent)
            if destination_parent >= 0:
                os.close(destination_parent)
    records.sort(key=lambda record: os.fsencode(str(record["path"])))
    return records


def _snapshot_entry(parent_fd: int, name: str, namespace_path: str) -> list[dict[str, object]]:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"native snapshot payload is missing: {namespace_path}") from exc
    if info.st_uid != os.geteuid():
        raise SnapshotError(f"native snapshot payload owner drifted: {namespace_path}")
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1 or _mode(info) not in {"0400", "0500"}:
            raise SnapshotError(f"native snapshot payload file is unsafe: {namespace_path}")
        try:
            descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=parent_fd)
            try:
                raw, opened = _read_regular(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise SnapshotError(f"native snapshot payload file drifted: {namespace_path}") from exc
        if _identity(info) != _identity(opened):
            raise SnapshotError(f"native snapshot payload file drifted: {namespace_path}")
        return [
            {
                "mode": _mode(info),
                "path": namespace_path,
                "sha256": _sha256(raw),
                "size": len(raw),
                "type": "file",
            }
        ]
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.readlink(name, dir_fd=parent_fd)
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"native snapshot payload symlink drifted: {namespace_path}") from exc
        if _identity(info) != _identity(after):
            raise SnapshotError(f"native snapshot payload symlink drifted: {namespace_path}")
        return [{"mode": "0777", "path": namespace_path, "target": target, "type": "symlink"}]
    if not stat.S_ISDIR(info.st_mode) or _mode(info) != "0500":
        raise SnapshotError(f"native snapshot payload special or writable entry: {namespace_path}")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise SnapshotError(f"native snapshot payload directory drifted: {namespace_path}") from exc
    records: list[dict[str, object]] = [
        {"mode": "0500", "path": namespace_path, "type": "directory"}
    ]
    try:
        if _identity(info) != _identity(os.fstat(descriptor)):
            raise SnapshotError(f"native snapshot payload directory drifted: {namespace_path}")
        with os.scandir(descriptor) as iterator:
            entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        for entry in entries:
            records.extend(
                _snapshot_entry(
                    descriptor, entry.name, f"{namespace_path}/{entry.name}"
                )
            )
        if _identity(info) != _identity(os.fstat(descriptor)):
            raise SnapshotError(f"native snapshot payload directory drifted: {namespace_path}")
    finally:
        os.close(descriptor)
    return records


def _open_snapshot_parent(root_fd: int, destination: str) -> tuple[int, str]:
    descriptor = os.dup(root_fd)
    parts = PurePosixPath(destination).parts[1:]
    try:
        for part in parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _observed_paths(root_fd: int) -> set[str]:
    observed: set[str] = set()

    def walk(descriptor: int, prefix: PurePosixPath) -> None:
        with os.scandir(descriptor) as iterator:
            entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        for entry in entries:
            relative = prefix / entry.name
            value = relative.as_posix()
            observed.add(value)
            info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if info.st_uid != os.geteuid() or _mode(info) != "0500":
                    raise SnapshotError(f"native snapshot structural directory is unsafe: {value}")
                child = os.open(
                    entry.name,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                    dir_fd=descriptor,
                )
                try:
                    walk(child, relative)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                raise SnapshotError(f"native snapshot payload special entry: {value}")

    walk(root_fd, PurePosixPath())
    return observed


def _verify_payload(
    root_fd: int,
    entries: Sequence[dict[str, object]],
    mappings: Sequence[tuple[str, str]],
) -> list[dict[str, object]]:
    root_info = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or _mode(root_info) != "0500"
    ):
        raise SnapshotError("native snapshot root is not sealed")
    records: list[dict[str, object]] = []
    for _source, destination in mappings:
        parent_fd = -1
        try:
            parent_fd, name = _open_snapshot_parent(root_fd, destination)
            records.extend(_snapshot_entry(parent_fd, name, destination))
        except SnapshotError:
            raise
        except OSError as exc:
            raise SnapshotError(f"native snapshot payload is missing: {destination}") from exc
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
    records.sort(key=lambda record: os.fsencode(str(record["path"])))
    expected_payload = _payload_records(entries)
    if records != expected_payload:
        raise SnapshotError("native snapshot canonical three-way equality failed")
    expected_paths: set[str] = set()
    for record in expected_payload:
        relative = PurePosixPath(str(record["path"])).relative_to("/")
        expected_paths.add(relative.as_posix())
        parent = relative.parent
        while parent != PurePosixPath("."):
            expected_paths.add(parent.as_posix())
            parent = parent.parent
    if _observed_paths(root_fd) != expected_paths:
        raise SnapshotError("native snapshot payload has extra or missing entries")
    _validate_symlinks(entries, mappings)
    return records


def _seal_structural_directories(root_fd: int) -> None:
    def walk(descriptor: int) -> None:
        with os.scandir(descriptor) as iterator:
            entries = list(iterator)
        for entry in entries:
            info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(
                    entry.name,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                    dir_fd=descriptor,
                )
                try:
                    walk(child)
                    os.fchmod(child, 0o500)
                    os.fsync(child)
                finally:
                    os.close(child)

    walk(root_fd)
    os.fchmod(root_fd, 0o500)
    os.fsync(root_fd)


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    if sys.platform != "linux":
        raise SnapshotError("Linux renameat2 RENAME_NOREPLACE is unavailable")
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise SnapshotError("Linux renameat2 RENAME_NOREPLACE is unavailable") from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if renameat2(
        parent_fd,
        os.fsencode(source),
        parent_fd,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    ) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise SnapshotError("native snapshot destination already exists")
        raise SnapshotError("native snapshot no-replace publication failed")


def _remove_tree(parent_fd: int, name: str) -> None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError:
        return
    try:
        os.fchmod(descriptor, 0o700)
        with os.scandir(descriptor) as iterator:
            entries = list(iterator)
        for entry in entries:
            info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                _remove_tree(descriptor, entry.name)
            else:
                try:
                    if stat.S_ISREG(info.st_mode):
                        os.chmod(entry.name, 0o600, dir_fd=descriptor, follow_symlinks=False)
                    os.unlink(entry.name, dir_fd=descriptor)
                except OSError:
                    pass
    finally:
        os.close(descriptor)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        pass


def _open_private_parent(path: Path) -> tuple[int, tuple[int, int]]:
    if not path.is_absolute() or Path(os.path.normpath(path)) != path:
        raise SnapshotError("native snapshot parent path is not canonical")
    parent = path.parent
    try:
        if parent.resolve(strict=True) != parent:
            raise SnapshotError("native snapshot parent contains a symlink")
        descriptor = os.open(
            parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC
        )
        info = os.fstat(descriptor)
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError("native snapshot parent is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise SnapshotError("native snapshot parent is not private")
    return descriptor, (info.st_dev, info.st_ino)


def _write_receipt(parent_fd: int, name: str, raw: bytes) -> str:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short receipt write")
            view = view[written:]
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        return _sha256(raw)
    except OSError as exc:
        raise SnapshotError("native snapshot receipt staging failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def materialize() -> dict[str, object]:
    """Copy only the fixed live-host mappings and atomically publish a sealed snapshot."""
    mappings = tuple(_SOURCE_DESTINATION_MAPPINGS)
    _validate_mappings(mappings)
    if _SNAPSHOT_ROOT.parent != _RECEIPT_PATH.parent:
        raise SnapshotError("native snapshot root and receipt must share one parent")
    parent_fd, parent_identity = _open_private_parent(_SNAPSHOT_ROOT)
    stage_name = f".{_SNAPSHOT_ROOT.name}.staging-{secrets.token_hex(12)}"
    receipt_stage = f".{_RECEIPT_PATH.name}.staging-{secrets.token_hex(12)}"
    stage_fd = -1
    root_published = False
    receipt_published = False
    try:
        for name in (_SNAPSHOT_ROOT.name, _RECEIPT_PATH.name):
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SnapshotError("native snapshot destination already exists")
        source_before = _source_inventory(mappings)
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_fd = os.open(
            stage_name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
        copied = _copy_mappings(stage_fd, mappings, source_before)
        if copied != _payload_records(source_before):
            raise SnapshotError("native snapshot canonical three-way equality failed")
        source_after = _source_inventory(mappings)
        if source_after != source_before:
            raise SnapshotError("native snapshot source inventory drifted")
        _seal_structural_directories(stage_fd)
        projected = _verify_payload(stage_fd, source_before, mappings)
        if projected != copied:
            raise SnapshotError("native snapshot canonical three-way equality failed")
        payload_digest = _payload_digest(source_before, mappings)
        source_digest = _sha256(_canonical(source_before))
        document: dict[str, object] = {
            "authority": _AUTHORITY,
            "entries": source_before,
            "mappings": _mapping_records(mappings),
            "payload_tree_sha256": payload_digest,
            "schema_version": 1,
            "source_after_sha256": source_digest,
            "source_before_sha256": source_digest,
            "threat_model": _THREAT_MODEL,
        }
        raw = _canonical(document)
        _write_receipt(parent_fd, receipt_stage, raw)
        parent_now = os.fstat(parent_fd)
        if (parent_now.st_dev, parent_now.st_ino) != parent_identity:
            raise SnapshotError("native snapshot parent identity drifted")
        _rename_noreplace(parent_fd, stage_name, _SNAPSHOT_ROOT.name)
        root_published = True
        _rename_noreplace(parent_fd, receipt_stage, _RECEIPT_PATH.name)
        receipt_published = True
        published = os.stat(
            _SNAPSHOT_ROOT.name, dir_fd=parent_fd, follow_symlinks=False
        )
        held = os.fstat(stage_fd)
        if (
            not stat.S_ISDIR(published.st_mode)
            or (published.st_dev, published.st_ino) != (held.st_dev, held.st_ino)
        ):
            raise SnapshotError("native snapshot published identity drifted")
        os.fsync(parent_fd)
        return document
    except BaseException:
        if receipt_published:
            try:
                os.unlink(_RECEIPT_PATH.name, dir_fd=parent_fd)
            except OSError:
                pass
        else:
            try:
                os.unlink(receipt_stage, dir_fd=parent_fd)
            except OSError:
                pass
        _remove_tree(
            parent_fd,
            _SNAPSHOT_ROOT.name if root_published else stage_name,
        )
        raise
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
        os.close(parent_fd)


def _read_receipt(parent_fd: int) -> tuple[bytes, dict[str, object]]:
    descriptor = -1
    try:
        descriptor = os.open(
            _RECEIPT_PATH.name,
            os.O_RDONLY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or _mode(info) != "0400"
            or info.st_size > 512 * 1024 * 1024
        ):
            raise SnapshotError("native snapshot receipt is unsafe")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError("native snapshot receipt is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        document = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("native snapshot receipt is invalid") from exc
    if (
        not isinstance(document, dict)
        or set(document) != _RECEIPT_FIELDS
        or raw != _canonical(document)
    ):
        raise SnapshotError("native snapshot receipt is not canonical")
    return raw, document


def _expected_policy(document: dict[str, object], raw: bytes) -> dict[str, object]:
    return {
        "authority": _AUTHORITY,
        "mappings": _mapping_records(_SOURCE_DESTINATION_MAPPINGS),
        "payload_tree_sha256": document.get("payload_tree_sha256"),
        "receipt_path": str(_RECEIPT_PATH),
        "receipt_sha256": _sha256(raw),
        "root": str(_SNAPSHOT_ROOT),
        "schema_version": 1,
        "threat_model": _THREAT_MODEL,
    }


def _resolve_payload_path(
    destination: str, entries: Sequence[dict[str, object]]
) -> str:
    records = {str(entry["path"]): entry for entry in entries}
    current = destination
    seen: set[str] = set()
    for _ in range(64):
        parts = PurePosixPath(current).parts
        replaced = False
        for index in range(2, len(parts) + 1):
            prefix = PurePosixPath(*parts[:index]).as_posix()
            record = records.get(prefix)
            if record is None or record.get("type") != "symlink":
                continue
            if prefix in seen:
                raise SnapshotError(f"native snapshot symlink loop: {destination}")
            seen.add(prefix)
            target = _lexical_target(prefix, str(record["target"]))
            suffix = parts[index:]
            current = PurePosixPath(target, *suffix).as_posix()
            replaced = True
            break
        if not replaced:
            return current
    raise SnapshotError(f"native snapshot symlink loop: {destination}")


def _open_payload_fd(
    root_fd: int,
    destination: str,
    entries: Sequence[dict[str, object]],
) -> int:
    resolved = _resolve_payload_path(destination, entries)
    descriptor = os.dup(root_fd)
    try:
        parts = PurePosixPath(resolved).parts[1:]
        for index, part in enumerate(parts):
            flags = _PATH | _NOFOLLOW | _CLOEXEC
            if index != len(parts) - 1:
                flags |= _DIRECTORY
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            raise SnapshotError(f"native snapshot mount source is unsafe: {destination}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def verify_and_open(policy: object) -> Iterator[VerifiedSnapshot]:
    """Verify the policy/receipt/payload chain and retain descriptor-rooted mount FDs."""
    if _SNAPSHOT_ROOT.parent != _RECEIPT_PATH.parent:
        raise SnapshotError("native snapshot root and receipt policy drifted")
    parent_fd, _parent_identity = _open_private_parent(_SNAPSHOT_ROOT)
    root_fd = -1
    mount_fds: list[int] = []
    try:
        raw, document = _read_receipt(parent_fd)
        if (
            not isinstance(policy, dict)
            or set(policy) != _POLICY_FIELDS
            or policy != _expected_policy(document, raw)
        ):
            raise SnapshotError("native snapshot policy binding drifted")
        if (
            document.get("authority") != _AUTHORITY
            or document.get("schema_version") != 1
            or document.get("threat_model") != _THREAT_MODEL
            or document.get("mappings")
            != _mapping_records(_SOURCE_DESTINATION_MAPPINGS)
            or document.get("source_before_sha256")
            != document.get("source_after_sha256")
            or not isinstance(document.get("entries"), list)
        ):
            raise SnapshotError("native snapshot receipt authority drifted")
        entries = document["entries"]
        assert isinstance(entries, list)
        if (
            _sha256(_canonical(entries)) != document["source_before_sha256"]
            or _payload_digest(entries, _SOURCE_DESTINATION_MAPPINGS)
            != document["payload_tree_sha256"]
        ):
            raise SnapshotError("native snapshot receipt digest drifted")
        root_fd = os.open(
            _SNAPSHOT_ROOT.name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
        _verify_payload(root_fd, entries, _SOURCE_DESTINATION_MAPPINGS)
        for _source, destination in _SOURCE_DESTINATION_MAPPINGS:
            mount_fds.append(_open_payload_fd(root_fd, destination, entries))
        yield VerifiedSnapshot(
            root_fd=root_fd,
            mounts=tuple(
                SnapshotMount(fd=descriptor, destination=destination)
                for descriptor, (_source, destination) in zip(
                    mount_fds, _SOURCE_DESTINATION_MAPPINGS, strict=True
                )
            ),
            receipt=document,
        )
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError("native snapshot verified-FD handoff failed") from exc
    finally:
        for descriptor in reversed(mount_fds):
            os.close(descriptor)
        if root_fd >= 0:
            os.close(root_fd)
        os.close(parent_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("materialize")
    verify = commands.add_parser("verify")
    verify.add_argument("--policy", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "materialize":
            document = materialize()
            print(_canonical(document).decode("ascii"), end="")
        else:
            try:
                policy = json.loads(arguments.policy.read_bytes())
            except (OSError, json.JSONDecodeError) as exc:
                raise SnapshotError("native snapshot policy is invalid") from exc
            with verify_and_open(policy):
                print("P1-U04 native authority snapshot verification: PASS")
        return 0
    except SnapshotError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
