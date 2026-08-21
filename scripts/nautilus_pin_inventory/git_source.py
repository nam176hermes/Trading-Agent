"""Read immutable, receipt-verified source bytes from exact Git tree objects."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from enum import Enum, auto
import errno
import hashlib
import math
import os
from pathlib import Path
import selectors
import select
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from typing import Callable, Final
import unicodedata

try:  # The approved implementation target is Linux; keep import failure explicit.
    import resource
except ImportError:  # pragma: no cover - this is a Linux-only authority boundary.
    resource = None  # type: ignore[assignment]


_SUPPORTED_OBJECT_FORMATS: Final = frozenset({"sha1", "sha256"})
_BLOCKED_GIT_ENV: Final = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_DIR", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_REPLACE_REF_BASE", "GIT_WORK_TREE",
})
_ALLOWED_MODES: Final = {b"100644": 0o100644, b"100755": 0o100755}
_MAX_ENTRIES: Final = 20_000
_MAX_PATH_BYTES: Final = 4_096
_MAX_BLOB_BYTES: Final = 2_000_000
_MAX_TOTAL_BYTES: Final = 100_000_000
_MAX_TIMEOUT_SECONDS: Final = 30.0
_METADATA_OUTPUT_CAP: Final = 65_536
_STDERR_CAP: Final = 65_536
_MAX_TREE_DEPTH: Final = 64
_MAX_PACK_INDEX_OBJECTS: Final = 2_000_000


class GitAuthorityError(ValueError):
    """An exact Git object cannot be accepted as immutable source authority."""


class GitAuthorityAggregateError(GitAuthorityError):
    """Both the primary authority failure and required cleanup failure occurred."""

    def __init__(self, primary: BaseException, cleanup: BaseException) -> None:
        self.primary = primary
        self.cleanup = cleanup
        super().__init__(f"Git authority failure: {primary}; required cleanup failure: {cleanup}")


_MAX_CLEANUP_CONTEXT_GRAPH_NODES: Final = 64


def _detach_cleanup_context_back_edges(
    incoming: BaseException, owner: BaseException
) -> None:
    """Keep an attached cleanup subtree from implicitly pointing back to its owner."""
    pending = [incoming]
    visited: set[int] = set()
    while pending and len(visited) < _MAX_CLEANUP_CONTEXT_GRAPH_NODES:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        context = current.__context__
        if context is owner:
            current.__context__ = None
        elif context is not None:
            pending.append(context)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if isinstance(current, GitAuthorityAggregateError):
            pending.extend((current.primary, current.cleanup))


class GitAuthorityCleanupPendingError(GitAuthorityAggregateError):
    """A bounded retained-capture cleanup retry remains the caller's authority."""

    def __init__(
        self,
        primary: BaseException,
        cleanup: BaseException,
        capture: "_ClosureCapture",
        *,
        placeholder_primary: bool = False,
    ) -> None:
        super().__init__(primary, cleanup)
        self._capture: _ClosureCapture | None = capture
        self._placeholder_primary = placeholder_primary

    @classmethod
    def _from_capture(
        cls, capture: "_ClosureCapture", cleanup: BaseException
    ) -> "GitAuthorityCleanupPendingError":
        return cls(cleanup, cleanup, capture, placeholder_primary=True)

    @property
    def cleanup_pending(self) -> bool:
        return self._capture is not None

    def retry_cleanup(self) -> None:
        capture = self._capture
        if capture is None:
            return
        try:
            capture._retry_close()
        except BaseException as cleanup:
            _detach_cleanup_context_back_edges(cleanup, self)
            raise self from None
        self._capture = None

    def _adopt_primary(self, primary: BaseException) -> None:
        if self._placeholder_primary:
            self.primary = primary
            self._placeholder_primary = False
        else:
            self.primary = GitAuthorityAggregateError(primary, self.primary)

    def _append_cleanup(self, cleanup: BaseException) -> None:
        if cleanup is self:
            return
        _detach_cleanup_context_back_edges(cleanup, self)
        if isinstance(cleanup, GitAuthorityCleanupPendingError):
            capture = cleanup._capture
            if capture is not None:
                if self._capture is None:
                    self._capture = capture
                elif self._capture is not capture:
                    raise GitAuthorityError("multiple Git capture cleanups are pending")
                cleanup._capture = None
        self.cleanup = GitAuthorityAggregateError(self.cleanup, cleanup)

    def __str__(self) -> str:
        return f"Git authority failure: {self.primary}; required cleanup failure: {self.cleanup}"


class _OwnedDescriptorCleanupError(GitAuthorityError):
    """An owned descriptor's terminal release could not be proved."""


def _combine_primary_and_cleanup(
    primary: BaseException,
    cleanup: BaseException | None,
) -> GitAuthorityError:
    if cleanup is not None:
        if isinstance(primary, GitAuthorityCleanupPendingError):
            primary._append_cleanup(cleanup)
            return primary
        if isinstance(cleanup, GitAuthorityCleanupPendingError):
            cleanup._adopt_primary(primary)
            return cleanup
        return GitAuthorityAggregateError(primary, cleanup)
    if isinstance(primary, GitAuthorityError):
        return primary
    return GitAuthorityError("Git authority operation failed")


def _aggregate_errors(errors: list[BaseException]) -> BaseException | None:
    if not errors:
        return None
    combined = errors[0]
    for error in errors[1:]:
        combined = _combine_primary_and_cleanup(combined, error)
    return combined


def _descriptor_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        stat.S_IMODE(metadata.st_mode),
    )


@dataclass(frozen=True)
class _OwnedDescriptorIdentity:
    device: int
    inode: int
    file_type: int
    uid: int
    gid: int
    mode: int


@dataclass(frozen=True)
class _DescriptorCleanupReceipt:
    attempts: int
    closed_confirmed: bool


def _owned_descriptor_identity(info: os.stat_result) -> _OwnedDescriptorIdentity:
    return _OwnedDescriptorIdentity(
        device=info.st_dev,
        inode=info.st_ino,
        file_type=stat.S_IFMT(info.st_mode),
        uid=info.st_uid,
        gid=info.st_gid,
        mode=stat.S_IMODE(info.st_mode),
    )


def _close_owned_descriptor(
    descriptor: int,
    expected: _OwnedDescriptorIdentity,
    *,
    label: str,
) -> _DescriptorCleanupReceipt:
    for attempt in range(1, 3):
        close_error: OSError | None = None
        try:
            os.close(descriptor)
        except OSError as exc:
            close_error = exc

        try:
            actual = _owned_descriptor_identity(os.fstat(descriptor))
        except OSError as inspect_error:
            if inspect_error.errno == errno.EBADF:
                return _DescriptorCleanupReceipt(
                    attempts=attempt,
                    closed_confirmed=True,
                )
            raise _OwnedDescriptorCleanupError(
                f"{label} descriptor identity is ambiguous"
            ) from inspect_error

        if actual != expected:
            raise _OwnedDescriptorCleanupError(
                f"{label} descriptor identity is ambiguous"
            ) from close_error
        if close_error is not None and attempt == 1:
            continue
        raise _OwnedDescriptorCleanupError(
            f"{label} descriptor cleanup was not confirmed"
        ) from close_error

    raise AssertionError("owned descriptor close attempt bound was not terminal")


def _close_retained_descriptor(
    descriptor: int,
    expected_identity: tuple[int, ...],
    *,
    label: str,
) -> None:
    if not expected_identity or len(expected_identity) > 6:
        raise GitAuthorityError(f"{label} descriptor identity is malformed")
    for attempt in range(2):
        try:
            os.close(descriptor)
        except OSError as close_error:
            try:
                metadata = os.fstat(descriptor)
            except OSError as inspect_error:
                if inspect_error.errno == errno.EBADF:
                    return
                raise GitAuthorityError(
                    f"{label} descriptor cleanup was not confirmed"
                ) from inspect_error
            if _descriptor_identity(metadata)[: len(expected_identity)] != expected_identity:
                raise GitAuthorityError(
                    f"{label} descriptor cleanup was not confirmed"
                ) from close_error
            if attempt == 0:
                continue
            raise GitAuthorityError(
                f"{label} descriptor cleanup was not confirmed"
            ) from close_error
        else:
            return


@dataclass(frozen=True)
class _CleanupReceipt:
    attempts: int
    operation_root_unlinked: bool
    source_links_restored: bool
    descriptors_closed: bool


@dataclass
class _ClosureObject:
    oid: str
    prefix: str
    name: str
    identity: tuple[int, int, int, int, int, int, int] | None
    compressed: bytes


@dataclass(frozen=True)
class _ClosurePackEntry:
    name: str
    identity: tuple[int, int, int, int, int, int, int]


@dataclass(frozen=True)
class _ClosurePackSource:
    directory_fd: int
    directory_identity: _OwnedDescriptorIdentity
    entries: tuple[_ClosurePackEntry, ...]


@dataclass
class _ClosureCapture:
    source: Path
    root_identity: tuple[int, int, int]
    root_fd: int
    prefixes: dict[str, tuple[int, tuple[int, int, int]]]
    objects: tuple[_ClosureObject, ...]
    pack_sources: tuple[_ClosurePackSource, ...] = ()
    pack_links_active: bool = False
    closed: bool = False
    _closed_descriptors: set[int] = field(default_factory=set, init=False, repr=False)

    def close(self) -> None:
        self._close(pending=True)

    def _retry_close(self) -> None:
        self._close(pending=False)

    def _close(self, *, pending: bool) -> None:
        if self.closed:
            return
        retained = [
            (descriptor, identity, f"Git closure prefix {prefix}")
            for prefix, (descriptor, identity) in sorted(self.prefixes.items())
        ]
        retained.append((self.root_fd, self.root_identity, "Git closure root"))
        errors: list[BaseException] = []
        for descriptor, identity, label in retained:
            if descriptor in self._closed_descriptors:
                continue
            try:
                _close_retained_descriptor(descriptor, identity, label=label)
            except BaseException as exc:
                errors.append(exc)
            else:
                self._closed_descriptors.add(descriptor)
        if errors:
            aggregate = _aggregate_errors(errors)
            assert aggregate is not None
            if pending:
                raise GitAuthorityCleanupPendingError._from_capture(self, aggregate)
            raise aggregate
        self.closed = True


@dataclass(frozen=True)
class _PrivateDirectoryIdentity:
    device: int
    inode: int
    file_type: int
    uid: int
    gid: int
    mode: int


def _private_descriptor_identity(identity: _PrivateDirectoryIdentity) -> tuple[int, ...]:
    return (
        identity.device,
        identity.inode,
        identity.file_type,
        identity.uid,
        identity.gid,
        identity.mode,
    )


@dataclass
class _OwnedDirectoryHandle:
    descriptor: int
    identity: _PrivateDirectoryIdentity
    basename: str
    parent_descriptor: int
    closed: bool = False
    owned: bool = True


@dataclass(frozen=True)
class _OwnedFileEntry:
    name: str
    identity: tuple[int, ...]
    sha256: str
    size: int
    mode: int


def _require_safe_child_name(name: str, *, label: str) -> str:
    unsafe = (
        type(name) is not str
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    )
    if not unsafe:
        assert type(name) is str
        unsafe = (
            unicodedata.normalize("NFC", name) != name
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in name
            )
        )
        try:
            unsafe = unsafe or len(name.encode("utf-8")) > 255
        except UnicodeEncodeError:
            unsafe = True
    if unsafe:
        raise GitAuthorityError(f"{label} name is unsafe")
    return name


def _owned_identity_from_private(
    identity: _PrivateDirectoryIdentity,
) -> _OwnedDescriptorIdentity:
    return _OwnedDescriptorIdentity(
        device=identity.device,
        inode=identity.inode,
        file_type=identity.file_type,
        uid=identity.uid,
        gid=identity.gid,
        mode=identity.mode,
    )


def _private_file_identity(
    metadata: os.stat_result,
    *,
    label: str,
) -> tuple[int, ...]:
    if not stat.S_ISREG(metadata.st_mode):
        raise GitAuthorityError(f"{label} is not a regular file")
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
        metadata.st_size,
    )
    if identity[3] != os.geteuid() or identity[4] != os.getegid():
        raise GitAuthorityError(f"{label} owner changed")
    return identity


def _close_owned_directory_handle(
    handle: _OwnedDirectoryHandle,
    *,
    label: str,
) -> None:
    if handle.closed:
        return
    if not handle.owned:
        raise GitAuthorityError(f"{label} descriptor ownership changed")
    _close_owned_descriptor(
        handle.descriptor,
        _owned_identity_from_private(handle.identity),
        label=label,
    )
    handle.closed = True
    handle.owned = False


