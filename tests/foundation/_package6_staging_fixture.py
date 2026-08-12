"""Private, test-only lease for Package-6 staging material beneath ``/tmp``."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterator

import pytest


_TMP = Path("/tmp")
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class Package6StagingLeaseError(RuntimeError):
    """The leased staging root changed or no longer satisfies its contract."""


@dataclass(frozen=True, slots=True)
class Package6StagingLease:
    root: Path
    identity: tuple[int, int]

    def assert_valid(self) -> None:
        _validate_tmp_root()
        _validate_issued_root(self.root, self.identity)

    def cleanup(self) -> None:
        """Remove only the issued root, through verified directory descriptors."""

        tmp_fd = -1
        root_fd = -1
        try:
            tmp_fd = os.open(_TMP, _DIRECTORY_FLAGS)
            _validate_tmp_descriptor(tmp_fd)
            info = _entry_lstat(tmp_fd, self.root.name)
            _validate_issued_info(info, self.identity)
            root_fd = os.open(self.root.name, _DIRECTORY_FLAGS, dir_fd=tmp_fd)
            opened = os.fstat(root_fd)
            _validate_issued_info(opened, self.identity)
            _remove_children(root_fd)
            current = _entry_lstat(tmp_fd, self.root.name)
            _validate_issued_info(current, self.identity)
            os.rmdir(self.root.name, dir_fd=tmp_fd)
        except (OSError, ValueError) as exc:
            raise Package6StagingLeaseError("package6 staging lease cleanup refused") from exc
        finally:
            if root_fd >= 0:
                os.close(root_fd)
            if tmp_fd >= 0:
                os.close(tmp_fd)


def create_package6_staging_lease() -> Package6StagingLease:
    """Issue one direct, private child of the exact trusted ``/tmp`` root."""

    _validate_tmp_root()
    root = Path(
        tempfile.mkdtemp(
            dir="/tmp",
            prefix="trading-agent-package6-staging-",
        )
    )
    os.chmod(root, 0o700)
    try:
        info = _validate_issued_root(root)
    except (OSError, ValueError) as exc:
        raise Package6StagingLeaseError("package6 staging lease issuance refused") from exc
    return Package6StagingLease(root=root, identity=(info.st_dev, info.st_ino))


@pytest.fixture
def package6_staging_lease() -> Iterator[Package6StagingLease]:
    """Keep a direct-/tmp staging lease alive for one outer pytest test."""

    lease = create_package6_staging_lease()
    try:
        yield lease
    finally:
        lease.cleanup()


def _validate_tmp_root() -> None:
    info = _TMP.lstat()
    root_uid = Path("/").lstat().st_uid
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != root_uid
        or stat.S_IMODE(info.st_mode) != 0o1777
    ):
        raise ValueError


def _validate_tmp_descriptor(descriptor: int) -> None:
    info = os.fstat(descriptor)
    root_uid = Path("/").lstat().st_uid
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != root_uid
        or stat.S_IMODE(info.st_mode) != 0o1777
    ):
        raise ValueError


def _validate_issued_root(
    root: Path,
    identity: tuple[int, int] | None = None,
) -> os.stat_result:
    if root.parent != _TMP or root.name in {"", ".", ".."}:
        raise ValueError
    info = root.lstat()
    _validate_issued_info(info, identity)
    return info


def _validate_issued_info(
    info: os.stat_result,
    identity: tuple[int, int] | None,
) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_mode & 0o7000
        or (identity is not None and (info.st_dev, info.st_ino) != identity)
    ):
        raise ValueError


def _entry_lstat(parent_fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)


def _remove_children(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        info = _entry_lstat(directory_fd, name)
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            child_fd = -1
            try:
                child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError
                os.fchmod(child_fd, 0o700)
                _remove_children(child_fd)
                current = _entry_lstat(directory_fd, name)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError
                os.rmdir(name, dir_fd=directory_fd)
            finally:
                if child_fd >= 0:
                    os.close(child_fd)
        else:
            current = _entry_lstat(directory_fd, name)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError
            os.unlink(name, dir_fd=directory_fd)
