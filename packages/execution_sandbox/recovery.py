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

from .models import SandboxModel, SandboxSnapshot


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


class SandboxSubmitCustody(SandboxModel):
    """Recovery evidence only; grants neither submit nor retry authority."""

    command_id: UUID
    order_id: UUID
    client_order_id: CanonicalIdentifier
    prepared_permit: PreparedSubmitPermit
    consumed_authority: ConsumedSubmitAuthority

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

    @field_validator("created_at")
    @classmethod
    def _utc_created_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_checkpoint(self) -> "SandboxRecoveryCheckpoint":
        snapshot = _canonical_model(self.snapshot, SandboxSnapshot, "snapshot")
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