def _mkdir_open_private_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
) -> _OwnedDirectoryHandle:
    child = _require_safe_child_name(name, label=label)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    named_identity: _PrivateDirectoryIdentity | None = None
    descriptor_identity: _PrivateDirectoryIdentity | None = None
    created = False
    try:
        os.mkdir(child, 0o700, dir_fd=parent_fd)
        created = True
        named_identity = _private_directory_identity(
            os.stat(child, dir_fd=parent_fd, follow_symlinks=False),
            expected_mode=0o700,
        )
        descriptor = os.open(child, flags, dir_fd=parent_fd)
        descriptor_identity = _private_directory_identity(
            os.fstat(descriptor),
            expected_mode=0o700,
        )
        if descriptor_identity != named_identity:
            raise GitAuthorityError(f"{label} identity changed")
        return _OwnedDirectoryHandle(
            descriptor=descriptor,
            identity=descriptor_identity,
            basename=child,
            parent_descriptor=parent_fd,
        )
    except BaseException as exc:
        if isinstance(exc, GitAuthorityError):
            primary: BaseException = exc
        else:
            primary = GitAuthorityError(f"{label} creation failed")
            primary.__cause__ = exc
        cleanup_errors: list[BaseException] = []
        cleanup_identity = descriptor_identity or named_identity
        if created:
            try:
                if cleanup_identity is None:
                    raise GitAuthorityError(f"{label} cleanup identity is unavailable")
                current = _private_directory_identity(
                    os.stat(child, dir_fd=parent_fd, follow_symlinks=False),
                    expected_mode=cleanup_identity.mode,
                )
                if current != cleanup_identity:
                    raise GitAuthorityError(f"{label} cleanup identity changed")
                if descriptor is None:
                    raise GitAuthorityError(f"{label} cleanup descriptor is unavailable")
                if _directory_inventory_at(descriptor, label=label):
                    raise GitAuthorityError(f"{label} cleanup directory is not empty")
                os.rmdir(child, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
        if descriptor is not None and cleanup_identity is not None:
            try:
                _close_owned_descriptor(
                    descriptor,
                    _owned_identity_from_private(cleanup_identity),
                    label=label,
                )
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
        cleanup = _aggregate_errors(cleanup_errors)
        if cleanup is not None:
            raise GitAuthorityAggregateError(primary, cleanup) from exc
        raise primary from exc


def _write_private_file_at(
    directory_fd: int,
    name: str,
    content: bytes,
    *,
    label: str,
) -> _OwnedFileEntry:
    child = _require_safe_child_name(name, label=label)
    if type(content) is not bytes:
        raise GitAuthorityError(f"{label} content is invalid")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | os.O_CLOEXEC
    )
    descriptor: int | None = None
    descriptor_identity: _OwnedDescriptorIdentity | None = None
    file_identity: tuple[int, ...] | None = None
    created = False
    try:
        descriptor = os.open(child, flags, 0o600, dir_fd=directory_fd)
        created = True
        os.fchmod(descriptor, 0o600)
        offset = 0
        view = memoryview(content)
        while offset < len(content):
            try:
                written = os.write(descriptor, view[offset:])
            except OSError as exc:
                raise GitAuthorityError(f"{label} write failed") from exc
            if written <= 0 or written > len(content) - offset:
                raise GitAuthorityError(f"{label} write was incomplete")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        file_identity = _private_file_identity(metadata, label=label)
        if (
            file_identity[5] != 0o400
            or file_identity[6] != 1
            or file_identity[7] != len(content)
        ):
            raise GitAuthorityError(f"{label} sealed identity changed")
        descriptor_identity = _owned_descriptor_identity(metadata)
        digest = hashlib.sha256(content).hexdigest()
        _close_owned_descriptor(descriptor, descriptor_identity, label=label)
        descriptor = None
        current = _private_file_identity(
            os.stat(child, dir_fd=directory_fd, follow_symlinks=False),
            label=label,
        )
        if current != file_identity:
            raise GitAuthorityError(f"{label} named identity changed")
        os.fsync(directory_fd)
        return _OwnedFileEntry(
            name=child,
            identity=file_identity,
            sha256=digest,
            size=len(content),
            mode=0o400,
        )
    except BaseException as exc:
        if isinstance(exc, GitAuthorityError):
            primary: BaseException = exc
        else:
            primary = GitAuthorityError(f"{label} creation failed")
            primary.__cause__ = exc
        cleanup_errors: list[BaseException] = []
        if descriptor is not None:
            try:
                metadata = os.fstat(descriptor)
                descriptor_identity = _owned_descriptor_identity(metadata)
                file_identity = _private_file_identity(metadata, label=label)
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            if descriptor_identity is not None:
                try:
                    _close_owned_descriptor(
                        descriptor,
                        descriptor_identity,
                        label=label,
                    )
                    descriptor = None
                except BaseException as cleanup_exc:
                    cleanup_errors.append(cleanup_exc)
        if created:
            try:
                if file_identity is None:
                    raise GitAuthorityError(f"{label} cleanup identity is unavailable")
                current = _private_file_identity(
                    os.stat(child, dir_fd=directory_fd, follow_symlinks=False),
                    label=label,
                )
                if current != file_identity:
                    raise GitAuthorityError(f"{label} cleanup identity changed")
                os.unlink(child, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
        cleanup = _aggregate_errors(cleanup_errors)
        if cleanup is not None:
            raise GitAuthorityAggregateError(primary, cleanup) from exc
        raise primary from exc


def _link_private_file_at(
    *,
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
    expected_source_identity: tuple[int, ...],
    label: str,
) -> _OwnedFileEntry:
    source_child = _require_safe_child_name(source_name, label=f"{label} source")
    destination_child = _require_safe_child_name(
        destination_name,
        label=f"{label} destination",
    )
    if len(expected_source_identity) != 7:
        raise GitAuthorityError(f"{label} source identity is malformed")
    linked = False
    source_before: tuple[int, int, int, int, int, int, int] | None = None
    destination_identity: tuple[int, ...] | None = None
    try:
        source_before = _store_identity(
            os.stat(source_child, dir_fd=source_fd, follow_symlinks=False)
        )
        if source_before != expected_source_identity:
            raise GitAuthorityError(f"{label} source identity changed")
        descriptor = os.open(
            source_child,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=source_fd,
        )
        descriptor_identity: _OwnedDescriptorIdentity | None = None
        try:
            descriptor_metadata = os.fstat(descriptor)
            descriptor_identity = _owned_descriptor_identity(descriptor_metadata)
            if _store_identity(descriptor_metadata) != source_before:
                raise GitAuthorityError(f"{label} source descriptor identity changed")
            remaining = source_before[4]
            digest = hashlib.sha256()
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    raise GitAuthorityError(f"{label} source was truncated")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise GitAuthorityError(f"{label} source size changed")
        finally:
            if descriptor_identity is None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            else:
                _close_owned_descriptor(
                    descriptor,
                    descriptor_identity,
                    label=f"{label} source",
                )
        os.link(
            source_child,
            destination_child,
            src_dir_fd=source_fd,
            dst_dir_fd=destination_fd,
            follow_symlinks=False,
        )
        linked = True
        source_after = _store_identity(
            os.stat(source_child, dir_fd=source_fd, follow_symlinks=False)
        )
        expected_after_stable = (
            source_before[:3] + source_before[4:6]
        )
        if (
            source_after[:3] + source_after[4:6] != expected_after_stable
            or source_after[3] != source_before[3] + 1
        ):
            raise GitAuthorityError(f"{label} source changed during hardlink")
        destination_metadata = os.stat(
            destination_child,
            dir_fd=destination_fd,
            follow_symlinks=False,
        )
        if _store_identity(destination_metadata) != source_after:
            raise GitAuthorityError(f"{label} destination identity mismatch")
        destination_identity = _private_file_identity(
            destination_metadata,
            label=label,
        )
        os.fsync(destination_fd)
        return _OwnedFileEntry(
            name=destination_child,
            identity=destination_identity,
            sha256=digest.hexdigest(),
            size=destination_metadata.st_size,
            mode=stat.S_IMODE(destination_metadata.st_mode),
        )
    except BaseException as exc:
        if isinstance(exc, GitAuthorityError):
            primary: BaseException = exc
        else:
            primary = GitAuthorityError(f"{label} hardlink failed")
            primary.__cause__ = exc
        cleanup_errors: list[BaseException] = []
        if linked:
            try:
                current = _private_file_identity(
                    os.stat(
                        destination_child,
                        dir_fd=destination_fd,
                        follow_symlinks=False,
                    ),
                    label=label,
                )
                if destination_identity is not None and current != destination_identity:
                    raise GitAuthorityError(f"{label} cleanup identity changed")
                if source_before is None or current[:3] != source_before[:3]:
                    raise GitAuthorityError(f"{label} cleanup source identity changed")
                os.unlink(destination_child, dir_fd=destination_fd)
                os.fsync(destination_fd)
                restored = _store_identity(
                    os.stat(source_child, dir_fd=source_fd, follow_symlinks=False)
                )
                if (
                    restored[:3] + restored[4:6]
                    != source_before[:3] + source_before[4:6]
                    or restored[3] != source_before[3]
                ):
                    raise GitAuthorityError(f"{label} source nlink was not restored")
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
        cleanup = _aggregate_errors(cleanup_errors)
        if cleanup is not None:
            raise GitAuthorityAggregateError(primary, cleanup) from exc
        raise primary from exc


def _directory_inventory_at(
    directory_fd: int,
    *,
    label: str,
) -> tuple[str, ...]:
    try:
        _private_directory_identity(os.fstat(directory_fd))
        entries = os.listdir(directory_fd)
    except OSError as exc:
        raise GitAuthorityError(f"{label} inventory failed") from exc
    normalized: list[str] = []
    for entry in entries:
        normalized.append(_require_safe_child_name(entry, label=label))
    return tuple(sorted(normalized))


def _verify_owned_directory_handle(
    handle: _OwnedDirectoryHandle,
    *,
    label: str,
) -> None:
    if handle.closed or not handle.owned:
        raise GitAuthorityError(f"{label} descriptor ownership changed")
    try:
        descriptor_identity = _private_directory_identity(
            os.fstat(handle.descriptor),
            expected_mode=handle.identity.mode,
        )
        named_identity = _private_directory_identity(
            os.stat(
                handle.basename,
                dir_fd=handle.parent_descriptor,
                follow_symlinks=False,
            ),
            expected_mode=handle.identity.mode,
        )
    except OSError as exc:
        raise GitAuthorityError(f"{label} identity is unavailable") from exc
    if descriptor_identity != handle.identity or named_identity != handle.identity:
        raise GitAuthorityError(f"{label} identity changed")


def _verify_owned_file_entry_at(
    directory_fd: int,
    entry: _OwnedFileEntry,
    *,
    label: str,
    deadline: float,
) -> tuple[int, int, int, int, int, int, int]:
    _seal_deadline(deadline)
    try:
        named_metadata = os.stat(
            entry.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise GitAuthorityError(f"{label} is unavailable") from exc
    named_identity = _private_file_identity(named_metadata, label=label)
    if (
        named_identity != entry.identity
        or named_identity[5] != entry.mode
        or named_identity[7] != entry.size
    ):
        raise GitAuthorityError(f"{label} identity changed")

    descriptor: int | None = None
    descriptor_identity: _OwnedDescriptorIdentity | None = None
    try:
        descriptor = os.open(
            entry.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        descriptor_metadata = os.fstat(descriptor)
        descriptor_identity = _owned_descriptor_identity(descriptor_metadata)
        if _private_file_identity(descriptor_metadata, label=label) != entry.identity:
            raise GitAuthorityError(f"{label} descriptor identity changed")
        remaining = entry.size
        digest = hashlib.sha256()
        while remaining:
            _seal_deadline(deadline)
            try:
                chunk = os.read(descriptor, min(65_536, remaining))
            except OSError as exc:
                raise GitAuthorityError(f"{label} could not be read") from exc
            if not chunk:
                raise GitAuthorityError(f"{label} was truncated")
            digest.update(chunk)
            remaining -= len(chunk)
        try:
            if os.read(descriptor, 1):
                raise GitAuthorityError(f"{label} size changed")
        except OSError as exc:
            raise GitAuthorityError(f"{label} could not be read") from exc
    finally:
        if descriptor is not None:
            if descriptor_identity is None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            else:
                _close_owned_descriptor(
                    descriptor,
                    descriptor_identity,
                    label=label,
                )
    if digest.hexdigest() != entry.sha256:
        raise GitAuthorityError(f"{label} SHA-256 changed")
    current_metadata = os.stat(
        entry.name,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    if _private_file_identity(current_metadata, label=label) != entry.identity:
        raise GitAuthorityError(f"{label} named identity changed")
    return _store_identity(current_metadata)


@dataclass(frozen=True)
class _ObjectDirectoryAuthority:
    descriptor: int
    identity: _PrivateDirectoryIdentity
    child_path: str
    pass_fds: tuple[int, ...]


class _DirectoryMutationGuard:
    _EVENT_HEADER = struct.Struct("iIII")
    _MUTATION_MASK = (
        0x00000002  # IN_MODIFY
        | 0x00000004  # IN_ATTRIB
        | 0x00000040  # IN_MOVED_FROM
        | 0x00000080  # IN_MOVED_TO
        | 0x00000100  # IN_CREATE
        | 0x00000200  # IN_DELETE
        | 0x00000400  # IN_DELETE_SELF
        | 0x00000800  # IN_MOVE_SELF
        | 0x00002000  # IN_UNMOUNT
        | 0x00004000  # IN_Q_OVERFLOW
        | 0x00008000  # IN_IGNORED
    )

    def __init__(self, descriptor: int, labels: dict[int, str]) -> None:
        self._descriptor = descriptor
        self._labels = labels
        self._closed = False

    @classmethod
    def arm(cls, directories: tuple[tuple[int, str], ...]) -> "_DirectoryMutationGuard":
        if not sys.platform.startswith("linux"):
            raise GitAuthorityError("private Git mutation guard requires Linux inotify")
        if not directories:
            raise GitAuthorityError("private Git mutation guard has no retained directories")
        libc = ctypes.CDLL(None, use_errno=True)
        init = libc.inotify_init1
        init.argtypes = [ctypes.c_int]
        init.restype = ctypes.c_int
        add_watch = libc.inotify_add_watch
        add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        add_watch.restype = ctypes.c_int
        descriptor = init(os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        if descriptor < 0:
            detail = errno.errorcode.get(ctypes.get_errno(), "UNKNOWN")
            raise GitAuthorityError(f"private Git mutation guard could not initialize: {detail}")
        labels: dict[int, str] = {}
        try:
            for directory_fd, label in directories:
                try:
                    os.fstat(directory_fd)
                except OSError as exc:
                    raise GitAuthorityError(
                        f"private Git mutation guard retained {label} is unavailable"
                    ) from exc
                watch = add_watch(
                    descriptor,
                    os.fsencode(f"/proc/self/fd/{directory_fd}"),
                    cls._MUTATION_MASK,
                )
                if watch < 0:
                    detail = errno.errorcode.get(ctypes.get_errno(), "UNKNOWN")
                    raise GitAuthorityError(
                        f"private Git mutation guard could not watch {label}: {detail}"
                    )
                labels[watch] = label
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        return cls(descriptor, labels)

    def assert_quiet(self) -> None:
        if self._closed:
            raise GitAuthorityError("private Git mutation guard is closed")
        while True:
            try:
                events = os.read(self._descriptor, 65_536)
            except BlockingIOError:
                return
            except OSError as exc:
                raise GitAuthorityError("private Git mutation guard could not be read") from exc
            if not events:
                raise GitAuthorityError("private Git mutation guard ended unexpectedly")
            offset = 0
            while offset < len(events):
                if len(events) - offset < self._EVENT_HEADER.size:
                    raise GitAuthorityError("private Git mutation guard returned a truncated event")
                watch, mask, _cookie, name_length = self._EVENT_HEADER.unpack_from(events, offset)
                offset += self._EVENT_HEADER.size
                if name_length > len(events) - offset:
                    raise GitAuthorityError("private Git mutation guard returned a truncated event")
                offset += name_length
                if mask & self._MUTATION_MASK:
                    label = self._labels.get(watch, "directory")
                    raise GitAuthorityError(
                        f"private Git mutation guard observed authority drift in {label}"
                    )

    def close(self) -> None:
        if self._closed:
            return
        try:
            os.close(self._descriptor)
        except OSError as exc:
            raise GitAuthorityError("private Git mutation guard cleanup failed") from exc
        self._closed = True


@dataclass(frozen=True)
class _PrivateClosureEntry:
    oid: str
    object_type: str
    prefix: str
    name: str
    identity: tuple[int, int, int, int, int, int, int]
    compressed_sha256: str
    payload_sha256: str


class _PrivateClosureOwnershipState(Enum):
    BUILDER = auto()
    STORE = auto()
    RELEASED = auto()


@dataclass(slots=True)
class _PrivateClosureOwnership:
    state: _PrivateClosureOwnershipState
    owner: _OwnedTemporaryRoot
    objects: _OwnedDirectoryHandle
    pack: _OwnedDirectoryHandle
    prefixes: dict[str, _OwnedDirectoryHandle]
    loose_entries: dict[tuple[str, str], _OwnedFileEntry]
    pack_entries: dict[str, _OwnedFileEntry]
    source_nlinks: dict[tuple[int, str], tuple[tuple[int, ...], int]]
    inventory: tuple[_PrivateClosureEntry, ...] = ()
    root_identity: _PrivateDirectoryIdentity | None = None
    active_source_receipts: dict[tuple[int, str], tuple[int, ...]] = field(
        default_factory=dict
    )
    object_authority: _ObjectDirectoryAuthority | None = None
    cleanup_started: bool = False
    cleanup_completed: bool = False
    terminal_cleanup_error: BaseException | None = None
    restored_source_receipts: dict[tuple[int, str], tuple[int, ...]] = field(
        default_factory=dict
    )

    def require(self, expected: _PrivateClosureOwnershipState) -> None:
        if self.state is not expected:
            raise GitAuthorityError("private closure ownership state is invalid")

    def transfer_to_store(self) -> None:
        self.require(_PrivateClosureOwnershipState.BUILDER)
        self.state = _PrivateClosureOwnershipState.STORE

    def mark_released(self) -> None:
        if self.state is _PrivateClosureOwnershipState.RELEASED:
            return
        self.state = _PrivateClosureOwnershipState.RELEASED


class _PrivateClosureStore:
    def __init__(
        self,
        *,
        ownership: _PrivateClosureOwnership,
        root_fd: int,
        root_identity: _PrivateDirectoryIdentity,
        objects_fd: int,
        objects_identity: _PrivateDirectoryIdentity,
        pack_fd: int,
        pack_identity: _PrivateDirectoryIdentity,
        prefixes: dict[str, tuple[int, _PrivateDirectoryIdentity]],
        entries: tuple[_PrivateClosureEntry, ...],
        pack_entries: dict[str, _OwnedFileEntry],
        source_nlinks: dict[tuple[int, str], tuple[tuple[int, ...], int]],
        active_source_receipts: dict[tuple[int, str], tuple[int, ...]],
        object_authority: _ObjectDirectoryAuthority,
    ) -> None:
        expected_prefixes = {
            prefix: (handle.descriptor, handle.identity)
            for prefix, handle in sorted(ownership.prefixes.items())
        }
        if (
            root_fd != ownership.owner.root_fd
            or root_identity != ownership.root_identity
            or objects_fd != ownership.objects.descriptor
            or objects_identity != ownership.objects.identity
            or pack_fd != ownership.pack.descriptor
            or pack_identity != ownership.pack.identity
            or prefixes != expected_prefixes
            or entries != ownership.inventory
            or pack_entries != ownership.pack_entries
            or source_nlinks != ownership.source_nlinks
            or active_source_receipts != ownership.active_source_receipts
            or object_authority != ownership.object_authority
        ):
            raise GitAuthorityError(
                "private Git object-store constructor metadata is invalid"
            )
        self._ownership = ownership

    @property
    def ownership(self) -> _PrivateClosureOwnership:
        return self._ownership

    @property
    def active(self) -> bool:
        return self._ownership.state is _PrivateClosureOwnershipState.STORE

    @property
    def owner(self) -> _OwnedTemporaryRoot:
        return self._ownership.owner

    @property
    def root_fd(self) -> int:
        return self._ownership.owner.root_fd

    @property
    def root_identity(self) -> _PrivateDirectoryIdentity:
        identity = self._ownership.root_identity
        if identity is None:
            raise GitAuthorityError(
                "private Git object-store root receipt is unavailable"
            )
        return identity

    @property
    def objects_fd(self) -> int:
        return self._ownership.objects.descriptor

    @property
    def objects_identity(self) -> _PrivateDirectoryIdentity:
        return self._ownership.objects.identity

    @property
    def pack_fd(self) -> int:
        return self._ownership.pack.descriptor

    @property
    def pack_identity(self) -> _PrivateDirectoryIdentity:
        return self._ownership.pack.identity

    @property
    def prefixes(self) -> dict[str, tuple[int, _PrivateDirectoryIdentity]]:
        return {
            prefix: (handle.descriptor, handle.identity)
            for prefix, handle in sorted(self._ownership.prefixes.items())
        }

    @property
    def entries(self) -> tuple[_PrivateClosureEntry, ...]:
        return self._ownership.inventory

    @property
    def pack_entries(self) -> dict[str, _OwnedFileEntry]:
        return self._ownership.pack_entries

    @property
    def source_nlinks(
        self,
    ) -> dict[tuple[int, str], tuple[tuple[int, ...], int]]:
        return self._ownership.source_nlinks

    @property
    def active_source_receipts(
        self,
    ) -> dict[tuple[int, str], tuple[int, ...]]:
        return self._ownership.active_source_receipts

    @property
    def restored_source_receipts(
        self,
    ) -> dict[tuple[int, str], tuple[int, ...]]:
        return self._ownership.restored_source_receipts

    @property
    def object_authority(self) -> _ObjectDirectoryAuthority:
        authority = self._ownership.object_authority
        if authority is None:
            raise GitAuthorityError(
                "private Git object-store object authority is unavailable"
            )
        return authority

    @property
    def closed(self) -> bool:
        return self._ownership.state is _PrivateClosureOwnershipState.RELEASED

    @property
    def descriptors_closed(self) -> bool:
        ownership = self._ownership
        return (
            ownership.owner.root_descriptor_closed
            and ownership.owner.parent_descriptor_closed
            and ownership.objects.closed
            and ownership.pack.closed
            and all(handle.closed for handle in ownership.prefixes.values())
        )

    @property
    def cleanup_started(self) -> bool:
        return self._ownership.cleanup_started

    @property
    def cleanup_completed(self) -> bool:
        return self._ownership.cleanup_completed

    @property
    def _terminal_cleanup_error(self) -> BaseException | None:
        return self._ownership.terminal_cleanup_error

    def close(self) -> None:
        state = self._ownership.state
        if state is _PrivateClosureOwnershipState.RELEASED:
            return
        if state is _PrivateClosureOwnershipState.BUILDER:
            raise GitAuthorityError("private Git object-store store is inactive")
        self._ownership.require(_PrivateClosureOwnershipState.STORE)
        _cleanup_private_closure_ownership(self._ownership)


def _require_owned_directory(
    identity: _OwnedDescriptorIdentity,
    *,
    label: str,
    private: bool,
) -> None:
    if identity.file_type != stat.S_IFDIR:
        raise GitAuthorityError(f"{label} is not a directory")
    if private and (identity.uid != os.geteuid() or identity.gid != os.getegid()):
        raise GitAuthorityError(f"{label} owner changed")
    if private and identity.mode != 0o700:
        raise GitAuthorityError(f"{label} mode changed")


def _owned_directory_is_empty(descriptor: int) -> bool:
    try:
        with os.scandir(descriptor) as iterator:
            return next(iterator, None) is None
    except OSError as exc:
        raise GitAuthorityError("owned temporary directory inventory failed") from exc


@dataclass
class _OwnedTemporaryRoot:
    parent_fd: int
    parent_identity: _OwnedDescriptorIdentity
    parent_path_hint: Path
    basename: str
    root_fd: int
    root_identity: _OwnedDescriptorIdentity
    root_descriptor_closed: bool = False
    parent_descriptor_closed: bool = False

    @property
    def path_hint(self) -> Path:
        return self.parent_path_hint / self.basename

    @classmethod
    def create(cls, parent: Path) -> "_OwnedTemporaryRoot":
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent_fd: int | None = None
        parent_identity: _OwnedDescriptorIdentity | None = None
        root_fd: int | None = None
        root_identity: _OwnedDescriptorIdentity | None = None
        named_root_identity: _OwnedDescriptorIdentity | None = None
        basename: str | None = None
        root_created = False
        try:
            parent_fd = os.open(parent, flags)
            parent_identity = _owned_descriptor_identity(os.fstat(parent_fd))
            _require_owned_directory(
                parent_identity,
                label="temporary-root parent authority",
                private=False,
            )
            named_parent_identity = _owned_descriptor_identity(
                os.stat(parent, follow_symlinks=False)
            )
            if named_parent_identity != parent_identity:
                raise GitAuthorityError("temporary-root parent identity changed")

            basename = f"p1-u00-pack-{os.urandom(16).hex()}"
            os.mkdir(basename, 0o700, dir_fd=parent_fd)
            root_created = True
            named_root_identity = _owned_descriptor_identity(
                os.stat(basename, dir_fd=parent_fd, follow_symlinks=False)
            )
            _require_owned_directory(
                named_root_identity,
                label="owned temporary root",
                private=True,
            )
            root_fd = os.open(basename, flags, dir_fd=parent_fd)
            root_identity = _owned_descriptor_identity(os.fstat(root_fd))
            _require_owned_directory(
                root_identity,
                label="owned temporary root",
                private=True,
            )
            if root_identity != named_root_identity:
                raise GitAuthorityError("owned temporary root identity changed")
            return cls(
                parent_fd=parent_fd,
                parent_identity=parent_identity,
                parent_path_hint=parent,
                basename=basename,
                root_fd=root_fd,
                root_identity=root_identity,
            )
        except BaseException as exc:
            if isinstance(exc, GitAuthorityError):
                primary: BaseException = exc
            else:
                primary = GitAuthorityError(
                    "private Git pack bootstrap temporary root is unavailable"
                )
                primary.__cause__ = exc
            cleanup_errors: list[BaseException] = []
            if root_created:
                name_confirmed = False
                if (
                    parent_fd is not None
                    and basename is not None
                    and named_root_identity is not None
                ):
                    try:
                        current = _owned_descriptor_identity(
                            os.stat(
                                basename,
                                dir_fd=parent_fd,
                                follow_symlinks=False,
                            )
                        )
                    except OSError:
                        current = None
                    if current == named_root_identity:
                        name_confirmed = True
                        cleanup_fd = root_fd
                        cleanup_identity = root_identity or named_root_identity
                        opened_for_cleanup = False
                        if cleanup_fd is None:
                            try:
                                cleanup_fd = os.open(
                                    basename, flags, dir_fd=parent_fd
                                )
                                opened_for_cleanup = True
                                cleanup_identity = _owned_descriptor_identity(
                                    os.fstat(cleanup_fd)
                                )
                            except BaseException as cleanup_exc:
                                cleanup_errors.append(cleanup_exc)
                        if cleanup_fd is not None:
                            try:
                                if cleanup_identity != named_root_identity:
                                    raise GitAuthorityError(
                                        "owned temporary root cleanup identity changed"
                                    )
                                if not _owned_directory_is_empty(cleanup_fd):
                                    raise GitAuthorityError(
                                        "owned temporary root cleanup was not empty"
                                    )
                                os.rmdir(basename, dir_fd=parent_fd)
                                os.fsync(parent_fd)
                            except BaseException as cleanup_exc:
                                cleanup_errors.append(cleanup_exc)
                            if opened_for_cleanup:
                                try:
                                    _close_owned_descriptor(
                                        cleanup_fd,
                                        cleanup_identity,
                                        label="owned temporary root",
                                    )
                                except BaseException as cleanup_exc:
                                    cleanup_errors.append(cleanup_exc)
                if not name_confirmed:
                    cleanup_errors.append(
                        GitAuthorityError(
                            "owned temporary root-name cleanup was not confirmed"
                        )
                    )
            unknown_identity = _OwnedDescriptorIdentity(-1, -1, -1, -1, -1, -1)
            if root_fd is not None:
                try:
                    _close_owned_descriptor(
                        root_fd,
                        root_identity or named_root_identity or unknown_identity,
                        label="owned temporary root",
                    )
                except BaseException as cleanup_exc:
                    cleanup_errors.append(cleanup_exc)
            if parent_fd is not None:
                try:
                    _close_owned_descriptor(
                        parent_fd,
                        parent_identity or unknown_identity,
                        label="owned temporary-root parent",
                    )
                except BaseException as cleanup_exc:
                    cleanup_errors.append(cleanup_exc)
            cleanup = _aggregate_errors(cleanup_errors)
            if cleanup is not None:
                raise GitAuthorityAggregateError(primary, cleanup) from exc
            raise primary from exc


def _cleanup_empty_owned_temporary_root(
    owner: _OwnedTemporaryRoot,
    *,
    label: str,
) -> None:
    errors: list[BaseException] = []
    root_name_unconfirmed = False
    try:
        current = _owned_descriptor_identity(
            os.stat(
                owner.basename,
                dir_fd=owner.parent_fd,
                follow_symlinks=False,
            )
        )
    except OSError:
        root_name_unconfirmed = True
    else:
        if current != owner.root_identity or not _owned_directory_is_empty(
            owner.root_fd
        ):
            root_name_unconfirmed = True
        else:
            try:
                os.rmdir(owner.basename, dir_fd=owner.parent_fd)
                os.fsync(owner.parent_fd)
            except BaseException as exc:
                errors.append(exc)
    for descriptor, identity, descriptor_label, receipt_name in (
        (
            owner.root_fd,
            owner.root_identity,
            f"{label} root",
            "root",
        ),
        (
            owner.parent_fd,
            owner.parent_identity,
            f"{label} parent",
            "parent",
        ),
    ):
        try:
            _close_owned_descriptor(
                descriptor,
                identity,
                label=descriptor_label,
            )
        except BaseException as exc:
            errors.append(exc)
        else:
            if receipt_name == "root":
                owner.root_descriptor_closed = True
            else:
                owner.parent_descriptor_closed = True
    if root_name_unconfirmed:
        errors.append(GitAuthorityError(f"{label} root-name cleanup was not confirmed"))
    aggregate = _aggregate_errors(errors)
    if aggregate is not None:
        raise aggregate


def _remove_empty_owned_directory_handle(
    handle: _OwnedDirectoryHandle,
    *,
    label: str,
) -> None:
    if handle.closed:
        return
    if not handle.owned:
        raise GitAuthorityError(f"{label} descriptor ownership changed")
    errors: list[BaseException] = []
    try:
        current = _private_directory_identity(
            os.stat(
                handle.basename,
                dir_fd=handle.parent_descriptor,
                follow_symlinks=False,
            ),
            expected_mode=handle.identity.mode,
        )
        if current != handle.identity:
            raise GitAuthorityError(f"{label} cleanup identity changed")
        if _directory_inventory_at(handle.descriptor, label=label):
            raise GitAuthorityError(f"{label} cleanup directory is not empty")
        os.rmdir(handle.basename, dir_fd=handle.parent_descriptor)
        os.fsync(handle.parent_descriptor)
    except BaseException as exc:
        errors.append(exc)
    try:
        _close_owned_directory_handle(handle, label=label)
    except BaseException as exc:
        errors.append(exc)
    aggregate = _aggregate_errors(errors)
    if aggregate is not None:
        raise aggregate


def _cleanup_private_closure_ownership(
    ownership: _PrivateClosureOwnership,
    *,
    confirm_source_nlinks_restored: (
        Callable[[], dict[tuple[int, str], tuple[int, ...]]] | None
    ) = None,
) -> dict[tuple[int, str], tuple[int, ...]]:
    if ownership.state is _PrivateClosureOwnershipState.RELEASED:
        return dict(ownership.restored_source_receipts)
    if ownership.terminal_cleanup_error is not None:
        raise ownership.terminal_cleanup_error
    if ownership.cleanup_started:
        if ownership.cleanup_completed:
            return dict(ownership.restored_source_receipts)
        raise GitAuthorityError(
            "private Git object-store cleanup state is unconfirmed"
        )

    ownership.cleanup_started = True
    errors: list[BaseException] = []
    restored_sources: dict[tuple[int, str], tuple[int, ...]] = {}
    for (prefix, name), entry in sorted(ownership.loose_entries.items()):
        handle = ownership.prefixes.get(prefix)
        if handle is None or handle.closed:
            errors.append(
                GitAuthorityError(
                    "private Git object-store loose cleanup authority is unavailable"
                )
            )
            continue
        try:
            current = _private_file_identity(
                os.stat(
                    name,
                    dir_fd=handle.descriptor,
                    follow_symlinks=False,
                ),
                label="private Git object-store loose object",
            )
            if current != entry.identity:
                raise GitAuthorityError(
                    "private Git object-store loose cleanup identity changed"
                )
            os.unlink(name, dir_fd=handle.descriptor)
            os.fsync(handle.descriptor)
        except BaseException as exc:
            errors.append(exc)

    for name, entry in sorted(ownership.pack_entries.items()):
        try:
            current = _private_file_identity(
                os.stat(
                    name,
                    dir_fd=ownership.pack.descriptor,
                    follow_symlinks=False,
                ),
                label="private Git object-store pack entry",
            )
            if current != entry.identity:
                raise GitAuthorityError(
                    "private Git object-store pack cleanup identity changed"
                )
            os.unlink(name, dir_fd=ownership.pack.descriptor)
            os.fsync(ownership.pack.descriptor)
        except BaseException as exc:
            errors.append(exc)

    if confirm_source_nlinks_restored is not None:
        try:
            restored_sources = confirm_source_nlinks_restored()
        except BaseException as exc:
            errors.append(exc)
    else:
        for key, (identity, expected_nlink) in sorted(
            ownership.source_nlinks.items(),
            key=lambda item: (item[0][1], item[0][0]),
        ):
            source_fd, source_name = key
            try:
                current = _store_identity(
                    os.stat(
                        source_name,
                        dir_fd=source_fd,
                        follow_symlinks=False,
                    )
                )
                if (
                    current[:3] + current[4:6] != identity[:3] + identity[4:6]
                    or current[3] != expected_nlink
                ):
                    raise GitAuthorityError(
                        "private Git object-store source nlink was not restored"
                    )
                restored_sources[key] = current
            except BaseException as exc:
                errors.append(exc)
    if len(restored_sources) == len(ownership.source_nlinks):
        ownership.restored_source_receipts = restored_sources

    for prefix in sorted(ownership.prefixes):
        try:
            _remove_empty_owned_directory_handle(
                ownership.prefixes[prefix],
                label="private Git object-store prefix",
            )
        except BaseException as exc:
            errors.append(exc)
    for handle, label in (
        (ownership.pack, "private Git object-store pack directory"),
        (ownership.objects, "private Git object-store objects directory"),
    ):
        try:
            _remove_empty_owned_directory_handle(handle, label=label)
        except BaseException as exc:
            errors.append(exc)
    try:
        _cleanup_empty_owned_temporary_root(
            ownership.owner,
            label="private Git object-store",
        )
    except BaseException as exc:
        errors.append(exc)

    aggregate = _aggregate_errors(errors)
    if aggregate is not None:
        if not isinstance(aggregate, GitAuthorityError):
            cleanup_error = GitAuthorityError(
                "private Git object-store descriptor-relative cleanup failed"
            )
            cleanup_error.__cause__ = aggregate
            aggregate = cleanup_error
        ownership.terminal_cleanup_error = aggregate
        raise aggregate
    ownership.cleanup_completed = True
    ownership.mark_released()
    return dict(restored_sources)


@dataclass
class _PrivateClosureBuilder:
    ownership: _PrivateClosureOwnership

    @property
    def sealed(self) -> bool:
        return self.ownership.state is not _PrivateClosureOwnershipState.BUILDER

    @property
    def owner(self) -> _OwnedTemporaryRoot | None:
        if self.sealed:
            return None
        return self.ownership.owner

    @property
    def objects(self) -> _OwnedDirectoryHandle | None:
        if self.sealed:
            return None
        return self.ownership.objects

    @property
    def pack(self) -> _OwnedDirectoryHandle | None:
        if self.sealed:
            return None
        return self.ownership.pack

    @property
    def prefixes(self) -> dict[str, _OwnedDirectoryHandle]:
        if self.sealed:
            return {}
        return self.ownership.prefixes

    @property
    def loose_entries(self) -> dict[tuple[str, str], _OwnedFileEntry]:
        if self.sealed:
            return {}
        return self.ownership.loose_entries

    @property
    def pack_entries(self) -> dict[str, _OwnedFileEntry]:
        if self.sealed:
            return {}
        return self.ownership.pack_entries

    @property
    def source_nlinks(
        self,
    ) -> dict[tuple[int, str], tuple[tuple[int, ...], int]]:
        if self.sealed:
            return {}
        return self.ownership.source_nlinks

    @classmethod
    def create(cls, owner: _OwnedTemporaryRoot) -> "_PrivateClosureBuilder":
        root_identity = _owned_descriptor_identity(os.fstat(owner.root_fd))
        _require_owned_directory(
            root_identity,
            label="private Git object-store root",
            private=True,
        )
        if root_identity != owner.root_identity:
            raise GitAuthorityError("private Git object-store root changed")
        objects = _mkdir_open_private_at(
            owner.root_fd,
            "objects",
            label="private Git object-store objects directory",
        )
        try:
            pack = _mkdir_open_private_at(
                objects.descriptor,
                "pack",
                label="private Git object-store pack directory",
            )
        except BaseException as primary:
            cleanup: BaseException | None = None
            try:
                _remove_empty_owned_directory_handle(
                    objects,
                    label="private Git object-store objects directory",
                )
            except BaseException as cleanup_exc:
                cleanup = cleanup_exc
            if cleanup is not None:
                raise GitAuthorityAggregateError(primary, cleanup) from primary
            raise
        return cls(
            ownership=_PrivateClosureOwnership(
                state=_PrivateClosureOwnershipState.BUILDER,
                owner=owner,
                objects=objects,
                pack=pack,
                prefixes={},
                loose_entries={},
                pack_entries={},
                source_nlinks={},
            )
        )

    def _require_builder_ownership(self) -> _PrivateClosureOwnership:
        self.ownership.require(_PrivateClosureOwnershipState.BUILDER)
        return self.ownership

    def ensure_loose_prefix(self, prefix: str) -> _OwnedDirectoryHandle:
        if self.sealed:
            raise GitAuthorityError("private Git object-store builder is sealed")
        if self.objects is None:
            raise GitAuthorityError(
                "private Git object-store builder ownership is unavailable"
            )
        child = _require_safe_child_name(
            prefix,
            label="private Git object-store prefix",
        )
        retained = self.prefixes.get(child)
        if retained is not None:
            if retained.closed or not retained.owned:
                raise GitAuthorityError(
                    "private Git object-store prefix ownership changed"
                )
            return retained
        retained = _mkdir_open_private_at(
            self.objects.descriptor,
            child,
            label="private Git object-store prefix",
        )
        self.prefixes[child] = retained
        return retained

    def write_loose_object(
        self,
        *,
        prefix: str,
        name: str,
        compressed: bytes,
    ) -> _OwnedFileEntry:
        if self.sealed:
            raise GitAuthorityError("private Git object-store builder is sealed")
        child = _require_safe_child_name(
            name,
            label="private Git object-store loose object",
        )
        key = (prefix, child)
        if key in self.loose_entries:
            raise GitAuthorityError("private Git object-store object is duplicated")
        directory = self.ensure_loose_prefix(prefix)
        entry = _write_private_file_at(
            directory.descriptor,
            child,
            compressed,
            label="private Git object-store loose object",
        )
        self.loose_entries[key] = entry
        return entry

    def link_pack_entry(
        self,
        *,
        source_directory_fd: int,
        source_name: str,
        destination_name: str,
        expected_source_identity: tuple[int, ...],
    ) -> _OwnedFileEntry:
        if self.sealed:
            raise GitAuthorityError("private Git object-store builder is sealed")
        if self.pack is None:
            raise GitAuthorityError(
                "private Git object-store builder ownership is unavailable"
            )
        destination_child = _require_safe_child_name(
            destination_name,
            label="private Git object-store pack entry",
        )
        if destination_child in self.pack_entries:
            raise GitAuthorityError("private Git object-store pack entry is duplicated")
        entry = _link_private_file_at(
            source_fd=source_directory_fd,
            source_name=source_name,
            destination_fd=self.pack.descriptor,
            destination_name=destination_child,
            expected_source_identity=expected_source_identity,
            label="private Git object-store pack entry",
        )
        self.pack_entries[destination_child] = entry
        self.source_nlinks[(source_directory_fd, source_name)] = (
            expected_source_identity,
            expected_source_identity[3],
        )
        return entry

    def confirm_source_nlinks_restored(
        self,
    ) -> dict[tuple[int, str], tuple[int, ...]]:
        restored_sources: dict[tuple[int, str], tuple[int, ...]] = {}
        for (source_fd, source_name), (identity, expected_nlink) in sorted(
            self.source_nlinks.items(),
            key=lambda item: (item[0][1], item[0][0]),
        ):
            current = _store_identity(
                os.stat(
                    source_name,
                    dir_fd=source_fd,
                    follow_symlinks=False,
                )
            )
            if (
                current[:3] + current[4:6] != identity[:3] + identity[4:6]
                or current[3] != expected_nlink
            ):
                raise GitAuthorityError(
                    "private Git object-store source nlink was not restored"
                )
            restored_sources[(source_fd, source_name)] = current
        return restored_sources

    def seal(
        self,
        *,
        expected_inventory: _ClosureCapture,
        limits: GitScanLimits,
        deadline: float,
    ) -> _PrivateClosureStore:
        ownership = self._require_builder_ownership()
        store: _PrivateClosureStore | None = None
        try:
            constructor_metadata = self._prepare_private_closure_transfer(
                expected_inventory=expected_inventory,
                limits=limits,
                deadline=deadline,
            )
            store = _PrivateClosureStore(
                ownership=ownership,
                **constructor_metadata,
            )
            self._verify_constructed_store(
                store,
                ownership,
                limits=limits,
                deadline=deadline,
            )
            _commit_private_closure_transfer(self, store)
            return store
        except BaseException as primary:
            try:
                if ownership.state is _PrivateClosureOwnershipState.BUILDER:
                    self.abort()
                elif ownership.state is _PrivateClosureOwnershipState.STORE:
                    if store is None:
                        raise GitAuthorityError(
                            "store ownership exists without store object"
                        )
                    store.close()
                elif ownership.state is not _PrivateClosureOwnershipState.RELEASED:
                    raise GitAuthorityError(
                        "private closure ownership state is invalid"
                    )
            except BaseException as cleanup:
                raise GitAuthorityAggregateError(primary, cleanup) from primary
            raise

    def _prepare_private_closure_transfer(
        self,
        *,
        expected_inventory: _ClosureCapture,
        limits: GitScanLimits,
        deadline: float,
    ) -> dict[str, object]:
        if self.sealed:
            raise GitAuthorityError("private Git object-store builder is already sealed")
        owner = self.owner
        objects = self.objects
        pack = self.pack
        if owner is None or objects is None or pack is None:
            raise GitAuthorityError(
                "private Git object-store builder ownership is unavailable"
            )
        if not isinstance(expected_inventory, _ClosureCapture):
            raise GitAuthorityError(
                "private Git object-store expected inventory is invalid"
            )

        _seal_deadline(deadline)
        try:
            parent_identity = _owned_descriptor_identity(os.fstat(owner.parent_fd))
            root_owned_identity = _owned_descriptor_identity(os.fstat(owner.root_fd))
        except OSError as exc:
            raise GitAuthorityError(
                "private Git object-store owner descriptor is unavailable"
            ) from exc
        if parent_identity != owner.parent_identity:
            raise GitAuthorityError("private Git object-store parent changed")
        _require_owned_directory(
            root_owned_identity,
            label="private Git object-store root",
            private=True,
        )
        if root_owned_identity != owner.root_identity:
            raise GitAuthorityError("private Git object-store root changed")

        for prefix in sorted(self.prefixes):
            os.fsync(self.prefixes[prefix].descriptor)
        os.fsync(pack.descriptor)
        os.fsync(objects.descriptor)
        os.fsync(owner.root_fd)

        _verify_owned_directory_handle(
            objects,
            label="private Git object-store objects directory",
        )
        _verify_owned_directory_handle(
            pack,
            label="private Git object-store pack directory",
        )
        for prefix, handle in sorted(self.prefixes.items()):
            _verify_owned_directory_handle(
                handle,
                label=f"private Git object-store prefix {prefix}",
            )

        if _directory_inventory_at(
            owner.root_fd,
            label="private Git object-store root",
        ) != ("objects",):
            raise GitAuthorityError("private Git object-store root inventory changed")
        if _directory_inventory_at(
            objects.descriptor,
            label="private Git object-store objects directory",
        ) != tuple(sorted(("pack", *self.prefixes))):
            raise GitAuthorityError(
                "private Git object-store objects inventory changed"
            )
        if _directory_inventory_at(
            pack.descriptor,
            label="private Git object-store pack directory",
        ) != tuple(sorted(self.pack_entries)):
            raise GitAuthorityError("private Git object-store pack inventory changed")

        expected_loose = {
            (entry.prefix, entry.name): entry
            for entry in expected_inventory.objects
        }
        if set(self.loose_entries) != set(expected_loose):
            raise GitAuthorityError("private Git object-store loose inventory changed")
        expected_pack_names = {
            entry.name
            for source in expected_inventory.pack_sources
            for entry in source.entries
        }
        if set(self.pack_entries) != expected_pack_names:
            raise GitAuthorityError("private Git object-store pack selection changed")

        closure_entries: list[_PrivateClosureEntry] = []
        for key, captured in sorted(expected_loose.items()):
            prefix, name = key
            handle = self.prefixes.get(prefix)
            owned_entry = self.loose_entries.get(key)
            if handle is None or owned_entry is None:
                raise GitAuthorityError(
                    "private Git object-store loose receipt is unavailable"
                )
            expected_names = tuple(
                sorted(
                    entry_name
                    for entry_prefix, entry_name in self.loose_entries
                    if entry_prefix == prefix
                )
            )
            if _directory_inventory_at(
                handle.descriptor,
                label="private Git object-store prefix",
            ) != expected_names:
                raise GitAuthorityError(
                    "private Git object-store prefix inventory changed"
                )
            store_identity = _verify_owned_file_entry_at(
                handle.descriptor,
                owned_entry,
                label="private Git copied object",
                deadline=deadline,
            )
            if (
                owned_entry.name != name
                or owned_entry.sha256
                != hashlib.sha256(captured.compressed).hexdigest()
                or owned_entry.size != len(captured.compressed)
            ):
                raise GitAuthorityError(
                    "private Git copied object receipt changed"
                )
            object_type, payload = _decode_loose_object(
                captured.compressed,
                captured.oid,
                _object_format_for_oid(captured.oid),
                limits,
                deadline,
            )
            closure_entries.append(
                _PrivateClosureEntry(
                    oid=captured.oid,
                    object_type=object_type,
                    prefix=prefix,
                    name=name,
                    identity=store_identity,
                    compressed_sha256=owned_entry.sha256,
                    payload_sha256=hashlib.sha256(payload).hexdigest(),
                )
            )

        for name, entry in sorted(self.pack_entries.items()):
            _verify_owned_file_entry_at(
                pack.descriptor,
                entry,
                label="private Git copied pack entry",
                deadline=deadline,
            )

        active_source_receipts: dict[tuple[int, str], tuple[int, ...]] = {}
        for key, (baseline, expected_nlink) in sorted(
            self.source_nlinks.items(),
            key=lambda item: (item[0][1], item[0][0]),
        ):
            source_fd, source_name = key
            _seal_deadline(deadline)
            current = _store_identity(
                os.stat(
                    source_name,
                    dir_fd=source_fd,
                    follow_symlinks=False,
                )
            )
            if (
                current[:3] + current[4:6]
                != baseline[:3] + baseline[4:6]
                or expected_nlink != baseline[3]
                or current[3] != expected_nlink + 1
            ):
                raise GitAuthorityError(
                    "private Git object-store source hardlink receipt changed"
                )
            destination = self.pack_entries.get(source_name)
            if destination is None or destination.identity[:3] != current[:3]:
                raise GitAuthorityError(
                    "private Git object-store pack hardlink identity changed"
                )
            active_source_receipts[key] = current

        root_identity = _private_directory_identity(
            os.fstat(owner.root_fd),
            expected_mode=owner.root_identity.mode,
        )
        prefix_receipts = {
            prefix: (handle.descriptor, handle.identity)
            for prefix, handle in sorted(self.prefixes.items())
        }
        object_authority = _ObjectDirectoryAuthority(
            descriptor=objects.descriptor,
            identity=objects.identity,
            child_path=f"/proc/self/fd/{objects.descriptor}",
            pass_fds=(objects.descriptor,),
        )

        ownership = self._require_builder_ownership()
        ownership.root_identity = root_identity
        ownership.inventory = tuple(closure_entries)
        ownership.active_source_receipts = active_source_receipts
        ownership.object_authority = object_authority

        return {
            "root_fd": owner.root_fd,
            "root_identity": root_identity,
            "objects_fd": objects.descriptor,
            "objects_identity": objects.identity,
            "pack_fd": pack.descriptor,
            "pack_identity": pack.identity,
            "prefixes": prefix_receipts,
            "entries": ownership.inventory,
            "pack_entries": ownership.pack_entries,
            "source_nlinks": ownership.source_nlinks,
            "active_source_receipts": ownership.active_source_receipts,
            "object_authority": object_authority,
        }

    def _verify_constructed_store(
        self,
        store: _PrivateClosureStore,
        ownership: _PrivateClosureOwnership,
        *,
        limits: GitScanLimits,
        deadline: float,
    ) -> None:
        ownership.require(_PrivateClosureOwnershipState.BUILDER)
        if store.ownership is not ownership:
            raise GitAuthorityError(
                "private closure ownership transfer binding is invalid"
            )
        _verify_private_closure(store, limits, deadline)

    def abort(self) -> dict[tuple[int, str], tuple[int, ...]]:
        state = self.ownership.state
        if state in (
            _PrivateClosureOwnershipState.STORE,
            _PrivateClosureOwnershipState.RELEASED,
        ):
            return {}
        self.ownership.require(_PrivateClosureOwnershipState.BUILDER)
        return _cleanup_private_closure_ownership(
            self.ownership,
            confirm_source_nlinks_restored=self.confirm_source_nlinks_restored,
        )


def _commit_private_closure_transfer(
    builder: _PrivateClosureBuilder,
    store: _PrivateClosureStore,
) -> None:
    ownership = builder.ownership
    ownership.require(_PrivateClosureOwnershipState.BUILDER)
    if store.ownership is not ownership:
        raise GitAuthorityError(
            "private closure ownership transfer binding is invalid"
        )
    ownership.transfer_to_store()


@dataclass(frozen=True)
class _PackEntry:
    name: str
    identity: tuple[int, int, int, int, int, int, int]


@dataclass
class _PackBootstrap:
    owner: _OwnedTemporaryRoot
    destination_fd: int
    destination_identity: _OwnedDescriptorIdentity
    destination_pack_fd: int
    destination_pack_identity: _OwnedDescriptorIdentity
    source_fd: int
    source_identity: _OwnedDescriptorIdentity
    entries: tuple[_PackEntry, ...]
    source_nlinks: tuple[tuple[str, int], ...]
    closed: bool = False
    _cleanup_attempts: int = field(default=0, init=False, repr=False)
    _operation_root_unlinked: bool = field(default=False, init=False, repr=False)
    _source_links_restored: bool = field(default=False, init=False, repr=False)
    _closed_descriptors: set[int] = field(default_factory=set, init=False, repr=False)
    _descriptor_errors: dict[int, BaseException] = field(
        default_factory=dict, init=False, repr=False
    )
    _terminal_cleanup_error: BaseException | None = field(
        default=None, init=False, repr=False
    )
    _construction_incomplete: bool = field(default=False, init=False, repr=False)

    @property
    def root(self) -> Path:
        return self.owner.path_hint

    @property
    def destination(self) -> Path:
        return self.owner.path_hint / "objects"

    @property
    def root_fd(self) -> int:
        return self.owner.root_fd

    @property
    def root_identity(self) -> _OwnedDescriptorIdentity:
        return self.owner.root_identity

    @property
    def object_authority(self) -> _ObjectDirectoryAuthority:
        identity = _PrivateDirectoryIdentity(
            device=self.destination_identity.device,
            inode=self.destination_identity.inode,
            file_type=self.destination_identity.file_type,
            uid=self.destination_identity.uid,
            gid=self.destination_identity.gid,
            mode=self.destination_identity.mode,
        )
        return _ObjectDirectoryAuthority(
            descriptor=self.destination_fd,
            identity=identity,
            child_path=f"/proc/self/fd/{self.destination_fd}",
            pass_fds=(self.destination_fd,),
        )

    def cleanup_receipt(self) -> _CleanupReceipt:
        return _CleanupReceipt(
            attempts=self._cleanup_attempts,
            operation_root_unlinked=self._operation_root_unlinked,
            source_links_restored=self._source_links_restored,
            descriptors_closed=(
                len(self._closed_descriptors) == 4 and not self._descriptor_errors
            ),
        )

    def close(self) -> _CleanupReceipt:
        if self.closed:
            return self.cleanup_receipt()
        if self._terminal_cleanup_error is not None:
            raise self._terminal_cleanup_error
        self._cleanup_attempts += 1
        errors: list[BaseException] = []
        root_name_unconfirmed = False
        try:
            _verify_private_pack_bootstrap(self)
            for entry in self.entries:
                metadata = os.stat(
                    entry.name,
                    dir_fd=self.destination_pack_fd,
                    follow_symlinks=False,
                )
                actual_entry_identity = _store_identity(metadata)
                if (
                    actual_entry_identity[:3] != entry.identity[:3]
                    or (
                        not self._construction_incomplete
                        and actual_entry_identity != entry.identity
                    )
                ):
                    raise GitAuthorityError(
                        "private Git pack bootstrap entry changed during cleanup"
                    )
                os.unlink(entry.name, dir_fd=self.destination_pack_fd)
            os.fsync(self.destination_pack_fd)

            self._source_links_restored = True
            for entry, (name, expected_nlink) in zip(
                self.entries, self.source_nlinks, strict=True
            ):
                metadata = os.stat(
                    name,
                    dir_fd=self.source_fd,
                    follow_symlinks=False,
                )
                if (
                    (
                        metadata.st_dev,
                        metadata.st_ino,
                        stat.S_IFMT(metadata.st_mode),
                    )
                    != entry.identity[:3]
                    or metadata.st_nlink != expected_nlink
                ):
                    self._source_links_restored = False
                    raise GitAuthorityError(
                        "pack bootstrap source hardlink cleanup was not confirmed"
                    )

            os.rmdir("pack", dir_fd=self.destination_fd)
            os.rmdir("objects", dir_fd=self.owner.root_fd)
            try:
                named_root_identity = _owned_descriptor_identity(
                    os.stat(
                        self.owner.basename,
                        dir_fd=self.owner.parent_fd,
                        follow_symlinks=False,
                    )
                )
            except FileNotFoundError:
                root_name_unconfirmed = True
            except OSError:
                root_name_unconfirmed = True
            else:
                if named_root_identity != self.owner.root_identity:
                    root_name_unconfirmed = True
                elif not _owned_directory_is_empty(self.owner.root_fd):
                    root_name_unconfirmed = True
                else:
                    os.rmdir(
                        self.owner.basename,
                        dir_fd=self.owner.parent_fd,
                    )
                    os.fsync(self.owner.parent_fd)
                    self._operation_root_unlinked = True
        except BaseException as exc:
            if isinstance(exc, GitAuthorityError):
                errors.append(exc)
            else:
                cleanup_error = GitAuthorityError(
                    "pack bootstrap descriptor-relative cleanup failed"
                )
                cleanup_error.__cause__ = exc
                errors.append(cleanup_error)

        retained = (
            (
                self.destination_pack_fd,
                self.destination_pack_identity,
                "pack bootstrap pack",
            ),
            (
                self.destination_fd,
                self.destination_identity,
                "pack bootstrap objects",
            ),
            (
                self.owner.root_fd,
                self.owner.root_identity,
                "pack bootstrap root",
            ),
            (
                self.owner.parent_fd,
                self.owner.parent_identity,
                "pack bootstrap parent",
            ),
        )
        for descriptor, identity, label in retained:
            if descriptor in self._closed_descriptors:
                continue
            previous_error = self._descriptor_errors.get(descriptor)
            if previous_error is not None:
                errors.append(previous_error)
                continue
            try:
                _close_owned_descriptor(
                    descriptor,
                    identity,
                    label=label,
                )
            except BaseException as exc:
                self._descriptor_errors[descriptor] = exc
                errors.append(exc)
            else:
                self._closed_descriptors.add(descriptor)
        if root_name_unconfirmed:
            errors.append(
                GitAuthorityError(
                    "pack bootstrap root-name cleanup was not confirmed"
                )
            )
        if errors:
            aggregate = _aggregate_errors(errors)
            assert aggregate is not None
            self._terminal_cleanup_error = aggregate
            raise aggregate
        self.closed = True
        return self.cleanup_receipt()


@dataclass
class _PersistentReaderAccounting:
    """Snapshot-owned limits which a replacement batch reader cannot reset."""

    deadline: float | None = None
    decoded_bytes: int = 0
    compressed_bytes: int = 0
    header_bytes: int = 0
    request_count: int = 0
    object_count: int = 0
    stderr_bytes: int = 0
    cpu_budget_seconds: float | None = None
    cpu_used_seconds: float = 0.0
    cpu_receipt_count: int = 0


class _BootstrapBatchReader:
    """One unbuffered cat-file child bound to one immutable pack bootstrap."""

    _HEADER_CAP = 512

    def __init__(self, runner: "_GitRunner", bootstrap: _PackBootstrap, deadline: float) -> None:
        self.runner = runner
        self.bootstrap = bootstrap
        self.deadline = deadline
        self.process: subprocess.Popen[bytes] | None = None
        self.termination: _ProcessTermination | None = None
        self.guard: _DirectoryMutationGuard | None = None
        self.closed = False
        self.poisoned = False
        self.in_flight = False
        self.requests = 0
        self.protocol_bytes = 0
        self._cpu_receipted = False
        self._launch()

    def _assert_authority(self) -> None:
        _seal_deadline(self._shared_deadline())
        self.runner._assert_frozen_pack_namespace(self._shared_deadline())
        _regular_inode_matches(self.runner.executable, self.runner._executable_inode, "Git executable")
        _verify_pack_bootstrap(self.bootstrap, self.runner.limits, self.deadline)
        _verify_private_pack_bootstrap(self.bootstrap)
        if self.guard is None:
            raise GitAuthorityError("persistent Git reader guard is unavailable")
        self.guard.assert_quiet()

    def _accounting(self) -> _PersistentReaderAccounting:
        accounting = getattr(self.runner, "_persistent_accounting", None)
        if accounting is None:
            accounting = _PersistentReaderAccounting(deadline=self.deadline)
            self.runner._persistent_accounting = accounting
        if accounting.deadline is None:
            accounting.deadline = self.deadline
        if accounting.deadline != self.deadline:
            raise GitAuthorityError("persistent Git reader deadline changed")
        return accounting

    def _shared_deadline(self) -> float:
        deadline = self._accounting().deadline
        assert deadline is not None
        return deadline

    def _remaining_cpu_seconds(self) -> float:
        accounting = self._accounting()
        if accounting.cpu_budget_seconds is None:
            envelope = _derive_child_resource_envelope(self.runner.limits)
            accounting.cpu_budget_seconds = float(envelope.cpu_seconds[0])
        remaining = accounting.cpu_budget_seconds - accounting.cpu_used_seconds
        if not math.isfinite(remaining) or remaining <= 0:
            raise GitAuthorityError("persistent Git reader snapshot CPU budget exceeded")
        return remaining

    def _record_cpu_receipt(self, usage: object) -> None:
        if self._cpu_receipted:
            raise GitAuthorityError("persistent Git reader CPU receipt was duplicated")
        try:
            used = float(usage.ru_utime) + float(usage.ru_stime)
        except (AttributeError, TypeError, ValueError) as exc:
            raise GitAuthorityError("persistent Git reader CPU receipt is unavailable") from exc
        if not math.isfinite(used) or used < 0:
            raise GitAuthorityError("persistent Git reader CPU receipt is inconsistent")
        accounting = self._accounting()
        budget = accounting.cpu_budget_seconds
        if budget is None:
            raise GitAuthorityError("persistent Git reader CPU budget is unavailable")
        if accounting.cpu_used_seconds + used > budget:
            raise GitAuthorityError("persistent Git reader snapshot CPU budget exceeded")
        accounting.cpu_used_seconds += used
        accounting.cpu_receipt_count += 1
        self._cpu_receipted = True

    def _launch(self) -> None:
        _seal_deadline(self.deadline)
        authority = self.bootstrap.object_authority
        directories = self.runner._verify_private_child_authority(authority)
        guard = _DirectoryMutationGuard.arm(directories)
        self.guard = guard
        try:
            self._assert_authority()
            environment = _git_env()
            environment.update(_bounded_git_resource_environment(self.runner.limits))
            environment.update({
                "GIT_OBJECT_DIRECTORY": authority.child_path,
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": "",
                "GIT_TERMINAL_PROMPT": "0",
            })
            envelope = _derive_child_resource_envelope(self.runner.limits)
            remaining_cpu = self._remaining_cpu_seconds()
            cpu_seconds = min(envelope.cpu_seconds[0], math.floor(remaining_cpu))
            if cpu_seconds < 1:
                raise GitAuthorityError("persistent Git reader snapshot CPU budget is exhausted")
            envelope = _ChildResourceEnvelope(
                address_space=envelope.address_space,
                cpu_seconds=(cpu_seconds, cpu_seconds),
            )
            process = subprocess.Popen(
                (str(self.runner.executable), "--no-replace-objects", "cat-file", "--batch"),
                cwd=self.runner.repo_root,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                preexec_fn=_bounded_git_preexec(envelope),
                pass_fds=authority.pass_fds,
            )
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise GitAuthorityError("persistent Git reader pipes are unavailable")
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stderr.fileno(), False)
            self.process = process
            self.termination = _ProcessTermination(
                process=process,
                pgid=process.pid,
                on_reap=self._record_cpu_receipt,
            )
            self._assert_authority()
        except BaseException as exc:
            self.poisoned = True
            cleanup = self.close(suppress_primary=True)
            primary = exc if isinstance(exc, GitAuthorityError) else GitAuthorityError("persistent Git reader launch failed")
            if cleanup is not None:
                raise GitAuthorityAggregateError(primary, cleanup) from exc
            raise primary from exc

    def _read(self, size: int) -> bytes:
        if size < 0:
            raise GitAuthorityError("persistent Git reader size is invalid")
        process = self.process
        if process is None or process.stdout is None or process.stderr is None:
            raise GitAuthorityError("persistent Git reader is unavailable")
        result = bytearray()
        selector = selectors.DefaultSelector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while len(result) < size:
                self._assert_authority()
                remaining = self._shared_deadline() - time.monotonic()
                if remaining <= 0:
                    raise GitAuthorityError("Git object-store seal deadline exceeded")
                events = selector.select(remaining)
                if not events:
                    continue
                for key, _event in events:
                    stream = key.data
                    chunk = os.read(key.fileobj.fileno(), min(65_536, size - len(result)) if stream == "stdout" else _STDERR_CAP + 1)
                    if stream == "stderr":
                        accounting = self._accounting()
                        accounting.stderr_bytes += len(chunk)
                        if accounting.stderr_bytes > self.runner.limits.max_total_bytes:
                            raise GitAuthorityError("persistent Git reader stderr budget exceeded")
                        if chunk:
                            raise GitAuthorityError("persistent Git reader wrote stderr")
                        selector.unregister(key.fileobj)
                        continue
                    if not chunk:
                        raise GitAuthorityError("persistent Git reader stdout ended early")
                    result.extend(chunk)
            return bytes(result)
        finally:
            selector.close()

    def _read_header(self) -> bytes:
        header = bytearray()
        while True:
            if len(header) >= self._HEADER_CAP:
                raise GitAuthorityError("persistent Git reader header exceeds cap")
            character = self._read(1)
            header.extend(character)
            if character == b"\n":
                return bytes(header[:-1])

    def _write_request(self, request: bytes) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise GitAuthorityError("persistent Git reader stdin is unavailable")
        offset = 0
        descriptor = process.stdin.fileno()
        while offset < len(request):
            self._assert_authority()
            remaining = self._shared_deadline() - time.monotonic()
            if remaining <= 0:
                raise GitAuthorityError("Git object-store seal deadline exceeded")
            _readable, writable, _exceptional = select.select((), (descriptor,), (), remaining)
            if not writable:
                continue
            try:
                written = os.write(descriptor, request[offset:])
            except OSError as exc:
                raise GitAuthorityError("persistent Git reader request write failed") from exc
            if written <= 0:
                raise GitAuthorityError("persistent Git reader request write was short")
            offset += written

    def read_object(self, oid: str, expected_type: str, object_format: str) -> tuple[str, bytes]:
        if self.closed or self.poisoned or self.in_flight:
            raise GitAuthorityError("persistent Git reader is not available for one in-flight request")
        _require_full_oid(oid, object_format, "packed object")
        if expected_type not in {"commit", "tree", "blob"}:
            raise GitAuthorityError("persistent Git reader object type is invalid")
        self.in_flight = True
        primary: BaseException | None = None
        try:
            self._assert_authority()
            accounting = self._accounting()
            if accounting.request_count >= self.runner.limits.max_entries:
                raise GitAuthorityError("persistent Git reader request budget exceeded")
            accounting.request_count += 1
            self._write_request(oid.encode("ascii") + b"\n")
            header = self._read_header()
            header_size = len(header) + 1
            self.protocol_bytes += header_size
            accounting.header_bytes += header_size
            if accounting.header_bytes > self.runner.limits.max_total_bytes:
                raise GitAuthorityError("persistent Git reader protocol budget exceeded")
            try:
                actual_raw, type_raw, size_raw = header.split(b" ")
                actual = actual_raw.decode("ascii")
                object_type = type_raw.decode("ascii")
                size_text = size_raw.decode("ascii")
            except (UnicodeDecodeError, ValueError) as exc:
                raise GitAuthorityError("malformed persistent Git reader header") from exc
            if not size_text.isdecimal() or (len(size_text) > 1 and size_text.startswith("0")):
                raise GitAuthorityError("persistent Git reader size is noncanonical")
            size = int(size_text)
            if actual != oid or object_type != expected_type:
                raise GitAuthorityError("persistent Git reader OID/type does not match request")
            if (
                (expected_type == "blob" and size > self.runner.limits.max_blob_bytes)
                or size > self.runner.limits.max_total_bytes
                or accounting.decoded_bytes + size > self.runner.limits.max_total_bytes
            ):
                raise GitAuthorityError("persistent Git reader object exceeds limit")
            payload = self._read(size)
            if self._read(1) != b"\n":
                raise GitAuthorityError("persistent Git reader body delimiter is missing")
            raw = f"{object_type} {size}\0".encode("ascii") + payload
            if hashlib.new(object_format, raw).hexdigest() != oid:
                raise GitAuthorityError("persistent Git reader object bytes do not reproduce OID")
            self._assert_authority()
            key = (oid, object_type)
            receipt = hashlib.sha256(payload).hexdigest()
            previous = self.runner._returned_object_sha256.get(key)
            if previous is not None and previous != receipt:
                raise GitAuthorityError("persistent Git reader SHA-256 changed between reads")
            self.runner._returned_object_sha256[key] = receipt
            if accounting.object_count >= self.runner.limits.max_entries:
                raise GitAuthorityError("persistent Git reader object budget exceeded")
            accounting.decoded_bytes += size
            accounting.object_count += 1
            self.requests += 1
            return object_type, payload
        except BaseException as exc:
            primary = exc
            self.poisoned = True
            raise
        finally:
            self.in_flight = False
            if primary is not None:
                cleanup = self.close(suppress_primary=True)
                if cleanup is not None:
                    raise GitAuthorityAggregateError(primary, cleanup) from primary

    def _drain_close_streams(self, process: subprocess.Popen[bytes]) -> BaseException | None:
        """Consume both protocol pipes to EOF before accepting normal shutdown."""
        if process.stdout is None or process.stderr is None:
            return GitAuthorityError("persistent Git reader pipes are unavailable during cleanup")
        selector: selectors.BaseSelector | None = None
        stdout_bytes = 0
        stderr_bytes = 0
        errors: list[BaseException] = []
        deadline = min(self._shared_deadline(), time.monotonic() + 1.0)
        try:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    errors.append(GitAuthorityError("persistent Git reader stream drain timed out"))
                    break
                events = selector.select(remaining)
                if not events:
                    continue
                for key, _event in events:
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65_536)
                    except OSError as exc:
                        error = GitAuthorityError("persistent Git reader stream drain failed")
                        error.__cause__ = exc
                        errors.append(error)
                        selector.unregister(key.fileobj)
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        stdout_bytes += len(chunk)
                    else:
                        stderr_bytes += len(chunk)
                    if stdout_bytes + stderr_bytes > self.runner.limits.max_total_bytes:
                        errors.append(GitAuthorityError("persistent Git reader cleanup stream budget exceeded"))
                        break
        except BaseException as exc:
            errors.append(exc)
        finally:
            if selector is not None:
                try:
                    selector.close()
                except BaseException as exc:
                    errors.append(exc)
        if stdout_bytes:
            errors.append(GitAuthorityError("persistent Git reader wrote trailing stdout during cleanup"))
        if stderr_bytes:
            errors.append(GitAuthorityError("persistent Git reader wrote stderr during cleanup"))
        return _aggregate_errors(errors)

    def close(
        self, *, suppress_primary: bool = False, force_terminate: bool = False
    ) -> BaseException | None:
        if self.closed:
            return None
        self.closed = True
        errors: list[BaseException] = []
        process = self.process
        if process is not None:
            try:
                self._assert_authority()
            except BaseException as exc:
                errors.append(exc)
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError as exc:
                    errors.append(GitAuthorityError("persistent Git reader stdin cleanup failed"))
            if self.poisoned:
                if self.termination is not None:
                    error = self.termination.terminate()
                    if error is not None:
                        errors.append(error)
            elif force_terminate:
                drain_error = self._drain_close_streams(process)
                if drain_error is not None:
                    errors.append(drain_error)
                if self.termination is not None:
                    error = self.termination.terminate()
                    if error is not None:
                        errors.append(error)
            else:
                termination_attempted = False
                drain_error = self._drain_close_streams(process)
                if drain_error is not None:
                    errors.append(drain_error)
                    if self.termination is not None:
                        termination_attempted = True
                        cleanup = self.termination.terminate()
                        if cleanup is not None:
                            errors.append(cleanup)
                if isinstance(self.termination, _ProcessTermination):
                    exit_state = self.termination._observe_leader_exit()
                    if isinstance(exit_state, GitAuthorityError):
                        errors.append(exit_state)
                        if not termination_attempted:
                            termination_attempted = True
                            cleanup = self.termination.terminate()
                            if cleanup is not None:
                                errors.append(cleanup)
                    elif exit_state:
                        errors.append(GitAuthorityError("persistent Git reader exited nonzero"))
                        if not termination_attempted:
                            termination_attempted = True
                            cleanup = self.termination.terminate()
                            if cleanup is not None:
                                errors.append(cleanup)
                if self.termination is None or not self.termination.leader_reaped:
                    try:
                        if isinstance(self.termination, _ProcessTermination):
                            reap_error = self.termination._reap_leader()
                            if reap_error is not None:
                                errors.append(reap_error)
                                if not termination_attempted:
                                    termination_attempted = True
                                    cleanup = self.termination.terminate()
                                    if cleanup is not None:
                                        errors.append(cleanup)
                        else:
                            process.wait(timeout=1.0)
                            if process.returncode != 0:
                                errors.append(GitAuthorityError("persistent Git reader exited nonzero"))
                                if self.termination is not None and not termination_attempted:
                                    cleanup = self.termination.terminate()
                                    if cleanup is not None:
                                        errors.append(cleanup)
                            elif self.termination is not None:
                                self.termination.leader_reaped = True
                    except (OSError, subprocess.TimeoutExpired):
                        if self.termination is not None:
                            error = self.termination.terminate()
                            if error is not None:
                                errors.append(error)
            if self.termination is not None and self.termination.leader_reaped:
                try:
                    self._assert_authority()
                except BaseException as exc:
                    errors.append(exc)
            stream_error = _close_process_streams(process)
            if stream_error is not None:
                errors.append(stream_error)
        if self.guard is not None:
            try:
                self.guard.close()
            except BaseException as exc:
                errors.append(exc)
        return _aggregate_errors(errors)


@dataclass
class _PackNamespace:
    source: Path
    fd: int | None
    identity: tuple[int, int, int] | None
    entries: dict[str, _PackEntry]
    sentinel: str | None

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError as exc:
                raise GitAuthorityError("Git pack namespace descriptor cleanup failed") from exc
            self.fd = None


def _verify_frozen_pack_namespace(
    namespace: _PackNamespace,
    deadline: float,
) -> None:
    """Reject any post-freeze entry or receipt drift before a reader request."""
    _seal_deadline(deadline)
    if namespace.sentinel is not None:
        raise GitAuthorityError(namespace.sentinel)
    if namespace.fd is None or namespace.identity is None:
        raise GitAuthorityError("Git pack namespace is unavailable")
    try:
        actual_directory = _directory_identity(os.fstat(namespace.fd))
    except OSError as exc:
        raise GitAuthorityError("Git pack namespace changed during source snapshot") from exc
    # Names absent at freeze are never selected.  Their later presence is not
    # authority drift; the immutable selected-entry receipts below remain the
    # complete authority boundary.
    if actual_directory != namespace.identity:
        raise GitAuthorityError("Git pack namespace changed during source snapshot")
    for name, entry in namespace.entries.items():
        try:
            actual = _store_identity(
                os.stat(name, dir_fd=namespace.fd, follow_symlinks=False)
            )
        except OSError as exc:
            raise GitAuthorityError("Git pack namespace changed during source snapshot") from exc
        # The owned bootstrap link/unlink transition necessarily updates ctime.
        # Namespace authority keeps device/inode/type/nlink/size/mtime exact.
        if actual[:6] != entry.identity[:6]:
            raise GitAuthorityError("Git pack namespace changed during source snapshot")


def _advance_namespace_owned_hardlink(
    namespace: _PackNamespace,
    name: str,
    linked_identity: tuple[int, int, int, int, int, int, int],
) -> None:
    """Record exactly the metadata transition caused by our successful hardlink.

    `st_nlink` and `st_ctime_ns` necessarily change when a source pack entry is
    hardlinked into the private bootstrap.  All other source identity fields
    remain immutable, and later reads compare against this post-link receipt.
    """
    prior = namespace.entries.get(name)
    if prior is None or len(linked_identity) != 7:
        raise GitAuthorityError("Git pack namespace owned hardlink receipt is invalid")
    expected = prior.identity
    if (
        linked_identity[:3] + linked_identity[4:6] != expected[:3] + expected[4:6]
        or linked_identity[3] != expected[3] + 1
    ):
        raise GitAuthorityError("Git pack namespace changed outside owned hardlink")
    namespace.entries[name] = _PackEntry(name, linked_identity)


@dataclass(frozen=True)
class GitScanLimits:
    max_entries: int = 20_000
    max_path_bytes: int = 4_096
    max_blob_bytes: int = 2_000_000
    max_total_bytes: int = 100_000_000
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class _ChildResourceEnvelope:
    address_space: tuple[int, int]
    cpu_seconds: tuple[int, int]


@dataclass(frozen=True)
class GitBlobSnapshot:
    path: str
    mode: int
    blob_oid: str
    sha256: str
    data: bytes


@dataclass(frozen=True)
class GitTreeSnapshot:
    commit_oid: str | None
    tree_oid: str
    object_format: str
    blobs: tuple[GitBlobSnapshot, ...]

    @classmethod
    def from_commit(cls, repo_root: Path, commit_oid: str, *, limits: GitScanLimits = GitScanLimits()) -> "GitTreeSnapshot":
        runner = _GitRunner(repo_root, limits)
        try:
            object_format = runner.object_format()
            _require_full_oid(commit_oid, object_format, "commit")
            runner.seal_object_store(commit_oid, "commit", object_format)
            _require_object_type(runner, commit_oid, "commit")
            tree_oid = _one_oid(runner.run(("rev-parse", f"{commit_oid}^{{tree}}")), object_format, "tree")
            snapshot = cls._from_exact_tree(runner, commit_oid, tree_oid, object_format)
        except BaseException as primary:
            try:
                runner.close()
            except BaseException as cleanup:
                combined = _combine_primary_and_cleanup(primary, cleanup)
                if combined is primary:
                    raise primary
                raise combined from primary
            raise
        else:
            runner.close()
            return snapshot

    @classmethod
    def from_tree(cls, repo_root: Path, tree_oid: str, *, limits: GitScanLimits = GitScanLimits()) -> "GitTreeSnapshot":
        runner = _GitRunner(repo_root, limits)
        try:
            object_format = runner.object_format()
            _require_full_oid(tree_oid, object_format, "tree")
            runner.seal_object_store(tree_oid, "tree", object_format)
            _require_object_type(runner, tree_oid, "tree")
            snapshot = cls._from_exact_tree(runner, None, tree_oid, object_format)
        except BaseException as primary:
            try:
                runner.close()
            except BaseException as cleanup:
                combined = _combine_primary_and_cleanup(primary, cleanup)
                if combined is primary:
                    raise primary
                raise combined from primary
            raise
        else:
            runner.close()
            return snapshot

    @classmethod
    def _from_exact_tree(cls, runner: "_GitRunner", commit_oid: str | None, tree_oid: str, object_format: str) -> "GitTreeSnapshot":
        records = _parse_tree_records(runner.run(("ls-tree", "-r", "-z", "--full-tree", tree_oid), stdout_cap=_tree_output_cap(runner.limits)), object_format, runner.limits)
        blobs = _read_verified_blobs(runner, records, object_format)
        runner.verify_sealed_source()
        runner.assert_ambient_authority_absent()
        return cls(commit_oid=commit_oid, tree_oid=tree_oid, object_format=object_format, blobs=blobs)

    def blob(self, path: str) -> GitBlobSnapshot:
        for blob in self.blobs:
            if blob.path == path:
                return blob
        raise GitAuthorityError(f"source path is absent from exact tree: {path!r}")


def _git_env() -> dict[str, str]:
    try:
        path = os.environ["PATH"]
    except KeyError as exc:
        raise GitAuthorityError("Git environment is missing PATH") from exc
    return {"PATH": path, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


class _GitRunner:
    def __init__(self, repo_root: Path, limits: GitScanLimits) -> None:
        _validate_limits(limits)
        _reject_injected_git_environment()
        self.limits = limits
        try:
            self.repo_root = Path(repo_root).resolve(strict=True)
        except OSError as exc:
            raise GitAuthorityError("repository root is absent or inaccessible") from exc
        if not self.repo_root.is_dir():
            raise GitAuthorityError("repository root is not a directory")
        executable = shutil.which("git")
        if executable is None:
            raise GitAuthorityError("Git executable is unavailable")
        try:
            self.executable = Path(executable).resolve(strict=True)
        except OSError as exc:
            raise GitAuthorityError("Git executable is unavailable") from exc
        self._executable_inode = _regular_inode(self.executable, "Git executable")
        self._object_store: _OwnedTemporaryRoot | None = None
        self._private_objects: Path | None = None
        self._private_closure: _PrivateClosureStore | None = None
        self._object_store_cleanup_attempts = 0
        self._object_store_unlinked = False
        self._object_store_descriptors_closed = False
        self._returned_object_sha256: dict[tuple[str, str], str] = {}
        self._closure: _ClosureCapture | None = None
        self._pack_bootstraps: list[_PackBootstrap] = []
        self._persistent_reader: _BootstrapBatchReader | None = None
        self._persistent_accounting = _PersistentReaderAccounting()
        self._persistent_terminal_error: BaseException | None = None
        self._pack_namespace: _PackNamespace | None = None
        self._closed = False
        self._verify_repository_root()
        self.assert_ambient_authority_absent()

    def _verify_repository_root(self) -> None:
        top_level = Path(_one_line(self.run(("rev-parse", "--show-toplevel")), "repository root")).resolve(strict=True)
        if top_level != self.repo_root:
            raise GitAuthorityError("repository root is not Git's exact top-level directory")
        common_text = _one_line(self.run(("rev-parse", "--git-common-dir")), "Git common directory")
        git_dir_text = _one_line(self.run(("rev-parse", "--git-dir")), "Git directory")
        common_dir = _resolve_git_path(self.repo_root, common_text, "Git common directory")
        git_dir = _resolve_git_path(self.repo_root, git_dir_text, "Git directory")
        if not (common_dir / "objects").is_dir() or not (git_dir == common_dir or common_dir in git_dir.parents):
            raise GitAuthorityError("unexpected repository common directory")
        self.common_dir = common_dir

    def assert_ambient_authority_absent(self) -> None:
        if (self.common_dir / "objects/info/alternates").exists():
            raise GitAuthorityError("Git alternates are forbidden source authority")
        if (self.common_dir / "info/grafts").exists():
            raise GitAuthorityError("Git grafts are forbidden source authority")
        if self.run(("replace", "-l")):
            raise GitAuthorityError("Git replace refs are forbidden source authority")

    def seal_object_store(self, root_oid: str, root_type: str, object_format: str) -> None:
        """Snapshot only the requested, descriptor-pinned object closure into a private store."""
        if self._object_store is not None:
            return
        source = self.common_dir / "objects"
        deadline = time.monotonic() + self.limits.timeout_seconds
        self._persistent_accounting = _PersistentReaderAccounting(deadline=deadline)
        self._persistent_terminal_error = None
        self.assert_ambient_authority_absent()
        capture: _ClosureCapture | None = None
        store: _OwnedTemporaryRoot | None = None
        builder: _PrivateClosureBuilder | None = None
        private_closure: _PrivateClosureStore | None = None
        try:
            self._pack_namespace = _freeze_pack_namespace(source, self.limits, deadline)
            packed_reader = lambda oid, expected: self._read_packed_source(source, oid, expected, object_format, deadline)
            capture = _capture_requested_closure(
                source,
                root_oid,
                root_type,
                object_format,
                self.limits,
                deadline,
                packed_reader,
                self._close_persistent_reader,
            )
            pack_sources: list[_ClosurePackSource] = []
            for bootstrap in self._pack_bootstraps:
                _verify_pack_bootstrap(bootstrap, self.limits, deadline)
                pack_sources.append(
                    _ClosurePackSource(
                        directory_fd=bootstrap.source_fd,
                        directory_identity=bootstrap.source_identity,
                        entries=tuple(
                            _ClosurePackEntry(entry.name, entry.identity)
                            for entry in bootstrap.entries
                        ),
                    )
                )
            capture.pack_sources = tuple(pack_sources)
            store = _OwnedTemporaryRoot.create(Path(tempfile.gettempdir()))
            builder = _PrivateClosureBuilder.create(store)
            _copy_requested_closure(capture, builder, self.limits, deadline)
            private_closure = _retain_private_closure(
                builder, capture, self.limits, deadline
            )
            _handoff_active_pack_receipts_after_builder_seal(
                capture,
                self._pack_bootstraps,
                private_closure.active_source_receipts,
            )
            _verify_requested_closure(capture, self.limits, deadline)
            _verify_private_closure(private_closure, self.limits, deadline)
            # Closing the live batch reader is a publication prerequisite, but
            # its immutable bootstrap remains the descriptor-owned custody and
            # hardlink receipt until ordinary runner cleanup.
            for bootstrap in self._pack_bootstraps:
                _verify_pack_bootstrap(bootstrap, self.limits, deadline)
            self.assert_ambient_authority_absent()
        except BaseException as primary:
            cleanup_errors: list[BaseException] = []
            if store is not None:
                if private_closure is None:
                    if builder is not None:
                        restored_sources: dict[
                            tuple[int, str], tuple[int, ...]
                        ] | None = None
                        if builder.ownership.state is _PrivateClosureOwnershipState.RELEASED:
                            restored_sources = dict(
                                builder.ownership.restored_source_receipts
                            )
                        elif builder.ownership.state is _PrivateClosureOwnershipState.BUILDER:
                            try:
                                restored_sources = builder.abort()
                            except BaseException as exc:
                                cleanup_errors.append(exc)
                                try:
                                    restored_sources = (
                                        builder.confirm_source_nlinks_restored()
                                    )
                                except BaseException as restore_exc:
                                    cleanup_errors.append(restore_exc)
                                    try:
                                        restored_sources = (
                                            _collect_restored_pack_source_receipts(
                                                capture
                                            )
                                            if capture is not None
                                            else None
                                        )
                                    except BaseException as fallback_exc:
                                        cleanup_errors.append(fallback_exc)
                                        restored_sources = None
                        elif builder.ownership.state is _PrivateClosureOwnershipState.STORE:
                            if builder.ownership.cleanup_started:
                                if len(
                                    builder.ownership.restored_source_receipts
                                ) == len(builder.ownership.source_nlinks):
                                    restored_sources = dict(
                                        builder.ownership.restored_source_receipts
                                    )
                            else:
                                cleanup_errors.append(
                                    GitAuthorityError(
                                        "store ownership exists without store object"
                                    )
                                )
                        if capture is not None and restored_sources is not None:
                            try:
                                _handoff_restored_pack_receipts_after_builder_abort(
                                    capture,
                                    self._pack_bootstraps,
                                    restored_sources,
                                )
                            except BaseException as exc:
                                cleanup_errors.append(exc)
                    else:
                        try:
                            _cleanup_empty_owned_temporary_root(
                                store,
                                label="private Git object-store",
                            )
                        except BaseException as exc:
                            cleanup_errors.append(exc)
                else:
                    self._object_store = store
                    previous_closure = self._closure
                    self._closure = capture
                    try:
                        self._cleanup_private_object_store(private_closure)
                    except BaseException as exc:
                        cleanup_errors.append(exc)
                    finally:
                        self._closure = previous_closure
                        self._object_store = None
            if private_closure is not None and not private_closure.cleanup_started:
                try:
                    private_closure.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            try:
                self._close_persistent_reader()
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                self._close_pack_bootstraps()
            except BaseException as exc:
                cleanup_errors.append(exc)
            namespace = self._pack_namespace
            if namespace is not None:
                try:
                    namespace.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
                else:
                    self._pack_namespace = None
            if capture is not None:
                try:
                    capture.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            cleanup_error = _aggregate_errors(cleanup_errors)
            if cleanup_error is not None:
                combined = _combine_primary_and_cleanup(primary, cleanup_error)
                if combined is primary:
                    raise primary
                raise combined from primary
            raise
        self._object_store = store
        self._private_objects = (
            store.parent_path_hint / store.basename / "objects"
        )
        self._private_closure = private_closure
        self._closure = capture

    def _read_packed_source(self, source: Path, oid: str, expected_type: str, object_format: str, deadline: float) -> tuple[str, bytes]:
        """Expose exactly one descriptor-pinned pair which indexes this full OID."""
        terminal = self._persistent_terminal_error
        if terminal is not None:
            raise terminal
        try:
            self._assert_frozen_pack_namespace(
                self._persistent_accounting.deadline or deadline
            )
            for bootstrap in self._pack_bootstraps:
                if _pack_index_contains(bootstrap.entries[1].name, bootstrap.source_fd, oid, object_format, self.limits, deadline):
                    _verify_pack_bootstrap(bootstrap, self.limits, deadline)
                    result = self._read_persistent_bootstrap_object(bootstrap, oid, expected_type, object_format, deadline)
                    _verify_pack_bootstrap(bootstrap, self.limits, deadline)
                    return result
            namespace = self._pack_namespace
            if namespace is None:
                raise GitAuthorityError("Git pack namespace was not frozen before source closure")
            bootstrap = _bootstrap_pack_view(namespace, self.common_dir, oid, object_format, self.limits, deadline)
            if bootstrap is None:
                raise GitAuthorityError("requested Git object is absent from the initial primary loose/pack set")
            self._pack_bootstraps.append(bootstrap)
            _verify_pack_bootstrap(bootstrap, self.limits, deadline)
            result = self._read_persistent_bootstrap_object(bootstrap, oid, expected_type, object_format, deadline)
            _verify_pack_bootstrap(bootstrap, self.limits, deadline)
            return result
        except BaseException as exc:
            terminal_error = (
                exc if isinstance(exc, GitAuthorityError)
                else GitAuthorityError("persistent Git reader operation failed")
            )
            self._persistent_terminal_error = terminal_error
            raise terminal_error from exc

    def _assert_frozen_pack_namespace(self, deadline: float) -> None:
        private_closure = self._private_closure
        if private_closure is not None:
            try:
                _verify_private_closure(private_closure, self.limits, deadline)
            except BaseException as exc:
                terminal = (
                    exc if isinstance(exc, GitAuthorityError)
                    else GitAuthorityError("private Git closure verification failed")
                )
                self._persistent_terminal_error = terminal
                raise terminal from exc
        namespace = self._pack_namespace
        if namespace is None:
            return
        try:
            _verify_frozen_pack_namespace(namespace, deadline)
        except BaseException as exc:
            terminal = (
                exc if isinstance(exc, GitAuthorityError)
                else GitAuthorityError("Git pack namespace verification failed")
            )
            self._persistent_terminal_error = terminal
            raise terminal from exc

    def _read_persistent_bootstrap_object(self, bootstrap: _PackBootstrap, oid: str, expected_type: str, object_format: str, deadline: float) -> tuple[str, bytes]:
        terminal = self._persistent_terminal_error
        if terminal is not None:
            raise terminal
        effective_deadline = self._persistent_accounting.deadline or deadline
        reader = self._persistent_reader
        try:
            if reader is not None and reader.bootstrap is not bootstrap:
                self._close_persistent_reader()
                reader = None
            if reader is None:
                reader = _BootstrapBatchReader(self, bootstrap, effective_deadline)
                self._persistent_reader = reader
            return reader.read_object(oid, expected_type, object_format)
        except BaseException as exc:
            terminal_error = (
                exc if isinstance(exc, GitAuthorityError)
                else GitAuthorityError("persistent Git reader operation failed")
            )
            self._persistent_terminal_error = terminal_error
            raise terminal_error from exc

    def _close_persistent_reader(self, *, force_terminate: bool = False) -> None:
        reader = self._persistent_reader
        if reader is None:
            return
        self._persistent_reader = None
        error = reader.close(force_terminate=force_terminate)
        if error is not None:
            raise error

    def _close_pack_bootstraps(self) -> None:
        self._close_persistent_reader()
        retained = list(self._pack_bootstraps)
        final_errors: list[BaseException] = []
        for _attempt in range(2):
            failed: list[_PackBootstrap] = []
            errors: list[BaseException] = []
            for bootstrap in reversed(retained):
                try:
                    bootstrap.close()
                except BaseException as exc:
                    failed.append(bootstrap)
                    errors.append(exc)
            retained = list(reversed(failed))
            final_errors = list(reversed(errors))
            if not retained:
                break
        self._pack_bootstraps = retained
        aggregate = _aggregate_errors(final_errors)
        if aggregate is not None:
            raise aggregate

    def _read_bootstrap_object(self, bootstrap: _PackBootstrap, oid: str, expected_type: str, object_format: str) -> tuple[str, bytes]:
        request = oid.encode("ascii") + b"\n"
        _verify_private_pack_bootstrap(bootstrap)
        output = self.run(
            ("cat-file", "--batch"),
            input_data=request,
            stdout_cap=(
                self.limits.max_blob_bytes + 512
                if expected_type == "blob"
                else self.limits.max_total_bytes + 512
            ),
            object_authority=bootstrap.object_authority,
        )
        _verify_private_pack_bootstrap(bootstrap)
        newline = output.find(b"\n")
        if newline < 0:
            raise GitAuthorityError("truncated bootstrap Git object header")
        header = output[:newline]
        if header == oid.encode("ascii") + b" missing":
            raise GitAuthorityError("requested Git object is absent from the private primary pack bootstrap")
        try:
            actual_oid_raw, object_type_raw, size_raw = header.split(b" ")
            actual_oid = actual_oid_raw.decode("ascii")
            object_type = object_type_raw.decode("ascii")
            size_text = size_raw.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitAuthorityError("malformed bootstrap Git object header") from exc
        if not size_text.isdecimal() or (
            len(size_text) > 1 and size_text.startswith("0")
        ):
            raise GitAuthorityError("bootstrap Git object size is noncanonical")
        size = int(size_text)
        if actual_oid != oid or object_type != expected_type or size < 0:
            raise GitAuthorityError("bootstrap Git object OID/type/size does not match request")
        if expected_type == "blob" and size > self.limits.max_blob_bytes:
            raise GitAuthorityError("Git blob exceeds configured blob limit")
        if size > self.limits.max_total_bytes:
            raise GitAuthorityError("bootstrap Git object exceeds aggregate limit")
        body_start = newline + 1
        body_end = body_start + size
        if body_end >= len(output) or output[body_end:body_end + 1] != b"\n" or body_end + 1 != len(output):
            raise GitAuthorityError("truncated bootstrap Git object body")
        payload = output[body_start:body_end]
        raw = f"{object_type} {size}\0".encode("ascii") + payload
        if hashlib.new(object_format, raw).hexdigest() != oid:
            raise GitAuthorityError("bootstrap Git object bytes do not reproduce their OID")
        receipt_key = (oid, object_type)
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        previous_sha256 = self._returned_object_sha256.get(receipt_key)
        if previous_sha256 is not None and previous_sha256 != payload_sha256:
            raise GitAuthorityError("bootstrap Git object SHA-256 changed between reads")
        self._returned_object_sha256[receipt_key] = payload_sha256
        return object_type, payload

    def verify_sealed_source(self) -> None:
        if getattr(self, "_closure", None) is not None:
            deadline = time.monotonic() + self.limits.timeout_seconds
            _verify_requested_closure(self._closure, self.limits, deadline)
            for entry in self._closure.objects:
                if entry.identity is not None:
                    continue
                object_type, payload = _decode_loose_object(
                    entry.compressed,
                    entry.oid,
                    _object_format_for_oid(entry.oid),
                    self.limits,
                    deadline,
                )
                if self._returned_object_sha256.get((entry.oid, object_type)) != hashlib.sha256(
                    payload
                ).hexdigest():
                    raise GitAuthorityError("bootstrap Git object terminal SHA-256 changed")
            if self._private_closure is None:
                raise GitAuthorityError("private Git object descriptor authority is unavailable")
            _verify_private_closure(self._private_closure, self.limits, deadline)
            for bootstrap in self._pack_bootstraps:
                _verify_pack_bootstrap(bootstrap, self.limits, deadline)
                _verify_private_pack_bootstrap(bootstrap)

    def _cleanup_private_object_store(
        self, private_closure: _PrivateClosureStore
    ) -> None:
        if private_closure.closed:
            return
        private_closure.ownership.require(_PrivateClosureOwnershipState.STORE)
        store = private_closure.owner
        if self._object_store is None:
            return
        if self._object_store is not store:
            raise GitAuthorityError(
                "private Git object-store cleanup owner changed"
            )
        self._object_store_cleanup_attempts += 1
        errors: list[BaseException] = []
        try:
            _verify_private_closure(
                private_closure,
                self.limits,
                time.monotonic() + self.limits.timeout_seconds,
            )
        except BaseException as exc:
            errors.append(exc)
        try:
            private_closure.close()
        except BaseException as exc:
            errors.append(exc)
        else:
            self._object_store_unlinked = True
        capture = self._closure
        if (
            capture is not None
            and len(private_closure.restored_source_receipts)
            == len(private_closure.source_nlinks)
        ):
            try:
                _handoff_restored_pack_receipts_after_builder_abort(
                    capture,
                    self._pack_bootstraps,
                    private_closure.restored_source_receipts,
                )
                # The copied closure has released exactly its own link.  The
                # retained bootstrap now owns the sole extra link and must
                # verify its final unlink against the operation-start count.
                for bootstrap in self._pack_bootstraps:
                    bootstrap.source_nlinks = tuple(
                        (name, expected_nlink - 1)
                        for name, expected_nlink in bootstrap.source_nlinks
                    )
            except BaseException as exc:
                errors.append(exc)
        self._object_store_descriptors_closed = private_closure.descriptors_closed
        aggregate = _aggregate_errors(errors)
        if aggregate is not None:
            raise aggregate

    def close(self, *, suppress_terminal_error: bool = False, primary_error: BaseException | None = None) -> None:
        if self._closed:
            return
        authority_errors: list[BaseException] = []
        cleanup_errors: list[BaseException] = []
        try:
            self.assert_ambient_authority_absent()
        except BaseException as exc:
            authority_errors.append(exc)

        closure = getattr(self, "_closure", None)
        closure_released = closure is None
        if closure is not None:
            try:
                self.verify_sealed_source()
            except BaseException as exc:
                authority_errors.append(exc)
            try:
                closure.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
            else:
                closure_released = True

        private_closure = self._private_closure
        object_store_released = self._object_store is None
        if self._object_store is not None:
            if private_closure is None:
                cleanup_errors.append(
                    GitAuthorityError(
                        "private Git object-store cleanup authority is unavailable"
                    )
                )
            else:
                try:
                    self._cleanup_private_object_store(private_closure)
                except BaseException as exc:
                    cleanup_errors.append(exc)
                if self._object_store_descriptors_closed:
                    object_store_released = True
                    self._object_store = None
                    self._private_objects = None

        if closure_released:
            self._closure = None

        if private_closure is not None and object_store_released:
            if private_closure.descriptors_closed:
                self._private_closure = None
            else:
                try:
                    private_closure.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
                else:
                    self._private_closure = None

        try:
            self._close_pack_bootstraps()
        except BaseException as exc:
            cleanup_errors.append(exc)

        namespace = self._pack_namespace
        if namespace is not None:
            try:
                namespace.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
            else:
                self._pack_namespace = None

        cleanup_confirmed = (
            self._closure is None
            and self._object_store is None
            and self._private_closure is None
            and not self._pack_bootstraps
            and self._pack_namespace is None
        )
        if cleanup_confirmed and not authority_errors and not cleanup_errors:
            self._closed = True

        authority_error = _aggregate_errors(authority_errors)
        cleanup_error = _aggregate_errors(cleanup_errors)
        if authority_error is not None and cleanup_error is not None:
            terminal_error: BaseException | None = _combine_primary_and_cleanup(
                authority_error, cleanup_error
            )
        elif authority_error is not None:
            terminal_error = authority_error
        else:
            terminal_error = cleanup_error
        if terminal_error is not None:
            if suppress_terminal_error:
                if primary_error is not None:
                    primary_error.add_note(f"terminal Git cleanup failure: {terminal_error}")
                return
            raise terminal_error

    def object_format(self) -> str:
        object_format = _one_line(self.run(("rev-parse", "--show-object-format")), "Git object format")
        if object_format not in _SUPPORTED_OBJECT_FORMATS:
            raise GitAuthorityError(f"unsupported Git object format: {object_format!r}")
        return object_format

    def _verify_private_child_authority(
        self, authority: _ObjectDirectoryAuthority
    ) -> tuple[tuple[int, str], ...]:
        _verify_object_directory_authority(authority)
        private_closure = self._private_closure
        if private_closure is not None and private_closure.object_authority == authority:
            _verify_private_closure(
                private_closure,
                self.limits,
                time.monotonic() + self.limits.timeout_seconds,
            )
            return (
                (private_closure.root_fd, "copied closure root"),
                (private_closure.objects_fd, "copied closure objects"),
                (private_closure.pack_fd, "copied closure pack"),
            )
        for bootstrap in self._pack_bootstraps:
            if bootstrap.object_authority == authority:
                _verify_private_pack_bootstrap(bootstrap)
                return (
                    (bootstrap.root_fd, "bootstrap root"),
                    (bootstrap.destination_fd, "bootstrap objects"),
                    (bootstrap.destination_pack_fd, "bootstrap pack"),
                )
        raise GitAuthorityError("private Git object descriptor authority is not retained")

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        input_data: bytes | None = None,
        stdout_cap: int = _METADATA_OUTPUT_CAP,
        object_authority: _ObjectDirectoryAuthority | None = None,
    ) -> bytes:
        if object_authority is None and self._private_closure is not None:
            object_authority = self._private_closure.object_authority
        _regular_inode_matches(self.executable, self._executable_inode, "Git executable")
        if type(stdout_cap) is not int or stdout_cap < 0:
            raise GitAuthorityError("Git stdout cap is invalid")
        environment = _git_env()
        authority_directories: tuple[tuple[int, str], ...] | None = None
        if object_authority is not None:
            authority_directories = self._verify_private_child_authority(object_authority)
            environment["GIT_OBJECT_DIRECTORY"] = object_authority.child_path
            environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = ""
        environment.update(_bounded_git_resource_environment(self.limits))
        input_file = None
        try:
            input_file = tempfile.TemporaryFile() if input_data is not None else None
            if input_file is not None:
                input_file.write(input_data)
                input_file.seek(0)
        except OSError as exc:
            if input_file is not None:
                try:
                    input_file.close()
                except BaseException as cleanup_exc:
                    primary = GitAuthorityError("Git temporary input setup failed")
                    cleanup = GitAuthorityError("Git temporary input cleanup failed")
                    raise _combine_primary_and_cleanup(primary, cleanup) from cleanup_exc
            raise GitAuthorityError("Git temporary input setup failed") from exc
        guard: _DirectoryMutationGuard | None = None
        launching = False
        try:
            resource_envelope = _derive_child_resource_envelope(self.limits)
            if object_authority is not None:
                assert authority_directories is not None
                guard = _DirectoryMutationGuard.arm(authority_directories)
                self._verify_private_child_authority(object_authority)
                guard.assert_quiet()
            launching = True
            process = subprocess.Popen((str(self.executable), "--no-replace-objects", *arguments), cwd=self.repo_root, env=environment, stdin=input_file if input_file is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, preexec_fn=_bounded_git_preexec(resource_envelope), pass_fds=object_authority.pass_fds if object_authority is not None else ())
            termination = _ProcessTermination(process=process, pgid=process.pid)
        except BaseException as exc:
            if launching and isinstance(exc, (OSError, subprocess.SubprocessError)):
                primary: BaseException = GitAuthorityError("Git command could not start")
            elif isinstance(exc, (KeyboardInterrupt, SystemExit, GitAuthorityError)):
                primary = exc
            else:
                primary = GitAuthorityError("Git command setup failed")
            cleanup_error: BaseException | None = None
            if input_file is not None and not input_file.closed:
                try:
                    input_file.close()
                except BaseException as cleanup_exc:
                    cleanup_error = GitAuthorityError("Git temporary input cleanup failed")
            if guard is not None:
                try:
                    guard.close()
                except BaseException as cleanup_exc:
                    guard_error = (
                        cleanup_exc
                        if isinstance(cleanup_exc, GitAuthorityError)
                        else GitAuthorityError("private Git mutation guard cleanup failed")
                    )
                    cleanup_error = (
                        guard_error
                        if cleanup_error is None
                        else _combine_primary_and_cleanup(cleanup_error, guard_error)
                    )
            if isinstance(primary, (KeyboardInterrupt, SystemExit)) and cleanup_error is None:
                raise
            combined = _combine_primary_and_cleanup(primary, cleanup_error)
            if combined is primary:
                raise primary
            raise combined from exc
        primary = None
        stdout: bytes | None = None
        try:
            if input_file is not None:
                try:
                    input_file.close()
                except BaseException as exc:
                    raise GitAuthorityError("Git temporary input cleanup failed") from exc
                else:
                    input_file = None
            stdout, stderr = _read_process_streams(process, self.limits.timeout_seconds, stdout_cap, _STDERR_CAP, termination)
            _regular_inode_matches(self.executable, self._executable_inode, "Git executable")
            if object_authority is not None:
                assert guard is not None
                guard.assert_quiet()
                self._verify_private_child_authority(object_authority)
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise GitAuthorityError(f"Git command failed ({' '.join(arguments)}): {detail}")
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit, GitAuthorityError)):
                primary = exc
            else:
                primary = GitAuthorityError("Git command I/O setup or stream read failed")

        cleanup_error = termination.terminate() if primary is not None else None
        if input_file is not None and not input_file.closed:
            try:
                input_file.close()
            except BaseException:
                input_error = GitAuthorityError("Git temporary input cleanup failed")
                cleanup_error = (
                    input_error
                    if cleanup_error is None
                    else _combine_primary_and_cleanup(cleanup_error, input_error)
                )
        stream_error = _close_process_streams(process)
        if stream_error is not None:
            cleanup_error = (
                stream_error
                if cleanup_error is None
                else _combine_primary_and_cleanup(cleanup_error, stream_error)
            )
        if guard is not None:
            try:
                guard.close()
            except BaseException as exc:
                guard_error = (
                    exc
                    if isinstance(exc, GitAuthorityError)
                    else GitAuthorityError("private Git mutation guard cleanup failed")
                )
                cleanup_error = (
                    guard_error
                    if cleanup_error is None
                    else _combine_primary_and_cleanup(cleanup_error, guard_error)
                )

        if primary is not None:
            if isinstance(primary, (KeyboardInterrupt, SystemExit)) and cleanup_error is None:
                raise primary
            combined = _combine_primary_and_cleanup(primary, cleanup_error)
            if combined is primary:
                raise primary
            raise combined from primary
        if cleanup_error is not None:
            if isinstance(cleanup_error, GitAuthorityError):
                raise cleanup_error
            raise GitAuthorityError("Git command cleanup failed") from cleanup_error
        assert stdout is not None
        return stdout


def _validate_limits(limits: GitScanLimits) -> None:
    integer_limits = ((limits.max_entries, _MAX_ENTRIES, True), (limits.max_path_bytes, _MAX_PATH_BYTES, False), (limits.max_blob_bytes, _MAX_BLOB_BYTES, True), (limits.max_total_bytes, _MAX_TOTAL_BYTES, True))
    if any(type(value) is not int or value > maximum or (value < 0 if zero_allowed else value <= 0) for value, maximum, zero_allowed in integer_limits):
        raise GitAuthorityError("Git scan limits must be exact bounded integers")
    if type(limits.timeout_seconds) not in {int, float} or not math.isfinite(limits.timeout_seconds) or not 0 < limits.timeout_seconds <= _MAX_TIMEOUT_SECONDS:
        raise GitAuthorityError("Git scan limits require a finite bounded timeout")


def _bounded_git_resource_environment(limits: GitScanLimits) -> dict[str, str]:
    """Bound Git's pack/delta caches in addition to the mandatory child rlimits."""
    cache = max(1_048_576, min(16_777_216, limits.max_total_bytes // 4))
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.deltaBaseCacheLimit",
        "GIT_CONFIG_VALUE_0": str(cache),
        "GIT_CONFIG_KEY_1": "core.packedGitLimit",
        "GIT_CONFIG_VALUE_1": str(cache),
    }


def _derive_child_resource_envelope(
    limits: GitScanLimits,
) -> _ChildResourceEnvelope:
    if resource is None:
        raise GitAuthorityError("Linux resource limits are unavailable")
    desired_memory = max(
        256 * 1024 * 1024,
        min(900 * 1024 * 1024, limits.max_total_bytes * 8 + 16 * 1024 * 1024),
    )
    desired_cpu = max(1, min(31, math.ceil(limits.timeout_seconds) + 1))
    _inherited_memory_soft, inherited_memory_hard = resource.getrlimit(
        resource.RLIMIT_AS
    )
    _inherited_cpu_soft, inherited_cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
    if (
        inherited_memory_hard != resource.RLIM_INFINITY
        and inherited_memory_hard < 256 * 1024 * 1024
    ):
        raise GitAuthorityError("inherited Git address-space limit is insufficient")
    if inherited_cpu_hard != resource.RLIM_INFINITY and inherited_cpu_hard < 1:
        raise GitAuthorityError("inherited Git CPU limit is insufficient")
    memory = (
        desired_memory
        if inherited_memory_hard == resource.RLIM_INFINITY
        else min(desired_memory, inherited_memory_hard)
    )
    cpu = (
        desired_cpu
        if inherited_cpu_hard == resource.RLIM_INFINITY
        else min(desired_cpu, inherited_cpu_hard)
    )
    return _ChildResourceEnvelope(
        address_space=(memory, memory),
        cpu_seconds=(cpu, cpu),
    )


def _bounded_git_preexec(envelope: _ChildResourceEnvelope) -> Callable[[], None]:

    def configure() -> None:
        if resource is None:
            raise OSError("Linux resource limits are unavailable")
        resource.setrlimit(resource.RLIMIT_AS, envelope.address_space)
        resource.setrlimit(resource.RLIMIT_CPU, envelope.cpu_seconds)

    return configure


def _reject_injected_git_environment() -> None:
    injected = sorted(name for name in os.environ if name in _BLOCKED_GIT_ENV or name.startswith("GIT_CONFIG_"))
    if injected:
        raise GitAuthorityError(f"injected Git environment is forbidden: {', '.join(injected)}")


def _regular_inode(path: Path, label: str) -> tuple[int, int]:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise GitAuthorityError(f"{label} cannot be statted") from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise GitAuthorityError(f"{label} is not an executable regular file")
    return metadata.st_dev, metadata.st_ino


def _regular_inode_matches(path: Path, expected: tuple[int, int], label: str) -> None:
    if _regular_inode(path, label) != expected:
        raise GitAuthorityError(f"{label} changed during source snapshot")


def _resolve_git_path(repo_root: Path, text: str, label: str) -> Path:
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise GitAuthorityError(f"unexpected {label}") from exc


def _one_line(output: bytes, label: str) -> str:
    try:
        text = output.decode("ascii")
    except UnicodeDecodeError as exc:
        raise GitAuthorityError(f"malformed {label}") from exc
    if not text.endswith("\n") or "\n" in text[:-1] or not text[:-1]:
        raise GitAuthorityError(f"malformed {label}")
    return text[:-1]


def _require_full_oid(oid: str, object_format: str, label: str) -> None:
    width = 40 if object_format == "sha1" else 64
    if len(oid) != width or any(character not in "0123456789abcdef" for character in oid):
        raise GitAuthorityError(f"full {label} OID is required for {object_format}")


def _one_oid(output: bytes, object_format: str, label: str) -> str:
    oid = _one_line(output, f"{label} OID")
    _require_full_oid(oid, object_format, label)
    return oid


def _require_object_type(runner: _GitRunner, oid: str, expected: str) -> None:
    actual = _one_line(runner.run(("cat-file", "-t", oid)), "Git object type")
    if actual != expected:
        raise GitAuthorityError(f"expected {expected} object, received {actual!r}")


def _parse_tree_records(output: bytes, object_format: str, limits: GitScanLimits) -> tuple[tuple[int, str, str], ...]:
    if output and not output.endswith(b"\0"):
        raise GitAuthorityError("truncated Git tree listing")
    records: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for raw_record in output[:-1].split(b"\0") if output else ():
        if len(records) >= limits.max_entries:
            raise GitAuthorityError("Git tree entry limit exceeded")
        try:
            header, raw_path = raw_record.split(b"\t", 1)
            mode, object_type, raw_oid = header.split(b" ")
        except ValueError as exc:
            raise GitAuthorityError("malformed Git tree record") from exc
        if object_type != b"blob":
            raise GitAuthorityError("Git tree record is not a blob")
        if mode not in _ALLOWED_MODES:
            raise GitAuthorityError("Git tree record has unsupported regular-file mode")
        path = _validate_path(raw_path, limits.max_path_bytes)
        if path in seen:
            raise GitAuthorityError("duplicate Git tree path")
        try:
            oid = raw_oid.decode("ascii")
        except UnicodeDecodeError as exc:
            raise GitAuthorityError("malformed Git tree object OID") from exc
        _require_full_oid(oid, object_format, "blob")
        seen.add(path)
        records.append((_ALLOWED_MODES[mode], oid, path))
    return tuple(sorted(records, key=lambda record: record[2].encode("utf-8")))


def _validate_path(raw_path: bytes, maximum_bytes: int) -> str:
    if not raw_path or len(raw_path) > maximum_bytes:
        raise GitAuthorityError("Git tree path violates configured path limit")
    try:
        path = raw_path.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GitAuthorityError("Git tree path is not valid UTF-8") from exc
    if path != unicodedata.normalize("NFC", path):
        raise GitAuthorityError("Git tree path is not NFC-normalized")
    if path.startswith("/") or "\\" in path or "\x00" in path:
        raise GitAuthorityError("Git tree path is not canonical POSIX form")
    if any(component in {"", ".", ".."} for component in path.split("/")):
        raise GitAuthorityError("Git tree path is not canonical relative form")
    if any(unicodedata.category(character).startswith("C") for character in path):
        raise GitAuthorityError("Git tree path contains a control character")
    return path


def _store_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode), metadata.st_nlink, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    if not stat.S_ISDIR(metadata.st_mode):
        raise GitAuthorityError("Git object-store directory changed during source snapshot")
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _private_directory_identity(
    metadata: os.stat_result, *, expected_mode: int | None = None
) -> _PrivateDirectoryIdentity:
    if not stat.S_ISDIR(metadata.st_mode):
        raise GitAuthorityError("private Git directory authority is not a directory")
    identity = _PrivateDirectoryIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        file_type=stat.S_IFMT(metadata.st_mode),
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        mode=stat.S_IMODE(metadata.st_mode),
    )
    if identity.uid != os.geteuid() or identity.gid != os.getegid():
        raise GitAuthorityError("private Git directory authority owner changed")
    if expected_mode is not None and identity.mode != expected_mode:
        raise GitAuthorityError("private Git directory authority mode changed")
    return identity


