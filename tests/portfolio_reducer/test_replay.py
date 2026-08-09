from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import socket
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
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PositionMark,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.portfolio_reducer import (
    PortfolioReplayError,
    replay_portfolio,
    snapshot_from_portfolio_result,
)
from packages.event_ledger import InMemoryEventLedger


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
STREAM = UUID(int=100)
OTHER_STREAM = UUID(int=101)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(amount: str) -> Money:
    return Money(Decimal(amount), Currency.USD)


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


def balance() -> AccountBalanceSnapshot:
    return AccountBalanceSnapshot(
        account_id="account-1",
        currency=Currency.USD,
        cash=money("1000"),
        locked_funds=money("0"),
        margin_used=money("0"),
        realized_pnl=money("0"),
        unrealized_pnl=money("0"),
        fees=money("0"),
        funding=money("0"),
        observed_at=NOW,
        schema_version="balance-v1",
    )


def envelope(payload: object, *, number: int, stream: UUID = STREAM) -> EventEnvelope[object]:
    at = NOW + timedelta(minutes=number)
    return EventEnvelope[object](
        event_id=uid(number),
        event_type=type(payload).__name__,
        schema_version="1.0",
        source="portfolio-test",
        stream_id=stream,
        sequence=number,
        observed_at=at,
        ingested_at=at + timedelta(seconds=1),
        produced_at=at + timedelta(seconds=2),
        effective_at=at + timedelta(seconds=2),
        expires_at=at + timedelta(minutes=5),
        correlation_id=uid(500 + number),
        causation_id=uid(600 + number),
        trace_id=uid(700 + number),
        payload=payload,
    )


def opening(*, number: int = 1, account: str = "account-1", stream: UUID = STREAM) -> EventEnvelope[object]:
    return envelope(
        PortfolioOpeningEntry(
            account_id=account,
            reporting_currency=Currency.USD,
            balances=(balance().model_copy(update={"account_id": account}),),
            source_id="opening-source",
            source_revision="r1",
            effective_at=NOW + timedelta(minutes=number),
            schema_version="portfolio-entry-v1",
        ),
        number=number,
        stream=stream,
    )


def fill(*, number: int, account: str = "account-1", stream: UUID = STREAM) -> EventEnvelope[object]:
    at = NOW + timedelta(minutes=number)
    return envelope(
        PortfolioFillEntry(
            account_id=account,
            strategy_id="strategy-1",
            fill=FillEvent(
                execution_id=uid(1_000 + number),
                order_id=uid(2_000 + number),
                report_sequence=1,
                venue_trade_id=f"trade-{number}",
                instrument_definition=definition(),
                side=OrderSide.BUY,
                liquidity_side=LiquiditySide.MAKER,
                status=FillReportStatus.FILLED,
                quantity=OrderQuantity(Decimal("1"), 2),
                cumulative_fill_quantity=OrderQuantity(Decimal("1"), 2),
                leaves_quantity=OrderQuantity(Decimal("0"), 2),
                order_quantity=OrderQuantity(Decimal("1"), 2),
                last_fill_price=Price(Decimal("100"), Currency.USD),
                average_fill_price=Price(Decimal("100"), Currency.USD),
                commission=money("0"),
                reconciliation_source=ReconciliationSource.VENUE,
                duplicate_of_execution_id=None,
                correction_of_execution_id=None,
                bust_of_execution_id=None,
                filled_at=at,
                schema_version="2.0",
            ),
            effective_at=at,
            schema_version="portfolio-entry-v1",
        ),
        number=number,
        stream=stream,
    )


def mark(*, number: int, stream: UUID = STREAM) -> EventEnvelope[object]:
    at = NOW + timedelta(minutes=number)
    return envelope(
        PortfolioMarkEntry(
            account_id="account-1",
            instrument=INSTRUMENT,
            mark=PositionMark(
                price=Price(Decimal("101"), Currency.USD),
                marked_at=at,
                provenance_id="marks-v1",
            ),
            marked_at=at,
            effective_at=at,
            schema_version="portfolio-entry-v1",
        ),
        number=number,
        stream=stream,
    )


