"""Regression controls proving source bytes come from exact Git objects."""

from __future__ import annotations

from scripts.nautilus_pin_inventory.git_source import GitTreeSnapshot

from test_git_source import GitFixture


def test_git_source_leaf_swap_cannot_change_exact_tree_bytes(tmp_path) -> None:
    """Break caught: a changed worktree leaf changes an already selected Git tree snapshot."""
    fixture = GitFixture(tmp_path / "repo")
    commit_oid, expected = fixture.commit_file("pin.md", b"1.227.0\n")
    (fixture.root / "pin.md").write_bytes(b"1.231.0\n")

    assert GitTreeSnapshot.from_commit(fixture.root, commit_oid).blob("pin.md").data == expected


def test_git_source_parent_swap_cannot_change_exact_tree_bytes(tmp_path) -> None:
    """Break caught: swapping a worktree parent changes an exact Git tree snapshot."""
    fixture = GitFixture(tmp_path / "repo")
    commit_oid, expected = fixture.commit_file("nested/pin.md", b"1.227.0\n")
    fixture.replace_worktree_parent("nested", b"1.231.0\n")

    assert GitTreeSnapshot.from_commit(fixture.root, commit_oid).blob("nested/pin.md").data == expected


def test_moving_branch_cannot_change_exact_commit_snapshot(tmp_path) -> None:
    """Break caught: a branch move changes a snapshot selected by a full commit OID."""
    fixture = GitFixture(tmp_path / "repo")
    commit_oid, expected = fixture.commit_file("pin.md", b"1.227.0\n")
    fixture.move_head_to_new_commit()

    snapshot = GitTreeSnapshot.from_commit(fixture.root, commit_oid)
    assert snapshot.commit_oid == commit_oid
    assert snapshot.blob("pin.md").data == expected
