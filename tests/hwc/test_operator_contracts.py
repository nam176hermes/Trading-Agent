from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.operator_control.contracts import (
    CommandAppliedV1,
    CommandExecutionResultV1,
    CommandIntentV1,
    CommandReceiptV1,
    OperatorActorV1,
    OperatorSafetyEvidenceV1,
    OperatorSourceStateV1,
    SetKillSwitchV1,
    SetRequestedModeV1,
    SubmitOperatorCommandV1,
)
from packages.operator_control.hashing import (
    evidence_sha256,
    idempotency_key_sha256,
    journal_sha256,
    reason_sha256,
    request_sha256,
    state_sha256,
)


NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
SHA = "a" * 64
OTHER_SHA = "b" * 64


def actor(
    *, interface: str = "CLI", principal_id: str = "operator.alice"
) -> OperatorActorV1:
    return OperatorActorV1(
        schema_version="operator-actor-v1",
        principal_id=principal_id,
        interface=interface,
    )


def request(**changes: object) -> SubmitOperatorCommandV1:
    values: dict[str, object] = {
        "schema_version": "submit-operator-command-v1",
        "command_id": "cmd_0123456789abcdef0123456789abcdef",
        "idempotency_key": "idem.1",
        "correlation_id": "corr.1",
        "expected_state_sha256": SHA,
        "command": {
            "command_type": "SET_REQUESTED_MODE",
            "desired_mode": "PAPER",
        },
    }
    values.update(changes)
    return SubmitOperatorCommandV1.model_validate(values)


@pytest.mark.parametrize(
    ("model", "values"),
    (
        (
            OperatorActorV1,
            {
                "schema_version": "operator-actor-v1",
                "principal_id": "alice",
                "interface": "CLI",
            },
        ),
        (
            SetRequestedModeV1,
            {"command_type": "SET_REQUESTED_MODE", "desired_mode": "PAPER"},
        ),
        (
            SetKillSwitchV1,
            {
                "command_type": "SET_KILL_SWITCH",
                "desired_state": "ACTIVE",
                "reason": "incident",
            },
        ),
    ),
)
def test_contracts_are_frozen_and_reject_extra_fields(
    model: type, values: dict[str, object]
) -> None:
    instance = model.model_validate(values)
    with pytest.raises(ValidationError):
        model.model_validate({**values, "unexpected": True})
    with pytest.raises(ValidationError):
        instance.schema_version = "changed"  # type: ignore[attr-defined,misc]


@pytest.mark.parametrize("bad_id", ("cmd_ABC", "cmd_" + "a" * 31, "cmd_" + "g" * 32))
def test_submit_rejects_noncanonical_command_ids(bad_id: str) -> None:
    with pytest.raises(ValidationError, match="command_id"):
        request(command_id=bad_id)


def test_submit_uses_a_closed_discriminated_command_union() -> None:
    assert isinstance(request().command, SetRequestedModeV1)
    kill = request(
        command={
            "command_type": "SET_KILL_SWITCH",
            "desired_state": "ACTIVE",
            "reason": " incident ",
        }
    )
    assert isinstance(kill.command, SetKillSwitchV1)
    assert kill.command.reason == "incident"
    with pytest.raises(ValidationError):
        request(command={"command_type": "DELETE_AUTHORITY", "desired_state": "ACTIVE"})


@pytest.mark.parametrize("reason", (None, "", "   ", "x" * 257))
def test_kill_activation_requires_a_bounded_trimmed_reason(reason: str | None) -> None:
    with pytest.raises(ValidationError, match="reason"):
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="ACTIVE", reason=reason
        )


def test_kill_clear_forbids_reason() -> None:
    clear = SetKillSwitchV1(
        command_type="SET_KILL_SWITCH", desired_state="INACTIVE", reason=None
    )
    assert clear.reason is None
    with pytest.raises(ValidationError, match="reason"):
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="INACTIVE", reason="because"
        )


