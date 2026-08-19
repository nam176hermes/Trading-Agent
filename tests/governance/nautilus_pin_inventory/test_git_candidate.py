"""Contract tests for immutable, temporary-index candidate Git trees."""

from __future__ import annotations

import os
from dataclasses import replace
import hashlib
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import pytest

import scripts.nautilus_pin_inventory.git_candidate as git_candidate
from scripts.nautilus_pin_inventory.git_candidate import (
    CandidateAuthorityError,
    CandidatePathReceipt,
    GitCandidateTreeBuilder,
    TreeBuildReceipt,
)
from scripts.nautilus_pin_inventory.git_source import GitBlobSnapshot, GitTreeSnapshot


class GitFixture:
    """A disposable repository exercised only through real Git objects."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "candidate@example.invalid")
        self.git("config", "user.name", "Candidate Test")

    def git(self, *arguments: str, input: bytes | None = None) -> bytes:
        completed = subprocess.run(
            ("git", *arguments), cwd=self.root, input=input, capture_output=True, check=False
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
        return completed.stdout

    def commit(self, path: str, data: bytes) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.git("add", "--", path)
        self.git("commit", "-qm", "seed")
        return self.head_oid

    @property
    def head_oid(self) -> str:
        return self.git("rev-parse", "HEAD").decode("ascii").strip()

    @property
    def index_oid(self) -> str:
        return self.git("write-tree").decode("ascii").strip()


@pytest.fixture
def git_fixture(tmp_path: Path) -> GitFixture:
    fixture = GitFixture(tmp_path / "repo")
    fixture.commit("engine.py", b"print('v3')\n")
    return fixture


@pytest.fixture
def private_tmp(tmp_path: Path) -> Path:
    root = tmp_path / "private-tmp"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _builder(git_fixture: GitFixture, private_tmp: Path) -> GitCandidateTreeBuilder:
    return GitCandidateTreeBuilder(
        git_fixture.root,
        expected_parent_commit_oid=git_fixture.head_oid,
        allowed_paths=frozenset({"engine.py", "pin-inventory.json"}),
        temp_root=private_tmp,
    )


def _tree_with_updates(git_fixture: GitFixture, updates: dict[str, bytes]) -> str:
    """Make an adversarial tree through an isolated disposable-repository index."""
    with tempfile.TemporaryDirectory(dir=git_fixture.root) as temporary:
        index = Path(temporary) / "index"
        environment = {**os.environ, "GIT_INDEX_FILE": str(index)}
        def git(*arguments: str, input: bytes | None = None) -> bytes:
            completed = subprocess.run(
                ("git", *arguments), cwd=git_fixture.root, input=input,
                capture_output=True, check=False, env=environment,
            )
            assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
            return completed.stdout
        git("read-tree", "HEAD^{tree}")
        for path, data in updates.items():
            blob_oid = git("hash-object", "-w", "--stdin", input=data).decode().strip()
            git("update-index", "--add", "--cacheinfo", f"100644,{blob_oid},{path}")
        return git("write-tree").decode().strip()


def _replace_index_with_equivalent_different_inode(index: Path) -> None:
    """Atomically exchange an index for byte- and metadata-equivalent foreign state."""
    before = os.stat(index, follow_symlinks=False)
    replacement = index.with_name("replacement-index")
    replacement.write_bytes(index.read_bytes())
    os.chmod(replacement, before.st_mode & 0o7777)
    os.replace(replacement, index)
    after = os.stat(index, follow_symlinks=False)
    assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)


def _mutate_sha1_index_in_place(index: Path) -> None:
    """Change a stat-cache byte while preserving the inode and Git checksum."""
    before = os.stat(index, follow_symlinks=False)
    data = bytearray(index.read_bytes())
    assert data[:4] == b"DIRC"
    assert len(data) > 32
    data[12] ^= 1
    data[-20:] = hashlib.sha1(data[:-20], usedforsecurity=False).digest()
    descriptor = os.open(index, os.O_WRONLY | os.O_CLOEXEC)
    try:
        assert os.pwrite(descriptor, data, 0) == len(data)
        os.ftruncate(descriptor, len(data))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    after = os.stat(index, follow_symlinks=False)
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


def test_builder_creates_t0_and_t1_without_moving_head(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: staging updates the worktree index or moves repository authority."""
    parent = git_fixture.head_oid
    initial_index = git_fixture.index_oid
    builder = _builder(git_fixture, private_tmp)

    t0 = builder.build_source_tree({"engine.py": b"print('v4')\n"})
    final = builder.add_inventory(
        t0,
        path="pin-inventory.json",
        inventory_bytes=b'{"schema":"nautilus-pin-inventory/v4"}\n',
    )

    assert git_fixture.head_oid == parent
    assert git_fixture.index_oid == initial_index
    assert final.source_tree_oid == t0.tree_oid
    assert final.final_tree_oid != t0.tree_oid


