from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from packages.operator_control.hashing import journal_sha256, reason_sha256
from services.operator_control import journal as journal_module
from services.operator_control.journal import CommandJournal, CommandJournalError
from services.operator_control.protected_fs import ProtectedFilesystemError
from services.operator_control.state_store import OperatorStateStore
from tests.hwc.fixtures.operator_state import (
    applied_for,
    intent_for,
    provision_operator_state,
    receipt_for,
    write_private,
)


def test_journal_round_trip_is_create_only_and_relationally_validated(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    intent = intent_for(store.read_state(), desired_file_sha256="b" * 64)
    journal = CommandJournal(paths)
    journal.create_intent(intent)
    with pytest.raises(CommandJournalError, match="exists"):
        journal.create_intent(intent)

    write_private(paths.mode_path, b"paper\n")
    resulting = store.read_state()
    applied = applied_for(intent, resulting)
    journal.create_applied(intent.idempotency_key_sha256, applied)
    receipt = receipt_for(intent, applied)
    journal.create_receipt(receipt)
    snapshot = journal.load(intent.idempotency_key_sha256)
    assert snapshot.intent == intent
    assert snapshot.applied == applied
    assert snapshot.receipt == receipt
    assert (
        (paths.command_root / "receipts" / f"{intent.idempotency_key_sha256}.json")
        .read_bytes()
        .endswith(b"\n")
    )


def test_journal_rejects_oversize_invalid_utf8_duplicate_keys_and_bad_digest(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    intent = intent_for(store.read_state())
    name = f"{intent.idempotency_key_sha256}.json"
    path = paths.command_root / "intents" / name
    journal = CommandJournal(paths)
    for value in (
        b"x" * 65_537,
        b"\xff\n",
        b'{"schema_version":"operator-command-intent-v1","schema_version":"again"}\n',
        (json.dumps(intent.model_dump(mode="json")) + "\n").encode(),
    ):
        if path.exists():
            path.unlink()
        write_private(path, value)
        with pytest.raises(CommandJournalError, match="unsafe"):
            journal.load(intent.idempotency_key_sha256)


def test_journal_rejects_applied_without_intent_and_cross_record_mismatch(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    intent = intent_for(store.read_state())
    applied = applied_for(intent, store.read_state())
    journal = CommandJournal(paths)
    journal.create_applied(intent.idempotency_key_sha256, applied)
    with pytest.raises(CommandJournalError, match="unsafe"):
        journal.load(intent.idempotency_key_sha256)

    journal.create_intent(intent)
    bad = applied.model_copy(update={"intent_sha256": "f" * 64})
    bad = bad.model_copy(
        update={"applied_sha256": journal_sha256(bad, "applied_sha256")}
    )
    path = paths.command_root / "applied" / f"{intent.idempotency_key_sha256}.json"
    path.unlink()
    journal.create_applied(intent.idempotency_key_sha256, bad)
    with pytest.raises(CommandJournalError, match="unsafe"):
        journal.load(intent.idempotency_key_sha256)


def test_journal_rejects_receipt_that_conflicts_with_applied_record(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    intent = intent_for(store.read_state())
    journal = CommandJournal(paths)
    journal.create_intent(intent)
    resulting = store.apply_mode(intent)
    applied = applied_for(intent, resulting)
    journal.create_applied(intent.idempotency_key_sha256, applied)
    receipt = receipt_for(intent, applied).model_copy(
        update={"resulting_state_sha256": "f" * 64}
    )
    receipt = receipt.model_copy(
        update={"receipt_sha256": journal_sha256(receipt, "receipt_sha256")}
    )
    journal.create_receipt(receipt)
    with pytest.raises(CommandJournalError, match="unsafe"):
        journal.load(intent.idempotency_key_sha256)


def test_mode_activation_and_clear_use_intent_bound_exact_bytes(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    prior = store.read_state()
    mode_intent = intent_for(
        prior,
        desired_file_sha256=hashlib.sha256(b"paper\n").hexdigest(),
    )
    mode = store.apply_mode(mode_intent)
    assert mode.requested_mode == "PAPER"

    desired = b"2026-09-02T12:00:00Z: incident\n"
    activate_intent = intent_for(
        mode,
        desired_state="KILL_SWITCH_ACTIVE",
        desired_file_sha256=hashlib.sha256(desired).hexdigest(),
        reason_sha256=reason_sha256("incident"),
    )
    active = store.activate_kill_switch(activate_intent, desired)
    assert active.kill_switch_state == "ACTIVE"
    assert paths.kill_switch_path.read_bytes() == desired

    clear_intent = intent_for(
        active,
        desired_state="KILL_SWITCH_INACTIVE",
        desired_file_sha256=active.kill_switch_file_sha256,
    )
    cleared = store.clear_kill_switch(clear_intent)
    assert cleared.state.kill_switch_state == "INACTIVE"
    assert cleared.tombstone_sha256 == active.kill_switch_file_sha256
    assert (
        store.tombstone_sha256(clear_intent.idempotency_key_sha256)
        == cleared.tombstone_sha256
    )
    with pytest.raises(ProtectedFilesystemError):
        store.clear_kill_switch(clear_intent)

    write_private(paths.kill_switch_path, desired)
    reactivated = store.read_state()
    second_clear = intent_for(
        reactivated,
        desired_state="KILL_SWITCH_INACTIVE",
        desired_file_sha256=reactivated.kill_switch_file_sha256,
    )
    with pytest.raises(ProtectedFilesystemError, match="already exists"):
        store.clear_kill_switch(second_clear)
    assert paths.kill_switch_path.read_bytes() == desired


def test_mutations_reject_wrong_prior_or_desired_digest(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    intent = intent_for(store.read_state(), desired_file_sha256="f" * 64)
    with pytest.raises(ProtectedFilesystemError):
        store.apply_mode(intent)


def test_journal_global_lock_serializes_threads(tmp_path: Path) -> None:
    import threading

    paths = provision_operator_state(tmp_path)
    first = CommandJournal(paths)
    second = CommandJournal(paths)
    entered = threading.Event()

    def contender() -> None:
        with second.locked():
            entered.set()

    with first.locked():
        thread = threading.Thread(target=contender)
        thread.start()
        assert not entered.wait(0.05)
    thread.join(timeout=1)
    assert entered.is_set()


def test_journal_lock_replacement_after_flock_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = provision_operator_state(tmp_path)
    original_flock = journal_module.fcntl.flock
    replaced = False

    def replace_after_lock(descriptor: int, operation: int) -> None:
        nonlocal replaced
        original_flock(descriptor, operation)
        if operation == journal_module.fcntl.LOCK_EX and not replaced:
            competitor = paths.command_root / "replacement-lock"
            write_private(competitor, b"")
            os.replace(competitor, paths.command_root / "lock")
            replaced = True

    monkeypatch.setattr(journal_module.fcntl, "flock", replace_after_lock)
    with pytest.raises(CommandJournalError, match="unsafe"):
        with CommandJournal(paths).locked():
            pytest.fail("unsafe replaced lock was accepted")


def test_journal_lock_open_errors_are_typed(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    lock = paths.command_root / "lock"
    lock.unlink()
    lock.symlink_to("/dev/null")
    with pytest.raises(CommandJournalError, match="unsafe"):
        with CommandJournal(paths).locked():
            pytest.fail("unsafe lock was accepted")


def test_journal_record_key_must_match_loaded_path(tmp_path: Path) -> None:
    from packages.engine_contracts.serialization import canonical_json_bytes

    paths = provision_operator_state(tmp_path)
    intent = intent_for(OperatorStateStore(paths).read_state())
    wrong_key = "f" * 64
    write_private(
        paths.command_root / "intents" / f"{wrong_key}.json",
        canonical_json_bytes(intent) + b"\n",
    )
    with pytest.raises(CommandJournalError, match="unsafe"):
        CommandJournal(paths).load(wrong_key)
