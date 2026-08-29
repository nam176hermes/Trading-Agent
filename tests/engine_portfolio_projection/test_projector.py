from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from packages.domain import (
    AccountBalanceSnapshot,
    AssetClass,
    Currency,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    PortfolioFillEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.nautilus_runtime_contracts.events import (
    P1AccountObserved,
    P1Fill,
    P1OrderSubmitted,
    P1PositionObserved,
    P1RunCompleted,
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
)
from packages.nautilus_runtime_contracts.semantic import semantic_digest


NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
LATER = datetime(2026, 8, 5, 12, 1, tzinfo=UTC)
DIGEST = "a" * 64
REQUEST_A = UUID("10000000-0000-4000-8000-000000000001")
REQUEST_B = UUID("10000000-0000-4000-8000-000000000002")


def _money(value: str) -> Money:
    return Money(Decimal(value), Currency.USDT)


def _instrument(*, settlement: Currency = Currency.USDT) -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE"),
        raw_symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=settlement,
        settlement_currency=settlement,
        tick_size=Price(Decimal("0.01"), settlement),
        size_increment=OrderQuantity(Decimal("0.000001"), 6),
        minimum_quantity=OrderQuantity(Decimal("0.000001"), 6),
        maximum_quantity=OrderQuantity(Decimal("1000000"), 6),
        minimum_notional=Money(Decimal("0.01"), settlement),
        maximum_notional=Money(Decimal("100000000"), settlement),
        multiplier=Decimal("1"),
        margin=None,
        session_calendar="24X7",
        provenance=InstrumentProvenance("P1CATALOG", "R1", NOW),
    )


def _balance() -> AccountBalanceSnapshot:
    zero = _money("0")
    return AccountBalanceSnapshot(
        account_id="account-1",
        currency=Currency.USDT,
        cash=_money("1000"),
        locked_funds=zero,
        margin_used=zero,
        realized_pnl=zero,
        unrealized_pnl=zero,
        fees=zero,
        funding=zero,
        observed_at=NOW,
        schema_version="balance-v1",
    )


def _opening() -> PortfolioOpeningEntry:
    return PortfolioOpeningEntry(
        account_id="account-1",
        reporting_currency=Currency.USDT,
        balances=(_balance(),),
        source_id="p1-opening",
        source_revision="r1",
        effective_at=NOW,
        schema_version="portfolio-entry-v1",
    )


