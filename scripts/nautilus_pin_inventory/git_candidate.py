"""Build immutable candidate Git trees through a private temporary index.

This module deliberately creates Git objects but never moves a ref, changes
the caller's index, or reads update bytes back from the worktree.
"""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import secrets
import stat
import struct
import subprocess
import unicodedata

from .git_source import GitAuthorityError, GitBlobSnapshot, GitTreeSnapshot


_REGULAR_MODES = frozenset({0o100644, 0o100755})
_GIT_TIMEOUT_SECONDS = 30.0
_IndexIdentity = tuple[int, int, int, int, int, int, int, int, str]
_IN_MODIFY = 0x00000002
_IN_ATTRIB = 0x00000004
_IN_CLOSE_WRITE = 0x00000008
_IN_MOVED_FROM = 0x00000040
_IN_MOVED_TO = 0x00000080
_IN_CREATE = 0x00000100
_IN_DELETE = 0x00000200
_IN_Q_OVERFLOW = 0x00004000
_IN_CLOEXEC = os.O_CLOEXEC
_IN_NONBLOCK = os.O_NONBLOCK
_INDEX_WATCH_MASK = (
    _IN_MODIFY
    | _IN_ATTRIB
    | _IN_CLOSE_WRITE
    | _IN_MOVED_FROM
    | _IN_MOVED_TO
    | _IN_CREATE
    | _IN_DELETE
)
_INOTIFY_EVENT = struct.Struct("iIII")
_RENAME_NOREPLACE = 1


class CandidateAuthorityError(ValueError):
    """Candidate-tree authority could not be established or was disturbed."""


@dataclass(frozen=True)
class RetentionPolicy:
    """Hard bounds for evidence retained below one caller-owned private root.

    Candidate construction never evicts or deletes retained or foreign state.
    Once either bound is exhausted, an operator must archive/reclaim evidence
    outside this builder before a later construction can begin.
    """

    max_entries: int = 64
    max_bytes: int = 1_073_741_824
    operation_reserve_bytes: int = 67_108_864
    minimum_free_bytes: int = 67_108_864

    def __post_init__(self) -> None:
        for label, value, minimum in (
            ("retention max entries", self.max_entries, 1),
            ("retention max bytes", self.max_bytes, 1),
            ("retention operation reserve bytes", self.operation_reserve_bytes, 1),
            ("retention minimum free bytes", self.minimum_free_bytes, 0),
        ):
            if type(value) is not int or value < minimum:
                raise CandidateAuthorityError(f"{label} is invalid")
        if self.operation_reserve_bytes > self.max_bytes:
            raise CandidateAuthorityError("retention operation reserve exceeds byte capacity")


@dataclass(frozen=True)
class RetainedEvidenceReceipt:
    """Operator handoff for completed evidence this builder never deletes."""

    directory_name: str
    index_bytes: int | None
    index_sha256: str | None

    def __post_init__(self) -> None:
        name = _require_exact_string(self.directory_name, label="retained evidence directory name")
        if (
            "/" in name
            or "\\" in name
            or not name.startswith("p1-u00-candidate-")
            or "-retained-" not in name
        ):
            raise CandidateAuthorityError("retained evidence directory name is invalid")
        if (self.index_bytes is None) != (self.index_sha256 is None):
            raise CandidateAuthorityError("retained index receipt is incomplete")
        if self.index_bytes is not None:
            if type(self.index_bytes) is not int or self.index_bytes < 0:
                raise CandidateAuthorityError("retained index byte count is invalid")
            _require_sha256(self.index_sha256, label="retained index SHA-256")


@dataclass(frozen=True)
class _RetentionUsage:
    entries: int
    bytes: int
    free_bytes: int


