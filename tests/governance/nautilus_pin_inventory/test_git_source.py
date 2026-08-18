"""Contract tests for immutable Git-tree source custody."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
import hashlib

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


@pytest.mark.parametrize("operation", ("replace", "corrupt", "late-unrelated"))
def test_pinned_pack_source_drift_is_rejected_but_late_unrelated_pack_is_ignored(git_fixture: GitFixture, tmp_path: Path, monkeypatch, operation: str) -> None:
    """Break caught: a used pack can change after bootstrap, or a late unrelated pack changes the exact closure."""
    commit_oid, expected = git_fixture.commit_file("pin.md", b"packed authority\n")
    git_fixture._git("gc", "--prune=now")
    pack = next((git_fixture.root / ".git/objects/pack").glob("*.pack"))
    real_git = shutil.which("git")
    assert real_git
    wrapper = _controlled_executable(
        tmp_path / "git-pack-race",
        "import os, pathlib, subprocess, sys\n"
        f"pack = pathlib.Path({str(pack)!r})\n"
        f"operation = {operation!r}\n"
        f"result = subprocess.run([{real_git!r}, *sys.argv[1:]], input=sys.stdin.buffer.read(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "if 'cat-file' in sys.argv and '--batch' in sys.argv:\n"
        "    if operation == 'replace':\n"
        "        replacement = pack.with_name('replacement.pack'); replacement.write_bytes(pack.read_bytes()); replacement.replace(pack)\n"
        "    elif operation == 'corrupt':\n"
        "        os.chmod(pack, 0o600); pack.write_bytes(b'corrupt')\n"
        "    else:\n"
        "        pack.with_name('late-unrelated.pack').write_bytes(b'unrelated')\n"
        "sys.stdout.buffer.write(result.stdout); sys.stderr.buffer.write(result.stderr); raise SystemExit(result.returncode)",
    )
    import scripts.nautilus_pin_inventory.git_source as git_source
    monkeypatch.setattr(git_source.shutil, "which", lambda _name: str(wrapper))

    if operation == "late-unrelated":
        assert GitTreeSnapshot.from_commit(git_fixture.root, commit_oid).blob("pin.md").data == expected
    else:
        with pytest.raises(GitAuthorityError, match="Git pack changed|bootstrap Git object|primary pack bootstrap"):
            GitTreeSnapshot.from_commit(git_fixture.root, commit_oid)


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


def test_temporary_directory_failure_closes_all_captured_descriptors(git_fixture: GitFixture, monkeypatch) -> None:
    """Break caught: a tempdir failure after multi-directory capture leaks source-store descriptors."""
    import scripts.nautilus_pin_inventory.git_source as git_source

    commit_oid, _ = git_fixture.commit_file("nested/deeper/pin.md", b"x")
    before = len(tuple(Path("/proc/self/fd").iterdir()))

    class TempdirFailure:
        def __init__(self, *args, **kwargs) -> None:
            raise OSError("no temporary directory")

    monkeypatch.setattr(git_source.tempfile, "TemporaryDirectory", TempdirFailure)
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

    signals: list[int] = []
    monkeypatch.setattr(git_source.os, "killpg", lambda pgid, _signal: signals.append(pgid))
    with pytest.raises(GitAuthorityError, match="failed"):
        runner.run(("probe",))
    assert len(signals) == 1


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
    original = git_source._GitRunner._read_bootstrap_object
    injected = False

    def inject_after_commit(self, bootstrap, oid, expected_type, object_format):
        nonlocal injected
        result = original(self, bootstrap, oid, expected_type, object_format)
        if not injected and expected_type == "commit":
            injected = True
            target = bootstrap.destination / tree_oid[:2] / tree_oid[2:]
            target.parent.mkdir(mode=0o700)
            target.write_bytes(zlib.compress(b"tree " + str(len(tree_raw)).encode("ascii") + b"\0" + tree_raw))
        return result

    git_fixture._git("gc", "--prune=now")
    monkeypatch.setattr(git_source._GitRunner, "_read_bootstrap_object", inject_after_commit)
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
