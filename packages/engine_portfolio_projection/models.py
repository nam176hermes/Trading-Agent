"""Immutable inputs and outputs for the pure P1 portfolio projection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias
from uuid import UUID

from packages.domain import (
    InstrumentDefinition,
    LiquiditySide,
    OrderEvent,
    PortfolioAccountObservationEntry,
    PortfolioFillEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    ReconciliationSource,
)
from packages.nautilus_runtime_contracts.artifacts import P1InstrumentCatalogV1


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
