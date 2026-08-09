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
from .primitives import Currency, FiniteDecimal, Money, Price, Quantity


NonEmptyText = Annotated[str, Field(min_length=1)]
CanonicalPortfolioIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    ),
]


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


def _require_money_currency(
    currency: Currency, values: tuple[Money, ...], currency_name: str
) -> None:
    if any(value.currency is not currency for value in values):
        raise ValueError(f"money currency must match {currency_name} currency")


class AccountBalanceSnapshot(DomainModel):
    account_id: CanonicalPortfolioIdentifier
    currency: Currency
    cash: Money
    locked_funds: Money
    margin_used: Money
    realized_pnl: Money
    unrealized_pnl: Money
    fees: Money
    funding: Money
    observed_at: datetime
    schema_version: NonEmptyText

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_balance(self) -> "AccountBalanceSnapshot":
        _require_money_currency(
            self.currency,
            (
                self.cash,
                self.locked_funds,
                self.margin_used,
                self.realized_pnl,
                self.unrealized_pnl,
                self.fees,
                self.funding,
            ),
            "snapshot",
        )
        if self.locked_funds.amount < 0:
            raise ValueError("locked_funds must not be negative")
        if self.margin_used.amount < 0:
            raise ValueError("margin_used must not be negative")
        return self


class PositionMark(DomainModel):
    price: Price
    marked_at: datetime
    provenance_id: CanonicalPortfolioIdentifier

    @field_validator("marked_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class AccountPositionSnapshot(DomainModel):
    account_id: CanonicalPortfolioIdentifier
    strategy_id: CanonicalPortfolioIdentifier
    instrument: InstrumentId
    settlement_currency: Currency
    quantity: Quantity
    mark: PositionMark | None
    realized_pnl: Money
    unrealized_pnl: Money
    fees: Money
    funding: Money
    observed_at: datetime
    schema_version: NonEmptyText

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_position(self) -> "AccountPositionSnapshot":
        _require_money_currency(
            self.settlement_currency,
            (self.realized_pnl, self.unrealized_pnl, self.fees, self.funding),
            "settlement",
        )
        if self.mark is not None:
            if self.mark.price.currency is not self.settlement_currency:
                raise ValueError("mark currency must match settlement currency")
            if self.mark.marked_at > self.observed_at:
                raise ValueError("mark timestamp must not be after observation")
        elif self.quantity.value != 0:
            raise ValueError("non-zero position requires a mark")
        return self


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