def test_t0_rejects_complete_tree_drift_outside_requested_updates(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a substituted write-tree result adds an unowned path to T0."""
    builder = _builder(git_fixture, private_tmp)
    drifted_tree = _tree_with_updates(git_fixture, {
        "engine.py": b"print('v4')\n", "unowned.txt": b"forbidden\n",
    })
    original = builder._run_git

    def substituted_tree(arguments: tuple[str, ...], **kwargs: object) -> object:
        output = original(arguments, **kwargs)  # type: ignore[arg-type]
        return (
            replace(output, stdout=f"{drifted_tree}\n".encode())
            if arguments == ("write-tree",) else output
        )

    monkeypatch.setattr(builder, "_run_git", substituted_tree)
    with pytest.raises(CandidateAuthorityError, match="tree delta|allowed|drift"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})


def test_add_inventory_rejects_forged_t0_with_unreceipted_off_allowlist_path(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: public receipt shape alone blesses a foreign T0 tree."""
    builder = _builder(git_fixture, private_tmp)
    forged = TreeBuildReceipt(
        expected_parent_commit_oid=git_fixture.head_oid,
        expected_parent_tree_oid=git_fixture.git("rev-parse", "HEAD^{tree}").decode().strip(),
        tree_oid=_tree_with_updates(git_fixture, {"unowned.txt": b"forbidden\n"}),
        paths=(),
    )
    with pytest.raises(CandidateAuthorityError, match="T0|receipt|delta"):
        builder.add_inventory(forged, path="pin-inventory.json", inventory_bytes=b"{}\n")


def test_state_capture_never_materializes_the_real_index(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: observation runs write-tree against the caller's real index."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._run_git

    def forbid_real_write_tree(arguments: tuple[str, ...], **kwargs: object) -> bytes:
        if arguments == ("write-tree",) and kwargs.get("index_path") is None:
            raise AssertionError("real worktree index materialization is forbidden")
        return original(arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builder, "_run_git", forbid_real_write_tree)
    builder.build_source_tree({"engine.py": b"print('v4')\n"})


def test_builder_preserves_raw_real_index_bytes(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: state capture changes cache-tree bytes despite an unchanged logical index."""
    index = git_fixture.root / ".git" / "index"
    before = hashlib.sha256(index.read_bytes()).hexdigest()
    _builder(git_fixture, private_tmp).build_source_tree({"engine.py": b"print('v4')\n"})
    assert hashlib.sha256(index.read_bytes()).hexdigest() == before


def test_symbolic_head_and_dirty_worktree_byte_drift_are_rejected(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: OID/status-only state capture misses symbolic HEAD and dirty-byte changes."""
    git_fixture.git("branch", "other", "HEAD")
    (git_fixture.root / "engine.py").write_bytes(b"dirty-before\n")
    builder = _builder(git_fixture, private_tmp)
    original = builder._build_tree

    def drift_after_tree(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        git_fixture.git("symbolic-ref", "HEAD", "refs/heads/other")
        (git_fixture.root / "engine.py").write_bytes(b"dirty-after\n")
        return result

    monkeypatch.setattr(builder, "_build_tree", drift_after_tree)
    with pytest.raises(CandidateAuthorityError, match="HEAD|worktree|changed"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})


def test_wrong_parent_object_type_and_nonzero_subprocess_fail_closed(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a blob parent or nonzero Git child creates an authority result."""
    blob_oid = git_fixture.git("rev-parse", "HEAD:engine.py").decode().strip()
    with pytest.raises(CandidateAuthorityError, match="parent"):
        GitCandidateTreeBuilder(
            git_fixture.root, expected_parent_commit_oid=blob_oid,
            allowed_paths=frozenset({"engine.py", "pin-inventory.json"}), temp_root=private_tmp,
        )
    builder = _builder(git_fixture, private_tmp)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 17, b"", b"failure"),
    )
    with pytest.raises(CandidateAuthorityError, match="command failed"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})


def test_cleanup_retains_unexpected_content_evidence(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: cleanup deletes an operation directory containing unexpected evidence."""
    builder = _builder(git_fixture, private_tmp)
    operation = builder._new_operation()
    descriptor = os.open("foreign", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=operation.fd)
    os.close(descriptor)
    operation_path = private_tmp / operation.name
    with pytest.raises(CandidateAuthorityError, match="unexpected contents|evidence"):
        builder._cleanup_operation(operation, None)
    assert (operation_path / "foreign").exists()


def test_post_write_tree_index_mode_drift_is_rejected_and_retained(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: final cleanup re-baselines a mode-mutated private index then deletes it."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._run_git

    def chmod_after_write_tree(arguments: tuple[str, ...], **kwargs: object) -> bytes:
        output = original(arguments, **kwargs)  # type: ignore[arg-type]
        if arguments == ("write-tree",):
            index = kwargs["index_path"]
            assert isinstance(index, Path)
            os.chmod(index, 0o666)
        return output

    monkeypatch.setattr(builder, "_run_git", chmod_after_write_tree)
    with pytest.raises(CandidateAuthorityError, match="index|cleanup|identity"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert tuple(private_tmp.iterdir()), "mode-mutated index evidence must be retained"


def test_post_write_tree_index_replacement_is_rejected_and_retained(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a replaced final private index cannot receive cleanup authority."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._run_git
    replaced = False

    def replace_after_first_write_tree(arguments: tuple[str, ...], **kwargs: object) -> bytes:
        nonlocal replaced
        output = original(arguments, **kwargs)  # type: ignore[arg-type]
        if arguments == ("write-tree",) and not replaced:
            replaced = True
            index = kwargs["index_path"]
            assert isinstance(index, Path)
            index.unlink()
            index.write_bytes(b"foreign-index\n")
        return output

    monkeypatch.setattr(builder, "_run_git", replace_after_first_write_tree)
    with pytest.raises(CandidateAuthorityError, match="command failed|index|cleanup|identity"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert tuple(private_tmp.iterdir()), "replaced index evidence must be retained"


def test_off_allowlist_dirty_and_untracked_worktree_bytes_are_observed(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: status-stable foreign worktree bytes can change during candidate construction."""
    (git_fixture.root / "outside.txt").write_bytes(b"untracked-before\n")
    (git_fixture.root / "other.py").write_bytes(b"dirty-before\n")
    git_fixture.git("add", "other.py")
    git_fixture.git("commit", "-qm", "other")
    (git_fixture.root / "other.py").write_bytes(b"dirty-before\n")
    builder = _builder(git_fixture, private_tmp)
    original = builder._build_tree

    def drift_outside(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        (git_fixture.root / "outside.txt").write_bytes(b"untracked-after\n")
        (git_fixture.root / "other.py").write_bytes(b"dirty-after\n")
        return result

    monkeypatch.setattr(builder, "_build_tree", drift_outside)
    with pytest.raises(CandidateAuthorityError, match="worktree|changed"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})


def test_reopened_candidate_blob_corruption_fails_closed(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: corrupted bytes returned while reopening a candidate tree receive a receipt."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._snapshot_tree

    def corrupt_blob(tree_oid: str) -> GitTreeSnapshot:
        snapshot = original(tree_oid)
        if snapshot.tree_oid == tree_oid:
            blob = snapshot.blobs[0]
            return replace(snapshot, blobs=(
                GitBlobSnapshot(blob.path, blob.mode, blob.blob_oid, blob.sha256, b"corrupt\n"),
            ))
        return snapshot

    monkeypatch.setattr(builder, "_snapshot_tree", corrupt_blob)
    with pytest.raises(CandidateAuthorityError, match="receipt|bytes|delta"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})


@pytest.mark.parametrize("mutation", ("mode", "hardlink", "symlink"))
def test_cleanup_rejects_unsafe_private_index_forms(
    git_fixture: GitFixture, private_tmp: Path, mutation: str
) -> None:
    """Break caught: cleanup unlinks an index whose mode, link count, or type changed."""
    builder = _builder(git_fixture, private_tmp)
    operation = builder._new_operation()
    builder._run_git(
        ("read-tree", git_fixture.git("rev-parse", "HEAD^{tree}").decode().strip()),
        index_path=operation.index_path, index_fd=operation.fd,
    )
    expected = builder._index_identity(operation.index_path)
    operation_path = private_tmp / operation.name
    if mutation == "mode":
        os.chmod(operation.index_path, 0o666)
    elif mutation == "hardlink":
        os.link(operation.index_path, operation_path / "index-copy")
    else:
        operation.index_path.rename(operation_path / "real-index")
        operation.index_path.symlink_to("real-index")
    with pytest.raises(CandidateAuthorityError, match="index|cleanup|identity|unexpected"):
        builder._cleanup_operation(operation, expected)
    assert operation_path.exists(), "unsafe index evidence must be retained"


def test_post_update_index_replacement_is_not_rebaselined(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: an index swapped after update-index becomes trusted as the next baseline."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._run_git

    def swap_after_update(arguments: tuple[str, ...], **kwargs: object) -> bytes:
        output = original(arguments, **kwargs)  # type: ignore[arg-type]
        if arguments[:2] == ("update-index", "--add"):
            index_fd = kwargs["index_fd"]
            assert isinstance(index_fd, int)
            blob_oid = git_fixture.git("hash-object", "-w", "--stdin", input=b"evil\n").decode().strip()
            completed = subprocess.run(
                ("git", "update-index", "--add", "--cacheinfo", f"100644,{blob_oid},evil.py"),
                cwd=git_fixture.root, capture_output=True, check=False,
                env={**os.environ, "GIT_INDEX_FILE": f"/proc/self/fd/{index_fd}/index"},
                pass_fds=(index_fd,),
            )
            assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
        return output

    monkeypatch.setattr(builder, "_run_git", swap_after_update)
    with pytest.raises(CandidateAuthorityError, match="index|tree delta|drift"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})


def test_equivalent_index_swap_after_update_index_is_rejected_and_retained(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a same-byte new inode after update-index becomes trusted state."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._run_git
    replaced = False

    def replace_after_update(arguments: tuple[str, ...], **kwargs: object) -> bytes:
        nonlocal replaced
        output = original(arguments, **kwargs)  # type: ignore[arg-type]
        if arguments[:2] == ("update-index", "--add") and not replaced:
            replaced = True
            index = kwargs["index_path"]
            assert isinstance(index, Path)
            _replace_index_with_equivalent_different_inode(index)
        return output

    monkeypatch.setattr(builder, "_run_git", replace_after_update)
    with pytest.raises(CandidateAuthorityError, match="index|replaced|identity"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert tuple(private_tmp.iterdir()), "equivalent replacement evidence must be retained"


def test_equivalent_index_swap_after_first_write_tree_is_rejected_and_retained(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a same-byte new inode after write-tree becomes cleanup authority."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._run_git
    replaced = False

    def replace_after_first_write_tree(arguments: tuple[str, ...], **kwargs: object) -> bytes:
        nonlocal replaced
        output = original(arguments, **kwargs)  # type: ignore[arg-type]
        if arguments == ("write-tree",) and not replaced:
            replaced = True
            index = kwargs["index_path"]
            assert isinstance(index, Path)
            _replace_index_with_equivalent_different_inode(index)
        return output

    monkeypatch.setattr(builder, "_run_git", replace_after_first_write_tree)
    with pytest.raises(CandidateAuthorityError, match="index|replaced|identity"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert tuple(private_tmp.iterdir()), "equivalent replacement evidence must be retained"


def test_equivalent_index_swap_at_lower_update_index_boundary_is_rejected_and_retained(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a swap after Git exits but before its receipt is sampled is trusted."""
    builder = _builder(git_fixture, private_tmp)
    original = subprocess.run
    replaced = False

    def replace_before_run_returns(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal replaced
        completed = original(*args, **kwargs)  # type: ignore[arg-type]
        command = args[0]
        environment = kwargs.get("env")
        if (
            not replaced
            and isinstance(command, tuple)
            and command[1:3] == ("update-index", "--add")
            and isinstance(environment, dict)
        ):
            replaced = True
            _replace_index_with_equivalent_different_inode(Path(environment["GIT_INDEX_FILE"]))
        return completed

    monkeypatch.setattr(subprocess, "run", replace_before_run_returns)
    with pytest.raises(CandidateAuthorityError, match="index|replaced|identity"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert replaced, "the lower update-index command boundary must be exercised"
    assert tuple(private_tmp.iterdir()), "lower-boundary replacement evidence must be retained"


def test_equivalent_index_swap_at_lower_write_tree_boundary_is_rejected_and_retained(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a post-write swap before receipt capture becomes cleanup authority."""
    builder = _builder(git_fixture, private_tmp)
    original = subprocess.run
    replaced = False

    def replace_before_run_returns(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal replaced
        completed = original(*args, **kwargs)  # type: ignore[arg-type]
        command = args[0]
        environment = kwargs.get("env")
        if (
            not replaced
            and isinstance(command, tuple)
            and command[1:] == ("write-tree",)
            and isinstance(environment, dict)
        ):
            replaced = True
            _replace_index_with_equivalent_different_inode(Path(environment["GIT_INDEX_FILE"]))
        return completed

    monkeypatch.setattr(subprocess, "run", replace_before_run_returns)
    with pytest.raises(CandidateAuthorityError, match="index|replaced|identity"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert replaced, "the lower write-tree command boundary must be exercised"
    assert tuple(private_tmp.iterdir()), "lower-boundary replacement evidence must be retained"


def test_same_inode_valid_index_write_at_lower_child_boundary_is_rejected_and_retained(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: post-child same-inode writes become the command's trusted receipt."""
    builder = _builder(git_fixture, private_tmp)
    original = subprocess.run
    mutated = False

    def mutate_before_run_returns(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal mutated
        completed = original(*args, **kwargs)  # type: ignore[arg-type]
        command = args[0]
        environment = kwargs.get("env")
        if (
            not mutated
            and isinstance(command, tuple)
            and command[1:3] == ("update-index", "--add")
            and isinstance(environment, dict)
        ):
            mutated = True
            _mutate_sha1_index_in_place(Path(environment["GIT_INDEX_FILE"]))
        return completed

    monkeypatch.setattr(subprocess, "run", mutate_before_run_returns)
    with pytest.raises(CandidateAuthorityError, match="event|write|changed|index"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert mutated, "the lower child-exit boundary must be exercised"
    assert tuple(private_tmp.iterdir()), "same-inode mutation evidence must be retained"


def test_linux_inotify_coalesces_distinct_same_uid_index_lock_writers(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Blocker proof: exact lock events carry no same-UID writer provenance."""
    seed_index = private_tmp / "seed-index"
    completed = subprocess.run(
        ("git", "read-tree", "HEAD^{tree}"),
        cwd=git_fixture.root,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_INDEX_FILE": str(seed_index)},
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    original_bytes = seed_index.read_bytes()
    operation = private_tmp / "coalescing-proof"
    operation.mkdir(mode=0o700)
    operation_fd = os.open(operation, os.O_RDONLY | os.O_DIRECTORY)
    watch = git_candidate._IndexNamespaceWatch.open(operation_fd)
    writer_fd = os.open(
        "index.lock",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
        dir_fd=operation_fd,
    )
    try:
        assert os.write(writer_fd, original_bytes) == len(original_bytes)
        attacker = subprocess.run(
            (
                sys.executable,
                "-c",
                "import hashlib,os,sys; p=sys.argv[1]; d=bytearray(open(p,'rb').read()); "
                "d[12]^=1; d[-20:]=hashlib.sha1(d[:-20],usedforsecurity=False).digest(); "
                "f=os.open(p,os.O_WRONLY); os.pwrite(f,d,0); os.fsync(f); os.close(f)",
                str(operation / "index.lock"),
            ),
            capture_output=True,
            check=False,
        )
        assert attacker.returncode == 0, attacker.stderr.decode("utf-8", errors="replace")
    finally:
        os.close(writer_fd)
    os.rename("index.lock", "index", src_dir_fd=operation_fd, dst_dir_fd=operation_fd)
    events = watch.drain()
    watch.close()
    os.close(operation_fd)

    assert tuple((event.mask, event.name) for event in events) == (
        (git_candidate._IN_CREATE, b"index.lock"),
        (git_candidate._IN_MODIFY, b"index.lock"),
        (git_candidate._IN_CLOSE_WRITE, b"index.lock"),
        (git_candidate._IN_MOVED_FROM, b"index.lock"),
        (git_candidate._IN_MOVED_TO, b"index"),
    )
    assert events[-2].cookie != 0 and events[-1].cookie == events[-2].cookie
    assert (operation / "index").read_bytes() != original_bytes
    verified = subprocess.run(
        ("git", "write-tree"),
        cwd=git_fixture.root,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_INDEX_FILE": str(operation / "index")},
    )
    assert verified.returncode == 0, verified.stderr.decode("utf-8", errors="replace")


@pytest.mark.parametrize(("shared_mode", "expected_mode"), (("0600", 0o600), ("0640", 0o640)))
def test_causally_configured_shared_repository_index_attrib_is_accepted(
    git_fixture: GitFixture,
    private_tmp: Path,
    shared_mode: str,
    expected_mode: int,
) -> None:
    """Break caught: the exact safe Git chmod event is rejected as foreign activity."""
    git_fixture.git("config", "core.sharedRepository", shared_mode)
    builder = _builder(git_fixture, private_tmp)

    builder.build_source_tree({"engine.py": b"print('v4')\n"})

    retained = tuple(private_tmp.glob("*-retained-*"))
    assert len(retained) == 1
    assert stat.S_IMODE(os.stat(retained[0] / "index", follow_symlinks=False).st_mode) == expected_mode


def test_retention_destination_collision_is_noreplace_and_preserves_foreign_inode(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: cleanup rename replaces a raced foreign empty directory."""
    builder = _builder(git_fixture, private_tmp)
    operation = builder._new_operation()
    result = builder._indexed_result(builder._run_git(
        ("read-tree", git_fixture.git("rev-parse", "HEAD^{tree}").decode().strip()),
        index_path=operation.index_path, index_fd=operation.fd,
    ))
    original = getattr(GitCandidateTreeBuilder, "_rename_noreplace", None)
    collided_name: str | None = None
    foreign_fd: int | None = None

    def collide_then_rename(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal collided_name, foreign_fd
        if collided_name is None:
            collided_name = destination
            os.mkdir(destination, 0o700, dir_fd=dst_dir_fd)
            foreign_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dst_dir_fd)
        assert original is not None
        original(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(
        GitCandidateTreeBuilder,
        "_rename_noreplace",
        staticmethod(collide_then_rename),
        raising=False,
    )
    builder._cleanup_operation(operation, result.index_identity)

    assert collided_name is not None, "cleanup must use the no-replace primitive"
    assert foreign_fd is not None
    try:
        foreign = os.fstat(foreign_fd)
        named = os.stat(private_tmp / collided_name, follow_symlinks=False)
        assert foreign.st_nlink > 0
        assert (named.st_dev, named.st_ino) == (foreign.st_dev, foreign.st_ino)
    finally:
        os.close(foreign_fd)


def test_retention_entry_cap_fails_before_new_operation_and_never_evicts(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: normal success grows retained evidence past its explicit entry cap."""
    policy = git_candidate.RetentionPolicy(
        max_entries=1,
        max_bytes=1_048_576,
        operation_reserve_bytes=65_536,
        minimum_free_bytes=0,
    )
    builder = GitCandidateTreeBuilder(
        git_fixture.root,
        expected_parent_commit_oid=git_fixture.head_oid,
        allowed_paths=frozenset({"engine.py", "pin-inventory.json"}),
        temp_root=private_tmp,
        retention_policy=policy,
    )
    builder.build_source_tree({"engine.py": b"print('v4')\n"})
    before = {
        path.name: (os.stat(path, follow_symlinks=False).st_dev, os.stat(path, follow_symlinks=False).st_ino)
        for path in private_tmp.iterdir()
    }

    with pytest.raises(CandidateAuthorityError, match="retained evidence entry capacity"):
        builder.build_source_tree({"engine.py": b"print('v5')\n"})

    after = {
        path.name: (os.stat(path, follow_symlinks=False).st_dev, os.stat(path, follow_symlinks=False).st_ino)
        for path in private_tmp.iterdir()
    }
    assert after == before
    assert len(builder.retained_evidence) == 1
    handoff = builder.retained_evidence[0]
    retained_index = private_tmp / handoff.directory_name / "index"
    assert handoff.directory_name in before
    assert handoff.index_bytes == retained_index.stat().st_size
    assert handoff.index_sha256 == hashlib.sha256(retained_index.read_bytes()).hexdigest()


def test_retention_byte_reservation_counts_foreign_evidence_without_deleting_it(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: construction ignores the byte cap or reclaims foreign evidence."""
    foreign = private_tmp / "foreign-evidence"
    foreign.write_bytes(b"12345")
    before = os.stat(foreign, follow_symlinks=False)
    builder = GitCandidateTreeBuilder(
        git_fixture.root,
        expected_parent_commit_oid=git_fixture.head_oid,
        allowed_paths=frozenset({"engine.py", "pin-inventory.json"}),
        temp_root=private_tmp,
        retention_policy=git_candidate.RetentionPolicy(
            max_entries=10,
            max_bytes=8,
            operation_reserve_bytes=4,
            minimum_free_bytes=0,
        ),
    )

    with pytest.raises(CandidateAuthorityError, match="retained evidence byte capacity"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})

    after = os.stat(foreign, follow_symlinks=False)
    assert foreign.read_bytes() == b"12345"
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


def test_cleanup_never_unlinks_a_swap_after_final_identity_check(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: cleanup verifies one inode and unlinks a replacement by pathname."""
    builder = _builder(git_fixture, private_tmp)
    operation = builder._new_operation()
    result = builder._indexed_result(builder._run_git(
        ("read-tree", git_fixture.git("rev-parse", "HEAD^{tree}").decode().strip()),
        index_path=operation.index_path, index_fd=operation.fd,
    ))
    original = GitCandidateTreeBuilder._index_identity
    swapped = False

    def swap_after_identity(index: Path):
        nonlocal swapped
        identity = original(index)
        if not swapped:
            swapped = True
            _replace_index_with_equivalent_different_inode(index)
        return identity

    monkeypatch.setattr(GitCandidateTreeBuilder, "_index_identity", staticmethod(swap_after_identity))
    with pytest.raises(CandidateAuthorityError, match="cleanup|identity|retained|index"):
        builder._cleanup_operation(operation, result.index_identity)
    assert swapped, "the identity-check-to-unlink seam must be exercised"
    evidence = tuple(private_tmp.iterdir())
    assert evidence, "replacement evidence must not be deleted"
    assert any((path / "index").exists() for path in evidence), "the replacement must be retained"


def test_cleanup_syscall_failure_retains_verified_index_evidence(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: an atomic-retention failure is ignored or destroys evidence."""
    builder = _builder(git_fixture, private_tmp)
    operation = builder._new_operation()
    result = builder._indexed_result(builder._run_git(
        ("read-tree", git_fixture.git("rev-parse", "HEAD^{tree}").decode().strip()),
        index_path=operation.index_path, index_fd=operation.fd,
    ))
    operation_path = private_tmp / operation.name
    original = builder._rename_noreplace

    def fail_operation_retention(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if source == operation.name and src_dir_fd == operation.parent_fd:
            raise OSError("injected retention failure")
        original(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(
        GitCandidateTreeBuilder,
        "_rename_noreplace",
        staticmethod(fail_operation_retention),
    )
    with pytest.raises(CandidateAuthorityError, match="cleanup|retained|confirmed"):
        builder._cleanup_operation(operation, result.index_identity)
    assert operation_path.exists(), "cleanup failure must retain the operation directory"
    assert (operation_path / "index").exists(), "cleanup failure must retain index evidence"


def test_same_oid_non_head_symbolic_ref_retarget_is_rejected(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: ref observation records resolved OIDs but omits symbolic targets."""
    git_fixture.git("branch", "first", "HEAD")
    git_fixture.git("branch", "second", "HEAD")
    git_fixture.git("symbolic-ref", "refs/heads/alias", "refs/heads/first")
    builder = _builder(git_fixture, private_tmp)
    original = builder._build_tree

    def retarget_after_tree(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        git_fixture.git("symbolic-ref", "refs/heads/alias", "refs/heads/second")
        return result

    monkeypatch.setattr(builder, "_build_tree", retarget_after_tree)
    with pytest.raises(CandidateAuthorityError, match="ref|repository.*changed"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
    assert (
        git_fixture.git("symbolic-ref", "refs/heads/alias").decode().strip()
        == "refs/heads/second"
    )


def test_cleanup_retains_replaced_operation_directory_evidence(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: cleanup deletes a same-owner replacement instead of retained evidence."""
    builder = _builder(git_fixture, private_tmp)
    operation = builder._new_operation()
    index = operation.index_path
    builder._run_git(
        ("read-tree", git_fixture.git("rev-parse", "HEAD^{tree}").decode().strip()),
        index_path=index, index_fd=operation.fd,
    )
    displaced = private_tmp / "displaced"
    operation_path = private_tmp / operation.name
    operation_path.rename(displaced)
    operation_path.mkdir(mode=0o700)
    os.chmod(operation_path, 0o700)
    (operation_path / "index").write_bytes(b"replacement evidence\n")
    with pytest.raises(CandidateAuthorityError, match="cleanup|identity|evidence"):
        builder._cleanup_operation(operation, None)
    assert operation_path.exists(), "replacement evidence must be retained"


def test_update_outside_allowed_set_fails_before_hash_object(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: an unowned path can be written into a candidate object tree."""
    builder = _builder(git_fixture, private_tmp)

    def must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("hash-object must not run for a rejected path")

    monkeypatch.setattr(builder, "_run_git", must_not_run)
    with pytest.raises(CandidateAuthorityError, match="outside allowed path set"):
        builder.build_source_tree({"unowned.txt": b"forbidden\n"})


@pytest.mark.parametrize("oid", ("HEAD", "0" * 39, "f" * 41))
def test_wrong_parent_oid_is_rejected(git_fixture: GitFixture, private_tmp: Path, oid: str) -> None:
    """Break caught: a ref, abbreviated OID, or malformed parent selects mutable authority."""
    with pytest.raises(CandidateAuthorityError, match="parent|OID"):
        GitCandidateTreeBuilder(
            git_fixture.root, expected_parent_commit_oid=oid,
            allowed_paths=frozenset({"engine.py", "pin-inventory.json"}), temp_root=private_tmp,
        )


@pytest.mark.parametrize("path", ("../engine.py", "dir//engine.py", "e\u0301.py", "line\nbreak.py"))
def test_noncanonical_or_duplicate_update_paths_are_rejected(
    git_fixture: GitFixture, private_tmp: Path, path: str
) -> None:
    """Break caught: ambiguous path spelling reaches Git's index parser."""
    builder = _builder(git_fixture, private_tmp)
    with pytest.raises(CandidateAuthorityError, match="path"):
        builder.build_source_tree({path: b"x\n"})


def test_bad_receipt_mode_and_sha_are_rejected() -> None:
    """Break caught: a non-regular mode or malformed independent digest is receipted."""
    with pytest.raises(CandidateAuthorityError, match="mode"):
        CandidatePathReceipt("engine.py", 0o120000, "0" * 40, "0" * 64)
    with pytest.raises(CandidateAuthorityError, match="SHA-256"):
        CandidatePathReceipt("engine.py", 0o100644, "0" * 40, "not-a-digest")


def test_inventory_cannot_be_included_in_t0_updates(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: generated inventory recursively becomes an input to itself."""
    builder = _builder(git_fixture, private_tmp)
    with pytest.raises(CandidateAuthorityError, match="inventory"):
        builder.build_source_tree({"pin-inventory.json": b"{}\n"})


def test_add_inventory_rejects_t0_receipt_for_another_parent(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: T1 is built from a tree receipt outside this parent's authority."""
    builder = _builder(git_fixture, private_tmp)
    t0 = builder.build_source_tree({"engine.py": b"print('v4')\n"})
    with pytest.raises(CandidateAuthorityError, match="T0"):
        builder.add_inventory(
            replace(t0, expected_parent_commit_oid="0" * len(t0.expected_parent_commit_oid)),
            path="pin-inventory.json", inventory_bytes=b"{}\n",
        )


def test_t1_has_no_path_drift_outside_inventory(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: adding the inventory also changes an unrelated source blob."""
    builder = _builder(git_fixture, private_tmp)
    t0 = builder.build_source_tree({"engine.py": b"print('v4')\n"})
    final = builder.add_inventory(t0, path="pin-inventory.json", inventory_bytes=b"{}\n")
    assert tuple(receipt.path for receipt in final.paths) == ("engine.py", "pin-inventory.json")
    assert final.paths[0].sha256 == "3e8829ec1b0420b792623556e438ac81576bff51140ca5bb77f4ba8620c01a1c"


def test_private_root_mode_owner_and_ancestor_are_validated(
    git_fixture: GitFixture, tmp_path: Path
) -> None:
    """Break caught: a group-writable or nonprivate namespace hosts the temporary index."""
    unsafe_root = tmp_path / "unsafe"
    unsafe_root.mkdir(mode=0o755)
    os.chmod(unsafe_root, 0o755)
    with pytest.raises(CandidateAuthorityError, match="temporary root"):
        GitCandidateTreeBuilder(
            git_fixture.root, expected_parent_commit_oid=git_fixture.head_oid,
            allowed_paths=frozenset({"engine.py"}), temp_root=unsafe_root,
        )


def test_symlinked_private_root_is_rejected(
    git_fixture: GitFixture, private_tmp: Path, tmp_path: Path
) -> None:
    """Break caught: resolving a symlink hides an attacker-controlled temporary-root name."""
    link = tmp_path / "private-link"
    link.symlink_to(private_tmp, target_is_directory=True)
    with pytest.raises(CandidateAuthorityError, match="temporary root.*symlink"):
        GitCandidateTreeBuilder(
            git_fixture.root, expected_parent_commit_oid=git_fixture.head_oid,
            allowed_paths=frozenset({"engine.py", "pin-inventory.json"}), temp_root=link,
        )


def test_temporary_index_replacement_fails_closed(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a replacement index is accepted after read-tree established its identity."""
    builder = _builder(git_fixture, private_tmp)
    original = builder._run_git

    def replace_index(arguments: tuple[str, ...], **kwargs: object) -> bytes:
        output = original(arguments, **kwargs)  # type: ignore[arg-type]
        if arguments == ("hash-object", "-w", "--stdin"):
            index = kwargs["index_path"]
            assert isinstance(index, Path)
            index.unlink()
            index.write_bytes(b"replacement index\n")
        return output

    monkeypatch.setattr(builder, "_run_git", replace_index)
    with pytest.raises(CandidateAuthorityError, match="temporary index was replaced"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})


def test_worktree_mutation_after_byte_capture_does_not_change_t0(
    git_fixture: GitFixture, private_tmp: Path
) -> None:
    """Break caught: a builder reopens a mutable worktree pathname after receiving bytes."""
    builder = _builder(git_fixture, private_tmp)
    captured = b"print('captured')\n"
    (git_fixture.root / "engine.py").write_bytes(b"print('mutated worktree')\n")
    t0 = builder.build_source_tree({"engine.py": captured})
    assert t0.paths[0].sha256 == "0b8c0441e724d096e0c12737b37e1b13f3757972d5afce4702011e2887ad75fb"


def test_subprocess_failure_and_timeout_fail_closed(
    git_fixture: GitFixture, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a failed or hung Git command yields an accepted partial tree."""
    builder = _builder(git_fixture, private_tmp)

    def timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(("git",), 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(CandidateAuthorityError, match="timed out"):
        builder.build_source_tree({"engine.py": b"print('v4')\n"})