def _verify_object_directory_authority(authority: _ObjectDirectoryAuthority) -> None:
    expected_path = f"/proc/self/fd/{authority.descriptor}"
    if authority.child_path != expected_path or authority.descriptor not in authority.pass_fds:
        raise GitAuthorityError("private Git object descriptor authority is malformed")
    try:
        actual = _private_directory_identity(
            os.fstat(authority.descriptor), expected_mode=authority.identity.mode
        )
    except OSError as exc:
        raise GitAuthorityError("private Git object descriptor authority is unavailable") from exc
    if actual != authority.identity:
        raise GitAuthorityError("private Git object descriptor authority changed")


def _seal_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise GitAuthorityError("Git object-store seal deadline exceeded")


def _parse_and_search_pack_index(
    descriptor: int,
    oid_bytes: bytes,
    hash_width: int,
    limits: GitScanLimits,
    deadline: float,
) -> bool:
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise GitAuthorityError("Git pack index is not regular")
        header = os.pread(descriptor, 8, 0)
        if header[:4] != b"\xfftOc" or header[4:] != b"\x00\x00\x00\x02":
            raise GitAuthorityError("unsupported Git pack index format")
        fanout = os.pread(descriptor, 1024, 8)
        if len(fanout) != 1024:
            raise GitAuthorityError("truncated Git pack index")
        count = int.from_bytes(fanout[-4:], "big")
        if count > _MAX_PACK_INDEX_OBJECTS or count > limits.max_total_bytes // hash_width:
            raise GitAuthorityError("Git pack index object cap exceeded")
        minimum = 8 + 1024 + count * (hash_width + 8) + hash_width * 2
        if metadata.st_size < minimum:
            raise GitAuthorityError("truncated Git pack index")
        start = 8 + 1024
        low = int.from_bytes(fanout[(oid_bytes[0] - 1) * 4:oid_bytes[0] * 4], "big") if oid_bytes[0] else 0
        high = int.from_bytes(fanout[oid_bytes[0] * 4:(oid_bytes[0] + 1) * 4], "big")
        while low < high:
            _seal_deadline(deadline)
            middle = (low + high) // 2
            current = os.pread(descriptor, hash_width, start + middle * hash_width)
            if len(current) != hash_width:
                raise GitAuthorityError("truncated Git pack index")
            if current < oid_bytes:
                low = middle + 1
            else:
                high = middle
        _seal_deadline(deadline)
        return low < count and os.pread(descriptor, hash_width, start + low * hash_width) == oid_bytes
    except OSError as exc:
        raise GitAuthorityError("Git pack index could not be read") from exc