def _require_exact_string(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise CandidateAuthorityError(f"{label} must be a string")
    return value


def _require_full_oid(value: object, *, label: str, width: int | None = None) -> str:
    oid = _require_exact_string(value, label=label)
    valid_width = len(oid) in {40, 64} if width is None else len(oid) == width
    if not valid_width or any(character not in "0123456789abcdef" for character in oid):
        raise CandidateAuthorityError(f"{label} must be a full lowercase Git OID")
    return oid


def _require_sha256(value: object, *, label: str) -> str:
    digest = _require_exact_string(value, label=label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CandidateAuthorityError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _canonical_path(value: object, *, label: str = "path") -> str:
    path = _require_exact_string(value, label=label)
    unsafe = (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or unicodedata.normalize("NFC", path) != path
    )
    if not unsafe:
        unsafe = any(
            component in {"", ".", ".."}
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in component)
            for component in path.split("/")
        )
    if unsafe:
        raise CandidateAuthorityError(f"{label} is not a canonical relative POSIX path")
    try:
        if len(path.encode("utf-8")) > 4096:
            raise CandidateAuthorityError(f"{label} is too long")
    except UnicodeEncodeError as exc:
        raise CandidateAuthorityError(f"{label} is not valid UTF-8") from exc
    return path


def _require_regular_mode(value: object, *, label: str = "mode") -> int:
    if type(value) is not int or value not in _REGULAR_MODES:
        raise CandidateAuthorityError(f"{label} must be an allowed regular-file mode")
    return value


@dataclass(frozen=True)
class CandidatePathReceipt:
    path: str
    mode: int
    blob_oid: str
    sha256: str

    def __post_init__(self) -> None:
        _canonical_path(self.path)
        _require_regular_mode(self.mode)
        _require_full_oid(self.blob_oid, label="blob OID")
        _require_sha256(self.sha256, label="SHA-256")


def _validate_receipts(value: object, *, oid_width: int) -> tuple[CandidatePathReceipt, ...]:
    if type(value) is not tuple or any(type(receipt) is not CandidatePathReceipt for receipt in value):
        raise CandidateAuthorityError("path receipts must be an exact tuple of CandidatePathReceipt values")
    receipts = value
    paths = tuple(receipt.path for receipt in receipts)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise CandidateAuthorityError("path receipts must be sorted and unique")
    for receipt in receipts:
        _require_full_oid(receipt.blob_oid, label="receipt blob OID", width=oid_width)
    return receipts


@dataclass(frozen=True)
class TreeBuildReceipt:
    expected_parent_commit_oid: str
    expected_parent_tree_oid: str
    tree_oid: str
    paths: tuple[CandidatePathReceipt, ...]

    def __post_init__(self) -> None:
        parent = _require_full_oid(self.expected_parent_commit_oid, label="expected parent commit OID")
        _require_full_oid(self.expected_parent_tree_oid, label="expected parent tree OID", width=len(parent))
        _require_full_oid(self.tree_oid, label="tree OID", width=len(parent))
        _validate_receipts(self.paths, oid_width=len(parent))


@dataclass(frozen=True)
class CandidateTreeReceipt:
    expected_parent_commit_oid: str
    expected_parent_tree_oid: str
    source_tree_oid: str
    final_tree_oid: str
    inventory_blob_oid: str
    inventory_sha256: str
    paths: tuple[CandidatePathReceipt, ...]

    def __post_init__(self) -> None:
        parent = _require_full_oid(self.expected_parent_commit_oid, label="expected parent commit OID")
        for label, oid in (
            ("expected parent tree OID", self.expected_parent_tree_oid),
            ("source tree OID", self.source_tree_oid),
            ("final tree OID", self.final_tree_oid),
            ("inventory blob OID", self.inventory_blob_oid),
        ):
            _require_full_oid(oid, label=label, width=len(parent))
        _require_sha256(self.inventory_sha256, label="inventory SHA-256")
        _validate_receipts(self.paths, oid_width=len(parent))


@dataclass(frozen=True)
class _RepositoryState:
    head: str
    head_file: "_ObservedFile"
    refs: bytes
    index_file: "_ObservedFile"
    status: bytes
    worktree_paths: tuple["_ObservedFile", ...]


@dataclass(frozen=True)
class _ObservedFile:
    path: str
    identity: tuple[int, int, int, int, int, int, int, int] | None
    sha256: str | None


@dataclass
class _OwnedOperation:
    parent_fd: int
    parent_identity: tuple[int, int, int, int, int, int]
    name: str
    fd: int
    identity: tuple[int, int, int, int, int, int]
    closed: bool = False

    @property
    def index_path(self) -> Path:
        return Path(f"/proc/self/fd/{self.fd}/index")


@dataclass(frozen=True)
class _IndexedGitResult:
    """Git output and the exact private-index state observed before return."""

    stdout: bytes
    index_identity: _IndexIdentity


@dataclass(frozen=True)
class _IndexNamespaceEvent:
    mask: int
    cookie: int
    name: bytes


@dataclass
class _IndexNamespaceWatch:
    """Fail-closed Linux namespace-event receipt for one indexed Git command."""

    fd: int
    closed: bool = False

    @classmethod
    def open(cls, directory_fd: int) -> "_IndexNamespaceWatch":
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            inotify_init1 = libc.inotify_init1
            inotify_add_watch = libc.inotify_add_watch
        except AttributeError as exc:
            raise CandidateAuthorityError("Linux private-index event authority is unavailable") from exc
        inotify_init1.argtypes = (ctypes.c_int,)
        inotify_init1.restype = ctypes.c_int
        inotify_add_watch.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
        inotify_add_watch.restype = ctypes.c_int
        watch_fd = inotify_init1(_IN_CLOEXEC | _IN_NONBLOCK)
        if watch_fd < 0:
            error = ctypes.get_errno()
            raise CandidateAuthorityError("Linux private-index event authority is unavailable") from OSError(
                error, os.strerror(error)
            )
        try:
            watched = inotify_add_watch(
                watch_fd,
                os.fsencode(f"/proc/self/fd/{directory_fd}"),
                _INDEX_WATCH_MASK,
            )
            if watched < 0:
                error = ctypes.get_errno()
                raise CandidateAuthorityError(
                    "Linux private-index event authority is unavailable"
                ) from OSError(error, os.strerror(error))
            return cls(watch_fd)
        except BaseException:
            os.close(watch_fd)
            raise

    def drain(self) -> tuple[_IndexNamespaceEvent, ...]:
        if self.closed:
            raise CandidateAuthorityError("private-index event authority is already closed")
        events: list[_IndexNamespaceEvent] = []
        while True:
            try:
                data = os.read(self.fd, 65_536)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno == errno.EAGAIN:
                    break
                raise CandidateAuthorityError("private-index event receipt is unavailable") from exc
            offset = 0
            while offset < len(data):
                if len(data) - offset < _INOTIFY_EVENT.size:
                    raise CandidateAuthorityError("private-index event receipt is malformed")
                _watch, mask, cookie, name_length = _INOTIFY_EVENT.unpack_from(data, offset)
                offset += _INOTIFY_EVENT.size
                end = offset + name_length
                if end > len(data):
                    raise CandidateAuthorityError("private-index event receipt is malformed")
                name = data[offset:end].split(b"\0", 1)[0]
                offset = end
                if mask & _IN_Q_OVERFLOW:
                    raise CandidateAuthorityError("private-index event receipt overflowed")
                events.append(_IndexNamespaceEvent(mask & _INDEX_WATCH_MASK, cookie, name))
        return tuple(events)

    def close(self) -> None:
        if self.closed:
            return
        try:
            os.close(self.fd)
        except OSError as exc:
            raise CandidateAuthorityError("private-index event authority cleanup failed") from exc
        finally:
            self.closed = True


class GitCandidateTreeBuilder:
    """Create source (T0) and inventory (T1) trees without repository mutation."""

    def __init__(
        self,
        repo_root: Path,
        *,
        expected_parent_commit_oid: str,
        allowed_paths: frozenset[str],
        temp_root: Path,
        inventory_path: str = "pin-inventory.json",
        retention_policy: RetentionPolicy = RetentionPolicy(),
    ) -> None:
        self._repo_root = self._validated_repo_root(repo_root)
        self._temp_root = self._validated_temp_root(temp_root)
        if type(retention_policy) is not RetentionPolicy:
            raise CandidateAuthorityError("retention policy must be an exact RetentionPolicy")
        self._retention_policy = retention_policy
        self._retained_evidence: list[RetainedEvidenceReceipt] = []
        self._allowed_paths = self._validated_allowed_paths(allowed_paths)
        self._inventory_path = _canonical_path(inventory_path, label="inventory path")
        if self._inventory_path not in self._allowed_paths:
            raise CandidateAuthorityError("inventory path must be in the allowed path set")
        self._shared_repository_mode = self._configured_shared_repository_mode()
        parent = _require_full_oid(expected_parent_commit_oid, label="expected parent commit OID")
        try:
            parent_snapshot = GitTreeSnapshot.from_commit(self._repo_root, parent)
        except GitAuthorityError as exc:
            raise CandidateAuthorityError("expected parent commit is not immutable Git authority") from exc
        if parent_snapshot.commit_oid != parent:
            raise CandidateAuthorityError("expected parent commit receipt mismatch")
        self._expected_parent_commit_oid = parent
        self._expected_parent_tree_oid = parent_snapshot.tree_oid
        self._object_format = parent_snapshot.object_format
        self._issued_t0: dict[int, TreeBuildReceipt] = {}

    @property
    def retention_policy(self) -> RetentionPolicy:
        return self._retention_policy

    @property
    def retained_evidence(self) -> tuple[RetainedEvidenceReceipt, ...]:
        """Completed evidence awaiting separate operator archival/reclamation."""
        return tuple(self._retained_evidence)

    def _configured_shared_repository_mode(self) -> int | None:
        raw = self._run_git((
            "config",
            "--local",
            "--default=__p1_u00_unset__",
            "--get",
            "core.sharedRepository",
        ))
        if raw == b"__p1_u00_unset__\n":
            return None
        if raw == b"0600\n":
            return 0o600
        if raw == b"0640\n":
            return 0o640
        raise CandidateAuthorityError(
            "core.sharedRepository must be unset or the supported safe numeric mode 0600/0640"
        )

    @staticmethod
    def _validated_repo_root(value: object) -> Path:
        if not isinstance(value, Path):
            raise CandidateAuthorityError("repository root must be a Path")
        try:
            root = value.resolve(strict=True)
        except OSError as exc:
            raise CandidateAuthorityError("repository root is unavailable") from exc
        if not root.is_dir():
            raise CandidateAuthorityError("repository root is not a directory")
        return root

    @staticmethod
    def _validated_allowed_paths(value: object) -> frozenset[str]:
        if type(value) is not frozenset or not value:
            raise CandidateAuthorityError("allowed path set must be a nonempty frozenset")
        paths = tuple(_canonical_path(path, label="allowed path") for path in value)
        if len(paths) != len(set(paths)):
            raise CandidateAuthorityError("allowed path set contains duplicate canonical paths")
        return frozenset(paths)

    @staticmethod
    def _validated_temp_root(value: object) -> Path:
        if not isinstance(value, Path):
            raise CandidateAuthorityError("temporary root must be a Path")
        try:
            supplied_metadata = os.lstat(value)
            root = value.resolve(strict=True)
        except OSError as exc:
            raise CandidateAuthorityError("temporary root is unavailable") from exc
        if stat.S_ISLNK(supplied_metadata.st_mode):
            raise CandidateAuthorityError("temporary root must not be a symlink")
        try:
            metadata = os.stat(root, follow_symlinks=False)
        except OSError as exc:
            raise CandidateAuthorityError("temporary root is unavailable") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise CandidateAuthorityError("temporary root must be private mode 0700 and owned by this user")
        current = root
        while True:
            try:
                ancestor = os.lstat(current)
            except OSError as exc:
                raise CandidateAuthorityError("temporary root ancestor is unavailable") from exc
            if stat.S_ISLNK(ancestor.st_mode):
                raise CandidateAuthorityError("temporary root ancestor must not be a symlink")
            if ancestor.st_uid not in {0, os.geteuid()}:
                raise CandidateAuthorityError("temporary root ancestor owner is untrusted")
            mode = stat.S_IMODE(ancestor.st_mode)
            if mode & 0o022 and not (mode & stat.S_ISVTX):
                raise CandidateAuthorityError("temporary root ancestor is writable by another user")
            if current.parent == current:
                break
            current = current.parent
        return root

    def _run_git(
        self,
        arguments: tuple[str, ...],
        *,
        input_data: bytes | None = None,
        index_path: Path | None = None,
        index_fd: int | None = None,
        expected_index: _IndexIdentity | None = None,
    ) -> bytes | _IndexedGitResult:
        index_watch: _IndexNamespaceWatch | None = None
        if index_path is not None:
            if index_fd is None:
                raise CandidateAuthorityError("temporary index command receipt is unavailable")
            index_watch = _IndexNamespaceWatch.open(index_fd)
        try:
            if expected_index is not None:
                if index_path is None or index_fd is None:
                    raise CandidateAuthorityError("temporary index command receipt is unavailable")
                if self._index_identity(index_path) != expected_index:
                    raise CandidateAuthorityError("temporary index was replaced before Git command")
        except BaseException:
            if index_watch is not None:
                index_watch.close()
            raise
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        shared_mode = getattr(self, "_shared_repository_mode", None)
        environment.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.sharedRepository",
            "GIT_CONFIG_VALUE_0": "false" if shared_mode is None else f"0{shared_mode:o}",
        })
        if index_path is not None:
            environment["GIT_INDEX_FILE"] = os.fspath(index_path)
        primary: BaseException | None = None
        try:
            completed = subprocess.run(
                ("git", *arguments), cwd=self._repo_root, input=input_data,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                env=environment, timeout=_GIT_TIMEOUT_SECONDS,
                pass_fds=() if index_fd is None else (index_fd,),
            )
        except subprocess.TimeoutExpired as exc:
            primary = CandidateAuthorityError("Git candidate-tree command timed out")
            if index_watch is not None:
                index_watch.close()
            raise primary from exc
        except OSError as exc:
            primary = CandidateAuthorityError("Git candidate-tree command could not run")
            if index_watch is not None:
                index_watch.close()
            raise primary from exc
        try:
            if completed.returncode != 0:
                raise CandidateAuthorityError("Git candidate-tree command failed")
            if index_path is not None:
                if index_fd is None or index_watch is None:
                    raise CandidateAuthorityError("temporary index command receipt is unavailable")
                # Watch the descriptor-bound directory before launch, capture
                # the post-command object, then drain again. This closes the
                # lower boundary where a completed child can otherwise be
                # followed by an equivalent pathname replacement before a
                # caller-side identity sample.
                events = index_watch.drain()
                post_index = self._index_identity(index_path)
                events += index_watch.drain()
                confirmed_index = self._index_identity(index_path)
                events += index_watch.drain()
                if confirmed_index != post_index:
                    raise CandidateAuthorityError(
                        "temporary index changed during post-command receipt confirmation"
                    )
                if confirmed_index[7] > self._retention_policy.operation_reserve_bytes:
                    raise CandidateAuthorityError("temporary index exceeds retention byte reservation")
                self._validate_index_events(arguments, expected_index, confirmed_index, events)
                return _IndexedGitResult(completed.stdout, confirmed_index)
            return completed.stdout
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if index_watch is not None:
                try:
                    index_watch.close()
                except BaseException:
                    if primary is None:
                        raise

    def _validate_index_events(
        self,
        arguments: tuple[str, ...],
        before: _IndexIdentity | None,
        after: _IndexIdentity,
        events: tuple[_IndexNamespaceEvent, ...],
    ) -> None:
        configured_attrib = self._shared_repository_mode in {0o600, 0o640}
        attrib_events = (
            (_IndexNamespaceEvent(_IN_ATTRIB, 0, b"index.lock"),)
            if configured_attrib
            else ()
        )
        commit_prefix = (
            _IndexNamespaceEvent(_IN_CREATE, 0, b"index.lock"),
            *attrib_events,
            _IndexNamespaceEvent(_IN_MODIFY, 0, b"index.lock"),
            _IndexNamespaceEvent(_IN_CLOSE_WRITE, 0, b"index.lock"),
        )
        commit_cookie = events[-2].cookie if len(events) == len(commit_prefix) + 2 else 0
        committed = (
            len(events) == len(commit_prefix) + 2
            and events[:len(commit_prefix)] == commit_prefix
            and commit_cookie != 0
            and events[-2] == _IndexNamespaceEvent(_IN_MOVED_FROM, commit_cookie, b"index.lock")
            and events[-1] == _IndexNamespaceEvent(_IN_MOVED_TO, commit_cookie, b"index")
        )
        rollback_events = (
            _IndexNamespaceEvent(_IN_CREATE, 0, b"index.lock"),
            *attrib_events,
            _IndexNamespaceEvent(_IN_CLOSE_WRITE, 0, b"index.lock"),
            _IndexNamespaceEvent(_IN_DELETE, 0, b"index.lock"),
        )
        rolled_back = events == rollback_events
        if arguments[:1] == ("hash-object",):
            if events or before is None or after != before:
                raise CandidateAuthorityError("temporary index changed during hash-object")
            return
        if arguments[:1] not in {("read-tree",), ("update-index",), ("write-tree",)}:
            raise CandidateAuthorityError("indexed Git command is not allowed")
        if committed:
            if configured_attrib and after[5] != self._shared_repository_mode:
                raise CandidateAuthorityError("Git index attribute event did not establish configured safe mode")
            if before is not None and (after[0], after[1]) == (before[0], before[1]):
                raise CandidateAuthorityError("Git index commit did not establish a new inode")
            return
        if rolled_back:
            if before is None or after != before:
                raise CandidateAuthorityError("Git index rollback changed private-index identity")
            return
        raise CandidateAuthorityError("private-index namespace transition was not Git-owned")

    @staticmethod
    def _indexed_result(value: bytes | _IndexedGitResult) -> _IndexedGitResult:
        if type(value) is not _IndexedGitResult:
            raise CandidateAuthorityError("temporary index command receipt is missing")
        return value

    @staticmethod
    def _raw_file_state(path: Path, *, label: str) -> _ObservedFile:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return _ObservedFile(os.fspath(path), None, None)
        except OSError as exc:
            raise CandidateAuthorityError(f"{label} is unavailable") from exc
        identity = (
            metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode),
            metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode),
            metadata.st_nlink, metadata.st_size,
        )
        if not stat.S_ISREG(metadata.st_mode):
            return _ObservedFile(os.fspath(path), identity, None)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_gid, stat.S_IMODE(opened.st_mode), opened.st_size) != (
                metadata.st_dev, metadata.st_ino, metadata.st_gid, stat.S_IMODE(metadata.st_mode), metadata.st_size,
            ):
                raise CandidateAuthorityError(f"{label} changed while being captured")
            digest = hashlib.file_digest(os.fdopen(descriptor, "rb", closefd=False), "sha256").hexdigest()
        except CandidateAuthorityError:
            raise
        except OSError as exc:
            raise CandidateAuthorityError(f"{label} is unavailable") from exc
        finally:
            try:
                os.close(descriptor)
            except UnboundLocalError:
                pass
            except OSError as exc:
                raise CandidateAuthorityError(f"{label} descriptor cleanup was not confirmed") from exc
        return _ObservedFile(os.fspath(path), identity, digest)

    def _worktree_state(self) -> tuple[_ObservedFile, ...]:
        observed: list[_ObservedFile] = []
        try:
            for current, directories, files in os.walk(self._repo_root, topdown=True, followlinks=False):
                current_path = Path(current)
                directories[:] = sorted(name for name in directories if name != ".git")
                for name in (*directories, *sorted(files)):
                    path = current_path / name
                    observed.append(self._raw_file_state(path, label="worktree path"))
                    if len(observed) > 20_000:
                        raise CandidateAuthorityError("worktree observation entry limit exceeded")
        except OSError as exc:
            raise CandidateAuthorityError("worktree observation is unavailable") from exc
        return tuple(sorted(observed, key=lambda item: item.path))

    def _git_path(self, name: str) -> Path:
        raw = self._run_git(("rev-parse", "--git-path", name))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CandidateAuthorityError("Git path output is malformed") from exc
        if not text.endswith("\n") or text.count("\n") != 1:
            raise CandidateAuthorityError("Git path output is malformed")
        path = Path(text[:-1])
        return path if path.is_absolute() else self._repo_root / path

    def _repository_state(self) -> _RepositoryState:
        return _RepositoryState(
            head=self._one_oid(self._run_git(("rev-parse", "HEAD")), label="HEAD"),
            head_file=self._raw_file_state(self._git_path("HEAD"), label="Git HEAD"),
            refs=self._run_git((
                "for-each-ref",
                "--sort=refname",
                "--format=%(refname)%00%(objectname)%00%(symref)%00",
            )),
            index_file=self._raw_file_state(self._git_path("index"), label="Git index"),
            status=self._run_git(("status", "--porcelain=v1", "-z")),
            worktree_paths=self._worktree_state(),
        )

    def _one_oid(self, value: bytes, *, label: str) -> str:
        try:
            decoded = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise CandidateAuthorityError(f"{label} output is not ASCII") from exc
        if not decoded.endswith("\n") or decoded.count("\n") != 1:
            raise CandidateAuthorityError(f"{label} output is malformed")
        return _require_full_oid(decoded[:-1], label=label, width=len(self._expected_parent_commit_oid) if hasattr(self, "_expected_parent_commit_oid") else None)

    def _assert_repository_unchanged(self, before: _RepositoryState) -> None:
        after = self._repository_state()
        if after != before:
            raise CandidateAuthorityError("HEAD, refs, worktree index, or worktree changed during candidate construction")

    def _validated_updates(self, updates: object, *, forbid_inventory: bool) -> tuple[tuple[str, bytes], ...]:
        if not isinstance(updates, Mapping) or not updates:
            raise CandidateAuthorityError("updates must be a nonempty immutable path-to-bytes mapping")
        checked: list[tuple[str, bytes]] = []
        for raw_path, data in updates.items():
            path = _canonical_path(raw_path, label="update path")
            if path not in self._allowed_paths:
                raise CandidateAuthorityError("update path is outside allowed path set")
            if forbid_inventory and path == self._inventory_path:
                raise CandidateAuthorityError("inventory path must not be included in T0 updates")
            if type(data) is not bytes:
                raise CandidateAuthorityError("update bytes must have exact bytes type")
            checked.append((path, data))
        checked.sort(key=lambda pair: pair[0])
        if len({path for path, _data in checked}) != len(checked):
            raise CandidateAuthorityError("update paths are duplicated")
        return tuple(checked)

    def _verify_exact_tree_delta(
        self,
        base: GitTreeSnapshot,
        target: GitTreeSnapshot,
        receipts: tuple[CandidatePathReceipt, ...],
        *,
        expected_bytes: Mapping[str, bytes] | None,
        forbid_inventory: bool,
    ) -> None:
        base_paths = {blob.path: blob for blob in base.blobs}
        target_paths = {blob.path: blob for blob in target.blobs}
        changed = tuple(sorted(
            path for path in base_paths.keys() | target_paths.keys()
            if base_paths.get(path) != target_paths.get(path)
        ))
        receipt_paths = tuple(receipt.path for receipt in receipts)
        if changed != receipt_paths:
            raise CandidateAuthorityError("candidate tree delta does not exactly match its path receipts")
        for receipt in receipts:
            if receipt.path not in self._allowed_paths:
                raise CandidateAuthorityError("candidate tree delta contains a path outside allowed path set")
            if forbid_inventory and receipt.path == self._inventory_path:
                raise CandidateAuthorityError("candidate tree delta includes the inventory path")
            try:
                blob = target.blob(receipt.path)
            except GitAuthorityError as exc:
                raise CandidateAuthorityError("candidate tree receipt path is absent") from exc
            if (
                blob.mode != receipt.mode or blob.blob_oid != receipt.blob_oid
                or blob.sha256 != receipt.sha256
            ):
                raise CandidateAuthorityError("candidate tree receipt does not reproduce reopened blob identity")
            if expected_bytes is not None:
                try:
                    data = expected_bytes[receipt.path]
                except KeyError as exc:
                    raise CandidateAuthorityError("candidate tree receipt lacks immutable input bytes") from exc
                if blob.data != data or hashlib.sha256(data).hexdigest() != receipt.sha256:
                    raise CandidateAuthorityError("candidate tree receipt bytes do not match immutable input")

    @staticmethod
    def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode),
            metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode),
        )

    @classmethod
    def _retention_usage(cls, parent_fd: int) -> _RetentionUsage:
        """Inventory all root state without following names outside the private root."""
        try:
            top_level = tuple(sorted(os.listdir(parent_fd)))
            total_bytes = 0
            seen_directories: set[tuple[int, int]] = set()

            def visit(directory_fd: int) -> None:
                nonlocal total_bytes
                before = tuple(sorted(os.listdir(directory_fd)))
                for name in before:
                    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISDIR(metadata.st_mode):
                        descriptor = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=directory_fd,
                        )
                        try:
                            opened = os.fstat(descriptor)
                            if cls._directory_identity(opened) != cls._directory_identity(metadata):
                                raise CandidateAuthorityError(
                                    "retained evidence changed during capacity inventory"
                                )
                            identity = (opened.st_dev, opened.st_ino)
                            if identity in seen_directories:
                                raise CandidateAuthorityError(
                                    "retained evidence directory identity is duplicated"
                                )
                            seen_directories.add(identity)
                            visit(descriptor)
                        finally:
                            os.close(descriptor)
                    else:
                        total_bytes += metadata.st_size
                if tuple(sorted(os.listdir(directory_fd))) != before:
                    raise CandidateAuthorityError(
                        "retained evidence changed during capacity inventory"
                    )

            visit(parent_fd)
            capacity = os.fstatvfs(parent_fd)
            free_bytes = capacity.f_bavail * capacity.f_frsize
            if tuple(sorted(os.listdir(parent_fd))) != top_level:
                raise CandidateAuthorityError("retained evidence changed during capacity inventory")
            return _RetentionUsage(len(top_level), total_bytes, free_bytes)
        except CandidateAuthorityError:
            raise
        except OSError as exc:
            raise CandidateAuthorityError("retained evidence capacity inventory is unavailable") from exc

    def _assert_retention_capacity(self, parent_fd: int) -> None:
        usage = self._retention_usage(parent_fd)
        policy = self._retention_policy
        if usage.entries >= policy.max_entries:
            raise CandidateAuthorityError("retained evidence entry capacity is exhausted")
        if usage.bytes + policy.operation_reserve_bytes > policy.max_bytes:
            raise CandidateAuthorityError("retained evidence byte capacity is exhausted")
        required_free = policy.operation_reserve_bytes + policy.minimum_free_bytes
        if usage.free_bytes < required_free:
            raise CandidateAuthorityError("retained evidence filesystem capacity is exhausted")

    @staticmethod
    def _rename_noreplace(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        """Linux atomic no-clobber rename used for evidence handoff."""
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renameat2 = libc.renameat2
        except AttributeError as exc:
            raise CandidateAuthorityError("atomic no-replace retention is unavailable") from exc
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            src_dir_fd,
            os.fsencode(source),
            dst_dir_fd,
            os.fsencode(destination),
            _RENAME_NOREPLACE,
        )
        if result == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, os.strerror(error), destination)
        raise OSError(error, os.strerror(error), destination)

    def _new_operation(self) -> _OwnedOperation:
        parent_fd: int | None = None
        operation_fd: int | None = None
        try:
            parent_fd = os.open(
                self._temp_root,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            )
            parent_identity = self._directory_identity(os.fstat(parent_fd))
            if parent_identity[3] != os.geteuid() or parent_identity[5] != 0o700:
                raise CandidateAuthorityError("private temporary root identity changed")
            self._assert_retention_capacity(parent_fd)
            for _attempt in range(16):
                name = f"p1-u00-candidate-{secrets.token_hex(16)}"
                try:
                    os.mkdir(name, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    continue
                operation_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                identity = self._directory_identity(os.fstat(operation_fd))
                named_identity = self._directory_identity(
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                )
                if identity != named_identity or identity[3] != os.geteuid() or identity[5] != 0o700:
                    raise CandidateAuthorityError("private temporary operation directory is unsafe")
                return _OwnedOperation(parent_fd, parent_identity, name, operation_fd, identity)
            raise CandidateAuthorityError("private temporary operation directory name collision bound exceeded")
        except OSError as exc:
            raise CandidateAuthorityError("private temporary operation directory is unavailable") from exc
        except BaseException:
            for descriptor in (operation_fd, parent_fd):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise

    @classmethod
    def _cleanup_operation(
        cls,
        operation: _OwnedOperation,
        expected_index: _IndexIdentity | None,
    ) -> RetainedEvidenceReceipt:
        """Atomically quarantine completed state; never pathname-unlink mutable evidence."""
        receipt: RetainedEvidenceReceipt | None = None
        try:
            if operation.closed:
                raise CandidateAuthorityError("temporary operation is already closed")
            if cls._directory_identity(os.fstat(operation.parent_fd)) != operation.parent_identity:
                raise CandidateAuthorityError("temporary operation cleanup parent identity changed; retained evidence")
            if cls._directory_identity(os.fstat(operation.fd)) != operation.identity:
                raise CandidateAuthorityError("temporary operation cleanup descriptor identity changed; retained evidence")
            if cls._directory_identity(os.stat(operation.name, dir_fd=operation.parent_fd, follow_symlinks=False)) != operation.identity:
                raise CandidateAuthorityError("temporary operation cleanup identity changed")
            names = tuple(sorted(os.listdir(operation.fd)))
            if names not in {(), ("index",)}:
                raise CandidateAuthorityError("temporary operation has unexpected contents; retained for evidence")
            if names == ("index",):
                if expected_index is None or cls._index_identity(operation.index_path) != expected_index:
                    raise CandidateAuthorityError("temporary index cleanup identity changed")
            elif expected_index is not None:
                raise CandidateAuthorityError("temporary index cleanup evidence is missing")

            # Linux has no unprivileged conditional unlink-by-inode primitive.
            # Move the whole descriptor-bound directory atomically to a fresh
            # retention name, then reverify it there. We intentionally retain
            # even normal completed state: deleting through any later pathname
            # would recreate the exact identity-check-to-unlink race this
            # authority boundary is required to exclude.
            for _attempt in range(16):
                candidate = f"{operation.name}-retained-{secrets.token_hex(16)}"
                try:
                    cls._rename_noreplace(
                        operation.name,
                        candidate,
                        src_dir_fd=operation.parent_fd,
                        dst_dir_fd=operation.parent_fd,
                    )
                except FileExistsError:
                    continue
                else:
                    retained_name = candidate
                    break
            else:
                raise CandidateAuthorityError("temporary operation retention name bound exceeded")
            if cls._directory_identity(
                os.stat(retained_name, dir_fd=operation.parent_fd, follow_symlinks=False)
            ) != operation.identity:
                raise CandidateAuthorityError("temporary operation retention identity changed; retained evidence")
            retained_names = tuple(sorted(os.listdir(operation.fd)))
            if retained_names != names:
                raise CandidateAuthorityError("temporary operation changed during retention; retained evidence")
            if retained_names == ("index",):
                if expected_index is None or cls._index_identity(operation.index_path) != expected_index:
                    raise CandidateAuthorityError("temporary index changed during retention; retained evidence")
            receipt = RetainedEvidenceReceipt(
                directory_name=retained_name,
                index_bytes=None if expected_index is None else expected_index[7],
                index_sha256=None if expected_index is None else expected_index[8],
            )
        except CandidateAuthorityError:
            raise
        except OSError as exc:
            raise CandidateAuthorityError("temporary operation cleanup was not confirmed") from exc
        finally:
            errors: list[OSError] = []
            for descriptor in (operation.fd, operation.parent_fd):
                try:
                    os.close(descriptor)
                except OSError as exc:
                    errors.append(exc)
            operation.closed = True
            if errors:
                raise CandidateAuthorityError("temporary operation descriptor cleanup was not confirmed") from errors[0]
        if receipt is None:
            raise CandidateAuthorityError("temporary operation retention receipt is unavailable")
        return receipt

    @staticmethod
    def _index_identity(index: Path) -> _IndexIdentity:
        try:
            metadata = os.stat(index, follow_symlinks=False)
        except OSError as exc:
            raise CandidateAuthorityError("temporary index is unavailable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_nlink != 1
        ):
            raise CandidateAuthorityError("temporary index identity is unsafe")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(index, flags)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise CandidateAuthorityError("temporary index was replaced")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                chunks.append(chunk)
        except CandidateAuthorityError:
            raise
        except OSError as exc:
            raise CandidateAuthorityError("temporary index is unavailable") from exc
        finally:
            try:
                os.close(descriptor)
            except UnboundLocalError:
                pass
            except OSError as exc:
                raise CandidateAuthorityError("temporary index descriptor cleanup was not confirmed") from exc
        return (
            metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode),
            metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode),
            metadata.st_nlink, metadata.st_size,
            hashlib.sha256(b"".join(chunks)).hexdigest(),
        )

    def _build_tree(
        self,
        base: GitTreeSnapshot,
        updates: tuple[tuple[str, bytes], ...],
    ) -> tuple[str, tuple[CandidatePathReceipt, ...]]:
        operation = self._new_operation()
        index = operation.index_path
        cleanup_index: _IndexIdentity | None = None
        primary: BaseException | None = None
        try:
            read_tree = self._indexed_result(self._run_git(
                ("read-tree", base.tree_oid), index_path=index, index_fd=operation.fd,
            ))
            index_identity = read_tree.index_identity
            receipts: list[CandidatePathReceipt] = []
            for path, data in updates:
                hashed = self._indexed_result(self._run_git(
                    ("hash-object", "-w", "--stdin"), input_data=data,
                    index_path=index, index_fd=operation.fd, expected_index=index_identity,
                ))
                if hashed.index_identity != index_identity:
                    raise CandidateAuthorityError("temporary index changed during hash-object")
                blob_oid = self._one_oid(
                    hashed.stdout,
                    label="hash-object blob OID",
                )
                try:
                    mode = base.blob(path).mode
                except GitAuthorityError:
                    mode = 0o100644
                updated = self._indexed_result(self._run_git(
                    ("update-index", "--add", "--cacheinfo", f"{mode:o},{blob_oid},{path}"),
                    index_path=index, index_fd=operation.fd, expected_index=index_identity,
                ))
                index_identity = updated.index_identity
                receipts.append(CandidatePathReceipt(
                    path=path, mode=mode, blob_oid=blob_oid,
                    sha256=hashlib.sha256(data).hexdigest(),
                ))
            written = self._indexed_result(self._run_git(
                ("write-tree",), index_path=index, index_fd=operation.fd,
                expected_index=index_identity,
            ))
            tree_oid = self._one_oid(written.stdout, label="write-tree OID")
            # Git legitimately rewrites its cache-tree extension while producing
            # a tree. The first command's in-boundary receipt is the only
            # authority accepted by the second command and final cleanup.
            final_index_identity = written.index_identity
            reread = self._indexed_result(self._run_git(
                ("write-tree",), index_path=index, index_fd=operation.fd,
                expected_index=final_index_identity,
            ))
            reread_tree_oid = self._one_oid(reread.stdout, label="post-write-tree OID")
            if reread_tree_oid != tree_oid:
                raise CandidateAuthorityError("temporary index changed after write-tree; retained evidence")
            snapshot = self._snapshot_tree(tree_oid)
            self._verify_exact_tree_delta(
                base, snapshot, tuple(receipts),
                expected_bytes=dict(updates), forbid_inventory=False,
            )
            cleanup_index = reread.index_identity
            return tree_oid, tuple(receipts)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                retained = self._cleanup_operation(operation, cleanup_index)
                self._retained_evidence.append(retained)
            except BaseException as cleanup:
                if primary is None:
                    raise
                # Preserve the primary authority rejection. Cleanup has retained
                # the descriptor-bound evidence and must not mask its cause.
                _ = cleanup

    def _snapshot_tree(self, tree_oid: str) -> GitTreeSnapshot:
        try:
            snapshot = GitTreeSnapshot.from_tree(self._repo_root, tree_oid)
        except GitAuthorityError as exc:
            raise CandidateAuthorityError("candidate tree could not be reopened and verified") from exc
        if snapshot.tree_oid != tree_oid or snapshot.object_format != self._object_format:
            raise CandidateAuthorityError("candidate tree receipt mismatch")
        return snapshot

    def build_source_tree(self, updates: Mapping[str, bytes]) -> TreeBuildReceipt:
        checked = self._validated_updates(updates, forbid_inventory=True)
        before = self._repository_state()
        try:
            parent = GitTreeSnapshot.from_commit(self._repo_root, self._expected_parent_commit_oid)
        except GitAuthorityError as exc:
            raise CandidateAuthorityError("expected parent object no longer verifies") from exc
        if parent.tree_oid != self._expected_parent_tree_oid:
            raise CandidateAuthorityError("expected parent tree changed")
        tree_oid, receipts = self._build_tree(parent, checked)
        self._assert_repository_unchanged(before)
        receipt = TreeBuildReceipt(
            expected_parent_commit_oid=self._expected_parent_commit_oid,
            expected_parent_tree_oid=self._expected_parent_tree_oid,
            tree_oid=tree_oid,
            paths=receipts,
        )
        self._issued_t0[id(receipt)] = receipt
        return receipt

    def add_inventory(
        self,
        source: TreeBuildReceipt,
        *,
        path: str,
        inventory_bytes: bytes,
    ) -> CandidateTreeReceipt:
        if type(source) is not TreeBuildReceipt:
            raise CandidateAuthorityError("T0 receipt must be a TreeBuildReceipt")
        if self._issued_t0.get(id(source)) is not source:
            raise CandidateAuthorityError("T0 receipt was not issued by this builder instance")
        if (
            source.expected_parent_commit_oid != self._expected_parent_commit_oid
            or source.expected_parent_tree_oid != self._expected_parent_tree_oid
        ):
            raise CandidateAuthorityError("T0 receipt does not belong to this expected parent")
        inventory_path = _canonical_path(path, label="inventory path")
        if inventory_path != self._inventory_path:
            raise CandidateAuthorityError("inventory path does not match configured output path")
        if type(inventory_bytes) is not bytes:
            raise CandidateAuthorityError("inventory bytes must have exact bytes type")
        before = self._repository_state()
        t0 = self._snapshot_tree(source.tree_oid)
        parent = self._snapshot_tree(self._expected_parent_tree_oid)
        self._verify_exact_tree_delta(
            parent, t0, source.paths, expected_bytes=None, forbid_inventory=True,
        )
        try:
            parent_inventory = parent.blob(inventory_path)
        except GitAuthorityError:
            parent_inventory = None
        try:
            t0_inventory = t0.blob(inventory_path)
        except GitAuthorityError:
            t0_inventory = None
        if t0_inventory != parent_inventory:
            raise CandidateAuthorityError("T0 inventory state drifted from accepted parent")
        final_tree_oid, inventory_receipts = self._build_tree(
            t0, ((inventory_path, inventory_bytes),)
        )
        final = self._snapshot_tree(final_tree_oid)
        final_inventory = final.blob(inventory_path)
        receipt = inventory_receipts[0]
        if (
            final_inventory.blob_oid != receipt.blob_oid
            or final_inventory.sha256 != receipt.sha256
            or final_inventory.data != inventory_bytes
        ):
            raise CandidateAuthorityError("T1 inventory receipt verification failed")
        def without_inventory(snapshot: GitTreeSnapshot) -> tuple[GitBlobSnapshot, ...]:
            return tuple(blob for blob in snapshot.blobs if blob.path != inventory_path)
        if without_inventory(t0) != without_inventory(final):
            raise CandidateAuthorityError("T0/T1 path drift outside inventory output")
        self._assert_repository_unchanged(before)
        all_paths = tuple(sorted((*source.paths, receipt), key=lambda item: item.path))
        return CandidateTreeReceipt(
            expected_parent_commit_oid=self._expected_parent_commit_oid,
            expected_parent_tree_oid=self._expected_parent_tree_oid,
            source_tree_oid=source.tree_oid,
            final_tree_oid=final_tree_oid,
            inventory_blob_oid=receipt.blob_oid,
            inventory_sha256=receipt.sha256,
            paths=all_paths,
        )
