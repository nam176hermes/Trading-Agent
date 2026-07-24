"""Strict risk-state and deterministic-decision payload contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .clock import require_utc
from .portfolio import PortfolioSnapshot, TargetPortfolio


NonEmptyText = Annotated[str, Field(min_length=1)]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class RiskOutcome(str, Enum):
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"


class RiskReasonCode(str, Enum):
    """Closed reason taxonomy in deterministic risk-check order."""

    WITHIN_LIMITS = "WITHIN_LIMITS"
    DATA_STALE = "DATA_STALE"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    MODEL_NOT_APPROVED = "MODEL_NOT_APPROVED"
    PRICE_OUTSIDE_COLLAR = "PRICE_OUTSIDE_COLLAR"
    ORDER_NOTIONAL_LIMIT = "ORDER_NOTIONAL_LIMIT"
    GROSS_EXPOSURE_LIMIT = "GROSS_EXPOSURE_LIMIT"
    MARGIN_BUFFER_LIMIT = "MARGIN_BUFFER_LIMIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    VENUE_DEGRADED = "VENUE_DEGRADED"
    DUPLICATE_COMMAND = "DUPLICATE_COMMAND"
    GLOBAL_HALT = "GLOBAL_HALT"


_REASON_ORDER = {reason: index for index, reason in enumerate(RiskReasonCode)}


def _canonical_position_targets(target: TargetPortfolio) -> tuple[tuple[str, Decimal], ...]:
    return tuple(
        sorted(
            (
                (position.instrument.canonical, position.target_weight)
                for position in target.positions
            ),
            key=lambda item: item[0],
        )
    )


class RiskStateSnapshot(DomainModel):
    model_config = ConfigDict(
        json_schema_extra={
            "x-temporal-invariants": [
                "portfolio.observed_at <= observed_at",
                "portfolio.positions[*].observed_at <= portfolio.observed_at",
            ]
        }
    )

    state_id: UUID
    portfolio: PortfolioSnapshot
    open_order_ids: tuple[UUID, ...]
    kill_switch_engaged: bool
    observed_at: datetime
    schema_version: NonEmptyText

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_snapshot(self) -> "RiskStateSnapshot":
        if len(self.open_order_ids) != len(set(self.open_order_ids)):
            raise ValueError("open_order_ids contain duplicates")
        if self.portfolio.observed_at > self.observed_at:
            raise ValueError("portfolio snapshot must not be observed after risk state")
        if any(
            position.observed_at > self.portfolio.observed_at
            for position in self.portfolio.positions
        ):
            raise ValueError("position snapshot must not be observed after portfolio snapshot")
        return self


class RiskDecision(DomainModel):
    model_config = ConfigDict(
        json_schema_extra={
            "x-risk-invariants": [
                "outcome == approved => approved_target == original_target",
                "outcome == modified => approved_target has a new identity and changed positions",
                "outcome == rejected => approved_target == null",
                "state_snapshot.observed_at <= decided_at",
                "original_target.effective_at <= decided_at",
                "kill_switch_engaged => outcome == rejected and GLOBAL_HALT in reason_codes",
            ]
        }
    )

    decision_id: UUID
    original_target: TargetPortfolio
    approved_target: TargetPortfolio | None
    outcome: RiskOutcome
    reason_codes: tuple[RiskReasonCode, ...] = Field(min_length=1)
    policy_version: NonEmptyText
    state_snapshot: RiskStateSnapshot
    decided_at: datetime
    schema_version: NonEmptyText

    @field_validator("decided_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_decision_semantics(self) -> "RiskDecision":
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes contain duplicates")
        if tuple(sorted(self.reason_codes, key=_REASON_ORDER.__getitem__)) != self.reason_codes:
            raise ValueError("reason_codes must follow canonical risk-check order")

        if self.outcome is RiskOutcome.APPROVED:
            if self.approved_target != self.original_target:
                raise ValueError("approved outcome must preserve the original target exactly")
            if self.reason_codes != (RiskReasonCode.WITHIN_LIMITS,):
                raise ValueError("approved outcome requires only WITHIN_LIMITS")
        elif self.outcome is RiskOutcome.MODIFIED:
            if self.approved_target is None:
                raise ValueError("modified outcome requires an approved target")
            if self.approved_target.target_id == self.original_target.target_id:
                raise ValueError("modified outcome requires a distinct target identity")
            if _canonical_position_targets(
                self.approved_target
            ) == _canonical_position_targets(self.original_target):
                raise ValueError("modified outcome requires changed target positions")
            if (
                self.approved_target.source_signal_ids
                != self.original_target.source_signal_ids
                or self.approved_target.effective_at != self.original_target.effective_at
                or self.approved_target.schema_version != self.original_target.schema_version
            ):
                raise ValueError("modified outcome must preserve target provenance")
            if RiskReasonCode.WITHIN_LIMITS in self.reason_codes:
                raise ValueError("modified outcome cannot use WITHIN_LIMITS")
        else:
            if self.approved_target is not None:
                raise ValueError("rejected outcome must not contain an approved target")
            if RiskReasonCode.WITHIN_LIMITS in self.reason_codes:
                raise ValueError("rejected outcome cannot use WITHIN_LIMITS")

        if self.state_snapshot.observed_at > self.decided_at:
            raise ValueError("state snapshot must not be observed after the decision")
        if self.original_target.effective_at > self.decided_at:
            raise ValueError("original target must not be effective after the decision")

        halted = self.state_snapshot.kill_switch_engaged
        has_global_halt = RiskReasonCode.GLOBAL_HALT in self.reason_codes
        if halted and (self.outcome is not RiskOutcome.REJECTED or not has_global_halt):
            raise ValueError("kill switch requires rejection with GLOBAL_HALT")
        if not halted and has_global_halt:
            raise ValueError("GLOBAL_HALT requires an engaged kill switch")
        return self