def _pack_index_contains(name: str, directory_fd: int, oid: str, object_format: str, limits: GitScanLimits, deadline: float) -> bool:
    """Bounded binary lookup in a Git v2 pack index without listing its objects."""
    oid_bytes = bytes.fromhex(oid)
    hash_width = 20 if object_format == "sha1" else 32
    try:
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    except OSError as exc:
        raise GitAuthorityError("Git pack index cannot be opened") from exc
    identity = _owned_descriptor_identity(os.fstat(descriptor))
    primary: BaseException | None = None
    result: bool | None = None

    try:
        result = _parse_and_search_pack_index(
            descriptor,
            oid_bytes,
            hash_width,
            limits,
            deadline,
        )
    except BaseException as exc:
        primary = exc

    try:
        _close_owned_descriptor(
            descriptor,
            identity,
            label="Git pack-index",
        )
    except BaseException as cleanup:
        if primary is not None:
            raise GitAuthorityAggregateError(primary, cleanup) from primary
        raise

    if primary is not None:
        raise primary
    assert result is not None
    return result


def _freeze_pack_namespace(source: Path, limits: GitScanLimits, deadline: float) -> _PackNamespace:
    """Freeze names/identities once; a bad namespace is a packed-only failure sentinel."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(source, flags)
        try:
            fd = os.open("pack", flags, dir_fd=root_fd)
        finally:
            os.close(root_fd)
    except OSError:
        return _PackNamespace(source / "pack", None, None, {}, "Git pack namespace is unavailable")
    try:
        identity = _directory_identity(os.fstat(fd))
        entries: dict[str, _PackEntry] = {}
        with os.scandir(fd) as iterator:
            for count, item in enumerate(iterator, start=1):
                _seal_deadline(deadline)
                if count > limits.max_entries:
                    return _PackNamespace(source / "pack", fd, identity, entries, "Git pack discovery entry cap exceeded")
                if not item.name.startswith("pack-") or not item.name.endswith((".idx", ".pack")):
                    continue
                try:
                    metadata = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    return _PackNamespace(source / "pack", fd, identity, entries, "Git pack namespace changed during inventory")
                if not stat.S_ISREG(metadata.st_mode):
                    return _PackNamespace(source / "pack", fd, identity, entries, "Git pack namespace contains a non-regular entry")
                entries[item.name] = _PackEntry(item.name, _store_identity(metadata))
        return _PackNamespace(source / "pack", fd, identity, entries, None)
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise GitAuthorityError("Git pack discovery failed") from exc


def _cleanup_unfinished_pack_root(
    owner: _OwnedTemporaryRoot,
    *,
    destination_fd: int | None,
    destination_identity: _OwnedDescriptorIdentity | None,
    destination_named_identity: _OwnedDescriptorIdentity | None,
    destination_created: bool,
    destination_pack_fd: int | None,
    destination_pack_identity: _OwnedDescriptorIdentity | None,
    destination_pack_named_identity: _OwnedDescriptorIdentity | None,
    destination_pack_created: bool,
) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    errors: list[BaseException] = []

    if destination_created and destination_fd is None:
        if destination_named_identity is None:
            errors.append(
                GitAuthorityError(
                    "pack bootstrap objects cleanup identity is unavailable"
                )
            )
        else:
            try:
                destination_identity = destination_named_identity
                destination_fd = os.open("objects", flags, dir_fd=owner.root_fd)
                destination_identity = _owned_descriptor_identity(
                    os.fstat(destination_fd)
                )
                _require_owned_directory(
                    destination_identity,
                    label="pack bootstrap objects directory",
                    private=True,
                )
                if destination_identity != destination_named_identity:
                    raise GitAuthorityError(
                        "pack bootstrap objects cleanup identity changed"
                    )
            except BaseException as exc:
                errors.append(exc)

    if destination_pack_created and destination_pack_fd is None:
        if destination_fd is None or destination_pack_named_identity is None:
            errors.append(
                GitAuthorityError(
                    "pack bootstrap pack cleanup identity is unavailable"
                )
            )
        else:
            try:
                destination_pack_identity = destination_pack_named_identity
                destination_pack_fd = os.open(
                    "pack", flags, dir_fd=destination_fd
                )
                destination_pack_identity = _owned_descriptor_identity(
                    os.fstat(destination_pack_fd)
                )
                _require_owned_directory(
                    destination_pack_identity,
                    label="pack bootstrap pack directory",
                    private=True,
                )
                if destination_pack_identity != destination_pack_named_identity:
                    raise GitAuthorityError(
                        "pack bootstrap pack cleanup identity changed"
                    )
            except BaseException as exc:
                errors.append(exc)

    if destination_pack_created and destination_fd is not None:
        try:
            if (
                destination_pack_fd is None
                or destination_pack_identity is None
                or destination_pack_named_identity is None
            ):
                raise GitAuthorityError(
                    "pack bootstrap pack cleanup identity is unavailable"
                )
            current = _owned_descriptor_identity(
                os.stat("pack", dir_fd=destination_fd, follow_symlinks=False)
            )
            if (
                current != destination_pack_named_identity
                or destination_pack_identity != destination_pack_named_identity
                or not _owned_directory_is_empty(destination_pack_fd)
            ):
                raise GitAuthorityError(
                    "pack bootstrap pack cleanup was not confirmed"
                )
            os.rmdir("pack", dir_fd=destination_fd)
        except BaseException as exc:
            errors.append(exc)

    if destination_created:
        try:
            if (
                destination_fd is None
                or destination_identity is None
                or destination_named_identity is None
            ):
                raise GitAuthorityError(
                    "pack bootstrap objects cleanup identity is unavailable"
                )
            current = _owned_descriptor_identity(
                os.stat("objects", dir_fd=owner.root_fd, follow_symlinks=False)
            )
            if (
                current != destination_named_identity
                or destination_identity != destination_named_identity
                or not _owned_directory_is_empty(destination_fd)
            ):
                raise GitAuthorityError(
                    "pack bootstrap objects cleanup was not confirmed"
                )
            os.rmdir("objects", dir_fd=owner.root_fd)
        except BaseException as exc:
            errors.append(exc)

    root_name_unconfirmed = False
    try:
        current_root = _owned_descriptor_identity(
            os.stat(
                owner.basename,
                dir_fd=owner.parent_fd,
                follow_symlinks=False,
            )
        )
    except OSError:
        root_name_unconfirmed = True
    else:
        if (
            current_root != owner.root_identity
            or not _owned_directory_is_empty(owner.root_fd)
        ):
            root_name_unconfirmed = True
        else:
            try:
                os.rmdir(owner.basename, dir_fd=owner.parent_fd)
                os.fsync(owner.parent_fd)
            except BaseException as exc:
                errors.append(exc)

    retained = (
        (
            destination_pack_fd,
            destination_pack_identity,
            "pack bootstrap pack",
        ),
        (destination_fd, destination_identity, "pack bootstrap objects"),
        (owner.root_fd, owner.root_identity, "pack bootstrap root"),
        (owner.parent_fd, owner.parent_identity, "pack bootstrap parent"),
    )
    for descriptor, identity, label in retained:
        if descriptor is None or identity is None:
            continue
        try:
            _close_owned_descriptor(descriptor, identity, label=label)
        except BaseException as exc:
            errors.append(exc)
    if root_name_unconfirmed:
        errors.append(
            GitAuthorityError("pack bootstrap root-name cleanup was not confirmed")
        )
    aggregate = _aggregate_errors(errors)
    if aggregate is not None:
        raise aggregate


def _bootstrap_pack_view(namespace: _PackNamespace, common_dir: Path, required_oid: str, object_format: str, limits: GitScanLimits, deadline: float) -> _PackBootstrap | None:
    """Hardlink only the canonical pack/index pair indexing ``required_oid``.

    Discovery is incremental and descriptor-backed.  Bad, incomplete, or huge
    unrelated pairs are deliberately not retained and cannot influence a loose
    closure.  A pair which claims the requested OID is pinned as one unit.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    if namespace.sentinel is not None:
        raise GitAuthorityError(namespace.sentinel)
    if namespace.fd is None or namespace.identity is None:
        raise GitAuthorityError("Git pack namespace is unavailable")
    source_fd = namespace.fd
    try:
        source_identity = _owned_descriptor_identity(os.fstat(source_fd))
        if (
            source_identity.device,
            source_identity.inode,
            source_identity.file_type,
        ) != namespace.identity:
            raise GitAuthorityError("Git pack namespace changed during source snapshot")
        candidate: str | None = None
        for name, entry in sorted(namespace.entries.items()):
            _seal_deadline(deadline)
            if not name.endswith(".idx"):
                continue
            if _store_identity(
                os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            )[:6] != entry.identity[:6]:
                raise GitAuthorityError("Git pack namespace changed during source snapshot")
            try:
                if _pack_index_contains(name, source_fd, required_oid, object_format, limits, deadline):
                    if candidate is None or name < candidate:
                        candidate = name
            except GitAuthorityAggregateError as exc:
                if isinstance(exc.cleanup, _OwnedDescriptorCleanupError):
                    raise
                continue
            except _OwnedDescriptorCleanupError:
                raise
            except GitAuthorityError:
                continue
        if candidate is None:
            return None
        pack_name = candidate[:-4] + ".pack"
        entries: list[_PackEntry] = []
        source_nlinks: list[tuple[str, int]] = []
        for name in (pack_name, candidate):
            _seal_deadline(deadline)
            initial = namespace.entries.get(name)
            if initial is None:
                raise GitAuthorityError("requested Git pack/index pair was absent from operation-start namespace")
            try:
                descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=source_fd)
            except OSError as exc:
                raise GitAuthorityError("requested Git pack/index pair is incomplete") from exc
            descriptor_identity: _OwnedDescriptorIdentity | None = None
            try:
                metadata = os.fstat(descriptor)
                descriptor_identity = _owned_descriptor_identity(metadata)
                if not stat.S_ISREG(metadata.st_mode):
                    raise GitAuthorityError("requested Git pack/index pair contains a non-regular entry")
                identity = _store_identity(metadata)
                if identity[:6] != initial.identity[:6]:
                    raise GitAuthorityError("requested Git pack/index pair changed during bootstrap")
                if name.endswith(".idx") and identity[4] > limits.max_total_bytes:
                    raise GitAuthorityError("requested Git pack index exceeds configured limit")
                entries.append(_PackEntry(name, identity))
                source_nlinks.append((name, metadata.st_nlink))
            finally:
                if name.endswith(".idx") and descriptor_identity is not None:
                    _close_owned_descriptor(
                        descriptor,
                        descriptor_identity,
                        label="Git pack-index",
                    )
                else:
                    os.close(descriptor)
        owner: _OwnedTemporaryRoot | None = None
        destination_created = False
        destination_named_identity: _OwnedDescriptorIdentity | None = None
        destination_fd: int | None = None
        destination_identity: _OwnedDescriptorIdentity | None = None
        destination_pack_created = False
        destination_pack_named_identity: _OwnedDescriptorIdentity | None = None
        destination_pack_fd: int | None = None
        destination_pack_identity: _OwnedDescriptorIdentity | None = None
        bootstrap: _PackBootstrap | None = None
        try:
            owner = _OwnedTemporaryRoot.create(common_dir)
            os.mkdir("objects", 0o700, dir_fd=owner.root_fd)
            destination_created = True
            destination_named_identity = _owned_descriptor_identity(
                os.stat("objects", dir_fd=owner.root_fd, follow_symlinks=False)
            )
            _require_owned_directory(
                destination_named_identity,
                label="pack bootstrap objects directory",
                private=True,
            )
            destination_fd = os.open("objects", flags, dir_fd=owner.root_fd)
            destination_identity = _owned_descriptor_identity(
                os.fstat(destination_fd)
            )
            _require_owned_directory(
                destination_identity,
                label="pack bootstrap objects directory",
                private=True,
            )
            if destination_identity != destination_named_identity:
                raise GitAuthorityError(
                    "pack bootstrap objects directory identity changed"
                )

            os.mkdir("pack", 0o700, dir_fd=destination_fd)
            destination_pack_created = True
            destination_pack_named_identity = _owned_descriptor_identity(
                os.stat("pack", dir_fd=destination_fd, follow_symlinks=False)
            )
            _require_owned_directory(
                destination_pack_named_identity,
                label="pack bootstrap pack directory",
                private=True,
            )
            destination_pack_fd = os.open(
                "pack", flags, dir_fd=destination_fd
            )
            destination_pack_identity = _owned_descriptor_identity(
                os.fstat(destination_pack_fd)
            )
            _require_owned_directory(
                destination_pack_identity,
                label="pack bootstrap pack directory",
                private=True,
            )
            if destination_pack_identity != destination_pack_named_identity:
                raise GitAuthorityError(
                    "pack bootstrap pack directory identity changed"
                )

            bootstrap = _PackBootstrap(
                owner=owner,
                destination_fd=destination_fd,
                destination_identity=destination_identity,
                destination_pack_fd=destination_pack_fd,
                destination_pack_identity=destination_pack_identity,
                source_fd=source_fd,
                source_identity=source_identity,
                entries=(),
                source_nlinks=(),
            )
            bootstrap._construction_incomplete = True
            for entry, source_nlink in zip(entries, source_nlinks, strict=True):
                _seal_deadline(deadline)
                os.link(
                    entry.name,
                    entry.name,
                    src_dir_fd=source_fd,
                    dst_dir_fd=destination_pack_fd,
                    follow_symlinks=False,
                )
                bootstrap.entries += (entry,)
                bootstrap.source_nlinks += (source_nlink,)
                linked_identity = _store_identity(
                    os.stat(entry.name, dir_fd=source_fd, follow_symlinks=False)
                )
                if (
                    linked_identity[:3] + linked_identity[4:6]
                    != entry.identity[:3] + entry.identity[4:6]
                ):
                    raise GitAuthorityError("Git pack changed during bootstrap")
                if (
                    _store_identity(
                        os.stat(
                            entry.name,
                            dir_fd=destination_pack_fd,
                            follow_symlinks=False,
                        )
                    )
                    != linked_identity
                ):
                    raise GitAuthorityError(
                        "private Git pack bootstrap identity mismatch"
                    )
                _advance_namespace_owned_hardlink(
                    namespace, entry.name, linked_identity
                )
                bootstrap.entries = (
                    *bootstrap.entries[:-1],
                    _PackEntry(entry.name, linked_identity),
                )
            bootstrap._construction_incomplete = False
            return bootstrap
        except BaseException as exc:
            if isinstance(exc, GitAuthorityError):
                primary: BaseException = exc
            else:
                primary = GitAuthorityError(
                    "private Git pack bootstrap copy failed"
                )
                primary.__cause__ = exc
            cleanup: BaseException | None = None
            if bootstrap is not None:
                try:
                    bootstrap.close()
                except BaseException as cleanup_exc:
                    cleanup = cleanup_exc
            elif owner is not None:
                try:
                    _cleanup_unfinished_pack_root(
                        owner,
                        destination_fd=destination_fd,
                        destination_identity=destination_identity,
                        destination_named_identity=destination_named_identity,
                        destination_created=destination_created,
                        destination_pack_fd=destination_pack_fd,
                        destination_pack_identity=destination_pack_identity,
                        destination_pack_named_identity=(
                            destination_pack_named_identity
                        ),
                        destination_pack_created=destination_pack_created,
                    )
                except BaseException as cleanup_exc:
                    cleanup = cleanup_exc
            if cleanup is not None:
                raise GitAuthorityAggregateError(primary, cleanup) from exc
            raise primary from exc
    except BaseException:
        raise