@pytest.mark.parametrize("reason", ("first\nsecond", "first\rsecond"))
def test_kill_activation_reason_must_fit_the_single_line_sentinel(reason: str) -> None:
    with pytest.raises(ValidationError, match="reason"):
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="ACTIVE", reason=reason
        )


def test_source_and_safety_contracts_require_canonical_utc_timestamps() -> None:
    state = OperatorSourceStateV1(
        schema_version="operator-source-state-v1",
        requested_mode="PAPER",
        kill_switch_state="ACTIVE",
        kill_switch_activated_at=NOW,
        kill_switch_reason="incident",
        mode_file_sha256=SHA,
        kill_switch_file_sha256=OTHER_SHA,
        state_sha256=SHA,
    )
    assert (
        state.model_dump(mode="json")["kill_switch_activated_at"]
        == "2026-09-02T12:00:00Z"
    )
    with pytest.raises(ValidationError, match="canonical|UTC"):
        OperatorSourceStateV1.model_validate(
            {
                **state.model_dump(mode="json"),
                "kill_switch_activated_at": "2026-09-02T12:00:00+00:00",
            }
        )

    evidence = OperatorSafetyEvidenceV1(
        schema_version="operator-safety-evidence-v1",
        requested_mode="PAPER",
        effective_mode="PAPER",
        live_execution_enabled=False,
        live_trading_approved=False,
        kill_switch_state="ACTIVE",
        observed_at=NOW,
        source_fingerprint=SHA,
        evidence_sha256=OTHER_SHA,
    )
    assert evidence.model_dump(mode="json")["observed_at"].endswith("Z")


