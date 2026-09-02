from __future__ import annotations

import argparse
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from packages.operator_control.contracts import (
    CommandAppliedV1,
    CommandIntentV1,
    CommandReceiptV1,
    OperatorActorV1,
    OperatorSourceStateV1,
)
from packages.operator_control.hashing import journal_sha256
from services.operator_control.journal import CommandJournal
from services.operator_control.state_store import OperatorStatePaths, OperatorStateStore


NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def write_private(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o600)


def provision_operator_state(parent: Path) -> OperatorStatePaths:
    root = parent / "operator-data"
    root.mkdir(mode=0o700)
    command_root = root / ".operator-commands"
    command_root.mkdir(mode=0o700)
    for name in ("intents", "applied", "receipts", "tombstones"):
        (command_root / name).mkdir(mode=0o700)
    write_private(command_root / "lock", b"")
    return OperatorStatePaths(
        data_root=root,
        command_root=command_root,
        mode_path=root / ".mode",
        kill_switch_path=root / ".kill_switch",
    )


def intent_for(
    state: OperatorSourceStateV1,
    *,
    desired_state: str = "PAPER",
    desired_file_sha256: str | None = None,
    reason_sha256: str | None = None,
) -> CommandIntentV1:
    command_type = (
        "SET_REQUESTED_MODE" if desired_state == "PAPER" else "SET_KILL_SWITCH"
    )
    if desired_file_sha256 is None and desired_state == "PAPER":
        desired_file_sha256 = hashlib.sha256(b"paper\n").hexdigest()
    base = CommandIntentV1(
        schema_version="operator-command-intent-v1",
        command_id="cmd_0123456789abcdef0123456789abcdef",
        idempotency_key_sha256=hashlib.sha256(b"idem.1").hexdigest(),
        correlation_id="corr.1",
        request_sha256="b" * 64,
        actor=OperatorActorV1(
            schema_version="operator-actor-v1",
            principal_id="operator.alice",
            interface="CLI",
        ),
        command_type=command_type,
        desired_state=desired_state,
        prior_state_sha256=state.state_sha256,
        expected_state_sha256=state.state_sha256,
        safety_evidence_sha256="c" * 64
        if desired_state == "KILL_SWITCH_INACTIVE"
        else None,
        reason_sha256=reason_sha256,
        accepted_at=NOW,
        desired_file_sha256=desired_file_sha256,
        intent_sha256="0" * 64,
    )
    return base.model_copy(
        update={"intent_sha256": journal_sha256(base, "intent_sha256")}
    )


def applied_for(
    intent: CommandIntentV1,
    resulting_state: OperatorSourceStateV1,
    *,
    application_kind: str = "MODE_REPLACED",
    tombstone_sha256: str | None = None,
) -> CommandAppliedV1:
    base = CommandAppliedV1(
        schema_version="operator-command-applied-v1",
        intent_sha256=intent.intent_sha256,
        applied_at=NOW,
        application_kind=application_kind,
        resulting_state_sha256=resulting_state.state_sha256,
        tombstone_sha256=tombstone_sha256,
        applied_sha256="0" * 64,
    )
    return base.model_copy(
        update={"applied_sha256": journal_sha256(base, "applied_sha256")}
    )


def receipt_for(
    intent: CommandIntentV1,
    applied: CommandAppliedV1,
    *,
    outcome: str = "APPLIED",
) -> CommandReceiptV1:
    base = CommandReceiptV1(
        schema_version="operator-command-receipt-v1",
        command_id=intent.command_id,
        idempotency_key_sha256=intent.idempotency_key_sha256,
        correlation_id=intent.correlation_id,
        request_sha256=intent.request_sha256,
        actor=intent.actor,
        command_type=intent.command_type,
        desired_state=intent.desired_state,
        prior_state_sha256=intent.prior_state_sha256,
        expected_state_sha256=intent.expected_state_sha256,
        safety_evidence_sha256=intent.safety_evidence_sha256,
        reason_sha256=intent.reason_sha256,
        accepted_at=intent.accepted_at,
        applied_at=applied.applied_at,
        completed_at=NOW,
        outcome=outcome,
        outcome_code="MODE_SET_PAPER",
        resulting_state_sha256=applied.resulting_state_sha256,
        intent_sha256=intent.intent_sha256,
        applied_sha256=applied.applied_sha256,
        receipt_sha256="0" * 64,
    )
    return base.model_copy(
        update={"receipt_sha256": journal_sha256(base, "receipt_sha256")}
    )


def crash_hook(target: str):
    def hook(name: str) -> None:
        if name == target:
            os._exit(77)

    return hook


def _crash_scenario(root: Path, failpoint: str) -> None:
    root.mkdir(parents=True, mode=0o700)
    paths = provision_operator_state(root)
    hook = crash_hook(failpoint)
    store = OperatorStateStore(paths, failpoint=hook)
    journal = CommandJournal(paths, failpoint=hook)
    prior = store.read_state()
    intent = intent_for(
        prior, desired_file_sha256=hashlib.sha256(b"paper\n").hexdigest()
    )
    journal.create_intent(intent)
    resulting = store.apply_mode(intent)
    applied = applied_for(intent, resulting)
    journal.create_applied(intent.idempotency_key_sha256, applied)
    journal.create_receipt(receipt_for(intent, applied))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--crash-at", required=True)
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    _crash_scenario(arguments.root, arguments.crash_at)