def _verify_pack_bootstrap(bootstrap: _PackBootstrap, limits: GitScanLimits, deadline: float) -> None:
    _seal_deadline(deadline)
    try:
        source_identity = _owned_descriptor_identity(os.fstat(bootstrap.source_fd))
    except OSError as exc:
        raise GitAuthorityError(
            "Git pack directory changed during source snapshot"
        ) from exc
    if source_identity != bootstrap.source_identity:
        raise GitAuthorityError("Git pack directory changed during source snapshot")
    for entry in bootstrap.entries:
        _seal_deadline(deadline)
        try:
            if _store_identity(os.stat(entry.name, dir_fd=bootstrap.source_fd, follow_symlinks=False)) != entry.identity:
                raise GitAuthorityError("Git pack changed during source snapshot")
        except OSError as exc:
            raise GitAuthorityError("Git pack changed during source snapshot") from exc


def _exact_directory_entries(descriptor: int, expected: dict[str, tuple[int, int, int]]) -> None:
    """Reject addition, removal, replacement, or type drift in a child-readable directory."""
    try:
        actual: dict[str, tuple[int, int, int]] = {}
        with os.scandir(descriptor) as iterator:
            for item in iterator:
                metadata = os.stat(item.name, dir_fd=descriptor, follow_symlinks=False)
                actual[item.name] = (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))
    except OSError as exc:
        raise GitAuthorityError("private Git pack bootstrap inventory failed") from exc
    if actual != expected:
        raise GitAuthorityError("private Git pack bootstrap inventory changed")


