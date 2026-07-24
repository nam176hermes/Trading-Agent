"""Bounded, protected storage for child process streams."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_STREAM_BYTES = 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    artifact_type: str
    relative_ref: str
    sha256: str
    size_bytes: int
    media_type: str
    truncated: bool
    validator_id: str = "bounded-stream-v1"


class ArtifactWriter:
    """Continuously drain a stream while retaining at most one MiB."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).absolute()
        try:
            descriptor = self._open_directory_chain(self.root, create=True)
        except OSError as exc:
            raise ValueError("artifact directory must be a real directory") from exc
        try:
            self._secure_directory_fd(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _directory_flags() -> int:
        return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)

    @classmethod
    def _open_directory_chain(cls, path: Path, *, create: bool) -> int:
        descriptor = os.open(path.anchor, cls._directory_flags())
        try:
            for part in path.parts[1:]:
                if part in {"", ".", ".."}:
                    raise OSError("unsafe artifact directory component")
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, cls._directory_flags(), dir_fd=descriptor)
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode):
                    os.close(child)
                    raise OSError("artifact component is not a directory")
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _secure_directory_fd(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("artifact directory has unsafe ownership or type")
        os.fchmod(descriptor, 0o700)

    @classmethod
    def _open_child_directory(cls, parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        descriptor = os.open(name, cls._directory_flags(), dir_fd=parent_fd)
        try:
            cls._secure_directory_fd(descriptor)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _validate_identifier(value: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError("artifact identifier is unsafe")
        return value

    def capture_stream(
        self,
        job_id: str,
        attempt_id: str,
        artifact_type: str,
        stream: BinaryIO,
    ) -> ArtifactMetadata:
        job = self._validate_identifier(job_id)
        attempt = self._validate_identifier(attempt_id)
        kind = self._validate_identifier(artifact_type)
        relative = Path(job) / attempt / f"{kind}.log"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        root_fd = self._open_directory_chain(self.root, create=False)
        job_fd = attempt_fd = descriptor = -1
        try:
            self._secure_directory_fd(root_fd)
            job_fd = self._open_child_directory(root_fd, job)
            attempt_fd = self._open_child_directory(job_fd, attempt)
            descriptor = os.open(f"{kind}.log", flags, 0o600, dir_fd=attempt_fd)
        except BaseException:
            for opened in (attempt_fd, job_fd, root_fd):
                if opened >= 0:
                    os.close(opened)
            raise
        digest = hashlib.sha256()
        observed = 0
        stored = 0
        try:
            os.fchmod(descriptor, 0o600)
            output = os.fdopen(descriptor, "wb")
            descriptor = -1
            with output:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise TypeError("artifact streams must yield bytes")
                    digest.update(chunk)
                    observed += len(chunk)
                    if stored < MAX_STREAM_BYTES:
                        retained = chunk[: MAX_STREAM_BYTES - stored]
                        output.write(retained)
                        stored += len(retained)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(f"{kind}.log", dir_fd=attempt_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            for opened in (attempt_fd, job_fd, root_fd):
                if opened >= 0:
                    os.close(opened)
        return ArtifactMetadata(
            artifact_type=kind,
            relative_ref=relative.as_posix(),
            sha256=digest.hexdigest(),
            size_bytes=observed,
            media_type="application/octet-stream",
            truncated=observed > stored,
        )


__all__ = ["MAX_STREAM_BYTES", "ArtifactMetadata", "ArtifactWriter"]
