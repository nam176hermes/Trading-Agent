from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from packages.operator_control.contracts import (
    CommandAppliedV1,
    CommandIntentV1,
    CommandReceiptV1,
    OperatorActorV1,
    OperatorSafetyEvidenceV1,
    OperatorSourceStateV1,
    SetKillSwitchV1,
    SetRequestedModeV1,
    SubmitOperatorCommandV1,
)
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.operator_control.hashing import evidence_sha256, journal_sha256
from packages.operator_control.policy import OperatorCommandRejected
from services.operator_control.journal import CommandJournal, CommandJournalError
from services.operator_control.service import OperatorControlService
from services.operator_control.state_store import OperatorStatePaths, OperatorStateStore
from services.operator_control.state_store import RecoveryError


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


def _paths(root: Path) -> OperatorStatePaths:
    data = root / "operator-data"
    command = data / ".operator-commands"
    return OperatorStatePaths(data, command, data / ".mode", data / ".kill_switch")


def _safety() -> OperatorSafetyEvidenceV1:
    payload = {
        "schema_version": "operator-safety-evidence-v1",
        "requested_mode": "PAPER",
        "effective_mode": "PAPER",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "kill_switch_state": "ACTIVE",
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "source_fingerprint": "d" * 64,
    }
    return OperatorSafetyEvidenceV1.model_validate(
        {**payload, "evidence_sha256": evidence_sha256(payload)}
    )


def _prepare(root: Path, scenario: str) -> tuple[OperatorStatePaths, SubmitOperatorCommandV1]:
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    paths = provision_operator_state(root)
    if scenario in {"activate", "clear"}:
        write_private(paths.mode_path, b"paper\n")
    if scenario == "clear":
        write_private(paths.kill_switch_path, b"2026-09-02T12:00:00Z: incident\n")
    state = OperatorStateStore(paths).read_state()
    command = (
        SetRequestedModeV1(command_type="SET_REQUESTED_MODE", desired_mode="PAPER")
        if scenario == "paper"
        else SetKillSwitchV1(
            command_type="SET_KILL_SWITCH",
            desired_state="ACTIVE" if scenario == "activate" else "INACTIVE",
            reason="recovery drill" if scenario == "activate" else None,
        )
    )
    request = SubmitOperatorCommandV1(
        schema_version="submit-operator-command-v1",
        command_id={
            "paper": "cmd_0123456789abcdef0123456789abcdef",
            "activate": "cmd_10000000000000000000000000000002",
            "clear": "cmd_10000000000000000000000000000003",
        }[scenario],
        idempotency_key="idem.1" if scenario == "paper" else f"recovery.{scenario}.v1",
        correlation_id="corr.1" if scenario == "paper" else f"recovery.{scenario}.v1",
        expected_state_sha256=state.state_sha256 if scenario == "clear" else None,
        command=command,
    )
    write_private(root / "request.json", canonical_json_bytes(request) + b"\n")
    return paths, request


def _execute_scenario(
    root: Path,
    scenario: str,
    failpoint: str | None,
    request_file: Path | None = None,
) -> None:
    if failpoint is None:
        paths = _paths(root)
        request = SubmitOperatorCommandV1.model_validate_json(
            (request_file or root / "request.json").read_bytes()
        )
    else:
        paths, request = _prepare(root, scenario)
    hook = crash_hook(failpoint)
    store = OperatorStateStore(paths, failpoint=hook)
    journal = CommandJournal(paths, failpoint=hook)
    actor = OperatorActorV1(
        schema_version="operator-actor-v1",
        principal_id=f"operator.{scenario}",
        interface="WEB" if scenario == "activate" else "CLI",
    )
    result = OperatorControlService(
        state_store=store,
        journal=journal,
        safety_provider=_safety,
        clock=lambda: NOW,
    ).execute(actor, request)
    print(
        json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--crash-at")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--request-file", type=Path)
    parser.add_argument("--scenario", choices=("paper", "activate", "clear"), default="paper")
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    if sum((arguments.retry, arguments.prepare, arguments.crash_at is not None)) != 1:
        parser.error("choose exactly one of --crash-at, --prepare, or --retry")
    if arguments.request_file is not None and not arguments.retry:
        parser.error("--request-file requires --retry")
    if arguments.prepare:
        _prepare(arguments.root, arguments.scenario)
    else:
        try:
            _execute_scenario(
                arguments.root,
                arguments.scenario,
                None if arguments.retry else arguments.crash_at,
                arguments.request_file,
            )
        except (OperatorCommandRejected, RecoveryError, CommandJournalError) as exc:
            print(json.dumps({"error": getattr(exc, "code", str(exc))}), flush=True)
            raise SystemExit(3) from None
