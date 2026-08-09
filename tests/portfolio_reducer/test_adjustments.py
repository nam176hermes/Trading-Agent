from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    AssetClass,
    Currency,
    CurrencyConversion,
    EventEnvelope,
    ExposureSnapshot,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderQuantity,
    OrderSide,
    PortfolioConversionEntry,
    PortfolioFillEntry,
    PortfolioFundingEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PortfolioReconciliationEntry,
    PortfolioReconciliationSource,
    PortfolioValuationRateEntry,
    PositionMark,
    Price,
    ProductType,
    Quantity,
    ReconciliationSource,
)
from packages.portfolio_reducer import (
    PortfolioReplayError,
    apply_portfolio_event,
    derive_account_snapshot,
    reduce_portfolio_events,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
STREAM = UUID(int=100)
BTC_USD = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")
ETH_USDT = InstrumentId("ETH-USDT", ProductType.CRYPTO_SPOT, "ALPACA")


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(amount: str, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(amount), currency)


def definition(instrument: InstrumentId, settlement: Currency) -> InstrumentDefinition:
    base = Currency.BTC if instrument == BTC_USD else Currency.ETH
    return InstrumentDefinition(
        instrument_id=instrument,
        raw_symbol=instrument.symbol,
        asset_class=AssetClass.CRYPTO,
        base_currency=base,
        quote_currency=settlement,
        settlement_currency=settlement,
        tick_size=Price(Decimal("0.01"), settlement),
        size_increment=OrderQuantity(Decimal("0.01"), 2),
        minimum_quantity=OrderQuantity(Decimal("0.01"), 2),
        maximum_quantity=OrderQuantity(Decimal("100"), 2),
        minimum_notional=money("1", settlement),
        maximum_notional=money("100000", settlement),
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


def envelope(payload: object, *, number: int, effective_at: datetime = NOW) -> EventEnvelope[object]:
    return EventEnvelope[object](
        event_id=uid(number),
        event_type=type(payload).__name__,
        schema_version="1.0",
        source="portfolio-test",
        stream_id=STREAM,
        sequence=number,
        observed_at=effective_at,
        ingested_at=effective_at + timedelta(seconds=1),
        produced_at=effective_at + timedelta(seconds=2),
        effective_at=effective_at + timedelta(seconds=2),
        expires_at=effective_at + timedelta(minutes=5),
        correlation_id=uid(500 + number),
        causation_id=uid(600 + number),
        trace_id=uid(700 + number),
        payload=payload,
    )


def opening(*, currencies: tuple[tuple[Currency, str], ...] = ((Currency.USD, "1000"),)) -> EventEnvelope[object]:
    return envelope(
        PortfolioOpeningEntry(
            account_id="account-1",
            reporting_currency=Currency.USD,
            balances=tuple(balance(currency, cash) for currency, cash in currencies),
            source_id="opening-source",
            source_revision="r1",
            effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ),
        number=1,
    )


def fill(
    *,
    number: int,
    execution: int,
    instrument: InstrumentId = BTC_USD,
    settlement: Currency = Currency.USD,
    strategy: str = "strategy-1",
    quantity: str = "1",
    price: str = "100",
    side: OrderSide = OrderSide.BUY,
    status: FillReportStatus = FillReportStatus.FILLED,
    correction_of: UUID | None = None,
    bust_of: UUID | None = None,
) -> EventEnvelope[object]:
    event = FillEvent(
        execution_id=uid(execution),
        order_id=uid(1_000 + execution),
        report_sequence=1,
        venue_trade_id=f"trade-{execution}",
        instrument_definition=definition(instrument, settlement),
        side=side,
        liquidity_side=LiquiditySide.MAKER,
        status=status,
        quantity=OrderQuantity(Decimal(quantity), 2),
        cumulative_fill_quantity=OrderQuantity(Decimal(quantity), 2),
        leaves_quantity=OrderQuantity(Decimal("0"), 2),
        order_quantity=OrderQuantity(Decimal(quantity), 2),
        last_fill_price=Price(Decimal(price), settlement),
        average_fill_price=Price(Decimal(price), settlement),
        commission=money("0", settlement),
        reconciliation_source=ReconciliationSource.VENUE,
        duplicate_of_execution_id=None,
        correction_of_execution_id=correction_of,
        bust_of_execution_id=bust_of,
        filled_at=NOW,
        schema_version="2.0",
    )
    return envelope(
        PortfolioFillEntry(
            account_id="account-1",
            strategy_id=strategy,
            fill=event,
            effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ),
        number=number,
    )


def mark(*, number: int, price: str, marked_at: datetime = NOW) -> EventEnvelope[object]:
    return envelope(
        PortfolioMarkEntry(
            account_id="account-1",
            instrument=BTC_USD,
            mark=PositionMark(
                price=Price(Decimal(price), Currency.USD),
                marked_at=marked_at,
                provenance_id="marks-v1",
            ),
            marked_at=marked_at,
            effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ),
        number=number,
    )


def test_mark_updates_every_matching_strategy_and_rejects_an_older_mark() -> None:
    state = reduce_portfolio_events(
        (
            opening(),
            fill(number=2, execution=20, strategy="strategy-a"),
            fill(number=3, execution=30, strategy="strategy-b"),
        )
    )

    state = apply_portfolio_event(state, mark(number=4, price="105"))
    assert [position.unrealized_pnl.amount for position in state.snapshot.positions] == [
        Decimal("5"),
        Decimal("5"),
    ]
    older = mark(number=5, price="99", marked_at=NOW - timedelta(seconds=1))
    with pytest.raises(PortfolioReplayError, match="older than retained mark"):
        apply_portfolio_event(state, older)
    assert [position.mark.price.amount for position in state.snapshot.positions] == [
        Decimal("105"),
        Decimal("105"),
    ]


def test_mark_rejects_a_settlement_currency_mismatch() -> None:
    state = reduce_portfolio_events((opening(), fill(number=2, execution=20)))
    invalid = envelope(
        PortfolioMarkEntry(
            account_id="account-1",
            instrument=BTC_USD,
            mark=PositionMark(
                price=Price(Decimal("105"), Currency.USDT),
                marked_at=NOW,
                provenance_id="marks-v1",
            ),
            marked_at=NOW,
            effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ),
        number=3,
    )
    with pytest.raises(PortfolioReplayError, match="mark currency"):
        apply_portfolio_event(state, invalid)


def test_mark_rejects_a_time_after_its_event_effective_time() -> None:
    state = reduce_portfolio_events((opening(), fill(number=2, execution=20)))
    future_mark = mark(number=3, price="105", marked_at=NOW + timedelta(seconds=1))

    with pytest.raises(PortfolioReplayError, match="mark time"):
        apply_portfolio_event(state, future_mark)

    assert state.snapshot.positions[0].mark is None


def test_post_mark_fill_retains_mark_and_revalues_position_and_balance() -> None:
    state = reduce_portfolio_events((opening(), fill(number=2, execution=20)))
    state = apply_portfolio_event(state, mark(number=3, price="101"))

    state = apply_portfolio_event(
        state,
        fill(number=4, execution=40, price="110"),
    )

    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal("2.00")
    assert position.average_entry_price is not None
    assert position.average_entry_price.amount == Decimal("105")
    assert position.mark is not None
    assert position.mark.price.amount == Decimal("101")
    assert position.unrealized_pnl.amount == Decimal("-8")
    assert state.snapshot.balances[0].unrealized_pnl.amount == Decimal("-8")


def test_post_mark_close_clears_mark_and_unrealized_pnl_everywhere() -> None:
    state = reduce_portfolio_events((opening(), fill(number=2, execution=20)))
    state = apply_portfolio_event(state, mark(number=3, price="101"))

    state = apply_portfolio_event(
        state,
        fill(
            number=4,
            execution=40,
            side=OrderSide.SELL,
            price="110",
        ),
    )

    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal("0.00")
    assert position.average_entry_price is None
    assert position.mark is None
    assert position.unrealized_pnl.amount == Decimal("0")
    assert position.realized_pnl.amount == Decimal("10")
    assert state.snapshot.balances[0].unrealized_pnl.amount == Decimal("0")


@pytest.mark.parametrize(
    ("status", "reference_name", "expected_quantity", "expected_average", "expected_unrealized"),
    [
        (FillReportStatus.CORRECTION, "correction_of", "1.00", "90", "11"),
        (FillReportStatus.BUST, "bust_of", "0.00", None, "0"),
    ],
)
def test_post_mark_correction_or_bust_revalues_retained_mark(
    status: FillReportStatus,
    reference_name: str,
    expected_quantity: str,
    expected_average: str | None,
    expected_unrealized: str,
) -> None:
    normal = fill(number=2, execution=20)
    state = reduce_portfolio_events((opening(), normal))
    state = apply_portfolio_event(state, mark(number=3, price="101"))
    adjustment = fill(
        number=4,
        execution=40,
        price="90" if status is FillReportStatus.CORRECTION else "100",
        status=status,
        **{reference_name: normal.payload.fill.execution_id},
    )

    state = apply_portfolio_event(state, adjustment)

    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal(expected_quantity)
    assert (
        position.average_entry_price.amount
        if position.average_entry_price is not None
        else None
    ) == (Decimal(expected_average) if expected_average is not None else None)
    assert (position.mark.price.amount if position.mark is not None else None) == (
        Decimal("101") if expected_average is not None else None
    )
    assert position.unrealized_pnl.amount == Decimal(expected_unrealized)
    assert state.snapshot.balances[0].unrealized_pnl.amount == Decimal(
        expected_unrealized
    )


def test_funding_mutates_only_declared_balance_or_matching_position() -> None:
    state = reduce_portfolio_events((opening(currencies=((Currency.USD, "1000"), (Currency.USDT, "0"))), fill(number=2, execution=20)))
    account_funding = envelope(
        PortfolioFundingEntry(
            account_id="account-1", funding_id=uid(30), strategy_id=None, instrument=None,
            amount=money("7", Currency.USDT), provenance_id="funding-v1", effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ), number=3,
    )
    state = apply_portfolio_event(state, account_funding)
    assert [item.cash.amount for item in state.snapshot.balances] == [Decimal("900"), Decimal("7")]
    assert [item.funding.amount for item in state.snapshot.balances] == [Decimal("0"), Decimal("7")]
    positional = envelope(
        PortfolioFundingEntry(
            account_id="account-1", funding_id=uid(40), strategy_id="strategy-1", instrument=BTC_USD,
            amount=money("2"), provenance_id="funding-v1", effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ), number=4,
    )
    state = apply_portfolio_event(state, positional)
    assert state.snapshot.positions[0].funding.amount == Decimal("2")
    assert state.snapshot.balances[0].cash.amount == Decimal("902")
    assert state.snapshot.balances[0].funding.amount == Decimal("2")
    unknown = positional.model_copy(update={"event_id": uid(5), "sequence": 5, "payload": positional.payload.model_copy(update={"funding_id": uid(50), "strategy_id": "missing"})})
    with pytest.raises(PortfolioReplayError, match="funding position"):
        apply_portfolio_event(state, unknown)
    wrong_currency = positional.model_copy(update={"event_id": uid(6), "sequence": 6, "payload": positional.payload.model_copy(update={"funding_id": uid(60), "amount": money("2", Currency.USDT)})})
    with pytest.raises(PortfolioReplayError, match="funding currency"):
        apply_portfolio_event(state, wrong_currency)


def test_conversion_uses_supplied_amounts_and_rejects_insufficient_source_cash() -> None:
    state = reduce_portfolio_events((opening(currencies=((Currency.USD, "100"), (Currency.USDT, "0"))),))
    conversion = envelope(
        PortfolioConversionEntry(
            account_id="account-1",
            conversion=CurrencyConversion(money("10"), Currency.USDT, Decimal("2"), money("20", Currency.USDT)),
            provenance_id="conversion-v1", effective_at=NOW, schema_version="portfolio-entry-v1",
        ), number=2,
    )
    state = apply_portfolio_event(state, conversion)
    assert [item.cash.amount for item in state.snapshot.balances] == [Decimal("90"), Decimal("20")]
    insufficient = conversion.model_copy(update={"event_id": uid(3), "sequence": 3, "payload": conversion.payload.model_copy(update={"conversion": CurrencyConversion(money("101"), Currency.USDT, Decimal("2"), money("202", Currency.USDT))})})
    with pytest.raises(PortfolioReplayError, match="insufficient"):
        apply_portfolio_event(state, insufficient)


def test_valuation_rate_keeps_latest_quote_and_derives_deterministic_partitions() -> None:
    state = reduce_portfolio_events(
        (
            opening(currencies=((Currency.USD, "1000"), (Currency.USDT, "0"))),
            fill(number=2, execution=20, instrument=ETH_USDT, settlement=Currency.USDT, strategy="strategy-b", price="10"),
            fill(number=3, execution=30, strategy="strategy-a", price="100"),
        )
    )
    state = apply_portfolio_event(state, mark(number=4, price="101"))
    eur_mark = envelope(
        PortfolioMarkEntry(
            account_id="account-1", instrument=ETH_USDT,
            mark=PositionMark(price=Price(Decimal("10"), Currency.USDT), marked_at=NOW, provenance_id="marks-v1"),
            marked_at=NOW, effective_at=NOW, schema_version="portfolio-entry-v1",
        ), number=5,
    )
    state = apply_portfolio_event(state, eur_mark)
    with pytest.raises(PortfolioReplayError, match="valuation rate"):
        derive_account_snapshot(state, observed_at=NOW)
    latest = envelope(
        PortfolioValuationRateEntry(
            account_id="account-1", source_currency=Currency.USDT, target_currency=Currency.USD,
            rate=Decimal("2"), quoted_at=NOW, provenance_id="fx-new", effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ), number=6,
    )
    state = apply_portfolio_event(state, latest)
    older = latest.model_copy(update={"event_id": uid(7), "sequence": 7, "payload": latest.payload.model_copy(update={"rate": Decimal("3"), "quoted_at": NOW - timedelta(seconds=1), "provenance_id": "fx-old"})})
    state = apply_portfolio_event(state, older)
    assert state.valuation_rates[0].rate == Decimal("2")
    assert state.valuation_rates[0].provenance_id == "fx-new"
    snapshot = derive_account_snapshot(state, observed_at=NOW + timedelta(seconds=3))
    assert snapshot.total_exposure.gross.amount == Decimal("121")
    assert snapshot.total_exposure.net.amount == Decimal("121")
    assert [item.exposure.gross.amount for item in snapshot.instrument_exposures] == [Decimal("101"), Decimal("20")]
    assert [item.exposure.gross.amount for item in snapshot.strategy_exposures] == [Decimal("101"), Decimal("20")]
    assert [item.exposure.gross.amount for item in snapshot.venue_exposures] == [Decimal("121")]
    assert [item.exposure.pending.amount for item in snapshot.instrument_exposures] == [Decimal("0"), Decimal("0")]
    assert snapshot.total_exposure == ExposureSnapshot(currency=Currency.USD, gross=money("121"), net=money("121"), pending=money("0"))
    assert derive_account_snapshot(state, observed_at=NOW + timedelta(seconds=3)) == snapshot
    future = latest.model_copy(update={"event_id": uid(8), "sequence": 8, "payload": latest.payload.model_copy(update={"rate": Decimal("4"), "quoted_at": NOW + timedelta(seconds=10), "provenance_id": "fx-future"})})
    state = apply_portfolio_event(state, future)
    with pytest.raises(PortfolioReplayError, match="valuation rate"):
        derive_account_snapshot(state, observed_at=NOW + timedelta(seconds=3))


def test_exposure_gross_preserves_offsetting_strategy_positions() -> None:
    state = reduce_portfolio_events(
        (
            opening(),
            fill(number=2, execution=20, strategy="strategy-long", side=OrderSide.BUY),
            fill(number=3, execution=30, strategy="strategy-short", side=OrderSide.SELL),
        )
    )
    state = apply_portfolio_event(state, mark(number=4, price="105"))

    snapshot = derive_account_snapshot(state, observed_at=NOW + timedelta(seconds=3))
    assert snapshot.total_exposure.gross.amount == Decimal("210")
    assert snapshot.total_exposure.net.amount == Decimal("0")
    assert snapshot.instrument_exposures[0].exposure.gross.amount == Decimal("210")
    assert snapshot.instrument_exposures[0].exposure.net.amount == Decimal("0")


def test_conversion_never_supplies_a_valuation_rate() -> None:
    state = reduce_portfolio_events(
        (
            opening(currencies=((Currency.USD, "1000"), (Currency.USDT, "0"))),
            fill(number=2, execution=20, instrument=ETH_USDT, settlement=Currency.USDT),
        )
    )
    state = apply_portfolio_event(
        state,
        envelope(
            PortfolioMarkEntry(
                account_id="account-1", instrument=ETH_USDT,
                mark=PositionMark(price=Price(Decimal("10"), Currency.USDT), marked_at=NOW, provenance_id="marks-v1"),
                marked_at=NOW, effective_at=NOW, schema_version="portfolio-entry-v1",
            ), number=3,
        ),
    )
    state = apply_portfolio_event(
        state,
        envelope(
            PortfolioConversionEntry(
                account_id="account-1",
                conversion=CurrencyConversion(money("10"), Currency.USDT, Decimal("1"), money("10", Currency.USDT)),
                provenance_id="conversion-v1", effective_at=NOW, schema_version="portfolio-entry-v1",
            ), number=4,
        ),
    )
    with pytest.raises(PortfolioReplayError, match="valuation rate"):
        derive_account_snapshot(state, observed_at=NOW + timedelta(seconds=3))


def test_reconciliation_replaces_account_state_and_invalidates_old_execution() -> None:
    normal = fill(number=2, execution=20)
    reconciled = AccountPortfolioSnapshot(
        snapshot_id=uid(90), account_id="account-1", reporting_currency=Currency.USD,
        balances=(balance(Currency.USD, "777"),), positions=(),
        total_exposure=ExposureSnapshot(currency=Currency.USD, gross=money("0"), net=money("0"), pending=money("0")),
        instrument_exposures=(), strategy_exposures=(), venue_exposures=(), observed_at=NOW,
        schema_version="portfolio-snapshot-v1",
    )
    reconciliation = envelope(
        PortfolioReconciliationEntry(
            account_id="account-1", reconciliation_id=uid(91), source=PortfolioReconciliationSource.VENUE,
            source_revision="revision-7", snapshot=reconciled, effective_at=NOW + timedelta(seconds=10),
            schema_version="portfolio-entry-v1",
        ), number=3, effective_at=NOW + timedelta(seconds=10),
    )
    state = reduce_portfolio_events((opening(), normal, reconciliation))
    assert state.snapshot.balances[0].cash.amount == Decimal("777")
    assert state.snapshot.observed_at == NOW
    assert state.reconciliation == reconciliation.payload
    assert state.reconciliation.source_revision == "revision-7"
    assert state.active_execution_ids == ()
    bust = fill(number=4, execution=40, status=FillReportStatus.BUST, bust_of=normal.payload.fill.execution_id)
    with pytest.raises(PortfolioReplayError, match="active normal execution"):
        apply_portfolio_event(state, bust)


def test_reconciliation_preserves_a_zero_position_without_prior_execution() -> None:
    zero_position = AccountPositionSnapshot(
        account_id="account-1", strategy_id="strategy-zero", instrument=BTC_USD,
        settlement_currency=Currency.USD, quantity=Quantity(Decimal("0"), 2), mark=None,
        average_entry_price=None, realized_pnl=money("0"), unrealized_pnl=money("0"),
        fees=money("0"), funding=money("0"), observed_at=NOW, schema_version="position-v1",
    )
    reconciled = AccountPortfolioSnapshot(
        snapshot_id=uid(100), account_id="account-1", reporting_currency=Currency.USD,
        balances=(balance(Currency.USD, "777"),), positions=(zero_position,),
        total_exposure=ExposureSnapshot(currency=Currency.USD, gross=money("0"), net=money("0"), pending=money("0")),
        instrument_exposures=(), strategy_exposures=(), venue_exposures=(), observed_at=NOW,
        schema_version="portfolio-snapshot-v1",
    )
    reconciliation = envelope(
        PortfolioReconciliationEntry(
            account_id="account-1", reconciliation_id=uid(101), source=PortfolioReconciliationSource.VENUE,
            source_revision="revision-zero", snapshot=reconciled, effective_at=NOW + timedelta(seconds=10),
            schema_version="portfolio-entry-v1",
        ), number=2, effective_at=NOW + timedelta(seconds=10),
    )

    state = reduce_portfolio_events((opening(), reconciliation))
    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal("0.00")
    assert position.mark is None
    assert position.average_entry_price is None


def test_derived_snapshot_retains_reconciled_flat_position_accounting() -> None:
    zero_position = AccountPositionSnapshot(
        account_id="account-1",
        strategy_id="strategy-flat",
        instrument=BTC_USD,
        settlement_currency=Currency.USD,
        quantity=Quantity(Decimal("0"), 2),
        mark=None,
        average_entry_price=None,
        realized_pnl=money("12"),
        unrealized_pnl=money("0"),
        fees=money("3"),
        funding=money("-2"),
        observed_at=NOW,
        schema_version="position-v1",
    )
    reconciled = AccountPortfolioSnapshot(
        snapshot_id=uid(110),
        account_id="account-1",
        reporting_currency=Currency.USD,
        balances=(balance(Currency.USD, "777"),),
        positions=(zero_position,),
        total_exposure=ExposureSnapshot(
            currency=Currency.USD,
            gross=money("0"),
            net=money("0"),
            pending=money("0"),
        ),
        instrument_exposures=(),
        strategy_exposures=(),
        venue_exposures=(),
        observed_at=NOW,
        schema_version="portfolio-snapshot-v1",
    )
    reconciliation = envelope(
        PortfolioReconciliationEntry(
            account_id="account-1",
            reconciliation_id=uid(111),
            source=PortfolioReconciliationSource.VENUE,
            source_revision="revision-flat",
            snapshot=reconciled,
            effective_at=NOW + timedelta(seconds=10),
            schema_version="portfolio-entry-v1",
        ),
        number=2,
        effective_at=NOW + timedelta(seconds=10),
    )
    state = reduce_portfolio_events((opening(), reconciliation))

    canonical = derive_account_snapshot(state, observed_at=NOW)

    assert canonical.positions == (zero_position,)
    assert canonical.total_exposure.gross.amount == Decimal("0")
    assert canonical.instrument_exposures == ()
