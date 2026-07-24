"""Strict target and observed portfolio payload contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .clock import require_utc
from .instruments import InstrumentId
from .primitives import FiniteDecimal, Price, Quantity


NonEmptyText = Annotated[str, Field(min_length=1)]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class TargetPosition(DomainModel):
    instrument: InstrumentId
    target_weight: FiniteDecimal


class TargetPortfolio(DomainModel):
    target_id: UUID
    positions: tuple[TargetPosition, ...]
    source_signal_ids: tuple[UUID, ...] = Field(min_length=1)
    effective_at: datetime
    schema_version: NonEmptyText

    @field_validator("effective_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_positions(self) -> "TargetPortfolio":
        instruments = [position.instrument.canonical for position in self.positions]
        if len(instruments) != len(set(instruments)):
            raise ValueError("positions contain duplicate instruments")
        if len(self.source_signal_ids) != len(set(self.source_signal_ids)):
            raise ValueError("source_signal_ids contain duplicates")
        if sum((abs(position.target_weight) for position in self.positions), Decimal(0)) > Decimal(1):
            raise ValueError("total absolute target weight must be <= 1")
        return self


class PositionSnapshot(DomainModel):
    instrument: InstrumentId
    quantity: Quantity
    observed_at: datetime
    mark_price: Price | None = None

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class PortfolioSnapshot(DomainModel):
    snapshot_id: UUID
    positions: tuple[PositionSnapshot, ...]
    observed_at: datetime
    schema_version: NonEmptyText

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _unique_instruments(self) -> "PortfolioSnapshot":
        instruments = [position.instrument.canonical for position in self.positions]
        if len(instruments) != len(set(instruments)):
            raise ValueError("positions contain duplicate instruments")
        return self
