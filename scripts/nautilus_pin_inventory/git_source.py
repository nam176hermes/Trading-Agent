"""Read immutable, receipt-verified source bytes from exact Git tree objects."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
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


@dataclass
class _ClosureObject:
    oid: str
    prefix: str
    name: str
    identity: tuple[int, int, int, int, int, int, int] | None
    compressed: bytes


@dataclass
class _ClosureCapture:
    source: Path
    root_identity: tuple[int, int, int]
    root_fd: int
    prefixes: dict[str, tuple[int, tuple[int, int, int]]]
    objects: tuple[_ClosureObject, ...]

    def close(self) -> None:
        for descriptor, _ in self.prefixes.values():
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.close(self.root_fd)
        except OSError:
            pass


@dataclass(frozen=True)
class _PackEntry:
    name: str
    identity: tuple[int, int, int, int, int, int, int]


@dataclass
class _PackBootstrap:
    store: tempfile.TemporaryDirectory[str]
    root: Path
    destination: Path
    source: Path
    source_fd: int
    source_identity: tuple[int, int, int]
    entries: tuple[_PackEntry, ...]
    source_nlinks: tuple[tuple[str, int], ...]
    root_fd: int
    root_identity: tuple[int, int, int]
    destination_fd: int
    destination_identity: tuple[int, int, int]
    destination_pack_fd: int
    destination_pack_identity: tuple[int, int, int]
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        cleanup_error: BaseException | None = None
        try:
            self.store.cleanup()
        except OSError as exc:
            cleanup_error = cleanup_error or GitAuthorityError("pack bootstrap cleanup failed")
        if self.root.exists():
            cleanup_error = cleanup_error or GitAuthorityError("pack bootstrap cleanup was not confirmed")
        for name, expected_nlink in self.source_nlinks:
            try:
                if os.stat(name, dir_fd=self.source_fd, follow_symlinks=False).st_nlink != expected_nlink:
                    cleanup_error = cleanup_error or GitAuthorityError("pack bootstrap hardlink release was not confirmed")
            except OSError:
                # The descriptor is intentionally closed before cleanup; source deletion is
                # already a terminal authority failure and cannot become a successful close.
                cleanup_error = cleanup_error or GitAuthorityError("pack bootstrap hardlink release was not confirmed")
        if cleanup_error is not None:
            raise cleanup_error
        for descriptor in (self.destination_pack_fd, self.destination_fd, self.root_fd):
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_error = cleanup_error or GitAuthorityError("pack bootstrap descriptor cleanup failed")
        if cleanup_error is not None:
            raise cleanup_error
        self.closed = True


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


@dataclass(frozen=True)
class GitScanLimits:
    max_entries: int = 20_000
    max_path_bytes: int = 4_096
    max_blob_bytes: int = 2_000_000
    max_total_bytes: int = 100_000_000
    timeout_seconds: float = 30.0


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
                raise GitAuthorityAggregateError(primary, cleanup) from primary
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
                raise GitAuthorityAggregateError(primary, cleanup) from primary
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
        self._object_store: tempfile.TemporaryDirectory[str] | None = None
        self._private_objects: Path | None = None
        self._closure: _ClosureCapture | None = None
        self._pack_bootstraps: list[_PackBootstrap] = []
        self._pack_namespace: _PackNamespace | None = None
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
        self.assert_ambient_authority_absent()
        capture: _ClosureCapture | None = None
        store: tempfile.TemporaryDirectory[str] | None = None
        sealed = False
        try:
            self._pack_namespace = _freeze_pack_namespace(source, self.limits, deadline)
            packed_reader = lambda oid, expected: self._read_packed_source(source, oid, expected, object_format, deadline)
            capture = _capture_requested_closure(source, root_oid, root_type, object_format, self.limits, deadline, packed_reader)
            try:
                store = tempfile.TemporaryDirectory(prefix="p1-u00-git-objects-")
            except OSError as exc:
                raise GitAuthorityError("private Git object-store is unavailable") from exc
            destination = Path(store.name) / "objects"
            _copy_requested_closure(capture, destination, self.limits, deadline)
            _verify_requested_closure(capture, self.limits, deadline)
            for bootstrap in self._pack_bootstraps:
                _verify_pack_bootstrap(bootstrap, self.limits, deadline)
            self.assert_ambient_authority_absent()
            sealed = True
        except BaseException as primary:
            cleanup_error: BaseException | None = None
            if store is not None:
                try:
                    store.cleanup()
                except OSError as exc:
                    cleanup_error = GitAuthorityError("private Git object-store cleanup failed")
            try:
                self._close_pack_bootstraps()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            namespace = self._pack_namespace
            if namespace is not None:
                try:
                    namespace.close()
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
                else:
                    self._pack_namespace = None
            if cleanup_error is not None:
                raise GitAuthorityAggregateError(primary, cleanup_error) from primary
            raise
        finally:
            # Keep these descriptors until terminal verification; failure paths always close them.
            if not sealed and capture is not None:
                capture.close()
        self._object_store = store
        self._private_objects = destination
        self._closure = capture

    def _read_packed_source(self, source: Path, oid: str, expected_type: str, object_format: str, deadline: float) -> tuple[str, bytes]:
        """Expose exactly one descriptor-pinned pair which indexes this full OID."""
        for bootstrap in self._pack_bootstraps:
            if _pack_index_contains(bootstrap.entries[1].name, bootstrap.source_fd, oid, object_format, self.limits, deadline):
                _verify_pack_bootstrap(bootstrap, self.limits, deadline)
                result = self._read_bootstrap_object(bootstrap, oid, expected_type, object_format)
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
        result = self._read_bootstrap_object(bootstrap, oid, expected_type, object_format)
        _verify_pack_bootstrap(bootstrap, self.limits, deadline)
        return result

    def _close_pack_bootstraps(self) -> None:
        error: BaseException | None = None
        retained: list[_PackBootstrap] = []
        for bootstrap in reversed(self._pack_bootstraps):
            try:
                bootstrap.close()
            except BaseException as exc:
                error = error or exc
                retained.append(bootstrap)
        self._pack_bootstraps = list(reversed(retained))
        if error is not None:
            raise error

    def _read_bootstrap_object(self, bootstrap: _PackBootstrap, oid: str, expected_type: str, object_format: str) -> tuple[str, bytes]:
        request = oid.encode("ascii") + b"\n"
        _verify_private_pack_bootstrap(bootstrap)
        checked = self.run(("cat-file", "--batch-check"), input_data=request, stdout_cap=512, object_directory=bootstrap.destination)
        _verify_private_pack_bootstrap(bootstrap)
        newline = checked.find(b"\n")
        if newline < 0 or newline + 1 != len(checked):
            raise GitAuthorityError("truncated bootstrap Git object header")
        header = checked[:newline]
        if header == oid.encode("ascii") + b" missing":
            raise GitAuthorityError("requested Git object is absent from the private primary pack bootstrap")
        try:
            actual_oid_raw, object_type_raw, size_raw = header.split(b" ")
            actual_oid = actual_oid_raw.decode("ascii")
            object_type = object_type_raw.decode("ascii")
            size = int(size_raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitAuthorityError("malformed bootstrap Git object header") from exc
        if actual_oid != oid or object_type != expected_type or size < 0:
            raise GitAuthorityError("bootstrap Git object OID/type/size does not match request")
        if expected_type == "blob" and size > self.limits.max_blob_bytes:
            raise GitAuthorityError("Git blob exceeds configured blob limit")
        if size > self.limits.max_total_bytes:
            raise GitAuthorityError("bootstrap Git object exceeds aggregate limit")
        output = self.run(("cat-file", "--batch"), input_data=request, stdout_cap=size + 512, object_directory=bootstrap.destination)
        _verify_private_pack_bootstrap(bootstrap)
        newline = output.find(b"\n")
        if newline < 0:
            raise GitAuthorityError("truncated bootstrap Git object header")
        if output[:newline] != header:
            raise GitAuthorityError("bootstrap Git object changed between type/size and body reads")
        body_start = newline + 1
        body_end = body_start + size
        if body_end >= len(output) or output[body_end:body_end + 1] != b"\n" or body_end + 1 != len(output):
            raise GitAuthorityError("truncated bootstrap Git object body")
        payload = output[body_start:body_end]
        raw = f"{object_type} {size}\0".encode("ascii") + payload
        if hashlib.new(object_format, raw).hexdigest() != oid:
            raise GitAuthorityError("bootstrap Git object bytes do not reproduce their OID")
        return object_type, payload

    def verify_sealed_source(self) -> None:
        if getattr(self, "_closure", None) is not None:
            deadline = time.monotonic() + self.limits.timeout_seconds
            _verify_requested_closure(self._closure, self.limits, deadline)
            for bootstrap in self._pack_bootstraps:
                _verify_pack_bootstrap(bootstrap, self.limits, deadline)
                _verify_private_pack_bootstrap(bootstrap)

    def close(self, *, suppress_terminal_error: bool = False, primary_error: BaseException | None = None) -> None:
        terminal_error: BaseException | None = None
        try:
            self.assert_ambient_authority_absent()
        except BaseException as exc:
            terminal_error = exc
        finally:
            closure = getattr(self, "_closure", None)
            if closure is not None:
                try:
                    self.verify_sealed_source()
                except BaseException as exc:
                    if terminal_error is None:
                        terminal_error = exc
                finally:
                    closure.close()
                    self._closure = None
            if self._object_store is not None:
                try:
                    self._object_store.cleanup()
                except OSError as exc:
                    if terminal_error is None:
                        terminal_error = GitAuthorityError("private Git object-store cleanup failed")
                finally:
                    self._object_store = None
                    self._private_objects = None
            try:
                self._close_pack_bootstraps()
            except BaseException as exc:
                if terminal_error is None:
                    terminal_error = exc
            namespace = self._pack_namespace
            if namespace is not None:
                try:
                    namespace.close()
                except BaseException as exc:
                    terminal_error = terminal_error or exc
                else:
                    self._pack_namespace = None
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

    def run(self, arguments: tuple[str, ...], *, input_data: bytes | None = None, stdout_cap: int = _METADATA_OUTPUT_CAP, object_directory: Path | None = None) -> bytes:
        _regular_inode_matches(self.executable, self._executable_inode, "Git executable")
        if type(stdout_cap) is not int or stdout_cap < 0:
            raise GitAuthorityError("Git stdout cap is invalid")
        environment = _git_env()
        selected_objects = self._private_objects if object_directory is None else object_directory
        if selected_objects is not None:
            environment["GIT_OBJECT_DIRECTORY"] = str(selected_objects)
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
                except OSError:
                    pass
            raise GitAuthorityError("Git temporary input setup failed") from exc
        try:
            process = subprocess.Popen((str(self.executable), "--no-replace-objects", *arguments), cwd=self.repo_root, env=environment, stdin=input_file if input_file is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, preexec_fn=_bounded_git_preexec(self.limits))
        except OSError as exc:
            raise GitAuthorityError(f"Git command could not start: {exc}") from exc
        finally:
            if input_file is not None:
                input_file.close()
        termination = _ProcessTermination(process, process.pid)
        try:
            stdout, stderr = _read_process_streams(process, self.limits.timeout_seconds, stdout_cap, _STDERR_CAP, termination)
            _regular_inode_matches(self.executable, self._executable_inode, "Git executable")
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise GitAuthorityError(f"Git command failed ({' '.join(arguments)}): {detail}")
            return stdout
        except BaseException as exc:
            cleanup_error = termination.terminate()
            if cleanup_error is not None:
                raise GitAuthorityError(f"Git command failed and cleanup was not confirmed: {cleanup_error}") from exc
            if isinstance(exc, (KeyboardInterrupt, SystemExit, GitAuthorityError)):
                raise
            raise GitAuthorityError("Git command I/O setup or stream read failed") from exc
        finally:
            _close_process_streams(process)


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


def _bounded_git_preexec(limits: GitScanLimits) -> Callable[[], None]:
    memory = max(256 * 1024 * 1024, min(900 * 1024 * 1024, limits.max_total_bytes * 8 + 16 * 1024 * 1024))
    cpu = max(1, min(31, math.ceil(limits.timeout_seconds) + 1))

    def configure() -> None:
        if resource is None:
            raise OSError("Linux resource limits are unavailable")
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

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


def _seal_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise GitAuthorityError("Git object-store seal deadline exceeded")


def _pack_index_contains(name: str, directory_fd: int, oid: str, object_format: str, limits: GitScanLimits, deadline: float) -> bool:
    """Bounded binary lookup in a Git v2 pack index without listing its objects."""
    oid_bytes = bytes.fromhex(oid)
    hash_width = 20 if object_format == "sha1" else 32
    try:
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    except OSError as exc:
        raise GitAuthorityError("Git pack index cannot be opened") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise GitAuthorityError("Git pack index is not regular")
        header = os.pread(descriptor, 8, 0)
        if header[:4] != b"\xfftOc" or header[4:] not in {b"\x00\x00\x00\x02", b"\x00\x00\x00\x03"}:
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
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _freeze_pack_namespace(source: Path, limits: GitScanLimits, deadline: float) -> _PackNamespace:
    """Freeze names/identities once; a bad namespace is a packed-only failure sentinel."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
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


