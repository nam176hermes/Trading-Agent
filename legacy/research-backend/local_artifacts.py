"""Small, no-follow helpers for bounded local research artifacts."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator


class UnsafeLocalArtifactError(ValueError):
    """Raised when a local artifact is not a bounded regular file."""


def _no_follow_flag() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _read_flags() -> int:
    return os.O_RDONLY | _no_follow_flag() | getattr(os, "O_CLOEXEC", 0)


def _write_flags() -> int:
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow_flag() | getattr(os, "O_CLOEXEC", 0)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | _no_follow_flag() | getattr(os, "O_CLOEXEC", 0)


def _open_private_parent(artifact: Path) -> int:
    """Create and anchor an artifact parent without following any directory links."""
    if not artifact.is_absolute() or not artifact.name:
        raise UnsafeLocalArtifactError("artifact path must be an absolute file path")

    directory_fd = -1
    try:
        directory_fd = os.open(os.sep, _directory_flags())
        parts = artifact.parent.parts[1:]
        for index, part in enumerate(parts):
            try:
                os.mkdir(part, 0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            child_fd = os.open(part, _directory_flags(), dir_fd=directory_fd)
            metadata = os.fstat(child_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child_fd)
                raise UnsafeLocalArtifactError("artifact parent must be a directory")

            is_final = index == len(parts) - 1
            writable_by_another_principal = bool(metadata.st_mode & 0o022)
            sticky_directory = bool(metadata.st_mode & stat.S_ISVTX)
            if writable_by_another_principal and not sticky_directory:
                os.close(child_fd)
                raise UnsafeLocalArtifactError("artifact parent is writable by another principal")
            if is_final:
                current_uid = os.getuid() if hasattr(os, "getuid") else metadata.st_uid
                if metadata.st_uid != current_uid:
                    os.close(child_fd)
                    raise UnsafeLocalArtifactError("artifact parent is not owned by the current user")
                os.fchmod(child_fd, 0o700)

            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd
    except (OSError, UnsafeLocalArtifactError) as exc:
        if directory_fd >= 0:
            os.close(directory_fd)
        if isinstance(exc, UnsafeLocalArtifactError):
            raise
        raise UnsafeLocalArtifactError("artifact parent cannot be opened safely") from exc


def _open_existing_parent(artifact: Path) -> int:
    """Anchor an existing artifact parent without following directory links."""
    artifact = Path(os.path.abspath(os.fspath(artifact)))
    if not artifact.name:
        raise UnsafeLocalArtifactError("artifact path must be an absolute file path")
    directory_fd = -1
    try:
        directory_fd = os.open(os.sep, _directory_flags())
        for part in artifact.parent.parts[1:]:
            child_fd = os.open(part, _directory_flags(), dir_fd=directory_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                os.close(child_fd)
                raise UnsafeLocalArtifactError("artifact parent must be a directory")
            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd
    except (OSError, UnsafeLocalArtifactError) as exc:
        if directory_fd >= 0:
            os.close(directory_fd)
        if isinstance(exc, UnsafeLocalArtifactError):
            raise
        raise UnsafeLocalArtifactError("artifact parent cannot be opened safely") from exc


def _require_regular(st: os.stat_result, max_bytes: int) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeLocalArtifactError("artifact must be a regular file")
    if not isinstance(max_bytes, int) or max_bytes < 0 or st.st_size > max_bytes:
        raise UnsafeLocalArtifactError("artifact exceeds the allowed size")


@contextmanager
def open_regular_read(path: str | Path, *, max_bytes: int) -> Iterator[BinaryIO]:
    """Open a bounded regular file without following links or races."""
    artifact = Path(os.path.abspath(os.fspath(path)))
    parent_fd = _open_existing_parent(artifact)
    fd = -1
    try:
        before = os.stat(artifact.name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise UnsafeLocalArtifactError("symlink artifacts are not accepted")
        _require_regular(before, max_bytes)
        fd = os.open(artifact.name, _read_flags(), dir_fd=parent_fd)
    except (OSError, UnsafeLocalArtifactError) as exc:
        os.close(parent_fd)
        if isinstance(exc, UnsafeLocalArtifactError):
            raise
        raise UnsafeLocalArtifactError("artifact cannot be opened safely") from exc

    try:
        after = os.fstat(fd)
        _require_regular(after, max_bytes)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise UnsafeLocalArtifactError("artifact changed while opening")
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            yield stream
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent_fd)


def read_utf8_text(path: str | Path, *, max_bytes: int) -> str:
    """Read a bounded regular UTF-8 file with strict decoding."""
    with open_regular_read(path, max_bytes=max_bytes) as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsafeLocalArtifactError("artifact exceeds the allowed size")
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnsafeLocalArtifactError("artifact is not strict UTF-8") from exc


def exclusive_private_write(path: str | Path, data: bytes) -> None:
    """Create a private file once; never replace an existing audit artifact."""
    artifact = Path(os.path.abspath(os.fspath(path)))
    parent_fd = _open_private_parent(artifact)
    fd = -1
    created = False
    try:
        fd = os.open(artifact.name, _write_flags(), 0o600, dir_fd=parent_fd)
        created = True
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent_fd)
    except Exception:
        if fd >= 0:
            os.close(fd)
        if created:
            try:
                os.unlink(artifact.name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(parent_fd)


def atomic_private_write(path: str | Path, writer: Callable[[BinaryIO], None]) -> None:
    """Atomically replace an artifact with a private, fsynced regular file."""
    artifact = Path(os.path.abspath(os.fspath(path)))
    temporary_name = f".{artifact.name}.{secrets.token_hex(16)}.tmp"
    parent_fd = _open_private_parent(artifact)
    fd = -1
    try:
        fd = os.open(temporary_name, _write_flags(), 0o600, dir_fd=parent_fd)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = -1
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, artifact.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(parent_fd)


def bounded_regular_files(directory: str | Path, *, suffix: str, max_entries: int) -> list[Path]:
    """Enumerate only a bounded number of non-symlink regular files."""
    return [
        path for path in bounded_directory_entries(directory, suffix=suffix, max_entries=max_entries)
        if _is_regular_nofollow(path)
    ]


def _is_regular_nofollow(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def bounded_directory_entries(directory: str | Path, *, suffix: str, max_entries: int) -> list[Path]:
    """Return bounded candidate names without opening or following any entry."""
    if not isinstance(max_entries, int) or max_entries < 1:
        return []
    root = Path(directory)
    try:
        root_stat = os.lstat(root)
    except OSError:
        return []
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return []

    files: list[Path] = []
    try:
        with os.scandir(root) as entries:
            for inspected, entry in enumerate(entries):
                if inspected >= max_entries:
                    break
                if not entry.name.endswith(suffix):
                    continue
                files.append(root / entry.name)
    except OSError:
        return []
    return files
