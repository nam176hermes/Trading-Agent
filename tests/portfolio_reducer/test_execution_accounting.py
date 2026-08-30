from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from packages.domain import (
    AccountBalanceSnapshot,
    AssetClass,
    Currency,
    EventEnvelope,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderQuantity,
    OrderSide,
    PortfolioFillEntry,
    PortfolioOpeningEntry,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.portfolio_reducer import (
    PortfolioReplayError,
    apply_portfolio_event,
    reduce_portfolio_events,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")
STREAM = UUID(int=100)


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(amount: str, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(amount), currency)


def definition() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=INSTRUMENT,
        raw_symbol="BTCUSD",
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.01"), 2),
        minimum_quantity=OrderQuantity(Decimal("0.01"), 2),
        maximum_quantity=OrderQuantity(Decimal("100"), 2),
        minimum_notional=money("1"),
        maximum_notional=money("100000"),
        multiplier=Decimal("1"),
        margin=None,
        session_calendar="24X7",
        provenance=InstrumentProvenance(
            source_id="catalog", source_revision="r1", observed_at=NOW
        ),
    )


def balance(currency: Currency, cash: str) -> AccountBalanceSnapshot:
    return AccountBalanceSnapshot(
        account_id="account-1",
        currency=currency,
        cash=money(cash, currency),
        locked_funds=money("0", currency),
        margin_used=money("0", currency),
        realized_pnl=money("0", currency),
        unrealized_pnl=money("0", currency),
        fees=money("0", currency),
        funding=money("0", currency),
        observed_at=NOW,
        schema_version="balance-v1",
    )


def envelope(
    payload: object,
    *,
    event_number: int,
    effective_at: datetime = NOW,
) -> EventEnvelope[object]:
    return EventEnvelope[object](
        event_id=uid(event_number),
        event_type=type(payload).__name__,
        schema_version="1.0",
        source="portfolio-test",
        stream_id=STREAM,
        sequence=event_number,
        observed_at=effective_at,
        ingested_at=effective_at + timedelta(seconds=1),
        produced_at=effective_at + timedelta(seconds=2),
        effective_at=effective_at + timedelta(seconds=2),
        expires_at=effective_at + timedelta(minutes=5),
        correlation_id=uid(500 + event_number),
        causation_id=uid(600 + event_number),
        trace_id=uid(700 + event_number),
        payload=payload,
    )


def opening() -> EventEnvelope[object]:
    return envelope(
        PortfolioOpeningEntry(
            account_id="account-1",
            reporting_currency=Currency.USD,
            balances=(balance(Currency.USD, "1000"), balance(Currency.USDT, "0")),
            source_id="opening-source",
            source_revision="r1",
            effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ),
        event_number=1,
    )


def fill_event(
    *,
    event_number: int,
    execution_number: int,
    side: OrderSide,
    quantity: str,
    price: str,
    status: FillReportStatus = FillReportStatus.FILLED,
    commission: Money | None = None,
    strategy_id: str = "strategy-1",
    account_id: str = "account-1",
    duplicate_of: UUID | None = None,
    correction_of: UUID | None = None,
    bust_of: UUID | None = None,
    effective_at: datetime = NOW,
) -> EventEnvelope[object]:
    fill = FillEvent(
        execution_id=uid(execution_number),
        order_id=uid(1_000 + execution_number),
        report_sequence=1,
        venue_trade_id=f"trade-{execution_number}",
        instrument_definition=definition(),
        side=side,
        liquidity_side=LiquiditySide.MAKER,
        status=status,
        quantity=OrderQuantity(Decimal(quantity), 2),
        cumulative_fill_quantity=OrderQuantity(Decimal(quantity), 2),
        leaves_quantity=OrderQuantity(Decimal("0"), 2),
        order_quantity=OrderQuantity(Decimal(quantity), 2),
        last_fill_price=Price(Decimal(price), Currency.USD),
        average_fill_price=Price(Decimal(price), Currency.USD),
        commission=commission or money("0"),
        reconciliation_source=ReconciliationSource.VENUE,
        duplicate_of_execution_id=duplicate_of,
        correction_of_execution_id=correction_of,
        bust_of_execution_id=bust_of,
        filled_at=effective_at,
        schema_version="2.0",
    )
    return envelope(
        PortfolioFillEntry(
            account_id=account_id,
            strategy_id=strategy_id,
            fill=fill,
            effective_at=effective_at,
            schema_version="portfolio-entry-v1",
        ),
        event_number=event_number,
        effective_at=effective_at,
    )


@pytest.fixture
def opened_state():
    return reduce_portfolio_events((opening(),))


def test_long_partial_close_keeps_basis_and_realizes_exact_pnl(opened_state) -> None:
    state = apply_portfolio_event(
        opened_state,
        fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="3", price="100"),
    )
    state = apply_portfolio_event(
        state,
        fill_event(event_number=3, execution_number=30, side=OrderSide.SELL, quantity="1", price="110"),
    )
    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal("2.00")
    assert position.average_entry_price.amount == Decimal("100")
    assert position.realized_pnl.amount == Decimal("10")
    assert state.snapshot.balances[0].cash.amount == Decimal("810")
    assert state.snapshot.balances[0].realized_pnl.amount == Decimal("10")