def _bootstrap_pack_view(namespace: _PackNamespace, common_dir: Path, required_oid: str, object_format: str, limits: GitScanLimits, deadline: float) -> _PackBootstrap | None:
    """Hardlink only the canonical pack/index pair indexing ``required_oid``.

    Discovery is incremental and descriptor-backed.  Bad, incomplete, or huge
    unrelated pairs are deliberately not retained and cannot influence a loose
    closure.  A pair which claims the requested OID is pinned as one unit.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    if namespace.sentinel is not None:
        raise GitAuthorityError(namespace.sentinel)
    if namespace.fd is None or namespace.identity is None:
        raise GitAuthorityError("Git pack namespace is unavailable")
    source_fd = namespace.fd
    try:
        source_identity = _directory_identity(os.fstat(source_fd))
        if source_identity != namespace.identity:
            raise GitAuthorityError("Git pack namespace changed during source snapshot")
        candidate: str | None = None
        for name, entry in sorted(namespace.entries.items()):
            _seal_deadline(deadline)
            if not name.endswith(".idx"):
                continue
            if _store_identity(os.stat(name, dir_fd=source_fd, follow_symlinks=False)) != entry.identity:
                raise GitAuthorityError("Git pack namespace changed during source snapshot")
            try:
                if _pack_index_contains(name, source_fd, required_oid, object_format, limits, deadline):
                    if candidate is None or name < candidate:
                        candidate = name
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
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise GitAuthorityError("requested Git pack/index pair contains a non-regular entry")
                identity = _store_identity(metadata)
                if identity != initial.identity:
                    raise GitAuthorityError("requested Git pack/index pair changed during bootstrap")
                if name.endswith(".idx") and identity[4] > limits.max_total_bytes:
                    raise GitAuthorityError("requested Git pack index exceeds configured limit")
                entries.append(_PackEntry(name, identity))
                source_nlinks.append((name, metadata.st_nlink))
            finally:
                os.close(descriptor)
        try:
            store = tempfile.TemporaryDirectory(prefix="p1-u00-git-pack-", dir=common_dir)
        except OSError as exc:
            raise GitAuthorityError("private Git pack bootstrap is unavailable") from exc
        root = Path(store.name)
        destination = root / "objects"
        try:
            destination.mkdir(mode=0o700)
            pack_destination = destination / "pack"
            pack_destination.mkdir(mode=0o700)
            root_fd = os.open(root, flags)
            destination_fd = os.open(destination, flags)
            destination_pack_fd = os.open(pack_destination, flags)
            try:
                linked_entries: list[_PackEntry] = []
                for entry in entries:
                    _seal_deadline(deadline)
                    os.link(entry.name, entry.name, src_dir_fd=source_fd, dst_dir_fd=destination_pack_fd, follow_symlinks=False)
                    linked_identity = _store_identity(os.stat(entry.name, dir_fd=source_fd, follow_symlinks=False))
                    if linked_identity[:3] + linked_identity[4:6] != entry.identity[:3] + entry.identity[4:6]:
                        raise GitAuthorityError("Git pack changed during bootstrap")
                    if _store_identity(os.stat(entry.name, dir_fd=destination_pack_fd, follow_symlinks=False)) != linked_identity:
                        raise GitAuthorityError("private Git pack bootstrap identity mismatch")
                    linked_entries.append(_PackEntry(entry.name, linked_identity))
            finally:
                pass
        except GitAuthorityError:
            store.cleanup()
            raise
        except OSError as exc:
            store.cleanup()
            raise GitAuthorityError("private Git pack bootstrap copy failed") from exc
        return _PackBootstrap(store, root, destination, namespace.source, source_fd, source_identity, tuple(linked_entries), tuple(source_nlinks), root_fd, _directory_identity(os.fstat(root_fd)), destination_fd, _directory_identity(os.fstat(destination_fd)), destination_pack_fd, _directory_identity(os.fstat(destination_pack_fd)))
    except BaseException:
        raise


def _verify_pack_bootstrap(bootstrap: _PackBootstrap, limits: GitScanLimits, deadline: float) -> None:
    _seal_deadline(deadline)
    try:
        if _directory_identity(os.stat(bootstrap.source, follow_symlinks=False)) != bootstrap.source_identity:
            raise GitAuthorityError("Git pack directory changed during source snapshot")
    except OSError as exc:
        raise GitAuthorityError("Git pack directory changed during source snapshot") from exc
    if _directory_identity(os.fstat(bootstrap.source_fd)) != bootstrap.source_identity:
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
    if _directory_identity(os.fstat(bootstrap.root_fd)) != bootstrap.root_identity:
        raise GitAuthorityError("private Git pack bootstrap root changed")
    if _directory_identity(os.fstat(bootstrap.destination_fd)) != bootstrap.destination_identity:
        raise GitAuthorityError("private Git pack bootstrap objects directory changed")
    if _directory_identity(os.fstat(bootstrap.destination_pack_fd)) != bootstrap.destination_pack_identity:
        raise GitAuthorityError("private Git pack bootstrap pack directory changed")
    _exact_directory_entries(
        bootstrap.root_fd,
        {"objects": bootstrap.destination_identity},
    )
    _exact_directory_entries(
        bootstrap.destination_fd,
        {"pack": bootstrap.destination_pack_identity},
    )
    _exact_directory_entries(
        bootstrap.destination_pack_fd,
        {entry.name: (entry.identity[0], entry.identity[1], entry.identity[2]) for entry in bootstrap.entries},
    )


def _capture_requested_closure(source: Path, root_oid: str, root_type: str, object_format: str, limits: GitScanLimits, deadline: float, packed_reader: Callable[[str, str], tuple[str, bytes]] | None = None) -> _ClosureCapture:
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

    try:
        while pending:
            oid, expected_type, depth = pending.pop()
            scheduled.pop(oid, None)
            if depth > _MAX_TREE_DEPTH:
                raise GitAuthorityError("Git tree depth limit exceeded")
            retain(oid, expected_type, depth)
        return _ClosureCapture(source, root_identity, root_fd, prefixes, tuple(objects))
    except BaseException:
        _ClosureCapture(source, root_identity, root_fd, prefixes, ()).close()
        raise


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


def _copy_requested_closure(capture: _ClosureCapture, destination: Path, limits: GitScanLimits, deadline: float) -> None:
    try:
        destination.mkdir(mode=0o700)
        for prefix in sorted({entry.prefix for entry in capture.objects}):
            _seal_deadline(deadline)
            (destination / prefix).mkdir(mode=0o700)
        copied_bytes = 0
        for entry in capture.objects:
            _seal_deadline(deadline)
            copied_bytes += len(entry.compressed)
            if copied_bytes > limits.max_total_bytes:
                raise GitAuthorityError("Git object closure aggregate seal cap exceeded")
            (destination / entry.prefix / entry.name).write_bytes(entry.compressed)
    except GitAuthorityError:
        raise
    except OSError as exc:
        raise GitAuthorityError("private Git object-store copy failed") from exc


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


def _tree_output_cap(limits: GitScanLimits) -> int:
    return (limits.max_entries + 1) * (limits.max_path_bytes + 128)


def _batch_output_cap(limits: GitScanLimits) -> int:
    return limits.max_total_bytes + limits.max_entries * 128


@dataclass
class _ProcessTermination:
    process: subprocess.Popen[bytes]
    pgid: int
    terminated: bool = False
    reaped: bool = False

    def terminate(self) -> GitAuthorityError | None:
        """Send at most one group signal, then perform only bounded direct-child waits."""
        if self.reaped:
            return None
        if not self.terminated:
            self.terminated = True
            try:
                os.killpg(self.pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                # The session leader can have exited while a descendant retains the pipes.
                # Its known PGID remains the only authority to terminate that descendant.
                pass
        if not self.reaped:
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                # Never use an unbounded wait; the group signal above is deliberately one-shot.
                return GitAuthorityError("Git child reap was not confirmed after bounded cleanup")
            else:
                self.reaped = True
        return None


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()


def _read_process_streams(process: subprocess.Popen[bytes], timeout: float, stdout_cap: int, stderr_cap: int, termination: _ProcessTermination) -> tuple[bytes, bytes]:
    assert process.stdout is not None and process.stderr is not None
    selector: selectors.BaseSelector | None = None
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
            termination.reaped = True
            break
        return bytes(output["stdout"]), bytes(output["stderr"])
    finally:
        if selector is not None:
            selector.close()


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
