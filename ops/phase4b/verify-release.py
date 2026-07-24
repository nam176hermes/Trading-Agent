#!/usr/bin/env python3
"""Standalone stdlib attestation for one complete Phase 4B release tree."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"CPython 3\.11\.[0-9]+\Z")
_RELEASE_TYPE = re.compile(r"phase4-(?:app|backend)\Z")
_MANIFEST_KEYS = (
    "manifest_version", "release_type", "git_commit", "python_identity",
    "entries", "aggregate_sha256",
)
_ENTRY_KEYS = ("path", "type", "mode", "size", "sha256")
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class VerificationError(RuntimeError):
    pass


def _reject() -> None:
    raise VerificationError()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _reject()
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _safe_path(path: Path) -> Path:
    candidate = Path(path)
    if (
        not candidate.is_absolute() or ".." in candidate.parts
        or os.fspath(candidate) != os.path.normpath(candidate)
    ):
        _reject()
    return candidate


def _safe_ancestor(
    info: os.stat_result, expected_uid: int, expected_gid: int, system_uid: int,
) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, expected_uid, system_uid}
        or (info.st_uid == expected_uid and info.st_gid != expected_gid)
        or stat.S_IMODE(info.st_mode) & (0o022 | 0o7000)
    ):
        _reject()


def _open_parent(path: Path, expected_uid: int, expected_gid: int) -> tuple[int, str]:
    path = _safe_path(path)
    descriptors: list[int] = []
    try:
        current = os.open(path.anchor, _DIR_FLAGS)
        descriptors.append(current)
        system_uid = os.fstat(current).st_uid
        _safe_ancestor(os.fstat(current), expected_uid, expected_gid, system_uid)
        for part in path.parts[1:-1]:
            child = os.open(part, _DIR_FLAGS, dir_fd=current)
            descriptors.append(child)
            current = child
            _safe_ancestor(os.fstat(current), expected_uid, expected_gid, system_uid)
        retained = os.dup(current)
        return retained, path.name
    except Exception:
        _reject()
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    raise AssertionError("unreachable")


def _read_manifest(
    path: Path, *, expected_uid: int, expected_gid: int, expected_mode: int,
) -> tuple[dict[str, Any], bytes]:
    parent_fd, name = _open_parent(path, expected_uid, expected_gid)
    descriptor = -1
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        initial = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1
            or initial.st_uid != expected_uid or initial.st_gid != expected_gid
            or stat.S_IMODE(initial.st_mode) != expected_mode
            or initial.st_size > _MAX_MANIFEST_BYTES
        ):
            _reject()
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > _MAX_MANIFEST_BYTES:
                _reject()
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if (
            final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns
        ) != (
            initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns
        ):
            _reject()
        raw = b"".join(chunks)
        document = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(document, dict):
            _reject()
        return document, raw
    except VerificationError:
        raise
    except Exception:
        _reject()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
    raise AssertionError("unreachable")


def _safe_entry(info: os.stat_result, expected_uid: int, expected_gid: int) -> str:
    if (
        info.st_uid != expected_uid or info.st_gid != expected_gid
        or stat.S_IMODE(info.st_mode) & (0o022 | 0o7000)
    ):
        _reject()
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            _reject()
        return "file"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    _reject()
    raise AssertionError("unreachable")


def _hash_file(directory_fd: int, name: str, expected: os.stat_result) -> str:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    try:
        initial = os.fstat(descriptor)
        if (
            initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns
        ) != (
            expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns
        ) or not stat.S_ISREG(initial.st_mode):
            _reject()
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        final = os.fstat(descriptor)
        if (
            final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns
        ) != (
            initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns
        ):
            _reject()
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _walk(
    directory_fd: int, prefix: str, expected_uid: int, expected_gid: int,
) -> list[dict[str, Any]]:
    with os.scandir(directory_fd) as children:
        names = sorted((child.name for child in children), key=os.fsencode)
    entries: list[dict[str, Any]] = []
    for name in names:
        if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
            _reject()
        relative = f"{prefix}/{name}" if prefix else name
        relative.encode("utf-8", "strict")
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        kind = _safe_entry(info, expected_uid, expected_gid)
        entry = {
            "path": relative,
            "type": kind,
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "size": info.st_size if kind == "file" else 0,
            "sha256": _hash_file(directory_fd, name, info) if kind == "file" else _EMPTY_SHA256,
        }
        entries.append(entry)
        if kind == "directory":
            child_fd = os.open(name, _DIR_FLAGS, dir_fd=directory_fd)
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    _reject()
                entries.extend(_walk(child_fd, relative, expected_uid, expected_gid))
            finally:
                os.close(child_fd)
    return entries


def _release_entries(root: Path, expected_uid: int, expected_gid: int) -> list[dict[str, Any]]:
    parent_fd, name = _open_parent(root, expected_uid, expected_gid)
    descriptor = -1
    try:
        descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        info = os.fstat(descriptor)
        if (
            _safe_entry(info, expected_uid, expected_gid) != "directory"
            or stat.S_IMODE(info.st_mode) != 0o755
        ):
            _reject()
        return sorted(
            _walk(descriptor, "", expected_uid, expected_gid),
            key=lambda entry: os.fsencode(entry["path"]),
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def verify_release(
    release_root: Path, manifest_path: Path, canonical_digest: str, raw_digest: str,
    *, expected_commit: str, expected_python_identity: str, release_type: str,
    expected_uid: int, expected_gid: int, manifest_mode: int,
) -> None:
    if (
        _DIGEST.fullmatch(canonical_digest) is None
        or _DIGEST.fullmatch(raw_digest) is None
        or _COMMIT.fullmatch(expected_commit) is None
        or _IDENTITY.fullmatch(expected_python_identity) is None
        or _RELEASE_TYPE.fullmatch(release_type) is None
        or expected_uid < 0 or expected_gid < 0
        or manifest_mode not in {0o444, 0o644}
    ):
        _reject()
    document, raw = _read_manifest(
        manifest_path, expected_uid=expected_uid, expected_gid=expected_gid,
        expected_mode=manifest_mode,
    )
    if tuple(document) != _MANIFEST_KEYS or document["manifest_version"] != 1:
        _reject()
    canonical = _canonical(document)
    if raw != canonical + b"\n":
        _reject()
    if not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(), canonical_digest):
        _reject()
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), raw_digest):
        _reject()
    if (
        document["release_type"] != release_type
        or document["git_commit"] != expected_commit
        or document["python_identity"] != expected_python_identity
        or not isinstance(document["entries"], list)
    ):
        _reject()
    entries = document["entries"]
    previous: bytes | None = None
    for entry in entries:
        if not isinstance(entry, dict) or tuple(entry) != _ENTRY_KEYS:
            _reject()
        path = entry["path"]
        pure = PurePosixPath(path) if isinstance(path, str) else PurePosixPath(".")
        encoded = path.encode("utf-8", "strict") if isinstance(path, str) else b""
        if (
            not encoded or pure.is_absolute() or ".." in pure.parts
            or pure.as_posix() != path or (previous is not None and encoded <= previous)
            or entry["type"] not in {"file", "directory"}
            or not isinstance(entry["mode"], str)
            or re.fullmatch(r"[0-7]{4}", entry["mode"]) is None
            or not isinstance(entry["size"], int) or isinstance(entry["size"], bool)
            or entry["size"] < 0 or not isinstance(entry["sha256"], str)
            or _DIGEST.fullmatch(entry["sha256"]) is None
        ):
            _reject()
        previous = encoded
    if document["aggregate_sha256"] != hashlib.sha256(_canonical(entries)).hexdigest():
        _reject()
    actual = _release_entries(release_root, expected_uid, expected_gid)
    if not hmac.compare_digest(_canonical(actual), _canonical(entries)):
        _reject()
    interpreter = next(
        (item for item in actual if item["path"] == ".venv/bin/python3.11"), None
    )
    if (
        interpreter is None or interpreter["type"] != "file"
        or int(interpreter["mode"], 8) & 0o111 == 0
    ):
        _reject()


def _mode(value: str) -> int:
    if re.fullmatch(r"0[0-7]{3}", value) is None:
        raise argparse.ArgumentTypeError("invalid mode")
    return int(value, 8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_root", type=Path)
    parser.add_argument("manifest_path", type=Path)
    parser.add_argument("canonical_digest")
    parser.add_argument("raw_digest")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--python-identity", required=True)
    parser.add_argument("--release-type", required=True)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    parser.add_argument("--manifest-mode", required=True, type=_mode)
    args = parser.parse_args()
    try:
        verify_release(
            args.release_root, args.manifest_path, args.canonical_digest,
            args.raw_digest, expected_commit=args.commit,
            expected_python_identity=args.python_identity,
            release_type=args.release_type, expected_uid=args.uid,
            expected_gid=args.gid, manifest_mode=args.manifest_mode,
        )
    except Exception:
        print("release verification rejected", file=sys.stderr)
        return 2
    print("release verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