def _verify_private_pack_bootstrap(bootstrap: _PackBootstrap) -> None:
    root_identity = _owned_descriptor_identity(os.fstat(bootstrap.owner.root_fd))
    _require_owned_directory(
        root_identity,
        label="private Git pack bootstrap root",
        private=True,
    )
    if root_identity != bootstrap.owner.root_identity:
        raise GitAuthorityError("private Git pack bootstrap root changed")
    destination_identity = _owned_descriptor_identity(
        os.fstat(bootstrap.destination_fd)
    )
    _require_owned_directory(
        destination_identity,
        label="private Git pack bootstrap objects directory",
        private=True,
    )
    if destination_identity != bootstrap.destination_identity:
        raise GitAuthorityError(
            "private Git pack bootstrap objects directory changed"
        )
    destination_pack_identity = _owned_descriptor_identity(
        os.fstat(bootstrap.destination_pack_fd)
    )
    _require_owned_directory(
        destination_pack_identity,
        label="private Git pack bootstrap pack directory",
        private=True,
    )
    if destination_pack_identity != bootstrap.destination_pack_identity:
        raise GitAuthorityError(
            "private Git pack bootstrap pack directory changed"
        )
    _exact_directory_entries(
        bootstrap.root_fd,
        {
            "objects": (
                bootstrap.destination_identity.device,
                bootstrap.destination_identity.inode,
                bootstrap.destination_identity.file_type,
            )
        },
    )
    _exact_directory_entries(
        bootstrap.destination_fd,
        {
            "pack": (
                bootstrap.destination_pack_identity.device,
                bootstrap.destination_pack_identity.inode,
                bootstrap.destination_pack_identity.file_type,
            )
        },
    )
    _exact_directory_entries(
        bootstrap.destination_pack_fd,
        {entry.name: (entry.identity[0], entry.identity[1], entry.identity[2]) for entry in bootstrap.entries},
    )