def _stream(
    *,
    first_native_order: str = "native-order-a",
    first_native_fill: str = "native-fill-a",
) -> tuple[object, ...]:
    events: tuple[object, ...] = (
        P1RunStarted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="RunStarted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=2,
            simulation_time=NOW,
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit="b" * 40,
            closure_digest=DIGEST,
            config_digest=DIGEST,
            catalog_digest=DIGEST,
            data_digest=DIGEST,
        ),
        P1TargetAccepted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetAccepted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=3,
            simulation_time=NOW,
            target_id="target-1",
            source_signal_ids=("signal-1",),
            target_weight=Decimal("1"),
        ),
        P1TargetQuantityPlanned(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetQuantityPlanned",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=4,
            simulation_time=NOW,
            target_id="target-1",
            quantity=Decimal("1"),
        ),
        P1OrderSubmitted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="OrderSubmitted",
            origin="CONTROL_PLANE",
            native_type="Order",
            sequence=5,
            simulation_time=NOW,
            client_order_id="order-1",
            native_order_id=first_native_order,
            target_id="target-1",
            source_signal_ids=("signal-1",),
            side="BUY",
            quantity=Decimal("1"),
            order_type="MARKET",
        ),
        P1Fill(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="Fill",
            origin="NAUTILUS_CALLBACK",
            native_type="OrderFilled",
            sequence=6,
            simulation_time=NOW,
            client_order_id="order-1",
            native_fill_id=first_native_fill,
            side="BUY",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("0.1"),
            fee_currency="USDT",
        ),
        P1PositionObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="PositionObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Position",
            sequence=7,
            simulation_time=NOW,
            quantity=Decimal("1"),
            average_entry_price=Decimal("100"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("1"),
        ),
        P1AccountObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="AccountObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Account",
            sequence=8,
            simulation_time=NOW,
            cash_balance=Decimal("899.9"),
            fees=Decimal("0.1"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("1"),
        ),
        P1TargetAccepted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetAccepted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=9,
            simulation_time=LATER,
            target_id="target-2",
            source_signal_ids=("signal-2",),
            target_weight=Decimal("0"),
        ),
        P1TargetQuantityPlanned(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetQuantityPlanned",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=10,
            simulation_time=LATER,
            target_id="target-2",
            quantity=Decimal("1"),
        ),
        P1OrderSubmitted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="OrderSubmitted",
            origin="CONTROL_PLANE",
            native_type="Order",
            sequence=11,
            simulation_time=LATER,
            client_order_id="order-2",
            native_order_id="native-order-b",
            target_id="target-2",
            source_signal_ids=("signal-2",),
            side="SELL",
            quantity=Decimal("1"),
            order_type="MARKET",
        ),
        P1Fill(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="Fill",
            origin="NAUTILUS_CALLBACK",
            native_type="OrderFilled",
            sequence=12,
            simulation_time=LATER,
            client_order_id="order-2",
            native_fill_id="native-fill-b",
            side="SELL",
            quantity=Decimal("1"),
            price=Decimal("102"),
            fee=Decimal("0.1"),
            fee_currency="USDT",
        ),
        P1PositionObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="PositionObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Position",
            sequence=13,
            simulation_time=LATER,
            quantity=Decimal("0"),
            average_entry_price=Decimal("0"),
            realized_pnl=Decimal("2"),
            unrealized_pnl=Decimal("0"),
        ),
        P1AccountObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="AccountObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Account",
            sequence=14,
            simulation_time=LATER,
            cash_balance=Decimal("1001.8"),
            fees=Decimal("0.2"),
            realized_pnl=Decimal("2"),
            unrealized_pnl=Decimal("0"),
        ),
        P1RunCompleted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="RunCompleted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=15,
            simulation_time=LATER,
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit="b" * 40,
            closure_digest=DIGEST,
            target_count=2,
            order_count=2,
            fill_count=2,
            final_cash=Decimal("1001.8"),
            final_position=Decimal("0"),
            fees=Decimal("0.2"),
            realized_pnl=Decimal("2"),
            unrealized_pnl=Decimal("0"),
            semantic_digest="0" * 64,
        ),
    )
    typed = tuple(events)
    return typed[:-1] + (
        typed[-1].model_copy(update={"semantic_digest": semantic_digest(typed)}),
    )


def _project(events: tuple[object, ...] | None = None, *, request_id: UUID = REQUEST_A):
    try:
        from packages.engine_portfolio_projection import (
            ProjectionAuthority,
            project_portfolio,
        )
    except ModuleNotFoundError:
        pytest.fail("P1 portfolio projection API is missing")
    return project_portfolio(
        events or _stream(),
        ProjectionAuthority(
            request_message_id=request_id,
            instrument=_instrument(),
            opening=_opening(),
            strategy_id="strategy-1",
            liquidity_side=LiquiditySide.TAKER,
            reconciliation_source=ReconciliationSource.VENUE,
        ),
    )


def _redigest(events: tuple[object, ...]) -> tuple[object, ...]:
    return events[:-1] + (
        events[-1].model_copy(update={"semantic_digest": semantic_digest(events)}),
    )


def test_zero_long_flat_projects_exact_opening_fills_mark_and_accounting() -> None:
    projection = _project()

    assert tuple(event.target_status for event in projection.order_events) == (
        OrderStatus.SUBMITTED,
        OrderStatus.FILLED,
        OrderStatus.SUBMITTED,
        OrderStatus.FILLED,
    )
    assert tuple(type(item.entry) for item in projection.entries) == (
        PortfolioOpeningEntry,
        PortfolioFillEntry,
        PortfolioMarkEntry,
        PortfolioFillEntry,
    )
    first_fill = projection.entries[1].entry.fill
    assert first_fill.status is FillReportStatus.FILLED
    assert first_fill.side is OrderSide.BUY
    assert first_fill.quantity.value == Decimal("1.000000")
    assert first_fill.last_fill_price.amount == Decimal("100")
    assert first_fill.commission == _money("0.1")
    assert projection.entries[2].entry.mark.price.amount == Decimal("101")
    assert projection.accounting.cash_balance == Decimal("1001.8")
    assert projection.accounting.position_quantity == Decimal("0")
    assert projection.accounting.fees == Decimal("0.2")
    assert projection.accounting.realized_pnl == Decimal("2")
    assert projection.accounting.unrealized_pnl == Decimal("0")


