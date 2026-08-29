"""Immutable inputs and outputs for the pure P1 portfolio projection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias
from uuid import UUID

from packages.domain import (
    Currency,
    InstrumentDefinition,
    LiquiditySide,
    Money,
    OrderEvent,
    PortfolioFillEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    ReconciliationSource,
)
from packages.domain.portfolio_events import PortfolioLedgerEntry
from packages.nautilus_runtime_contracts.artifacts import P1InstrumentCatalogV1
from pydantic import model_validator


class PortfolioAccountObservationEntry(PortfolioLedgerEntry):
    """Exact final account facts emitted by the validated P1 stream."""

    currency: Currency
    cash_balance: Money
    fees: Money
    realized_pnl: Money
    unrealized_pnl: Money

    @model_validator(mode="after")
    def _one_currency(self) -> "PortfolioAccountObservationEntry":
        if any(
            value.currency is not self.currency
            for value in (
                self.cash_balance,
                self.fees,
                self.realized_pnl,
                self.unrealized_pnl,
            )
        ):
            raise ValueError("account observation money must use one currency")
        return self


PortfolioEntry: TypeAlias = (
    PortfolioOpeningEntry
    | PortfolioFillEntry
    | PortfolioMarkEntry
    | PortfolioAccountObservationEntry
)


@dataclass(frozen=True, slots=True)
class ProjectionAuthority:
    """Exact non-runtime authority missing from the typed P1 event stream."""

    request_message_id: UUID
    catalog: P1InstrumentCatalogV1
    instrument: InstrumentDefinition
    opening: PortfolioOpeningEntry
    strategy_id: str
    liquidity_side: LiquiditySide
    reconciliation_source: ReconciliationSource


@dataclass(frozen=True, slots=True)
class ProjectedPortfolioEntry:
    source_message_id: UUID
    event_id: UUID
    source_sequence: int
    entry: PortfolioEntry


@dataclass(frozen=True, slots=True)
class ProjectedAccounting:
    cash_balance: Decimal
    position_quantity: Decimal
    fees: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioProjection:
    order_events: tuple[OrderEvent, ...]
    entries: tuple[ProjectedPortfolioEntry, ...]
    accounting: ProjectedAccounting
    canonical_identity: str
