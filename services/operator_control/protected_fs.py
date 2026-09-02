"""Descriptor-anchored private filesystem primitives for operator state."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_RENAME_NOREPLACE = 1
_RENAME_EXCHANGE = 2


class ProtectedFilesystemError(ValueError):
    """A protected path, object, or atomic operation is not trustworthy."""


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid)


def _require_safe_ancestor(info: os.stat_result) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or (mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX))
    ):
        raise ProtectedFilesystemError("protected path ancestor is unsafe")


def _require_private_directory(info: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ProtectedFilesystemError("protected directory is not private")


def require_private_regular_file(info: os.stat_result, *, max_bytes: int) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size < 0
        or info.st_size > max_bytes
    ):
        raise ProtectedFilesystemError("protected file metadata is unsafe")


@dataclass(slots=True)
class ProtectedDirectory:
    path: Path
    descriptor: int
    identity: tuple[int, int, int, int]

    def __enter__(self) -> "ProtectedDirectory":
        return self

    def __exit__(self, *_: object) -> None:
        os.close(self.descriptor)
        self.descriptor = -1

    def recheck(self) -> None:
        try:
            opened = os.fstat(self.descriptor)
            fresh = open_private_directory(self.path)
        except (OSError, ProtectedFilesystemError) as exc:
            raise ProtectedFilesystemError(
                "protected directory identity changed"
            ) from exc
        try:
            _require_private_directory(opened)
            if _identity(opened) != self.identity or fresh.identity != self.identity:
                raise ProtectedFilesystemError("protected directory identity changed")
        finally:
            fresh.__exit__()


def open_private_directory(path: Path) -> ProtectedDirectory:
    path = Path(path)
    if (
        not path.is_absolute()
        or path == Path("/")
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise ProtectedFilesystemError(
            "protected directory path is not canonical absolute"
        )
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if index == len(parts) - 1:
                _require_private_directory(info)
            else:
                _require_safe_ancestor(info)
        info = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if _identity(info) != _identity(named):
            raise ProtectedFilesystemError("protected directory identity changed")
        return ProtectedDirectory(path, descriptor, _identity(info))
    except Exception as exc:
        os.close(descriptor)
        if isinstance(exc, ProtectedFilesystemError):
            raise
        raise ProtectedFilesystemError("protected directory is unavailable") from exc


def _name(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\x00" in value:
        raise ProtectedFilesystemError("protected file name is invalid")
    return value


def read_private_file(
    directory: ProtectedDirectory,
    name: str,
    *,
    max_bytes: int,
    missing_ok: bool = False,
) -> bytes | None:
    descriptor = -1
    directory.recheck()
    try:
        descriptor = os.open(_name(name), _READ_FLAGS, dir_fd=directory.descriptor)
    except FileNotFoundError:
        if missing_ok:
            directory.recheck()
            return None
        raise ProtectedFilesystemError("protected file is missing") from None
    except OSError as exc:
        raise ProtectedFilesystemError("protected file is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        require_private_regular_file(before, max_bytes=max_bytes)
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise ProtectedFilesystemError("protected file read was incomplete")
            chunks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ProtectedFilesystemError("protected file grew during read")
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(before) != _identity(
            named
        ):
            raise ProtectedFilesystemError(
                "protected file identity changed during read"
            )
        directory.recheck()
        return b"".join(chunks)
    except OSError as exc:
        raise ProtectedFilesystemError("protected file read failed") from exc
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise ProtectedFilesystemError("protected file write made no progress")
        remaining = remaining[written:]


def _renameat2(
    source_directory: int,
    source_name: str,
    destination_directory: int,
    destination_name: str,
    flag: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        raise ProtectedFilesystemError("atomic renameat2 is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    if (
        function(
            source_directory,
            os.fsencode(source_name),
            destination_directory,
            os.fsencode(destination_name),
            flag,
        )
        != 0
    ):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ProtectedFilesystemError("protected destination already exists")
        raise ProtectedFilesystemError("protected atomic rename failed") from OSError(
            error, os.strerror(error)
        )


def create_private_file(
    directory: ProtectedDirectory,
    name: str,
    value: bytes,
    *,
    max_bytes: int,
) -> None:
    if len(value) > max_bytes:
        raise ProtectedFilesystemError("protected file exceeds size bound")
    descriptor = -1
    directory.recheck()
    try:
        descriptor = os.open(
            _name(name), _WRITE_FLAGS, 0o600, dir_fd=directory.descriptor
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, value)
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        require_private_regular_file(opened, max_bytes=max_bytes)
        if _identity(opened) != _identity(named):
            raise ProtectedFilesystemError(
                "protected file identity changed after create"
            )
        os.fsync(directory.descriptor)
        directory.recheck()
    except FileExistsError:
        raise ProtectedFilesystemError("protected file already exists") from None
    except OSError as exc:
        raise ProtectedFilesystemError("protected file create failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def replace_private_file(
    directory: ProtectedDirectory,
    name: str,
    value: bytes,
    *,
    max_bytes: int,
    expected_sha256: str | None,
) -> None:
    if len(value) > max_bytes:
        raise ProtectedFilesystemError("protected file exceeds size bound")
    name = _name(name)
    directory.recheck()
    existing_raw = read_private_file(
        directory, name, max_bytes=max_bytes, missing_ok=True
    )
    if expected_sha256 is None:
        if existing_raw is not None:
            raise ProtectedFilesystemError("protected target unexpectedly exists")
        existing_identity = None
    else:
        if (
            existing_raw is None
            or not hashlib.sha256(existing_raw).hexdigest() == expected_sha256
        ):
            raise ProtectedFilesystemError("protected target digest changed")
        existing = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        existing_identity = _identity(existing)

    temporary = f".hwc-{uuid.uuid4().hex}.tmp"
    descriptor = -1
    temporary_present = True
    cleanup_allowed = True
    try:
        descriptor = os.open(
            temporary, _WRITE_FLAGS, 0o600, dir_fd=directory.descriptor
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, value)
        os.fsync(descriptor)
        require_private_regular_file(os.fstat(descriptor), max_bytes=max_bytes)
        os.close(descriptor)
        descriptor = -1
        if existing_identity is None:
            _renameat2(
                directory.descriptor,
                temporary,
                directory.descriptor,
                name,
                _RENAME_NOREPLACE,
            )
            temporary_present = False
        else:
            _renameat2(
                directory.descriptor,
                temporary,
                directory.descriptor,
                name,
                _RENAME_EXCHANGE,
            )
            cleanup_allowed = False
            displaced = read_private_file(directory, temporary, max_bytes=max_bytes)
            displaced_info = os.stat(
                temporary, dir_fd=directory.descriptor, follow_symlinks=False
            )
            if (
                displaced is None
                or hashlib.sha256(displaced).hexdigest() != expected_sha256
                or _identity(displaced_info) != existing_identity
            ):
                _renameat2(
                    directory.descriptor,
                    temporary,
                    directory.descriptor,
                    name,
                    _RENAME_EXCHANGE,
                )
                cleanup_allowed = True
                os.fsync(directory.descriptor)
                raise ProtectedFilesystemError(
                    "protected target changed before publish"
                )
            os.unlink(temporary, dir_fd=directory.descriptor)
            temporary_present = False
        os.fsync(directory.descriptor)
        directory.recheck()
        observed = read_private_file(directory, name, max_bytes=max_bytes)
        if observed != value:
            raise ProtectedFilesystemError("protected file published bytes changed")
    except ProtectedFilesystemError:
        raise
    except OSError as exc:
        raise ProtectedFilesystemError("protected file replace failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_present and cleanup_allowed:
            try:
                os.unlink(temporary, dir_fd=directory.descriptor)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise ProtectedFilesystemError("temporary cleanup failed") from exc


def rename_private_file_noreplace(
    source_directory: ProtectedDirectory,
    source_name: str,
    destination_directory: ProtectedDirectory,
    destination_name: str,
    *,
    max_bytes: int,
    expected_sha256: str,
) -> bytes:
    source_name = _name(source_name)
    destination_name = _name(destination_name)
    source_directory.recheck()
    destination_directory.recheck()
    source = read_private_file(source_directory, source_name, max_bytes=max_bytes)
    assert source is not None
    source_info = os.stat(
        source_name, dir_fd=source_directory.descriptor, follow_symlinks=False
    )
    require_private_regular_file(source_info, max_bytes=max_bytes)
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise ProtectedFilesystemError("protected source digest changed")
    if (
        os.fstat(source_directory.descriptor).st_dev
        != os.fstat(destination_directory.descriptor).st_dev
    ):
        raise ProtectedFilesystemError("protected rename crosses filesystems")

    try:
        _renameat2(
            source_directory.descriptor,
            source_name,
            destination_directory.descriptor,
            destination_name,
            _RENAME_NOREPLACE,
        )
    except ProtectedFilesystemError:
        raise

    observed: bytes | None = None
    try:
        # Make the tombstone durable before making source removal durable.
        os.fsync(destination_directory.descriptor)
        os.fsync(source_directory.descriptor)
        source_directory.recheck()
        destination_directory.recheck()
        try:
            os.stat(
                source_name,
                dir_fd=source_directory.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise ProtectedFilesystemError("protected rename left its source behind")
        destination_info = os.stat(
            destination_name,
            dir_fd=destination_directory.descriptor,
            follow_symlinks=False,
        )
        observed = read_private_file(
            destination_directory, destination_name, max_bytes=max_bytes
        )
        if (
            _identity(destination_info) != _identity(source_info)
            or observed != source
            or hashlib.sha256(observed).hexdigest() != expected_sha256
        ):
            raise ProtectedFilesystemError("protected source changed during rename")
    except (OSError, ProtectedFilesystemError) as validation_error:
        try:
            _renameat2(
                destination_directory.descriptor,
                destination_name,
                source_directory.descriptor,
                source_name,
                _RENAME_NOREPLACE,
            )
            os.fsync(source_directory.descriptor)
            os.fsync(destination_directory.descriptor)
            source_directory.recheck()
            destination_directory.recheck()
            restored = read_private_file(
                source_directory, source_name, max_bytes=max_bytes
            )
            if observed is not None and restored != observed:
                raise ProtectedFilesystemError(
                    "protected rename rollback bytes changed"
                )
        except (OSError, ProtectedFilesystemError) as rollback_error:
            raise ProtectedFilesystemError(
                "protected source changed during rename and rollback failed"
            ) from rollback_error
        raise ProtectedFilesystemError(
            "protected source changed during rename"
        ) from validation_error
    return source