def test_source_ids_follow_custody_but_business_identity_is_stable() -> None:
    first = _project(_stream(), request_id=REQUEST_A)
    changed = _project(
        _stream(
            first_native_order="different-native-order",
            first_native_fill="different-native-fill",
        ),
        request_id=REQUEST_B,
    )

    assert first.entries[0].source_message_id != changed.entries[0].source_message_id
    assert first.entries[0].event_id != changed.entries[0].event_id
    assert first.entries[1].entry.fill.venue_trade_id != changed.entries[1].entry.fill.venue_trade_id
    assert first.entries[1].entry.fill.execution_id == changed.entries[1].entry.fill.execution_id
    assert first.entries[1].entry.fill.order_id == changed.entries[1].entry.fill.order_id
    assert first.canonical_identity == changed.canonical_identity


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("position", "position observation"),
        ("account", "account observation"),
        ("fee_precision", "currency precision"),
    ),
)
def test_projection_rejects_unexplained_position_cash_or_fee_precision(
    mutation: str, message: str
) -> None:
    events = list(_stream())
    if mutation == "position":
        events[-3] = events[-3].model_copy(update={"quantity": Decimal("1")})
        events[-1] = events[-1].model_copy(update={"final_position": Decimal("1")})
    elif mutation == "account":
        events[-2] = events[-2].model_copy(update={"cash_balance": Decimal("999")})
        events[-1] = events[-1].model_copy(update={"final_cash": Decimal("999")})
    else:
        events[4] = events[4].model_copy(update={"fee": Decimal("0.1234567")})
        events[6] = events[6].model_copy(update={"cash_balance": Decimal("899.8765433"), "fees": Decimal("0.1234567")})
        events[-2] = events[-2].model_copy(update={"cash_balance": Decimal("1001.7765433"), "fees": Decimal("0.2234567")})
        events[-1] = events[-1].model_copy(update={"final_cash": Decimal("1001.7765433"), "fees": Decimal("0.2234567")})
    mutated = _redigest(tuple(events))

    with pytest.raises(ValueError, match=message):
        _project(mutated)


def test_duplicate_correction_and_bust_are_fail_closed_at_the_typed_boundary() -> None:
    events = list(_stream())
    events[10] = events[10].model_copy(update={"native_fill_id": "native-fill-a"})
    duplicate = _redigest(tuple(events))
    with pytest.raises(ValueError, match="fill occurred before order submission"):
        _project(duplicate)

    invalid = list(_stream())
    invalid[4] = {"event_type": "Correction", "sequence": 6}
    for label in ("Correction", "Bust"):
        invalid[4] = {"event_type": label, "sequence": 6}
        with pytest.raises(ValueError, match="duplicate/correction/bust"):
            _project(tuple(invalid))


def test_projection_rejects_wrong_catalog_currency_and_order_fill_linkage() -> None:
    from packages.engine_portfolio_projection import ProjectionAuthority, project_portfolio

    authority = ProjectionAuthority(
        request_message_id=REQUEST_A,
        instrument=_instrument(settlement=Currency.USD),
        opening=_opening(),
        strategy_id="strategy-1",
        liquidity_side=LiquiditySide.TAKER,
        reconciliation_source=ReconciliationSource.VENUE,
    )
    with pytest.raises(ValueError, match="settlement currency"):
        project_portfolio(_stream(), authority)

    events = list(_stream())
    events[4] = events[4].model_copy(update={"client_order_id": "unknown-order"})
    mutated = _redigest(tuple(events))
    with pytest.raises(ValueError, match="fill occurred before order submission"):
        _project(mutated)