def test_short_cash_flow_and_cross_zero_reversal_use_close_first_accounting(opened_state) -> None:
    state = apply_portfolio_event(
        opened_state,
        fill_event(event_number=2, execution_number=20, side=OrderSide.SELL, quantity="2", price="100"),
    )
    state = apply_portfolio_event(
        state,
        fill_event(event_number=3, execution_number=30, side=OrderSide.BUY, quantity="3", price="90"),
    )
    position = state.snapshot.positions[0]
    assert state.snapshot.balances[0].cash.amount == Decimal("930")
    assert position.quantity.value == Decimal("1.00")
    assert position.average_entry_price.amount == Decimal("90")
    assert position.realized_pnl.amount == Decimal("20")
    assert state.snapshot.balances[0].realized_pnl.amount == Decimal("20")


def test_multiple_entries_use_exact_weighted_average_and_settlement_fee(opened_state) -> None:
    state = apply_portfolio_event(
        opened_state,
        fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="1", price="100", commission=money("1")),
    )
    state = apply_portfolio_event(
        state,
        fill_event(event_number=3, execution_number=30, side=OrderSide.BUY, quantity="3", price="110", commission=money("2")),
    )
    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal("4.00")
    assert position.average_entry_price.amount == Decimal("107.5")
    assert position.fees.amount == Decimal("3")
    assert state.snapshot.balances[0].cash.amount == Decimal("567")
    assert state.snapshot.balances[0].fees.amount == Decimal("3")


def test_repeating_weighted_basis_is_rounded_only_at_currency_boundaries(opened_state) -> None:
    events = (
        fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="1", price="100"),
        fill_event(event_number=3, execution_number=30, side=OrderSide.BUY, quantity="2", price="101"),
        fill_event(event_number=4, execution_number=40, side=OrderSide.SELL, quantity="1", price="102"),
        fill_event(event_number=5, execution_number=50, side=OrderSide.SELL, quantity="2", price="102"),
    )

    state = opened_state
    state = apply_portfolio_event(state, events[0])
    state = apply_portfolio_event(state, events[1])
    assert state.snapshot.positions[0].average_entry_price == Price(
        Decimal("100.67"), Currency.USD
    )
    state = apply_portfolio_event(state, events[2])
    assert state.snapshot.positions[0].realized_pnl.amount == Decimal("1.33")
    state = apply_portfolio_event(state, events[3])

    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal("0.00")
    assert position.average_entry_price is None
    assert position.realized_pnl.amount == Decimal("4.00")
    assert state.snapshot.balances[0].cash.amount == Decimal("1004.00")
    assert state.snapshot.balances[0].realized_pnl.amount == Decimal("4.00")


def test_different_fee_currency_debits_only_its_balance(opened_state) -> None:
    state = apply_portfolio_event(
        opened_state,
        fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="1", price="100", commission=money("0.5", Currency.USDT)),
    )
    position = state.snapshot.positions[0]
    assert state.snapshot.balances[0].cash.amount == Decimal("900")
    assert state.snapshot.balances[1].cash.amount == Decimal("-0.5")
    assert state.snapshot.balances[1].fees.amount == Decimal("0.5")
    assert position.fees.amount == Decimal("0")