def test_content_digests_use_canonical_json_and_exclude_only_self_digest() -> None:
    state_payload = {
        "schema_version": "operator-source-state-v1",
        "requested_mode": "PAPER",
        "state_sha256": SHA,
    }
    expected = hashlib.sha256(
        json.dumps(
            {"requested_mode": "PAPER", "schema_version": "operator-source-state-v1"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    assert state_sha256(state_payload) == expected
    assert (
        evidence_sha256({"observed_at": "2026-09-02T12:00:00Z", "evidence_sha256": SHA})
        == hashlib.sha256(b'{"observed_at":"2026-09-02T12:00:00Z"}').hexdigest()
    )
    assert idempotency_key_sha256(" idem.1 ") == hashlib.sha256(b" idem.1 ").hexdigest()
    assert reason_sha256(" incident ") == hashlib.sha256(b"incident").hexdigest()


def test_request_digest_excludes_transport_ids_and_raw_idempotency_key() -> None:
    first = request()
    second = request(
        command_id="cmd_ffffffffffffffffffffffffffffffff",
        idempotency_key="different",
        correlation_id="different",
    )
    assert request_sha256(actor(), first) == request_sha256(actor(), second)
    assert request_sha256(actor(), first) != request_sha256(
        actor(principal_id="operator.bob"), first
    )
    assert request_sha256(actor(), first) != request_sha256(
        actor(), first.model_copy(update={"expected_state_sha256": OTHER_SHA})
    )


def test_journal_contracts_have_exact_fields_and_stable_self_digests() -> None:
    intent = CommandIntentV1(
        schema_version="operator-command-intent-v1",
        command_id="cmd_0123456789abcdef0123456789abcdef",
        idempotency_key_sha256=SHA,
        correlation_id="corr.1",
        request_sha256=OTHER_SHA,
        actor=actor(),
        command_type="SET_REQUESTED_MODE",
        desired_state="PAPER",
        prior_state_sha256=SHA,
        expected_state_sha256=SHA,
        safety_evidence_sha256=None,
        reason_sha256=None,
        accepted_at=NOW,
        desired_file_sha256=OTHER_SHA,
        intent_sha256=SHA,
    )
    assert journal_sha256(intent, "intent_sha256") == journal_sha256(
        intent.model_copy(update={"intent_sha256": OTHER_SHA}), "intent_sha256"
    )
    applied = CommandAppliedV1(
        schema_version="operator-command-applied-v1",
        intent_sha256=SHA,
        applied_at=NOW,
        application_kind="MODE_REPLACED",
        resulting_state_sha256=OTHER_SHA,
        tombstone_sha256=None,
        applied_sha256=SHA,
    )
    receipt = CommandReceiptV1(
        schema_version="operator-command-receipt-v1",
        command_id=intent.command_id,
        idempotency_key_sha256=SHA,
        correlation_id=intent.correlation_id,
        request_sha256=intent.request_sha256,
        actor=intent.actor,
        command_type=intent.command_type,
        desired_state=intent.desired_state,
        prior_state_sha256=intent.prior_state_sha256,
        expected_state_sha256=intent.expected_state_sha256,
        safety_evidence_sha256=None,
        reason_sha256=None,
        accepted_at=NOW,
        applied_at=NOW,
        completed_at=NOW,
        outcome="APPLIED",
        outcome_code="MODE_SET_PAPER",
        resulting_state_sha256=OTHER_SHA,
        intent_sha256=SHA,
        applied_sha256=journal_sha256(applied, "applied_sha256"),
        receipt_sha256=SHA,
    )
    result = CommandExecutionResultV1(
        schema_version="operator-command-execution-result-v1",
        receipt=receipt,
        deduplicated=False,
    )
    assert result.receipt.command_id == intent.command_id
    assert set(type(intent).model_fields) == {
        "schema_version",
        "command_id",
        "idempotency_key_sha256",
        "correlation_id",
        "request_sha256",
        "actor",
        "command_type",
        "desired_state",
        "prior_state_sha256",
        "expected_state_sha256",
        "safety_evidence_sha256",
        "reason_sha256",
        "accepted_at",
        "desired_file_sha256",
        "intent_sha256",
    }


@pytest.mark.parametrize(
    ("model", "fields"),
    (
        (OperatorActorV1, {"schema_version", "principal_id", "interface"}),
        (SetRequestedModeV1, {"command_type", "desired_mode"}),
        (SetKillSwitchV1, {"command_type", "desired_state", "reason"}),
        (
            SubmitOperatorCommandV1,
            {
                "schema_version",
                "command_id",
                "idempotency_key",
                "correlation_id",
                "expected_state_sha256",
                "command",
            },
        ),
        (
            OperatorSourceStateV1,
            {
                "schema_version",
                "requested_mode",
                "kill_switch_state",
                "kill_switch_activated_at",
                "kill_switch_reason",
                "mode_file_sha256",
                "kill_switch_file_sha256",
                "state_sha256",
            },
        ),
        (
            OperatorSafetyEvidenceV1,
            {
                "schema_version",
                "requested_mode",
                "effective_mode",
                "live_execution_enabled",
                "live_trading_approved",
                "kill_switch_state",
                "observed_at",
                "source_fingerprint",
                "evidence_sha256",
            },
        ),
        (
            CommandAppliedV1,
            {
                "schema_version",
                "intent_sha256",
                "applied_at",
                "application_kind",
                "resulting_state_sha256",
                "tombstone_sha256",
                "applied_sha256",
            },
        ),
        (
            CommandReceiptV1,
            {
                "schema_version",
                "command_id",
                "idempotency_key_sha256",
                "correlation_id",
                "request_sha256",
                "actor",
                "command_type",
                "desired_state",
                "prior_state_sha256",
                "expected_state_sha256",
                "safety_evidence_sha256",
                "reason_sha256",
                "accepted_at",
                "applied_at",
                "completed_at",
                "outcome",
                "outcome_code",
                "resulting_state_sha256",
                "intent_sha256",
                "applied_sha256",
                "receipt_sha256",
            },
        ),
        (CommandExecutionResultV1, {"schema_version", "receipt", "deduplicated"}),
    ),
)
def test_contract_field_sets_are_exact(model: type, fields: set[str]) -> None:
    assert set(model.model_fields) == fields
