"""Descriptor-safe private token-file loading shared by HWC interfaces."""

from __future__ import annotations

import os
import stat
from pathlib import Path


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_TOKEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_MAX_TOKEN_BYTES = 4096


class PrivateTokenError(ValueError):
    """A configured token file is not a trustworthy private regular file."""


def _directory_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _require_safe_directory(info: os.stat_result) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or (mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX))
    ):
        raise PrivateTokenError("private token authority is unavailable")


def _open_parent(path: Path) -> tuple[int, tuple[int, int, int, int]]:
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _require_safe_directory(os.fstat(descriptor))
        info = os.fstat(descriptor)
        _require_safe_directory(info)
        named = os.stat(path.parent, follow_symlinks=False)
        if _directory_identity(info) != _directory_identity(named):
            raise PrivateTokenError("private token authority is unavailable")
        return descriptor, _directory_identity(info)
    except Exception:
        os.close(descriptor)
        raise


def _canonical_path(path: Path) -> Path:
    raw = os.fspath(path)
    candidate = Path(raw)
    if (
        not candidate.is_absolute()
        or candidate.anchor != "/"
        or candidate == Path("/")
        or os.path.normpath(raw) != raw
        or candidate.as_posix() != raw
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
    ):
        raise PrivateTokenError("private token authority is unavailable")
    return candidate


def load_private_token(path: Path) -> bytes:
    parent = file_descriptor = -1
    try:
        target = _canonical_path(path)
        parent, parent_identity = _open_parent(target)
        file_descriptor = os.open(target.name, _TOKEN_FLAGS, dir_fd=parent)
        before = os.fstat(file_descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or mode & ~0o600
            or before.st_size > _MAX_TOKEN_BYTES + 1
        ):
            raise PrivateTokenError("private token authority is unavailable")
        chunks: list[bytes] = []
        observed = 0
        while observed <= _MAX_TOKEN_BYTES + 1:
            block = os.read(file_descriptor, min(4096, _MAX_TOKEN_BYTES + 2 - observed))
            if not block:
                break
            chunks.append(block)
            observed += len(block)
        if observed > _MAX_TOKEN_BYTES + 1:
            raise PrivateTokenError("private token authority is unavailable")
        after = os.fstat(file_descriptor)
        named = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        if _file_identity(before) != _file_identity(after) or _file_identity(
            before
        ) != _file_identity(named):
            raise PrivateTokenError("private token authority is unavailable")
        reopened, reopened_identity = _open_parent(target)
        os.close(reopened)
        if reopened_identity != parent_identity:
            raise PrivateTokenError("private token authority is unavailable")
        raw = b"".join(chunks)
        token = raw[:-1] if raw.endswith(b"\n") else raw
        if (
            not (32 <= len(token) <= _MAX_TOKEN_BYTES)
            or b"\n" in token
            or any(byte < 0x21 or byte > 0x7E for byte in token)
        ):
            raise PrivateTokenError("private token authority is unavailable")
        return token
    except PrivateTokenError:
        raise
    except (OSError, TypeError, ValueError):
        raise PrivateTokenError("private token authority is unavailable") from None
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if parent >= 0:
            os.close(parent)


__all__ = ["PrivateTokenError", "load_private_token"]
