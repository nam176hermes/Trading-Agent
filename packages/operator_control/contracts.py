"""Strict versioned contracts for headless operator commands."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.engine_contracts.serialization import (
    CanonicalUtcDateTime,
    ProducerIdentity,
    Sha256Hex,
)


InterfaceKind: TypeAlias = Literal["WEB", "CLI"]
RequestedMode: TypeAlias = Literal["PAPER", "DRYRUN", "LIVE"]
SourceMode: TypeAlias = Literal["PAPER", "DRYRUN", "LIVE", "UNKNOWN"]
KillSwitchDesiredState: TypeAlias = Literal["ACTIVE", "INACTIVE"]
KillSwitchSourceState: TypeAlias = Literal["ACTIVE", "INACTIVE", "UNKNOWN"]
CommandId = Annotated[str, Field(pattern=r"^cmd_[0-9a-f]{32}$")]


class OperatorModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class OperatorActorV1(OperatorModel):
    schema_version: Literal["operator-actor-v1"]
    principal_id: ProducerIdentity
    interface: InterfaceKind


class SetRequestedModeV1(OperatorModel):
    command_type: Literal["SET_REQUESTED_MODE"]
    desired_mode: RequestedMode


class SetKillSwitchV1(OperatorModel):
    command_type: Literal["SET_KILL_SWITCH"]
    desired_state: KillSwitchDesiredState
    reason: str | None

    @model_validator(mode="after")
    def _validate_reason(self) -> "SetKillSwitchV1":
        if self.desired_state == "INACTIVE":
            if self.reason is not None:
                raise ValueError("reason must be None when clearing the kill switch")
            return self
        if (
            self.reason is None
            or not (1 <= len(self.reason.strip()) <= 256)
            or "\n" in self.reason
            or "\r" in self.reason
        ):
            raise ValueError("reason must contain 1..256 characters after trim")
        object.__setattr__(self, "reason", self.reason.strip())
        return self


OperatorCommand: TypeAlias = Annotated[
    SetRequestedModeV1 | SetKillSwitchV1,
    Field(discriminator="command_type"),
]


class SubmitOperatorCommandV1(OperatorModel):
    schema_version: Literal["submit-operator-command-v1"]
    command_id: CommandId
    idempotency_key: ProducerIdentity
    correlation_id: ProducerIdentity
    expected_state_sha256: Sha256Hex | None
    command: OperatorCommand


class OperatorSourceStateV1(OperatorModel):
    schema_version: Literal["operator-source-state-v1"]
    requested_mode: SourceMode
    kill_switch_state: KillSwitchSourceState
    kill_switch_activated_at: CanonicalUtcDateTime | None
    kill_switch_reason: str | None
    mode_file_sha256: Sha256Hex | None
    kill_switch_file_sha256: Sha256Hex | None
    state_sha256: Sha256Hex


class OperatorSafetyEvidenceV1(OperatorModel):
    schema_version: Literal["operator-safety-evidence-v1"]
    requested_mode: SourceMode
    effective_mode: SourceMode
    live_execution_enabled: bool | None
    live_trading_approved: bool | None
    kill_switch_state: KillSwitchSourceState
    observed_at: CanonicalUtcDateTime
    source_fingerprint: Sha256Hex
    evidence_sha256: Sha256Hex


CommandType: TypeAlias = Literal["SET_REQUESTED_MODE", "SET_KILL_SWITCH"]
JournalDesiredState: TypeAlias = Literal[
    "PAPER", "KILL_SWITCH_ACTIVE", "KILL_SWITCH_INACTIVE"
]


class CommandIntentV1(OperatorModel):
    schema_version: Literal["operator-command-intent-v1"]
    command_id: CommandId
    idempotency_key_sha256: Sha256Hex
    correlation_id: ProducerIdentity
    request_sha256: Sha256Hex
    actor: OperatorActorV1
    command_type: CommandType
    desired_state: JournalDesiredState
    prior_state_sha256: Sha256Hex
    expected_state_sha256: Sha256Hex | None
    safety_evidence_sha256: Sha256Hex | None
    reason_sha256: Sha256Hex | None
    accepted_at: CanonicalUtcDateTime
    desired_file_sha256: Sha256Hex | None
    intent_sha256: Sha256Hex


class CommandAppliedV1(OperatorModel):
    schema_version: Literal["operator-command-applied-v1"]
    intent_sha256: Sha256Hex
    applied_at: CanonicalUtcDateTime
    application_kind: Literal[
        "NO_CHANGE",
        "MODE_REPLACED",
        "KILL_SWITCH_CREATED",
        "KILL_SWITCH_CLEARED_TO_TOMBSTONE",
        "RECOVERED_MODE_REPLACEMENT",
        "RECOVERED_KILL_SWITCH_CREATE",
        "RECOVERED_KILL_SWITCH_CLEAR",
    ]
    resulting_state_sha256: Sha256Hex
    tombstone_sha256: Sha256Hex | None
    applied_sha256: Sha256Hex


class CommandReceiptV1(OperatorModel):
    schema_version: Literal["operator-command-receipt-v1"]
    command_id: CommandId
    idempotency_key_sha256: Sha256Hex
    correlation_id: ProducerIdentity
    request_sha256: Sha256Hex
    actor: OperatorActorV1
    command_type: CommandType
    desired_state: JournalDesiredState
    prior_state_sha256: Sha256Hex
    expected_state_sha256: Sha256Hex | None
    safety_evidence_sha256: Sha256Hex | None
    reason_sha256: Sha256Hex | None
    accepted_at: CanonicalUtcDateTime
    applied_at: CanonicalUtcDateTime
    completed_at: CanonicalUtcDateTime
    outcome: Literal["APPLIED", "NO_CHANGE", "RECOVERED_APPLIED"]
    outcome_code: str
    resulting_state_sha256: Sha256Hex
    intent_sha256: Sha256Hex
    applied_sha256: Sha256Hex
    receipt_sha256: Sha256Hex


class CommandExecutionResultV1(OperatorModel):
    schema_version: Literal["operator-command-execution-result-v1"]
    receipt: CommandReceiptV1
    deduplicated: bool
