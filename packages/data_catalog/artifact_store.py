"""Content-addressed local artifact storage with atomic no-clobber publication."""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import stat
import uuid

from packages.data_contracts import ArtifactRefV1


class ArtifactIntegrityError(ValueError):
    """An artifact path, type, size, or digest is not trustworthy."""


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_MAX_BYTES = 1 << 30


def _open_directory(path: Path) -> int:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ArtifactIntegrityError("artifact root must be an absolute directory")
    fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise ArtifactIntegrityError("artifact root contains an unsafe component")
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ArtifactIntegrityError("artifact root must be caller-owned")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ArtifactIntegrityError("artifact root must be private")
        return fd
    except Exception as exc:
        os.close(fd)
        if isinstance(exc, ArtifactIntegrityError):
            raise
        raise ArtifactIntegrityError("artifact root is unavailable or unsafe") from exc


def _write_all(fd: int, value: bytes) -> None:
    view = memoryview(value)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise ArtifactIntegrityError("artifact write made no progress")
        offset += written


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        fd = _open_directory(root)
        os.close(fd)
        self._root = root

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        if not isinstance(value, bytes) or len(value) > _MAX_BYTES:
            raise ArtifactIntegrityError("artifact bytes exceed the local bound")
        digest = hashlib.sha256(value).hexdigest()
        name = f"{digest}.blob"
        reference = ArtifactRefV1(
            content_sha256=digest,
            size_bytes=len(value),
            media_type=media_type,
            locator=name,
        )
        root_fd = _open_directory(self._root)
        temporary = f".{digest}.{uuid.uuid4().hex}.tmp"
        temporary_fd = -1
        try:
            temporary_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=root_fd,
            )
            _write_all(temporary_fd, value)
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = -1
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                    follow_symlinks=False,
                )
                os.fsync(root_fd)
            except FileExistsError:
                self._read_from_fd(root_fd, reference)
            finally:
                os.unlink(temporary, dir_fd=root_fd)
        except Exception as exc:
            if temporary_fd >= 0:
                os.close(temporary_fd)
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except OSError as cleanup_error:
                if cleanup_error.errno != errno.ENOENT:
                    raise ArtifactIntegrityError("artifact temporary cleanup failed") from exc
            if isinstance(exc, ArtifactIntegrityError):
                raise
            raise ArtifactIntegrityError("artifact publication failed") from exc
        finally:
            os.close(root_fd)
        return reference

    @staticmethod
    def _read_from_fd(root_fd: int, reference: ArtifactRefV1) -> bytes:
        try:
            fd = os.open(reference.locator, _READ_FLAGS, dir_fd=root_fd)
        except OSError as exc:
            raise ArtifactIntegrityError("artifact is unavailable or not a regular file") from exc
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or info.st_size != reference.size_bytes
                or info.st_size > _MAX_BYTES
            ):
                raise ArtifactIntegrityError("artifact size or ownership is invalid")
            digest = hashlib.sha256()
            blocks: list[bytes] = []
            while block := os.read(fd, 64 * 1024):
                digest.update(block)
                blocks.append(block)
            if digest.hexdigest() != reference.content_sha256:
                raise ArtifactIntegrityError("artifact digest is invalid")
            return b"".join(blocks)
        finally:
            os.close(fd)

    def read_bytes(self, reference: ArtifactRefV1) -> bytes:
        value = ArtifactRefV1.model_validate(reference)
        if value.locator != f"{value.content_sha256}.blob":
            raise ArtifactIntegrityError("artifact locator does not bind its digest")
        root_fd = _open_directory(self._root)
        try:
            return self._read_from_fd(root_fd, value)
        finally:
            os.close(root_fd)

    def verified_path(self, reference: ArtifactRefV1) -> Path:
        self.read_bytes(reference)
        return self._root / reference.locator


__all__ = ["ArtifactIntegrityError", "LocalArtifactStore"]