def test_duplicate_requires_exact_known_economics_without_mutation(opened_state) -> None:
    normal = fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="1", price="100")
    duplicate = fill_event(
        event_number=3,
        execution_number=30,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
        status=FillReportStatus.DUPLICATE,
        duplicate_of=normal.payload.fill.execution_id,
    )
    state = reduce_portfolio_events((opening(), normal, duplicate))
    assert state.snapshot.balances[0].cash.amount == Decimal("900")
    assert state.snapshot.positions[0].quantity.value == Decimal("1.00")
    assert state.active_execution_ids == (normal.payload.fill.execution_id,)

    conflicting = fill_event(
        event_number=4,
        execution_number=40,
        side=OrderSide.BUY,
        quantity="1",
        price="101",
        status=FillReportStatus.DUPLICATE,
        duplicate_of=normal.payload.fill.execution_id,
    )
    with pytest.raises(PortfolioReplayError, match="duplicate economics"):
        apply_portfolio_event(state, conflicting)


def test_applied_events_record_payload_type_and_business_identity_at_ingress() -> None:
    normal = fill_event(
        event_number=2,
        execution_number=20,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
    )

    state = reduce_portfolio_events((opening(), normal))

    applied = {item.event_id: item for item in state.applied_events}
    assert applied[uid(1)].stream_id == STREAM
    assert applied[uid(1)].sequence == 1
    assert applied[uid(1)].event_type == "PortfolioOpeningEntry"
    assert applied[uid(1)].business_identity_id is None
    assert applied[uid(2)].stream_id == STREAM
    assert applied[uid(2)].sequence == 2
    assert applied[uid(2)].event_type == "PortfolioFillEntry"
    assert applied[uid(2)].business_identity_id == uid(20)


def test_correction_reverses_normal_effect_then_applies_replacement() -> None:
    normal = fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="1", price="100")
    correction = fill_event(
        event_number=3,
        execution_number=30,
        side=OrderSide.BUY,
        quantity="1",
        price="90",
        status=FillReportStatus.CORRECTION,
        correction_of=normal.payload.fill.execution_id,
    )
    state = reduce_portfolio_events((opening(), normal, correction))
    assert state.snapshot.balances[0].cash.amount == Decimal("910")
    assert state.snapshot.positions[0].average_entry_price.amount == Decimal("90")
    assert state.active_execution_ids == (correction.payload.fill.execution_id,)


def test_correction_can_replace_a_prior_correction() -> None:
    normal = fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="1", price="100")
    first_correction = fill_event(
        event_number=3,
        execution_number=30,
        side=OrderSide.BUY,
        quantity="1",
        price="90",
        status=FillReportStatus.CORRECTION,
        correction_of=normal.payload.fill.execution_id,
    )
    second_correction = fill_event(
        event_number=4,
        execution_number=40,
        side=OrderSide.BUY,
        quantity="1",
        price="80",
        status=FillReportStatus.CORRECTION,
        correction_of=first_correction.payload.fill.execution_id,
    )
    state = reduce_portfolio_events((opening(), normal, first_correction, second_correction))
    assert state.snapshot.balances[0].cash.amount == Decimal("920")
    assert state.snapshot.positions[0].average_entry_price.amount == Decimal("80")
    assert state.active_execution_ids == (second_correction.payload.fill.execution_id,)


def test_correction_rejects_missing_or_busted_execution() -> None:
    missing = fill_event(
        event_number=2,
        execution_number=20,
        side=OrderSide.BUY,
        quantity="1",
        price="90",
        status=FillReportStatus.CORRECTION,
        correction_of=uid(999),
    )
    with pytest.raises(PortfolioReplayError, match="active normal execution"):
        reduce_portfolio_events((opening(), missing))

    normal = fill_event(event_number=2, execution_number=30, side=OrderSide.BUY, quantity="1", price="100")
    bust = fill_event(
        event_number=3,
        execution_number=40,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
        status=FillReportStatus.BUST,
        bust_of=normal.payload.fill.execution_id,
    )
    correction = fill_event(
        event_number=4,
        execution_number=50,
        side=OrderSide.BUY,
        quantity="1",
        price="90",
        status=FillReportStatus.CORRECTION,
        correction_of=normal.payload.fill.execution_id,
    )
    with pytest.raises(PortfolioReplayError, match="active normal execution"):
        reduce_portfolio_events((opening(), normal, bust, correction))


def test_correction_of_an_older_fill_rebases_later_same_position_effects() -> None:
    older = fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="2", price="100")
    newer = fill_event(event_number=3, execution_number=30, side=OrderSide.SELL, quantity="1", price="110")
    correction = fill_event(
        event_number=4,
        execution_number=40,
        side=OrderSide.BUY,
        quantity="2",
        price="90",
        status=FillReportStatus.CORRECTION,
        correction_of=older.payload.fill.execution_id,
    )

    state = reduce_portfolio_events((opening(), older, newer, correction))

    position = state.snapshot.positions[0]
    assert state.snapshot.balances[0].cash.amount == Decimal("930")
    assert position.quantity.value == Decimal("1.00")
    assert position.average_entry_price.amount == Decimal("90")
    assert position.realized_pnl.amount == Decimal("20")
    assert state.active_execution_ids == (
        newer.payload.fill.execution_id,
        correction.payload.fill.execution_id,
    )


