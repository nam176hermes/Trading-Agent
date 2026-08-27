"""Create test-only temporary directories below a trusted private root."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


_COMPONENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TEMP_ENVIRONMENT_NAMES = ("TMPDIR", "TEMP", "TMP")


class TrustedTestTmpError(RuntimeError):
    """Raised when a test temp root or session directory is not trustworthy."""


def _make_tree_removable(directory: Path, *, expected_device: int) -> None:
    """Restore owner access to real directories without following symlinks."""
    metadata = directory.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_dev != expected_device
    ):
        raise TrustedTestTmpError(
            "test temp session contains an unsafe directory; refusing cleanup"
        )
    directory.chmod(0o700)
    with os.scandir(directory) as entries:
        children = tuple(entries)
    for entry in children:
        if entry.is_dir(follow_symlinks=False):
            _make_tree_removable(Path(entry.path), expected_device=expected_device)


def _validate_existing_root(candidate: Path) -> Path:
    if not candidate.is_absolute():
        raise TrustedTestTmpError("test temp root must be absolute")

    normalized = Path(os.path.abspath(candidate))
    try:
        canonical = normalized.resolve(strict=True)
    except FileNotFoundError as error:
        raise TrustedTestTmpError("test temp root does not exist") from error
    if canonical != normalized:
        raise TrustedTestTmpError("test temp root must be canonical and contain no symlinks")

    allowed_owners = {0, os.geteuid()}
    current = Path(canonical.anchor)
    for part in canonical.parts[1:]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise TrustedTestTmpError("test temp root must contain no symlinks")
        if not stat.S_ISDIR(metadata.st_mode):
            raise TrustedTestTmpError(f"test temp path component is not a directory: {current}")
        if metadata.st_uid not in allowed_owners:
            raise TrustedTestTmpError(f"test temp path has an untrusted owner: {current}")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise TrustedTestTmpError(f"test temp path has a writable ancestor: {current}")

    leaf = canonical.lstat()
    if leaf.st_uid != os.geteuid():
        raise TrustedTestTmpError("test temp root must be owned by the current user")
    if stat.S_IMODE(leaf.st_mode) != 0o700:
        raise TrustedTestTmpError("test temp root mode must be 0700")
    return canonical


def _fallback_root() -> Path:
    candidate = Path.home() / ".cache" / "trading-agent" / "test-tmp"
    candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
    if candidate.is_symlink():
        raise TrustedTestTmpError("fallback test temp root must not be a symlink")
    candidate.chmod(0o700)
    return _validate_existing_root(candidate)


def select_trusted_test_tmp_root() -> Path:
    """Select the explicit root, a safe ambient root, or the private fallback."""
    explicit = os.environ.get("TRADING_TEST_TMP_ROOT")
    if explicit:
        return _validate_existing_root(Path(explicit))

    ambient = os.environ.get("TMPDIR")
    if ambient:
        try:
            return _validate_existing_root(Path(ambient))
        except TrustedTestTmpError:
            pass
    return _fallback_root()


@dataclass
class TrustedTestTmpSession:
    root: Path
    path: Path
    _device: int
    _inode: int
    _original_environment: dict[str, str | None]
    _original_tempdir: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None
    _cleaned: bool = False

    def cleanup(self) -> None:
        """Remove only the exact directory created by this session."""
        if self._cleaned:
            return

        cleanup_error: TrustedTestTmpError | None = None
        try:
            metadata = self.path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_dev != self._device
                or metadata.st_ino != self._inode
            ):
                cleanup_error = TrustedTestTmpError(
                    "test temp session directory identity changed; refusing cleanup"
                )
            else:
                _make_tree_removable(self.path, expected_device=self._device)
                shutil.rmtree(self.path)
        except FileNotFoundError:
            cleanup_error = TrustedTestTmpError(
                "test temp session directory identity changed; refusing cleanup"
            )
        finally:
            for name, value in self._original_environment.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            tempfile.tempdir = self._original_tempdir
            self._cleaned = True

        if cleanup_error is not None:
            raise cleanup_error


def prepare_trusted_test_tmp(component: str) -> TrustedTestTmpSession:
    """Create and activate one private temporary directory for a test process."""
    if not _COMPONENT_PATTERN.fullmatch(component):
        raise TrustedTestTmpError("invalid test temp component name")

    root = select_trusted_test_tmp_root()
    session_path = Path(tempfile.mkdtemp(prefix=f"{component}-", dir=root))
    session_path.chmod(0o700)
    metadata = session_path.lstat()
    originals = {name: os.environ.get(name) for name in _TEMP_ENVIRONMENT_NAMES}
    for name in _TEMP_ENVIRONMENT_NAMES:
        os.environ[name] = str(session_path)
    original_tempdir = tempfile.tempdir
    tempfile.tempdir = str(session_path)
    return TrustedTestTmpSession(
        root=root,
        path=session_path,
        _device=metadata.st_dev,
        _inode=metadata.st_ino,
        _original_environment=originals,
        _original_tempdir=original_tempdir,
    )
