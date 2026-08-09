"""Strict immutable payloads for one portfolio's accounting ledger stream."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import field_validator, model_validator

from .clock import require_utc
from .instruments import InstrumentId
from .orders import FillEvent
from .portfolio import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    CanonicalPortfolioIdentifier,
    DomainModel,
    NonEmptyText,
    PositionMark,
)
from .primitives import Currency, CurrencyConversion, FiniteDecimal, Money


class PortfolioLedgerEntry(DomainModel):
    """Common account and temporal authority for every portfolio payload."""

    account_id: CanonicalPortfolioIdentifier
    effective_at: datetime
    schema_version: NonEmptyText

    @field_validator("effective_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class PortfolioOpeningEntry(PortfolioLedgerEntry):
    reporting_currency: Currency
    balances: tuple[AccountBalanceSnapshot, ...]
    source_id: NonEmptyText
    source_revision: NonEmptyText

    @model_validator(mode="after")
    def _valid_opening(self) -> "PortfolioOpeningEntry":
        balance_keys = tuple(balance.currency.code for balance in self.balances)
        if len(balance_keys) != len(set(balance_keys)):
            raise ValueError("duplicate opening balance currency")
        if balance_keys != tuple(sorted(balance_keys)):
            raise ValueError("opening balances must be ordered by currency")
        if any(balance.account_id != self.account_id for balance in self.balances):
            raise ValueError("opening balance account must match entry account")
        if any(balance.observed_at > self.effective_at for balance in self.balances):
            raise ValueError("opening balance timestamp must not be after effective time")
        return self


class PortfolioFillEntry(PortfolioLedgerEntry):
    strategy_id: CanonicalPortfolioIdentifier
    fill: FillEvent

    @model_validator(mode="after")
    def _exact_fill(self) -> "PortfolioFillEntry":
        if type(self.fill) is not FillEvent:
            raise ValueError("fill must be an exact FillEvent")
        return self


class PortfolioMarkEntry(PortfolioLedgerEntry):
    instrument: InstrumentId
    mark: PositionMark
    marked_at: datetime

    @field_validator("marked_at")
    @classmethod
    def _utc_marked_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _matching_mark_time(self) -> "PortfolioMarkEntry":
        if self.marked_at != self.mark.marked_at:
            raise ValueError("marked_at must match mark.marked_at")
        return self


class PortfolioFundingEntry(PortfolioLedgerEntry):
    funding_id: UUID
    strategy_id: CanonicalPortfolioIdentifier | None
    instrument: InstrumentId | None
    amount: Money
    provenance_id: CanonicalPortfolioIdentifier

    @model_validator(mode="after")
    def _complete_position_key(self) -> "PortfolioFundingEntry":
        if (self.strategy_id is None) != (self.instrument is None):
            raise ValueError("funding position key must provide strategy_id and instrument together")
        return self


class PortfolioConversionEntry(PortfolioLedgerEntry):
    conversion: CurrencyConversion
    provenance_id: CanonicalPortfolioIdentifier


class PortfolioValuationRateEntry(PortfolioLedgerEntry):
    source_currency: Currency
    target_currency: Currency
    rate: FiniteDecimal
    quoted_at: datetime
    provenance_id: CanonicalPortfolioIdentifier

    @field_validator("quoted_at")
    @classmethod
    def _utc_quoted_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @field_validator("rate")
    @classmethod
    def _positive_rate(cls, value: FiniteDecimal) -> FiniteDecimal:
        if value <= 0:
            raise ValueError("valuation rate must be positive")
        return value

    @model_validator(mode="after")
    def _cross_currency_rate(self) -> "PortfolioValuationRateEntry":
        if self.source_currency is self.target_currency:
            raise ValueError("valuation rate currencies must differ")
        return self


class PortfolioReconciliationSource(str, Enum):
    """Closed origins for an account-level portfolio reconciliation."""

    VENUE = "venue"
    DROP_COPY = "drop_copy"
    CLEARING = "clearing"


class PortfolioReconciliationEntry(PortfolioLedgerEntry):
    reconciliation_id: UUID
    source: PortfolioReconciliationSource
    source_revision: NonEmptyText
    snapshot: AccountPortfolioSnapshot

    @model_validator(mode="after")
    def _matching_reconciliation_snapshot(self) -> "PortfolioReconciliationEntry":
        if self.snapshot.account_id != self.account_id:
            raise ValueError("reconciliation snapshot account must match entry account")
        if self.snapshot.observed_at > self.effective_at:
            raise ValueError("reconciliation snapshot must not be after effective time")
        return self
