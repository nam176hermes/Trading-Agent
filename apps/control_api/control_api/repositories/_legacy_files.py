"""Bounded, fail-closed readers for untrusted legacy evidence files."""

from __future__ import annotations

import heapq
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


READ_CHUNK_BYTES = 8192


class LegacyFileError(ValueError):
    """Legacy evidence cannot be safely consumed."""


def _read_chunk(descriptor: int, size: int) -> bytes:
    try:
        return os.read(descriptor, size)
    except OSError as error:
        raise LegacyFileError("legacy evidence cannot be read") from error


def _open_parent(path: Path) -> tuple[Path, int]:
    artifact = Path(os.path.abspath(os.fspath(path)))
    if not artifact.name:
        raise LegacyFileError("legacy evidence path is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = -1
    try:
        directory_fd = os.open(os.sep, flags)
        for part in artifact.parent.parts[1:]:
            child_fd = os.open(part, flags, dir_fd=directory_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                os.close(child_fd)
                raise LegacyFileError("legacy evidence parent must be a directory")
            os.close(directory_fd)
            directory_fd = child_fd
        return artifact, directory_fd
    except (OSError, LegacyFileError) as error:
        if directory_fd >= 0:
            os.close(directory_fd)
        if isinstance(error, LegacyFileError):
            raise
        raise LegacyFileError("legacy evidence parent cannot be opened safely") from error


@contextmanager
def _open_regular(path: Path) -> Iterator[int]:
    """Open a non-symlink regular file and yield its descriptor."""
    artifact, parent_fd = _open_parent(path)
    descriptor = -1
    try:
        initial = os.stat(artifact.name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
            raise LegacyFileError("legacy evidence must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(artifact.name, flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise LegacyFileError("legacy evidence must be a regular file")
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise LegacyFileError("legacy evidence changed while opening")
        yield descriptor
    except OSError as error:
        raise LegacyFileError("legacy evidence cannot be opened safely") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def validate_regular_file(path: Path) -> None:
    """Verify that a legacy path is currently a regular, non-symlinked file."""

    with _open_regular(path):
        return


def read_text(path: Path, *, max_bytes: int) -> str:
    """Read strict UTF-8 evidence without allocating beyond ``max_bytes``."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    content = bytearray()
    with _open_regular(path) as descriptor:
        remaining = max_bytes
        while True:
            chunk = _read_chunk(descriptor, min(READ_CHUNK_BYTES, remaining + 1))
            if not chunk:
                break
            if len(chunk) > remaining:
                raise LegacyFileError("legacy evidence exceeds the byte limit")
            content.extend(chunk)
            remaining -= len(chunk)
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise LegacyFileError("legacy evidence is not UTF-8") from error


def read_json(path: Path, *, max_bytes: int):
    """Load bounded, strict-UTF-8 JSON evidence."""

    return json.loads(read_text(path, max_bytes=max_bytes))


def iter_jsonl(
    path: Path,
    *,
    max_bytes: int,
    max_line_bytes: int,
    max_records: int,
) -> Iterator[tuple[int, str]]:
    """Yield bounded strict-UTF-8 JSONL lines, rejecting oversized evidence."""

    if min(max_bytes, max_line_bytes, max_records) < 1:
        raise ValueError("JSONL limits must be positive")
    buffer = bytearray()
    line_number = 0
    record_count = 0
    with _open_regular(path) as descriptor:
        remaining = max_bytes
        while True:
            chunk = _read_chunk(descriptor, min(READ_CHUNK_BYTES, remaining + 1))
            if not chunk:
                break
            if len(chunk) > remaining:
                raise LegacyFileError("legacy evidence exceeds the byte limit")
            remaining -= len(chunk)
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    if len(buffer) > max_line_bytes:
                        raise LegacyFileError("legacy JSONL line exceeds the byte limit")
                    break
                raw_line = bytes(buffer[:newline])
                del buffer[: newline + 1]
                line_number += 1
                if len(raw_line) > max_line_bytes:
                    raise LegacyFileError("legacy JSONL line exceeds the byte limit")
                if not raw_line:
                    continue
                record_count += 1
                if record_count > max_records:
                    raise LegacyFileError("legacy JSONL record limit exceeded")
                try:
                    yield line_number, raw_line.decode("utf-8", errors="strict")
                except UnicodeDecodeError as error:
                    raise LegacyFileError("legacy evidence is not UTF-8") from error
    if buffer:
        line_number += 1
        if len(buffer) > max_line_bytes:
            raise LegacyFileError("legacy JSONL line exceeds the byte limit")
        record_count += 1
        if record_count > max_records:
            raise LegacyFileError("legacy JSONL record limit exceeded")
        try:
            yield line_number, bytes(buffer).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise LegacyFileError("legacy evidence is not UTF-8") from error


def iter_directory_candidates(
    directory: Path,
    *,
    prefix: str,
    suffix: str,
    max_entries: int,
    max_candidates: int,
    fail_on_truncation: bool = False,
) -> Iterator[Path]:
    """Yield name-matching directory entries with bounded inspection and output."""

    if min(max_entries, max_candidates) < 1:
        raise ValueError("directory limits must be positive")
    try:
        directory_info = directory.lstat()
    except OSError:
        return
    if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode):
        return
    selected: list[tuple[str, Path]] = []
    try:
        with os.scandir(directory) as entries:
            for inspected, entry in enumerate(entries):
                if inspected >= max_entries:
                    if fail_on_truncation:
                        raise LegacyFileError("legacy directory inspection limit exceeded")
                    break
                if not (entry.name.startswith(prefix) and entry.name.endswith(suffix)):
                    continue
                candidate = (entry.name, Path(entry.path))
                if len(selected) < max_candidates:
                    heapq.heappush(selected, candidate)
                elif fail_on_truncation:
                    raise LegacyFileError("legacy directory candidate limit exceeded")
                elif candidate[0] > selected[0][0]:
                    heapq.heapreplace(selected, candidate)
    except OSError:
        return
    for _, candidate in sorted(selected, reverse=True):
        yield candidate