def _collect_restored_pack_source_receipts(
    capture: _ClosureCapture,
) -> dict[tuple[int, str], tuple[int, ...]]:
    restored: dict[tuple[int, str], tuple[int, ...]] = {}
    for source in capture.pack_sources:
        for entry in source.entries:
            current = _store_identity(
                os.stat(
                    entry.name,
                    dir_fd=source.directory_fd,
                    follow_symlinks=False,
                )
            )
            if (
                current[:3] + current[4:6]
                != entry.identity[:3] + entry.identity[4:6]
                or current[3] != entry.identity[3]
            ):
                raise GitAuthorityError(
                    "private Git object-store source restoration fallback changed"
                )
            restored[(source.directory_fd, entry.name)] = current
    return restored


def _handoff_restored_pack_receipts_after_builder_abort(
    capture: _ClosureCapture,
    bootstraps: list[_PackBootstrap],
    restored_sources: dict[tuple[int, str], tuple[int, ...]],
) -> None:
    refreshed_sources: list[_ClosurePackSource] = []
    for source in capture.pack_sources:
        refreshed_entries: list[_ClosurePackEntry] = []
        for entry in source.entries:
            restored = restored_sources.get((source.directory_fd, entry.name))
            if restored is None:
                raise GitAuthorityError(
                    "private Git object-store source restoration receipt is missing"
                )
            if (
                restored[:3] + restored[4:6]
                != entry.identity[:3] + entry.identity[4:6]
                or restored[3] != entry.identity[3]
            ):
                raise GitAuthorityError(
                    "private Git object-store source restoration receipt changed"
                )
            refreshed_entries.append(_ClosurePackEntry(entry.name, restored))
        refreshed_sources.append(
            _ClosurePackSource(
                directory_fd=source.directory_fd,
                directory_identity=source.directory_identity,
                entries=tuple(refreshed_entries),
            )
        )

    capture.pack_sources = tuple(refreshed_sources)
    capture.pack_links_active = False
    for bootstrap in bootstraps:
        refreshed_entries = []
        for entry in bootstrap.entries:
            restored = restored_sources.get((bootstrap.source_fd, entry.name))
            if restored is None:
                raise GitAuthorityError(
                    "private Git pack bootstrap restoration receipt is missing"
                )
            if (
                restored[:3] + restored[4:6]
                != entry.identity[:3] + entry.identity[4:6]
                or entry.identity[3] not in {restored[3], restored[3] + 1}
            ):
                raise GitAuthorityError(
                    "private Git pack bootstrap restoration receipt changed"
                )
            destination = _store_identity(
                os.stat(
                    entry.name,
                    dir_fd=bootstrap.destination_pack_fd,
                    follow_symlinks=False,
                )
            )
            if destination != restored:
                raise GitAuthorityError(
                    "private Git pack bootstrap restoration identity mismatch"
                )
            refreshed_entries.append(_PackEntry(entry.name, restored))
        bootstrap.entries = tuple(refreshed_entries)


def _handoff_active_pack_receipts_after_builder_seal(
    capture: _ClosureCapture,
    bootstraps: list[_PackBootstrap],
    active_sources: dict[tuple[int, str], tuple[int, ...]],
) -> None:
    expected_keys = {
        (source.directory_fd, entry.name)
        for source in capture.pack_sources
        for entry in source.entries
    }
    if set(active_sources) != expected_keys:
        raise GitAuthorityError(
            "private Git object-store active source receipt set changed"
        )
    for source in capture.pack_sources:
        for entry in source.entries:
            active = active_sources[(source.directory_fd, entry.name)]
            if (
                active[:3] + active[4:6]
                != entry.identity[:3] + entry.identity[4:6]
                or active[3] != entry.identity[3] + 1
            ):
                raise GitAuthorityError(
                    "private Git object-store active source receipt changed"
                )
    for bootstrap in bootstraps:
        refreshed_entries: list[_PackEntry] = []
        for entry in bootstrap.entries:
            active = active_sources.get((bootstrap.source_fd, entry.name))
            if active is None:
                raise GitAuthorityError(
                    "private Git pack bootstrap active source receipt is missing"
                )
            if (
                active[:3] + active[4:6]
                != entry.identity[:3] + entry.identity[4:6]
                or active[3] != entry.identity[3] + 1
            ):
                raise GitAuthorityError(
                    "private Git pack bootstrap active source receipt changed"
                )
            destination = _store_identity(
                os.stat(
                    entry.name,
                    dir_fd=bootstrap.destination_pack_fd,
                    follow_symlinks=False,
                )
            )
            if destination != active:
                raise GitAuthorityError(
                    "private Git pack bootstrap active identity mismatch"
                )
            refreshed_entries.append(_PackEntry(entry.name, active))
        bootstrap.entries = tuple(refreshed_entries)
        bootstrap.source_nlinks = tuple(
            (name, expected_nlink + 1)
            for name, expected_nlink in bootstrap.source_nlinks
        )


