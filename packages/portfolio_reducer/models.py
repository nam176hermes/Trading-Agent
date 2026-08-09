"""Immutable working state for the pure portfolio accounting reducer."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from packages.domain.instruments import InstrumentDefinition, InstrumentId
from packages.domain.orders import FillEvent
from packages.domain.portfolio import (
    AccountBalanceSnapshot,
    CanonicalPortfolioIdentifier,
    DomainModel,
    NonEmptyText,
    PositionMark,
)
from packages.domain.portfolio_events import PortfolioFillEntry, PortfolioReconciliationEntry
from packages.domain.primitives import Currency, FiniteDecimal, Money, Price, Quantity


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PortfolioReplayError(ValueError):
    """Raised when portfolio accounting input cannot be reduced exactly."""


class PortfolioStreamCursor(DomainModel):
    stream_id: UUID
    sequence: Annotated[int, Field(gt=0)]


class PortfolioAppliedEvent(DomainModel):
    event_id: UUID
    digest: Sha256


class PortfolioValuationRateState(DomainModel):
    """Reserved immutable state for a later explicit valuation-rate entry."""

    source_currency: Currency
    target_currency: Currency
    rate: FiniteDecimal
    quoted_at: datetime
    provenance_id: CanonicalPortfolioIdentifier


class PortfolioPositionState(DomainModel):
    """Accounting position state; unlike an observed snapshot it may be unmarked."""

    account_id: CanonicalPortfolioIdentifier
    strategy_id: CanonicalPortfolioIdentifier
    instrument: InstrumentId
    instrument_definition: InstrumentDefinition | None
    settlement_currency: Currency
    quantity: Quantity
    mark: PositionMark | None = None
    average_entry_price: Price | None = None
    realized_pnl: Money
    unrealized_pnl: Money
    fees: Money
    funding: Money
    observed_at: datetime
    schema_version: NonEmptyText

    @model_validator(mode="after")
    def _valid_state(self) -> "PortfolioPositionState":
        if self.instrument_definition is not None:
            if self.instrument_definition.instrument_id != self.instrument:
                raise ValueError("instrument definition must match position instrument")
            if self.instrument_definition.settlement_currency is not self.settlement_currency:
                raise ValueError("instrument definition settlement currency must match position")
        if any(
            value.currency is not self.settlement_currency
            for value in (self.realized_pnl, self.unrealized_pnl, self.fees, self.funding)
        ):
            raise ValueError("position money must match settlement currency")
        if self.quantity.value == 0:
            if self.average_entry_price is not None or self.mark is not None:
                raise ValueError("zero position must not retain an average entry price or mark")
        else:
            if self.instrument_definition is None:
                raise ValueError("non-zero position requires an instrument definition")
            if self.average_entry_price is None:
                raise ValueError("non-zero position requires an average entry price")
            if self.average_entry_price.currency is not self.settlement_currency:
                raise ValueError("average entry price currency must match settlement currency")
        return self


class PortfolioWorkingSnapshot(DomainModel):
    account_id: CanonicalPortfolioIdentifier
    reporting_currency: Currency
    balances: tuple[AccountBalanceSnapshot, ...]
    positions: tuple[PortfolioPositionState, ...]
    observed_at: datetime
    schema_version: NonEmptyText

    @model_validator(mode="after")
    def _ordered_state(self) -> "PortfolioWorkingSnapshot":
        balance_keys = tuple(balance.currency.code for balance in self.balances)
        if balance_keys != tuple(sorted(set(balance_keys))):
            raise ValueError("balances must be sorted and unique")
        if any(balance.account_id != self.account_id for balance in self.balances):
            raise ValueError("balance account must match portfolio account")
        position_keys = tuple(
            (position.strategy_id, position.instrument.canonical)
            for position in self.positions
        )
        if position_keys != tuple(sorted(set(position_keys))):
            raise ValueError("positions must be sorted and unique")
        if any(position.account_id != self.account_id for position in self.positions):
            raise ValueError("position account must match portfolio account")
        return self


class PortfolioExecutionEffect(DomainModel):
    """Exact normal-execution deltas retained for one correction or bust."""

    execution_id: UUID
    account_id: CanonicalPortfolioIdentifier
    strategy_id: CanonicalPortfolioIdentifier
    fill: FillEvent
    entry: PortfolioFillEntry
    logical_sequence: Annotated[int, Field(gt=0)]
    cash_deltas: tuple[Money, ...]
    balance_realized_pnl_deltas: tuple[Money, ...]
    balance_fee_deltas: tuple[Money, ...]
    position_key: tuple[CanonicalPortfolioIdentifier, str]
    quantity_delta: Quantity
    realized_pnl_delta: Money
    fees_delta: Money
    average_before: Price | None
    average_after: Price | None

    @model_validator(mode="after")
    def _valid_effect(self) -> "PortfolioExecutionEffect":
        if self.execution_id != self.fill.execution_id:
            raise ValueError("effect execution ID must match fill")
        if self.entry.fill != self.fill:
            raise ValueError("effect entry must match fill")
        if self.entry.account_id != self.account_id or self.entry.strategy_id != self.strategy_id:
            raise ValueError("effect entry scope must match")
        if self.strategy_id != self.position_key[0]:
            raise ValueError("effect position key strategy does not match")
        for name, deltas in (
            ("cash deltas", self.cash_deltas),
            ("balance realized PnL deltas", self.balance_realized_pnl_deltas),
            ("balance fee deltas", self.balance_fee_deltas),
        ):
            currencies = tuple(delta.currency.code for delta in deltas)
            if currencies != tuple(sorted(set(currencies))):
                raise ValueError(f"{name} must be sorted and unique")
        settlement = self.fill.instrument_definition.settlement_currency
        if self.realized_pnl_delta.currency is not settlement or self.fees_delta.currency is not settlement:
            raise ValueError("position deltas must use settlement currency")
        return self


class PortfolioReplayState(DomainModel):
    snapshot: PortfolioWorkingSnapshot
    cursor: tuple[PortfolioStreamCursor, ...]
    applied_events: tuple[PortfolioAppliedEvent, ...]
    valuation_rates: tuple[PortfolioValuationRateState, ...] = ()
    active_effects: tuple[PortfolioExecutionEffect, ...] = ()
    reconciliation: PortfolioReconciliationEntry | None = None

    @model_validator(mode="after")
    def _ordered_state(self) -> "PortfolioReplayState":
        cursor_keys = tuple(item.stream_id.bytes for item in self.cursor)
        if cursor_keys != tuple(sorted(set(cursor_keys))):
            raise ValueError("cursor must be sorted and unique")
        applied_keys = tuple(item.event_id.bytes for item in self.applied_events)
        if applied_keys != tuple(sorted(set(applied_keys))):
            raise ValueError("applied events must be sorted and unique")
        rate_keys = tuple(
            (item.source_currency.code, item.target_currency.code)
            for item in self.valuation_rates
        )
        if rate_keys != tuple(sorted(set(rate_keys))):
            raise ValueError("valuation rates must be sorted and unique")
        effect_keys = tuple(item.execution_id.bytes for item in self.active_effects)
        if effect_keys != tuple(sorted(set(effect_keys))):
            raise ValueError("active effects must be sorted and unique")
        return self

    @property
    def active_execution_ids(self) -> tuple[UUID, ...]:
        return tuple(effect.execution_id for effect in self.active_effects)
