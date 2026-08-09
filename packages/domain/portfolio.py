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


class ExposureSnapshot(DomainModel):
    currency: Currency
    gross: Money
    net: Money
    pending: Money

    @model_validator(mode="after")
    def _valid_exposure(self) -> "ExposureSnapshot":
        if any(
            value.currency is not self.currency
            for value in (self.gross, self.net, self.pending)
        ):
            raise ValueError(
                "exposure money currency must match exposure currency"
            )
        if self.gross.amount < 0 or self.pending.amount < 0:
            raise ValueError("gross and pending exposure must be non-negative")
        if self.gross.amount < abs(self.net.amount):
            raise ValueError("gross exposure must cover absolute net exposure")
        return self


class InstrumentExposureSnapshot(DomainModel):
    instrument: InstrumentId
    exposure: ExposureSnapshot


class StrategyExposureSnapshot(DomainModel):
    strategy_id: CanonicalPortfolioIdentifier
    exposure: ExposureSnapshot


class VenueExposureSnapshot(DomainModel):
    venue_id: CanonicalPortfolioIdentifier
    exposure: ExposureSnapshot


class AccountPortfolioSnapshot(DomainModel):
    snapshot_id: UUID
    account_id: CanonicalPortfolioIdentifier
    reporting_currency: Currency
    balances: tuple[AccountBalanceSnapshot, ...]
    positions: tuple[AccountPositionSnapshot, ...]
    total_exposure: ExposureSnapshot
    instrument_exposures: tuple[InstrumentExposureSnapshot, ...]
    strategy_exposures: tuple[StrategyExposureSnapshot, ...]
    venue_exposures: tuple[VenueExposureSnapshot, ...]
    observed_at: datetime
    schema_version: NonEmptyText

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_snapshot(self) -> "AccountPortfolioSnapshot":
        balance_keys = tuple(balance.currency.code for balance in self.balances)
        if len(balance_keys) != len(set(balance_keys)):
            raise ValueError("duplicate balance currency")
        if balance_keys != tuple(sorted(balance_keys)):
            raise ValueError("balances must be ordered by currency")

        position_keys = tuple(
            (position.strategy_id, position.instrument.canonical)
            for position in self.positions
        )
        if len(position_keys) != len(set(position_keys)):
            raise ValueError("duplicate position key")
        if position_keys != tuple(sorted(position_keys)):
            raise ValueError("positions must be ordered by strategy and instrument")

        instrument_keys = tuple(
            item.instrument.canonical for item in self.instrument_exposures
        )
        if len(instrument_keys) != len(set(instrument_keys)):
            raise ValueError("duplicate instrument exposure key")
        if instrument_keys != tuple(sorted(instrument_keys)):
            raise ValueError("instrument_exposures must be ordered by instrument")

        strategy_keys = tuple(
            item.strategy_id for item in self.strategy_exposures
        )
        if len(strategy_keys) != len(set(strategy_keys)):
            raise ValueError("duplicate strategy exposure key")
        if strategy_keys != tuple(sorted(strategy_keys)):
            raise ValueError("strategy_exposures must be ordered by strategy")

        venue_keys = tuple(item.venue_id for item in self.venue_exposures)
        if len(venue_keys) != len(set(venue_keys)):
            raise ValueError("duplicate venue exposure key")
        if venue_keys != tuple(sorted(venue_keys)):
            raise ValueError("venue_exposures must be ordered by venue")

        if any(balance.account_id != self.account_id for balance in self.balances):
            raise ValueError("balance account must match portfolio account")
        if any(position.account_id != self.account_id for position in self.positions):
            raise ValueError("position account must match portfolio account")
        if any(balance.observed_at > self.observed_at for balance in self.balances):
            raise ValueError("balance timestamp must not be after portfolio observation")
        if any(position.observed_at > self.observed_at for position in self.positions):
            raise ValueError("position timestamp must not be after portfolio observation")

        if self.total_exposure.currency is not self.reporting_currency:
            raise ValueError("total exposure currency must match reporting currency")
        if any(
            item.exposure.currency is not self.reporting_currency
            for item in self.instrument_exposures
        ):
            raise ValueError("instrument exposure currency must match reporting currency")
        if any(
            item.exposure.currency is not self.reporting_currency
            for item in self.strategy_exposures
        ):
            raise ValueError("strategy exposure currency must match reporting currency")
        if any(
            item.exposure.currency is not self.reporting_currency
            for item in self.venue_exposures
        ):
            raise ValueError("venue exposure currency must match reporting currency")
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