def _capture_requested_closure(source: Path, root_oid: str, root_type: str, object_format: str, limits: GitScanLimits, deadline: float, packed_reader: Callable[[str, str], tuple[str, bytes]] | None = None, packed_reader_close: Callable[..., None] | None = None) -> _ClosureCapture:
    """Read an exact closure from loose sources or a private, pinned pack bootstrap."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(source, flags)
        root_identity = _directory_identity(os.fstat(root_fd))
    except OSError as exc:
        raise GitAuthorityError("Git object-store is inaccessible") from exc
    prefixes: dict[str, tuple[int, tuple[int, int, int]]] = {}
    objects: list[_ClosureObject] = []
    seen: dict[str, str] = {}
    pending: list[tuple[str, str, int]] = []
    scheduled: dict[str, str] = {}
    retained_entries = 1
    retained_bytes = 0

    def schedule(oid: str, expected_type: str, depth: int) -> None:
        previous = seen.get(oid) or scheduled.get(oid)
        if previous is not None:
            if previous != expected_type:
                raise GitAuthorityError("Git object closure has inconsistent object type")
            return
        if len(seen) + len(scheduled) >= limits.max_entries:
            raise GitAuthorityError("Git object closure scheduled entry cap exceeded")
        scheduled[oid] = expected_type
        pending.append((oid, expected_type, depth))

    schedule(root_oid, root_type, 0)

    def retain(oid: str, expected_type: str, depth: int = 0) -> None:
        nonlocal retained_entries, retained_bytes
        _seal_deadline(deadline)
        previous = seen.get(oid)
        if previous is not None:
            if previous != expected_type:
                raise GitAuthorityError("Git object closure has inconsistent object type")
            return
        seen[oid] = expected_type
        prefix, name = oid[:2], oid[2:]
        if prefix not in prefixes:
            try:
                prefix_fd = os.open(prefix, flags, dir_fd=root_fd)
                prefix_identity = _directory_identity(os.fstat(prefix_fd))
            except OSError as exc:
                if packed_reader is None:
                    raise GitAuthorityError("requested Git object is absent from the primary object store") from exc
                _retain_packed(oid, expected_type, depth)
                return
            prefixes[prefix] = (prefix_fd, prefix_identity)
            retained_entries += 1
        if retained_entries >= limits.max_entries:
            raise GitAuthorityError("Git object closure entry seal cap exceeded")
        prefix_fd, _ = prefixes[prefix]
        try:
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=prefix_fd)
        except OSError as exc:
            if packed_reader is None:
                raise GitAuthorityError("requested Git object is absent from the primary object store") from exc
            _retain_packed(oid, expected_type, depth)
            return
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise GitAuthorityError("requested Git object is not a regular primary object")
            identity = _store_identity(metadata)
            compressed = _read_limited_fd(descriptor, identity[4], limits, deadline)
        finally:
            os.close(descriptor)
        retained_entries += 1
        retained_bytes += len(compressed)
        if retained_entries > limits.max_entries:
            raise GitAuthorityError("Git object closure entry seal cap exceeded")
        if retained_bytes > limits.max_total_bytes:
            raise GitAuthorityError("Git object closure aggregate seal cap exceeded")
        actual_type, payload = _decode_loose_object(compressed, oid, object_format, limits, deadline)
        if actual_type != expected_type:
            raise GitAuthorityError(f"expected {expected_type} object, received {actual_type!r}")
        objects.append(_ClosureObject(oid, prefix, name, identity, compressed))
        if actual_type == "commit":
            tree_oid = _commit_tree_oid(payload, object_format)
            schedule(tree_oid, "tree", depth)
        elif actual_type == "tree":
            for child_oid, child_type in _tree_children(payload, object_format, limits, depth=depth):
                schedule(child_oid, child_type, depth + 1)

    def _retain_packed(oid: str, expected_type: str, depth: int) -> None:
        nonlocal retained_entries, retained_bytes
        assert packed_reader is not None
        actual_type, payload = packed_reader(oid, expected_type)
        raw = f"{actual_type} {len(payload)}\0".encode("ascii") + payload
        import zlib
        compressed = zlib.compress(raw)
        retained_entries += 1
        retained_bytes += len(compressed)
        if retained_entries > limits.max_entries:
            raise GitAuthorityError("Git object closure entry seal cap exceeded")
        if retained_bytes > limits.max_total_bytes:
            raise GitAuthorityError("Git object closure aggregate seal cap exceeded")
        objects.append(_ClosureObject(oid, oid[:2], oid[2:], None, compressed))
        if actual_type == "commit":
            schedule(_commit_tree_oid(payload, object_format), "tree", depth)
        elif actual_type == "tree":
            for child_oid, child_type in _tree_children(payload, object_format, limits, depth=depth):
                schedule(child_oid, child_type, depth + 1)

    capture: _ClosureCapture | None = None
    try:
        while pending:
            oid, expected_type, depth = pending.pop()
            scheduled.pop(oid, None)
            if depth > _MAX_TREE_DEPTH:
                raise GitAuthorityError("Git tree depth limit exceeded")
            retain(oid, expected_type, depth)
        capture = _ClosureCapture(source, root_identity, root_fd, prefixes, tuple(objects))
    except BaseException as primary:
        cleanup_errors: list[BaseException] = []
        try:
            temporary_capture = _ClosureCapture(
                source, root_identity, root_fd, prefixes, ()
            )
            temporary_capture.close()
        except BaseException as cleanup:
            cleanup_errors.append(cleanup)
        if packed_reader_close is not None:
            try:
                packed_reader_close(force_terminate=True)
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
        cleanup_error = _aggregate_errors(cleanup_errors)
        if cleanup_error is not None:
            combined = _combine_primary_and_cleanup(primary, cleanup_error)
            if combined is primary:
                raise primary
            raise combined from primary
        raise
    if packed_reader_close is not None:
        try:
            packed_reader_close()
        except BaseException as primary:
            try:
                capture.close()
            except BaseException as cleanup:
                combined = _combine_primary_and_cleanup(primary, cleanup)
                if combined is primary:
                    raise primary
                raise combined from primary
            raise
    assert capture is not None
    return capture


def _read_limited_fd(descriptor: int, expected_size: int, limits: GitScanLimits, deadline: float) -> bytes:
    if expected_size < 0 or expected_size > limits.max_total_bytes:
        raise GitAuthorityError("Git object closure aggregate seal cap exceeded")
    chunks: list[bytes] = []
    remaining = expected_size
    while remaining:
        _seal_deadline(deadline)
        try:
            chunk = os.read(descriptor, min(65_536, remaining))
        except OSError as exc:
            raise GitAuthorityError("requested Git object could not be read") from exc
        if not chunk:
            raise GitAuthorityError("requested Git object was truncated during sealing")
        chunks.append(chunk)
        remaining -= len(chunk)
    try:
        if os.read(descriptor, 1):
            raise GitAuthorityError("requested Git object changed during sealing")
    except OSError as exc:
        raise GitAuthorityError("requested Git object could not be read") from exc
    return b"".join(chunks)


def _decode_loose_object(compressed: bytes, expected_oid: str, object_format: str, limits: GitScanLimits, deadline: float) -> tuple[str, bytes]:
    """Incrementally decode a loose object, parsing the declared size before body growth."""
    try:
        import zlib
        decoder = zlib.decompressobj()
        raw = bytearray()
        header_end: int | None = None
        object_type: str | None = None
        declared_size: int | None = None
        pending = compressed
        while pending:
            _seal_deadline(deadline)
            cap = (declared_size + 256 if declared_size is not None else 64)
            piece = decoder.decompress(pending, cap - len(raw) + 1)
            raw.extend(piece)
            pending = decoder.unconsumed_tail
            if header_end is None:
                header_end = raw.find(b"\0")
                if header_end >= 0:
                    header = bytes(raw[:header_end])
                    kind, size = header.split(b" ", 1)
                    object_type = kind.decode("ascii")
                    declared_size = int(size.decode("ascii"))
                    if declared_size < 0 or declared_size > limits.max_total_bytes:
                        raise GitAuthorityError("loose Git object exceeds aggregate limit")
                    if object_type == "blob" and declared_size > limits.max_blob_bytes:
                        raise GitAuthorityError("loose Git blob exceeds configured blob limit")
                    if object_type not in {"commit", "tree", "blob"}:
                        raise GitAuthorityError("requested Git object has unsupported type")
            if header_end is None and len(raw) > 64:
                raise GitAuthorityError("loose Git object header exceeds configured limit")
            if header_end is not None and declared_size is not None and len(raw) > header_end + 1 + declared_size:
                raise GitAuthorityError("loose Git object exceeds declared bounded limit")
            if not pending and decoder.unused_data:
                raise GitAuthorityError("requested Git object is corrupt")
            if not pending and decoder.eof:
                break
            if not pending:
                pending = b""
        if not decoder.eof or header_end is None or object_type is None or declared_size is None:
            raise ValueError
        raw_bytes = bytes(raw)
        payload = raw_bytes[header_end + 1:]
        if len(payload) != declared_size:
            raise ValueError
    except GitAuthorityError:
        raise
    except (UnicodeDecodeError, ValueError, zlib.error) as exc:
        raise GitAuthorityError("requested Git object is corrupt") from exc
    if hashlib.new(object_format, raw_bytes).hexdigest() != expected_oid:
        raise GitAuthorityError("requested Git object bytes do not reproduce their OID")
    return object_type, payload


def _commit_tree_oid(payload: bytes, object_format: str) -> str:
    for line in payload.split(b"\n"):
        if line == b"":
            break
        if line.startswith(b"tree "):
            try:
                oid = line[5:].decode("ascii")
            except UnicodeDecodeError as exc:
                raise GitAuthorityError("commit tree OID is malformed") from exc
            _require_full_oid(oid, object_format, "tree")
            return oid
    raise GitAuthorityError("commit has no exact tree OID")


def _tree_children(payload: bytes, object_format: str, limits: GitScanLimits, *, depth: int) -> tuple[tuple[str, str], ...]:
    if depth > _MAX_TREE_DEPTH:
        raise GitAuthorityError("Git tree depth limit exceeded")
    oid_width = 20 if object_format == "sha1" else 32
    offset = 0
    children: list[tuple[str, str]] = []
    while offset < len(payload):
        if len(children) >= limits.max_entries:
            raise GitAuthorityError("Git tree entry limit exceeded")
        separator = payload.find(b" ", offset)
        nul = payload.find(b"\0", separator + 1)
        if separator < 0 or nul < 0 or nul + 1 + oid_width > len(payload):
            raise GitAuthorityError("Git tree object is malformed")
        mode = payload[offset:separator]
        name = payload[separator + 1:nul]
        if not name or b"/" in name or name in {b".", b".."}:
            raise GitAuthorityError("Git tree object has an invalid path component")
        oid = payload[nul + 1:nul + 1 + oid_width].hex()
        if mode == b"40000":
            child_type = "tree"
        elif mode in _ALLOWED_MODES:
            child_type = "blob"
        else:
            raise GitAuthorityError("Git tree object has unsupported entry mode")
        children.append((oid, child_type))
        offset = nul + 1 + oid_width
    return tuple(children)


def _verify_captured_pack_sources(
    capture: _ClosureCapture,
    deadline: float,
) -> None:
    for source in capture.pack_sources:
        _seal_deadline(deadline)
        try:
            current_directory = _owned_descriptor_identity(
                os.fstat(source.directory_fd)
            )
        except OSError as exc:
            raise GitAuthorityError(
                "Git pack directory changed during source snapshot"
            ) from exc
        if current_directory != source.directory_identity:
            raise GitAuthorityError(
                "Git pack directory changed during source snapshot"
            )
        for entry in source.entries:
            _seal_deadline(deadline)
            try:
                current = _store_identity(
                    os.stat(
                        entry.name,
                        dir_fd=source.directory_fd,
                        follow_symlinks=False,
                    )
                )
            except OSError as exc:
                raise GitAuthorityError(
                    "requested Git pack/index pair changed during copied-store construction"
                ) from exc
            expected_nlink = entry.identity[3] + int(capture.pack_links_active)
            if (
                current[:3] + current[4:6]
                != entry.identity[:3] + entry.identity[4:6]
                or current[3] != expected_nlink
            ):
                raise GitAuthorityError(
                    "requested Git pack/index pair changed during copied-store construction"
                )


def _copy_requested_closure(
    capture: _ClosureCapture,
    builder: _PrivateClosureBuilder,
    limits: GitScanLimits,
    deadline: float,
) -> None:
    _verify_requested_closure(capture, limits, deadline)
    for source in capture.pack_sources:
        for entry in source.entries:
            _seal_deadline(deadline)
            builder.link_pack_entry(
                source_directory_fd=source.directory_fd,
                source_name=entry.name,
                destination_name=entry.name,
                expected_source_identity=entry.identity,
            )
    capture.pack_links_active = bool(capture.pack_sources)
    for prefix in sorted({entry.prefix for entry in capture.objects}):
        _seal_deadline(deadline)
        builder.ensure_loose_prefix(prefix)
    copied_bytes = 0
    for entry in capture.objects:
        _seal_deadline(deadline)
        copied_bytes += len(entry.compressed)
        if copied_bytes > limits.max_total_bytes:
            raise GitAuthorityError("Git object closure aggregate seal cap exceeded")
        builder.write_loose_object(
            prefix=entry.prefix,
            name=entry.name,
            compressed=entry.compressed,
        )
    _verify_requested_closure(capture, limits, deadline)


def _retain_private_closure(
    builder: _PrivateClosureBuilder,
    capture: _ClosureCapture,
    limits: GitScanLimits,
    deadline: float,
) -> _PrivateClosureStore:
    try:
        return builder.seal(
            expected_inventory=capture,
            limits=limits,
            deadline=deadline,
        )
    except BaseException as exc:
        if builder.sealed:
            raise
        if isinstance(exc, GitAuthorityError):
            raise
        raise GitAuthorityError(
            "private Git object-store descriptor seal failed"
        ) from exc


def _object_format_for_oid(oid: str) -> str:
    if len(oid) == 40:
        return "sha1"
    if len(oid) == 64:
        return "sha256"
    raise GitAuthorityError("private Git object OID width is unsupported")


def _verify_private_closure(
    private: _PrivateClosureStore, limits: GitScanLimits, deadline: float
) -> None:
    _seal_deadline(deadline)
    if private.closed:
        raise GitAuthorityError("private Git copied-closure is closed")
    if _private_directory_identity(
        os.fstat(private.root_fd), expected_mode=private.root_identity.mode
    ) != private.root_identity:
        raise GitAuthorityError("private Git copied-closure root changed")
    if _private_directory_identity(
        os.fstat(private.objects_fd), expected_mode=private.objects_identity.mode
    ) != private.objects_identity:
        raise GitAuthorityError("private Git copied-closure objects directory changed")
    if _private_directory_identity(
        os.fstat(private.pack_fd), expected_mode=private.pack_identity.mode
    ) != private.pack_identity:
        raise GitAuthorityError("private Git copied-closure pack directory changed")
    if _directory_inventory_at(
        private.root_fd,
        label="private Git copied-closure root",
    ) != ("objects",):
        raise GitAuthorityError("private Git copied-closure root inventory changed")
    if _private_directory_identity(
        os.stat(
            "objects",
            dir_fd=private.root_fd,
            follow_symlinks=False,
        ),
        expected_mode=private.objects_identity.mode,
    ) != private.objects_identity:
        raise GitAuthorityError("private Git copied-closure objects name changed")
    expected_object_names = tuple(sorted(("pack", *private.prefixes)))
    if _directory_inventory_at(
        private.objects_fd,
        label="private Git copied-closure objects directory",
    ) != expected_object_names:
        raise GitAuthorityError("private Git copied-closure objects inventory changed")
    if _private_directory_identity(
        os.stat(
            "pack",
            dir_fd=private.objects_fd,
            follow_symlinks=False,
        ),
        expected_mode=private.pack_identity.mode,
    ) != private.pack_identity:
        raise GitAuthorityError("private Git copied-closure pack name changed")
    if _directory_inventory_at(
        private.pack_fd,
        label="private Git copied-closure pack directory",
    ) != tuple(sorted(private.pack_entries)):
        raise GitAuthorityError("private Git copied-closure pack inventory changed")
    for prefix, (prefix_fd, identity) in private.prefixes.items():
        _seal_deadline(deadline)
        if _private_directory_identity(
            os.fstat(prefix_fd), expected_mode=identity.mode
        ) != identity:
            raise GitAuthorityError("private Git copied-closure prefix directory changed")
        if _private_directory_identity(
            os.stat(
                prefix,
                dir_fd=private.objects_fd,
                follow_symlinks=False,
            ),
            expected_mode=identity.mode,
        ) != identity:
            raise GitAuthorityError("private Git copied-closure prefix name changed")
        expected = tuple(
            sorted(
                entry.name
            for entry in private.entries
            if entry.prefix == prefix
            )
        )
        if _directory_inventory_at(
            prefix_fd,
            label="private Git copied-closure prefix",
        ) != expected:
            raise GitAuthorityError("private Git copied-closure prefix inventory changed")
    for entry in private.entries:
        _seal_deadline(deadline)
        prefix_fd = private.prefixes[entry.prefix][0]
        descriptor_identity: _OwnedDescriptorIdentity | None = None
        try:
            descriptor = os.open(
                entry.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=prefix_fd,
            )
        except OSError as exc:
            raise GitAuthorityError("private Git copied object is unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            descriptor_identity = _owned_descriptor_identity(metadata)
            if _store_identity(metadata) != entry.identity:
                raise GitAuthorityError("private Git copied object identity changed")
            compressed = _read_limited_fd(descriptor, entry.identity[4], limits, deadline)
        finally:
            if descriptor_identity is None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            else:
                _close_owned_descriptor(
                    descriptor,
                    descriptor_identity,
                    label="private Git copied object",
                )
        if hashlib.sha256(compressed).hexdigest() != entry.compressed_sha256:
            raise GitAuthorityError("private Git copied object SHA-256 changed")
        object_type, payload = _decode_loose_object(
            compressed, entry.oid, _object_format_for_oid(entry.oid), limits, deadline
        )
        if object_type != entry.object_type:
            raise GitAuthorityError("private Git copied object type changed")
        if hashlib.sha256(payload).hexdigest() != entry.payload_sha256:
            raise GitAuthorityError("private Git copied object payload SHA-256 changed")
    for name, entry in sorted(private.pack_entries.items()):
        if entry.name != name:
            raise GitAuthorityError("private Git copied pack receipt changed")
        _verify_owned_file_entry_at(
            private.pack_fd,
            entry,
            label="private Git copied pack entry",
            deadline=deadline,
        )
    for key, active in sorted(
        private.active_source_receipts.items(),
        key=lambda item: (item[0][1], item[0][0]),
    ):
        source_fd, source_name = key
        baseline = private.source_nlinks.get(key)
        if baseline is None:
            raise GitAuthorityError(
                "private Git copied pack source receipt is unavailable"
            )
        initial, expected_nlink = baseline
        current = _store_identity(
            os.stat(
                source_name,
                dir_fd=source_fd,
                follow_symlinks=False,
            )
        )
        if (
            current != active
            or current[:3] + current[4:6] != initial[:3] + initial[4:6]
            or current[3] != expected_nlink + 1
        ):
            raise GitAuthorityError(
                "private Git copied pack source hardlink receipt changed"
            )


def _confirm_private_closure_source_nlinks_restored(
    private: _PrivateClosureStore,
) -> dict[tuple[int, str], tuple[int, ...]]:
    restored: dict[tuple[int, str], tuple[int, ...]] = {}
    for key, (initial, expected_nlink) in sorted(
        private.source_nlinks.items(),
        key=lambda item: (item[0][1], item[0][0]),
    ):
        source_fd, source_name = key
        current = _store_identity(
            os.stat(
                source_name,
                dir_fd=source_fd,
                follow_symlinks=False,
            )
        )
        if (
            current[:3] + current[4:6] != initial[:3] + initial[4:6]
            or current[3] != expected_nlink
        ):
            raise GitAuthorityError(
                "private Git object-store source nlink was not restored"
            )
        restored[key] = current
    return restored


def _verify_requested_closure(capture: _ClosureCapture, limits: GitScanLimits, deadline: float) -> None:
    _seal_deadline(deadline)
    try:
        if _directory_identity(os.stat(capture.source, follow_symlinks=False)) != capture.root_identity:
            raise GitAuthorityError("Git object-store changed during source snapshot")
    except OSError as exc:
        raise GitAuthorityError("Git object-store changed during source snapshot") from exc
    for prefix, (descriptor, identity) in capture.prefixes.items():
        _seal_deadline(deadline)
        if _directory_identity(os.fstat(descriptor)) != identity:
            raise GitAuthorityError("Git object-store changed during source snapshot")
        for entry in (item for item in capture.objects if item.prefix == prefix):
            if entry.identity is None:
                continue
            try:
                object_fd = os.open(entry.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            except OSError as exc:
                raise GitAuthorityError("requested Git object changed during source snapshot") from exc
            try:
                if _store_identity(os.fstat(object_fd)) != entry.identity:
                    raise GitAuthorityError("requested Git object changed during source snapshot")
                if _read_limited_fd(object_fd, entry.identity[4], limits, deadline) != entry.compressed:
                    raise GitAuthorityError("requested Git object changed during source snapshot")
            finally:
                os.close(object_fd)
    _verify_captured_pack_sources(capture, deadline)


def _tree_output_cap(limits: GitScanLimits) -> int:
    return (limits.max_entries + 1) * (limits.max_path_bytes + 128)


def _batch_output_cap(limits: GitScanLimits) -> int:
    return limits.max_total_bytes + limits.max_entries * 128


@dataclass(frozen=True)
class _ProcessCleanupReceipt:
    signal_attempts: int
    group_cleanup_confirmed: bool
    leader_reaped: bool
    streams_closed: bool


@dataclass
class _ProcessTermination:
    process: subprocess.Popen[bytes]
    pgid: int
    signal_attempts: int = 0
    group_signal_confirmed: bool = False
    group_absence_confirmed: bool = False
    leader_reaped: bool = False
    on_reap: Callable[[object], None] | None = None

    def cleanup_receipt(self) -> _ProcessCleanupReceipt:
        return _ProcessCleanupReceipt(
            signal_attempts=self.signal_attempts,
            group_cleanup_confirmed=(
                self.group_signal_confirmed or self.group_absence_confirmed
            ),
            leader_reaped=self.leader_reaped,
            streams_closed=all(
                stream is None or stream.closed
                for stream in (self.process.stdout, self.process.stderr)
            ),
        )

    def _reap_leader(self) -> GitAuthorityError | None:
        deadline = time.monotonic() + 1.0
        if not hasattr(os, "wait4"):
            return GitAuthorityError("exact Git child CPU receipt is unavailable")
        while True:
            try:
                pid, status, usage = os.wait4(self.process.pid, os.WNOHANG)
            except ChildProcessError as exc:
                error = GitAuthorityError("Git child exact reap receipt is unavailable")
                error.__cause__ = exc
                return error
            except OSError as exc:
                error = GitAuthorityError("Git child exact reap receipt is unavailable")
                error.__cause__ = exc
                return error
            if pid == self.process.pid:
                try:
                    self.process.returncode = os.waitstatus_to_exitcode(status)
                    if self.on_reap is not None:
                        self.on_reap(usage)
                except BaseException as exc:
                    error = GitAuthorityError("Git child exact reap receipt is inconsistent")
                    error.__cause__ = exc
                    return error
                self.leader_reaped = True
                return None
            if time.monotonic() >= deadline:
                return GitAuthorityError("Git child reap was not confirmed after bounded cleanup")
            time.sleep(min(0.01, deadline - time.monotonic()))

    def _observe_leader_exit(self) -> bool | GitAuthorityError:
        """Observe terminal status without giving up the owned PGID before cleanup."""
        deadline = time.monotonic() + 1.0
        while True:
            try:
                status = os.waitid(
                    os.P_PID,
                    self.process.pid,
                    os.WEXITED | os.WNOWAIT | os.WNOHANG,
                )
            except (AttributeError, OSError) as exc:
                error = GitAuthorityError(
                    "Git child status could not be observed before exact reap"
                )
                error.__cause__ = exc
                return error
            if status is not None:
                return status.si_code != os.CLD_EXITED or status.si_status != 0
            if time.monotonic() >= deadline:
                return GitAuthorityError(
                    "Git child status was not observed before bounded cleanup"
                )
            time.sleep(min(0.01, deadline - time.monotonic()))

    def terminate(self) -> GitAuthorityError | None:
        """Signal only while direct-child ownership pins the PGID, then reap boundedly."""
        if self.leader_reaped:
            if self.group_signal_confirmed or self.group_absence_confirmed:
                return None
            return GitAuthorityError("Git process-group cleanup was not confirmed")

        signal_error: OSError | None = None
        while (
            self.signal_attempts < 2
            and not self.group_signal_confirmed
            and not self.group_absence_confirmed
        ):
            self.signal_attempts += 1
            try:
                os.killpg(self.pgid, signal.SIGKILL)
            except ProcessLookupError:
                self.group_absence_confirmed = True
            except OSError as exc:
                signal_error = signal_error or exc
            else:
                self.group_signal_confirmed = True

        cleanup_error: GitAuthorityError | None = None
        if not self.group_signal_confirmed and not self.group_absence_confirmed:
            cleanup_error = GitAuthorityError(
                "Git process-group cleanup was not confirmed"
            )
            cleanup_error.__cause__ = signal_error

        reap_error = self._reap_leader()
        absence_error: GitAuthorityError | None = None
        if self.group_signal_confirmed:
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    os.killpg(self.pgid, 0)
                except ProcessLookupError:
                    self.group_absence_confirmed = True
                    break
                except OSError as exc:
                    absence_error = GitAuthorityError(
                        "Git process-group absence could not be confirmed"
                    )
                    absence_error.__cause__ = exc
                    break
                if time.monotonic() >= deadline:
                    absence_error = GitAuthorityError(
                        "Git process-group absence was not confirmed after bounded cleanup"
                    )
                    break
                time.sleep(min(0.01, deadline - time.monotonic()))
        result: BaseException | None = cleanup_error
        for error in (reap_error, absence_error):
            if error is not None:
                result = error if result is None else _combine_primary_and_cleanup(result, error)
        return result if isinstance(result, GitAuthorityError) else result


def _close_process_streams(process: subprocess.Popen[bytes]) -> GitAuthorityError | None:
    cleanup_error: GitAuthorityError | None = None
    for stream_name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except BaseException as exc:
                stream_error = GitAuthorityError(
                    f"Git {stream_name} stream cleanup failed"
                )
                stream_error.__cause__ = exc
                cleanup_error = (
                    stream_error
                    if cleanup_error is None
                    else _combine_primary_and_cleanup(cleanup_error, stream_error)
                )
    return cleanup_error


def _read_process_streams(process: subprocess.Popen[bytes], timeout: float, stdout_cap: int, stderr_cap: int, termination: _ProcessTermination) -> tuple[bytes, bytes]:
    assert process.stdout is not None and process.stderr is not None
    selector: selectors.BaseSelector | None = None
    result: tuple[bytes, bytes] | None = None
    primary: BaseException | None = None
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout_cap))
        selector.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr_cap))
        output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GitAuthorityError("Git command timed out")
            events = selector.select(remaining)
            if not events:
                continue
            for key, _ in events:
                stream_name, cap = key.data
                collected = output[stream_name]
                chunk = os.read(key.fileobj.fileno(), min(65_536, cap - len(collected) + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if len(chunk) > cap - len(collected):
                    raise GitAuthorityError(f"Git {stream_name} cap exceeded")
                collected.extend(chunk)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GitAuthorityError("Git command timed out")
            try:
                status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            except OSError as exc:
                raise GitAuthorityError("Git child status could not be observed before reap") from exc
            if status is None:
                time.sleep(min(0.01, remaining))
                continue
            nonzero = status.si_code != os.CLD_EXITED or status.si_status != 0
            if nonzero:
                cleanup_error = termination.terminate()
                if cleanup_error is not None:
                    raise GitAuthorityError(f"Git child failed and cleanup was not confirmed: {cleanup_error}")
                raise GitAuthorityError("Git command failed before reap")
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise GitAuthorityError("Git command timed out") from exc
            termination.leader_reaped = True
            break
        result = bytes(output["stdout"]), bytes(output["stderr"])
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GitAuthorityError)):
            primary = exc
        else:
            primary = GitAuthorityError("Git command I/O setup or stream read failed")

    cleanup_error: GitAuthorityError | None = None
    if selector is not None:
        try:
            selector.close()
        except BaseException as exc:
            cleanup_error = GitAuthorityError("Git selector cleanup failed")
            cleanup_error.__cause__ = exc

    if primary is not None:
        if isinstance(primary, (KeyboardInterrupt, SystemExit)) and cleanup_error is None:
            raise primary
        combined = _combine_primary_and_cleanup(primary, cleanup_error)
        if combined is primary:
            raise primary
        raise combined from primary
    if cleanup_error is not None:
        raise cleanup_error
    assert result is not None
    return result


def _read_verified_blobs(runner: _GitRunner, records: tuple[tuple[int, str, str], ...], object_format: str) -> tuple[GitBlobSnapshot, ...]:
    requested = b"".join(oid.encode("ascii") + b"\n" for _, oid, _ in records)
    output = runner.run(("cat-file", "--batch"), input_data=requested, stdout_cap=_batch_output_cap(runner.limits))
    offset = total_bytes = 0
    blobs: list[GitBlobSnapshot] = []
    for mode, expected_oid, path in records:
        newline = output.find(b"\n", offset)
        if newline < 0:
            raise GitAuthorityError("truncated Git cat-file batch header")
        header = output[offset:newline]
        offset = newline + 1
        try:
            actual_oid_bytes, object_type, size_bytes = header.split(b" ")
            actual_oid = actual_oid_bytes.decode("ascii")
            size = int(size_bytes.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitAuthorityError("malformed Git cat-file batch header") from exc
        if actual_oid != expected_oid or object_type != b"blob" or size < 0:
            raise GitAuthorityError("Git cat-file OID/type/size does not match tree record")
        if size > runner.limits.max_blob_bytes:
            raise GitAuthorityError("Git blob exceeds configured blob limit")
        end = offset + size
        if end >= len(output) or output[end:end + 1] != b"\n":
            raise GitAuthorityError("truncated Git cat-file batch blob")
        data = output[offset:end]
        offset = end + 1
        total_bytes += size
        if total_bytes > runner.limits.max_total_bytes:
            raise GitAuthorityError("Git blob aggregate limit exceeded")
        calculated_oid = hashlib.new(object_format, f"blob {len(data)}\0".encode("ascii") + data).hexdigest()
        if calculated_oid != expected_oid:
            raise GitAuthorityError("Git blob bytes do not reproduce their object OID")
        blobs.append(GitBlobSnapshot(path=path, mode=mode, blob_oid=expected_oid, sha256=hashlib.sha256(data).hexdigest(), data=data))
    if offset != len(output):
        raise GitAuthorityError("malformed trailing Git cat-file batch data")
    return tuple(blobs)
