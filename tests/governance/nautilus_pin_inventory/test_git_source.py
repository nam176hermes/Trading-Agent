"""Contract tests for immutable Git-tree source custody."""

from __future__ import annotations

import ast
import copy
import gc
import inspect
from pathlib import Path
import errno
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import hashlib
import math
import stat
from types import SimpleNamespace

import pytest

from scripts.nautilus_pin_inventory.git_source import GitAuthorityError, GitScanLimits, GitTreeSnapshot


class GitFixture:
    """A disposable repository exercised through real Git objects."""

    def __init__(self, root: Path, *, object_format: str | None = None) -> None:
        self.root = root
        self.root.mkdir()
        arguments = ("init", "-q") if object_format is None else ("init", f"--object-format={object_format}", "-q")
        self._git(*arguments)
        self._git("config", "user.email", "p1-u00@example.invalid")
        self._git("config", "user.name", "P1 U00")

    def _git(self, *arguments: str, input: bytes | None = None) -> bytes:
        completed = subprocess.run(
            ["git", *arguments], cwd=self.root, input=input, capture_output=True, check=False
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
        return completed.stdout

    def commit_file(self, path: str, data: bytes, *, mode: str = "100644") -> tuple[str, bytes]:
        leaf = self.root / path
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_bytes(data)
        self._git("add", "--", path)
        if mode == "100755":
            os.chmod(leaf, 0o755)
            self._git("add", "--", path)
        self._git("commit", "-qm", "seed")
        return self._git("rev-parse", "HEAD").decode().strip(), data

    def commit_index_entry(self, path: bytes, *, mode: str, object_oid: str) -> str:
        record = mode.encode("ascii") + b" " + object_oid.encode("ascii") + b"\t" + path + b"\0"
        self._git("update-index", "--add", "-z", "--index-info", input=record)
        tree = self._git("write-tree").decode().strip()
        return self._git("commit-tree", tree, "-m", "index entry").decode().strip()

    def blob_oid(self, data: bytes) -> str:
        return self._git("hash-object", "-w", "--stdin", input=data).decode().strip()

    def replace_worktree_parent(self, parent: str, replacement: bytes) -> None:
        target = self.root / parent
        target.rename(self.root / f"{parent}-old")
        target.mkdir()
        (target / "pin.md").write_bytes(replacement)

    def move_head_to_new_commit(self) -> str:
        return self.commit_file("moving.md", b"new head\n")[0]


@pytest.fixture
def git_fixture(tmp_path: Path) -> GitFixture:
    return GitFixture(tmp_path / "repo")


@pytest.fixture
def packed_git_fixture(tmp_path: Path) -> tuple[GitFixture, str, bytes]:
    fixture = GitFixture(tmp_path / "packed-repo")
    commit_oid, expected = fixture.commit_file(
        "pin.md", b"pack-index descriptor custody\n"
    )
    fixture._git("gc", "--prune=now")
    objects = fixture.root / ".git/objects"
    assert not (objects / commit_oid[:2] / commit_oid[2:]).exists()
    assert tuple((objects / "pack").glob("*.idx"))
    return fixture, commit_oid, expected


def _is_pack_index_descriptor(descriptor: int) -> bool:
    try:
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError:
        return False
    return target.removesuffix(" (deleted)").endswith(".idx")


def _open_descriptor_count() -> int:
    return len(tuple(Path("/proc/self/fd").iterdir()))


def _stable_descriptor_classes() -> tuple[str, ...]:
    """Classify open descriptors without depending on numeric FDs or pipe IDs."""
    classes: list[str] = []
    for entry in tuple(Path("/proc/self/fd").iterdir()):
        try:
            descriptor = int(entry.name)
            target = os.readlink(entry).removesuffix(" (deleted)")
            mode = os.fstat(descriptor).st_mode
        except (OSError, ValueError):
            continue
        if target.endswith(".idx"):
            descriptor_class = "git-pack-index"
        elif target.endswith(".pack"):
            descriptor_class = "git-pack"
        elif "p1-u00-pack-" in target:
            descriptor_class = "p1-u00-pack-root"
        elif target.startswith("pipe:["):
            descriptor_class = "pipe"
        elif target.startswith("socket:["):
            descriptor_class = "socket"
        elif target.startswith("anon_inode:"):
            descriptor_class = "anon-inode"
        elif stat.S_ISDIR(mode):
            descriptor_class = "directory"
        elif stat.S_ISREG(mode):
            descriptor_class = "regular-file"
        elif stat.S_ISFIFO(mode):
            descriptor_class = "fifo"
        else:
            descriptor_class = f"mode-{stat.S_IFMT(mode):o}"
        classes.append(descriptor_class)
    return tuple(sorted(classes))


def _packed_source_nlinks(fixture: GitFixture) -> tuple[tuple[str, int, int, int], ...]:
    pack_directory = fixture.root / ".git/objects/pack"
    paths = tuple(sorted((*pack_directory.glob("*.pack"), *pack_directory.glob("*.idx"))))
    assert paths
    return tuple(
        (path.name, metadata.st_dev, metadata.st_ino, metadata.st_nlink)
        for path in paths
        for metadata in (path.stat(),)
    )


def _task_owned_pack_roots(fixture: GitFixture) -> tuple[str, ...]:
    roots = {
        *Path("/tmp").glob("p1-u00-pack-*"),
        *(fixture.root / ".git").glob("p1-u00-pack-*"),
    }
    return tuple(sorted(str(path) for path in roots if path.is_dir()))


def _reviewer_process_groups() -> tuple[int, ...]:
    """Return process groups still represented by a direct test-process child."""
    groups: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            parent_pid = int(fields[1])
            process_group = int(fields[2])
        except (
            FileNotFoundError,
            IndexError,
            PermissionError,
            ProcessLookupError,
            ValueError,
        ):
            continue
        if parent_pid == os.getpid():
            groups.add(process_group)
    return tuple(sorted(groups))


def _emit_b4_batch_receipt(
    *,
    label: str,
    iterations: int,
    baseline_fds: tuple[str, ...],
    observed_fds: tuple[str, ...],
    baseline_roots: tuple[str, ...],
    observed_roots: tuple[str, ...],
    process_groups: tuple[int, ...],
    baseline_nlinks: tuple[tuple[str, int, int, int], ...],
    observed_nlinks: tuple[tuple[str, int, int, int], ...],
    foreign_receipts: tuple[tuple[object, ...], ...] = (),
) -> None:
    """Expose stable, exact B4 receipts when a focused run disables capture."""
    foreign_digest = hashlib.sha256(repr(foreign_receipts).encode("utf-8")).hexdigest()
    print(
        "B4_BATCH_RECEIPT "
        f"label={label!r} iterations={iterations} "
        f"baseline_fds={baseline_fds!r} observed_fds={observed_fds!r} "
        f"baseline_roots={baseline_roots!r} observed_roots={observed_roots!r} "
        f"process_groups={process_groups!r} "
        f"baseline_nlinks={baseline_nlinks!r} observed_nlinks={observed_nlinks!r} "
        f"foreign_receipt_count={len(foreign_receipts)} "
        f"foreign_receipt_sha256={foreign_digest} "
        f"foreign_receipts={foreign_receipts!r}"
    )


def test_exact_commit_snapshot_ignores_worktree_leaf_and_parent_replacement(git_fixture: GitFixture) -> None:
    """Break caught: a scanner reopens mutable worktree paths after choosing a commit."""
    commit_oid, expected = git_fixture.commit_file("nested/pin.md", b"1.227.0\n")
    git_fixture.replace_worktree_parent("nested", b"1.231.0\n")

    snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)

    assert snapshot.commit_oid == commit_oid
    assert snapshot.blob("nested/pin.md").data == expected


def test_full_oid_is_required_and_moving_head_is_not_authority(git_fixture: GitFixture) -> None:
    """Break caught: a moving ref or abbreviated identifier can select source authority."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"1.227.0\n")

    with pytest.raises(GitAuthorityError, match="full commit OID"):
        GitTreeSnapshot.from_commit(git_fixture.root, "HEAD")
    git_fixture.move_head_to_new_commit()
    assert GitTreeSnapshot.from_commit(git_fixture.root, commit_oid).commit_oid == commit_oid


def test_rejects_wrong_object_type_and_bad_oid_width(git_fixture: GitFixture) -> None:
    """Break caught: a blob or truncated digest is accepted where an exact tree is required."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"1.227.0\n")
    blob_oid = git_fixture._git("rev-parse", f"{commit_oid}:pin.md").decode().strip()

    with pytest.raises(GitAuthorityError, match="tree object"):
        GitTreeSnapshot.from_tree(git_fixture.root, blob_oid)
    with pytest.raises(GitAuthorityError, match="full tree OID"):
        GitTreeSnapshot.from_tree(git_fixture.root, blob_oid[:-1])


def test_rejects_absent_repository_root_as_authority_error(tmp_path: Path) -> None:
    """Break caught: a missing repository root leaks a filesystem exception instead of failing closed."""
    with pytest.raises(GitAuthorityError, match="repository root"):
        GitTreeSnapshot.from_commit(tmp_path / "absent", "0" * 40)


@pytest.mark.parametrize("mode", ("120000", "160000"))
def test_rejects_symlink_and_submodule_tree_entries(git_fixture: GitFixture, mode: str) -> None:
    """Break caught: non-regular tracked entries are silently treated as source blobs."""
    object_oid = git_fixture.blob_oid(b"target\n") if mode == "120000" else git_fixture.commit_file("base", b"x")[0]
    commit_oid = git_fixture.commit_index_entry(b"unsafe", mode=mode, object_oid=object_oid)
    tree_oid = git_fixture._git("rev-parse", f"{commit_oid}^{{tree}}").decode().strip()

    with pytest.raises(GitAuthorityError, match="mode|regular|blob"):
        GitTreeSnapshot.from_tree(git_fixture.root, tree_oid)