@pytest.fixture
def portfolio_events() -> tuple[EventEnvelope[object], ...]:
    return (opening(), fill(number=2), mark(number=3), fill(number=4), mark(number=5))


def test_snapshot_tail_is_identical_to_full_replay(portfolio_events) -> None:
    full = replay_portfolio(portfolio_events)
    record = snapshot_from_portfolio_result(replay_portfolio(portfolio_events[:3]))

    tail = replay_portfolio(portfolio_events[3:], snapshot=record)

    assert tail == full
    assert tail.canonical_state_json == full.canonical_state_json
    assert tail.state_hash == full.state_hash


def test_tampered_record_hash_fails_closed(portfolio_events) -> None:
    record = snapshot_from_portfolio_result(replay_portfolio(portfolio_events[:3]))

    with pytest.raises(PortfolioReplayError, match="state hash"):
        replay_portfolio(
            portfolio_events[3:], snapshot=record.model_copy(update={"state_hash": "0" * 64})
        )


@pytest.mark.parametrize(
    "events, pattern",
    [
        ((fill(number=1),), "opening"),
        ((opening(), opening(number=2)), "opening"),
        ((opening(), fill(number=3)), "sequence"),
        ((opening(), fill(number=2), fill(number=2)), "duplicate"),
        ((opening(), fill(number=2, account="account-2")), "account"),
        ((opening(), fill(number=2, stream=OTHER_STREAM)), "stream"),
    ],
)
def test_replay_rejects_invalid_full_history(events, pattern: str) -> None:
    with pytest.raises(PortfolioReplayError, match=pattern):
        replay_portfolio(events)


def test_snapshot_rejects_tampered_applied_digest_and_effect_index(portfolio_events) -> None:
    record = snapshot_from_portfolio_result(replay_portfolio(portfolio_events[:3]))
    applied = record.state.applied_events[0].model_copy(update={"digest": "0" * 64})
    bad_applied = record.model_copy(
        update={"state": record.state.model_copy(update={"applied_events": (applied, *record.state.applied_events[1:])})}
    )
    with pytest.raises(PortfolioReplayError, match="state hash"):
        replay_portfolio((), snapshot=bad_applied)

    effect = record.state.active_effects[0].model_copy(update={"logical_sequence": 1})
    bad_effect = record.model_copy(
        update={"state": record.state.model_copy(update={"active_effects": (effect,)})}
    )
    with pytest.raises(PortfolioReplayError, match="state hash"):
        replay_portfolio((), snapshot=bad_effect)


def test_snapshot_tail_rejects_old_and_conflicting_events(portfolio_events) -> None:
    record = snapshot_from_portfolio_result(replay_portfolio(portfolio_events[:3]))
    old = portfolio_events[2]
    conflict = old.model_copy(update={"source": "another-source"})

    with pytest.raises(PortfolioReplayError, match="cursor"):
        replay_portfolio((old,), snapshot=record)
    with pytest.raises(PortfolioReplayError, match="conflicting"):
        replay_portfolio((conflict,), snapshot=record)


def test_replay_rejects_caller_reordering(portfolio_events) -> None:
    with pytest.raises(PortfolioReplayError, match="ordered"):
        replay_portfolio((portfolio_events[1], portfolio_events[0], *portfolio_events[2:]))


def test_replay_does_not_call_persistence_or_a_provider(portfolio_events, monkeypatch) -> None:
    def forbidden(*_args, **_kwargs) -> None:
        raise AssertionError("pure portfolio replay must not perform external work")

    monkeypatch.setattr(InMemoryEventLedger, "append", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    assert replay_portfolio(portfolio_events).state.cursor[0].sequence == 5