def test_bust_of_an_older_fill_rebases_later_same_position_effects() -> None:
    older = fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="2", price="100")
    newer = fill_event(event_number=3, execution_number=30, side=OrderSide.SELL, quantity="1", price="110")
    bust = fill_event(
        event_number=4,
        execution_number=40,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
        status=FillReportStatus.BUST,
        bust_of=older.payload.fill.execution_id,
    )

    state = reduce_portfolio_events((opening(), older, newer, bust))

    position = state.snapshot.positions[0]
    assert state.snapshot.balances[0].cash.amount == Decimal("1110")
    assert position.quantity.value == Decimal("-1.00")
    assert position.average_entry_price.amount == Decimal("110")
    assert position.realized_pnl.amount == Decimal("0")
    assert state.active_execution_ids == (newer.payload.fill.execution_id,)


@pytest.mark.parametrize("status,reference", [(FillReportStatus.CORRECTION, "correction_of"), (FillReportStatus.BUST, "bust_of")])
def test_rebase_keeps_snapshot_time_at_correction_or_bust_time(status, reference) -> None:
    later_time = NOW + timedelta(minutes=1)
    adjustment_time = NOW + timedelta(minutes=2)
    older = fill_event(
        event_number=2,
        execution_number=20,
        side=OrderSide.BUY,
        quantity="2",
        price="100",
        effective_at=NOW,
    )
    newer = fill_event(
        event_number=3,
        execution_number=30,
        side=OrderSide.SELL,
        quantity="1",
        price="110",
        effective_at=later_time,
    )
    adjustment = fill_event(
        event_number=4,
        execution_number=40,
        side=OrderSide.BUY,
        quantity="2",
        price="90" if status is FillReportStatus.CORRECTION else "100",
        status=status,
        effective_at=adjustment_time,
        **{reference: older.payload.fill.execution_id},
    )

    state = reduce_portfolio_events((opening(), older, newer, adjustment))

    assert state.snapshot.observed_at == adjustment_time
    assert state.snapshot.observed_at > later_time
    assert state.snapshot.balances[0].observed_at == adjustment_time
    position = state.snapshot.positions[0]
    assert position.observed_at == adjustment_time
    if status is FillReportStatus.CORRECTION:
        assert position.average_entry_price.amount == Decimal("90")
        assert position.realized_pnl.amount == Decimal("20")
    else:
        assert position.average_entry_price.amount == Decimal("110")
        assert position.realized_pnl.amount == Decimal("0")


@pytest.mark.parametrize("status,reference", [(FillReportStatus.BUST, "bust_of"), (FillReportStatus.CORRECTION, "correction_of")])
def test_bust_or_correction_rejects_consumed_or_cross_scope_reference(status, reference) -> None:
    normal = fill_event(event_number=2, execution_number=20, side=OrderSide.BUY, quantity="1", price="100")
    first = fill_event(
        event_number=3,
        execution_number=30,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
        status=status,
        **{reference: normal.payload.fill.execution_id},
    )
    state = reduce_portfolio_events((opening(), normal, first))
    repeated = fill_event(
        event_number=4,
        execution_number=40,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
        status=status,
        **{reference: normal.payload.fill.execution_id},
    )
    with pytest.raises(PortfolioReplayError, match="active normal execution"):
        apply_portfolio_event(state, repeated)

    cross_strategy = fill_event(
        event_number=4,
        execution_number=41,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
        status=status,
        strategy_id="strategy-2",
        **{reference: first.payload.fill.execution_id},
    )
    with pytest.raises(PortfolioReplayError, match="active normal execution"):
        apply_portfolio_event(state, cross_strategy)

    cross_account = fill_event(
        event_number=4,
        execution_number=42,
        side=OrderSide.BUY,
        quantity="1",
        price="100",
        status=status,
        account_id="account-2",
        **{reference: first.payload.fill.execution_id},
    )
    with pytest.raises(PortfolioReplayError, match="account"):
        apply_portfolio_event(state, cross_account)
