from __future__ import annotations

import os
import shutil
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from services.operator_control import protected_fs
from services.operator_control.protected_fs import ProtectedFilesystemError
from services.operator_control.state_store import OperatorStateStore
from tests.hwc.fixtures.operator_state import (
    intent_for,
    provision_operator_state,
    write_private,
)


def test_state_reader_preserves_missing_and_valid_source_semantics(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    missing = store.read_state()
    assert (missing.requested_mode, missing.kill_switch_state) == (
        "UNKNOWN",
        "INACTIVE",
    )

    write_private(paths.mode_path, b"paper\n")
    write_private(paths.kill_switch_path, b"2026-09-02T12:00:00Z: incident\n")
    state = store.read_state()
    assert state.requested_mode == "PAPER"
    assert state.kill_switch_state == "ACTIVE"
    assert state.kill_switch_reason == "incident"
    assert state.kill_switch_activated_at.isoformat() == "2026-09-02T12:00:00+00:00"
    assert len(state.state_sha256) == 64


@pytest.mark.parametrize(
    "value",
    (b"Paper\n", b"paper", b"paper\nextra\n", b"x" * 129, b"\xff\n"),
)
def test_malformed_mode_is_unknown(tmp_path: Path, value: bytes) -> None:
    paths = provision_operator_state(tmp_path)
    write_private(paths.mode_path, value)
    assert OperatorStateStore(paths).read_state().requested_mode == "UNKNOWN"


@pytest.mark.parametrize(
    "value",
    (
        b"bad\n",
        b"2026-09-02T12:00:00Z: \n",
        b"2026-09-02T12:00:00Z: a\nextra\n",
        b"x" * 1025,
        b"\xff\n",
    ),
)
def test_malformed_kill_switch_is_unknown(tmp_path: Path, value: bytes) -> None:
    paths = provision_operator_state(tmp_path)
    write_private(paths.kill_switch_path, value)
    assert OperatorStateStore(paths).read_state().kill_switch_state == "UNKNOWN"


@pytest.mark.parametrize("kind", ("symlink", "directory", "fifo", "socket"))
def test_non_regular_state_objects_are_unknown(tmp_path: Path, kind: str) -> None:
    cleanup: Path | None = None
    if kind == "socket":
        cleanup = Path(tempfile.mkdtemp(prefix="hwc-socket-", dir="/tmp"))
        tmp_path = cleanup
    paths = provision_operator_state(tmp_path)
    target = paths.mode_path
    if kind == "symlink":
        target.symlink_to("/dev/null")
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(os.fspath(target))
    try:
        assert OperatorStateStore(paths).read_state().requested_mode == "UNKNOWN"
    finally:
        if kind == "socket":
            sock.close()
        if cleanup is not None:
            shutil.rmtree(cleanup)


def test_private_file_metadata_rejects_devices_and_wrong_owner() -> None:
    device = os.stat("/dev/null", follow_symlinks=False)
    with pytest.raises(ProtectedFilesystemError):
        protected_fs.require_private_regular_file(device, max_bytes=128)
    regular = list(device)
    regular[0] = stat.S_IFREG | 0o600
    regular[4] = os.geteuid() + 1
    with pytest.raises(ProtectedFilesystemError):
        protected_fs.require_private_regular_file(
            os.stat_result(regular), max_bytes=128
        )


def test_group_or_other_permissions_make_state_unknown(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    write_private(paths.mode_path, b"paper\n")
    paths.mode_path.chmod(0o640)
    assert OperatorStateStore(paths).read_state().requested_mode == "UNKNOWN"


def test_symlink_command_directory_is_rejected(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    real = paths.command_root.rename(paths.data_root / "commands-real")
    paths.command_root.symlink_to(real)
    with pytest.raises(ProtectedFilesystemError):
        protected_fs.open_private_directory(paths.command_root)


def test_ancestor_rename_during_mode_replace_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    original = protected_fs._renameat2

    def replace_then_rename(*args, **kwargs):
        result = original(*args, **kwargs)
        paths.data_root.rename(tmp_path / "displaced")
        return result

    monkeypatch.setattr(protected_fs, "_renameat2", replace_then_rename)
    with pytest.raises(ProtectedFilesystemError, match="identity"):
        store.write_mode_bytes(b"paper\n")


def test_ancestor_symlink_inserted_during_replace_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    paths = provision_operator_state(parent)
    store = OperatorStateStore(paths)
    original = protected_fs._renameat2

    def replace_then_redirect(*args, **kwargs):
        result = original(*args, **kwargs)
        displaced = tmp_path / "displaced"
        parent.rename(displaced)
        parent.symlink_to(displaced, target_is_directory=True)
        return result

    monkeypatch.setattr(protected_fs, "_renameat2", replace_then_redirect)
    with pytest.raises(ProtectedFilesystemError, match="directory"):
        store.write_mode_bytes(b"paper\n")


def test_target_replacement_after_publish_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    original = protected_fs._renameat2

    def replace_then_corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        write_private(paths.mode_path, b"live\n")
        return result

    monkeypatch.setattr(protected_fs, "_renameat2", replace_then_corrupt)
    with pytest.raises(ProtectedFilesystemError, match="published"):
        store.write_mode_bytes(b"paper\n")


def test_target_inode_replacement_before_publish_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = provision_operator_state(tmp_path)
    write_private(paths.mode_path, b"dryrun\n")
    store = OperatorStateStore(paths)
    current = store.read_state()
    original = protected_fs._renameat2
    replaced = False

    def replace_before_exchange(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            competitor = paths.data_root / "competitor"
            write_private(competitor, b"live\n")
            os.replace(competitor, paths.mode_path)
            replaced = True
        return original(*args, **kwargs)

    monkeypatch.setattr(protected_fs, "_renameat2", replace_before_exchange)
    with pytest.raises(ProtectedFilesystemError, match="changed before publish"):
        store.write_mode_bytes(b"paper\n", expected_sha256=current.mode_file_sha256)
    assert paths.mode_path.read_bytes() == b"live\n"


def test_kill_switch_replacement_during_clear_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = provision_operator_state(tmp_path)
    original_bytes = b"2026-09-02T12:00:00Z: original\n"
    replacement_bytes = b"2026-09-02T12:00:01Z: replacement\n"
    write_private(paths.kill_switch_path, original_bytes)
    store = OperatorStateStore(paths)
    active = store.read_state()
    intent = intent_for(
        active,
        desired_state="KILL_SWITCH_INACTIVE",
        desired_file_sha256=active.kill_switch_file_sha256,
    )
    original_rename = protected_fs._renameat2
    replaced = False

    def replace_before_clear(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            competitor = paths.data_root / "competitor"
            write_private(competitor, replacement_bytes)
            os.replace(competitor, paths.kill_switch_path)
            replaced = True
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(protected_fs, "_renameat2", replace_before_clear)
    with pytest.raises(ProtectedFilesystemError, match="changed during rename"):
        store.clear_kill_switch(intent)

    assert paths.kill_switch_path.read_bytes() == replacement_bytes
    assert not (
        paths.command_root
        / "tombstones"
        / f"{intent.idempotency_key_sha256}.kill-switch"
    ).exists()


def test_failed_kill_switch_rollback_preserves_active_state_and_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = provision_operator_state(tmp_path)
    original_bytes = b"2026-09-02T12:00:00Z: original\n"
    raced_bytes = b"2026-09-02T12:00:01Z: raced\n"
    newest_bytes = b"2026-09-02T12:00:02Z: newest\n"
    write_private(paths.kill_switch_path, original_bytes)
    store = OperatorStateStore(paths)
    active = store.read_state()
    intent = intent_for(
        active,
        desired_state="KILL_SWITCH_INACTIVE",
        desired_file_sha256=active.kill_switch_file_sha256,
    )
    original_rename = protected_fs._renameat2
    calls = 0

    def race_and_block_rollback(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            competitor = paths.data_root / "competitor"
            write_private(competitor, raced_bytes)
            os.replace(competitor, paths.kill_switch_path)
        elif calls == 2:
            write_private(paths.kill_switch_path, newest_bytes)
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(protected_fs, "_renameat2", race_and_block_rollback)
    with pytest.raises(ProtectedFilesystemError, match="rollback failed"):
        store.clear_kill_switch(intent)

    tombstone = (
        paths.command_root
        / "tombstones"
        / f"{intent.idempotency_key_sha256}.kill-switch"
    )
    assert paths.kill_switch_path.read_bytes() == newest_bytes
    assert tombstone.read_bytes() == raced_bytes
