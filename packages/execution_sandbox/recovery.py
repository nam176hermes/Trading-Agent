"""Strict immutable recovery evidence for the deterministic execution sandbox."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator

from packages.domain.clock import require_utc
from packages.domain.orders import CanonicalIdentifier
from packages.domain.runtime_halt import (
    ConsumedSubmitAuthority,
    PreparedSubmitPermit,
)
from packages.domain.runtime_risk import Sha256
from packages.runtime_risk import canonical_model_digest

from .models import SandboxModel, SandboxOrderSnapshot, SandboxSnapshot


ModelT = TypeVar("ModelT", bound=BaseModel)


def _canonical_model(
    value: object, expected: type[ModelT], field_name: str
) -> ModelT:
    """Rebuild one exact nested model without trusting an existing instance."""

    if type(value) is not expected:
        raise ValueError(f"{field_name} must be a concrete {expected.__name__}")
    try:
        values = {name: getattr(value, name) for name in expected.model_fields}
        return expected.model_validate(values)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not canonical") from exc


def _require_concrete_uuid(value: object, field_name: str) -> UUID:
    if type(value) is not UUID:
        raise ValueError(f"{field_name} must be a concrete UUID")
    return value


def _require_authority_boundary_types(
    prepared: PreparedSubmitPermit,
    consumed: ConsumedSubmitAuthority,
) -> None:
    for field_name in ("permit_id", "halt_stream_id"):
        _require_concrete_uuid(
            getattr(prepared, field_name),
            f"prepared_permit {field_name}",
        )
        _require_concrete_uuid(
            getattr(consumed, field_name),
            f"consumed_authority {field_name}",
        )
    if type(consumed.consumed_at) is not datetime:
        raise ValueError("consumed_authority consumed_at must be a concrete datetime")


def _require_snapshot_boundary_types(value: object) -> None:
    if type(value) is not SandboxSnapshot:
        return
    if type(value.current_time) is not datetime:
        raise ValueError("snapshot current_time must be a concrete datetime")
    if type(value.orders) is not tuple:
        return
    for order in value.orders:
        if type(order) is not SandboxOrderSnapshot:
            continue
        identities = (
            ("order_id", order.order_id),
            ("order_intent.intent_id", order.order_intent.intent_id),
            ("venue_state.order_id", order.venue_state.order_id),
            ("observed_state.order_id", order.observed_state.order_id),
        )
        for identity_name, identity in identities:
            _require_concrete_uuid(
                identity,
                f"snapshot order {identity_name}",
            )


class SandboxSubmitCustody(SandboxModel):
    """Recovery evidence only; grants neither submit nor retry authority."""

    command_id: UUID
    order_id: UUID
    client_order_id: CanonicalIdentifier
    prepared_permit: PreparedSubmitPermit
    consumed_authority: ConsumedSubmitAuthority

    @field_validator("command_id", mode="before")
    @classmethod
    def _concrete_command_id(cls, value: object) -> UUID:
        return _require_concrete_uuid(value, "command_id")

    @field_validator("order_id", mode="before")
    @classmethod
    def _concrete_order_id(cls, value: object) -> UUID:
        return _require_concrete_uuid(value, "order_id")

    @model_validator(mode="after")
    def _canonical_authority_lineage(self) -> "SandboxSubmitCustody":
        prepared = _canonical_model(
            self.prepared_permit,
            PreparedSubmitPermit,
            "prepared_permit",
        )
        consumed = _canonical_model(
            self.consumed_authority,
            ConsumedSubmitAuthority,
            "consumed_authority",
        )
        _require_authority_boundary_types(prepared, consumed)
        for field_name in (
            "permit_id",
            "prepared_event_digest",
            "halt_stream_id",
            "halt_generation",
            "halt_transition_digest",
        ):
            if getattr(prepared, field_name) != getattr(consumed, field_name):
                raise ValueError(
                    f"prepared_permit and consumed_authority {field_name} must match"
                )
        object.__setattr__(self, "prepared_permit", prepared)
        object.__setattr__(self, "consumed_authority", consumed)
        return self


class SandboxRecoveryCheckpoint(SandboxModel):
    """Canonical process-local recovery evidence with no execution authority."""

    checkpoint_id: UUID
    scenario_digest: Sha256
    snapshot: SandboxSnapshot
    executed_command_ids: tuple[UUID, ...]
    submit_custodies: tuple[SandboxSubmitCustody, ...]
    created_at: datetime
    schema_version: Literal["sandbox-recovery-checkpoint-v1"]

    @field_validator("checkpoint_id", mode="before")
    @classmethod
    def _concrete_checkpoint_id(cls, value: object) -> UUID:
        return _require_concrete_uuid(value, "checkpoint_id")

    @field_validator("executed_command_ids", mode="before")
    @classmethod
    def _concrete_executed_command_ids(cls, value: object) -> object:
        if type(value) is tuple:
            for command_id in value:
                _require_concrete_uuid(command_id, "executed_command_ids")
        return value

    @field_validator("created_at")
    @classmethod
    def _utc_created_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_checkpoint(self) -> "SandboxRecoveryCheckpoint":
        _require_snapshot_boundary_types(self.snapshot)
        snapshot = _canonical_model(self.snapshot, SandboxSnapshot, "snapshot")
        _require_snapshot_boundary_types(snapshot)
        custodies = tuple(
            _canonical_model(custody, SandboxSubmitCustody, "submit_custodies")
            for custody in self.submit_custodies
        )
        if len(self.executed_command_ids) != len(set(self.executed_command_ids)):
            raise ValueError("executed_command_ids must not contain duplicates")

        custody_command_ids = tuple(custody.command_id for custody in custodies)
        custody_order_ids = tuple(custody.order_id for custody in custodies)
        custody_client_order_ids = tuple(
            custody.client_order_id for custody in custodies
        )
        if len(custody_command_ids) != len(set(custody_command_ids)):
            raise ValueError("custody command_id must be unique")
        if len(custody_order_ids) != len(set(custody_order_ids)):
            raise ValueError("custody order_id must be unique")
        if len(custody_client_order_ids) != len(set(custody_client_order_ids)):
            raise ValueError("custody client_order_id must be unique")

        executed_command_ids = set(self.executed_command_ids)
        for custody in custodies:
            if custody.command_id not in executed_command_ids:
                raise ValueError(
                    "custody command_id must occur in executed_command_ids"
                )
            matching_orders = tuple(
                order for order in snapshot.orders if order.order_id == custody.order_id
            )
            if len(matching_orders) != 1:
                raise ValueError("custody must bind exactly one snapshot order")
            snapshot_order = matching_orders[0]
            if (
                custody.client_order_id != snapshot_order.client_order_id
                or custody.client_order_id
                != snapshot_order.order_intent.client_order_id
            ):
                raise ValueError(
                    "custody client_order_id must match the snapshot order intent"
                )
            if (
                canonical_model_digest(snapshot_order.order_intent)
                != custody.prepared_permit.intent_digest
            ):
                raise ValueError(
                    "snapshot order intent_digest must match prepared_permit"
                )
            if custody.consumed_authority.consumed_at > snapshot.current_time:
                raise ValueError(
                    "consumed_authority consumed_at cannot follow snapshot current_time"
                )

        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "submit_custodies", custodies)
        return self

    @property
    def digest(self) -> str:
        return canonical_model_digest(self)


__all__ = ["SandboxRecoveryCheckpoint", "SandboxSubmitCustody"]