@pytest.mark.parametrize("path", ("e\u0301.md", "line\nbreak.md", "back\\slash.md"))
def test_rejects_noncanonical_real_tree_paths(git_fixture: GitFixture, path: str) -> None:
    """Break caught: a Git tree name outside canonical NFC POSIX form reaches extractors."""
    blob_oid = git_fixture.blob_oid(b"content\n")
    commit_oid = git_fixture.commit_index_entry(path.encode("utf-8"), mode="100644", object_oid=blob_oid)

    with pytest.raises(GitAuthorityError, match="path"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_rejects_invalid_utf8_tree_path(git_fixture: GitFixture) -> None:
    """Break caught: a byte-only Git path is decoded with replacement instead of rejected."""
    blob_oid = git_fixture.blob_oid(b"content\n")
    commit_oid = git_fixture.commit_index_entry(b"bad-\xff.md", mode="100644", object_oid=blob_oid)

    with pytest.raises(GitAuthorityError, match="UTF-8"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_rejects_corrupt_cat_file_metadata_and_bytes(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: cat-file OID, type, size, or bytes are trusted without cross-checking."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"1.227.0\n")
    import scripts.nautilus_pin_inventory.git_source as git_source

    original = git_source._GitRunner.run

    def corrupt(self, arguments, *, input_data=None, stdout_cap=65_536):
        output = original(self, arguments, input_data=input_data, stdout_cap=stdout_cap)
        if arguments == ("cat-file", "--batch"):
            header, body = output.split(b"\n", 1)
            oid, _kind, size = header.split()
            return b"0" * len(oid) + b" commit " + size + b"\n" + body
        return output

    monkeypatch.setattr(git_source._GitRunner, "run", corrupt)
    with pytest.raises(GitAuthorityError, match="cat-file|blob"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_rejects_missing_truncated_and_oversized_blobs(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: unavailable, partial, or over-limit blob data produces a partial snapshot."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"12345")
    with pytest.raises(GitAuthorityError, match="blob|limit"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid, limits=GitScanLimits(max_blob_bytes=4))

    import scripts.nautilus_pin_inventory.git_source as git_source
    original = git_source._GitRunner.run

    def truncate(self, arguments, *, input_data=None, stdout_cap=65_536):
        output = original(self, arguments, input_data=input_data, stdout_cap=stdout_cap)
        return output[:-2] if arguments == ("cat-file", "--batch") else output

    monkeypatch.setattr(git_source._GitRunner, "run", truncate)
    with pytest.raises(GitAuthorityError, match="truncated|batch"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_rejects_duplicate_nul_and_noncanonical_injected_tree_records(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: malformed ls-tree records are skipped or de-duplicated after parsing."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    import scripts.nautilus_pin_inventory.git_source as git_source

    original = git_source._GitRunner.run

    def malformed(self, arguments, *, input_data=None, stdout_cap=65_536):
        output = original(self, arguments, input_data=input_data, stdout_cap=stdout_cap)
        if arguments[:3] == ("ls-tree", "-r", "-z"):
            record = output.split(b"\0", 1)[0]
            return record + b"\0" + record + b"\0"
        return output

    monkeypatch.setattr(git_source._GitRunner, "run", malformed)
    with pytest.raises(GitAuthorityError, match="duplicate"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_rejects_replace_graft_alternate_and_injected_git_environment(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: Git's mutable ambient authority changes exact-object resolution."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    second_oid = git_fixture.move_head_to_new_commit()
    git_fixture._git("replace", commit_oid, second_oid)
    with pytest.raises(GitAuthorityError, match="replace"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    git_fixture._git("replace", "-d", commit_oid)

    grafts = git_fixture.root / ".git/info/grafts"
    grafts.write_text("deadbeef\n", encoding="ascii")
    with pytest.raises(GitAuthorityError, match="graft"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    grafts.unlink()
    alternates = git_fixture.root / ".git/objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text("/tmp/not-authority\n", encoding="utf-8")
    with pytest.raises(GitAuthorityError, match="alternate"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    alternates.unlink()
    monkeypatch.setenv("GIT_DIR", str(git_fixture.root / ".git"))
    with pytest.raises(GitAuthorityError, match="environment"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_fails_closed_for_timeout_and_entry_path_and_aggregate_limits(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: resource exhaustion or a hung Git process returns an incomplete accepted scan."""
    commit_oid, _ = git_fixture.commit_file("long-name.md", b"12345")
    with pytest.raises(GitAuthorityError, match="path"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid, limits=GitScanLimits(max_path_bytes=2))
    with pytest.raises(GitAuthorityError, match="aggregate"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid, limits=GitScanLimits(max_total_bytes=4))
    with pytest.raises(GitAuthorityError, match="entry"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid, limits=GitScanLimits(max_entries=0))

    import scripts.nautilus_pin_inventory.git_source as git_source
    original = git_source._GitRunner.run

    def timeout(self, arguments, *, input_data=None):
        if arguments == ("rev-parse", "--show-object-format"):
            raise GitAuthorityError("Git command timed out")
        return original(self, arguments, input_data=input_data)

    monkeypatch.setattr(git_source._GitRunner, "run", timeout)
    with pytest.raises(GitAuthorityError, match="timed out"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_returns_deterministically_sorted_paths_and_receipts(git_fixture: GitFixture) -> None:
    """Break caught: tree output order or blob digests vary by host or worktree state."""
    commit_oid, _ = git_fixture.commit_file("z.md", b"z")
    (git_fixture.root / "a.md").write_bytes(b"a")
    git_fixture._git("add", "a.md")
    git_fixture._git("commit", "-qm", "add a")
    commit_oid = git_fixture._git("rev-parse", "HEAD").decode().strip()

    snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)

    assert tuple(blob.path for blob in snapshot.blobs) == ("a.md", "z.md")
    assert snapshot.blob("a.md").sha256 == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    with pytest.raises(GitAuthorityError, match="path"):
        snapshot.blob("missing.md")


def _controlled_executable(path: Path, program: str) -> Path:
    """Create a local executable used only to exercise the real runner boundary."""
    path.write_text(f"#!{sys.executable}\n{program}\n", encoding="utf-8")
    os.chmod(path, 0o755)
    return path


def _controlled_runner(git_fixture: GitFixture, executable: Path, limits: GitScanLimits):
    import scripts.nautilus_pin_inventory.git_source as git_source

    runner = git_source._GitRunner(git_fixture.root, limits)
    runner.executable = executable
    runner._executable_inode = git_source._regular_inode(executable, "Git executable")
    return runner


def _controlled_nonzero_with_pipe_descendant(
    executable: Path, descendant_state: Path
) -> Path:
    descendant_program = (
        "import os, pathlib, time\n"
        f"target = pathlib.Path({str(descendant_state)!r})\n"
        "ready = target.with_name(target.name + '.ready')\n"
        "ready.write_text(f'{os.getpid()} {os.getpgrp()}', encoding='ascii')\n"
        "ready.replace(target)\n"
        "time.sleep(60)"
    )
    return _controlled_executable(
        executable,
        "import pathlib, subprocess, sys, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', {descendant_program!r}])\n"
        f"target = pathlib.Path({str(descendant_state)!r})\n"
        "while not target.is_file():\n"
        "    time.sleep(0.01)\n"
        "raise SystemExit(17)",
    )


def test_killpg_permission_error_preserves_primary_and_unconfirmed_cleanup_receipt(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch
) -> None:
    """Break caught: a failed group signal is recorded as ordinary cleanup success."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    descendant_state = tmp_path / "killpg-permission-descendant.state"
    executable = _controlled_nonzero_with_pipe_descendant(
        tmp_path / "git-killpg-permission", descendant_state
    )
    runner = _controlled_runner(
        git_fixture, executable, GitScanLimits(timeout_seconds=0.2)
    )
    real_killpg = git_source.os.killpg
    real_termination = git_source._ProcessTermination
    terminations = []
    killpg_calls: list[tuple[int, int]] = []

    def capture_termination(*args, **kwargs):
        termination = real_termination(*args, **kwargs)
        terminations.append(termination)
        return termination

    def deny_killpg(pgid: int, requested_signal: int) -> None:
        killpg_calls.append((pgid, requested_signal))
        raise PermissionError(errno.EPERM, "injected group signal denial")

    monkeypatch.setattr(git_source, "_ProcessTermination", capture_termination)
    monkeypatch.setattr(git_source.os, "killpg", deny_killpg)
    caught: BaseException | None = None
    descendant_pid = descendant_pgid = -1
    descendant_survived = False
    receipt = None
    try:
        try:
            runner.run(("probe",))
        except BaseException as exc:
            caught = exc
        assert descendant_state.is_file()
        descendant_pid, descendant_pgid = map(
            int, descendant_state.read_text(encoding="ascii").split()
        )
        assert terminations
        termination = terminations[0]
        descendant_survived = Path(f"/proc/{descendant_pid}").exists()
    finally:
        if terminations:
            try:
                real_killpg(terminations[0].pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if terminations[0].process.poll() is None:
                terminations[0].process.wait(timeout=1.0)
        if descendant_pid > 0:
            deadline = time.monotonic() + 1.0
            while Path(f"/proc/{descendant_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.01)

    assert isinstance(caught, git_source.GitAuthorityAggregateError)
    assert type(caught.primary) is GitAuthorityError
    assert str(caught.primary) == "Git command timed out"
    assert type(caught.cleanup) is GitAuthorityError
    assert "process-group cleanup was not confirmed" in str(caught.cleanup)
    assert [requested for _pgid, requested in killpg_calls[:2]] == [
        signal.SIGKILL,
        signal.SIGKILL,
    ]
    assert all(requested == 0 for _pgid, requested in killpg_calls[2:])
    assert terminations[0].signal_attempts == 2
    assert not terminations[0].group_signal_confirmed
    assert not terminations[0].group_absence_confirmed
    assert terminations[0].leader_reaped
    assert not hasattr(terminations[0], "terminated")
    receipt = terminations[0].cleanup_receipt()
    assert receipt is not None
    assert receipt.signal_attempts == 2
    assert not receipt.group_cleanup_confirmed
    assert receipt.leader_reaped
    assert receipt.streams_closed
    assert all(pgid == terminations[0].pgid for pgid, _ in killpg_calls)
    assert descendant_pgid == terminations[0].pgid
    assert descendant_survived
    assert not Path(f"/proc/{descendant_pid}").exists()


def test_killpg_transient_failure_retries_once_and_confirms_cleanup_receipt(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch
) -> None:
    """Break caught: a transient group-signal failure permanently disables safe cleanup."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    descendant_state = tmp_path / "killpg-retry-descendant.state"
    executable = _controlled_nonzero_with_pipe_descendant(
        tmp_path / "git-killpg-retry", descendant_state
    )
    runner = _controlled_runner(
        git_fixture, executable, GitScanLimits(timeout_seconds=0.2)
    )
    real_killpg = git_source.os.killpg
    real_termination = git_source._ProcessTermination
    terminations = []
    killpg_calls: list[tuple[int, int]] = []

    def capture_termination(*args, **kwargs):
        termination = real_termination(*args, **kwargs)
        terminations.append(termination)
        return termination

    def fail_once_then_signal(pgid: int, requested_signal: int) -> None:
        killpg_calls.append((pgid, requested_signal))
        if len(killpg_calls) == 1:
            raise PermissionError(errno.EPERM, "injected transient signal denial")
        real_killpg(pgid, requested_signal)

    monkeypatch.setattr(git_source, "_ProcessTermination", capture_termination)
    monkeypatch.setattr(git_source.os, "killpg", fail_once_then_signal)
    caught: BaseException | None = None
    descendant_pid = descendant_pgid = -1
    receipt = None
    try:
        try:
            runner.run(("probe",))
        except BaseException as exc:
            caught = exc
        assert descendant_state.is_file()
        descendant_pid, descendant_pgid = map(
            int, descendant_state.read_text(encoding="ascii").split()
        )
        assert terminations
    finally:
        if terminations:
            try:
                real_killpg(terminations[0].pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if terminations[0].process.poll() is None:
                terminations[0].process.wait(timeout=1.0)
        if descendant_pid > 0:
            deadline = time.monotonic() + 1.0
            while Path(f"/proc/{descendant_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.01)

    assert type(caught) is GitAuthorityError
    assert str(caught) == "Git command timed out"
    assert [requested for _pgid, requested in killpg_calls[:2]] == [
        signal.SIGKILL,
        signal.SIGKILL,
    ]
    assert all(requested == 0 for _pgid, requested in killpg_calls[2:])
    assert all(pgid == terminations[0].pgid for pgid, _ in killpg_calls)
    assert terminations[0].signal_attempts == 2
    assert terminations[0].group_signal_confirmed
    assert terminations[0].group_absence_confirmed
    assert terminations[0].leader_reaped
    receipt = terminations[0].cleanup_receipt()
    assert receipt is not None
    assert receipt.signal_attempts == 2
    assert receipt.group_cleanup_confirmed
    assert receipt.leader_reaped
    assert receipt.streams_closed
    assert descendant_pgid == terminations[0].pgid
    assert not Path(f"/proc/{descendant_pid}").exists()


def test_post_popen_input_close_failure_terminates_reaps_and_normalizes(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch
) -> None:
    """Break caught: parent-input close fails after launch before a process owner exists."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    process_ids = tmp_path / "post-popen-input-close.pids"
    executable = _controlled_executable(
        tmp_path / "git-post-popen-input-close",
        "import os, pathlib, subprocess, sys, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"target = pathlib.Path({str(process_ids)!r})\n"
        "ready = target.with_name(target.name + '.ready')\n"
        "ready.write_text(f'{os.getpid()} {child.pid}', encoding='ascii')\n"
        "ready.replace(target)\n"
        "time.sleep(60)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=2.0))
    real_temporary_file = git_source.tempfile.TemporaryFile
    real_popen = git_source.subprocess.Popen
    launched: list[subprocess.Popen[bytes]] = []

    class PostPopenCloseFault:
        def __init__(self) -> None:
            self._delegate = real_temporary_file()
            self._faulted = False

        def __getattr__(self, name: str):
            return getattr(self._delegate, name)

        def close(self) -> None:
            if not self._faulted:
                deadline = time.monotonic() + 2.0
                while not process_ids.is_file() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self._faulted = True
                self._delegate.close()
                raise OSError("post-Popen input close fault")
            self._delegate.close()

    def capture_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(git_source.tempfile, "TemporaryFile", PostPopenCloseFault)
    monkeypatch.setattr(git_source.subprocess, "Popen", capture_popen)
    before_fds = len(tuple(Path("/proc/self/fd").iterdir()))
    caught: BaseException | None = None
    leader_pid = descendant_pid = -1
    leader_gone = descendant_gone = streams_closed = False
    after_fds = -1
    try:
        try:
            runner.run(("probe",), input_data=b"controlled input")
        except BaseException as exc:
            caught = exc
        assert launched
        leader_pid, descendant_pid = map(
            int, process_ids.read_text(encoding="ascii").split()
        )
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and (
            Path(f"/proc/{leader_pid}").exists()
            or Path(f"/proc/{descendant_pid}").exists()
        ):
            time.sleep(0.01)
        leader_gone = not Path(f"/proc/{leader_pid}").exists()
        descendant_gone = not Path(f"/proc/{descendant_pid}").exists()
        streams_closed = all(
            stream is None or stream.closed
            for stream in (launched[0].stdout, launched[0].stderr)
        )
        after_fds = len(tuple(Path("/proc/self/fd").iterdir()))
    finally:
        if launched and launched[0].poll() is None:
            try:
                os.killpg(launched[0].pid, 9)
            except ProcessLookupError:
                pass
            launched[0].wait(timeout=1.0)
        if launched:
            for stream in (launched[0].stdout, launched[0].stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    assert isinstance(caught, GitAuthorityError)
    assert not isinstance(caught, OSError)
    assert leader_gone
    assert descendant_gone
    assert streams_closed
    assert after_fds <= before_fds


@pytest.mark.parametrize("stream_name", ("stdout", "stderr"))
def test_process_stream_close_failure_is_normalized_and_closes_both_streams(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch, stream_name: str
) -> None:
    """Break caught: a process-pipe close leaks a raw error or skips the peer pipe."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    executable = _controlled_executable(
        tmp_path / f"git-{stream_name}-close-fault",
        "import sys\nsys.stdout.buffer.write(b'ok')",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=1.0))
    real_popen = git_source.subprocess.Popen
    launched: list[subprocess.Popen[bytes]] = []

    class StreamCloseFault:
        def __init__(self, delegate) -> None:
            self._delegate = delegate
            self._faulted = False

        def __getattr__(self, name: str):
            return getattr(self._delegate, name)

        def close(self) -> None:
            self._delegate.close()
            if not self._faulted:
                self._faulted = True
                raise OSError(f"sensitive {stream_name} close detail")

    def capture_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        stream = getattr(process, stream_name)
        assert stream is not None
        setattr(process, stream_name, StreamCloseFault(stream))
        launched.append(process)
        return process

    monkeypatch.setattr(git_source.subprocess, "Popen", capture_popen)
    before_fds = len(tuple(Path("/proc/self/fd").iterdir()))
    caught: BaseException | None = None
    streams_closed = False
    after_fds = -1
    try:
        try:
            runner.run(("probe",))
        except BaseException as exc:
            caught = exc
        assert launched
        streams_closed = all(
            stream is None or stream.closed
            for stream in (launched[0].stdout, launched[0].stderr)
        )
        after_fds = len(tuple(Path("/proc/self/fd").iterdir()))
    finally:
        if launched and launched[0].poll() is None:
            try:
                os.killpg(launched[0].pid, 9)
            except ProcessLookupError:
                pass
            launched[0].wait(timeout=1.0)
        if launched:
            for stream in (launched[0].stdout, launched[0].stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    assert isinstance(caught, GitAuthorityError)
    assert not isinstance(caught, OSError)
    assert f"Git {stream_name} stream cleanup failed" in str(caught)
    assert f"sensitive {stream_name} close detail" not in str(caught)
    assert streams_closed
    assert after_fds <= before_fds


def test_selector_close_failure_is_normalized_and_process_streams_close(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch
) -> None:
    """Break caught: selector cleanup exposes its raw close error after a completed child."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    executable = _controlled_executable(
        tmp_path / "git-selector-close-fault",
        "import sys\nsys.stdout.buffer.write(b'ok')",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=1.0))
    real_close = git_source.selectors.DefaultSelector.close
    real_popen = git_source.subprocess.Popen
    launched: list[subprocess.Popen[bytes]] = []

    def selector_close_fault(self) -> None:
        real_close(self)
        raise OSError("sensitive selector close detail")

    def capture_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(git_source.selectors.DefaultSelector, "close", selector_close_fault)
    monkeypatch.setattr(git_source.subprocess, "Popen", capture_popen)
    with pytest.raises(GitAuthorityError, match="Git selector cleanup failed") as raised:
        runner.run(("probe",))
    assert "sensitive selector close detail" not in str(raised.value)
    assert launched
    assert all(
        stream is None or stream.closed
        for stream in (launched[0].stdout, launched[0].stderr)
    )


def test_primary_read_error_and_process_stream_close_failure_are_aggregated(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch
) -> None:
    """Break caught: process-stream cleanup overwrites the primary stream-read failure."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    executable = _controlled_executable(
        tmp_path / "git-primary-and-stream-close-fault",
        "import time\ntime.sleep(60)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=1.0))
    real_popen = git_source.subprocess.Popen
    launched: list[subprocess.Popen[bytes]] = []

    class StdoutCloseFault:
        def __init__(self, delegate) -> None:
            self._delegate = delegate
            self._faulted = False

        def __getattr__(self, name: str):
            return getattr(self._delegate, name)

        def close(self) -> None:
            self._delegate.close()
            if not self._faulted:
                self._faulted = True
                raise OSError("sensitive aggregate close detail")

    def capture_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        assert process.stdout is not None
        process.stdout = StdoutCloseFault(process.stdout)
        launched.append(process)
        return process

    def read_fault(*_args, **_kwargs):
        time.sleep(0.05)
        raise OSError("sensitive primary read detail")

    monkeypatch.setattr(git_source.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(git_source.selectors.DefaultSelector, "select", read_fault)
    caught: BaseException | None = None
    try:
        runner.run(("probe",))
    except BaseException as exc:
        caught = exc
    finally:
        if launched and launched[0].poll() is None:
            try:
                os.killpg(launched[0].pid, 9)
            except ProcessLookupError:
                pass
            launched[0].wait(timeout=1.0)
        if launched:
            for stream in (launched[0].stdout, launched[0].stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    assert isinstance(caught, git_source.GitAuthorityAggregateError)
    assert isinstance(caught.primary, GitAuthorityError)
    assert isinstance(caught.cleanup, GitAuthorityError)
    assert "Git command I/O setup or stream read failed" in str(caught.primary)
    assert "Git stdout stream cleanup failed" in str(caught.cleanup)
    assert "sensitive primary read detail" not in str(caught)
    assert "sensitive aggregate close detail" not in str(caught)
    assert launched
    assert all(
        stream is None or stream.closed
        for stream in (launched[0].stdout, launched[0].stderr)
    )


@pytest.mark.parametrize(
    "launch_error",
    (
        OSError("sensitive /mutable/repository launch detail"),
        subprocess.SubprocessError("sensitive ENV=authority launch detail"),
    ),
    ids=("oserror", "subprocess-error"),
)
def test_setup_normalization_hides_mutable_launch_diagnostics(
    git_fixture: GitFixture, monkeypatch, launch_error: BaseException
) -> None:
    """Break caught: Popen/pre-exec setup exposes raw exception types or mutable diagnostics."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())

    def fail_launch(*_args, **_kwargs):
        raise launch_error

    monkeypatch.setattr(git_source.subprocess, "Popen", fail_launch)
    caught: BaseException | None = None
    try:
        runner.run(("rev-parse", "--git-dir"))
    except BaseException as exc:
        caught = exc

    assert type(caught) is GitAuthorityError
    assert str(caught) == "Git command could not start"


def test_streaming_runner_stops_at_stdout_cap_and_reaps_process_group(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: child stdout is fully captured before the configured cap terminates its process group."""
    child_pid = tmp_path / "child.pid"
    executable = _controlled_executable(
        tmp_path / "git",
        "import pathlib, subprocess, sys, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid), encoding='ascii')\n"
        "sys.stdout.buffer.write(b'x' * 131072); sys.stdout.flush(); time.sleep(60)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(max_total_bytes=32, timeout_seconds=1.0))
    before_fds = len(tuple(Path("/proc/self/fd").iterdir()))
    started = time.monotonic()
    with pytest.raises(GitAuthorityError, match="stdout cap"):
        runner.run(("probe",))
    assert time.monotonic() - started < 2.0
    assert child_pid.is_file()
    pid = int(child_pid.read_text(encoding="ascii"))
    for _ in range(20):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists(), "cap-breached child process was not reaped"
    assert len(tuple(Path("/proc/self/fd").iterdir())) == before_fds


def test_streaming_runner_timeout_terminates_and_reaps_process_group(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: a timeout leaves the Git child or a descendant running after the authority failure."""
    child_pid = tmp_path / "timeout-child.pid"
    executable = _controlled_executable(
        tmp_path / "git-timeout",
        "import pathlib, subprocess, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid), encoding='ascii')\n"
        "time.sleep(60)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=0.15))
    started = time.monotonic()
    with pytest.raises(GitAuthorityError, match="timed out"):
        runner.run(("probe",))
    assert time.monotonic() - started < 2.0
    pid = int(child_pid.read_text(encoding="ascii"))
    for _ in range(20):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists(), "timed-out child process was not reaped"


@pytest.mark.parametrize(
    "limits",
    (
        GitScanLimits(max_entries=True),
        GitScanLimits(max_path_bytes=1.5),
        GitScanLimits(max_blob_bytes=float("inf")),
        GitScanLimits(max_total_bytes=float("nan")),
        GitScanLimits(timeout_seconds=float("inf")),
        GitScanLimits(timeout_seconds=float("nan")),
        GitScanLimits(max_entries=20_001),
        GitScanLimits(timeout_seconds=30.1),
    ),
)
def test_limits_reject_nonintegral_nonfinite_and_excessive_values(git_fixture: GitFixture, limits: GitScanLimits) -> None:
    """Break caught: boolean, float, non-finite, or excessive limits disable bounded source scanning."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    with pytest.raises(GitAuthorityError, match="limits"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid, limits=limits)


def test_executable_inode_change_is_rejected_before_next_command(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: a replaced Git executable is used after custody was established."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    executable = _controlled_executable(tmp_path / "git", "raise SystemExit(0)")
    runner = _controlled_runner(git_fixture, executable, GitScanLimits())
    replacement = _controlled_executable(tmp_path / "replacement", "raise SystemExit(0)")
    replacement.replace(executable)
    with pytest.raises(GitAuthorityError, match="executable changed"):
        runner.run(("probe",))


def test_body_only_corruption_with_unchanged_batch_header_is_rejected(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: blob-body corruption is accepted when the cat-file header still names the expected blob."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"authoritative bytes\n")
    real_git = shutil.which("git")
    assert real_git
    executable = _controlled_executable(
        tmp_path / "git-corrupt",
        "import subprocess, sys\n"
        f"result = subprocess.run([{real_git!r}, *sys.argv[1:]], input=sys.stdin.buffer.read(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "data = result.stdout\n"
        "if 'cat-file' in sys.argv and '--batch' in sys.argv:\n"
        "    header_end = data.find(b'\\n') + 1\n"
        "    if header_end < len(data): data = data[:header_end] + bytes([data[header_end] ^ 1]) + data[header_end + 1:]\n"
        "sys.stdout.buffer.write(data); sys.stderr.buffer.write(result.stderr); raise SystemExit(result.returncode)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits())
    with pytest.raises(GitAuthorityError, match="object OID"):
        GitTreeSnapshot._from_exact_tree(
            runner,
            commit_oid,
            git_fixture._git("rev-parse", f"{commit_oid}^{{tree}}").decode().strip(),
            "sha1",
        )


def test_sha256_repository_uses_64_hex_oids_and_independent_sha256_receipt(tmp_path: Path) -> None:
    """Break caught: SHA-256 repositories are parsed as SHA-1 or skip independent blob receipts."""
    fixture = GitFixture(tmp_path / "sha256", object_format="sha256")
    commit_oid, data = fixture.commit_file("pin.md", b"sha256 repository\n")
    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    assert len(snapshot.commit_oid or "") == 64
    assert len(snapshot.blob("pin.md").blob_oid) == 64
    assert snapshot.blob("pin.md").sha256 == hashlib.sha256(data).hexdigest()


def test_pack_only_requested_closure_materializes_exact_receipts(git_fixture: GitFixture) -> None:
    """Break caught: source authority rejects ordinary packed Git objects or reads a live canonical store."""
    commit_oid, first = git_fixture.commit_file("nested/first.md", b"packed first\n")
    (git_fixture.root / "nested" / "second.md").write_bytes(b"packed second\n")
    git_fixture._git("add", "nested/second.md")
    git_fixture._git("commit", "-qm", "second packed source")
    commit_oid = git_fixture._git("rev-parse", "HEAD").decode().strip()
    tree_oid = git_fixture._git("rev-parse", f"{commit_oid}^{{tree}}").decode().strip()
    blob_oids = [
        git_fixture._git("rev-parse", f"{commit_oid}:nested/first.md").decode().strip(),
        git_fixture._git("rev-parse", f"{commit_oid}:nested/second.md").decode().strip(),
    ]
    git_fixture._git("gc", "--prune=now")
    objects = git_fixture.root / ".git/objects"
    for oid in (commit_oid, tree_oid, *blob_oids):
        assert not (objects / oid[:2] / oid[2:]).exists(), f"{oid} unexpectedly remained loose"

    snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)

    assert snapshot.tree_oid == tree_oid
    assert snapshot.blob("nested/first.md").data == first
    assert snapshot.blob("nested/second.md").sha256 == hashlib.sha256(b"packed second\n").hexdigest()


def test_private_git_child_uses_inherited_descriptor_object_directory(
    git_fixture: GitFixture, monkeypatch
) -> None:
    """Break caught: a private Git child reopens its object directory through a mutable pathname."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"descriptor-rooted authority\n")
    git_fixture._git("gc", "--prune=now")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    real_popen = git_source.subprocess.Popen
    private_calls: list[
        tuple[str, tuple[int, ...], tuple[int, ...] | None, tuple[int, ...], str]
    ] = []
    capture_private_calls = True

    def private_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            stat.S_IFMT(metadata.st_mode),
            metadata.st_uid,
            metadata.st_gid,
            stat.S_IMODE(metadata.st_mode),
        )

    def capture_popen(*args, **kwargs):
        object_directory = kwargs["env"].get("GIT_OBJECT_DIRECTORY")
        if object_directory is not None and capture_private_calls:
            bootstrap = runner._pack_bootstraps[-1]
            pass_fds = tuple(kwargs.get("pass_fds", ()))
            descriptor_identity = None
            if object_directory.startswith("/proc/self/fd/"):
                descriptor = int(object_directory.rsplit("/", 1)[1])
                descriptor_identity = private_identity(os.fstat(descriptor))
            private_calls.append(
                (
                    object_directory,
                    pass_fds,
                    descriptor_identity,
                    private_identity(os.fstat(bootstrap.destination_fd)),
                    str(bootstrap.destination),
                )
            )
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(git_source.subprocess, "Popen", capture_popen)
    try:
        runner.seal_object_store(commit_oid, "commit", "sha1")
    finally:
        capture_private_calls = False
        runner.close(suppress_terminal_error=True)

    assert private_calls
    for object_directory, pass_fds, descriptor_identity, pinned_identity, mutable_path in private_calls:
        assert object_directory.startswith("/proc/self/fd/")
        descriptor = int(object_directory.rsplit("/", 1)[1])
        assert descriptor in pass_fds
        assert descriptor_identity == pinned_identity
        assert object_directory != mutable_path


def test_copied_closure_git_child_uses_inherited_descriptor_object_directory(
    git_fixture: GitFixture, monkeypatch
) -> None:
    """Break caught: later private reads reopen the copied closure through its temporary pathname."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"copied closure authority\n")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", "sha1")
    private_path = runner._private_objects
    assert private_path is not None
    pinned_identity = os.stat(private_path, follow_symlinks=False)
    expected_identity = (
        pinned_identity.st_dev,
        pinned_identity.st_ino,
        stat.S_IFMT(pinned_identity.st_mode),
        pinned_identity.st_uid,
        pinned_identity.st_gid,
        stat.S_IMODE(pinned_identity.st_mode),
    )
    real_popen = git_source.subprocess.Popen
    private_calls: list[tuple[str, tuple[int, ...], tuple[int, ...] | None]] = []
    capture_private_calls = True

    def capture_popen(*args, **kwargs):
        object_directory = kwargs["env"].get("GIT_OBJECT_DIRECTORY")
        if object_directory is not None and capture_private_calls:
            descriptor_identity = None
            if object_directory.startswith("/proc/self/fd/"):
                descriptor = int(object_directory.rsplit("/", 1)[1])
                metadata = os.fstat(descriptor)
                descriptor_identity = (
                    metadata.st_dev,
                    metadata.st_ino,
                    stat.S_IFMT(metadata.st_mode),
                    metadata.st_uid,
                    metadata.st_gid,
                    stat.S_IMODE(metadata.st_mode),
                )
            private_calls.append(
                (object_directory, tuple(kwargs.get("pass_fds", ())), descriptor_identity)
            )
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(git_source.subprocess, "Popen", capture_popen)
    try:
        assert runner.run(("cat-file", "-t", commit_oid)) == b"commit\n"
    finally:
        capture_private_calls = False
        runner.close(suppress_terminal_error=True)

    assert private_calls
    for object_directory, pass_fds, descriptor_identity in private_calls:
        assert object_directory.startswith("/proc/self/fd/")
        descriptor = int(object_directory.rsplit("/", 1)[1])
        assert descriptor in pass_fds
        assert descriptor_identity == expected_identity
        assert object_directory != str(private_path)


@pytest.mark.parametrize("swap_level", ("root", "objects", "pack"))
def test_transient_private_namespace_swap_cannot_supply_non_authoritative_pack(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch, swap_level: str
) -> None:
    """Break caught: a restored private pathname can transiently redirect Git to a donor pack."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, donor_bytes = git_fixture.commit_file(
        "pin.md", b"non-authoritative donor pack bytes\n"
    )
    git_fixture._git("gc", "--prune=now")
    source_pack_directory = git_fixture.root / ".git/objects/pack"
    source_pack = next(source_pack_directory.glob("*.pack"))
    source_index = source_pack.with_suffix(".idx")
    donor_root = tmp_path / f"donor-{swap_level}"
    donor_pack_directory = donor_root / "objects/pack"
    donor_pack_directory.mkdir(parents=True, mode=0o700)
    shutil.copy2(source_pack, donor_pack_directory / source_pack.name)
    shutil.copy2(source_index, donor_pack_directory / source_index.name)
    os.chmod(source_pack, 0o600)
    source_pack.write_bytes(b"operation-start pack cannot supply the requested object")
    marker = tmp_path / f"{swap_level}-swap-observed"
    real_git = shutil.which("git")
    assert real_git
    wrapper = _controlled_executable(
        tmp_path / f"git-transient-{swap_level}-swap",
        "import os, pathlib, subprocess, sys\n"
        f"real_git = {real_git!r}\n"
        f"swap_level = {swap_level!r}\n"
        f"donor_root = pathlib.Path({str(donor_root)!r})\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "object_text = os.environ.get('GIT_OBJECT_DIRECTORY')\n"
        "authority_fds = (int(object_text.rsplit('/', 1)[1]),) if object_text is not None and object_text.startswith('/proc/self/fd/') else ()\n"
        "object_path = pathlib.Path(os.readlink(object_text) if object_text is not None and object_text.startswith('/proc/self/fd/') else object_text) if object_text is not None else None\n"
            "should_swap = object_path is not None and 'p1-u00-pack-' in str(object_path) and 'cat-file' in sys.argv\n"
        "if not should_swap:\n"
        "    raise SystemExit(subprocess.call([real_git, *sys.argv[1:]], pass_fds=authority_fds))\n"
        "selected = {'root': object_path.parent, 'objects': object_path, 'pack': object_path / 'pack'}[swap_level]\n"
        "donor = {'root': donor_root, 'objects': donor_root / 'objects', 'pack': donor_root / 'objects' / 'pack'}[swap_level]\n"
        "parked = selected.with_name(selected.name + '.operation-start')\n"
        "child = subprocess.Popen([real_git, *sys.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=authority_fds)\n"
        "swapped = False\n"
        "try:\n"
        "    for request in sys.stdin.buffer:\n"
        "        selected.rename(parked); donor.rename(selected); swapped = True\n"
        "        marker.write_text('pre-request', encoding='ascii')\n"
        "        child.stdin.write(request); child.stdin.flush()\n"
        "        header = child.stdout.readline(); size = int(header.rstrip(b'\\n').rsplit(b' ', 1)[1]); body = child.stdout.read(size + 1)\n"
        "        sys.stdout.buffer.write(header + body); sys.stdout.buffer.flush()\n"
        "        selected.rename(donor); parked.rename(selected); swapped = False\n"
        "finally:\n"
        "    if swapped:\n"
        "        selected.rename(donor); parked.rename(selected)\n"
        "    child.stdin.close(); child.wait()",
    )
    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(wrapper))

    try:
        with pytest.raises(GitAuthorityError) as raised:
            GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
        assert "mutation guard observed authority drift" in str(raised.value)
        assert marker.read_text(encoding="ascii") == "pre-request"
        assert donor_bytes == b"non-authoritative donor pack bytes\n"
    finally:
        shutil.rmtree(donor_root, ignore_errors=True)
        marker.unlink(missing_ok=True)
        wrapper.unlink(missing_ok=True)


@pytest.mark.parametrize("drift", ("mode", "owner"))
def test_private_directory_identity_rejects_mode_and_owner_drift(
    git_fixture: GitFixture, monkeypatch, drift: str
) -> None:
    """Break caught: retained private directories bind only device, inode, and file type."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"private directory identity\n")
    git_fixture._git("gc", "--prune=now")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", "sha1")
    assert runner._persistent_reader is None
    bootstrap = runner._pack_bootstraps[0]
    try:
        if drift == "mode":
            os.fchmod(bootstrap.root_fd, 0o755)
            with pytest.raises(GitAuthorityError, match="private|bootstrap|mode"):
                git_source._verify_private_pack_bootstrap(bootstrap)
        else:
            real_fstat = git_source.os.fstat

            def owner_drift(descriptor: int) -> os.stat_result:
                metadata = real_fstat(descriptor)
                if descriptor != bootstrap.destination_pack_fd:
                    return metadata
                fields = list(metadata)
                fields[4] = metadata.st_uid + 1
                return os.stat_result(fields)

            with monkeypatch.context() as context:
                context.setattr(git_source.os, "fstat", owner_drift)
                with pytest.raises(GitAuthorityError, match="private|bootstrap|owner"):
                    git_source._verify_private_pack_bootstrap(bootstrap)
    finally:
        os.fchmod(bootstrap.root_fd, 0o700)
        runner.close(suppress_terminal_error=True)


@pytest.mark.parametrize("drift", ("mode", "owner"))
def test_t6_migrations_04_05_public_snapshot_rejects_active_private_drift(
    packed_git_fixture, monkeypatch, drift: str,
) -> None:
    """The public snapshot never publishes after active reader authority drifts."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    original_read = git_source._BootstrapBatchReader._read
    original_fstat = git_source.os.fstat
    readers = []
    drifted = False

    def mutate_after_header(self, size: int) -> bytes:
        nonlocal drifted
        readers.append(self)
        result = original_read(self, size)
        if not drifted and size == 1 and result == b"\n":
            drifted = True
            if drift == "mode":
                os.fchmod(self.bootstrap.root_fd, 0o755)
        return result

    def owner_drift(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        if drift == "owner" and drifted and readers and descriptor == readers[-1].bootstrap.destination_pack_fd:
            fields = list(metadata)
            fields[4] = metadata.st_uid + 1
            return os.stat_result(fields)
        return metadata

    monkeypatch.setattr(git_source._BootstrapBatchReader, "_read", mutate_after_header)
    monkeypatch.setattr(git_source.os, "fstat", owner_drift)
    try:
        with pytest.raises((GitAuthorityError, git_source.GitAuthorityAggregateError)):
            GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    finally:
        monkeypatch.setattr(git_source.os, "fstat", original_fstat)
        for reader in readers:
            try:
                os.fchmod(reader.bootstrap.root_fd, 0o700)
            except OSError:
                pass
    assert drifted
    assert readers
    assert all(reader.closed for reader in readers)
    assert all(reader.process is None or reader.process.poll() is not None for reader in readers)


def test_unproved_pack_index_v3_is_rejected(tmp_path: Path) -> None:
    """Break caught: the v2 parser speculatively treats an unproved pack-index v3 as v2."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    index = tmp_path / "pack-unproved.idx"
    index.write_bytes(b"\xfftOc\x00\x00\x00\x03" + b"\x00" * (1024 + 40))
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(GitAuthorityError, match="unsupported Git pack index format"):
            git_source._pack_index_contains(
                index.name,
                directory_fd,
                "00" * 20,
                "sha1",
                GitScanLimits(),
                time.monotonic() + 1,
            )
    finally:
        os.close(directory_fd)


def test_pack_index_persistent_close_failure_rejects_public_snapshot(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: an accepted public snapshot retains a pack-index descriptor."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    before_fds = _open_descriptor_count()
    real_close = os.close
    target_descriptor: int | None = None
    close_attempts = 0
    snapshot = None
    caught: BaseException | None = None

    def fail_persistently(descriptor: int) -> None:
        nonlocal target_descriptor, close_attempts
        if target_descriptor is None and _is_pack_index_descriptor(descriptor):
            target_descriptor = descriptor
        if descriptor == target_descriptor:
            close_attempts += 1
            raise OSError(errno.EIO, "injected persistent pack-index close failure")
        real_close(descriptor)

    monkeypatch.setattr(git_source.os, "close", fail_persistently)
    try:
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc
    finally:
        monkeypatch.setattr(git_source.os, "close", real_close)
        if target_descriptor is not None:
            try:
                real_close(target_descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise

    assert snapshot is None
    assert isinstance(
        caught, (GitAuthorityError, git_source.GitAuthorityAggregateError)
    )
    assert "Git pack-index descriptor cleanup was not confirmed" in str(caught)
    assert close_attempts == 2
    assert target_descriptor is not None
    with pytest.raises(OSError) as raised:
        os.fstat(target_descriptor)
    assert raised.value.errno == errno.EBADF
    assert _open_descriptor_count() == before_fds


def test_pack_index_close_failure_retries_once_for_same_owned_descriptor(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: a transient same-index close failure leaks instead of retrying once."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    before_fds = _open_descriptor_count()
    real_close = os.close
    target_descriptor: int | None = None
    close_attempts = 0
    injected = False
    snapshot = None

    def fail_once(descriptor: int) -> None:
        nonlocal target_descriptor, close_attempts, injected
        if target_descriptor is None and _is_pack_index_descriptor(descriptor):
            target_descriptor = descriptor
        if descriptor == target_descriptor and not injected:
            close_attempts += 1
            if close_attempts == 1:
                raise OSError(errno.EIO, "injected transient pack-index close failure")
            injected = True
        real_close(descriptor)

    monkeypatch.setattr(git_source.os, "close", fail_once)
    try:
        snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    finally:
        monkeypatch.setattr(git_source.os, "close", real_close)
        if target_descriptor is not None:
            try:
                real_close(target_descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise

    assert snapshot is not None
    assert snapshot.blob("pin.md").data == expected
    assert close_attempts == 2
    assert target_descriptor is not None
    with pytest.raises(OSError) as raised:
        os.fstat(target_descriptor)
    assert raised.value.errno == errno.EBADF
    assert _open_descriptor_count() == before_fds


def test_pack_index_close_error_after_actual_close_is_confirmed_without_retry(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: close took effect, but EBADF is never checked before acceptance."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    before_fds = _open_descriptor_count()
    real_close = os.close
    real_fstat = os.fstat
    target_descriptor: int | None = None
    close_attempts = 0
    awaiting_confirmation = False
    ebadf_confirmations = 0
    unexpected_retry_attempts = 0
    injected = False
    snapshot = None

    def close_then_error(descriptor: int) -> None:
        nonlocal target_descriptor, close_attempts, awaiting_confirmation
        nonlocal unexpected_retry_attempts, injected
        if target_descriptor is None and _is_pack_index_descriptor(descriptor):
            target_descriptor = descriptor
        if descriptor == target_descriptor and not injected:
            close_attempts += 1
            real_close(descriptor)
            awaiting_confirmation = True
            injected = True
            raise OSError(errno.EIO, "injected post-close pack-index error")
        if descriptor == target_descriptor and awaiting_confirmation:
            unexpected_retry_attempts += 1
        real_close(descriptor)

    def record_confirmation(descriptor: int):
        nonlocal awaiting_confirmation, ebadf_confirmations
        try:
            return real_fstat(descriptor)
        except OSError as exc:
            if (
                descriptor == target_descriptor
                and awaiting_confirmation
                and exc.errno == errno.EBADF
            ):
                awaiting_confirmation = False
                ebadf_confirmations += 1
            raise

    monkeypatch.setattr(git_source.os, "close", close_then_error)
    monkeypatch.setattr(git_source.os, "fstat", record_confirmation)
    try:
        snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    finally:
        monkeypatch.setattr(git_source.os, "close", real_close)
        monkeypatch.setattr(git_source.os, "fstat", real_fstat)

    assert snapshot is not None
    assert snapshot.blob("pin.md").data == expected
    assert close_attempts == 1
    assert ebadf_confirmations == 1
    assert unexpected_retry_attempts == 0
    assert target_descriptor is not None
    with pytest.raises(OSError) as raised:
        os.fstat(target_descriptor)
    assert raised.value.errno == errno.EBADF
    assert _open_descriptor_count() == before_fds


def test_pack_index_ambiguous_fd_reuse_close_failure_rejects_without_retry(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: an index FD reused for a foreign file is closed by a blind retry."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    foreign_path = fixture.root / "foreign-descriptor.txt"
    foreign_path.write_bytes(b"foreign descriptor remains owned by the reviewer\n")
    before_fds = _open_descriptor_count()
    real_close = os.close
    target_descriptor: int | None = None
    foreign_descriptor: int | None = None
    close_attempts = 0
    snapshot = None
    caught: BaseException | None = None
    foreign_open_after_call = False

    def reuse_then_error(descriptor: int) -> None:
        nonlocal target_descriptor, foreign_descriptor, close_attempts
        if target_descriptor is None and _is_pack_index_descriptor(descriptor):
            target_descriptor = descriptor
        if descriptor == target_descriptor:
            close_attempts += 1
            real_close(descriptor)
            foreign_descriptor = os.open(foreign_path, os.O_RDONLY)
            assert foreign_descriptor == descriptor
            raise OSError(errno.EIO, "injected ambiguous pack-index close failure")
        real_close(descriptor)

    monkeypatch.setattr(git_source.os, "close", reuse_then_error)
    try:
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc
        if foreign_descriptor is not None:
            foreign_open_after_call = os.pread(foreign_descriptor, 7, 0) == b"foreign"
    finally:
        monkeypatch.setattr(git_source.os, "close", real_close)
        if foreign_descriptor is not None:
            try:
                real_close(foreign_descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise

    assert snapshot is None
    assert isinstance(
        caught, (GitAuthorityError, git_source.GitAuthorityAggregateError)
    )
    assert "Git pack-index descriptor identity is ambiguous" in str(caught)
    assert close_attempts == 1
    assert target_descriptor is not None
    assert foreign_descriptor == target_descriptor
    assert foreign_open_after_call
    with pytest.raises(OSError) as raised:
        os.fstat(foreign_descriptor)
    assert raised.value.errno == errno.EBADF
    assert _open_descriptor_count() == before_fds


@pytest.mark.parametrize("operation", ("replace", "corrupt", "late-unrelated"))
def test_pinned_pack_source_drift_is_rejected_but_late_unrelated_pack_is_ignored(git_fixture: GitFixture, tmp_path: Path, monkeypatch, operation: str) -> None:
    """Break caught: a used pack can change after bootstrap, or a late unrelated pack changes the exact closure."""
    commit_oid, expected = git_fixture.commit_file("pin.md", b"packed authority\n")
    git_fixture._git("gc", "--prune=now")
    pack = next((git_fixture.root / ".git/objects/pack").glob("*.pack"))
    phase = tmp_path / f"pack-race-{operation}.phase"
    real_git = shutil.which("git")
    assert real_git
    wrapper = _controlled_executable(
        tmp_path / "git-pack-race",
        "import os, pathlib, subprocess, sys\n"
        f"pack = pathlib.Path({str(pack)!r})\n"
        f"operation = {operation!r}\n"
        f"phase = pathlib.Path({str(phase)!r})\n"
        "object_text = os.environ.get('GIT_OBJECT_DIRECTORY')\n"
        "authority_fds = (int(object_text.rsplit('/', 1)[1]),) if object_text is not None and object_text.startswith('/proc/self/fd/') else ()\n"
        "if 'cat-file' not in sys.argv or '--batch' not in sys.argv:\n"
        f"    raise SystemExit(subprocess.call([{real_git!r}, *sys.argv[1:]], pass_fds=authority_fds))\n"
        f"child = subprocess.Popen([{real_git!r}, *sys.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=authority_fds)\n"
        "for request in sys.stdin.buffer:\n"
        "    child.stdin.write(request); child.stdin.flush()\n"
        "    header = child.stdout.readline(); size = int(header.rstrip(b'\\n').rsplit(b' ', 1)[1]); body = child.stdout.read(size); delimiter = child.stdout.read(1)\n"
        "    sys.stdout.buffer.write(header + body); sys.stdout.buffer.flush()\n"
        "    phase.write_text('post-body-pre-delimiter ' + str(child.pid), encoding='ascii')\n"
        "    if operation == 'replace':\n"
        "        replacement = pack.with_name('replacement.pack'); replacement.write_bytes(pack.read_bytes()); replacement.replace(pack)\n"
        "    elif operation == 'corrupt':\n"
        "        os.chmod(pack, 0o600); pack.write_bytes(b'corrupt')\n"
        "    else:\n"
        "        pack.with_name('late-unrelated.pack').write_bytes(b'unrelated')\n"
        "    sys.stdout.buffer.write(delimiter); sys.stdout.buffer.flush()\n"
        "child.stdin.close(); raise SystemExit(child.wait())",
    )
    import scripts.nautilus_pin_inventory.git_source as git_source
    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(wrapper))

    try:
        if operation == "late-unrelated":
            assert GitTreeSnapshot.from_commit(git_fixture.root, commit_oid).blob("pin.md").data == expected
            assert phase.read_text(encoding="ascii").startswith("post-body-pre-delimiter ")
        else:
            snapshot = None
            caught: BaseException | None = None
            try:
                snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
            assert snapshot is None
            assert type(caught) is git_source.GitAuthorityAggregateError
            assert isinstance(caught, git_source.GitAuthorityAggregateError)
            assert "Git pack namespace changed during source snapshot" in str(caught.primary)
            assert "private Git pack bootstrap entry changed during cleanup" in str(caught.cleanup)
            phase_text = phase.read_text(encoding="ascii")
            assert phase_text.startswith("post-body-pre-delimiter ")
            child_pid = int(phase_text.rsplit(" ", 1)[1])
            deadline = time.monotonic() + 2.0
            while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not Path(f"/proc/{child_pid}").exists()
    finally:
        phase.unlink(missing_ok=True)


def _object_database_digest(root: Path) -> str:
    objects = root / ".git/objects"
    digest = hashlib.sha256()
    for path in sorted((path for path in objects.rglob("*") if path.is_file()), key=lambda value: str(value)):
        digest.update(str(path.relative_to(objects)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_transient_alternate_cannot_supply_a_missing_primary_blob(git_fixture: GitFixture, tmp_path: Path, monkeypatch) -> None:
    """Break caught: a blob available only through a temporary alternate is accepted as local source authority."""
    commit_oid, expected = git_fixture.commit_file("pin.md", b"alternate authority bytes\n")
    blob_oid = git_fixture._git("rev-parse", f"{commit_oid}:pin.md").decode().strip()
    source_object = git_fixture.root / ".git/objects" / blob_oid[:2] / blob_oid[2:]
    alternate_objects = tmp_path / "alternate-objects"
    alternate_objects.mkdir()
    (alternate_objects / blob_oid[:2]).mkdir()
    shutil.copy2(source_object, alternate_objects / blob_oid[:2] / blob_oid[2:])
    source_object.unlink()
    before = _object_database_digest(git_fixture.root)
    alternates = git_fixture.root / ".git/objects/info/alternates"
    real_git = shutil.which("git")
    assert real_git
    wrapper = _controlled_executable(
        tmp_path / "git-alternate",
        "import pathlib, subprocess, sys\n"
        f"alternates = pathlib.Path({str(alternates)!r})\n"
        f"alternate = {str(alternate_objects)!r}\n"
        "if 'cat-file' in sys.argv and '--batch' in sys.argv: alternates.write_text(alternate + '\\n', encoding='utf-8')\n"
        "try:\n"
        f"    result = subprocess.run([{real_git!r}, *sys.argv[1:]], input=sys.stdin.buffer.read(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "finally:\n"
        "    alternates.unlink(missing_ok=True)\n"
        "sys.stdout.buffer.write(result.stdout); sys.stderr.buffer.write(result.stderr); raise SystemExit(result.returncode)",
    )
    import scripts.nautilus_pin_inventory.git_source as git_source
    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(wrapper))

    with pytest.raises(GitAuthorityError, match="primary|cat-file|missing|namespace"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    assert not alternates.exists()
    assert _object_database_digest(git_fixture.root) == before
    assert expected == b"alternate authority bytes\n"


def test_partial_clone_missing_blob_fails_without_hydrating_local_object_database(tmp_path: Path) -> None:
    """Break caught: a promisor configuration fetches a missing blob from a remote during immutable local scanning."""
    source = GitFixture(tmp_path / "source")
    commit_oid, _ = source.commit_file("pin.md", b"promisor authority bytes\n")
    blob_oid = source._git("rev-parse", f"{commit_oid}:pin.md").decode().strip()
    remote = tmp_path / "remote.git"
    clone = tmp_path / "partial"
    source._git("clone", "--bare", str(source.root), str(remote))
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=remote, check=True)
    completed = subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", f"file://{remote}", str(clone)], capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    absent = subprocess.run(["git", "cat-file", "-e", f"{blob_oid}^{{blob}}"], cwd=clone, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}, capture_output=True, check=False)
    assert absent.returncode != 0, "fixture unexpectedly hydrated the promised blob before the scanner ran"
    before = _object_database_digest(clone)
    helper_seen = tmp_path / "promisor-helper-invoked"
    real_upload_pack = shutil.which("git-upload-pack")
    assert real_upload_pack
    helper = _controlled_executable(
        tmp_path / "upload-pack-probe",
        "import os, pathlib, sys\n"
        f"pathlib.Path({str(helper_seen)!r}).write_text('invoked', encoding='ascii')\n"
        f"os.execv({real_upload_pack!r}, [{real_upload_pack!r}, *sys.argv[1:]])",
    )
    subprocess.run(["git", "config", "remote.origin.uploadpack", str(helper)], cwd=clone, check=True)

    with pytest.raises(GitAuthorityError, match="primary|cat-file|missing|namespace"):
        GitTreeSnapshot.from_commit(clone, commit_oid)
    assert _object_database_digest(clone) == before
    assert not helper_seen.exists(), "promisor helper or remote was invoked during local source scan"


def test_requested_closure_ignores_unrelated_oversized_object_store_content(git_fixture: GitFixture) -> None:
    """Break caught: sealing copies the whole object store rather than just the requested exact closure."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    primary = git_fixture.root / ".git/objects"
    unrelated = git_fixture.root / ".git/objects/ff/unrelated"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"u" * 10_000_000)

    snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid, limits=GitScanLimits(max_total_bytes=1024))

    assert snapshot.blob("pin.md").data == b"x"


def test_requested_closure_ignores_unrelated_object_store_symlink(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: a symlink outside the requested closure blocks an otherwise pinned source tree."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    external = tmp_path / "external"
    external.write_bytes(b"not an object")
    (git_fixture.root / ".git/objects/symlink").symlink_to(external)
    snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    assert snapshot.blob("pin.md").data == b"x"


def test_owned_temporary_root_failure_closes_all_captured_descriptors(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: owned-root failure after multi-directory capture leaks source-store descriptors."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("nested/deeper/pin.md", b"x")
    before = len(tuple(Path("/proc/self/fd").iterdir()))

    def fail_owned_root(cls, parent) -> None:
        raise GitAuthorityError("private Git object-store temporary root is unavailable")

    monkeypatch.setattr(
        git_source._OwnedTemporaryRoot,
        "create",
        classmethod(fail_owned_root),
    )
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    with pytest.raises(GitAuthorityError, match="private Git object-store"):
        runner.seal_object_store(commit_oid, "commit", "sha1")
    assert len(tuple(Path("/proc/self/fd").iterdir())) == before


def test_exited_launcher_descendant_holding_pipe_is_killed_on_timeout(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: an exited launcher leaves a pipe-holding process alive when the runner deadline expires."""
    child_pid = tmp_path / "orphan.pid"
    executable = _controlled_executable(
        tmp_path / "git-exit",
        "import pathlib, subprocess\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid), encoding='ascii')",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=0.15))
    with pytest.raises(GitAuthorityError, match="timed out"):
        runner.run(("probe",))
    pid = int(child_pid.read_text(encoding="ascii"))
    for _ in range(20):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists(), "exited-launcher descendant remained alive"


def test_terminal_authority_failure_cleans_private_store(git_fixture: GitFixture) -> None:
    """Break caught: a terminal alternate failure leaves the task-owned private object store behind."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", "sha1")
    private = runner._private_objects
    assert private is not None
    alternates = git_fixture.root / ".git/objects/info/alternates"
    alternates.write_text("/tmp/not-authority\n", encoding="utf-8")
    try:
        with pytest.raises(GitAuthorityError, match="alternates"):
            runner.close()
    finally:
        alternates.unlink(missing_ok=True)
    assert not private.exists()


def test_terminal_executable_custody_failure_cleans_private_store(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: an executable replacement found only at terminal validation leaks the private object store."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    executable = _controlled_executable(tmp_path / "git-terminal", "raise SystemExit(0)")
    runner = _controlled_runner(git_fixture, executable, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", "sha1")
    private = runner._private_objects
    assert private is not None
    replacement = _controlled_executable(tmp_path / "replacement", "raise SystemExit(0)")
    replacement.replace(executable)
    with pytest.raises(GitAuthorityError, match="executable changed"):
        runner.close()
    assert not private.exists()


def test_stream_reader_exception_kills_blocking_process_group(git_fixture: GitFixture, tmp_path: Path, monkeypatch) -> None:
    """Break caught: an unexpected stream-reader exception leaks a blocking child process and descriptors."""
    child_pid = tmp_path / "reader-error.pid"
    executable = _controlled_executable(
        tmp_path / "git-reader-error",
        "import pathlib, subprocess, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid), encoding='ascii')\n"
        "time.sleep(60)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=2.0))
    import scripts.nautilus_pin_inventory.git_source as git_source
    def reader_fault(*_args, **_kwargs):
        time.sleep(0.1)
        raise OSError("reader fault")

    monkeypatch.setattr(git_source.selectors.DefaultSelector, "select", reader_fault)
    with pytest.raises(GitAuthorityError, match="I/O setup|stream read"):
        runner.run(("probe",))
    pid = int(child_pid.read_text(encoding="ascii"))
    for _ in range(20):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists(), "reader-fault child process was not reaped"


def test_selector_registration_failure_kills_blocking_process_group(git_fixture: GitFixture, tmp_path: Path, monkeypatch) -> None:
    """Break caught: selector registration fails before the cleanup handler and leaves a process group alive."""
    child_pid = tmp_path / "selector-error.pid"
    executable = _controlled_executable(
        tmp_path / "git-selector-error",
        "import pathlib, subprocess, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid), encoding='ascii')\n"
        "time.sleep(60)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=2.0))
    import scripts.nautilus_pin_inventory.git_source as git_source

    def registration_fault(*_args, **_kwargs):
        time.sleep(0.1)
        raise OSError("selector registration fault")

    monkeypatch.setattr(git_source.selectors.DefaultSelector, "register", registration_fault)
    with pytest.raises(GitAuthorityError, match="I/O setup|stream read"):
        runner.run(("probe",))
    pid = int(child_pid.read_text(encoding="ascii"))
    for _ in range(20):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists(), "selector-fault child process was not reaped"


def test_nonzero_launcher_with_detached_pipe_descendant_is_killed(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: a nonzero launcher leaves its pipe-owning descendant alive after the authority error."""
    child_pid = tmp_path / "nonzero-descendant.pid"
    executable = _controlled_executable(
        tmp_path / "git-nonzero",
        "import pathlib, subprocess, sys\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid), encoding='ascii')\n"
        "raise SystemExit(17)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=0.15))
    with pytest.raises(GitAuthorityError, match="timed out"):
        runner.run(("probe",))
    pid = int(child_pid.read_text(encoding="ascii"))
    for _ in range(20):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists(), "nonzero-launcher descendant remained alive"


def test_requested_closure_deadline_and_unrelated_late_addition(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: exact closure ignores its deadline or treats an unrelated late object as source authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"x")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    runner.limits = GitScanLimits(timeout_seconds=0.000_001)
    with pytest.raises(GitAuthorityError, match="timed out|seal deadline"):
        runner.seal_object_store(commit_oid, "commit", "sha1")
    runner.close(suppress_terminal_error=True)

    original_read = git_source.os.read
    injected = False

    def add_object_after_inventory(descriptor: int, count: int) -> bytes:
        nonlocal injected
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        if not injected and "/.git/objects/" in target:
            injected = True
            late = git_fixture.root / ".git/objects/ee/late-object"
            late.parent.mkdir(exist_ok=True)
            late.write_bytes(b"late")
        return original_read(descriptor, count)

    monkeypatch.setattr(git_source.os, "read", add_object_after_inventory)
    snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    assert snapshot.blob("pin.md").data == b"x"
    assert injected


@pytest.mark.parametrize("operation", ("delete", "replace", "corrupt"))
def test_requested_object_drift_during_closure_is_rejected(git_fixture: GitFixture, monkeypatch, operation: str) -> None:
    """Break caught: a requested source object can disappear, be replaced, or be corrupted after capture."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"authoritative\n")
    blob_oid = git_fixture._git("rev-parse", f"{commit_oid}:pin.md").decode().strip()
    source_object = git_fixture.root / ".git/objects" / blob_oid[:2] / blob_oid[2:]
    original_read = git_source.os.read
    injected = False

    def mutate_requested_object(descriptor: int, count: int) -> bytes:
        nonlocal injected
        chunk = original_read(descriptor, count)
        if not injected and os.readlink(f"/proc/self/fd/{descriptor}").removesuffix(" (deleted)") == str(source_object):
            injected = True
            if operation == "delete":
                source_object.unlink()
            elif operation == "replace":
                replacement = source_object.with_name("replacement")
                replacement.write_bytes(source_object.read_bytes())
                replacement.replace(source_object)
            else:
                os.chmod(source_object, 0o600)
                source_object.write_bytes(b"corrupt")
        return chunk

    monkeypatch.setattr(git_source.os, "read", mutate_requested_object)
    with pytest.raises(GitAuthorityError, match="requested Git object|object-store changed|corrupt"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    assert injected


def test_loose_decoder_rejects_declared_expansion_before_payload_allocation() -> None:
    """Break caught: a tiny compressed loose blob expands beyond the blob cap."""
    import zlib
    import scripts.nautilus_pin_inventory.git_source as git_source

    payload = b"x" * 4096
    raw = b"blob 4096\0" + payload
    oid = hashlib.sha1(raw).hexdigest()
    with pytest.raises(GitAuthorityError, match="blob limit"):
        git_source._decode_loose_object(
            zlib.compress(raw), oid, "sha1", GitScanLimits(max_blob_bytes=32), time.monotonic() + 1
        )


def test_tree_parser_rejects_wide_and_deep_closure_before_retaining_children() -> None:
    """Break caught: a wide/deep tree materializes an unbounded child tuple or recursion stack."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    child = b"100644 x\0" + bytes.fromhex("00" * 20)
    with pytest.raises(GitAuthorityError, match="entry|tree"):
        git_source._tree_children(child * 4, "sha1", GitScanLimits(max_entries=2), depth=0)
    with pytest.raises(GitAuthorityError, match="depth"):
        git_source._tree_children(child, "sha1", GitScanLimits(), depth=git_source._MAX_TREE_DEPTH + 1)


def test_pack_discovery_ignores_unrelated_incomplete_and_huge_pairs_for_loose_closure(git_fixture: GitFixture) -> None:
    """Break caught: loose-only closure scans or trusts unrelated pack artifacts."""
    commit_oid, expected = git_fixture.commit_file("pin.md", b"loose authority\n")
    pack = git_fixture.root / ".git/objects/pack"
    pack.mkdir(exist_ok=True)
    (pack / "pack-deadbeef.pack").write_bytes(b"x" * 1_000_000)
    (pack / "pack-incomplete.idx").write_bytes(b"not an index")

    assert GitTreeSnapshot.from_commit(git_fixture.root, commit_oid).blob("pin.md").data == expected


def test_immediate_nonzero_closed_pipe_is_signalled_before_reap(git_fixture: GitFixture, tmp_path: Path, monkeypatch) -> None:
    """Break caught: a completed failing child is reaped before its owned group receives abnormal cleanup."""
    executable = _controlled_executable(tmp_path / "git-immediate-nonzero", "raise SystemExit(17)")
    runner = _controlled_runner(git_fixture, executable, GitScanLimits())
    import scripts.nautilus_pin_inventory.git_source as git_source

    signals: list[tuple[int, int]] = []

    def record_group_cleanup(pgid: int, requested_signal: int) -> None:
        signals.append((pgid, requested_signal))
        if requested_signal == 0:
            raise ProcessLookupError

    monkeypatch.setattr(git_source.os, "killpg", record_group_cleanup)
    with pytest.raises(GitAuthorityError, match="failed"):
        runner.run(("probe",))
    assert [requested for _pgid, requested in signals] == [signal.SIGKILL, 0]
    assert signals[0][0] == signals[1][0]


def test_temporary_file_setup_oserror_is_normalized(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: input setup leaks a raw filesystem exception from the public interface."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    monkeypatch.setattr(git_source.tempfile, "TemporaryFile", lambda: (_ for _ in ()).throw(OSError("no tmp")))
    with pytest.raises(GitAuthorityError, match="temporary input"):
        runner.run(("rev-parse", "--git-dir"), input_data=b"x")


def test_bootstrap_cleanup_failure_is_not_success(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: an unconfirmed pack-bootstrap removal returns an accepted snapshot."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"packed cleanup\n")
    (git_fixture.root / "second.md").write_bytes(b"force a packed closure\n")
    git_fixture._git("add", "second.md")
    git_fixture._git("commit", "-qm", "force pack")
    commit_oid = git_fixture._git("rev-parse", "HEAD").decode().strip()
    git_fixture._git("gc", "--prune=now")
    assert not (git_fixture.root / ".git/objects" / commit_oid[:2] / commit_oid[2:]).exists()
    original_close = git_source._PackBootstrap.close

    def fail_close(self):
        original_close(self)
        raise GitAuthorityError("pack bootstrap cleanup failed")

    monkeypatch.setattr(git_source._PackBootstrap, "close", fail_close)
    with pytest.raises(GitAuthorityError, match="cleanup"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_packed_sha256_repository_keeps_exact_oid_and_independent_receipt(tmp_path: Path) -> None:
    """Break caught: pack-only SHA-256 objects are parsed as SHA-1 or lose their receipt."""
    fixture = GitFixture(tmp_path / "sha256-packed", object_format="sha256")
    commit_oid, expected = fixture.commit_file("pin.md", b"sha256 packed authority\n")
    fixture._git("gc", "--prune=now")

    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    blob = snapshot.blob("pin.md")
    assert len(blob.blob_oid) == 64
    assert blob.data == expected
    assert blob.sha256 == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("operation", ("delete", "replace", "mutate"))
def test_post_seal_pinned_pack_and_index_drift_is_rejected(git_fixture: GitFixture, operation: str) -> None:
    """Break caught: a used canonical pack/index can drift after its private bootstrap was sealed."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"post seal packed authority\n")
    git_fixture._git("gc", "--prune=now")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", "sha1")
    pack_dir = git_fixture.root / ".git/objects/pack"
    target = next(pack_dir.glob("*.idx"))
    try:
        if operation == "delete":
            target.unlink()
        elif operation == "replace":
            replacement = target.with_name("replacement.idx")
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
        else:
            os.chmod(target, 0o600)
            with target.open("r+b") as handle:
                handle.seek(0)
                handle.write(b"broken")
        with pytest.raises(GitAuthorityError, match="pack"):
            runner.verify_sealed_source()
    finally:
        runner.close(suppress_terminal_error=True)


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_bootstrap_owned_hardlink_advances_namespace_receipt_but_external_drift_is_terminal(
    object_format: str,
) -> None:
    """Break caught: bootstrap-owned nlink/ctime changes look like an external pack mutation."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    initial = git_source._PackEntry(
        f"pack-{object_format}.idx", (11, 22, stat.S_IFREG, 1, 4096, 33, 44)
    )
    namespace = git_source._PackNamespace(
        Path("/non-authority"), None, None, {initial.name: initial}, None
    )
    linked = (11, 22, stat.S_IFREG, 2, 4096, 33, 55)

    git_source._advance_namespace_owned_hardlink(namespace, initial.name, linked)
    assert namespace.entries[initial.name].identity == linked

    with pytest.raises(GitAuthorityError, match="namespace"):
        git_source._advance_namespace_owned_hardlink(
            namespace, initial.name, (11, 22, stat.S_IFREG, 4, 4096, 33, 56)
        )


def test_bootstrap_owned_hardlink_receipt_does_not_relax_pack_byte_or_inode_drift() -> None:
    """Break caught: accepting the expected metadata transition masks replacement or byte drift."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    initial = git_source._PackEntry("pack-safe.pack", (11, 22, stat.S_IFREG, 1, 4096, 33, 44))
    namespace = git_source._PackNamespace(Path("/non-authority"), None, None, {initial.name: initial}, None)

    with pytest.raises(GitAuthorityError, match="namespace"):
        git_source._advance_namespace_owned_hardlink(
            namespace, initial.name, (11, 23, stat.S_IFREG, 2, 4096, 33, 55)
        )
    with pytest.raises(GitAuthorityError, match="namespace"):
        git_source._advance_namespace_owned_hardlink(
            namespace, initial.name, (11, 22, stat.S_IFREG, 2, 4097, 33, 55)
        )


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_bootstrap_object_read_uses_one_authoritative_header_and_body_call(
    monkeypatch, object_format: str
) -> None:
    """Break caught: one packed object incurs a second authority subprocess just to read its header."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    payload = b"one bounded packed read\n"
    raw = b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
    oid = hashlib.new(object_format, raw).hexdigest()
    output = f"{oid} blob {len(payload)}\n".encode("ascii") + payload + b"\n"
    runner = object.__new__(git_source._GitRunner)
    runner.limits = GitScanLimits()
    runner._returned_object_sha256 = {}
    calls: list[tuple[str, ...]] = []

    def run(arguments, **_kwargs):
        calls.append(arguments)
        return output

    runner.run = run
    monkeypatch.setattr(git_source, "_verify_private_pack_bootstrap", lambda _bootstrap: None)
    result = runner._read_bootstrap_object(
        SimpleNamespace(object_authority=object()), oid, "blob", object_format
    )

    assert result == ("blob", payload)
    assert calls == [("cat-file", "--batch")]


def test_pack_discovery_setup_oserror_is_normalized(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: pack-directory enumeration leaks a raw setup OSError."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"packed setup\n")
    git_fixture._git("gc", "--prune=now")
    monkeypatch.setattr(git_source.os, "scandir", lambda _fd: (_ for _ in ()).throw(OSError("no list")))
    with pytest.raises(GitAuthorityError, match="discovery"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


def test_late_private_loose_object_cannot_be_adopted_by_packed_bootstrap(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: a same-UID writer supplies a needed tree through the child-visible private store."""
    import zlib
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, expected = git_fixture.commit_file("nested/pin.md", b"packed provenance\n")
    tree_oid = git_fixture._git("rev-parse", f"{commit_oid}^{{tree}}").decode().strip()
    tree_raw = git_fixture._git("cat-file", "tree", tree_oid)
    original = git_source._BootstrapBatchReader.read_object
    injected = False

    def inject_after_commit(self, oid, expected_type, object_format):
        nonlocal injected
        result = original(self, oid, expected_type, object_format)
        if not injected and expected_type == "commit":
            injected = True
            target = self.bootstrap.destination / tree_oid[:2] / tree_oid[2:]
            target.parent.mkdir(mode=0o700)
            target.write_bytes(zlib.compress(b"tree " + str(len(tree_raw)).encode("ascii") + b"\0" + tree_raw))
        return result

    git_fixture._git("gc", "--prune=now")
    monkeypatch.setattr(git_source._BootstrapBatchReader, "read_object", inject_after_commit)
    with pytest.raises(GitAuthorityError, match="private Git pack bootstrap|private bootstrap|inventory"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    assert injected


def test_runner_normalizes_temporary_input_write_and_seek_errors(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: temporary-input write/seek failures escape the Git authority error boundary."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    class BrokenInput:
        def write(self, _data):
            raise OSError("write denied")

        def seek(self, _offset):
            raise AssertionError("seek must not run after write failure")

        def close(self):
            pass

    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    monkeypatch.setattr(git_source.tempfile, "TemporaryFile", BrokenInput)
    with pytest.raises(GitAuthorityError, match="temporary input"):
        runner.run(("rev-parse", "--git-dir"), input_data=b"x")


def test_inherited_hard_limit_below_minimum_fails_before_popen(
    git_fixture: GitFixture, tmp_path: Path
) -> None:
    """Break caught: an undersized inherited AS hard limit reaches Popen/preexec."""
    commit_oid, _ = git_fixture.commit_file("pin.md", b"bounded authority\n")
    launch_marker = tmp_path / "unexpected-child-launch"
    program = (
        "import pathlib, resource, subprocess\n"
        "from scripts.nautilus_pin_inventory.git_source import GitAuthorityError, GitTreeSnapshot\n"
        "import scripts.nautilus_pin_inventory.git_source as git_source\n"
        f"repo = pathlib.Path({str(git_fixture.root)!r})\n"
        f"commit_oid = {commit_oid!r}\n"
        f"marker = pathlib.Path({str(launch_marker)!r})\n"
        "resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))\n"
        "def fail_if_launched(*_args, **_kwargs):\n"
        "    marker.write_text('launched', encoding='ascii')\n"
        "    raise subprocess.SubprocessError('raw child launch detail')\n"
        "git_source.subprocess.Popen = fail_if_launched\n"
        "try:\n"
        "    GitTreeSnapshot.from_commit(repo, commit_oid)\n"
        "except BaseException as exc:\n"
        "    print(type(exc).__name__ + ':' + str(exc))\n"
        "    valid = (type(exc) is GitAuthorityError and str(exc) == "
        "'inherited Git address-space limit is insufficient' and not marker.exists())\n"
        "    raise SystemExit(0 if valid else 17)\n"
        "raise SystemExit(18)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == (
        "GitAuthorityError:inherited Git address-space limit is insufficient"
    )
    assert not launch_marker.exists()


def test_finite_inherited_hard_limit_clamps_exact_envelope_and_public_scan_succeeds(
    git_fixture: GitFixture,
) -> None:
    """Break caught: child limits exceed a sufficient finite inherited hard envelope."""
    commit_oid, expected = git_fixture.commit_file("pin.md", b"finite envelope\n")
    inherited_as = 384 * 1024 * 1024
    inherited_cpu = 10
    program = (
        "import pathlib, resource\n"
        "from scripts.nautilus_pin_inventory.git_source import GitScanLimits, GitTreeSnapshot\n"
        "import scripts.nautilus_pin_inventory.git_source as git_source\n"
        f"repo = pathlib.Path({str(git_fixture.root)!r})\n"
        f"commit_oid = {commit_oid!r}\n"
        f"expected = {expected!r}\n"
        f"inherited_as = {inherited_as}\n"
        f"inherited_cpu = {inherited_cpu}\n"
        "resource.setrlimit(resource.RLIMIT_AS, (inherited_as, inherited_as))\n"
        "resource.setrlimit(resource.RLIMIT_CPU, (inherited_cpu, inherited_cpu))\n"
        "limits = GitScanLimits(max_total_bytes=100_000_000, timeout_seconds=1.0)\n"
        "envelope = git_source._derive_child_resource_envelope(limits)\n"
        "assert envelope.address_space == (inherited_as, inherited_as)\n"
        "assert envelope.cpu_seconds == (2, 2)\n"
        "assert envelope.address_space[0] <= envelope.address_space[1] <= inherited_as\n"
        "assert envelope.cpu_seconds[0] <= envelope.cpu_seconds[1] <= inherited_cpu\n"
        "snapshot = GitTreeSnapshot.from_commit(repo, commit_oid, limits=limits)\n"
        "assert snapshot.blob('pin.md').data == expected\n"
        "print(f'inherited_as={inherited_as} derived_as={envelope.address_space} '"
        "      f'inherited_cpu={inherited_cpu} derived_cpu={envelope.cpu_seconds}')\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == (
        "inherited_as=402653184 derived_as=(402653184, 402653184) "
        "inherited_cpu=10 derived_cpu=(2, 2)"
    )


def test_resource_envelope_handles_inherited_infinity_with_exact_policy_values(
    monkeypatch,
) -> None:
    """Break caught: RLIM_INFINITY is compared as an ordinary finite hard limit."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    assert git_source.resource is not None

    def inherited_infinity(_kind: int) -> tuple[int, int]:
        return (123, git_source.resource.RLIM_INFINITY)

    monkeypatch.setattr(git_source.resource, "getrlimit", inherited_infinity)

    envelope = git_source._derive_child_resource_envelope(
        GitScanLimits(max_total_bytes=4096, timeout_seconds=1.0)
    )

    assert envelope.address_space == (268_435_456, 268_435_456)
    assert envelope.cpu_seconds == (2, 2)


def test_resource_envelope_rejects_inherited_cpu_hard_limit_below_one_second(
    monkeypatch,
) -> None:
    """Break caught: a zero inherited CPU hard limit reaches Popen/preexec."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    assert git_source.resource is not None

    def inherited_limits(kind: int) -> tuple[int, int]:
        if kind == git_source.resource.RLIMIT_AS:
            return (git_source.resource.RLIM_INFINITY, git_source.resource.RLIM_INFINITY)
        assert kind == git_source.resource.RLIMIT_CPU
        return (0, 0)

    monkeypatch.setattr(git_source.resource, "getrlimit", inherited_limits)

    with pytest.raises(
        GitAuthorityError,
        match="^inherited Git CPU limit is insufficient$",
    ):
        git_source._derive_child_resource_envelope(GitScanLimits())


def test_preexec_consumes_only_the_validated_resource_envelope(monkeypatch) -> None:
    """Break caught: preexec re-derives limits or consults mutable inherited state."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    assert git_source.resource is not None
    envelope = git_source._ChildResourceEnvelope(
        address_space=(268_435_456, 268_435_456),
        cpu_seconds=(2, 2),
    )
    calls: list[tuple[int, tuple[int, int]]] = []

    def forbidden_getrlimit(_kind: int) -> tuple[int, int]:
        raise AssertionError("preexec must not derive inherited limits")

    def capture_setrlimit(kind: int, values: tuple[int, int]) -> None:
        calls.append((kind, values))

    monkeypatch.setattr(git_source.resource, "getrlimit", forbidden_getrlimit)
    monkeypatch.setattr(git_source.resource, "setrlimit", capture_setrlimit)

    configure = git_source._bounded_git_preexec(envelope)
    configure()

    assert calls == [
        (git_source.resource.RLIMIT_AS, (268_435_456, 268_435_456)),
        (git_source.resource.RLIMIT_CPU, (2, 2)),
    ]


def test_git_child_receives_exact_bounded_cache_limits(
    git_fixture: GitFixture, tmp_path: Path
) -> None:
    """Break caught: Git pack/delta cache controls are absent, renamed, or unbounded."""
    executable = _controlled_executable(
        tmp_path / "git-cache-limits",
        "import os, sys\n"
        "names = ('GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0', "
        "'GIT_CONFIG_KEY_1', 'GIT_CONFIG_VALUE_1')\n"
        "sys.stdout.write('|'.join(os.environ[name] for name in names) + '|' + "
        "str('GIT_CONFIG_KEY_2' in os.environ))",
    )
    runner = _controlled_runner(
        git_fixture,
        executable,
        GitScanLimits(max_total_bytes=20_000_000, timeout_seconds=1.0),
    )

    output = runner.run(("probe",)).decode("ascii")

    assert output == (
        "2|core.deltaBaseCacheLimit|5000000|core.packedGitLimit|5000000|False"
    )


def test_runner_applies_finite_child_resource_limits_before_exec(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: packed Git can allocate unbounded memory/CPU before its output cap applies."""
    executable = _controlled_executable(
        tmp_path / "git-resource-limits",
        "import resource, sys\n"
        "memory = resource.getrlimit(resource.RLIMIT_AS)\n"
        "cpu = resource.getrlimit(resource.RLIMIT_CPU)\n"
        "sys.stdout.write(f'{memory[0]} {cpu[0]}\\n')",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(max_total_bytes=4096, timeout_seconds=1.0))
    output = runner.run(("probe",)).decode("ascii").strip().split()
    assert len(output) == 2
    assert 0 < int(output[0]) < 1_000_000_000
    assert 0 < int(output[1]) <= 2


def test_nonzero_leader_with_closed_pipe_descendant_is_terminated_before_reap(git_fixture: GitFixture, tmp_path: Path) -> None:
    """Break caught: a reaped failing leader leaves a same-group descendant alive after closing all pipes."""
    child_pid = tmp_path / "closed-pipe-descendant.pid"
    executable = _controlled_executable(
        tmp_path / "git-closed-pipe-nonzero",
        "import pathlib, subprocess, sys\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid), encoding='ascii')\n"
        "raise SystemExit(17)",
    )
    runner = _controlled_runner(git_fixture, executable, GitScanLimits(timeout_seconds=1.0))
    with pytest.raises(GitAuthorityError, match="failed"):
        runner.run(("probe",))
    pid = int(child_pid.read_text(encoding="ascii"))
    for _ in range(20):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists(), "closed-pipe descendant remained alive"


def _sealed_pack_runner(git_fixture: GitFixture, payload: bytes):
    """Return a runner which owns one real hardlinked private pack bootstrap."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", payload)
    git_fixture._git("gc", "--prune=now")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", "sha1")
    assert runner._persistent_reader is None
    assert len(runner._pack_bootstraps) == 1
    return runner, runner._pack_bootstraps[0], commit_oid


def _remove_owned_bootstrap_root(bootstrap, real_cleanup) -> None:
    """Precisely remove and close one test-owned retained bootstrap."""
    del real_cleanup
    assert bootstrap.root.parent == bootstrap.owner.parent_path_hint
    assert bootstrap.root.name.startswith("p1-u00-pack-")
    if bootstrap.root.exists():
        shutil.rmtree(bootstrap.root)
    retained = (
        (bootstrap.destination_pack_fd, bootstrap.destination_pack_identity),
        (bootstrap.destination_fd, bootstrap.destination_identity),
        (bootstrap.owner.root_fd, bootstrap.owner.root_identity),
        (bootstrap.owner.parent_fd, bootstrap.owner.parent_identity),
    )
    for descriptor, expected in retained:
        try:
            metadata = os.fstat(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise
            continue
        actual = (
            metadata.st_dev,
            metadata.st_ino,
            stat.S_IFMT(metadata.st_mode),
            metadata.st_uid,
            metadata.st_gid,
            stat.S_IMODE(metadata.st_mode),
        )
        expected_tuple = (
            expected.device,
            expected.inode,
            expected.file_type,
            expected.uid,
            expected.gid,
            expected.mode,
        )
        if actual == expected_tuple:
            os.close(descriptor)


def _capture_descriptor_receipt(descriptor: int) -> tuple[int, int, int]:
    metadata = os.fstat(descriptor)
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))


def _capture_pack_bootstrap_descriptors(bootstrap) -> dict[int, tuple[int, int, int]]:
    descriptors = {
        bootstrap.source_fd,
        bootstrap.destination_fd,
        bootstrap.destination_pack_fd,
    }
    owner = getattr(bootstrap, "owner", None)
    if owner is None:
        descriptors.add(bootstrap.root_fd)
    else:
        descriptors.update((owner.root_fd, owner.parent_fd))
    return {
        descriptor: _capture_descriptor_receipt(descriptor)
        for descriptor in descriptors
    }


def _assert_descriptors_closed(
    receipts: dict[int, tuple[int, int, int]],
) -> None:
    for descriptor in receipts:
        with pytest.raises(OSError) as raised:
            os.fstat(descriptor)
        assert raised.value.errno == errno.EBADF


def _close_descriptors_still_owned(
    receipts: dict[int, tuple[int, int, int]],
) -> None:
    for descriptor, expected in receipts.items():
        try:
            actual = _capture_descriptor_receipt(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise
            continue
        if actual == expected:
            os.close(descriptor)


def test_public_pack_bootstrap_foreign_replacement_survives_gc_and_releases_fds(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: a public failure leaves an armed pathname finalizer and bootstrap FDs."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    parked_root = fixture.root.parent / "parked-public-pack-bootstrap"
    foreign_root: Path | None = None
    foreign_marker: Path | None = None
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    source_links: tuple[tuple[Path, int], ...] = ()
    real_require_object_type = git_source._require_object_type

    def verify_then_replace(runner, oid, expected_type) -> None:
        nonlocal foreign_root, foreign_marker, descriptor_receipts, source_links
        real_require_object_type(runner, oid, expected_type)
        runner.verify_sealed_source()
        assert runner._persistent_reader is None
        assert len(runner._pack_bootstraps) == 1
        bootstrap = runner._pack_bootstraps[0]
        descriptor_receipts = _capture_pack_bootstrap_descriptors(bootstrap)
        source_root = Path(os.readlink(f"/proc/self/fd/{bootstrap.source_fd}"))
        source_links = tuple(
            (source_root / name, expected_nlink)
            for name, expected_nlink in bootstrap.source_nlinks
        )
        foreign_root = bootstrap.root
        bootstrap.root.rename(parked_root)
        bootstrap.root.mkdir(mode=0o700)
        foreign_marker = bootstrap.root / "foreign-owner-marker"
        foreign_marker.write_text("foreign public root\n", encoding="utf-8")
        raise GitAuthorityError("injected failure after verified pack bootstrap")

    monkeypatch.setattr(git_source, "_require_object_type", verify_then_replace)
    rejected = False
    try:
        try:
            GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except GitAuthorityError:
            rejected = True

        assert rejected
        assert foreign_root is not None
        assert foreign_marker is not None
        assert descriptor_receipts
        gc.collect()
        gc.collect()
        assert foreign_root.is_dir()
        assert foreign_marker.read_text(encoding="utf-8") == "foreign public root\n"
        assert parked_root.is_dir()
        assert not tuple(parked_root.iterdir())
        _assert_descriptors_closed(descriptor_receipts)
        for source, expected_nlink in source_links:
            assert source.stat().st_nlink == expected_nlink - 1
    finally:
        _close_descriptors_still_owned(descriptor_receipts)
        if foreign_root is not None and foreign_root.exists():
            shutil.rmtree(foreign_root)
        if parked_root.exists():
            shutil.rmtree(parked_root)


def test_public_object_store_foreign_replacement_survives_gc_and_releases_fds(
    git_fixture,
    monkeypatch,
) -> None:
    """Break caught: copied-store finalization deletes a foreign root and retains its FDs."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _expected = git_fixture.commit_file(
        "pin.md", b"public copied-store lifetime\n"
    )
    parked_root = git_fixture.root.parent / "parked-public-object-store"
    foreign_root: Path | None = None
    foreign_marker: Path | None = None
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    real_require_object_type = git_source._require_object_type

    def verify_then_replace(runner, oid, expected_type) -> None:
        nonlocal foreign_root, foreign_marker, descriptor_receipts
        real_require_object_type(runner, oid, expected_type)
        runner.verify_sealed_source()
        store = runner._object_store
        private = runner._private_closure
        assert store is not None
        assert private is not None
        if isinstance(store, git_source._OwnedTemporaryRoot):
            foreign_root = store.path_hint
            descriptors = (store.parent_fd, store.root_fd)
        else:
            foreign_root = Path(store.name)
            descriptors = ()
        descriptors = (
            *descriptors,
            private.root_fd,
            private.objects_fd,
            private.pack_fd,
            *(descriptor for descriptor, _identity in private.prefixes.values()),
        )
        descriptor_receipts = {
            descriptor: _capture_descriptor_receipt(descriptor)
            for descriptor in set(descriptors)
        }
        foreign_root.rename(parked_root)
        foreign_root.mkdir(mode=0o700)
        foreign_marker = foreign_root / "foreign-owner-marker"
        foreign_marker.write_text("foreign object store\n", encoding="utf-8")
        raise GitAuthorityError("injected failure after verified copied store")

    monkeypatch.setattr(git_source, "_require_object_type", verify_then_replace)
    rejected = False
    try:
        try:
            GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
        except GitAuthorityError:
            rejected = True

        assert rejected
        assert foreign_root is not None
        assert foreign_marker is not None
        assert descriptor_receipts
        gc.collect()
        gc.collect()
        assert foreign_root.is_dir()
        assert foreign_marker.read_text(encoding="utf-8") == "foreign object store\n"
        assert parked_root.is_dir()
        assert not tuple(parked_root.iterdir())
        _assert_descriptors_closed(descriptor_receipts)
    finally:
        _close_descriptors_still_owned(descriptor_receipts)
        if foreign_root is not None and foreign_root.exists():
            shutil.rmtree(foreign_root)
        if parked_root.exists():
            shutil.rmtree(parked_root)


def test_repeated_real_packed_public_snapshots_leave_stable_fd_classes_and_nlinks(
    packed_git_fixture,
) -> None:
    """Break caught: repeated packed public successes accumulate descriptors, roots, or hardlinks."""
    fixture, commit_oid, expected = packed_git_fixture
    tree_oid = fixture._git(
        "rev-parse", f"{commit_oid}^{{tree}}"
    ).decode("ascii").strip()
    blob_oid = fixture._git(
        "rev-parse", f"{commit_oid}:pin.md"
    ).decode("ascii").strip()
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)

    for _iteration in range(50):
        snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)

        assert snapshot.commit_oid == commit_oid
        assert snapshot.tree_oid == tree_oid
        assert tuple(blob.path for blob in snapshot.blobs) == ("pin.md",)
        assert snapshot.blob("pin.md").blob_oid == blob_oid
        assert snapshot.blob("pin.md").data == expected
        assert snapshot.blob("pin.md").sha256 == hashlib.sha256(expected).hexdigest()
        assert _stable_descriptor_classes() == baseline_fds
        assert _task_owned_pack_roots(fixture) == baseline_roots
        assert _packed_source_nlinks(fixture) == baseline_nlinks

    gc.collect()
    gc.collect()
    observed_fds = _stable_descriptor_classes()
    observed_roots = _task_owned_pack_roots(fixture)
    process_groups = _reviewer_process_groups()
    observed_nlinks = _packed_source_nlinks(fixture)
    _emit_b4_batch_receipt(
        label="successful-packed-snapshots",
        iterations=50,
        baseline_fds=baseline_fds,
        observed_fds=observed_fds,
        baseline_roots=baseline_roots,
        observed_roots=observed_roots,
        process_groups=process_groups,
        baseline_nlinks=baseline_nlinks,
        observed_nlinks=observed_nlinks,
    )
    assert observed_fds == baseline_fds
    assert observed_roots == baseline_roots
    assert process_groups == ()
    assert observed_nlinks == baseline_nlinks


def test_repeated_transient_pack_index_close_failure_retries_exact_same_fd(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated transient index-close failures leak, retry a replacement, or reject snapshots."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_close = os.close

    for _iteration in range(50):
        target_descriptor: int | None = None
        target_identity: tuple[int, int, int] | None = None
        close_attempts = 0
        same_identity_on_retry = False
        retry_completed = False
        snapshot = None

        def fail_first_close(descriptor: int) -> None:
            nonlocal target_descriptor, target_identity, close_attempts
            nonlocal same_identity_on_retry, retry_completed
            if target_descriptor is None and _is_pack_index_descriptor(descriptor):
                target_descriptor = descriptor
                target_identity = _capture_descriptor_receipt(descriptor)
            if descriptor == target_descriptor and not retry_completed:
                close_attempts += 1
                if close_attempts == 1:
                    raise OSError(
                        errno.EIO,
                        "injected repeated transient pack-index close failure",
                    )
                same_identity_on_retry = (
                    target_identity == _capture_descriptor_receipt(descriptor)
                )
                if not same_identity_on_retry:
                    raise OSError(
                        errno.EIO,
                        "injected descriptor identity changed before retry",
                    )
                retry_completed = True
            real_close(descriptor)

        try:
            with monkeypatch.context() as close_patch:
                close_patch.setattr(git_source.os, "close", fail_first_close)
                snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)

            assert snapshot.blob("pin.md").data == expected
            assert target_descriptor is not None
            assert target_identity is not None
            assert close_attempts == 2
            assert same_identity_on_retry
            assert retry_completed
        finally:
            if target_descriptor is not None and target_identity is not None:
                _close_descriptors_still_owned(
                    {target_descriptor: target_identity}
                )

        with pytest.raises(OSError) as raised:
            os.fstat(target_descriptor)
        assert raised.value.errno == errno.EBADF
        assert _stable_descriptor_classes() == baseline_fds
        assert _task_owned_pack_roots(fixture) == baseline_roots
        assert _packed_source_nlinks(fixture) == baseline_nlinks


def test_repeated_persistent_pack_index_close_failure_rejects_every_snapshot(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated persistent index-close failures accept a snapshot or leak reviewer-owned FDs."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_close = os.close
    public_errors: list[tuple[type[BaseException], str]] = []

    for _iteration in range(10):
        target_descriptor: int | None = None
        target_identity: tuple[int, int, int] | None = None
        close_attempts = 0
        snapshot = None
        caught: BaseException | None = None

        def fail_every_close(descriptor: int) -> None:
            nonlocal target_descriptor, target_identity, close_attempts
            if target_descriptor is None and _is_pack_index_descriptor(descriptor):
                target_descriptor = descriptor
                target_identity = _capture_descriptor_receipt(descriptor)
            if descriptor == target_descriptor:
                close_attempts += 1
                raise OSError(
                    errno.EIO,
                    "injected repeated persistent pack-index close failure",
                )
            real_close(descriptor)

        try:
            with monkeypatch.context() as close_patch:
                close_patch.setattr(git_source.os, "close", fail_every_close)
                try:
                    snapshot = GitTreeSnapshot.from_commit(
                        fixture.root, commit_oid
                    )
                except BaseException as exc:
                    caught = exc

            assert snapshot is None
            assert isinstance(
                caught,
                (GitAuthorityError, git_source.GitAuthorityAggregateError),
            )
            assert (
                "Git pack-index descriptor cleanup was not confirmed"
                in str(caught)
            )
            assert close_attempts == 2
            assert target_descriptor is not None
            assert target_identity is not None
            assert _capture_descriptor_receipt(target_descriptor) == target_identity
            public_errors.append((type(caught), str(caught)))
        finally:
            if target_descriptor is not None and target_identity is not None:
                _close_descriptors_still_owned(
                    {target_descriptor: target_identity}
                )

        with pytest.raises(OSError) as raised:
            os.fstat(target_descriptor)
        assert raised.value.errno == errno.EBADF
        assert _stable_descriptor_classes() == baseline_fds
        assert _task_owned_pack_roots(fixture) == baseline_roots
        assert _packed_source_nlinks(fixture) == baseline_nlinks

    assert len(public_errors) == 10
    assert len(set(public_errors)) == 1


def test_repeated_public_foreign_object_store_survives_double_gc_and_restores_pack_links(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated public foreign replacement deletes markers, finalizes late, or strands custody."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_require_object_type = git_source._require_object_type

    for iteration in range(20):
        parked_root = fixture.root.parent / f"parked-public-object-store-{iteration}"
        foreign_root: Path | None = None
        foreign_marker: Path | None = None
        descriptor_receipts: dict[int, tuple[int, int, int]] = {}
        source_links: tuple[tuple[Path, int], ...] = ()
        bootstrap_ref = None
        runner_ref = None
        store_ref = None
        replaced = False
        snapshot = None
        caught_type: type[BaseException] | None = None
        caught_text: str | None = None

        def verify_then_replace(runner, oid, expected_type) -> None:
            nonlocal foreign_root, foreign_marker, descriptor_receipts
            nonlocal source_links, bootstrap_ref, runner_ref, store_ref, replaced
            real_require_object_type(runner, oid, expected_type)
            runner.verify_sealed_source()
            assert runner._persistent_reader is None
            assert len(runner._pack_bootstraps) == 1
            bootstrap = runner._pack_bootstraps[0]
            store = runner._object_store
            private = runner._private_closure
            closure = runner._closure
            assert isinstance(store, git_source._OwnedTemporaryRoot)
            assert private is not None
            assert closure is not None

            descriptors = set(_capture_pack_bootstrap_descriptors(bootstrap))
            descriptors.update(
                (
                    store.parent_fd,
                    store.root_fd,
                    private.root_fd,
                    private.objects_fd,
                    private.pack_fd,
                    closure.root_fd,
                )
            )
            descriptors.update(
                descriptor for descriptor, _identity in private.prefixes.values()
            )
            descriptors.update(
                descriptor for descriptor, _identity in closure.prefixes.values()
            )
            descriptor_receipts = {
                descriptor: _capture_descriptor_receipt(descriptor)
                for descriptor in descriptors
            }
            source_root = Path(
                os.readlink(f"/proc/self/fd/{bootstrap.source_fd}")
            )
            source_links = tuple(
                (source_root / name, expected_nlink)
                for name, expected_nlink in bootstrap.source_nlinks
            )
            foreign_root = store.path_hint
            foreign_root.rename(parked_root)
            foreign_root.mkdir(mode=0o700)
            foreign_marker = foreign_root / "foreign-owner-marker"
            foreign_marker.write_text(
                f"foreign object store cycle {iteration}\n", encoding="utf-8"
            )
            bootstrap_ref = bootstrap
            runner_ref = runner
            store_ref = store
            replaced = True
            raise GitAuthorityError(
                "injected repeated failure after foreign object-store replacement"
            )

        try:
            with monkeypatch.context() as require_patch:
                require_patch.setattr(
                    git_source, "_require_object_type", verify_then_replace
                )
                try:
                    snapshot = GitTreeSnapshot.from_commit(
                        fixture.root, commit_oid
                    )
                except BaseException as exc:
                    caught_type = type(exc)
                    caught_text = str(exc)

            assert snapshot is None
            assert replaced
            assert caught_type is not None and issubclass(
                caught_type, GitAuthorityError
            )
            assert caught_text is not None
            assert (
                "private Git object-store root-name cleanup was not confirmed"
                in caught_text
            )
            assert foreign_root is not None
            assert foreign_marker is not None
            assert descriptor_receipts
            assert source_links
            assert bootstrap_ref is not None
            assert runner_ref is not None
            assert store_ref is not None
            receipt = bootstrap_ref.cleanup_receipt()
            assert receipt.attempts == 1
            assert receipt.operation_root_unlinked
            assert receipt.source_links_restored
            assert receipt.descriptors_closed
            assert runner_ref._object_store_cleanup_attempts == 1
            assert not runner_ref._object_store_unlinked
            assert runner_ref._object_store_descriptors_closed
            assert runner_ref._object_store is None
            assert runner_ref._private_closure is None
            assert not runner_ref._pack_bootstraps
            assert runner_ref._pack_namespace is None
            assert runner_ref._closure is None
            _assert_descriptors_closed(descriptor_receipts)
            for source, expected_nlink in source_links:
                assert source.stat().st_nlink == expected_nlink - 1

            bootstrap_ref = None
            runner_ref = None
            store_ref = None
            gc.collect()
            gc.collect()
            assert foreign_root.is_dir()
            assert foreign_marker.read_text(encoding="utf-8") == (
                f"foreign object store cycle {iteration}\n"
            )
            assert parked_root.is_dir()
            assert not tuple(parked_root.iterdir())
            assert _packed_source_nlinks(fixture) == baseline_nlinks
        finally:
            _close_descriptors_still_owned(descriptor_receipts)
            if foreign_root is not None and foreign_root.exists():
                shutil.rmtree(foreign_root)
            if parked_root.exists():
                shutil.rmtree(parked_root)

        assert _stable_descriptor_classes() == baseline_fds
        assert _task_owned_pack_roots(fixture) == baseline_roots
        assert _packed_source_nlinks(fixture) == baseline_nlinks


def test_pack_bootstrap_construction_failure_has_no_late_finalizer(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: post-acquisition construction failure leaks FDs and deletes a replacement."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    parked_root = fixture.root.parent / "parked-construction-pack-bootstrap"
    created_root: Path | None = None
    foreign_marker: Path | None = None
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    injected = False
    real_link = git_source.os.link

    def fail_first_private_pack_link(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal created_root, foreign_marker, descriptor_receipts, injected
        if not injected and dst_dir_fd is not None:
            destination_pack = Path(
                os.readlink(f"/proc/self/fd/{dst_dir_fd}")
            )
            assert destination_pack.name == "pack"
            assert destination_pack.parent.name == "objects"
            created_root = destination_pack.parent.parent
            for fd_path in Path("/proc/self/fd").iterdir():
                try:
                    descriptor = int(fd_path.name)
                    target = Path(os.readlink(fd_path))
                    if target != created_root.parent:
                        target.relative_to(created_root)
                    descriptor_receipts[descriptor] = _capture_descriptor_receipt(
                        descriptor
                    )
                except (OSError, ValueError):
                    continue
            assert descriptor_receipts
            created_root.rename(parked_root)
            created_root.mkdir(mode=0o700)
            foreign_marker = created_root / "foreign-owner-marker"
            foreign_marker.write_text(
                "foreign construction root\n", encoding="utf-8"
            )
            injected = True
            raise OSError(errno.EIO, "injected pack-link construction failure")
        return real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(git_source.os, "link", fail_first_private_pack_link)
    rejected = False
    try:
        try:
            GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except GitAuthorityError:
            rejected = True

        assert rejected
        assert injected
        assert created_root is not None
        assert foreign_marker is not None
        gc.collect()
        gc.collect()
        assert created_root.is_dir()
        assert foreign_marker.read_text(encoding="utf-8") == (
            "foreign construction root\n"
        )
        assert parked_root.is_dir()
        assert not tuple(parked_root.iterdir())
        _assert_descriptors_closed(descriptor_receipts)
    finally:
        _close_descriptors_still_owned(descriptor_receipts)
        if created_root is not None and created_root.exists():
            shutil.rmtree(created_root)
        if parked_root.exists():
            shutil.rmtree(parked_root)


def test_pack_bootstrap_uses_descriptor_owned_temporary_root() -> None:
    """Break caught: pack bootstrap ownership regresses to pathname-finalized storage."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    source = inspect.getsource(git_source._PackBootstrap)
    class_node = ast.parse(source).body[0]
    assert isinstance(class_node, ast.ClassDef)
    annotations = {
        statement.target.id: ast.unparse(statement.annotation)
        for statement in class_node.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
    }

    assert annotations["owner"] == "_OwnedTemporaryRoot"
    assert "store" not in annotations
    assert all("TemporaryDirectory" not in annotation for annotation in annotations.values())


def test_descriptor_cleanup_confirms_hardlinks_and_all_owned_descriptors(
    git_fixture: GitFixture, monkeypatch
) -> None:
    """Break caught: successful descriptor cleanup omits a hardlink or retained directory FD."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, expected = git_fixture.commit_file("pin.md", b"descriptor cleanup\n")
    git_fixture._git("gc", "--prune=now")
    real_bootstrap = git_source._bootstrap_pack_view
    captured: list[tuple[object, Path]] = []

    def capture_bootstrap(*args, **kwargs):
        bootstrap = real_bootstrap(*args, **kwargs)
        if bootstrap is not None:
            source_root = Path(
                os.readlink(f"/proc/self/fd/{bootstrap.source_fd}")
            )
            captured.append((bootstrap, source_root))
        return bootstrap

    monkeypatch.setattr(git_source, "_bootstrap_pack_view", capture_bootstrap)
    try:
        snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    finally:
        for bootstrap, _source_root in captured:
            _remove_owned_bootstrap_root(bootstrap, None)

    assert snapshot.blob("pin.md").data == expected
    assert len(captured) == 1
    bootstrap, source_root = captured[0]
    receipt = bootstrap.cleanup_receipt()
    assert receipt == git_source._CleanupReceipt(
        attempts=1,
        operation_root_unlinked=True,
        source_links_restored=True,
        descriptors_closed=True,
    )
    assert not bootstrap.root.exists()
    for name, expected_nlink in bootstrap.source_nlinks:
        assert (source_root / name).stat().st_nlink == expected_nlink
    for descriptor in (
        bootstrap.destination_pack_fd,
        bootstrap.destination_fd,
        bootstrap.owner.root_fd,
        bootstrap.owner.parent_fd,
    ):
        with pytest.raises(OSError) as raised:
            os.fstat(descriptor)
        assert raised.value.errno == errno.EBADF


def test_descriptor_unlink_noop_remains_unconfirmed_and_closes_descriptors(
    git_fixture: GitFixture, monkeypatch
) -> None:
    """Break caught: successful-return unlink no-ops are accepted as hardlink cleanup."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    runner, bootstrap, _commit_oid = _sealed_pack_runner(
        git_fixture, b"cleanup no-op\n"
    )
    real_unlink = git_source.os.unlink
    attempts: list[str] = []

    def noop_pack_unlink(path, *, dir_fd=None) -> None:
        if dir_fd == bootstrap.destination_pack_fd:
            attempts.append(os.fsdecode(path))
            return
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(git_source.os, "unlink", noop_pack_unlink)
    try:
        with pytest.raises(
            GitAuthorityError, match="source hardlink cleanup was not confirmed"
        ):
            runner.close()
        receipt = bootstrap.cleanup_receipt()
        assert receipt.attempts == 1
        assert not receipt.operation_root_unlinked
        assert not receipt.source_links_restored
        assert receipt.descriptors_closed
        assert attempts == [entry.name for entry in bootstrap.entries]
        assert bootstrap.root.exists()
        _assert_descriptors_closed(
            {
                descriptor: (0, 0, 0)
                for descriptor in (
                    bootstrap.destination_pack_fd,
                    bootstrap.destination_fd,
                    bootstrap.owner.root_fd,
                    bootstrap.owner.parent_fd,
                )
            }
        )
        assert not runner._closed
    finally:
        monkeypatch.setattr(git_source.os, "unlink", real_unlink)
        _remove_owned_bootstrap_root(bootstrap, None)
        runner._pack_bootstraps.clear()
        runner.close(suppress_terminal_error=True)


def test_descriptor_cleanup_exception_is_stable_and_closes_descriptors(
    git_fixture: GitFixture, monkeypatch
) -> None:
    """Break caught: an unlink exception escapes raw or prevents terminal FD release."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    runner, bootstrap, _commit_oid = _sealed_pack_runner(
        git_fixture, b"cleanup exception\n"
    )
    real_unlink = git_source.os.unlink
    attempts = 0

    def raise_pack_unlink(path, *, dir_fd=None) -> None:
        nonlocal attempts
        if dir_fd == bootstrap.destination_pack_fd:
            attempts += 1
            raise RuntimeError("unstable injected cleanup detail")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(git_source.os, "unlink", raise_pack_unlink)
    try:
        with pytest.raises(
            GitAuthorityError, match="descriptor-relative cleanup failed"
        ) as raised:
            runner.close()
        assert "unstable injected cleanup detail" not in str(raised.value)
        assert attempts == 1
        assert bootstrap.cleanup_receipt().attempts == 1
        assert bootstrap.cleanup_receipt().descriptors_closed
        assert bootstrap.root.exists()
        assert not runner._closed
    finally:
        monkeypatch.setattr(git_source.os, "unlink", real_unlink)
        _remove_owned_bootstrap_root(bootstrap, None)
        runner._pack_bootstraps.clear()
        runner.close(suppress_terminal_error=True)


def test_foreign_path_replacement_is_not_deleted_by_cleanup(
    git_fixture: GitFixture, tmp_path: Path
) -> None:
    """Break caught: descriptor cleanup deletes a foreign root or strands owned resources."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    runner, bootstrap, _commit_oid = _sealed_pack_runner(
        git_fixture, b"foreign pathname\n"
    )
    retained_root = tmp_path / "retained-operation-root"
    bootstrap.root.rename(retained_root)
    bootstrap.root.mkdir()
    marker = bootstrap.root / "foreign-owner-marker"
    marker.write_text("foreign\n", encoding="utf-8")
    try:
        with pytest.raises(GitAuthorityError, match="root-name cleanup was not confirmed"):
            runner.close()
        assert marker.read_text(encoding="utf-8") == "foreign\n"
        receipt = bootstrap.cleanup_receipt()
        assert receipt.attempts == 1
        assert not receipt.operation_root_unlinked
        assert receipt.source_links_restored
        assert receipt.descriptors_closed
        assert retained_root.exists()
        assert not tuple(retained_root.iterdir())
        for descriptor in (
            bootstrap.destination_pack_fd,
            bootstrap.destination_fd,
            bootstrap.owner.root_fd,
            bootstrap.owner.parent_fd,
        ):
            with pytest.raises(OSError) as raised:
                os.fstat(descriptor)
            assert raised.value.errno == errno.EBADF
        assert not runner._closed
    finally:
        if bootstrap.root.exists():
            shutil.rmtree(bootstrap.root)
        if retained_root.exists():
            shutil.rmtree(retained_root)
        runner._pack_bootstraps.clear()
        runner.close(suppress_terminal_error=True)


def test_foreign_path_replacement_is_not_deleted_by_object_store_cleanup(
    git_fixture: GitFixture, tmp_path: Path
) -> None:
    """Break caught: copied-store cleanup recursively deletes a replacement after losing its retained root FD."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"foreign copied store\n")
    runner = git_source._GitRunner(git_fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", "sha1")
    store = runner._object_store
    private = runner._private_closure
    assert store is not None
    assert private is not None
    root = store.path_hint
    retained_root = tmp_path / "retained-copied-store"
    root.rename(retained_root)
    root.mkdir()
    marker = root / "foreign-owner-marker"
    marker.write_text("foreign copied store\n", encoding="utf-8")
    try:
        with pytest.raises(
            GitAuthorityError, match="private Git object-store root-name cleanup was not confirmed"
        ):
            runner.close()
        assert marker.read_text(encoding="utf-8") == "foreign copied store\n"
        assert retained_root.exists()
        assert not tuple(retained_root.iterdir())
        for descriptor in (
            store.root_fd,
            store.parent_fd,
            private.root_fd,
            private.objects_fd,
            private.pack_fd,
            *(descriptor for descriptor, _identity in private.prefixes.values()),
        ):
            with pytest.raises(OSError) as raised:
                os.fstat(descriptor)
            assert raised.value.errno == errno.EBADF
        assert runner._private_closure is None
        assert runner._object_store is None
        assert not runner._closed
    finally:
        if root.exists():
            shutil.rmtree(root)
        if retained_root.exists():
            shutil.rmtree(retained_root)
        runner.close(suppress_terminal_error=True)


def test_descriptor_close_ebadf_confirms_already_closed(tmp_path: Path) -> None:
    """Break caught: EBADF after a close failure is treated as ambiguous instead of confirmed closed."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    directory = tmp_path / "ebadf"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(descriptor)
    identity = (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))
    os.close(descriptor)

    git_source._close_retained_descriptor(
        descriptor, identity, label="test EBADF"
    )


def test_descriptor_close_same_identity_retries_once(
    tmp_path: Path, monkeypatch
) -> None:
    """Break caught: a same-object transient close failure is never retried or retries without a bound."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    directory = tmp_path / "same-identity"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(descriptor)
    identity = (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))
    real_close = os.close
    calls = 0

    def fail_once(candidate: int) -> None:
        nonlocal calls
        if candidate == descriptor:
            calls += 1
            if calls == 1:
                raise OSError(errno.EIO, "injected transient close failure")
        real_close(candidate)

    monkeypatch.setattr(git_source.os, "close", fail_once)
    git_source._close_retained_descriptor(
        descriptor, identity, label="test same identity"
    )

    assert calls == 2
    with pytest.raises(OSError) as raised:
        os.fstat(descriptor)
    assert raised.value.errno == errno.EBADF


def test_descriptor_close_ambiguous_identity_does_not_retry(
    tmp_path: Path, monkeypatch
) -> None:
    """Break caught: an uncertain numeric FD is blindly retried after ownership cannot be inspected."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    directory = tmp_path / "ambiguous"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(descriptor)
    identity = (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))
    real_close = os.close
    real_fstat = os.fstat
    calls = 0

    def fail_close(candidate: int) -> None:
        nonlocal calls
        if candidate == descriptor:
            calls += 1
            raise OSError(errno.EIO, "injected close failure")
        real_close(candidate)

    def ambiguous_fstat(candidate: int):
        if candidate == descriptor:
            raise OSError(errno.EIO, "injected ambiguous descriptor state")
        return real_fstat(candidate)

    monkeypatch.setattr(git_source.os, "close", fail_close)
    monkeypatch.setattr(git_source.os, "fstat", ambiguous_fstat)
    try:
        with pytest.raises(
            GitAuthorityError, match="test ambiguous descriptor cleanup was not confirmed"
        ):
            git_source._close_retained_descriptor(
                descriptor, identity, label="test ambiguous"
            )
        assert calls == 1
    finally:
        real_close(descriptor)


def test_closure_cleanup_descriptor_failure_is_not_suppressed(
    tmp_path: Path, monkeypatch
) -> None:
    """Break caught: closure capture silently suppresses a retained descriptor close failure."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    root = tmp_path / "closure"
    prefix = root / "aa"
    prefix.mkdir(parents=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    prefix_fd = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY)
    root_identity = git_source._directory_identity(os.fstat(root_fd))
    prefix_identity = git_source._directory_identity(os.fstat(prefix_fd))
    capture = git_source._ClosureCapture(
        source=root,
        root_identity=root_identity,
        root_fd=root_fd,
        prefixes={"aa": (prefix_fd, prefix_identity)},
        objects=(),
    )
    real_close = os.close
    root_attempts = 0

    def fail_root_close(descriptor: int) -> None:
        nonlocal root_attempts
        if descriptor == root_fd:
            root_attempts += 1
            raise OSError(errno.EIO, "injected closure close failure")
        real_close(descriptor)

    monkeypatch.setattr(git_source.os, "close", fail_root_close)
    try:
        with pytest.raises(
            GitAuthorityError, match="Git closure root descriptor cleanup was not confirmed"
        ):
            capture.close()
        assert root_attempts == 2
        with pytest.raises(OSError) as raised:
            os.fstat(prefix_fd)
        assert raised.value.errno == errno.EBADF
        assert os.fstat(root_fd).st_ino == root_identity[1]
    finally:
        real_close(root_fd)


def test_aggregate_error_preserves_real_primary_and_cleanup_failures(
    git_fixture: GitFixture,
) -> None:
    """Break caught: a real terminal pack drift hides a simultaneous unconfirmed filesystem cleanup."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    runner, bootstrap, _commit_oid = _sealed_pack_runner(
        git_fixture, b"aggregate primary and cleanup\n"
    )
    removed_name = bootstrap.entries[0].name
    source_root = Path(os.readlink(f"/proc/self/fd/{bootstrap.source_fd}"))
    removed_source = source_root / removed_name
    removed_source.unlink()
    try:
        with pytest.raises(GitAuthorityError) as raised:
            runner.close()
        error = raised.value
        assert error.primary is not error.cleanup
        assert "pack" in str(error.primary)
        assert "cleanup" in str(error.cleanup)
        assert "pack" in str(error)
        assert "cleanup" in str(error)
        assert not runner._closed
    finally:
        _remove_owned_bootstrap_root(bootstrap, None)
        runner._pack_bootstraps.clear()
        runner.close(suppress_terminal_error=True)


def test_cleanup_processes_and_aggregates_all_retained_bootstraps(
    tmp_path: Path,
) -> None:
    """Break caught: the runner reports only one failed bootstrap and drops the rest after one attempt."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    first_fixture = GitFixture(tmp_path / "first-repo")
    second_fixture = GitFixture(tmp_path / "second-repo")
    first_runner, first, _ = _sealed_pack_runner(first_fixture, b"first cleanup\n")
    second_runner, second, _ = _sealed_pack_runner(second_fixture, b"second cleanup\n")

    def release_copied_store_pack_links(runner) -> None:
        private = runner._private_closure
        closure = runner._closure
        assert private is not None
        assert closure is not None
        runner._cleanup_private_object_store(private)
        assert runner._object_store_descriptors_closed
        runner._object_store = None
        runner._private_closure = None
        runner._private_objects = None
        closure.close()
        runner._closure = None

    release_copied_store_pack_links(first_runner)
    release_copied_store_pack_links(second_runner)
    first_runner._pack_bootstraps.append(second)
    parked = (
        tmp_path / "first-retained-bootstrap",
        tmp_path / "second-retained-bootstrap",
    )
    for bootstrap, retained in zip((first, second), parked, strict=True):
        bootstrap.root.rename(retained)
        bootstrap.root.mkdir(mode=0o700)
        (bootstrap.root / "foreign-marker").write_text(
            "foreign\n", encoding="utf-8"
        )
    try:
        with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
            first_runner._close_pack_bootstraps()
        assert raised.value.primary is not raised.value.cleanup
        assert first.cleanup_receipt().attempts == 1
        assert second.cleanup_receipt().attempts == 1
        assert first.cleanup_receipt().source_links_restored
        assert second.cleanup_receipt().source_links_restored
        assert first.cleanup_receipt().descriptors_closed
        assert second.cleanup_receipt().descriptors_closed
        assert first_runner._pack_bootstraps == [first, second]
        assert first.root.exists()
        assert second.root.exists()
        assert (first.root / "foreign-marker").is_file()
        assert (second.root / "foreign-marker").is_file()
        assert not tuple(parked[0].iterdir())
        assert not tuple(parked[1].iterdir())
    finally:
        for bootstrap, retained in zip((first, second), parked, strict=True):
            if bootstrap.root.exists():
                shutil.rmtree(bootstrap.root)
            if retained.exists():
                shutil.rmtree(retained)
        first_runner._pack_bootstraps.clear()
        second_runner._pack_bootstraps.clear()
        first_runner.close(suppress_terminal_error=True)
        second_runner.close(suppress_terminal_error=True)


def test_public_snapshot_rejects_required_pack_pair_introduced_after_freeze(
    git_fixture: GitFixture, tmp_path: Path, monkeypatch
) -> None:
    """Break caught: a required pair absent at operation start is adopted after namespace freeze."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("pin.md", b"late pair authority\n")
    git_fixture._git("gc", "--prune=now")
    objects = git_fixture.root / ".git/objects"
    assert not (objects / commit_oid[:2] / commit_oid[2:]).exists()
    pack_dir = objects / "pack"
    held = tmp_path / "held-pack-pair"
    held.mkdir()
    pair = tuple(sorted((*pack_dir.glob("*.pack"), *pack_dir.glob("*.idx"))))
    assert pair and any(path.suffix == ".pack" for path in pair)
    assert any(path.suffix == ".idx" for path in pair)
    for path in pair:
        path.replace(held / path.name)

    original_freeze = git_source._freeze_pack_namespace
    original_bootstrap = git_source._bootstrap_pack_view
    original_reader = git_source._BootstrapBatchReader
    introduced = False
    namespace_ref = None
    bootstrap_names: list[tuple[str, ...]] = []
    reader_launches = 0

    def freeze_then_introduce(source, limits, deadline):
        nonlocal introduced, namespace_ref
        namespace = original_freeze(source, limits, deadline)
        assert not namespace.entries
        namespace_ref = namespace
        for path in sorted(held.iterdir()):
            path.replace(pack_dir / path.name)
        introduced = True
        return namespace

    def record_bootstrap(*args, **kwargs):
        bootstrap = original_bootstrap(*args, **kwargs)
        if bootstrap is not None:
            bootstrap_names.append(tuple(entry.name for entry in bootstrap.entries))
        return bootstrap

    class RecordReader(original_reader):
        def __init__(self, *args, **kwargs):
            nonlocal reader_launches
            reader_launches += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(git_source, "_freeze_pack_namespace", freeze_then_introduce)
    monkeypatch.setattr(git_source, "_bootstrap_pack_view", record_bootstrap)
    monkeypatch.setattr(git_source, "_BootstrapBatchReader", RecordReader)
    try:
        snapshot = None
        caught = None
        try:
            snapshot = GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc
        assert snapshot is None
        assert type(caught) is GitAuthorityError
        assert introduced
        assert namespace_ref is not None and not namespace_ref.entries
        assert reader_launches == 0
        assert not bootstrap_names
    finally:
        for path in sorted(held.iterdir()):
            target = pack_dir / path.name
            if not target.exists():
                path.replace(target)


def test_public_deep_wide_dag_hits_global_unique_scheduling_budget(
    git_fixture: GitFixture, monkeypatch
) -> None:
    """Break caught: deep+wide unique objects accumulate outside the global scheduling budget."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    for branch in range(6):
        parts = [f"branch-{branch}", *(f"depth-{depth}" for depth in range(5))]
        leaf = git_fixture.root.joinpath(*parts, "pin.md")
        leaf.parent.mkdir(parents=True)
        leaf.write_bytes(f"branch {branch}\n".encode("ascii"))
    git_fixture._git("add", ".")
    git_fixture._git("commit", "-qm", "deep and wide")
    commit_oid = git_fixture._git("rev-parse", "HEAD").decode("ascii").strip()
    reachable = git_fixture._git("rev-list", "--objects", commit_oid).splitlines()
    limits = GitScanLimits(max_entries=8)
    object_reads: list[int] = []
    original_read = git_source._read_limited_fd

    def record_object_read(descriptor, expected_size, active_limits, deadline):
        object_reads.append(expected_size)
        return original_read(descriptor, expected_size, active_limits, deadline)

    monkeypatch.setattr(git_source, "_read_limited_fd", record_object_read)
    with pytest.raises(GitAuthorityError) as raised:
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid, limits=limits)

    assert type(raised.value) is GitAuthorityError
    assert str(raised.value) == "Git object closure scheduled entry cap exceeded"
    assert len(reachable) > limits.max_entries
    assert len(object_reads) == 3


def test_real_delta_compressed_pack_rejects_declared_blob_before_body_read(
    tmp_path: Path, monkeypatch
) -> None:
    """Break caught: a delta result is expanded through --batch before its declared size is bounded."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture = GitFixture(tmp_path / "delta-pack")
    revisions: dict[str, tuple[str, bytes]] = {}
    shared = b"shared delta-friendly payload line\n" * 2048
    for revision in range(24):
        data = shared + f"revision-{revision:02d}\n".encode("ascii") + bytes([65 + revision]) * 128
        commit_oid, _ = fixture.commit_file("pin.md", data)
        blob_oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
        revisions[blob_oid] = (commit_oid, data)
    fixture._git("repack", "-adf", "--depth=50", "--window=50")
    fixture._git("prune-packed")
    indexes = tuple((fixture.root / ".git/objects/pack").glob("*.idx"))
    assert len(indexes) == 1
    verified = fixture._git("verify-pack", "-v", str(indexes[0])).decode("ascii")
    delta_lines = []
    for line in verified.splitlines():
        fields = line.split()
        if len(fields) >= 7 and fields[0] in revisions and fields[1] == "blob":
            delta_lines.append(fields)
    assert delta_lines, "fixture did not create a delta-compressed related blob"
    target_fields = max(delta_lines, key=lambda fields: int(fields[5]))
    target_oid = target_fields[0]
    target_commit, target_data = revisions[target_oid]
    assert int(target_fields[5]) >= 1
    assert len(target_fields[6]) == 40
    objects = fixture.root / ".git/objects"
    assert not (objects / target_oid[:2] / target_oid[2:]).exists()

    target_reads: list[int] = []
    target_decoded_before_read: list[int] = []
    target_readers = []
    original_write = git_source._BootstrapBatchReader._write_request
    original_read = git_source._BootstrapBatchReader._read
    original_read_object = git_source._BootstrapBatchReader.read_object

    def record_write(self, request: bytes) -> None:
        self._t4_requested_oid = request.rstrip(b"\n").decode("ascii")
        return original_write(self, request)

    def record_read(self, size: int) -> bytes:
        if getattr(self, "_t4_requested_oid", None) == target_oid:
            target_reads.append(size)
            target_decoded_before_read.append(self._accounting().decoded_bytes)
        return original_read(self, size)

    def record_read_object(self, oid, expected_type, object_format):
        if oid == target_oid:
            target_readers.append(self)
        return original_read_object(self, oid, expected_type, object_format)

    monkeypatch.setattr(git_source._BootstrapBatchReader, "_write_request", record_write)
    monkeypatch.setattr(git_source._BootstrapBatchReader, "_read", record_read)
    monkeypatch.setattr(git_source._BootstrapBatchReader, "read_object", record_read_object)
    limits = GitScanLimits(
        max_blob_bytes=len(target_data) - 1,
        max_total_bytes=2_000_000,
        timeout_seconds=5.0,
    )
    with pytest.raises(GitAuthorityError) as raised:
        GitTreeSnapshot.from_commit(fixture.root, target_commit, limits=limits)

    assert type(raised.value) is GitAuthorityError
    assert target_reads
    assert all(size == 1 for size in target_reads)
    assert target_decoded_before_read
    reader = target_readers[-1]
    assert reader._accounting().decoded_bytes == target_decoded_before_read[0]
    assert reader.poisoned and reader.closed
    assert reader.process is not None and reader.process.poll() is not None


def test_controlled_runner_combines_cache_deadline_and_stream_caps(
    git_fixture: GitFixture, tmp_path: Path
) -> None:
    """Break caught: cache bounds disappear or stream activity resets the command deadline/caps."""
    import json

    records = tmp_path / "runner-records.jsonl"
    ticks = tmp_path / "deadline-ticks"
    executable = _controlled_executable(
        tmp_path / "git-r6-envelope",
        "import json, os, pathlib, sys, time\n"
        f"records = pathlib.Path({str(records)!r})\n"
        f"ticks = pathlib.Path({str(ticks)!r})\n"
        "mode = sys.argv[-1]\n"
        "record = {'mode': mode, 'count': os.environ.get('GIT_CONFIG_COUNT'), "
        "'key0': os.environ.get('GIT_CONFIG_KEY_0'), 'value0': os.environ.get('GIT_CONFIG_VALUE_0'), "
        "'key1': os.environ.get('GIT_CONFIG_KEY_1'), 'value1': os.environ.get('GIT_CONFIG_VALUE_1')}\n"
        "with records.open('a', encoding='utf-8') as handle: handle.write(json.dumps(record, sort_keys=True) + '\\n')\n"
        "if mode == 'deadline':\n"
        "    end = time.monotonic() + 1.5\n"
        "    count = 0\n"
        "    while time.monotonic() < end:\n"
        "        count += 1; ticks.write_text(str(count), encoding='ascii')\n"
        "        sys.stdout.buffer.write(b'o'); sys.stdout.flush()\n"
        "        sys.stderr.buffer.write(b'e'); sys.stderr.flush(); time.sleep(0.02)\n"
        "elif mode == 'stdout-cap':\n"
        "    sys.stdout.buffer.write(b'x' * 33); sys.stdout.flush(); time.sleep(1.5)\n"
        "elif mode == 'stderr-cap':\n"
        "    sys.stderr.buffer.write(b'x' * 65537); sys.stderr.flush(); time.sleep(1.5)",
    )
    runner = _controlled_runner(
        git_fixture,
        executable,
        GitScanLimits(max_total_bytes=20_000_000, timeout_seconds=0.15),
    )
    try:
        started = time.monotonic()
        with pytest.raises(GitAuthorityError) as deadline_error:
            runner.run(("deadline",), stdout_cap=1024)
        elapsed = time.monotonic() - started
        assert type(deadline_error.value) is GitAuthorityError
        assert str(deadline_error.value) == "Git command timed out"
        assert elapsed < 1.25
        assert int(ticks.read_text(encoding="ascii")) >= 3

        with pytest.raises(GitAuthorityError) as stdout_error:
            runner.run(("stdout-cap",), stdout_cap=32)
        assert type(stdout_error.value) is GitAuthorityError
        assert str(stdout_error.value) == "Git stdout cap exceeded"

        with pytest.raises(GitAuthorityError) as stderr_error:
            runner.run(("stderr-cap",))
        assert type(stderr_error.value) is GitAuthorityError
        assert str(stderr_error.value) == "Git stderr cap exceeded"
    finally:
        runner.close(suppress_terminal_error=True)

    observed = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    by_mode = {record["mode"]: record for record in observed}
    for mode in ("deadline", "stdout-cap", "stderr-cap"):
        assert by_mode[mode] == {
            "mode": mode,
            "count": "2",
            "key0": "core.deltaBaseCacheLimit",
            "value0": "5000000",
            "key1": "core.packedGitLimit",
            "value1": "5000000",
        }


@pytest.mark.parametrize(
    ("object_format", "packed", "data", "expected_sha256"),
    (
        (
            "sha1",
            False,
            b"sha1 ordinary receipt\n",
            "b97a40780fdccede5cefda19a94764b0af62470d0c89fb0c3e70ecd8361d2e19",
        ),
        (
            "sha1",
            True,
            b"sha1 packed receipt\n",
            "89cd39d72452c58ae314f19445234813558beb3812e3f2ab173cdd096cf0f943",
        ),
        (
            "sha256",
            False,
            b"sha256 ordinary receipt\n",
            "4dde3ed7996d139dd49bb55fe9ba913be1ab83fd79fb28639be53d2d1f2370f2",
        ),
        (
            "sha256",
            True,
            b"sha256 packed receipt\n",
            "2cf763773ea4af8bb6bbba0003f876caebade4e0b2a8ef83bb368ac9625a31ac",
        ),
    ),
    ids=("sha1-ordinary", "sha1-packed", "sha256-ordinary", "sha256-packed"),
)
def test_object_formats_preserve_exact_oids_and_literal_receipts_in_loose_and_packed_stores(
    tmp_path: Path,
    object_format: str,
    packed: bool,
    data: bytes,
    expected_sha256: str,
) -> None:
    """Break caught: storage representation changes exact OIDs or independent SHA-256 receipts."""
    fixture = GitFixture(tmp_path / "format-matrix", object_format=object_format)
    commit_oid, _ = fixture.commit_file("pin.md", data)
    tree_oid = fixture._git("rev-parse", f"{commit_oid}^{{tree}}").decode("ascii").strip()
    blob_oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
    width = 40 if object_format == "sha1" else 64
    objects = fixture.root / ".git/objects"
    loose_paths = tuple(objects / oid[:2] / oid[2:] for oid in (commit_oid, tree_oid, blob_oid))
    assert all(path.is_file() for path in loose_paths)
    if packed:
        fixture._git("gc", "--prune=now")
        assert all(not path.exists() for path in loose_paths)

    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    blob = snapshot.blob("pin.md")

    assert snapshot.object_format == object_format
    assert snapshot.commit_oid == commit_oid
    assert snapshot.tree_oid == tree_oid
    assert blob.blob_oid == blob_oid
    assert tuple(map(len, (commit_oid, tree_oid, blob_oid))) == (width, width, width)
    assert blob.data == data
    assert blob.sha256 == expected_sha256


def _copied_store_file_receipt(
    path: Path,
) -> tuple[int, int, int, int, int, int, int, str]:
    metadata = path.stat(follow_symlinks=False)
    assert stat.S_ISREG(metadata.st_mode)
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _copied_store_selected_file_receipt(
    path: Path,
) -> tuple[int, int, int, int, int, int, int, str, str]:
    """Capture the A4-selected foreign file receipt, including its exact path."""
    return (*_copied_store_file_receipt(path), os.fspath(path))


def _copied_store_tree_receipt(
    root: Path,
) -> tuple[tuple[str, int, int, int, int, int, int, int, str | None], ...]:
    receipt = []
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.stat(follow_symlinks=False)
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        receipt.append(
            (
                "." if path == root else path.relative_to(root).as_posix(),
                metadata.st_dev,
                metadata.st_ino,
                stat.S_IFMT(metadata.st_mode),
                metadata.st_uid,
                metadata.st_gid,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_size,
                digest,
            )
        )
    return tuple(receipt)


def _is_copied_store_parent(parent: Path) -> bool:
    return Path(parent).resolve() == Path(tempfile.gettempdir()).resolve()


@pytest.mark.parametrize(
    "unsafe_name",
    (
        b"bytes",
        "",
        ".",
        "..",
        "nested/name",
        "nested\\name",
        "nul\x00name",
        "control\x1fname",
        "delete\x7fname",
        "e\N{COMBINING ACUTE ACCENT}",
        "x" * 256,
        type("UnsafeString", (str,), {})("safe-looking"),
    ),
)
def test_copied_store_child_names_fail_closed_without_disclosure(
    unsafe_name,
) -> None:
    """Break caught: a multi-component or non-canonical child name reaches dir_fd I/O."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    with pytest.raises(GitAuthorityError) as caught:
        git_source._require_safe_child_name(
            unsafe_name,
            label="private copied-store child",
        )

    assert type(caught.value) is GitAuthorityError
    assert str(caught.value) == "private copied-store child name is unsafe"


def test_copied_store_descriptor_helpers_create_sealed_file_and_exact_inventory(
    tmp_path: Path,
) -> None:
    """Break caught: copied-store directory/file creation escapes retained descriptors."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    child = None
    try:
        child = git_source._mkdir_open_private_at(
            parent_fd,
            "objects",
            label="private copied-store objects",
        )
        entry = git_source._write_private_file_at(
            child.descriptor,
            "object",
            b"copied-object\n",
            label="private copied-store object",
        )

        assert child.basename == "objects"
        assert child.parent_descriptor == parent_fd
        assert not child.closed
        assert child.owned
        assert entry.name == "object"
        assert entry.sha256 == "107d6629bd9e0d762c44989bc0b9442ddc871e9111684bc506e949611f08fc95"
        assert entry.size == 14
        assert entry.mode == 0o400
        assert git_source._directory_inventory_at(
            child.descriptor,
            label="private copied-store objects",
        ) == ("object",)
        metadata = os.stat(
            "object",
            dir_fd=child.descriptor,
            follow_symlinks=False,
        )
        assert stat.S_ISREG(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == 0o400
        assert metadata.st_nlink == 1
        assert metadata.st_size == 14
    finally:
        if child is not None:
            try:
                os.unlink("object", dir_fd=child.descriptor)
            except FileNotFoundError:
                pass
            try:
                os.close(child.descriptor)
            except OSError:
                pass
            try:
                os.rmdir("objects", dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def test_copied_store_descriptor_hardlink_binds_source_and_destination_receipts(
    tmp_path: Path,
) -> None:
    """Break caught: a pack/index hardlink follows a mutable destination pathname."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir(mode=0o700)
    destination.mkdir(mode=0o700)
    source_entry = source / "source.pack"
    source_entry.write_bytes(b"pack-bytes\n")
    source_entry.chmod(0o400)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    source_fd = os.open(source, flags)
    destination_fd = os.open(destination, flags)
    try:
        expected_source_identity = git_source._store_identity(
            os.stat("source.pack", dir_fd=source_fd, follow_symlinks=False)
        )
        entry = git_source._link_private_file_at(
            source_fd=source_fd,
            source_name="source.pack",
            destination_fd=destination_fd,
            destination_name="retained.pack",
            expected_source_identity=expected_source_identity,
            label="private copied-store pack",
        )

        source_metadata = os.stat(
            "source.pack", dir_fd=source_fd, follow_symlinks=False
        )
        destination_metadata = os.stat(
            "retained.pack", dir_fd=destination_fd, follow_symlinks=False
        )
        assert (source_metadata.st_dev, source_metadata.st_ino) == (
            destination_metadata.st_dev,
            destination_metadata.st_ino,
        )
        assert source_metadata.st_nlink == expected_source_identity[3] + 1
        assert entry.name == "retained.pack"
        assert entry.sha256 == "af75ff216aea68c1de3e6dc0db458ae2f52b78f2df46325e6a2421d5f66b33b8"
        assert entry.size == 11
        assert entry.mode == 0o400
        assert git_source._directory_inventory_at(
            destination_fd,
            label="private copied-store pack",
        ) == ("retained.pack",)
    finally:
        try:
            os.unlink("retained.pack", dir_fd=destination_fd)
        except FileNotFoundError:
            pass
        os.close(destination_fd)
        os.close(source_fd)


def test_copied_store_copy_routes_selected_pack_pair_through_builder(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: selected pack/index files bypass builder descriptor authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_retain = git_source._retain_private_closure
    observed_names: tuple[str, ...] = ()
    observed_sources = 0

    def retain_after_builder_check(builder, capture, limits, deadline):
        nonlocal observed_names, observed_sources
        observed_names = tuple(sorted(builder.pack_entries))
        observed_sources = len(capture.pack_sources)
        assert observed_sources == 1
        assert len(observed_names) == 2
        assert observed_names[0].endswith(".idx") or observed_names[0].endswith(
            ".pack"
        )
        assert {Path(name).suffix for name in observed_names} == {".idx", ".pack"}
        return real_retain(builder, capture, limits, deadline)

    monkeypatch.setattr(
        git_source,
        "_retain_private_closure",
        retain_after_builder_check,
    )

    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)

    assert snapshot.blob("pin.md").data == expected
    assert observed_sources == 1
    assert len(observed_names) == 2
    assert _packed_source_nlinks(fixture) == baseline_nlinks


def test_copied_store_authority_transfer_moves_exact_builder_descriptors_once(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: sealing copies descriptor numbers while the builder remains an owner."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_retain = git_source._retain_private_closure
    before: tuple[object, int, int, dict[str, int]] | None = None
    after: tuple[object, int, int, dict[str, int]] | None = None
    builder_state: tuple[object, ...] | None = None

    def retain_and_record(builder, capture, limits, deadline):
        nonlocal before, after, builder_state
        before = (
            builder.owner,
            builder.objects.descriptor,
            builder.pack.descriptor,
            {
                prefix: handle.descriptor
                for prefix, handle in builder.prefixes.items()
            },
        )
        private = real_retain(builder, capture, limits, deadline)
        after = (
            private.owner,
            private.objects_fd,
            private.pack_fd,
            {
                prefix: descriptor
                for prefix, (descriptor, _identity) in private.prefixes.items()
            },
        )
        builder_state = (
            builder.sealed,
            builder.owner,
            builder.objects,
            builder.pack,
            dict(builder.prefixes),
            dict(builder.loose_entries),
            dict(builder.pack_entries),
            dict(builder.source_nlinks),
            builder.abort(),
        )
        return private

    monkeypatch.setattr(git_source, "_retain_private_closure", retain_and_record)

    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)

    assert snapshot.blob("pin.md").data == expected
    assert before is not None
    assert after == before
    assert builder_state == (True, None, None, None, {}, {}, {}, {}, {})
    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks


def test_copied_store_descriptor_relative_cleanup_removes_exact_entries_once(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: final cleanup skips pack links, uses a pathname, or closes twice."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_cleanup = git_source._GitRunner._cleanup_private_object_store
    real_unlink = os.unlink
    real_rmdir = os.rmdir
    real_close = os.close
    private_ref = None
    owned_root: Path | None = None
    receipts: dict[int, tuple[int, int, int]] = {}
    close_calls: dict[int, int] = {}
    operations: list[tuple[str, str]] = []
    prefix_fds: set[int] = set()
    pack_fd: int | None = None
    objects_fd: int | None = None
    root_fd: int | None = None

    def record_unlink(path, *, dir_fd=None):
        child = os.fsdecode(path)
        assert child not in {"", ".", ".."}
        assert "/" not in child and "\\" not in child
        if dir_fd in prefix_fds:
            operations.append(("unlink-loose", child))
        elif dir_fd == pack_fd:
            operations.append(("unlink-pack", child))
        return real_unlink(path, dir_fd=dir_fd)

    def record_rmdir(path, *, dir_fd=None):
        child = os.fsdecode(path)
        assert child not in {"", ".", ".."}
        assert "/" not in child and "\\" not in child
        if dir_fd == objects_fd:
            operations.append(("rmdir-object-child", child))
        elif dir_fd == root_fd:
            operations.append(("rmdir-objects", child))
        return real_rmdir(path, dir_fd=dir_fd)

    def record_close(descriptor):
        if descriptor in receipts:
            close_calls[descriptor] = close_calls.get(descriptor, 0) + 1
        return real_close(descriptor)

    def cleanup_and_record(self, private_closure):
        nonlocal private_ref, owned_root, receipts
        nonlocal prefix_fds, pack_fd, objects_fd, root_fd
        private_ref = private_closure
        owner = private_closure.owner
        owned_root = owner.parent_path_hint / owner.basename
        prefix_fds = {
            descriptor
            for descriptor, _identity in private_closure.prefixes.values()
        }
        pack_fd = private_closure.pack_fd
        objects_fd = private_closure.objects_fd
        root_fd = private_closure.root_fd
        retained = {
            owner.parent_fd,
            owner.root_fd,
            private_closure.objects_fd,
            private_closure.pack_fd,
            *prefix_fds,
        }
        receipts = {
            descriptor: _capture_descriptor_receipt(descriptor)
            for descriptor in retained
        }
        return real_cleanup(self, private_closure)

    monkeypatch.setattr(git_source.os, "unlink", record_unlink)
    monkeypatch.setattr(git_source.os, "rmdir", record_rmdir)
    monkeypatch.setattr(git_source.os, "close", record_close)
    monkeypatch.setattr(
        git_source._GitRunner,
        "_cleanup_private_object_store",
        cleanup_and_record,
    )

    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)

    assert snapshot.blob("pin.md").data == expected
    assert private_ref is not None
    assert owned_root is not None and not owned_root.exists()
    assert receipts
    _assert_descriptors_closed(receipts)
    assert close_calls == {descriptor: 1 for descriptor in receipts}
    close_calls_before_repeat = dict(close_calls)
    private_ref.close()
    assert close_calls == close_calls_before_repeat
    operation_kinds = [kind for kind, _name in operations]
    assert "unlink-loose" in operation_kinds
    assert operation_kinds.count("unlink-pack") == 2
    assert ("rmdir-object-child", "pack") in operations
    assert ("rmdir-objects", "objects") in operations
    assert max(
        index for index, kind in enumerate(operation_kinds) if kind == "unlink-loose"
    ) < min(
        index for index, kind in enumerate(operation_kinds) if kind == "unlink-pack"
    )
    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks


def test_copied_store_authority_transfer_preserves_verification_and_close_failures(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: verification primary is overwritten by final descriptor close failure."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_retain = git_source._retain_private_closure
    real_verify = git_source._verify_private_closure
    real_close = os.close
    target_descriptor: int | None = None
    target_receipt: tuple[int, int, int] | None = None
    verification_injected = False
    close_attempts = 0
    snapshot = None
    caught: BaseException | None = None

    def retain_and_capture(builder, capture, limits, deadline):
        nonlocal target_descriptor, target_receipt
        private = real_retain(builder, capture, limits, deadline)
        target_descriptor = private.root_fd
        target_receipt = _capture_descriptor_receipt(target_descriptor)
        return private

    def fail_first_post_transfer_verification(private, limits, deadline):
        nonlocal verification_injected
        if target_descriptor is not None and not verification_injected:
            verification_injected = True
            raise GitAuthorityError("injected B3 verification primary")
        return real_verify(private, limits, deadline)

    def fail_target_close(descriptor):
        nonlocal close_attempts
        if target_descriptor is not None and descriptor == target_descriptor:
            close_attempts += 1
            raise OSError(errno.EIO, "injected B3 retained-root close failure")
        return real_close(descriptor)

    try:
        with monkeypatch.context() as authority_patch:
            authority_patch.setattr(
                git_source, "_retain_private_closure", retain_and_capture
            )
            authority_patch.setattr(
                git_source, "_verify_private_closure", fail_first_post_transfer_verification
            )
            authority_patch.setattr(git_source.os, "close", fail_target_close)
            try:
                snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc

        assert snapshot is None
        assert verification_injected
        assert isinstance(caught, git_source.GitAuthorityAggregateError)
        assert str(caught.primary) == "injected B3 verification primary"
        assert "descriptor cleanup was not confirmed" in str(caught.cleanup)
        assert close_attempts == 2
        assert target_descriptor is not None
        assert target_receipt is not None
        assert _capture_descriptor_receipt(target_descriptor) == target_receipt
    finally:
        if target_descriptor is not None and target_receipt is not None:
            try:
                real_close(target_descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise

    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks


def test_copied_store_authority_transfer_preserves_seal_abort_and_restore_failures(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: seal-abort aggregation drops the source-restoration failure."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_abort = git_source._PrivateClosureBuilder.abort
    real_confirm = git_source._PrivateClosureBuilder.confirm_source_nlinks_restored
    confirm_calls = 0

    def fail_seal(self, *, expected_inventory, limits, deadline):
        raise RuntimeError("unstable injected B3 seal detail")

    def abort_then_fail(self):
        real_abort(self)
        raise GitAuthorityError("injected B3 builder abort cleanup")

    def fail_restoration_confirmation(self):
        nonlocal confirm_calls
        confirm_calls += 1
        if confirm_calls == 1:
            return real_confirm(self)
        raise GitAuthorityError("injected B3 restoration cleanup")

    monkeypatch.setattr(git_source._PrivateClosureBuilder, "seal", fail_seal)
    monkeypatch.setattr(
        git_source._PrivateClosureBuilder,
        "abort",
        abort_then_fail,
    )
    monkeypatch.setattr(
        git_source._PrivateClosureBuilder,
        "confirm_source_nlinks_restored",
        fail_restoration_confirmation,
    )

    with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
        GitTreeSnapshot.from_commit(fixture.root, commit_oid)

    rendered = str(raised.value)
    assert "private Git object-store descriptor seal failed" in rendered
    assert "unstable injected B3 seal detail" not in rendered
    assert "injected B3 builder abort cleanup" in rendered
    assert "injected B3 restoration cleanup" in rendered
    assert confirm_calls == 2
    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks


def test_copied_store_foreign_byte_identical_tree_is_never_retained_or_deleted(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: retention adopts and later deletes a byte-identical foreign object tree."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_retain = git_source._retain_private_closure
    parked_root: Path | None = None
    foreign_root: Path | None = None
    recorded_object: Path | None = None
    recorded_receipt: tuple[int, int, int, int, int, int, int, str, str] | None = None
    observed_receipt: tuple[int, int, int, int, int, int, int, str, str] | None = None
    parked_empty = False
    snapshot = None
    caught: BaseException | None = None
    swapped = False

    def retain_after_byte_identical_swap(builder, capture, limits, deadline):
        nonlocal parked_root, foreign_root, recorded_object, recorded_receipt
        nonlocal swapped
        root = builder.owner.parent_path_hint / builder.owner.basename
        destination = root / "objects"
        assert root.name.startswith("p1-u00-pack-")
        assert destination == root / "objects"
        parked_root = root.with_name(f"{root.name}.b1-original")
        root.rename(parked_root)
        shutil.copytree(parked_root, root)
        foreign_root = root
        candidates = tuple(
            path
            for path in sorted((foreign_root / "objects").rglob("*"))
            if path.is_file()
        )
        assert candidates
        recorded_object = candidates[0]
        recorded_receipt = _copied_store_selected_file_receipt(recorded_object)
        assert recorded_receipt[3:6] == (os.geteuid(), os.getegid(), 0o400)
        assert stat.S_IMODE(foreign_root.stat().st_mode) == 0o700
        swapped = True
        return real_retain(builder, capture, limits, deadline)

    monkeypatch.setattr(
        git_source, "_retain_private_closure", retain_after_byte_identical_swap
    )
    try:
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc

        assert swapped
        assert snapshot is None
        assert isinstance(caught, GitAuthorityError)
        assert foreign_root is not None
        assert parked_root is not None
        assert recorded_object is not None
        assert recorded_receipt is not None
        if recorded_object.is_file():
            observed_receipt = _copied_store_selected_file_receipt(recorded_object)
        parked_empty = parked_root.is_dir() and not tuple(parked_root.iterdir())
    finally:
        for root in (foreign_root, parked_root):
            if root is not None and root.exists():
                shutil.rmtree(root)

    gc.collect()
    gc.collect()
    observed_fds = _stable_descriptor_classes()
    observed_roots = _task_owned_pack_roots(fixture)
    process_groups = _reviewer_process_groups()
    observed_nlinks = _packed_source_nlinks(fixture)
    assert observed_fds == baseline_fds
    assert observed_roots == baseline_roots
    assert process_groups == ()
    assert observed_nlinks == baseline_nlinks
    assert observed_receipt == recorded_receipt, (
        "foreign byte-identical copied-store object was deleted or changed; "
        f"recorded={recorded_receipt!r}; observed={observed_receipt!r}"
    )
    assert parked_empty, "owned parked copied-store contents were not cleaned by descriptor"
    assert recorded_receipt is not None
    print(f"B4_A4_FOREIGN_SELECTED_FILE_RECEIPT={recorded_receipt!r}")
    _emit_b4_batch_receipt(
        label="a4-public-foreign-byte-identical",
        iterations=1,
        baseline_fds=baseline_fds,
        observed_fds=observed_fds,
        baseline_roots=baseline_roots,
        observed_roots=observed_roots,
        process_groups=process_groups,
        baseline_nlinks=baseline_nlinks,
        observed_nlinks=observed_nlinks,
        foreign_receipts=(recorded_receipt,),
    )


@pytest.mark.parametrize(
    "swap_point",
    (
        "after-owner-root-creation",
        "after-objects-directory-open",
        "after-pack-directory-open",
        "after-loose-object-copy",
        "after-pack-index-link",
        "immediately-before-seal",
        "immediately-after-seal",
        "immediately-before-cleanup",
    ),
)
def test_copied_store_root_name_swap_points_never_adopt_foreign_authority(
    packed_git_fixture,
    monkeypatch,
    swap_point: str,
) -> None:
    """Break caught: any lifecycle seam follows a foreign replacement root name."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    repetitions = 20 if swap_point in {
        "immediately-before-seal",
        "immediately-after-seal",
    } else 1
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)
    foreign_receipts: list[tuple[object, ...]] = []

    for iteration in range(repetitions):
        real_create = git_source._OwnedTemporaryRoot.create
        real_mkdir_open = git_source._mkdir_open_private_at
        real_write = git_source._PrivateClosureBuilder.write_loose_object
        real_link = git_source._PrivateClosureBuilder.link_pack_entry
        real_retain = git_source._retain_private_closure
        real_cleanup = git_source._GitRunner._cleanup_private_object_store
        copied_owner = None
        copied_objects_fd: int | None = None
        foreign_root: Path | None = None
        parked_root: Path | None = None
        initial_foreign_receipt: tuple[object, ...] | None = None
        observed_foreign_receipt: tuple[object, ...] | None = None
        caught: BaseException | None = None
        snapshot = None
        swapped = False
        parked_empty = False

        def swap_owned_root() -> None:
            nonlocal foreign_root, parked_root, initial_foreign_receipt, swapped
            assert copied_owner is not None
            assert not swapped
            foreign_root = copied_owner.parent_path_hint / copied_owner.basename
            parked_root = foreign_root.with_name(
                f"{copied_owner.basename}.b4-{swap_point}-{iteration}-original"
            )
            os.rename(
                copied_owner.basename,
                parked_root.name,
                src_dir_fd=copied_owner.parent_fd,
                dst_dir_fd=copied_owner.parent_fd,
            )
            os.mkdir(copied_owner.basename, 0o700, dir_fd=copied_owner.parent_fd)
            marker = foreign_root / "foreign-owner-marker"
            marker.write_text(
                f"foreign B4 {swap_point} cycle {iteration}\n",
                encoding="utf-8",
            )
            initial_foreign_receipt = _copied_store_tree_receipt(foreign_root)
            swapped = True

        def create_then_maybe_swap(cls, parent):
            nonlocal copied_owner
            owner = real_create(parent)
            if _is_copied_store_parent(Path(parent)):
                copied_owner = owner
                if swap_point == "after-owner-root-creation":
                    swap_owned_root()
            return owner

        def mkdir_open_then_maybe_swap(parent_fd, name, *, label):
            nonlocal copied_objects_fd
            handle = real_mkdir_open(parent_fd, name, label=label)
            if copied_owner is not None:
                if parent_fd == copied_owner.root_fd and name == "objects":
                    copied_objects_fd = handle.descriptor
                    if swap_point == "after-objects-directory-open" and not swapped:
                        swap_owned_root()
                elif (
                    copied_objects_fd is not None
                    and parent_fd == copied_objects_fd
                    and name == "pack"
                    and swap_point == "after-pack-directory-open"
                    and not swapped
                ):
                    swap_owned_root()
            return handle

        def write_then_maybe_swap(self, *, prefix, name, compressed):
            entry = real_write(
                self,
                prefix=prefix,
                name=name,
                compressed=compressed,
            )
            if (
                self.owner is copied_owner
                and swap_point == "after-loose-object-copy"
                and not swapped
            ):
                swap_owned_root()
            return entry

        def link_then_maybe_swap(
            self,
            *,
            source_directory_fd,
            source_name,
            destination_name,
            expected_source_identity,
        ):
            entry = real_link(
                self,
                source_directory_fd=source_directory_fd,
                source_name=source_name,
                destination_name=destination_name,
                expected_source_identity=expected_source_identity,
            )
            if (
                self.owner is copied_owner
                and swap_point == "after-pack-index-link"
                and len(self.pack_entries) == 2
                and not swapped
            ):
                swap_owned_root()
            return entry

        def retain_with_swap(builder, capture, limits, deadline):
            if swap_point == "immediately-before-seal" and not swapped:
                swap_owned_root()
            private = real_retain(builder, capture, limits, deadline)
            if swap_point == "immediately-after-seal" and not swapped:
                swap_owned_root()
            return private

        def cleanup_with_swap(self, private_closure):
            if swap_point == "immediately-before-cleanup" and not swapped:
                swap_owned_root()
            return real_cleanup(self, private_closure)

        try:
            with monkeypatch.context() as swap_patch:
                swap_patch.setattr(
                    git_source._OwnedTemporaryRoot,
                    "create",
                    classmethod(create_then_maybe_swap),
                )
                swap_patch.setattr(
                    git_source,
                    "_mkdir_open_private_at",
                    mkdir_open_then_maybe_swap,
                )
                swap_patch.setattr(
                    git_source._PrivateClosureBuilder,
                    "write_loose_object",
                    write_then_maybe_swap,
                )
                swap_patch.setattr(
                    git_source._PrivateClosureBuilder,
                    "link_pack_entry",
                    link_then_maybe_swap,
                )
                swap_patch.setattr(
                    git_source,
                    "_retain_private_closure",
                    retain_with_swap,
                )
                swap_patch.setattr(
                    git_source._GitRunner,
                    "_cleanup_private_object_store",
                    cleanup_with_swap,
                )
                try:
                    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
                except BaseException as exc:
                    caught = exc

            assert swapped
            assert snapshot is None
            assert isinstance(caught, GitAuthorityError)
            assert "root-name cleanup was not confirmed" in str(caught)
            assert foreign_root is not None
            assert parked_root is not None
            assert initial_foreign_receipt is not None
            observed_foreign_receipt = _copied_store_tree_receipt(foreign_root)
            parked_empty = parked_root.is_dir() and not tuple(parked_root.iterdir())
            assert observed_foreign_receipt == initial_foreign_receipt
            assert parked_empty
            foreign_receipts.append(initial_foreign_receipt)
        finally:
            for root in (foreign_root, parked_root):
                if root is not None and root.exists():
                    shutil.rmtree(root)

        assert _stable_descriptor_classes() == baseline_fds
        assert _task_owned_pack_roots(fixture) == baseline_roots
        assert _packed_source_nlinks(fixture) == baseline_nlinks

    gc.collect()
    gc.collect()
    observed_fds = _stable_descriptor_classes()
    observed_roots = _task_owned_pack_roots(fixture)
    process_groups = _reviewer_process_groups()
    observed_nlinks = _packed_source_nlinks(fixture)
    _emit_b4_batch_receipt(
        label=f"root-name-swap-{swap_point}",
        iterations=repetitions,
        baseline_fds=baseline_fds,
        observed_fds=observed_fds,
        baseline_roots=baseline_roots,
        observed_roots=observed_roots,
        process_groups=process_groups,
        baseline_nlinks=baseline_nlinks,
        observed_nlinks=observed_nlinks,
        foreign_receipts=tuple(foreign_receipts),
    )
    assert len(foreign_receipts) == repetitions
    assert observed_fds == baseline_fds
    assert observed_roots == baseline_roots
    assert process_groups == ()
    assert observed_nlinks == baseline_nlinks


def _exercise_public_foreign_byte_identical_replacement(
    *,
    fixture: GitFixture,
    commit_oid: str,
    monkeypatch,
    suffix: str,
    expect_pack_pair: bool,
) -> tuple[object, ...]:
    """Run one public copied-store foreign replacement with test-owned cleanup."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    real_retain = git_source._retain_private_closure
    parked_root: Path | None = None
    foreign_root: Path | None = None
    selected_file: Path | None = None
    recorded_receipt: tuple[object, ...] | None = None
    observed_receipt: tuple[object, ...] | None = None
    caught: BaseException | None = None
    snapshot = None
    swapped = False

    def retain_after_byte_identical_swap(builder, capture, limits, deadline):
        nonlocal parked_root, foreign_root, selected_file, recorded_receipt, swapped
        assert bool(capture.pack_sources) is expect_pack_pair
        assert bool(builder.pack_entries) is expect_pack_pair
        assert builder.owner is not None
        root = builder.owner.parent_path_hint / builder.owner.basename
        parked_root = root.with_name(f"{root.name}.b4-{suffix}-original")
        root.rename(parked_root)
        shutil.copytree(parked_root, root)
        foreign_root = root
        candidates = tuple(
            path
            for path in sorted((foreign_root / "objects").rglob("*"))
            if path.is_file()
        )
        assert candidates
        selected_file = candidates[0]
        recorded_receipt = _copied_store_selected_file_receipt(selected_file)
        swapped = True
        return real_retain(builder, capture, limits, deadline)

    try:
        with monkeypatch.context() as replacement_patch:
            replacement_patch.setattr(
                git_source,
                "_retain_private_closure",
                retain_after_byte_identical_swap,
            )
            try:
                snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc

        assert swapped
        assert snapshot is None
        assert isinstance(caught, GitAuthorityError)
        assert "root-name cleanup was not confirmed" in str(caught)
        assert foreign_root is not None
        assert parked_root is not None
        assert selected_file is not None
        assert recorded_receipt is not None
        if selected_file.is_file():
            observed_receipt = _copied_store_selected_file_receipt(selected_file)
        assert observed_receipt == recorded_receipt
        assert parked_root.is_dir() and not tuple(parked_root.iterdir())
        return recorded_receipt
    finally:
        for root in (foreign_root, parked_root):
            if root is not None and root.exists():
                shutil.rmtree(root)


def test_loose_only_copied_store_foreign_path_swap_has_no_selected_pack_pair(
    git_fixture,
    monkeypatch,
) -> None:
    """Break caught: a loose-only closure gains pack authority or adopts a foreign root."""
    commit_oid, expected = git_fixture.commit_file(
        "pin.md", b"B4 loose-only copied-store authority\n"
    )
    assert not tuple((git_fixture.root / ".git/objects/pack").glob("*.pack"))
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(git_fixture)
    baseline_nlinks: tuple[tuple[str, int, int, int], ...] = ()

    receipt = _exercise_public_foreign_byte_identical_replacement(
        fixture=git_fixture,
        commit_oid=commit_oid,
        monkeypatch=monkeypatch,
        suffix="loose-only",
        expect_pack_pair=False,
    )

    gc.collect()
    gc.collect()
    observed_fds = _stable_descriptor_classes()
    observed_roots = _task_owned_pack_roots(git_fixture)
    process_groups = _reviewer_process_groups()
    observed_nlinks: tuple[tuple[str, int, int, int], ...] = ()
    _emit_b4_batch_receipt(
        label="loose-only-foreign-path-swap",
        iterations=1,
        baseline_fds=baseline_fds,
        observed_fds=observed_fds,
        baseline_roots=baseline_roots,
        observed_roots=observed_roots,
        process_groups=process_groups,
        baseline_nlinks=baseline_nlinks,
        observed_nlinks=observed_nlinks,
        foreign_receipts=(receipt,),
    )
    assert observed_fds == baseline_fds
    assert observed_roots == baseline_roots
    assert process_groups == ()
    assert observed_nlinks == baseline_nlinks
    assert expected == b"B4 loose-only copied-store authority\n"


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_packed_object_formats_reject_foreign_byte_identical_copied_store(
    tmp_path: Path,
    monkeypatch,
    object_format: str,
) -> None:
    """Break caught: SHA-1 or SHA-256 packed bytes make a foreign tree authoritative."""
    fixture = GitFixture(
        tmp_path / f"packed-foreign-{object_format}", object_format=object_format
    )
    commit_oid, _expected = fixture.commit_file(
        "pin.md", f"B4 {object_format} packed foreign control\n".encode("ascii")
    )
    fixture._git("gc", "--prune=now")
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)

    receipt = _exercise_public_foreign_byte_identical_replacement(
        fixture=fixture,
        commit_oid=commit_oid,
        monkeypatch=monkeypatch,
        suffix=f"packed-{object_format}",
        expect_pack_pair=True,
    )

    gc.collect()
    gc.collect()
    observed_fds = _stable_descriptor_classes()
    observed_roots = _task_owned_pack_roots(fixture)
    process_groups = _reviewer_process_groups()
    observed_nlinks = _packed_source_nlinks(fixture)
    _emit_b4_batch_receipt(
        label=f"packed-{object_format}-foreign-byte-identical",
        iterations=1,
        baseline_fds=baseline_fds,
        observed_fds=observed_fds,
        baseline_roots=baseline_roots,
        observed_roots=observed_roots,
        process_groups=process_groups,
        baseline_nlinks=baseline_nlinks,
        observed_nlinks=observed_nlinks,
        foreign_receipts=(receipt,),
    )
    assert observed_fds == baseline_fds
    assert observed_roots == baseline_roots
    assert process_groups == ()
    assert observed_nlinks == baseline_nlinks


def test_repeated_foreign_byte_identical_replacements_preserve_receipts_and_custody(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated byte-identical swaps leak or eventually gain authority."""
    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_roots = _task_owned_pack_roots(fixture)
    baseline_nlinks = _packed_source_nlinks(fixture)
    receipts: list[tuple[object, ...]] = []

    for iteration in range(20):
        receipts.append(
            _exercise_public_foreign_byte_identical_replacement(
                fixture=fixture,
                commit_oid=commit_oid,
                monkeypatch=monkeypatch,
                suffix=f"stress-{iteration}",
                expect_pack_pair=True,
            )
        )
        assert _stable_descriptor_classes() == baseline_fds
        assert _task_owned_pack_roots(fixture) == baseline_roots
        assert _packed_source_nlinks(fixture) == baseline_nlinks

    gc.collect()
    gc.collect()
    observed_fds = _stable_descriptor_classes()
    observed_roots = _task_owned_pack_roots(fixture)
    process_groups = _reviewer_process_groups()
    observed_nlinks = _packed_source_nlinks(fixture)
    _emit_b4_batch_receipt(
        label="foreign-byte-identical-replacement-failures",
        iterations=20,
        baseline_fds=baseline_fds,
        observed_fds=observed_fds,
        baseline_roots=baseline_roots,
        observed_roots=observed_roots,
        process_groups=process_groups,
        baseline_nlinks=baseline_nlinks,
        observed_nlinks=observed_nlinks,
        foreign_receipts=tuple(receipts),
    )
    assert len(receipts) == 20
    assert observed_fds == baseline_fds
    assert observed_roots == baseline_roots
    assert process_groups == ()
    assert observed_nlinks == baseline_nlinks


def test_copied_store_creation_uses_owned_root_fd_after_name_swap(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: copied-store construction follows a replacement root name instead of its FD."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_create = git_source._OwnedTemporaryRoot.create
    foreign_root: Path | None = None
    parked_root: Path | None = None
    marker: Path | None = None
    snapshot = None
    caught: BaseException | None = None
    swapped = False
    foreign_objects_absent = False

    def create_then_swap(cls, parent):
        nonlocal foreign_root, parked_root, marker, swapped
        owner = real_create(parent)
        if not _is_copied_store_parent(Path(parent)):
            return owner
        foreign_root = owner.parent_path_hint / owner.basename
        parked_root = foreign_root.with_name(f"{owner.basename}.b1-original")
        os.rename(
            owner.basename,
            parked_root.name,
            src_dir_fd=owner.parent_fd,
            dst_dir_fd=owner.parent_fd,
        )
        os.mkdir(owner.basename, 0o700, dir_fd=owner.parent_fd)
        marker = foreign_root / "foreign-owner-marker"
        marker.write_text("foreign copied-store root\n", encoding="utf-8")
        swapped = True
        return owner

    monkeypatch.setattr(
        git_source._OwnedTemporaryRoot, "create", classmethod(create_then_swap)
    )
    try:
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc

        assert swapped
        assert snapshot is None
        assert isinstance(caught, GitAuthorityError)
        assert foreign_root is not None
        assert parked_root is not None
        assert marker is not None
        assert marker.read_text(encoding="utf-8") == "foreign copied-store root\n"
        foreign_objects_absent = not (foreign_root / "objects").exists()
    finally:
        for root in (foreign_root, parked_root):
            if root is not None and root.exists():
                shutil.rmtree(root)

    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks
    assert foreign_objects_absent, "copied-store construction wrote through a foreign root name"


def test_copied_store_writes_use_objects_fd_after_objects_name_swap(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: copied object writes follow a replacement objects name after FD capture."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_create = git_source._OwnedTemporaryRoot.create
    real_mkdir_open = git_source._mkdir_open_private_at
    real_open = os.open
    copied_owner = None
    owned_root: Path | None = None
    parked_objects: Path | None = None
    foreign_objects: Path | None = None
    marker: Path | None = None
    owned_objects_fd: int | None = None
    owned_objects_identity: tuple[int, int, int] | None = None
    initial_foreign_receipt = None
    observed_foreign_receipt = None
    snapshot = None
    caught: BaseException | None = None
    swapped = False

    def capture_owner(cls, parent):
        nonlocal copied_owner, owned_root
        owner = real_create(parent)
        if _is_copied_store_parent(Path(parent)):
            copied_owner = owner
            owned_root = owner.parent_path_hint / owner.basename
        return owner

    def mkdir_open_then_swap_objects(parent_fd, name, *, label):
        nonlocal parked_objects, foreign_objects, marker, owned_objects_fd
        nonlocal owned_objects_identity, initial_foreign_receipt, swapped
        result = real_mkdir_open(parent_fd, name, label=label)
        if (
            not swapped
            and copied_owner is not None
            and parent_fd == copied_owner.root_fd
            and name == "objects"
        ):
            assert owned_root is not None
            owned_objects_fd = os.dup(result.descriptor)
            owned_objects_identity = _capture_descriptor_receipt(owned_objects_fd)
            parked_objects = owned_root / "objects.b1-original"
            os.rename(
                "objects",
                "objects.b1-original",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.mkdir("objects", 0o700, dir_fd=parent_fd)
            foreign_objects = owned_root / "objects"
            marker = foreign_objects / "foreign-owner-marker"
            marker.write_text("foreign copied-store objects\n", encoding="utf-8")
            initial_foreign_receipt = _copied_store_tree_receipt(foreign_objects)
            swapped = True
        return result

    monkeypatch.setattr(
        git_source._OwnedTemporaryRoot, "create", classmethod(capture_owner)
    )
    monkeypatch.setattr(
        git_source, "_mkdir_open_private_at", mkdir_open_then_swap_objects
    )
    try:
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc

        assert swapped
        assert snapshot is None
        assert isinstance(caught, GitAuthorityError)
        assert foreign_objects is not None
        assert marker is not None
        assert marker.read_text(encoding="utf-8") == "foreign copied-store objects\n"
        observed_foreign_receipt = _copied_store_tree_receipt(foreign_objects)
    finally:
        if owned_objects_fd is not None and owned_objects_identity is not None:
            _close_descriptors_still_owned(
                {owned_objects_fd: owned_objects_identity}
            )
        if owned_root is not None and owned_root.exists():
            shutil.rmtree(owned_root)

    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks
    assert observed_foreign_receipt == initial_foreign_receipt, (
        "copied-store object writes mutated the foreign objects tree"
    )


def test_copied_store_pack_links_use_pack_fd_after_pack_name_swap(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: pack bootstrap hardlinks follow a replacement pack name instead of its FD."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_open = os.open
    bootstrap_root: Path | None = None
    parked_pack: Path | None = None
    foreign_pack: Path | None = None
    marker: Path | None = None
    initial_foreign_receipt = None
    observed_foreign_receipt = None
    snapshot = None
    caught: BaseException | None = None
    swapped = False

    def open_then_swap_pack(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal bootstrap_root, parked_pack, foreign_pack, marker
        nonlocal initial_foreign_receipt, swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if (
            not swapped
            and dir_fd is not None
            and os.fsdecode(path) == "pack"
        ):
            opened_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            if (
                opened_path.parent.name == "objects"
                and opened_path.parent.parent.name.startswith("p1-u00-pack-")
            ):
                bootstrap_root = opened_path.parent.parent
                parked_pack = opened_path.with_name("pack.b1-original")
                opened_path.rename(parked_pack)
                opened_path.mkdir(mode=0o700)
                foreign_pack = opened_path
                marker = foreign_pack / "foreign-owner-marker"
                marker.write_text("foreign copied-store pack\n", encoding="utf-8")
                initial_foreign_receipt = _copied_store_tree_receipt(foreign_pack)
                swapped = True
        return descriptor

    monkeypatch.setattr(git_source.os, "open", open_then_swap_pack)
    try:
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc

        assert swapped
        assert snapshot is None
        assert isinstance(caught, GitAuthorityError)
        assert foreign_pack is not None
        assert marker is not None
        assert marker.read_text(encoding="utf-8") == "foreign copied-store pack\n"
        observed_foreign_receipt = _copied_store_tree_receipt(foreign_pack)
    finally:
        if bootstrap_root is not None and bootstrap_root.exists():
            shutil.rmtree(bootstrap_root)

    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks
    assert observed_foreign_receipt == initial_foreign_receipt


def test_copied_store_construction_and_seal_do_not_consume_path_hint(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: copied-store construction consumes diagnostic path_hint as authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_create = git_source._OwnedTemporaryRoot.create
    real_path_hint = git_source._OwnedTemporaryRoot.path_hint
    guarded_owner = None
    foreign_root: Path | None = None
    parked_root: Path | None = None
    marker: Path | None = None
    path_hint_calls = 0
    snapshot = None
    caught: BaseException | None = None
    swapped = False

    def create_then_drift(cls, parent):
        nonlocal guarded_owner, foreign_root, parked_root, marker, swapped
        owner = real_create(parent)
        if not _is_copied_store_parent(Path(parent)):
            return owner
        foreign_root = owner.parent_path_hint / owner.basename
        parked_root = foreign_root.with_name(f"{owner.basename}.b1-original")
        os.rename(
            owner.basename,
            parked_root.name,
            src_dir_fd=owner.parent_fd,
            dst_dir_fd=owner.parent_fd,
        )
        os.mkdir(owner.basename, 0o700, dir_fd=owner.parent_fd)
        marker = foreign_root / "foreign-owner-marker"
        marker.write_text("foreign path-hint guard\n", encoding="utf-8")
        guarded_owner = owner
        swapped = True
        return owner

    def reject_authority_hint(owner):
        nonlocal path_hint_calls
        if owner is guarded_owner:
            path_hint_calls += 1
            raise AssertionError("path_hint used as copied-store authority")
        assert real_path_hint.fget is not None
        return real_path_hint.fget(owner)

    monkeypatch.setattr(
        git_source._OwnedTemporaryRoot, "create", classmethod(create_then_drift)
    )
    monkeypatch.setattr(
        git_source._OwnedTemporaryRoot, "path_hint", property(reject_authority_hint)
    )
    try:
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc

        assert swapped
        assert snapshot is None
        assert isinstance(caught, GitAuthorityError)
        assert marker is not None
        assert marker.read_text(encoding="utf-8") == "foreign path-hint guard\n"
    finally:
        for root in (foreign_root, parked_root):
            if root is not None and root.exists():
                shutil.rmtree(root)

    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks
    assert path_hint_calls == 0, "copied-store construction consumed diagnostic path_hint"


def test_private_closure_store_uses_builder_descriptors_without_reopen(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: private closure sealing reopens builder-owned directories by pathname."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    real_create = git_source._OwnedTemporaryRoot.create
    real_copy = git_source._copy_requested_closure
    real_open = os.open
    owned_root: Path | None = None
    forbidden_paths: set[str] = set()
    builder_ready = False
    blocked_reopens: list[str] = []
    snapshot = None
    caught: BaseException | None = None

    def capture_owner(cls, parent):
        nonlocal owned_root
        owner = real_create(parent)
        if _is_copied_store_parent(Path(parent)):
            owned_root = owner.parent_path_hint / owner.basename
        return owner

    def copy_then_close_path_authority(capture, builder, limits, deadline):
        nonlocal builder_ready, forbidden_paths
        result = real_copy(capture, builder, limits, deadline)
        root = builder.owner.parent_path_hint / builder.owner.basename
        forbidden_paths = {
            os.fspath(root),
            os.fspath(root / "objects"),
            os.fspath(root / "objects" / "pack"),
        }
        builder_ready = True
        return result

    def reject_pathname_reopen(path, flags, mode=0o777, *, dir_fd=None):
        decoded = os.fsdecode(path)
        if builder_ready and dir_fd is None and decoded in forbidden_paths:
            blocked_reopens.append(decoded)
            raise PermissionError(errno.EPERM, "copied-store pathname reopen rejected")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    try:
        with monkeypatch.context() as authority_patch:
            authority_patch.setattr(
                git_source._OwnedTemporaryRoot,
                "create",
                classmethod(capture_owner),
            )
            authority_patch.setattr(
                git_source,
                "_copy_requested_closure",
                copy_then_close_path_authority,
            )
            authority_patch.setattr(
                git_source.os, "open", reject_pathname_reopen
            )
            try:
                snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
    finally:
        if owned_root is not None and owned_root.exists():
            shutil.rmtree(owned_root)

    assert builder_ready
    assert _stable_descriptor_classes() == baseline_fds
    assert _packed_source_nlinks(fixture) == baseline_nlinks
    assert caught is None, f"private closure reopened builder paths: {blocked_reopens!r}"
    assert snapshot is not None
    assert snapshot.blob("pin.md").data == expected
    assert not blocked_reopens


test_private_closure_store_uses_builder_descriptors_without_reopen.copied_store = True


def test_copied_store_descriptor_authority_ast_boundary_rejects_pathname_reopen_and_mutation() -> None:
    """Break caught: copied-store authority functions regain direct pathname mutation or reopen."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    module = ast.parse(Path(git_source.__file__).read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in module.body if isinstance(node, ast.ClassDef)
    }
    functions = {
        node.name: node for node in module.body if isinstance(node, ast.FunctionDef)
    }
    builder = classes["_PrivateClosureBuilder"]
    builder_methods = {
        node.name: node
        for node in builder.body
        if isinstance(node, ast.FunctionDef)
    }
    store_methods = {
        node.name: node
        for node in classes["_PrivateClosureStore"].body
        if isinstance(node, ast.FunctionDef)
    }
    exact_boundaries = {
        "_GitRunner.seal_object_store": next(
            node
            for node in classes["_GitRunner"].body
            if isinstance(node, ast.FunctionDef) and node.name == "seal_object_store"
        ),
        "_PrivateClosureBuilder.create": builder_methods["create"],
        "_PrivateClosureBuilder.ensure_loose_prefix": builder_methods[
            "ensure_loose_prefix"
        ],
        "_PrivateClosureBuilder.write_loose_object": builder_methods[
            "write_loose_object"
        ],
        "_PrivateClosureBuilder.link_pack_entry": builder_methods[
            "link_pack_entry"
        ],
        "_PrivateClosureBuilder.seal": builder_methods["seal"],
        "_PrivateClosureBuilder.abort": builder_methods["abort"],
        "_PrivateClosureStore.close": store_methods["close"],
        "_mkdir_open_private_at": functions["_mkdir_open_private_at"],
        "_write_private_file_at": functions["_write_private_file_at"],
        "_link_private_file_at": functions["_link_private_file_at"],
        "_directory_inventory_at": functions["_directory_inventory_at"],
        "_verify_owned_directory_handle": functions[
            "_verify_owned_directory_handle"
        ],
        "_verify_owned_file_entry_at": functions[
            "_verify_owned_file_entry_at"
        ],
        "_verify_captured_pack_sources": functions[
            "_verify_captured_pack_sources"
        ],
        "_copy_requested_closure": functions["_copy_requested_closure"],
        "_retain_private_closure": functions["_retain_private_closure"],
        "_collect_restored_pack_source_receipts": functions[
            "_collect_restored_pack_source_receipts"
        ],
        "_handoff_restored_pack_receipts_after_builder_abort": functions[
            "_handoff_restored_pack_receipts_after_builder_abort"
        ],
        "_handoff_active_pack_receipts_after_builder_seal": functions[
            "_handoff_active_pack_receipts_after_builder_seal"
        ],
        "_verify_private_closure": functions["_verify_private_closure"],
        "_confirm_private_closure_source_nlinks_restored": functions[
            "_confirm_private_closure_source_nlinks_restored"
        ],
        "_GitRunner._cleanup_private_object_store": next(
            node
            for node in classes["_GitRunner"].body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_cleanup_private_object_store"
        ),
    }
    path_methods = {
        "iterdir",
        "mkdir",
        "open",
        "read_bytes",
        "rename",
        "replace",
        "rmdir",
        "unlink",
        "write_bytes",
    }
    dir_fd_requirements = {
        "link": {"src_dir_fd", "dst_dir_fd"},
        "mkdir": {"dir_fd"},
        "open": {"dir_fd"},
        "rename": {"src_dir_fd", "dst_dir_fd"},
        "replace": {"src_dir_fd", "dst_dir_fd"},
        "rmdir": {"dir_fd"},
        "stat": {"dir_fd"},
        "unlink": {"dir_fd"},
    }
    violations: list[str] = []

    for boundary, function in exact_boundaries.items():
        for node in ast.walk(function):
            if isinstance(node, ast.Attribute) and node.attr == "path_hint":
                violations.append(f"{boundary}: diagnostic path_hint consumed")
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "shutil"
                and node.func.attr.startswith("copy")
            ):
                violations.append(f"{boundary}: shutil.{node.func.attr}")
                continue
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                required = dir_fd_requirements.get(node.func.attr)
                if required is not None:
                    present = {keyword.arg for keyword in node.keywords}
                    missing = required - present
                    if missing:
                        violations.append(
                            f"{boundary}: os.{node.func.attr} lacks "
                            f"{','.join(sorted(missing))} at line {node.lineno}"
                        )
                continue
            if node.func.attr in path_methods:
                violations.append(
                    f"{boundary}: pathname {node.func.attr} at line {node.lineno}"
                )

    assert not violations, "copied-store descriptor boundary violations: " + "; ".join(
        sorted(set(violations))
    )


class _InjectedConstructionBaseException(BaseException):
    """Signal-style constructor fault used to prove BaseException cleanup."""


class _InjectedTransitionBaseException(BaseException):
    """Signal-style transition fault used to prove BaseException cleanup."""


def _private_store_owner(store):
    """Return the descriptor-root owner from the current or capsule-backed store."""
    direct = getattr(store, "owner", None)
    if direct is not None and all(
        hasattr(direct, attribute)
        for attribute in ("parent_fd", "root_fd", "basename")
    ):
        return direct
    for name, candidate in vars(store).items():
        if "ownership" not in name and "capsule" not in name:
            continue
        owner = getattr(candidate, "owner", None)
        if owner is not None and all(
            hasattr(owner, attribute)
            for attribute in ("parent_fd", "root_fd", "basename")
        ):
            return owner
    return None


def _private_store_descriptor_receipts(
    store,
) -> dict[int, tuple[int, int, int]]:
    """Capture independently fstat-derived receipts for copied-store authority."""
    descriptors: set[int] = set()
    owner = _private_store_owner(store)
    if owner is not None:
        descriptors.update((owner.parent_fd, owner.root_fd))
    for attribute in ("root_fd", "objects_fd", "pack_fd"):
        descriptor = getattr(store, attribute, None)
        if isinstance(descriptor, int):
            descriptors.add(descriptor)
    prefixes = getattr(store, "prefixes", {})
    for retained in prefixes.values():
        descriptor = (
            retained[0]
            if isinstance(retained, tuple)
            else getattr(retained, "descriptor", None)
        )
        if isinstance(descriptor, int):
            descriptors.add(descriptor)
    receipts: dict[int, tuple[int, int, int]] = {}
    for descriptor in descriptors:
        try:
            receipts[descriptor] = _capture_descriptor_receipt(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise
    return receipts


def _private_store_root(store) -> Path | None:
    owner = _private_store_owner(store)
    if owner is None:
        return None
    parent_hint = getattr(owner, "parent_path_hint", None)
    if not isinstance(parent_hint, Path):
        return None
    return parent_hint / owner.basename


def _descriptors_released(
    receipts: dict[int, tuple[int, int, int]],
) -> bool:
    """Treat EBADF or a different fstat identity as release of the captured FD."""
    for descriptor, expected in receipts.items():
        try:
            actual = _capture_descriptor_receipt(descriptor)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                continue
            raise
        if actual == expected:
            return False
    return True


def _private_store_capsule(store):
    candidates = [
        value
        for name, value in vars(store).items()
        if "ownership" in name or "capsule" in name
    ]
    assert len(candidates) == 1, (
        "private copied store must expose exactly one ownership capsule"
    )
    return candidates[0]


def _ownership_state_name(capsule) -> str:
    state = getattr(capsule, "state", None)
    name = getattr(state, "name", None)
    return name if isinstance(name, str) else str(state)


def _assert_store_receipts_unchanged(
    store,
    expected: dict[int, tuple[int, int, int]],
) -> None:
    assert _private_store_descriptor_receipts(store) == expected


def _reviewer_cleanup_private_store(store) -> None:
    """Release only the exact test-captured copied store after a RED failure."""
    owner = _private_store_owner(store)
    if owner is None:
        return
    receipts = _private_store_descriptor_receipts(store)
    prefixes = getattr(store, "prefixes", {})
    entries = getattr(store, "entries", ())
    pack_entries = getattr(store, "pack_entries", {})
    objects_fd = getattr(store, "objects_fd", None)
    pack_fd = getattr(store, "pack_fd", None)
    root_fd = getattr(store, "root_fd", owner.root_fd)

    for entry in entries:
        retained = prefixes.get(entry.prefix)
        if retained is None:
            continue
        prefix_fd = retained[0] if isinstance(retained, tuple) else retained.descriptor
        try:
            os.unlink(entry.name, dir_fd=prefix_fd)
        except (FileNotFoundError, NotADirectoryError):
            pass
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise
    if isinstance(pack_fd, int):
        for name in pack_entries:
            try:
                os.unlink(name, dir_fd=pack_fd)
            except (FileNotFoundError, NotADirectoryError):
                pass
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
    if isinstance(objects_fd, int):
        for prefix in prefixes:
            try:
                os.rmdir(prefix, dir_fd=objects_fd)
            except (FileNotFoundError, NotADirectoryError):
                pass
            except OSError as exc:
                if exc.errno not in (errno.EBADF, errno.ENOTEMPTY):
                    raise
        for child in ("pack",):
            try:
                os.rmdir(child, dir_fd=objects_fd)
            except (FileNotFoundError, NotADirectoryError):
                pass
            except OSError as exc:
                if exc.errno not in (errno.EBADF, errno.ENOTEMPTY):
                    raise
    if isinstance(root_fd, int):
        try:
            os.rmdir("objects", dir_fd=root_fd)
        except (FileNotFoundError, NotADirectoryError):
            pass
        except OSError as exc:
            if exc.errno not in (errno.EBADF, errno.ENOTEMPTY):
                raise
    try:
        os.rmdir(owner.basename, dir_fd=owner.parent_fd)
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError as exc:
        if exc.errno not in (errno.EBADF, errno.ENOTEMPTY):
            raise

    _close_descriptors_still_owned(receipts)
    root = _private_store_root(store)
    if root is not None and root.exists():
        assert root.parent.resolve() == Path(tempfile.gettempdir()).resolve()
        assert root.name.startswith("p1-u00-pack-")
        shutil.rmtree(root)


def _constructor_store(
    real_store,
    captured: tuple[tuple[object, ...], dict[str, object]] | None,
):
    if captured is None:
        return None
    arguments, keywords = captured
    return real_store(*arguments, **keywords)


def _constructor_authority_receipts(
    real_store,
    arguments: tuple[object, ...],
    keywords: dict[str, object],
) -> tuple[dict[int, tuple[int, int, int]], Path | None]:
    """Capture constructor inputs without completing store construction."""
    bound = inspect.signature(real_store).bind(*arguments, **keywords)
    owner = bound.arguments.get("owner")
    if owner is None:
        for name, candidate in bound.arguments.items():
            if "ownership" in name or "capsule" in name:
                owner = getattr(candidate, "owner", None)
                if owner is not None:
                    break
    descriptors = {
        descriptor
        for name, descriptor in bound.arguments.items()
        if (name.endswith("_fd") or name.endswith("descriptor"))
        and isinstance(descriptor, int)
    }
    if owner is not None:
        descriptors.update((owner.parent_fd, owner.root_fd))
    prefixes = bound.arguments.get("prefixes", {})
    if isinstance(prefixes, dict):
        for retained in prefixes.values():
            descriptor = (
                retained[0]
                if isinstance(retained, tuple)
                else getattr(retained, "descriptor", None)
            )
            if isinstance(descriptor, int):
                descriptors.add(descriptor)
    receipts = {
        descriptor: _capture_descriptor_receipt(descriptor)
        for descriptor in descriptors
    }
    root = None
    if owner is not None and isinstance(owner.parent_path_hint, Path):
        root = owner.parent_path_hint / owner.basename
    return receipts, root


def _capture_gc_receipts(fixture: GitFixture) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[int, ...],
    tuple[tuple[str, int, int, int], ...],
]:
    return (
        _stable_descriptor_classes(),
        _task_owned_pack_roots(fixture),
        _reviewer_process_groups(),
        _packed_source_nlinks(fixture),
    )


def _assert_ot4_iteration_clean(
    fixture: GitFixture,
    *,
    baseline: tuple[
        tuple[str, ...],
        tuple[str, ...],
        tuple[int, ...],
        tuple[tuple[str, int, int, int], ...],
    ],
    descriptor_receipts: dict[int, tuple[int, int, int]],
    owned_root: Path | None,
) -> tuple[object, ...]:
    """Prove one OT4 transfer attempt left no authority before or after GC."""
    observed = _capture_gc_receipts(fixture)
    descriptors_released = _descriptors_released(descriptor_receipts)
    root_absent = owned_root is not None and not owned_root.exists()
    gc.collect()
    after_first_gc = _capture_gc_receipts(fixture)
    gc.collect()
    after_second_gc = _capture_gc_receipts(fixture)

    assert baseline[2] == ()
    assert len(descriptor_receipts) >= 5
    assert descriptors_released
    assert root_absent
    assert observed == baseline
    assert after_first_gc == observed
    assert after_second_gc == after_first_gc
    return (
        len(descriptor_receipts),
        descriptors_released,
        root_absent,
        observed,
        after_first_gc,
        after_second_gc,
    )


def _emit_ot4_stress_receipt(
    *,
    label: str,
    iterations: int,
    baseline: tuple[object, ...],
    observed: tuple[object, ...],
    iteration_receipts: tuple[tuple[object, ...], ...],
) -> None:
    receipt_sha256 = hashlib.sha256(
        repr(iteration_receipts).encode("utf-8")
    ).hexdigest()
    print(
        "OT4_STRESS_RECEIPT "
        f"label={label!r} iterations={iterations} "
        f"baseline={baseline!r} observed={observed!r} "
        f"iteration_receipt_count={len(iteration_receipts)} "
        f"iteration_receipt_sha256={receipt_sha256}"
    )


@pytest.mark.parametrize(
    "injected",
    (
        MemoryError("injected private store constructor failure"),
        _InjectedConstructionBaseException(
            "injected private store constructor BaseException"
        ),
    ),
    ids=("memory-error", "base-exception"),
)
def test_store_constructor_failure_keeps_builder_ownership_and_cleans(
    packed_git_fixture,
    monkeypatch,
    injected: BaseException,
) -> None:
    """Break caught: a failed non-owning store construction strands builder authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    real_store = git_source._PrivateClosureStore
    captured: tuple[tuple[object, ...], dict[str, object]] | None = None
    store = None
    snapshot = None
    caught: BaseException | None = None
    observed: tuple[object, ...] | None = None
    after_first_gc: tuple[object, ...] | None = None
    after_second_gc: tuple[object, ...] | None = None
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    owned_root: Path | None = None

    def fail_construction(*arguments, **keywords):
        nonlocal captured, descriptor_receipts, owned_root
        captured = (arguments, dict(keywords))
        descriptor_receipts, owned_root = _constructor_authority_receipts(
            real_store,
            arguments,
            keywords,
        )
        raise injected

    try:
        with monkeypatch.context() as construction_patch:
            construction_patch.setattr(
                git_source, "_PrivateClosureStore", fail_construction
            )
            try:
                snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc

        observed = _capture_gc_receipts(fixture)
        gc.collect()
        after_first_gc = _capture_gc_receipts(fixture)
        gc.collect()
        after_second_gc = _capture_gc_receipts(fixture)
        store = _constructor_store(real_store, captured)
    finally:
        if store is not None:
            _reviewer_cleanup_private_store(store)

    assert snapshot is None
    assert caught is injected
    assert len(descriptor_receipts) >= 5
    assert _descriptors_released(descriptor_receipts)
    assert owned_root is not None and not owned_root.exists()
    assert observed == baseline
    assert after_first_gc == observed
    assert after_second_gc == after_first_gc
    assert observed[2] == ()


def test_store_constructor_failure_aggregates_builder_abort_cleanup_failure(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: constructor primary is normalized or lost behind abort cleanup."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    primary = MemoryError("injected private store constructor failure")
    cleanup = GitAuthorityError("injected builder abort cleanup failure")
    real_store = git_source._PrivateClosureStore
    real_abort = git_source._PrivateClosureBuilder.abort
    captured: tuple[tuple[object, ...], dict[str, object]] | None = None
    store = None
    caught: BaseException | None = None

    def fail_construction(*arguments, **keywords):
        nonlocal captured
        captured = (arguments, dict(keywords))
        raise primary

    def abort_then_fail(self):
        real_abort(self)
        raise cleanup

    try:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(
                git_source, "_PrivateClosureStore", fail_construction
            )
            failure_patch.setattr(
                git_source._PrivateClosureBuilder, "abort", abort_then_fail
            )
            try:
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
        observed = _capture_gc_receipts(fixture)
        store = _constructor_store(real_store, captured)
    finally:
        if store is not None:
            _reviewer_cleanup_private_store(store)

    assert isinstance(caught, git_source.GitAuthorityAggregateError)
    assert caught.primary is primary
    assert isinstance(caught.cleanup, GitAuthorityError)
    assert "injected builder abort cleanup failure" in str(caught.cleanup)
    assert observed == baseline


def test_ownership_transfer_precommit_failure_keeps_builder_cleanup_authority(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: a pre-transition failure is cleaned as if the store owned it."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    injected = RuntimeError("injected pre-transfer failure")
    calls = 0
    observed_state: str | None = None
    snapshot = None
    caught: BaseException | None = None

    def fail_before_transfer(builder, store):
        nonlocal calls, observed_state
        calls += 1
        capsule = _private_store_capsule(store)
        assert any(value is capsule for value in vars(builder).values())
        observed_state = _ownership_state_name(capsule)
        assert observed_state == "BUILDER"
        raise injected

    with monkeypatch.context() as transfer_patch:
        transfer_patch.setattr(
            git_source,
            "_commit_private_closure_transfer",
            fail_before_transfer,
            raising=False,
        )
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc

    gc.collect()
    gc.collect()
    assert snapshot is None
    assert caught is injected
    assert calls == 1
    assert observed_state == "BUILDER"
    assert _capture_gc_receipts(fixture) == baseline


def test_ownership_transfer_postcommit_failure_uses_store_cleanup_once(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: a post-transition failure aborts the inactive builder or cleans twice."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    original = getattr(git_source, "_commit_private_closure_transfer", None)
    injected = RuntimeError("injected post-transfer failure")
    calls = 0
    observed_state: str | None = None
    store_ref = None
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    snapshot = None
    caught: BaseException | None = None

    def transfer_then_raise(builder, store):
        nonlocal calls, observed_state, store_ref, descriptor_receipts
        calls += 1
        descriptor_receipts = _private_store_descriptor_receipts(store)
        original(builder, store)
        store_ref = store
        observed_state = _ownership_state_name(_private_store_capsule(store))
        assert observed_state == "STORE"
        raise injected

    with monkeypatch.context() as transfer_patch:
        if original is not None:
            transfer_patch.setattr(
                git_source,
                "_commit_private_closure_transfer",
                transfer_then_raise,
            )
        try:
            snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        except BaseException as exc:
            caught = exc

    gc.collect()
    gc.collect()
    assert original is not None, "atomic ownership-transfer helper is missing"
    assert snapshot is None
    assert caught is injected
    assert calls == 1
    assert observed_state == "STORE"
    assert store_ref is not None
    assert _descriptors_released(descriptor_receipts)
    receipt_before_repeat = _capture_gc_receipts(fixture)
    store_ref.close()
    assert _capture_gc_receipts(fixture) == receipt_before_repeat == baseline


def test_ownership_transfer_rejects_invalid_store_capsule_before_transition(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: transfer accepts a store bound to a different capsule."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    real_store = git_source._PrivateClosureStore
    store_ref = None
    snapshot = None
    caught: BaseException | None = None

    def construct_with_invalid_binding(*arguments, **keywords):
        nonlocal store_ref
        store = real_store(*arguments, **keywords)
        store_ref = store
        capsule_names = [
            name
            for name in vars(store)
            if "ownership" in name or "capsule" in name
        ]
        assert len(capsule_names) == 1, (
            "private copied store has no singular ownership binding"
        )
        capsule_name = capsule_names[0]
        invalid_capsule = copy.copy(getattr(store, capsule_name))
        assert invalid_capsule is not getattr(store, capsule_name)
        object.__setattr__(store, capsule_name, invalid_capsule)
        return store

    try:
        with monkeypatch.context() as binding_patch:
            binding_patch.setattr(
                git_source,
                "_PrivateClosureStore",
                construct_with_invalid_binding,
            )
            try:
                snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
        observed = _capture_gc_receipts(fixture)
    finally:
        if store_ref is not None:
            _reviewer_cleanup_private_store(store_ref)

    assert snapshot is None
    assert isinstance(caught, GitAuthorityError)
    assert observed == baseline


def test_double_transfer_rejects_without_descriptor_duplication_or_cleanup(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: a second BUILDER-to-STORE transition duplicates or closes authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    original = getattr(git_source, "_commit_private_closure_transfer", None)
    calls = 0
    second_error: BaseException | None = None
    receipts_before: dict[int, tuple[int, int, int]] = {}
    receipts_after_first: dict[int, tuple[int, int, int]] = {}
    receipts_after_second: dict[int, tuple[int, int, int]] = {}
    store_ref = None

    def transfer_twice(builder, store):
        nonlocal calls, second_error, receipts_before, receipts_after_first
        nonlocal receipts_after_second, store_ref
        calls += 1
        store_ref = store
        receipts_before = _private_store_descriptor_receipts(store)
        result = original(builder, store)
        receipts_after_first = _private_store_descriptor_receipts(store)
        try:
            original(builder, store)
        except BaseException as exc:
            second_error = exc
        receipts_after_second = _private_store_descriptor_receipts(store)
        return result

    with monkeypatch.context() as transfer_patch:
        if original is not None:
            transfer_patch.setattr(
                git_source,
                "_commit_private_closure_transfer",
                transfer_twice,
            )
        snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)

    assert original is not None, "atomic ownership-transfer helper is missing"
    assert snapshot.blob("pin.md").data == expected
    assert calls == 1
    assert isinstance(second_error, GitAuthorityError)
    assert receipts_after_first == receipts_before
    assert receipts_after_second == receipts_before
    assert store_ref is not None
    assert _descriptors_released(receipts_before)
    repeated_close_receipt = _capture_gc_receipts(fixture)
    store_ref.close()
    assert _capture_gc_receipts(fixture) == repeated_close_receipt == baseline


def test_inactive_store_cannot_clean_builder_owned_authority(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: an inactive constructed store closes builder-owned descriptors."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    real_store = git_source._PrivateClosureStore
    store_ref = None
    inactive_errors: list[tuple[type[BaseException], str]] = []
    owned_receipts: dict[int, tuple[int, int, int]] = {}
    snapshot = None
    caught: BaseException | None = None

    def construct_and_reject_inactive_cleanup(*arguments, **keywords):
        nonlocal store_ref, owned_receipts
        store = real_store(*arguments, **keywords)
        store_ref = store
        owned_receipts = _private_store_descriptor_receipts(store)
        for _attempt in range(2):
            try:
                store.close()
            except BaseException as exc:
                inactive_errors.append((type(exc), str(exc)))
            _assert_store_receipts_unchanged(store, owned_receipts)
        capsule = _private_store_capsule(store)
        assert _ownership_state_name(capsule) == "BUILDER"
        return store

    try:
        with monkeypatch.context() as store_patch:
            store_patch.setattr(
                git_source,
                "_PrivateClosureStore",
                construct_and_reject_inactive_cleanup,
            )
            try:
                snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
        observed = _capture_gc_receipts(fixture)
    finally:
        if store_ref is not None:
            _reviewer_cleanup_private_store(store_ref)

    assert caught is None
    assert snapshot is not None and snapshot.blob("pin.md").data == expected
    assert len(inactive_errors) == 2
    assert len(set(inactive_errors)) == 1
    assert issubclass(inactive_errors[0][0], GitAuthorityError)
    assert observed == baseline


def test_ownership_transfer_static_order_constructs_then_commits_without_early_clear() -> None:
    """Break caught: seal clears builder authority before the atomic transition."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    module = ast.parse(Path(git_source.__file__).read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in module.body if isinstance(node, ast.ClassDef)
    }
    builder = classes["_PrivateClosureBuilder"]
    seal = next(
        node
        for node in builder.body
        if isinstance(node, ast.FunctionDef) and node.name == "seal"
    )

    def call_name(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    store_calls = [
        node
        for node in ast.walk(seal)
        if isinstance(node, ast.Call) and call_name(node) == "_PrivateClosureStore"
    ]
    transfer_calls = [
        node
        for node in ast.walk(seal)
        if isinstance(node, ast.Call)
        and call_name(node)
        in {"_commit_private_closure_transfer", "transfer_to_store"}
    ]
    assert len(store_calls) == 1
    assert len(transfer_calls) == 1, (
        "seal must contain exactly one atomic BUILDER-to-STORE transition"
    )
    construction_line = store_calls[0].lineno
    transfer_line = transfer_calls[0].lineno
    assert construction_line < transfer_line

    early_clears: list[str] = []
    for node in ast.walk(seal):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.lineno >= transfer_line:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        value = node.value
        if isinstance(value, ast.Dict):
            clearing_value = not value.keys
        elif isinstance(value, (ast.List, ast.Set, ast.Tuple)):
            clearing_value = not value.elts
        else:
            clearing_value = (
                isinstance(value, ast.Constant) and value.value in (None, True)
            )
        if not clearing_value:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                early_clears.append(target.attr)
    assert not early_clears, (
        "builder authority cleared before atomic transfer: "
        + ", ".join(sorted(set(early_clears)))
    )

    annotations = {
        statement.target.id: ast.unparse(statement.annotation)
        for statement in builder.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
    }
    independent_ownership_flags = {
        name: annotation
        for name, annotation in annotations.items()
        if annotation == "bool"
        and name in {"active", "owns", "owning", "sealed", "transferred"}
    }
    assert not independent_ownership_flags, (
        "builder ownership must derive from one capsule, not booleans: "
        f"{independent_ownership_flags!r}"
    )


def test_transition_boundary_store_new_memory_error_keeps_builder_owner_until_cleanup(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: allocation failure transfers or strands builder-owned authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    primary = MemoryError("injected private store __new__ allocation failure")
    real_store = git_source._PrivateClosureStore
    real_abort = git_source._PrivateClosureBuilder.abort
    captured: tuple[tuple[object, ...], dict[str, object]] | None = None
    reconstructed = None
    caught: BaseException | None = None
    states: list[tuple[str, str]] = []
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    owned_root: Path | None = None

    class AllocationFailingStore(real_store):
        def __new__(cls, *arguments, **keywords):
            nonlocal captured, descriptor_receipts, owned_root
            captured = (arguments, dict(keywords))
            descriptor_receipts, owned_root = _constructor_authority_receipts(
                real_store,
                arguments,
                keywords,
            )
            states.append(
                ("allocation", _ownership_state_name(keywords["ownership"]))
            )
            raise primary

    def observe_abort(self):
        states.append(("abort-before", _ownership_state_name(self.ownership)))
        result = real_abort(self)
        states.append(("abort-after", _ownership_state_name(self.ownership)))
        return result

    try:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(
                git_source,
                "_PrivateClosureStore",
                AllocationFailingStore,
            )
            failure_patch.setattr(
                git_source._PrivateClosureBuilder,
                "abort",
                observe_abort,
            )
            try:
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
        reconstructed = _constructor_store(real_store, captured)
    finally:
        if reconstructed is not None:
            _reviewer_cleanup_private_store(reconstructed)

    assert caught is primary
    assert states == [
        ("allocation", "BUILDER"),
        ("abort-before", "BUILDER"),
        ("abort-after", "RELEASED"),
    ]
    assert len(descriptor_receipts) >= 5
    assert _descriptors_released(descriptor_receipts)
    assert owned_root is not None and not owned_root.exists()
    assert _capture_gc_receipts(fixture) == baseline


def test_transition_boundary_partial_store_init_failure_keeps_builder_owner_until_cleanup(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: partial non-owning initialization steals builder cleanup authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    primary = RuntimeError("injected partial private store __init__ failure")
    real_store = git_source._PrivateClosureStore
    real_abort = git_source._PrivateClosureBuilder.abort
    captured: tuple[tuple[object, ...], dict[str, object]] | None = None
    partial_store = None
    reconstructed = None
    caught: BaseException | None = None
    states: list[tuple[str, str]] = []
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    owned_root: Path | None = None

    class PartiallyInitializedStore(real_store):
        def __init__(self, *arguments, **keywords) -> None:
            nonlocal captured, partial_store, descriptor_receipts, owned_root
            captured = (arguments, dict(keywords))
            partial_store = self
            bound = inspect.signature(real_store).bind(*arguments, **keywords)
            self._partial_root_fd = bound.arguments["root_fd"]
            self._partial_entry_count = len(bound.arguments["entries"])
            descriptor_receipts, owned_root = _constructor_authority_receipts(
                real_store,
                arguments,
                keywords,
            )
            states.append(
                ("partial-init", _ownership_state_name(keywords["ownership"]))
            )
            raise primary

    def observe_abort(self):
        states.append(("abort-before", _ownership_state_name(self.ownership)))
        result = real_abort(self)
        states.append(("abort-after", _ownership_state_name(self.ownership)))
        return result

    try:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(
                git_source,
                "_PrivateClosureStore",
                PartiallyInitializedStore,
            )
            failure_patch.setattr(
                git_source._PrivateClosureBuilder,
                "abort",
                observe_abort,
            )
            try:
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
        reconstructed = _constructor_store(real_store, captured)
    finally:
        if reconstructed is not None:
            _reviewer_cleanup_private_store(reconstructed)

    assert caught is primary
    assert partial_store is not None
    assert partial_store._partial_root_fd in descriptor_receipts
    assert partial_store._partial_entry_count > 0
    assert "_ownership" not in vars(partial_store)
    assert states == [
        ("partial-init", "BUILDER"),
        ("abort-before", "BUILDER"),
        ("abort-after", "RELEASED"),
    ]
    assert len(descriptor_receipts) >= 5
    assert _descriptors_released(descriptor_receipts)
    assert owned_root is not None and not owned_root.exists()
    assert _capture_gc_receipts(fixture) == baseline


def test_transition_boundary_constructed_store_verification_failure_keeps_builder_owner(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: final store verification failure cleans through the inactive store."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    primary = RuntimeError("injected constructed-store verification failure")
    real_abort = git_source._PrivateClosureBuilder.abort
    store_ref = None
    caught: BaseException | None = None
    states: list[tuple[str, str]] = []
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    owned_root: Path | None = None

    def fail_verification(self, store, ownership, **_keywords) -> None:
        nonlocal store_ref, descriptor_receipts, owned_root
        store_ref = store
        descriptor_receipts = _private_store_descriptor_receipts(store)
        owned_root = _private_store_root(store)
        states.append(("verification", _ownership_state_name(ownership)))
        raise primary

    def observe_abort(self):
        states.append(("abort-before", _ownership_state_name(self.ownership)))
        result = real_abort(self)
        states.append(("abort-after", _ownership_state_name(self.ownership)))
        return result

    try:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(
                git_source._PrivateClosureBuilder,
                "_verify_constructed_store",
                fail_verification,
            )
            failure_patch.setattr(
                git_source._PrivateClosureBuilder,
                "abort",
                observe_abort,
            )
            try:
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
    finally:
        if store_ref is not None:
            _reviewer_cleanup_private_store(store_ref)

    assert caught is primary
    assert states == [
        ("verification", "BUILDER"),
        ("abort-before", "BUILDER"),
        ("abort-after", "RELEASED"),
    ]
    assert len(descriptor_receipts) >= 5
    assert _descriptors_released(descriptor_receipts)
    assert owned_root is not None and not owned_root.exists()
    assert _capture_gc_receipts(fixture) == baseline


def test_transition_boundary_posttransfer_failure_releases_only_after_confirmation(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: store authority becomes RELEASED before cleanup is confirmed."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    primary = RuntimeError("injected immediate post-transfer failure")
    real_transfer = git_source._commit_private_closure_transfer
    real_mark_released = git_source._PrivateClosureOwnership.mark_released
    store_ref = None
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    owned_root: Path | None = None
    caught: BaseException | None = None
    release_observations: list[tuple[object, ...]] = []

    def transfer_then_raise(builder, store):
        nonlocal store_ref, descriptor_receipts, owned_root
        descriptor_receipts = _private_store_descriptor_receipts(store)
        owned_root = _private_store_root(store)
        real_transfer(builder, store)
        store_ref = store
        assert _ownership_state_name(store.ownership) == "STORE"
        raise primary

    def observe_release(self) -> None:
        release_observations.append(
            (
                "before",
                _ownership_state_name(self),
                self.cleanup_started,
                self.cleanup_completed,
                len(self.restored_source_receipts) == len(self.source_nlinks),
                _descriptors_released(descriptor_receipts),
                owned_root is not None and owned_root.exists(),
            )
        )
        real_mark_released(self)
        release_observations.append(("after", _ownership_state_name(self)))

    try:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(
                git_source,
                "_commit_private_closure_transfer",
                transfer_then_raise,
            )
            failure_patch.setattr(
                git_source._PrivateClosureOwnership,
                "mark_released",
                observe_release,
            )
            try:
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
    finally:
        if store_ref is not None:
            _reviewer_cleanup_private_store(store_ref)

    assert caught is primary
    assert release_observations == [
        ("before", "STORE", True, True, True, True, False),
        ("after", "RELEASED"),
    ]
    assert _capture_gc_receipts(fixture) == baseline


def test_transition_boundary_posttransfer_primary_and_cleanup_failure_are_not_nested(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: outer cleanup replaces the post-transfer primary/cleanup pair."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    primary = RuntimeError("injected post-transfer primary failure")
    cleanup = GitAuthorityError("injected store cleanup confirmation failure")
    real_transfer = git_source._commit_private_closure_transfer
    real_cleanup_root = git_source._cleanup_empty_owned_temporary_root
    store_ref = None
    caught: BaseException | None = None
    cleanup_injections = 0
    observed_state: str | None = None

    def transfer_then_raise(builder, store):
        nonlocal store_ref
        real_transfer(builder, store)
        store_ref = store
        raise primary

    def cleanup_root_then_fail(owner, *, label):
        nonlocal cleanup_injections
        result = real_cleanup_root(owner, label=label)
        if store_ref is not None and owner is store_ref.owner:
            cleanup_injections += 1
            raise cleanup
        return result

    try:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(
                git_source,
                "_commit_private_closure_transfer",
                transfer_then_raise,
            )
            failure_patch.setattr(
                git_source,
                "_cleanup_empty_owned_temporary_root",
                cleanup_root_then_fail,
            )
            try:
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
        if store_ref is not None:
            observed_state = _ownership_state_name(store_ref.ownership)
    finally:
        if store_ref is not None:
            _reviewer_cleanup_private_store(store_ref)

    assert cleanup_injections == 1
    assert isinstance(caught, git_source.GitAuthorityAggregateError)
    assert caught.primary is primary
    assert caught.cleanup is cleanup
    assert observed_state == "STORE"
    assert _capture_gc_receipts(fixture) == baseline


@pytest.mark.parametrize(
    "injected",
    (
        KeyboardInterrupt("injected transition KeyboardInterrupt"),
        SystemExit("injected transition SystemExit"),
        _InjectedTransitionBaseException(
            "injected transition custom BaseException"
        ),
    ),
    ids=("keyboard-interrupt", "system-exit", "custom-base-exception"),
)
def test_transition_boundary_signal_baseexception_is_reraised_after_store_cleanup(
    packed_git_fixture,
    monkeypatch,
    injected: BaseException,
) -> None:
    """Break caught: signal-style transition faults are normalized or skip cleanup."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    baseline = _capture_gc_receipts(fixture)
    real_transfer = git_source._commit_private_closure_transfer
    store_ref = None
    descriptor_receipts: dict[int, tuple[int, int, int]] = {}
    caught: BaseException | None = None

    def transfer_then_raise(builder, store):
        nonlocal store_ref, descriptor_receipts
        descriptor_receipts = _private_store_descriptor_receipts(store)
        real_transfer(builder, store)
        store_ref = store
        raise injected

    try:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(
                git_source,
                "_commit_private_closure_transfer",
                transfer_then_raise,
            )
            try:
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            except BaseException as exc:
                caught = exc
    finally:
        if store_ref is not None:
            _reviewer_cleanup_private_store(store_ref)

    assert caught is injected
    assert store_ref is not None
    assert _ownership_state_name(store_ref.ownership) == "RELEASED"
    assert _descriptors_released(descriptor_receipts)
    assert _capture_gc_receipts(fixture) == baseline


def test_transition_boundary_static_capsule_has_no_none_or_preconstruction_clear() -> None:
    """Break caught: capsule ownership enters NONE or clears authority pre-construction."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    module = ast.parse(Path(git_source.__file__).read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in module.body if isinstance(node, ast.ClassDef)
    }
    state_enum = classes["_PrivateClosureOwnershipState"]
    enum_members = [
        target.id
        for statement in state_enum.body
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name)
    ]
    assert enum_members == ["BUILDER", "STORE", "RELEASED"]

    ownership = classes["_PrivateClosureOwnership"]
    state_annotation = next(
        statement
        for statement in ownership.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == "state"
    )
    assert ast.unparse(state_annotation.annotation) == "_PrivateClosureOwnershipState"

    none_writes: list[str] = []
    state_writes: list[str] = []
    for node in ast.walk(module):
        targets: tuple[ast.expr, ...] = ()
        value = None
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = (node.target,)
            value = node.value
        if value is not None:
            for target in targets:
                target_name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                if (
                    target_name in {"ownership", "state"}
                    and isinstance(value, ast.Constant)
                    and value.value is None
                ):
                    none_writes.append(f"{target_name} at line {node.lineno}")
                if isinstance(target, ast.Attribute) and target.attr == "state":
                    state_writes.append(ast.unparse(value))
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if (
                    keyword.arg in {"ownership", "state"}
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is None
                ):
                    none_writes.append(
                        f"{keyword.arg} keyword at line {node.lineno}"
                    )
                if keyword.arg == "state":
                    state_writes.append(ast.unparse(keyword.value))
    assert not none_writes, "ownership/state None writes: " + "; ".join(none_writes)
    assert set(state_writes) == {
        "_PrivateClosureOwnershipState.BUILDER",
        "_PrivateClosureOwnershipState.STORE",
        "_PrivateClosureOwnershipState.RELEASED",
    }

    builder = classes["_PrivateClosureBuilder"]
    seal = next(
        node
        for node in builder.body
        if isinstance(node, ast.FunctionDef) and node.name == "seal"
    )
    construction_line = next(
        node.lineno
        for node in ast.walk(seal)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_PrivateClosureStore"
    )
    preconstruction_clears: list[str] = []
    protected_fields = {"objects", "pack", "prefixes", "ownership"}
    for node in ast.walk(seal):
        if getattr(node, "lineno", construction_line) >= construction_line:
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            value = node.value
            clearing = (
                isinstance(value, ast.Constant) and value.value is None
            ) or (
                isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple))
                and not (value.keys if isinstance(value, ast.Dict) else value.elts)
            )
            if clearing:
                preconstruction_clears.extend(
                    target.attr
                    for target in targets
                    if isinstance(target, ast.Attribute)
                    and target.attr in protected_fields
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "clear"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in protected_fields
        ):
            preconstruction_clears.append(node.func.value.attr)
    assert not preconstruction_clears, (
        "builder authority cleared before store construction: "
        + ", ".join(sorted(set(preconstruction_clears)))
    )


def test_ownership_transfer_stress_repeated_constructor_failures_reject_without_leak(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated constructor faults eventually strand builder authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    iterations = 50
    baseline = _capture_gc_receipts(fixture)
    real_store = git_source._PrivateClosureStore
    campaign_receipts: list[tuple[object, ...]] = []

    for iteration in range(iterations):
        primary = MemoryError(
            f"injected OT4 constructor failure iteration {iteration}"
        )
        captured: tuple[tuple[object, ...], dict[str, object]] | None = None
        ownership_ref = None
        descriptor_receipts: dict[int, tuple[int, int, int]] = {}
        owned_root: Path | None = None
        snapshot = None
        caught: BaseException | None = None

        def fail_construction(*arguments, **keywords):
            nonlocal captured, ownership_ref, descriptor_receipts, owned_root
            captured = (arguments, dict(keywords))
            ownership_ref = keywords["ownership"]
            assert _ownership_state_name(ownership_ref) == "BUILDER"
            descriptor_receipts, owned_root = _constructor_authority_receipts(
                real_store,
                arguments,
                keywords,
            )
            raise primary

        try:
            with monkeypatch.context() as construction_patch:
                construction_patch.setattr(
                    git_source,
                    "_PrivateClosureStore",
                    fail_construction,
                )
                try:
                    snapshot = GitTreeSnapshot.from_commit(
                        fixture.root,
                        commit_oid,
                    )
                except BaseException as exc:
                    caught = exc

            assert snapshot is None
            assert caught is primary
            assert captured is not None
            assert ownership_ref is not None
            assert _ownership_state_name(ownership_ref) == "RELEASED"
            clean = _assert_ot4_iteration_clean(
                fixture,
                baseline=baseline,
                descriptor_receipts=descriptor_receipts,
                owned_root=owned_root,
            )
            campaign_receipts.append(
                (
                    iteration,
                    type(caught).__name__,
                    "BUILDER",
                    _ownership_state_name(ownership_ref),
                    *clean[:3],
                )
            )
        finally:
            if captured is not None and (
                (owned_root is not None and owned_root.exists())
                or not _descriptors_released(descriptor_receipts)
            ):
                fallback_store = _constructor_store(real_store, captured)
                if fallback_store is not None:
                    _reviewer_cleanup_private_store(fallback_store)

    observed = _capture_gc_receipts(fixture)
    assert len(campaign_receipts) == iterations
    assert observed == baseline
    _emit_ot4_stress_receipt(
        label="constructor-failures",
        iterations=iterations,
        baseline=baseline,
        observed=observed,
        iteration_receipts=tuple(campaign_receipts),
    )


def test_ownership_transfer_stress_repeated_pretransfer_failures_reject_without_leak(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated pre-transfer faults eventually clean as STORE."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    iterations = 20
    baseline = _capture_gc_receipts(fixture)
    campaign_receipts: list[tuple[object, ...]] = []

    for iteration in range(iterations):
        primary = RuntimeError(
            f"injected OT4 pre-transfer failure iteration {iteration}"
        )
        store_ref = None
        ownership_ref = None
        descriptor_receipts: dict[int, tuple[int, int, int]] = {}
        owned_root: Path | None = None
        states: list[str] = []
        snapshot = None
        caught: BaseException | None = None

        def fail_before_transfer(builder, store):
            nonlocal store_ref, ownership_ref, descriptor_receipts, owned_root
            store_ref = store
            ownership_ref = _private_store_capsule(store)
            assert any(value is ownership_ref for value in vars(builder).values())
            states.append(_ownership_state_name(ownership_ref))
            descriptor_receipts = _private_store_descriptor_receipts(store)
            owned_root = _private_store_root(store)
            raise primary

        try:
            with monkeypatch.context() as transfer_patch:
                transfer_patch.setattr(
                    git_source,
                    "_commit_private_closure_transfer",
                    fail_before_transfer,
                )
                try:
                    snapshot = GitTreeSnapshot.from_commit(
                        fixture.root,
                        commit_oid,
                    )
                except BaseException as exc:
                    caught = exc

            assert snapshot is None
            assert caught is primary
            assert states == ["BUILDER"]
            assert ownership_ref is not None
            assert _ownership_state_name(ownership_ref) == "RELEASED"
            clean = _assert_ot4_iteration_clean(
                fixture,
                baseline=baseline,
                descriptor_receipts=descriptor_receipts,
                owned_root=owned_root,
            )
            campaign_receipts.append(
                (
                    iteration,
                    type(caught).__name__,
                    states[0],
                    _ownership_state_name(ownership_ref),
                    *clean[:3],
                )
            )
        finally:
            if store_ref is not None and (
                (owned_root is not None and owned_root.exists())
                or not _descriptors_released(descriptor_receipts)
            ):
                _reviewer_cleanup_private_store(store_ref)

    observed = _capture_gc_receipts(fixture)
    assert len(campaign_receipts) == iterations
    assert observed == baseline
    _emit_ot4_stress_receipt(
        label="pre-transfer-failures",
        iterations=iterations,
        baseline=baseline,
        observed=observed,
        iteration_receipts=tuple(campaign_receipts),
    )


def test_ownership_transfer_stress_repeated_posttransfer_failures_reject_without_leak(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated post-transfer faults eventually leak STORE authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    iterations = 20
    baseline = _capture_gc_receipts(fixture)
    real_transfer = git_source._commit_private_closure_transfer
    campaign_receipts: list[tuple[object, ...]] = []

    for iteration in range(iterations):
        primary = RuntimeError(
            f"injected OT4 post-transfer failure iteration {iteration}"
        )
        store_ref = None
        descriptor_receipts: dict[int, tuple[int, int, int]] = {}
        owned_root: Path | None = None
        states: list[str] = []
        snapshot = None
        caught: BaseException | None = None

        def transfer_then_fail(builder, store):
            nonlocal store_ref, descriptor_receipts, owned_root
            descriptor_receipts = _private_store_descriptor_receipts(store)
            owned_root = _private_store_root(store)
            real_transfer(builder, store)
            store_ref = store
            states.append(_ownership_state_name(_private_store_capsule(store)))
            raise primary

        try:
            with monkeypatch.context() as transfer_patch:
                transfer_patch.setattr(
                    git_source,
                    "_commit_private_closure_transfer",
                    transfer_then_fail,
                )
                try:
                    snapshot = GitTreeSnapshot.from_commit(
                        fixture.root,
                        commit_oid,
                    )
                except BaseException as exc:
                    caught = exc

            assert snapshot is None
            assert caught is primary
            assert states == ["STORE"]
            assert store_ref is not None
            assert _ownership_state_name(_private_store_capsule(store_ref)) == "RELEASED"
            clean = _assert_ot4_iteration_clean(
                fixture,
                baseline=baseline,
                descriptor_receipts=descriptor_receipts,
                owned_root=owned_root,
            )
            campaign_receipts.append(
                (
                    iteration,
                    type(caught).__name__,
                    states[0],
                    _ownership_state_name(_private_store_capsule(store_ref)),
                    *clean[:3],
                )
            )
        finally:
            if store_ref is not None and (
                (owned_root is not None and owned_root.exists())
                or not _descriptors_released(descriptor_receipts)
            ):
                _reviewer_cleanup_private_store(store_ref)

    observed = _capture_gc_receipts(fixture)
    assert len(campaign_receipts) == iterations
    assert observed == baseline
    _emit_ot4_stress_receipt(
        label="post-transfer-failures",
        iterations=iterations,
        baseline=baseline,
        observed=observed,
        iteration_receipts=tuple(campaign_receipts),
    )


def test_ownership_transfer_stress_repeated_invalid_bindings_reject_without_leak(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated invalid capsule bindings eventually gain authority."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    iterations = 20
    baseline = _capture_gc_receipts(fixture)
    real_store = git_source._PrivateClosureStore
    campaign_receipts: list[tuple[object, ...]] = []

    for iteration in range(iterations):
        store_ref = None
        builder_capsule = None
        invalid_capsule = None
        descriptor_receipts: dict[int, tuple[int, int, int]] = {}
        owned_root: Path | None = None
        snapshot = None
        caught: BaseException | None = None

        def construct_with_invalid_binding(*arguments, **keywords):
            nonlocal store_ref, builder_capsule, invalid_capsule
            nonlocal descriptor_receipts, owned_root
            store = real_store(*arguments, **keywords)
            store_ref = store
            builder_capsule = keywords["ownership"]
            assert _ownership_state_name(builder_capsule) == "BUILDER"
            descriptor_receipts = _private_store_descriptor_receipts(store)
            owned_root = _private_store_root(store)
            capsule_names = [
                name
                for name in vars(store)
                if "ownership" in name or "capsule" in name
            ]
            assert capsule_names == ["_ownership"]
            invalid_capsule = copy.copy(builder_capsule)
            assert invalid_capsule is not builder_capsule
            object.__setattr__(store, "_ownership", invalid_capsule)
            return store

        try:
            with monkeypatch.context() as binding_patch:
                binding_patch.setattr(
                    git_source,
                    "_PrivateClosureStore",
                    construct_with_invalid_binding,
                )
                try:
                    snapshot = GitTreeSnapshot.from_commit(
                        fixture.root,
                        commit_oid,
                    )
                except BaseException as exc:
                    caught = exc

            assert snapshot is None
            assert isinstance(caught, GitAuthorityError)
            assert "ownership transfer binding is invalid" in str(caught)
            assert builder_capsule is not None
            assert invalid_capsule is not None
            assert _ownership_state_name(builder_capsule) == "RELEASED"
            assert _ownership_state_name(invalid_capsule) == "BUILDER"
            clean = _assert_ot4_iteration_clean(
                fixture,
                baseline=baseline,
                descriptor_receipts=descriptor_receipts,
                owned_root=owned_root,
            )
            campaign_receipts.append(
                (
                    iteration,
                    type(caught).__name__,
                    _ownership_state_name(builder_capsule),
                    _ownership_state_name(invalid_capsule),
                    *clean[:3],
                )
            )
        finally:
            if store_ref is not None and (
                (owned_root is not None and owned_root.exists())
                or not _descriptors_released(descriptor_receipts)
            ):
                _reviewer_cleanup_private_store(store_ref)

    observed = _capture_gc_receipts(fixture)
    assert len(campaign_receipts) == iterations
    assert observed == baseline
    _emit_ot4_stress_receipt(
        label="invalid-capsule-bindings",
        iterations=iterations,
        baseline=baseline,
        observed=observed,
        iteration_receipts=tuple(campaign_receipts),
    )


def test_ownership_transfer_stress_repeated_successes_transfer_and_cleanup_once(
    packed_git_fixture,
    monkeypatch,
) -> None:
    """Break caught: repeated successes skip, repeat, or incompletely clean transfer."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    iterations = 50
    tree_oid = fixture._git(
        "rev-parse", f"{commit_oid}^{{tree}}"
    ).decode("ascii").strip()
    blob_oid = fixture._git(
        "rev-parse", f"{commit_oid}:pin.md"
    ).decode("ascii").strip()
    expected_sha256 = hashlib.sha256(expected).hexdigest()
    baseline = _capture_gc_receipts(fixture)
    real_transfer = git_source._commit_private_closure_transfer
    real_cleanup = git_source._cleanup_private_closure_ownership
    campaign_receipts: list[tuple[object, ...]] = []

    for iteration in range(iterations):
        builder_ref = None
        store_ref = None
        descriptor_receipts: dict[int, tuple[int, int, int]] = {}
        owned_root: Path | None = None
        transfer_states: list[tuple[str, str]] = []
        cleanup_states: list[tuple[str, str]] = []

        def observe_transfer(builder, store):
            nonlocal builder_ref, store_ref, descriptor_receipts, owned_root
            builder_ref = builder
            store_ref = store
            descriptor_receipts = _private_store_descriptor_receipts(store)
            owned_root = _private_store_root(store)
            before = _ownership_state_name(_private_store_capsule(store))
            real_transfer(builder, store)
            after = _ownership_state_name(_private_store_capsule(store))
            transfer_states.append((before, after))

        def observe_cleanup(ownership, **keywords):
            before = _ownership_state_name(ownership)
            result = real_cleanup(ownership, **keywords)
            after = _ownership_state_name(ownership)
            cleanup_states.append((before, after))
            return result

        try:
            with monkeypatch.context() as ownership_patch:
                ownership_patch.setattr(
                    git_source,
                    "_commit_private_closure_transfer",
                    observe_transfer,
                )
                ownership_patch.setattr(
                    git_source,
                    "_cleanup_private_closure_ownership",
                    observe_cleanup,
                )
                snapshot = GitTreeSnapshot.from_commit(
                    fixture.root,
                    commit_oid,
                )

            assert snapshot.commit_oid == commit_oid
            assert snapshot.tree_oid == tree_oid
            assert tuple(blob.path for blob in snapshot.blobs) == ("pin.md",)
            assert snapshot.blob("pin.md").blob_oid == blob_oid
            assert snapshot.blob("pin.md").data == expected
            assert snapshot.blob("pin.md").sha256 == expected_sha256
            assert builder_ref is not None
            assert store_ref is not None
            assert builder_ref.ownership is _private_store_capsule(store_ref)
            assert transfer_states == [("BUILDER", "STORE")]
            assert cleanup_states == [("STORE", "RELEASED")]
            assert _ownership_state_name(_private_store_capsule(store_ref)) == "RELEASED"
            clean = _assert_ot4_iteration_clean(
                fixture,
                baseline=baseline,
                descriptor_receipts=descriptor_receipts,
                owned_root=owned_root,
            )
            campaign_receipts.append(
                (
                    iteration,
                    snapshot.commit_oid,
                    snapshot.tree_oid,
                    snapshot.blob("pin.md").blob_oid,
                    snapshot.blob("pin.md").sha256,
                    transfer_states[0],
                    cleanup_states[0],
                    *clean[:3],
                )
            )
        finally:
            if store_ref is not None and (
                (owned_root is not None and owned_root.exists())
                or not _descriptors_released(descriptor_receipts)
            ):
                _reviewer_cleanup_private_store(store_ref)

    observed = _capture_gc_receipts(fixture)
    assert len(campaign_receipts) == iterations
    assert observed == baseline
    _emit_ot4_stress_receipt(
        label="successful-ownership-transfers",
        iterations=iterations,
        baseline=baseline,
        observed=observed,
        iteration_receipts=tuple(campaign_receipts),
    )


def test_ownership_transfer_stress_static_architecture_has_one_guarded_owner() -> None:
    """Break caught: ownership splits, transitions in construction, or cleans unguarded."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    module = ast.parse(Path(git_source.__file__).read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in module.body if isinstance(node, ast.ClassDef)
    }
    builder = classes["_PrivateClosureBuilder"]
    store = classes["_PrivateClosureStore"]

    def method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef:
        return next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def call_name(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    seal = method(builder, "seal")
    store_init = method(store, "__init__")
    construction_line = next(
        node.lineno
        for node in ast.walk(seal)
        if isinstance(node, ast.Call) and call_name(node) == "_PrivateClosureStore"
    )
    preconstruction_clears: list[str] = []
    for node in ast.walk(seal):
        if getattr(node, "lineno", construction_line) >= construction_line:
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            value = node.value
            clearing = (
                isinstance(value, ast.Constant) and value.value is None
            ) or (
                isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple))
                and not (value.keys if isinstance(value, ast.Dict) else value.elts)
            )
            if clearing:
                preconstruction_clears.extend(
                    target.attr
                    for target in targets
                    if isinstance(target, ast.Attribute)
                    and target.attr in {"ownership", "objects", "pack", "prefixes"}
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "clear"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in {"ownership", "objects", "pack", "prefixes"}
        ):
            preconstruction_clears.append(node.func.value.attr)
    assert not preconstruction_clears

    builder_fields = {
        node.target.id
        for node in builder.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert builder_fields == {"ownership"}
    sealed_writes = [
        node.lineno
        for node in ast.walk(builder)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Attribute) and target.attr == "sealed"
    ]
    assert not sealed_writes

    constructor_self_writes = {
        target.attr
        for node in ast.walk(store_init)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    assert constructor_self_writes == {"_ownership"}
    constructor_calls = {
        call_name(node)
        for node in ast.walk(store_init)
        if isinstance(node, ast.Call)
    }
    assert constructor_calls.isdisjoint(
        {
            "dup",
            "dup2",
            "fcntl",
            "transfer_to_store",
            "mark_released",
            "_commit_private_closure_transfer",
            "_cleanup_private_closure_ownership",
        }
    )
    constructor_state_writes = [
        node.lineno
        for node in ast.walk(store_init)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Attribute) and target.attr == "state"
    ]
    assert not constructor_state_writes

    for cleanup_method in (method(builder, "abort"), method(store, "close")):
        cleanup_line = next(
            node.lineno
            for node in ast.walk(cleanup_method)
            if isinstance(node, ast.Call)
            and call_name(node) == "_cleanup_private_closure_ownership"
        )
        require_lines = [
            node.lineno
            for node in ast.walk(cleanup_method)
            if isinstance(node, ast.Call) and call_name(node) == "require"
        ]
        state_check_lines = [
            node.lineno
            for node in ast.walk(cleanup_method)
            if isinstance(node, ast.Compare)
            and "_PrivateClosureOwnershipState" in ast.unparse(node)
        ]
        assert require_lines and min(require_lines) < cleanup_line
        assert state_check_lines and min(state_check_lines) < cleanup_line


# T2 RED recovery batch A.  The fixtures below are intentionally disposable:
# pack construction is confined to pytest's private temporary repository and
# the assertions observe subprocess/lifecycle behaviour at the real Git-tree
# boundary, never a source-symbol substitute.
def _t2_delta_packed_fixture(
    tmp_path: Path,
    object_format: str,
) -> tuple[GitFixture, str, dict[str, bytes]]:
    fixture = GitFixture(tmp_path / f"t2-delta-{object_format}", object_format=object_format)
    payloads: dict[str, bytes] = {}
    shared = b"T2 delta-compatible payload\n" * 4096
    for revision in range(18):
        data = shared + f"revision={revision:02d}\n".encode("ascii") + bytes([65 + revision]) * 257
        commit_oid, _ = fixture.commit_file(f"nested/{revision:02d}.txt", data)
        blob_oid = fixture._git("rev-parse", f"{commit_oid}:nested/{revision:02d}.txt").decode("ascii").strip()
        payloads[blob_oid] = data
    fixture._git("repack", "-adf", "--depth=50", "--window=50")
    fixture._git("prune-packed")
    pack_indexes = tuple((fixture.root / ".git/objects/pack").glob("*.idx"))
    assert pack_indexes
    verification = fixture._git("verify-pack", "-v", str(pack_indexes[0])).decode("ascii")
    delta_oids = {
        fields[0]
        for line in verification.splitlines()
        for fields in (line.split(),)
        if len(fields) >= 7 and fields[0] in payloads and fields[1] == "blob" and int(fields[5]) >= 1
    }
    assert delta_oids, "private fixture did not construct a real delta-compressed blob"
    return fixture, commit_oid, payloads


def _t2_record_batch_launches(monkeypatch):
    import scripts.nautilus_pin_inventory.git_source as git_source

    launches: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(arguments, *args, **kwargs):
        process = real_popen(arguments, *args, **kwargs)
        command = tuple(str(argument) for argument in arguments)
        if (
            command[-2:] == ("cat-file", "--batch")
            and kwargs.get("stdin") is subprocess.PIPE
        ):
            launches.append(process)
        return process

    monkeypatch.setattr(git_source.subprocess, "Popen", recording_popen)
    return launches


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_01_persistent_reuse_real_delta_packs(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.1: one live batch child must serve a real delta-packed closure."""
    fixture, commit_oid, payloads = _t2_delta_packed_fixture(tmp_path, object_format)
    launches = _t2_record_batch_launches(monkeypatch)

    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)

    assert snapshot.commit_oid == commit_oid
    assert snapshot.object_format == object_format
    observed = {blob.blob_oid: blob for blob in snapshot.blobs}
    assert set(payloads).issubset(observed)
    for oid, expected in payloads.items():
        assert observed[oid].data == expected
        assert observed[oid].sha256 == hashlib.sha256(expected).hexdigest()
    assert len(launches) == 1
    assert launches[0].poll() is not None


def test_t2_group_02_exact_accepted_source_finishes_inside_seal(monkeypatch) -> None:
    """T2.2: exact accepted source has a bounded batch-child receipt."""
    accepted = "f007624191077edd0ba01e42b421e8bff12cbbf0"
    launches = _t2_record_batch_launches(monkeypatch)
    started = time.monotonic()
    snapshot = GitTreeSnapshot.from_commit(Path("."), accepted)
    elapsed = time.monotonic() - started

    assert snapshot.commit_oid == accepted
    assert len(snapshot.blobs) == 1384
    assert elapsed <= 30.0
    # The accepted source spans many pre-existing immutable pack pairs.  The
    # receipt is bounded per selected bootstrap, not per source blob; 128 is a
    # sealed source-specific ceiling well below its 1,384 blobs.
    assert 1 <= len(launches) <= 128
    assert all(process.poll() is not None for process in launches)


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_03_multi_pack_switching_reaps_reader_and_preserves_receipts(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.3: closure use of multiple start-time packs never overlaps readers."""
    fixture = GitFixture(tmp_path / f"t2-multi-pack-{object_format}", object_format=object_format)
    first_payloads = {
        f"first/{index:02d}.txt": f"first pack authority {index}\n".encode("ascii")
        for index in range(9)
    }
    for path, data in first_payloads.items():
        leaf = fixture.root / path
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_bytes(data)
    fixture._git("add", ".")
    fixture._git("commit", "-qm", "first pack")
    first_commit = fixture._git("rev-parse", "HEAD").decode("ascii").strip()
    fixture._git("repack", "-adf")
    second_payloads = {
        f"second/{index:02d}.txt": f"second pack authority {index}\n".encode("ascii")
        for index in range(9)
    }
    for path, data in second_payloads.items():
        leaf = fixture.root / path
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_bytes(data)
    fixture._git("add", ".")
    fixture._git("commit", "-qm", "second pack")
    second_commit = fixture._git("rev-parse", "HEAD").decode("ascii").strip()
    fixture._git("repack", "-f")
    pack_directory = fixture.root / ".git/objects/pack"
    pairs = tuple(sorted(pack_directory.glob("*.pack")))
    assert len(pairs) >= 2, "private fixture did not preserve multiple operation-start packs"
    baseline_fds = _stable_descriptor_classes()
    baseline_nlinks = _packed_source_nlinks(fixture)
    launches = _t2_record_batch_launches(monkeypatch)

    prior_launches = 0
    for _iteration in range(3):
        snapshot = GitTreeSnapshot.from_commit(fixture.root, second_commit)
        for path, data in {**first_payloads, **second_payloads}.items():
            assert snapshot.blob(path).data == data
        assert all(process.poll() is not None for process in launches[:-1])
        assert _stable_descriptor_classes() == baseline_fds
        assert _packed_source_nlinks(fixture) == baseline_nlinks
        assert len(launches) - prior_launches <= 2
        prior_launches = len(launches)
    assert 1 <= len(launches) <= 6
    assert all(process.poll() is not None for process in launches)
    assert first_commit != second_commit


@pytest.mark.parametrize(
    "label,header,body",
    (
        ("wrong-oid", b"0" * 40 + b" blob 1\n", b"x\n"),
        ("wrong-type", b"{oid} tree 1\n", b"x\n"),
        ("missing", b"{oid} missing\n", b""),
        ("short-header", b"{oid} blob", b""),
        ("overlong-header", b"x" * 513 + b"\n", b""),
        ("signed-size", b"{oid} blob -1\n", b""),
        ("noncanonical-size", b"{oid} blob 01\n", b"x\n"),
        ("huge-size", b"{oid} blob 2000001\n", b""),
        ("truncated-body", b"{oid} blob 2\n", b"x"),
        ("missing-delimiter", b"{oid} blob 1\n", b"x"),
        ("extra-same-response", b"{oid} blob 1\n", b"x\nextra"),
        ("delayed-extra", b"{oid} blob 1\n", b"x\nextra\n"),
        ("trailing-eof", b"{oid} blob 1\n", b"x\ntrailing"),
    ),
)
def test_t2_group_04_protocol_matrix_fails_closed_at_real_parser(
    tmp_path: Path, monkeypatch, label: str, header: bytes, body: bytes,
) -> None:
    """T2.4: malformed child protocol never publishes a payload or adds a second request."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    payload = b"x"
    fixture = GitFixture(tmp_path / f"t2-protocol-{label}")
    commit_oid, _ = fixture.commit_file("pin.md", payload)
    fixture._git("repack", "-adf")
    fixture._git("prune-packed")
    oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
    rendered_header = header.replace(b"{oid}", oid.encode("ascii"))
    response = rendered_header + body
    child = _controlled_executable(
        tmp_path / f"git-t2-protocol-{label}",
        "import sys\n"
        "sys.stdin.buffer.readline()\n"
        f"sys.stdout.buffer.write({response!r})\n"
        "sys.stdout.buffer.flush()\n",
    )
    runner = git_source._GitRunner(fixture.root, GitScanLimits())
    runner.executable = child
    runner._executable_inode = git_source._regular_inode(child, "Git executable")
    deadline = time.monotonic() + runner.limits.timeout_seconds
    runner._persistent_accounting = git_source._PersistentReaderAccounting(deadline=deadline)
    runner._pack_namespace = git_source._freeze_pack_namespace(
        fixture.root / ".git/objects", runner.limits, deadline
    )
    try:
        if label in {"extra-same-response", "delayed-extra", "trailing-eof"}:
            assert runner._read_packed_source(
                fixture.root / ".git/objects", oid, "blob", "sha1", deadline
            ) == ("blob", payload)
            with pytest.raises(GitAuthorityError, match="trailing"):
                runner._close_persistent_reader()
        else:
            with pytest.raises(GitAuthorityError):
                runner._read_packed_source(
                    fixture.root / ".git/objects", oid, "blob", "sha1", deadline
                )
        reader = runner._persistent_reader
        assert reader is None or reader.closed
    finally:
        runner.close(suppress_terminal_error=True)


@pytest.mark.parametrize(
    "object_type,declared_size",
    (("blob", 2_000_001), ("tree", 100_000_001)),
)
def test_t2_group_05_oversized_header_is_rejected_before_body_stream(
    monkeypatch, object_type: str, declared_size: int,
) -> None:
    """T2.5: a streaming header limit rejects without reading/retaining a body."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    oid = "0" * 40
    runner = object.__new__(git_source._GitRunner)
    runner.limits = GitScanLimits()
    runner._returned_object_sha256 = {}
    runner._persistent_accounting = git_source._PersistentReaderAccounting(
        deadline=time.monotonic() + 5.0
    )
    reader = object.__new__(git_source._BootstrapBatchReader)
    reader.runner = runner
    reader.bootstrap = SimpleNamespace()
    reader.deadline = runner._persistent_accounting.deadline
    reader.process = None
    reader.termination = None
    reader.guard = None
    reader.closed = False
    reader.poisoned = False
    reader.in_flight = False
    reader.requests = 0
    reader.protocol_bytes = 0
    reader._assert_authority = lambda: git_source._seal_deadline(reader._shared_deadline())
    reader._write_request = lambda _request: None
    reader._read_header = lambda: f"{oid} {object_type} {declared_size}".encode("ascii")
    reader._read = lambda _size: pytest.fail("oversized body was accepted or accumulated")
    with pytest.raises(GitAuthorityError, match="exceeds"):
        reader.read_object(oid, object_type, "sha1")
    assert reader.poisoned


def _t6_real_packed_reader(
    tmp_path: Path, limits: GitScanLimits, *, payload: bytes = b"x", executable: Path | None = None,
):
    """Create one real packed-object reader under a caller-controlled snapshot budget."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture = GitFixture(tmp_path / "t6-real-reader")
    commit_oid, payload = fixture.commit_file("pin.md", payload)
    fixture._git("repack", "-adf")
    fixture._git("prune-packed")
    oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
    runner = git_source._GitRunner(fixture.root, limits)
    if executable is not None:
        runner.executable = executable
        runner._executable_inode = git_source._regular_inode(executable, "Git executable")
    deadline = time.monotonic() + limits.timeout_seconds
    runner._persistent_accounting = git_source._PersistentReaderAccounting(deadline=deadline)
    runner._pack_namespace = git_source._freeze_pack_namespace(
        fixture.root / ".git/objects", limits, deadline
    )
    return fixture, runner, oid, payload, deadline


def _t6_read_then_close(
    runner, source: Path, oid: str, deadline: float, payload: bytes = b"x",
) -> object:
    assert runner._read_packed_source(source, oid, "blob", "sha1", deadline) == ("blob", payload)
    reader = runner._persistent_reader
    assert reader is not None and reader.process is not None
    runner._close_persistent_reader()
    assert reader.closed
    assert reader.termination is not None and reader.termination.leader_reaped
    return reader


def test_t2_group_06_decoded_header_and_request_budgets_survive_real_reader_incarnations(
    tmp_path: Path,
) -> None:
    """T2.6: actual readers retain all non-CPU request accounting after replacement."""
    decoded_payload = b"d" * 700
    fixture, runner, oid, _payload, deadline = _t6_real_packed_reader(
        tmp_path / "decoded",
        GitScanLimits(max_blob_bytes=len(decoded_payload), max_total_bytes=1_300, timeout_seconds=3.0),
        payload=decoded_payload,
    )
    try:
        first = _t6_read_then_close(
            runner, fixture.root / ".git/objects", oid, deadline, decoded_payload
        )
        with pytest.raises(GitAuthorityError, match="exceeds"):
            runner._read_packed_source(
                fixture.root / ".git/objects", oid, "blob", "sha1", deadline
            )
        assert runner._persistent_reader is not first
        assert runner._persistent_accounting.decoded_bytes == len(decoded_payload)
    finally:
        runner.close(suppress_terminal_error=True)

    header_limits = GitScanLimits(max_total_bytes=1_200, timeout_seconds=30.0)
    fixture, runner, oid, _payload, deadline = _t6_real_packed_reader(
        tmp_path / "header", header_limits
    )
    try:
        while runner._persistent_accounting.header_bytes <= header_limits.max_total_bytes:
            try:
                _t6_read_then_close(runner, fixture.root / ".git/objects", oid, deadline)
            except GitAuthorityError as exc:
                assert "protocol budget" in str(exc)
                break
        else:
            pytest.fail("real reader incarnations never exhausted the header budget")
        assert runner._persistent_accounting.header_bytes > header_limits.max_total_bytes
    finally:
        runner.close(suppress_terminal_error=True)

    request_limits = GitScanLimits(max_entries=3, timeout_seconds=3.0)
    fixture, runner, oid, _payload, deadline = _t6_real_packed_reader(
        tmp_path / "request", request_limits
    )
    try:
        first = _t6_read_then_close(runner, fixture.root / ".git/objects", oid, deadline)
        _t6_read_then_close(runner, fixture.root / ".git/objects", oid, deadline)
        _t6_read_then_close(runner, fixture.root / ".git/objects", oid, deadline)
        with pytest.raises(GitAuthorityError, match="request budget"):
            runner._read_packed_source(
                fixture.root / ".git/objects", oid, "blob", "sha1", deadline
            )
        assert runner._persistent_reader is not first
        assert runner._persistent_accounting.request_count == request_limits.max_entries
    finally:
        runner.close(suppress_terminal_error=True)


def test_t2_group_06_wall_and_cpu_budgets_use_real_snapshot_reader_receipts(
    tmp_path: Path,
) -> None:
    """T2.6: a wall deadline and exact-PID CPU receipt cannot be renewed by a child."""
    import json
    import scripts.nautilus_pin_inventory.git_source as git_source

    cpu_records = tmp_path / "t6-cpu.jsonl"
    executable = _controlled_executable(
        tmp_path / "git-t6-cpu",
        "import json, os, pathlib, resource, sys\n"
        "if 'cat-file' in sys.argv and '--batch' in sys.argv:\n"
        f"    with pathlib.Path({str(cpu_records)!r}).open('a', encoding='utf-8') as handle:\n"
        "        handle.write(json.dumps({'pid': os.getpid(), 'rlimit_cpu': resource.getrlimit(resource.RLIMIT_CPU)}) + '\\n')\n"
        "os.execvpe('/usr/bin/git', ['/usr/bin/git', *sys.argv[1:]], os.environ)",
    )
    limits = GitScanLimits(timeout_seconds=3.0)
    fixture, runner, oid, _payload, deadline = _t6_real_packed_reader(
        tmp_path / "cpu", limits, executable=executable
    )
    try:
        first = _t6_read_then_close(runner, fixture.root / ".git/objects", oid, deadline)
        accounting = runner._persistent_accounting
        assert first.termination is not None
        assert accounting.cpu_receipt_count == 1
        assert accounting.cpu_used_seconds > 0
        assert first.termination.process.pid == json.loads(
            cpu_records.read_text(encoding="utf-8").splitlines()[0]
        )["pid"]
        expected_second_limit = math.floor(
            git_source._derive_child_resource_envelope(limits).cpu_seconds[0]
            - accounting.cpu_used_seconds
        )
        assert runner._read_packed_source(
            fixture.root / ".git/objects", oid, "blob", "sha1", deadline
        ) == ("blob", b"x")
        runner._close_persistent_reader()
        receipts = [json.loads(line) for line in cpu_records.read_text(encoding="utf-8").splitlines()]
        assert len(receipts) == 2
        original = git_source._derive_child_resource_envelope(limits).cpu_seconds[0]
        assert receipts[0]["rlimit_cpu"] == [original, original]
        assert receipts[1]["rlimit_cpu"] == [expected_second_limit, expected_second_limit]
        assert expected_second_limit < original
        assert accounting.cpu_receipt_count == 2
    finally:
        runner.close(suppress_terminal_error=True)

    fixture, runner, oid, _payload, deadline = _t6_real_packed_reader(
        tmp_path / "wall", GitScanLimits(timeout_seconds=0.2)
    )
    try:
        _t6_read_then_close(runner, fixture.root / ".git/objects", oid, deadline)
        while time.monotonic() <= deadline:
            time.sleep(0.01)
        with pytest.raises(GitAuthorityError, match="seal deadline"):
            runner._read_packed_source(
                fixture.root / ".git/objects", oid, "blob", "sha1", deadline
            )
        assert runner._persistent_reader is None
    finally:
        runner.close(suppress_terminal_error=True)


def test_t2_group_06_stderr_budget_rejects_a_replacement_real_reader(
    tmp_path: Path,
) -> None:
    """T2.6: stderr is charged to the shared snapshot when the second child writes it."""
    state = tmp_path / "t6-stderr-count"
    executable = _controlled_executable(
        tmp_path / "git-t6-stderr",
        "import os, pathlib, sys\n"
        f"state = pathlib.Path({str(state)!r})\n"
        "count = int(state.read_text() if state.exists() else '0') + 1\n"
        "state.write_text(str(count), encoding='ascii')\n"
        "if count == 2:\n"
        "    sys.stderr.buffer.write(b'e' * 1201); sys.stderr.buffer.flush()\n"
        "os.execvpe('/usr/bin/git', ['/usr/bin/git', *sys.argv[1:]], os.environ)",
    )
    fixture, runner, oid, _payload, deadline = _t6_real_packed_reader(
        tmp_path / "stderr", GitScanLimits(max_total_bytes=1_200, timeout_seconds=3.0), executable=executable
    )
    try:
        _t6_read_then_close(runner, fixture.root / ".git/objects", oid, deadline)
        with pytest.raises(GitAuthorityError, match="stderr budget"):
            runner._read_packed_source(
                fixture.root / ".git/objects", oid, "blob", "sha1", deadline
            )
        assert runner._persistent_accounting.stderr_bytes > runner.limits.max_total_bytes
    finally:
        runner.close(suppress_terminal_error=True)


@pytest.mark.parametrize(
    "fault_program",
    (
        "raise SystemExit(17)",
        "import sys; sys.stderr.write('fault\\n'); sys.stderr.flush(); raise SystemExit(17)",
        "import time; time.sleep(60)",
        "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); raise SystemExit(17)",
    ),
)
def test_t2_group_07_faulted_batch_child_poisoning_is_terminal_and_reaped(
    packed_git_fixture, tmp_path: Path, monkeypatch, fault_program: str,
) -> None:
    """T2.7: process/protocol faults poison one persistent lifecycle and leak no fd."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    baseline_fds = _stable_descriptor_classes()
    blob_oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def faulting_popen(arguments, *args, **kwargs):
        command = tuple(str(argument) for argument in arguments)
        if command[-2:] == ("cat-file", "--batch"):
            process = real_popen((sys.executable, "-c", fault_program), *args, **kwargs)
            children.append(process)
            return process
        return real_popen(arguments, *args, **kwargs)

    runner = git_source._GitRunner(fixture.root, GitScanLimits(timeout_seconds=0.2))
    deadline = time.monotonic() + runner.limits.timeout_seconds
    runner._persistent_accounting = git_source._PersistentReaderAccounting(
        deadline=deadline
    )
    runner._pack_namespace = git_source._freeze_pack_namespace(
        fixture.root / ".git/objects", runner.limits, deadline
    )
    try:
        monkeypatch.setattr(git_source.subprocess, "Popen", faulting_popen)
        with pytest.raises(GitAuthorityError) as first:
            runner._read_packed_source(
                fixture.root / ".git/objects", blob_oid, "blob", "sha1", deadline
            )
        with pytest.raises(GitAuthorityError) as repeated:
            runner._read_packed_source(
                fixture.root / ".git/objects", blob_oid, "blob", "sha1", deadline
            )
        assert repeated.value is first.value
    finally:
        runner.close(suppress_terminal_error=True)
    assert children
    assert all(child.poll() is not None for child in children)
    assert _stable_descriptor_classes() == baseline_fds
    assert expected


def _t6_group_08_live_reader(tmp_path: Path):
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture = GitFixture(tmp_path / "t6-group-08")
    commit_oid, _payload = fixture.commit_file("pin.md", b"x")
    fixture._git("repack", "-adf")
    fixture._git("prune-packed")
    oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
    runner = git_source._GitRunner(fixture.root, GitScanLimits(timeout_seconds=3.0))
    deadline = time.monotonic() + runner.limits.timeout_seconds
    runner._persistent_accounting = git_source._PersistentReaderAccounting(deadline=deadline)
    runner._pack_namespace = git_source._freeze_pack_namespace(
        fixture.root / ".git/objects", runner.limits, deadline
    )
    assert runner._read_packed_source(
        fixture.root / ".git/objects", oid, "blob", "sha1", deadline
    ) == ("blob", b"x")
    reader = runner._persistent_reader
    assert reader is not None and reader.process is not None and reader.termination is not None
    return fixture, runner, reader, oid, deadline


def _t6_group_08_real_primary(runner, source: Path, oid: str, deadline: float):
    import scripts.nautilus_pin_inventory.git_source as git_source

    with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
        runner._read_packed_source(source, oid, "tree", "sha1", deadline)
    assert "OID/type does not match request" in str(raised.value.primary)
    return raised.value


def test_t2_group_08_killpg_failure_aggregates_at_owned_process_group(
    tmp_path: Path, monkeypatch,
) -> None:
    """T2.8: a real reader primary keeps cleanup at its exact owned PGID."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, runner, reader, oid, deadline = _t6_group_08_live_reader(tmp_path)
    real_killpg = git_source.os.killpg
    calls: list[tuple[int, int]] = []

    def signal_then_deny(pgid: int, requested_signal: int) -> None:
        calls.append((pgid, requested_signal))
        if requested_signal == signal.SIGKILL and len(calls) == 1:
            real_killpg(pgid, requested_signal)
        raise PermissionError(errno.EPERM, "T6 injected killpg cleanup failure")

    monkeypatch.setattr(git_source.os, "killpg", signal_then_deny)
    try:
        error = _t6_group_08_real_primary(
            runner, fixture.root / ".git/objects", oid, deadline
        )
        assert "process-group cleanup was not confirmed" in str(error.cleanup)
        assert reader.process.poll() is not None
        assert reader.process.stdout is not None and reader.process.stdout.closed
        assert reader.process.stderr is not None and reader.process.stderr.closed
        assert calls == [(reader.termination.pgid, signal.SIGKILL)] * 2
        with pytest.raises(ProcessLookupError):
            real_killpg(reader.termination.pgid, 0)
    finally:
        runner.close(suppress_terminal_error=True)


def test_t2_group_08_reap_failure_aggregates_after_exact_pid_reap(
    tmp_path: Path, monkeypatch,
) -> None:
    """T2.8: the real exact-PID reap receipt cannot hide a cleanup failure."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, runner, reader, oid, deadline = _t6_group_08_live_reader(tmp_path)
    real_reap = git_source._ProcessTermination._reap_leader
    real_wait4 = git_source.os.wait4
    reaped: list[int] = []
    wait4_pids: list[int] = []

    def record_wait4(pid: int, options: int):
        wait4_pids.append(pid)
        return real_wait4(pid, options)

    def reap_then_fail(termination):
        reaped.append(termination.process.pid)
        assert real_reap(termination) is None
        return GitAuthorityError("T6 injected exact-PID reap cleanup failure")

    monkeypatch.setattr(git_source.os, "wait4", record_wait4)
    monkeypatch.setattr(git_source._ProcessTermination, "_reap_leader", reap_then_fail)
    try:
        error = _t6_group_08_real_primary(
            runner, fixture.root / ".git/objects", oid, deadline
        )
        assert "exact-PID reap cleanup failure" in str(error.cleanup)
        assert reaped == [reader.process.pid]
        assert wait4_pids and set(wait4_pids) == {reader.process.pid}
        assert reader.termination.leader_reaped
        assert reader.process.poll() is not None
        assert reader.process.stdout is not None and reader.process.stdout.closed
        assert reader.process.stderr is not None and reader.process.stderr.closed
    finally:
        runner.close(suppress_terminal_error=True)


def test_t2_group_08_selector_and_guard_cleanup_continue_after_real_guard_primary(
    tmp_path: Path, monkeypatch,
) -> None:
    """T2.8: a guard primary still closes the real drain selector and guard."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, runner, reader, _oid, _deadline = _t6_group_08_live_reader(tmp_path)
    guard = reader.guard
    assert guard is not None
    real_selector = git_source.selectors.DefaultSelector
    real_assert_quiet = guard.assert_quiet
    selector_closed: list[bool] = []

    class FailingDrainSelector:
        def __init__(self) -> None:
            self._selector = real_selector()

        def __getattr__(self, name: str):
            return getattr(self._selector, name)

        def close(self) -> None:
            self._selector.close()
            if not reader.in_flight:
                selector_closed.append(True)
                raise OSError("T6 injected stream selector cleanup failure")

    def guard_primary() -> None:
        real_assert_quiet()
        raise GitAuthorityError("T6 injected guard primary")

    monkeypatch.setattr(git_source.selectors, "DefaultSelector", FailingDrainSelector)
    monkeypatch.setattr(guard, "assert_quiet", guard_primary)
    try:
        error = reader.close()
        assert isinstance(error, git_source.GitAuthorityAggregateError)
        assert error.primary is not error.cleanup
        assert "T6 injected guard primary" in str(error)
        assert "stream selector cleanup failure" in str(error)
        assert selector_closed == [True]
        assert reader.process is not None and reader.process.poll() is not None
        assert reader.process.stdout is not None and reader.process.stdout.closed
        assert reader.process.stderr is not None and reader.process.stderr.closed
        assert guard._closed
    finally:
        runner.close(suppress_terminal_error=True)


def test_t2_group_08_guard_close_failure_aggregates_after_real_reader_primary(
    tmp_path: Path, monkeypatch,
) -> None:
    """T2.8: guard-close failure does not stop child and stream cleanup."""
    fixture, runner, reader, oid, deadline = _t6_group_08_live_reader(tmp_path)
    guard = reader.guard
    assert guard is not None
    real_close = guard.close

    def close_then_fail() -> None:
        real_close()
        raise GitAuthorityError("T6 injected guard cleanup failure")

    monkeypatch.setattr(guard, "close", close_then_fail)
    try:
        error = _t6_group_08_real_primary(
            runner, fixture.root / ".git/objects", oid, deadline
        )
        assert "guard cleanup failure" in str(error.cleanup)
        assert guard._closed
        assert reader.process is not None and reader.process.poll() is not None
        assert reader.process.stdout is not None and reader.process.stdout.closed
        assert reader.process.stderr is not None and reader.process.stderr.closed
    finally:
        runner.close(suppress_terminal_error=True)


def test_t2_group_08_bootstrap_close_failure_runs_at_real_retained_owner(
    git_fixture: GitFixture, monkeypatch,
) -> None:
    """T2.8: retained bootstrap cleanup is invoked, not simulated as an aggregate."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _payload = git_fixture.commit_file("pin.md", b"T6 bootstrap cleanup\n")
    git_fixture._git("repack", "-adf")
    git_fixture._git("prune-packed")
    real_close = git_source._PackBootstrap.close
    calls: list[object] = []

    def close_then_fail(bootstrap):
        calls.append(bootstrap)
        real_close(bootstrap)
        raise GitAuthorityError("T6 injected bootstrap cleanup failure")

    monkeypatch.setattr(git_source._PackBootstrap, "close", close_then_fail)
    with pytest.raises(GitAuthorityError, match="bootstrap cleanup failure"):
        GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
    assert calls
    assert all(bootstrap.cleanup_receipt().descriptors_closed for bootstrap in calls)


def _t2_real_packed_runner(tmp_path: Path, object_format: str):
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture = GitFixture(tmp_path / f"t2-authority-{object_format}", object_format=object_format)
    commit_oid, payload = fixture.commit_file("pin.md", b"T2 retained pack payload\n")
    fixture._git("repack", "-adf")
    fixture._git("prune-packed")
    blob_oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
    runner = git_source._GitRunner(fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", object_format)
    return fixture, runner, commit_oid, blob_oid, payload


def _t2_active_packed_reader(tmp_path: Path, object_format: str):
    """A real bootstrap reader before copied-closure handoff."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture = GitFixture(tmp_path / f"t2-active-{object_format}", object_format=object_format)
    commit_oid, payload = fixture.commit_file("pin.md", b"T2 active bootstrap payload\n")
    fixture._git("repack", "-adf")
    fixture._git("prune-packed")
    blob_oid = fixture._git("rev-parse", f"{commit_oid}:pin.md").decode("ascii").strip()
    runner = git_source._GitRunner(fixture.root, GitScanLimits())
    deadline = time.monotonic() + runner.limits.timeout_seconds
    runner._persistent_accounting = git_source._PersistentReaderAccounting(deadline=deadline)
    runner._pack_namespace = git_source._freeze_pack_namespace(
        fixture.root / ".git/objects", runner.limits, deadline
    )
    assert runner._read_packed_source(
        fixture.root / ".git/objects", blob_oid, "blob", object_format, deadline
    ) == ("blob", payload)
    return fixture, runner, commit_oid, blob_oid, payload, deadline


@pytest.mark.parametrize("object_format,mutation", (("sha1", "source-mode"), ("sha256", "private-add")))
def test_t2_group_09_real_authority_mutation_poisoning_is_terminal(
    tmp_path: Path, object_format: str, mutation: str,
) -> None:
    """T2.9: source/private changes at a request boundary cannot be retried away."""
    fixture, runner, _commit_oid, blob_oid, _payload, deadline = _t2_active_packed_reader(tmp_path, object_format)
    pack_directory = fixture.root / ".git/objects/pack"
    source_member = next(iter(sorted(pack_directory.glob("*.pack"))))
    reader = runner._persistent_reader
    assert reader is not None
    marker = reader.bootstrap.root / "objects" / "pack" / "late-private-entry"
    original_mode = stat.S_IMODE(source_member.stat().st_mode)
    try:
        if mutation == "source-mode":
            os.chmod(source_member, 0o600)
        else:
            marker.write_bytes(b"foreign\n")
        with pytest.raises(GitAuthorityError) as first:
            runner._read_packed_source(
                fixture.root / ".git/objects", blob_oid, "blob", object_format, deadline
            )
        if mutation == "source-mode":
            os.chmod(source_member, original_mode)
        else:
            marker.unlink()
        # Restoring the name/mode cannot clear the retained terminal receipt.
        if mutation == "source-mode":
            with pytest.raises(GitAuthorityError) as repeated:
                runner._read_packed_source(
                    fixture.root / ".git/objects", blob_oid, "blob", object_format, deadline
                )
            assert repeated.value is first.value
        else:
            with pytest.raises(GitAuthorityError) as repeated:
                runner._read_packed_source(
                    fixture.root / ".git/objects", blob_oid, "blob", object_format, deadline
                )
            assert repeated.value is first.value
    finally:
        if source_member.exists():
            os.chmod(source_member, original_mode)
        marker.unlink(missing_ok=True)
        runner.close(suppress_terminal_error=True)


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_10_late_unrelated_pack_is_ignored_without_adoption(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.10: a late canonical pair cannot become selected authority."""
    launches = _t2_record_batch_launches(monkeypatch)
    fixture, runner, _commit_oid, blob_oid, payload, deadline = _t2_active_packed_reader(tmp_path, object_format)
    pack_directory = fixture.root / ".git/objects/pack"
    source_pack = next(iter(sorted(pack_directory.glob("*.pack"))))
    source_idx = source_pack.with_suffix(".idx")
    late_pack = pack_directory / f"late-{source_pack.name}"
    late_idx = pack_directory / f"late-{source_idx.name}"
    try:
        shutil.copy2(source_pack, late_pack)
        shutil.copy2(source_idx, late_idx)
        assert late_pack.is_file() and late_idx.is_file()
        actual_type, actual_payload = runner._read_packed_source(
            fixture.root / ".git/objects", blob_oid, "blob", object_format, deadline
        )
        assert actual_type == "blob"
        assert actual_payload == payload
        assert len(launches) == 1
        assert all(
            entry.name not in {late_pack.name, late_idx.name}
            for bootstrap in runner._pack_bootstraps
            for entry in bootstrap.entries
        )
    finally:
        late_pack.unlink(missing_ok=True)
        late_idx.unlink(missing_ok=True)
        runner.close(suppress_terminal_error=True)


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_11_controlled_child_receipts_are_pinned_and_reused(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.11: the real batch child has a bounded descriptor-only envelope."""
    import json
    import scripts.nautilus_pin_inventory.git_source as git_source

    records = tmp_path / f"t2-child-{object_format}.jsonl"
    executable = _controlled_executable(
        tmp_path / f"t2-child-{object_format}",
        "import json, os, pathlib, resource, sys\n"
        f"target = pathlib.Path({str(records)!r})\n"
        "if 'cat-file' in sys.argv and '--batch' in sys.argv:\n"
        "    record = {'cwd': os.getcwd(), 'pgid': os.getpgrp(), 'object_directory': os.environ.get('GIT_OBJECT_DIRECTORY'), 'alternates': os.environ.get('GIT_ALTERNATE_OBJECT_DIRECTORIES'), 'prompt': os.environ.get('GIT_TERMINAL_PROMPT'), 'no_lazy': os.environ.get('GIT_NO_LAZY_FETCH'), 'no_replace': os.environ.get('GIT_NO_REPLACE_OBJECTS'), 'count': os.environ.get('GIT_CONFIG_COUNT'), 'key0': os.environ.get('GIT_CONFIG_KEY_0'), 'key1': os.environ.get('GIT_CONFIG_KEY_1'), 'rlimit_cpu': resource.getrlimit(resource.RLIMIT_CPU), 'fds': sorted(os.listdir('/proc/self/fd'))}\n"
        "    with target.open('a', encoding='utf-8') as handle: handle.write(json.dumps(record, sort_keys=True) + '\\n')\n"
        "os.execvpe('/usr/bin/git', ['/usr/bin/git', *sys.argv[1:]], os.environ)"
    )
    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(executable))
    fixture, runner, _commit_oid, blob_oid, payload, deadline = _t2_active_packed_reader(tmp_path, object_format)
    try:
        for _ in range(1):
            assert runner._read_packed_source(
                fixture.root / ".git/objects", blob_oid, "blob", object_format, deadline
            ) == ("blob", payload)
    finally:
        runner.close(suppress_terminal_error=True)
    observed = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert len(observed) == 1
    receipt = observed[0]
    assert receipt["cwd"] == str(fixture.root)
    assert receipt["object_directory"]
    assert receipt["alternates"] == ""
    assert receipt["prompt"] == "0"
    assert receipt["no_lazy"] == "1"
    assert receipt["no_replace"] == "1"
    assert receipt["count"] == "2"
    assert receipt["key0"] == "core.deltaBaseCacheLimit"
    assert receipt["key1"] == "core.packedGitLimit"
    assert receipt["pgid"] != os.getpgrp()
    assert receipt["rlimit_cpu"] == list(git_source._derive_child_resource_envelope(
        GitScanLimits()
    ).cpu_seconds)
    authority_fd = receipt["object_directory"].rsplit("/", 1)[1]
    # fd 3 is the controlled executable interpreter's script descriptor;
    # the only inherited authority descriptor is the exact fd named by Git.
    assert set(receipt["fds"]) == {"0", "1", "2", "3", authority_fd}


def _t6_hardlink_events(monkeypatch, members: tuple[Path, ...]):
    """Observe real source-link deltas at the OS owner boundaries."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    sources = {member.name: member for member in members}
    links: list[tuple[str, int | None, int]] = []
    unlinks: list[tuple[str, int | None, int]] = []
    real_link = git_source.os.link
    real_unlink = git_source.os.unlink

    def record_link(source, destination, *args, **kwargs):
        result = real_link(source, destination, *args, **kwargs)
        name = os.fsdecode(source)
        if name in sources:
            links.append((name, kwargs.get("dst_dir_fd"), sources[name].stat().st_nlink))
        return result

    def record_unlink(path, *args, **kwargs):
        result = real_unlink(path, *args, **kwargs)
        name = os.fsdecode(path)
        if name in sources:
            unlinks.append((name, kwargs.get("dir_fd"), sources[name].stat().st_nlink))
        return result

    monkeypatch.setattr(git_source.os, "link", record_link)
    monkeypatch.setattr(git_source.os, "unlink", record_unlink)
    return links, unlinks


def _t6_packed_hardlink_fixture(tmp_path: Path, object_format: str):
    fixture = GitFixture(tmp_path / f"t2-hardlink-{object_format}", object_format=object_format)
    commit_oid, _payload = fixture.commit_file("pin.md", b"T2 hardlink receipt\n")
    fixture._git("repack", "-adf")
    fixture._git("prune-packed")
    members = tuple(sorted((*(fixture.root / ".git/objects/pack").glob("*.pack"), *(fixture.root / ".git/objects/pack").glob("*.idx"))))
    assert len(members) == 2
    return fixture, commit_oid, members, {member.name: member.stat().st_nlink for member in members}


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_12_real_hardlink_owner_transitions_are_independent(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.12: bootstrap and copied-store links transition one owner at a time."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, members, before = _t6_packed_hardlink_fixture(tmp_path, object_format)
    links, unlinks = _t6_hardlink_events(monkeypatch, members)
    runner = git_source._GitRunner(fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", object_format)
    bootstrap = runner._pack_bootstraps[0]
    private = runner._private_closure
    assert private is not None
    assert sorted(links) == sorted([
        *((member.name, bootstrap.destination_pack_fd, before[member.name] + 1) for member in members),
        *((member.name, private.pack_fd, before[member.name] + 2) for member in members),
    ])
    try:
        runner.close()
    finally:
        runner.close(suppress_terminal_error=True)
    assert sorted(unlinks) == sorted([
        *((member.name, private.pack_fd, before[member.name] + 1) for member in members),
        *((member.name, bootstrap.destination_pack_fd, before[member.name]) for member in members),
    ])
    assert {member.name: member.stat().st_nlink for member in members} == before


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_12_partial_bootstrap_construction_releases_its_only_link(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.12: failed construction cannot leave an unowned bootstrap link."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, members, before = _t6_packed_hardlink_fixture(tmp_path, object_format)
    links, unlinks = _t6_hardlink_events(monkeypatch, members)
    recorded_link = git_source.os.link

    def reject_second_bootstrap_link(source, destination, *args, **kwargs):
        if os.fsdecode(source) in before and len(links) == 1:
            raise OSError(errno.EIO, "T6 partial bootstrap construction")
        return recorded_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(git_source.os, "link", reject_second_bootstrap_link)
    runner = git_source._GitRunner(fixture.root, GitScanLimits())
    with pytest.raises(GitAuthorityError, match="bootstrap copy failed"):
        runner.seal_object_store(commit_oid, "commit", object_format)
    runner.close(suppress_terminal_error=True)
    assert len(links) == len(unlinks) == 1
    name, _owner, count = links[0]
    assert count == before[name] + 1
    assert unlinks == [(name, unlinks[0][1], before[name])]
    assert {member.name: member.stat().st_nlink for member in members} == before


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_12_failed_handoff_releases_each_owner_in_order(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.12: a failed handoff cannot compensate one owner with the other."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, members, before = _t6_packed_hardlink_fixture(tmp_path, object_format)
    links, unlinks = _t6_hardlink_events(monkeypatch, members)
    owners: dict[str, int] = {}

    def reject_handoff(builder, capture, limits, deadline):
        assert builder.pack is not None
        owners["copied"] = builder.pack.descriptor
        owners["bootstrap"] = runner._pack_bootstraps[0].destination_pack_fd
        raise GitAuthorityError("T6 injected handoff failure")

    monkeypatch.setattr(git_source, "_retain_private_closure", reject_handoff)
    runner = git_source._GitRunner(fixture.root, GitScanLimits())
    with pytest.raises(GitAuthorityError, match="injected handoff failure"):
        runner.seal_object_store(commit_oid, "commit", object_format)
    assert len(runner._pack_bootstraps) == 0
    assert len(links) == len(members) * 2
    assert sorted(count for _name, _owner, count in links[:len(members)]) == sorted(
        count + 1 for count in before.values()
    )
    assert sorted(count for _name, _owner, count in links[len(members):]) == sorted(
        count + 2 for count in before.values()
    )
    assert len(unlinks) == len(members) * 2
    assert sorted(unlinks) == sorted([
        *((member.name, owners["copied"], before[member.name] + 1) for member in members),
        *((member.name, owners["bootstrap"], before[member.name]) for member in members),
    ])
    assert {member.name: member.stat().st_nlink for member in members} == before


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t2_group_12_copied_store_close_failure_still_releases_bootstrap(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """T2.12: a copied-store close error cannot skip the bootstrap release."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, members, before = _t6_packed_hardlink_fixture(tmp_path, object_format)
    links, unlinks = _t6_hardlink_events(monkeypatch, members)
    runner = git_source._GitRunner(fixture.root, GitScanLimits())
    runner.seal_object_store(commit_oid, "commit", object_format)
    bootstrap = runner._pack_bootstraps[0]
    private = runner._private_closure
    assert private is not None
    recorded_unlink = git_source.os.unlink
    failed = False

    def unlink_then_fail(path, *args, **kwargs):
        nonlocal failed
        result = recorded_unlink(path, *args, **kwargs)
        if (
            not failed
            and os.fsdecode(path) in before
            and kwargs.get("dir_fd") == private.pack_fd
        ):
            failed = True
            raise OSError(errno.EIO, "T6 copied-store close failure")
        return result

    monkeypatch.setattr(git_source.os, "unlink", unlink_then_fail)
    try:
        with pytest.raises(GitAuthorityError) as raised:
            runner.close()
    finally:
        runner.close(suppress_terminal_error=True)
    assert "descriptor-relative cleanup failed" in str(raised.value)
    assert raised.value.__cause__ is not None
    assert "T6 copied-store close failure" in str(raised.value.__cause__)
    assert failed
    assert sorted(links) == sorted([
        *((member.name, bootstrap.destination_pack_fd, before[member.name] + 1) for member in members),
        *((member.name, private.pack_fd, before[member.name] + 2) for member in members),
    ])
    assert sorted(unlinks) == sorted([
        *((member.name, private.pack_fd, before[member.name] + 1) for member in members),
        *((member.name, bootstrap.destination_pack_fd, before[member.name]) for member in members),
    ])
    assert {member.name: member.stat().st_nlink for member in members} == before


def test_t5_i1_capture_closes_persistent_reader_before_return(
    packed_git_fixture, monkeypatch,
) -> None:
    """The closure boundary cannot return while its batch reader remains live."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, expected = packed_git_fixture
    original_capture = git_source._capture_requested_closure
    original_read = git_source._GitRunner._read_persistent_bootstrap_object
    owners = []

    def record_owner(self, *args, **kwargs):
        owners.append(self)
        return original_read(self, *args, **kwargs)

    def capture_with_lifecycle_receipt(*args, **kwargs):
        capture = original_capture(*args, **kwargs)
        assert owners
        reader = owners[-1]._persistent_reader
        assert reader is None or reader.closed
        return capture

    monkeypatch.setattr(git_source._GitRunner, "_read_persistent_bootstrap_object", record_owner)
    monkeypatch.setattr(git_source, "_capture_requested_closure", capture_with_lifecycle_receipt)
    assert GitTreeSnapshot.from_commit(fixture.root, commit_oid).blob("pin.md").data == expected


def test_t5_i2_close_rejects_trailing_stdout_and_stderr_after_valid_response() -> None:
    """Normal EOF cleanup must consume both pipes before accepting a reader."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    class Process:
        def __init__(self) -> None:
            stdout_read, stdout_write = os.pipe()
            stderr_read, stderr_write = os.pipe()
            os.write(stdout_write, b"TRAILING")
            os.write(stderr_write, b"unexpected stderr")
            os.close(stdout_write)
            os.close(stderr_write)
            self.stdin = open(os.devnull, "wb")
            self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
            self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
            self.returncode = None

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

    process = Process()
    reader = object.__new__(git_source._BootstrapBatchReader)
    reader.closed = False
    reader.poisoned = False
    reader.process = process
    reader.guard = None
    reader.runner = SimpleNamespace(limits=GitScanLimits())
    reader._shared_deadline = lambda: time.monotonic() + 1.0
    reader._assert_authority = lambda: None
    reader.termination = SimpleNamespace(leader_reaped=False, terminate=lambda: None)
    error = reader.close()
    assert error is not None
    assert "trailing stdout" in str(error)
    assert "stderr" in str(error)
    assert process.stdout.closed and process.stderr.closed


def test_t5_i3_nonzero_reader_exit_terminates_the_owned_process_group() -> None:
    """A nonzero leader is abnormal cleanup, not a successful reap receipt."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    class Process:
        def __init__(self) -> None:
            self.stdin = open(os.devnull, "wb")
            self.stdout = open(os.devnull, "rb")
            self.stderr = open(os.devnull, "rb")
            self.returncode = None

        def wait(self, timeout=None):
            self.returncode = 17
            return 17

    terminated: list[bool] = []
    reader = object.__new__(git_source._BootstrapBatchReader)
    reader.closed = False
    reader.poisoned = False
    reader.process = Process()
    reader.guard = None
    reader.runner = SimpleNamespace(limits=GitScanLimits())
    reader._shared_deadline = lambda: time.monotonic() + 1.0
    reader._assert_authority = lambda: None
    reader.termination = SimpleNamespace(
        leader_reaped=False,
        terminate=lambda: terminated.append(True) or None,
    )
    error = reader.close()
    assert error is not None
    assert terminated == [True]


def test_t6_i2_real_abnormal_drain_reaps_zero_exit_group_descendant(
    tmp_path: Path, monkeypatch,
) -> None:
    """A zero-exit batch leader cannot leave its pipe-owning PGID behind."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    descendant = tmp_path / "t6-drain-descendant"
    real_git = shutil.which("git")
    assert real_git
    executable = _controlled_executable(
        tmp_path / "git-t6-drain-descendant",
        "import os, pathlib, subprocess, sys\n"
        f"state = pathlib.Path({str(descendant)!r})\n"
        "if 'cat-file' not in sys.argv or '--batch' not in sys.argv:\n"
        f"    raise SystemExit(subprocess.call([{real_git!r}, *sys.argv[1:]]))\n"
        f"child = subprocess.Popen([{real_git!r}, *sys.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(int(os.environ['GIT_OBJECT_DIRECTORY'].rsplit('/', 1)[1]),))\n"
        "for request in sys.stdin.buffer:\n"
        "    child.stdin.write(request); child.stdin.flush()\n"
        "    header = child.stdout.readline(); size = int(header.rstrip(b'\\n').rsplit(b' ', 1)[1]); body = child.stdout.read(size + 1)\n"
        "    sys.stdout.buffer.write(header + body); sys.stdout.buffer.flush()\n"
        "child.stdin.close(); child.wait()\n"
        "descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "state.write_text(f'{descendant.pid} {os.getpgrp()}', encoding='ascii')\n",
    )
    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(executable))
    fixture, runner, _commit_oid, _blob_oid, _payload, _deadline = _t2_active_packed_reader(
        tmp_path, "sha1"
    )
    reader = runner._persistent_reader
    assert reader is not None
    try:
        error = reader.close()
        assert error is not None
        assert "stream drain timed out" in str(error)
        assert descendant.is_file()
        descendant_pid, descendant_pgid = map(int, descendant.read_text(encoding="ascii").split())
        assert reader.termination is not None
        assert descendant_pgid == reader.termination.pgid
        assert reader.termination.leader_reaped
        assert reader.termination.group_absence_confirmed
        deadline = time.monotonic() + 1.0
        while Path(f"/proc/{descendant_pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not Path(f"/proc/{descendant_pid}").exists()
        with pytest.raises(ProcessLookupError):
            os.killpg(descendant_pgid, 0)
    finally:
        runner.close(suppress_terminal_error=True)
        fixture.root.exists()


def test_t7_i1_capture_primary_terminates_a_clean_eof_authority_descendant(
    tmp_path: Path, monkeypatch,
) -> None:
    """A capture failure must terminate a clean-EOF reader group before its authority escapes."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture = GitFixture(tmp_path / "t7-capture-primary")
    commit_oid, _payload = fixture.commit_file("pin.md", b"x")
    fixture._git("repack", "-adf")
    fixture._git("prune-packed")
    descendant = tmp_path / "t7-capture-descendant"
    real_git = shutil.which("git")
    assert real_git
    executable = _controlled_executable(
        tmp_path / "git-t7-capture-primary",
        "import os, pathlib, subprocess, sys, time\n"
        f"state = pathlib.Path({str(descendant)!r})\n"
        "if 'cat-file' not in sys.argv or '--batch' not in sys.argv:\n"
        f"    raise SystemExit(subprocess.call([{real_git!r}, *sys.argv[1:]]))\n"
        "authority_fd = int(os.environ['GIT_OBJECT_DIRECTORY'].rsplit('/', 1)[1])\n"
        f"child = subprocess.Popen([{real_git!r}, *sys.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(authority_fd,))\n"
        "for request in sys.stdin.buffer:\n"
        "    child.stdin.write(request); child.stdin.flush()\n"
        "    header = child.stdout.readline(); size = int(header.rstrip(b'\\n').rsplit(b' ', 1)[1]); body = child.stdout.read(size + 1)\n"
        "    sys.stdout.buffer.write(header + body); sys.stdout.buffer.flush()\n"
        "child.stdin.close(); child.wait()\n"
        "descendant = subprocess.Popen([sys.executable, '-c', 'import os, time; os.fstat(int(sys.argv[1])); time.sleep(60)', str(authority_fd)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(authority_fd,))\n"
        "state.write_text(f'{descendant.pid} {os.getpgrp()} {authority_fd}', encoding='ascii')\n",
    )
    readers = []
    real_reader = git_source._BootstrapBatchReader
    real_close = git_source._close_retained_descriptor

    def record_reader(*args, **kwargs):
        reader = real_reader(*args, **kwargs)
        readers.append(reader)
        return reader

    def close_then_report(descriptor, identity, *, label):
        real_close(descriptor, identity, label=label)
        if label == "Git closure root":
            raise GitAuthorityError("T7 injected provisional capture descriptor failure")

    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(executable))
    monkeypatch.setattr(git_source, "_BootstrapBatchReader", record_reader)
    monkeypatch.setattr(git_source, "_close_retained_descriptor", close_then_report)
    descendant_pid = descendant_pgid = -1
    try:
        with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
            GitTreeSnapshot.from_commit(
                fixture.root, commit_oid, limits=GitScanLimits(max_entries=3)
            )
        assert "entry seal cap" in str(raised.value.primary)
        assert "provisional capture descriptor failure" in str(raised.value.cleanup)
        assert readers and readers[-1].process is not None and readers[-1].termination is not None
        reader = readers[-1]
        assert descendant.is_file()
        descendant_pid, descendant_pgid, _authority_fd = map(
            int, descendant.read_text(encoding="ascii").split()
        )
        assert descendant_pgid == reader.termination.pgid
        assert reader.process.stdout is not None and reader.process.stdout.closed
        assert reader.process.stderr is not None and reader.process.stderr.closed
        assert reader.guard is not None and reader.guard._closed
        assert reader.termination.leader_reaped
        assert reader.termination.group_absence_confirmed
        assert not Path(f"/proc/{descendant_pid}").exists()
        with pytest.raises(ProcessLookupError):
            os.killpg(descendant_pgid, 0)
    finally:
        if descendant_pgid > 0:
            try:
                os.killpg(descendant_pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if descendant_pid > 0:
            deadline = time.monotonic() + 1.0
            while Path(f"/proc/{descendant_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.01)


def test_t7_i2_public_capture_aggregates_reader_and_capture_close_failures(
    packed_git_fixture, monkeypatch,
) -> None:
    """A completed capture retains both real-owner close failures and no caller custody."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    captures = []
    real_reader_close = git_source._BootstrapBatchReader.close
    real_capture_close = git_source._ClosureCapture.close

    def reader_close_then_fail(reader, *args, **kwargs):
        real_reader_close(reader, *args, **kwargs)
        raise GitAuthorityError("T7 injected reader-close primary")

    def capture_close_then_fail(capture):
        captures.append(capture)
        real_capture_close(capture)
        raise GitAuthorityError("T7 injected capture descriptor close")

    monkeypatch.setattr(git_source._BootstrapBatchReader, "close", reader_close_then_fail)
    monkeypatch.setattr(git_source._ClosureCapture, "close", capture_close_then_fail)
    with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
        GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    assert "reader-close primary" in str(raised.value.primary)
    assert "capture descriptor close" in str(raised.value.cleanup)
    assert captures and all(capture.closed for capture in captures)
    for capture in captures:
        with pytest.raises(OSError):
            os.fstat(capture.root_fd)
        for descriptor, _identity in capture.prefixes.values():
            with pytest.raises(OSError):
                os.fstat(descriptor)


def _t8_reader_close_primary_with_blocked_capture_root(
    monkeypatch,
    *,
    blocked: list[bool],
):
    """Drive the real packed-reader close through a real retained-root failure."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    captures = []
    attempts = []
    real_reader_close = git_source._BootstrapBatchReader.close
    real_capture_close = git_source._ClosureCapture.close
    real_close = os.close

    def reader_close_then_fail(reader, *args, **kwargs):
        real_reader_close(reader, *args, **kwargs)
        raise GitAuthorityError("T8 injected reader-close primary")

    def record_capture_close(capture):
        captures.append(capture)
        return real_capture_close(capture)

    def fail_real_root_close(descriptor: int) -> None:
        if blocked[0] and captures and descriptor == captures[-1].root_fd:
            attempts.append(descriptor)
            raise OSError(errno.EIO, "T8 injected real root close failure")
        real_close(descriptor)

    monkeypatch.setattr(
        git_source._BootstrapBatchReader, "close", reader_close_then_fail
    )
    monkeypatch.setattr(git_source._ClosureCapture, "close", record_capture_close)
    monkeypatch.setattr(git_source.os, "close", fail_real_root_close)
    return captures, attempts


def _t8_release_captures(captures) -> None:
    """Release exact-T4 diagnostics after their intentional terminal-error cache."""
    for capture in captures:
        errors = getattr(capture, "_descriptor_errors", None)
        if errors is not None:
            errors.clear()
        if not capture.closed:
            capture.close()


@pytest.mark.parametrize("entrypoint", ("commit", "tree"))
def test_t8_public_snapshot_retains_real_failed_capture_cleanup_owner(
    packed_git_fixture, monkeypatch, entrypoint: str,
) -> None:
    """Break caught: losing the pending owner after a real reader-close primary."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    tree_oid = fixture._git("rev-parse", f"{commit_oid}^{{tree}}").decode().strip()
    blocked = [True]
    captures, attempts = _t8_reader_close_primary_with_blocked_capture_root(
        monkeypatch, blocked=blocked
    )
    try:
        with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
            if entrypoint == "commit":
                GitTreeSnapshot.from_commit(fixture.root, commit_oid)
            else:
                GitTreeSnapshot.from_tree(fixture.root, tree_oid)
        error = raised.value
        assert isinstance(error, git_source.GitAuthorityCleanupPendingError)
        assert "reader-close primary" in str(error.primary)
        assert attempts == [captures[-1].root_fd, captures[-1].root_fd]
        assert os.fstat(captures[-1].root_fd).st_ino == captures[-1].root_identity[1]
        assert error.cleanup_pending
    finally:
        blocked[0] = False
        _t8_release_captures(captures)


def test_t8_retry_cleanup_closes_the_real_retained_root_once_unblocked(
    packed_git_fixture, monkeypatch,
) -> None:
    """Break caught: caching an unresolved descriptor as terminal instead of retrying it."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    blocked = [True]
    captures, attempts = _t8_reader_close_primary_with_blocked_capture_root(
        monkeypatch, blocked=blocked
    )
    try:
        with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
            GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        error = raised.value
        assert isinstance(error, git_source.GitAuthorityCleanupPendingError)
        assert error.cleanup_pending
        blocked[0] = False
        error.retry_cleanup()
        assert not error.cleanup_pending
        with pytest.raises(OSError) as root_closed:
            os.fstat(captures[-1].root_fd)
        assert root_closed.value.errno == errno.EBADF
        retried_attempts = len(attempts)
        error.retry_cleanup()
        assert len(attempts) == retried_attempts
    finally:
        blocked[0] = False
        _t8_release_captures(captures)


def test_t8_retry_cleanup_preserves_the_same_pending_error_when_still_blocked(
    packed_git_fixture, monkeypatch,
) -> None:
    """Break caught: replacing a pending cleanup owner after another bounded retry fails."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    blocked = [True]
    captures, _attempts = _t8_reader_close_primary_with_blocked_capture_root(
        monkeypatch, blocked=blocked
    )
    try:
        with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
            GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        error = raised.value
        assert isinstance(error, git_source.GitAuthorityCleanupPendingError)
        assert error.cleanup_pending
        with pytest.raises(type(error)) as retried:
            error.retry_cleanup()
        assert retried.value is error
        assert error.cleanup_pending
        assert os.fstat(captures[-1].root_fd).st_ino == captures[-1].root_identity[1]
    finally:
        blocked[0] = False
        _t8_release_captures(captures)


@pytest.mark.parametrize("entrypoint", ("commit", "tree"))
def test_t8_retry_skips_confirmed_prefix_descriptor_after_numeric_reuse(
    git_fixture: GitFixture, monkeypatch, entrypoint: str,
) -> None:
    """Break caught: retrying a confirmed-closed descriptor after its number is reused."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _expected = git_fixture.commit_file("pin.md", b"loose capture\n")
    tree_oid = git_fixture._git("rev-parse", f"{commit_oid}^{{tree}}").decode().strip()
    captures = []
    blocked = [True]
    real_capture_close = git_source._ClosureCapture.close
    real_close = os.close

    def record_capture_close(capture):
        captures.append(capture)
        return real_capture_close(capture)

    def fail_real_root_close(descriptor: int) -> None:
        if blocked[0] and captures and descriptor == captures[-1].root_fd:
            raise OSError(errno.EIO, "T8 injected real root close failure")
        real_close(descriptor)

    monkeypatch.setattr(git_source._ClosureCapture, "close", record_capture_close)
    monkeypatch.setattr(git_source.os, "close", fail_real_root_close)
    reused_fds = []
    try:
        with pytest.raises(GitAuthorityError) as raised:
            if entrypoint == "commit":
                GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)
            else:
                GitTreeSnapshot.from_tree(git_fixture.root, tree_oid)
        error = raised.value
        assert isinstance(error, git_source.GitAuthorityCleanupPendingError)
        capture = captures[-1]
        prefix_fd = next(iter(capture.prefixes.values()))[0]
        with pytest.raises(OSError):
            os.fstat(prefix_fd)
        assert error.cleanup_pending
        for _ in range(4):
            reused_fds.append(
                os.open(git_fixture.root, os.O_RDONLY | os.O_DIRECTORY)
            )
            if reused_fds[-1] == prefix_fd:
                break
        assert reused_fds[-1] == prefix_fd
        blocked[0] = False
        error.retry_cleanup()
        assert os.fstat(reused_fds[-1]).st_ino == git_fixture.root.stat().st_ino
    finally:
        blocked[0] = False
        for reused_fd in reversed(reused_fds):
            real_close(reused_fd)
        _t8_release_captures(captures)


def test_t8_nested_runner_and_snapshot_cleanup_keeps_public_retry_owner(
    packed_git_fixture, monkeypatch,
) -> None:
    """Break caught: wrapping the pending cleanup owner into an ordinary aggregate."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture, commit_oid, _expected = packed_git_fixture
    blocked = [True]
    captures, _attempts = _t8_reader_close_primary_with_blocked_capture_root(
        monkeypatch, blocked=blocked
    )
    real_runner_close = git_source._GitRunner.close

    def close_then_fail(runner, *args, **kwargs):
        real_runner_close(runner, *args, **kwargs)
        raise GitAuthorityError("T8 injected snapshot cleanup")

    monkeypatch.setattr(git_source._GitRunner, "close", close_then_fail)
    try:
        with pytest.raises(git_source.GitAuthorityAggregateError) as raised:
            GitTreeSnapshot.from_commit(fixture.root, commit_oid)
        error = raised.value
        assert isinstance(error, git_source.GitAuthorityCleanupPendingError)
        assert "reader-close primary" in str(error.primary)
        assert "snapshot cleanup" in str(error.cleanup)
        assert error.cleanup_pending
    finally:
        blocked[0] = False
        _t8_release_captures(captures)


def test_t7_i3_normal_reader_exact_reap_error_still_closes_all_real_owners(
    tmp_path: Path, monkeypatch,
) -> None:
    """An exact wait4 receipt failure cannot bypass process, stream, selector, or guard cleanup."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    _fixture, runner, reader, _oid, _deadline = _t6_group_08_live_reader(tmp_path)
    assert reader.process is not None and reader.termination is not None and reader.guard is not None
    real_wait4 = git_source.os.wait4
    failed = False

    def fail_exact_wait4(pid: int, options: int):
        nonlocal failed
        if pid == reader.process.pid and not failed:
            failed = True
            raise OSError(errno.EIO, "T7 injected exact wait4 failure")
        return real_wait4(pid, options)

    monkeypatch.setattr(git_source.os, "wait4", fail_exact_wait4)
    try:
        error = reader.close()
        assert error is not None
        assert "exact reap receipt is unavailable" in str(error)
        assert failed
        assert reader.process.poll() is not None
        assert reader.process.stdout is not None and reader.process.stdout.closed
        assert reader.process.stderr is not None and reader.process.stderr.closed
        assert reader.guard._closed
        assert reader.termination.leader_reaped
        assert reader.termination.group_absence_confirmed
    finally:
        runner.close(suppress_terminal_error=True)


def test_t7_i4_clean_drain_nonzero_reader_signals_owned_group_before_reap(
    tmp_path: Path, monkeypatch,
) -> None:
    """A cleanly drained nonzero leader cannot strand a detached-stdio same-PGID child."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    descendant = tmp_path / "t7-nonzero-descendant"
    real_git = shutil.which("git")
    assert real_git
    executable = _controlled_executable(
        tmp_path / "git-t7-nonzero-descendant",
        "import os, pathlib, subprocess, sys\n"
        f"state = pathlib.Path({str(descendant)!r})\n"
        "if 'cat-file' not in sys.argv or '--batch' not in sys.argv:\n"
        f"    raise SystemExit(subprocess.call([{real_git!r}, *sys.argv[1:]]))\n"
        "authority_fd = int(os.environ['GIT_OBJECT_DIRECTORY'].rsplit('/', 1)[1])\n"
        f"child = subprocess.Popen([{real_git!r}, *sys.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(authority_fd,))\n"
        "for request in sys.stdin.buffer:\n"
        "    child.stdin.write(request); child.stdin.flush()\n"
        "    header = child.stdout.readline(); size = int(header.rstrip(b'\\n').rsplit(b' ', 1)[1]); body = child.stdout.read(size + 1)\n"
        "    sys.stdout.buffer.write(header + body); sys.stdout.buffer.flush()\n"
        "child.stdin.close(); child.wait()\n"
        "descendant = subprocess.Popen([sys.executable, '-c', 'import os, time; os.fstat(int(sys.argv[1])); time.sleep(60)', str(authority_fd)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(authority_fd,))\n"
        "state.write_text(f'{descendant.pid} {os.getpgrp()}', encoding='ascii')\n"
        "raise SystemExit(17)\n",
    )
    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(executable))
    fixture, runner, _commit_oid, _blob_oid, _payload, _deadline = _t2_active_packed_reader(
        tmp_path, "sha1"
    )
    reader = runner._persistent_reader
    assert reader is not None and reader.termination is not None
    descendant_pid = descendant_pgid = -1
    try:
        error = reader.close()
        assert error is not None
        assert "exited nonzero" in str(error)
        assert descendant.is_file()
        descendant_pid, descendant_pgid = map(int, descendant.read_text(encoding="ascii").split())
        assert descendant_pgid == reader.termination.pgid
        assert reader.termination.leader_reaped
        assert reader.termination.group_signal_confirmed
        assert reader.termination.group_absence_confirmed
        assert not Path(f"/proc/{descendant_pid}").exists()
        with pytest.raises(ProcessLookupError):
            os.killpg(descendant_pgid, 0)
    finally:
        if descendant_pgid > 0:
            try:
                os.killpg(descendant_pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        runner.close(suppress_terminal_error=True)
        fixture.root.exists()


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_t7_i5_bootstrap_switch_keeps_one_cpu_accounting_budget(
    tmp_path: Path, monkeypatch, object_format: str,
) -> None:
    """A real multi-pack bootstrap switch cannot renew CPU budget or receipt ownership."""
    import json
    import scripts.nautilus_pin_inventory.git_source as git_source

    fixture = GitFixture(tmp_path / f"t7-cpu-switch-{object_format}", object_format=object_format)
    for prefix in ("first", "second"):
        for index in range(4):
            leaf = fixture.root / prefix / f"{index}.txt"
            leaf.parent.mkdir(parents=True, exist_ok=True)
            leaf.write_bytes(f"{prefix}-{index}\n".encode("ascii"))
        fixture._git("add", ".")
        fixture._git("commit", "-qm", prefix)
        fixture._git("repack", "-adf" if prefix == "first" else "-f")
    first_commit = fixture._git("rev-parse", "HEAD~1").decode("ascii").strip()
    commit_oid = fixture._git("rev-parse", "HEAD").decode("ascii").strip()
    assert len(tuple((fixture.root / ".git/objects/pack").glob("*.pack"))) >= 2
    records = tmp_path / f"t7-cpu-{object_format}.jsonl"
    real_git = shutil.which("git")
    assert real_git
    executable = _controlled_executable(
        tmp_path / f"git-t7-cpu-{object_format}",
        "import json, os, pathlib, resource, sys\n"
        "if 'cat-file' in sys.argv and '--batch' in sys.argv:\n"
        "    burn = 0\n"
        "    for value in range(12_000_000): burn += value\n"
        f"    with pathlib.Path({str(records)!r}).open('a', encoding='utf-8') as handle:\n"
        "        handle.write(json.dumps({'pid': os.getpid(), 'rlimit_cpu': resource.getrlimit(resource.RLIMIT_CPU)}) + '\\n')\n"
        f"os.execvpe({real_git!r}, [{real_git!r}, *sys.argv[1:]], os.environ)\n",
    )
    receipt_ids: list[int] = []
    receipt_counts: list[int] = []
    receipt_seconds: list[float] = []
    original_receipt = git_source._BootstrapBatchReader._record_cpu_receipt

    def record_receipt(reader, usage):
        original_receipt(reader, usage)
        accounting = reader._accounting()
        receipt_ids.append(id(accounting))
        receipt_counts.append(accounting.cpu_receipt_count)
        receipt_seconds.append(float(usage.ru_utime) + float(usage.ru_stime))

    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(executable))
    monkeypatch.setattr(git_source._BootstrapBatchReader, "_record_cpu_receipt", record_receipt)
    limits = GitScanLimits(timeout_seconds=10.0)
    runner = git_source._GitRunner(fixture.root, limits)
    deadline = time.monotonic() + limits.timeout_seconds
    runner._persistent_accounting = git_source._PersistentReaderAccounting(deadline=deadline)
    runner._pack_namespace = git_source._freeze_pack_namespace(
        fixture.root / ".git/objects", limits, deadline
    )
    first_oid = fixture._git("rev-parse", f"{first_commit}:first/0.txt").decode("ascii").strip()
    second_oid = fixture._git("rev-parse", f"{commit_oid}:second/0.txt").decode("ascii").strip()
    try:
        assert runner._read_packed_source(
            fixture.root / ".git/objects", first_oid, "blob", object_format, deadline
        ) == ("blob", b"first-0\n")
        first_reader = runner._persistent_reader
        assert first_reader is not None
        assert runner._read_packed_source(
            fixture.root / ".git/objects", second_oid, "blob", object_format, deadline
        ) == ("blob", b"second-0\n")
        second_reader = runner._persistent_reader
        assert second_reader is not None and second_reader.bootstrap is not first_reader.bootstrap
        assert first_reader.closed
        runner._close_persistent_reader()
    finally:
        runner.close(suppress_terminal_error=True)
    receipts = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert len(receipts) == 2
    assert len(set(receipt_ids)) == 1
    assert receipt_counts == [1, 2]
    assert receipt_seconds[0] > 0
    assert receipts[1]["rlimit_cpu"][0] < receipts[0]["rlimit_cpu"][0], (
        receipts,
        receipt_seconds,
    )
