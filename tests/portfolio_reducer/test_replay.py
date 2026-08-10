from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import socket
from uuid import UUID

import pytest

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AssetClass,
    Currency,
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
    PortfolioFillEntry,
    PortfolioFundingEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PortfolioReconciliationEntry,
    PortfolioReconciliationSource,
    PositionMark,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.portfolio_reducer import (
    PortfolioReplayError,
    derive_account_snapshot,
    replay_portfolio,
    snapshot_authority_from_result,
    snapshot_from_portfolio_result,
)
from packages.event_ledger import InMemoryEventLedger
from packages.event_ledger.replay import _canonical_json, event_digest, serialize_event


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


def fill(
    *,
    number: int,
    account: str = "account-1",
    stream: UUID = STREAM,
    execution: int | None = None,
    side: OrderSide = OrderSide.BUY,
    quantity: str = "1",
    price: str = "100",
    status: FillReportStatus = FillReportStatus.FILLED,
    duplicate_of: UUID | None = None,
    correction_of: UUID | None = None,
    bust_of: UUID | None = None,
) -> EventEnvelope[object]:
    at = NOW + timedelta(minutes=number)
    execution_number = 1_000 + number if execution is None else execution
    return envelope(
        PortfolioFillEntry(
            account_id=account,
            strategy_id="strategy-1",
            fill=FillEvent(
                execution_id=uid(execution_number),
                order_id=uid(2_000 + execution_number),
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
                commission=money("0"),
                reconciliation_source=ReconciliationSource.VENUE,
                duplicate_of_execution_id=duplicate_of,
                correction_of_execution_id=correction_of,
                bust_of_execution_id=bust_of,
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


def foreign_reconciliation() -> PortfolioReconciliationEntry:
    account_id = "account-2"
    foreign_balance = AccountBalanceSnapshot(
        account_id=account_id,
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
    zero_exposure = ExposureSnapshot(
        currency=Currency.USD,
        gross=money("0"),
        net=money("0"),
        pending=money("0"),
    )
    return PortfolioReconciliationEntry(
        account_id=account_id,
        reconciliation_id=uid(9_000),
        source=PortfolioReconciliationSource.VENUE,
        source_revision="r1",
        snapshot=AccountPortfolioSnapshot(
            snapshot_id=uid(9_001),
            account_id=account_id,
            reporting_currency=Currency.USD,
            balances=(foreign_balance,),
            positions=(),
            total_exposure=zero_exposure,
            instrument_exposures=(),
            strategy_exposures=(),
            venue_exposures=(),
            observed_at=NOW,
            schema_version="portfolio-snapshot-v1",
        ),
        effective_at=NOW,
        schema_version="portfolio-entry-v1",
    )


def funding(*, number: int, funding_id: UUID, amount: str = "7") -> EventEnvelope[object]:
    at = NOW + timedelta(minutes=number)
    return envelope(
        PortfolioFundingEntry(
            account_id="account-1",
            funding_id=funding_id,
            strategy_id=None,
            instrument=None,
            amount=money(amount),
            provenance_id="funding-v1",
            effective_at=at,
            schema_version="portfolio-entry-v1",
        ),
        number=number,
    )


def reconciliation(
    *, number: int, reconciliation_id: UUID, cash: str = "777"
) -> EventEnvelope[object]:
    at = NOW + timedelta(minutes=number)
    reconciled_balance = balance().model_copy(update={"cash": money(cash)})
    zero_exposure = ExposureSnapshot(
        currency=Currency.USD,
        gross=money("0"),
        net=money("0"),
        pending=money("0"),
    )
    return envelope(
        PortfolioReconciliationEntry(
            account_id="account-1",
            reconciliation_id=reconciliation_id,
            source=PortfolioReconciliationSource.VENUE,
            source_revision=f"revision-{cash}",
            snapshot=AccountPortfolioSnapshot(
                snapshot_id=uid(8_000 + number),
                account_id="account-1",
                reporting_currency=Currency.USD,
                balances=(reconciled_balance,),
                positions=(),
                total_exposure=zero_exposure,
                instrument_exposures=(),
                strategy_exposures=(),
                venue_exposures=(),
                observed_at=NOW,
                schema_version="portfolio-snapshot-v1",
            ),
            effective_at=at,
            schema_version="portfolio-entry-v1",
        ),
        number=number,
    )


def hash_consistent_record_with_state(record, state):
    canonical_snapshot = derive_account_snapshot(state, state.snapshot.observed_at)
    canonical_state_json = _canonical_json(
        {
            "schema_version": record.schema_version,
            "reducer_version": record.reducer_version,
            "state": state.model_dump(mode="json"),
            "canonical_snapshot": canonical_snapshot.model_dump(mode="json"),
            "cursor": [item.model_dump(mode="json") for item in state.cursor],
            "prefix_history_hash": record.prefix_history_hash,
        }
    )
    return record.model_copy(
        update={
            "state": state,
            "canonical_snapshot": canonical_snapshot,
            "cursor": state.cursor,
            "canonical_state_json": canonical_state_json,
            "state_hash": event_digest(canonical_state_json),
        }
    )


def checkpoint(events):
    result = replay_portfolio(events)
    return (
        snapshot_from_portfolio_result(result),
        snapshot_authority_from_result(result),
    )


@pytest.fixture
def portfolio_events() -> tuple[EventEnvelope[object], ...]:
    return (opening(), fill(number=2), mark(number=3), fill(number=4), mark(number=5))


def test_full_replay_extends_a_rolling_prefix_history_commitment(portfolio_events) -> None:
    expected = (
        "c3c608bfacb605883cbe6c8fdd4b0180c7ea3697f7f7ede1c46ad2525aa00afd",
        "ac33c7a779285daebfb5fe54a69d45504d6ad3b70269e195f0c00619b285a085",
        "91ba3ba65b5674c3b31021199293b1bf0e7c5a3c61a923e2348f1ea9256b4aa7",
    )

    assert tuple(
        replay_portfolio(portfolio_events[:sequence]).prefix_history_hash
        for sequence in (1, 3, 5)
    ) == expected


def test_snapshot_tail_requires_independent_authority(portfolio_events) -> None:
    prefix = replay_portfolio(portfolio_events[:3])
    record = snapshot_from_portfolio_result(prefix)

    with pytest.raises(PortfolioReplayError, match="authority"):
        replay_portfolio(portfolio_events[3:], snapshot=record)


def test_recomputed_forged_record_rejects_original_authority(portfolio_events) -> None:
    prefix = replay_portfolio(portfolio_events[:3])
    record = snapshot_from_portfolio_result(prefix)
    authority = snapshot_authority_from_result(prefix)
    effect = record.state.active_effects[0]
    forged_source = effect.source_event.model_copy(update={"source": "forged-source"})
    forged_digest = event_digest(serialize_event(forged_source))
    forged_effect = effect.model_copy(update={"source_event": forged_source})
    forged_applied = tuple(
        item.model_copy(update={"digest": forged_digest})
        if item.event_id == forged_source.event_id
        else item
        for item in record.state.applied_events
    )
    forged_identities = tuple(
        item.model_copy(update={"event_digest": forged_digest})
        if item.event_id == forged_source.event_id
        else item
        for item in record.state.execution_identities
    )
    forged = hash_consistent_record_with_state(
        record,
        record.state.model_copy(
            update={
                "active_effects": (forged_effect,),
                "applied_events": forged_applied,
                "execution_identities": forged_identities,
            }
        ),
    )

    with pytest.raises(PortfolioReplayError, match="authority"):
        replay_portfolio(portfolio_events[3:], snapshot=forged, authority=authority)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "account-2"),
        ("stream_id", OTHER_STREAM),
        ("cursor_sequence", 4),
        ("snapshot_state_hash", "0" * 64),
        ("prefix_history_hash", "0" * 64),
        ("schema_version", "portfolio-replay-v2"),
        ("reducer_version", "portfolio-reducer-v2"),
    ],
)
def test_snapshot_tail_rejects_mismatched_authority_field(
    field: str, value: object, portfolio_events
) -> None:
    record, authority = checkpoint(portfolio_events[:3])
    mismatched = authority.model_copy(update={field: value})

    with pytest.raises(PortfolioReplayError, match="authority"):
        replay_portfolio(
            portfolio_events[3:], snapshot=record, authority=mismatched
        )


def test_snapshot_tail_rejects_authority_reused_with_another_record(
    portfolio_events,
) -> None:
    _, authority = checkpoint(portfolio_events[:1])
    record, _ = checkpoint(portfolio_events[:3])

    with pytest.raises(PortfolioReplayError, match="authority"):
        replay_portfolio(portfolio_events[3:], snapshot=record, authority=authority)


def test_snapshot_authority_without_snapshot_fails_closed(portfolio_events) -> None:
    authority = snapshot_authority_from_result(replay_portfolio(portfolio_events[:3]))

    with pytest.raises(PortfolioReplayError, match="authority"):
        replay_portfolio(portfolio_events, authority=authority)


def test_snapshot_authority_issuance_revalidates_result(portfolio_events) -> None:
    result = replay_portfolio(portfolio_events[:3])
    forged = result.model_copy(update={"state_hash": "0" * 64})

    with pytest.raises(PortfolioReplayError, match="state hash"):
        snapshot_authority_from_result(forged)


def test_snapshot_tail_is_identical_to_full_replay(portfolio_events) -> None:
    full = replay_portfolio(portfolio_events)
    record, authority = checkpoint(portfolio_events[:3])

    tail = replay_portfolio(
        portfolio_events[3:], snapshot=record, authority=authority
    )

    assert tail == full
    assert tail.canonical_state_json == full.canonical_state_json
    assert tail.state_hash == full.state_hash


def test_post_mark_fill_has_identical_marked_full_and_tail_snapshots() -> None:
    events = (opening(), fill(number=2), mark(number=3), fill(number=4, price="110"))

    full = replay_portfolio(events)
    record, authority = checkpoint(events[:3])
    tail = replay_portfolio(events[3:], snapshot=record, authority=authority)

    assert tail == full
    position = full.canonical_snapshot.positions[0]
    assert position.mark is not None
    assert position.mark.price.amount == Decimal("101")
    assert position.average_entry_price is not None
    assert position.average_entry_price.amount == Decimal("105")
    assert position.unrealized_pnl.amount == Decimal("-8")
    assert full.canonical_snapshot.balances[0].unrealized_pnl.amount == Decimal("-8")


@pytest.mark.parametrize(
    ("action", "expected_quantity", "expected_average", "expected_unrealized", "expected_realized"),
    [
        ("close", "0.00", None, "0", "10"),
        ("correction", "1.00", "90", "11", "0"),
        ("bust", "0.00", None, "0", "0"),
    ],
)
def test_post_mark_close_correction_and_bust_match_full_and_tail_snapshots(
    action: str,
    expected_quantity: str,
    expected_average: str | None,
    expected_unrealized: str,
    expected_realized: str,
) -> None:
    normal = fill(number=2)
    if action == "close":
        adjustment = fill(number=4, side=OrderSide.SELL, price="110")
    elif action == "correction":
        adjustment = fill(
            number=4,
            price="90",
            status=FillReportStatus.CORRECTION,
            correction_of=normal.payload.fill.execution_id,
        )
    else:
        adjustment = fill(
            number=4,
            status=FillReportStatus.BUST,
            bust_of=normal.payload.fill.execution_id,
        )
    events = (opening(), normal, mark(number=3), adjustment)

    full = replay_portfolio(events)
    record, authority = checkpoint(events[:3])
    tail = replay_portfolio(events[3:], snapshot=record, authority=authority)

    assert tail == full
    position = full.canonical_snapshot.positions[0]
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
    assert position.realized_pnl.amount == Decimal(expected_realized)
    assert full.canonical_snapshot.balances[0].unrealized_pnl.amount == Decimal(
        expected_unrealized
    )


def test_canonical_snapshot_retains_closed_position_accounting_without_exposure() -> None:
    result = replay_portfolio(
        (
            opening(),
            fill(number=2),
            fill(number=3, side=OrderSide.SELL, price="110"),
        )
    )

    assert len(result.canonical_snapshot.positions) == 1
    position = result.canonical_snapshot.positions[0]
    assert position.quantity.value == Decimal("0.00")
    assert position.average_entry_price is None
    assert position.mark is None
    assert position.realized_pnl.amount == Decimal("10")
    assert position.unrealized_pnl.amount == Decimal("0")
    assert result.canonical_snapshot.total_exposure.gross.amount == Decimal("0")
    assert result.canonical_snapshot.instrument_exposures == ()


def test_snapshot_tail_rejects_exact_and_conflicting_funding_identity_reuse() -> None:
    original = funding(number=2, funding_id=uid(7_000))
    record, authority = checkpoint((opening(), original))
    exact_repeat = envelope(original.payload, number=3)
    conflicting_repeat = funding(number=3, funding_id=uid(7_000), amount="9")

    with pytest.raises(PortfolioReplayError, match="duplicate funding identity"):
        replay_portfolio((exact_repeat,), snapshot=record, authority=authority)
    with pytest.raises(PortfolioReplayError, match="conflicting funding identity"):
        replay_portfolio((conflicting_repeat,), snapshot=record, authority=authority)


def test_snapshot_tail_rejects_exact_and_conflicting_reconciliation_identity_reuse() -> None:
    original = reconciliation(number=2, reconciliation_id=uid(7_100))
    record, authority = checkpoint((opening(), original))
    exact_repeat = envelope(original.payload, number=3)
    conflicting_repeat = reconciliation(
        number=3, reconciliation_id=uid(7_100), cash="778"
    )

    with pytest.raises(PortfolioReplayError, match="duplicate reconciliation identity"):
        replay_portfolio((exact_repeat,), snapshot=record, authority=authority)
    with pytest.raises(PortfolioReplayError, match="conflicting reconciliation identity"):
        replay_portfolio((conflicting_repeat,), snapshot=record, authority=authority)


@pytest.mark.parametrize("terminal", ["bust", "reconciliation"])
def test_snapshot_tail_rejects_exact_and_conflicting_consumed_execution_reuse(
    terminal: str,
) -> None:
    original = fill(number=2, execution=7_200)
    if terminal == "bust":
        terminal_event = fill(
            number=3,
            execution=7_201,
            status=FillReportStatus.BUST,
            bust_of=original.payload.fill.execution_id,
        )
    else:
        terminal_event = reconciliation(number=3, reconciliation_id=uid(7_202))
    record, authority = checkpoint((opening(), original, terminal_event))
    exact_repeat = envelope(original.payload, number=4)
    conflicting_fill = original.payload.fill.model_copy(
        update={
            "last_fill_price": Price(Decimal("90"), Currency.USD),
            "average_fill_price": Price(Decimal("90"), Currency.USD),
        }
    )
    conflicting_repeat = envelope(
        original.payload.model_copy(update={"fill": conflicting_fill}), number=4
    )

    with pytest.raises(PortfolioReplayError, match="duplicate execution identity"):
        replay_portfolio((exact_repeat,), snapshot=record, authority=authority)
    with pytest.raises(PortfolioReplayError, match="conflicting execution identity"):
        replay_portfolio((conflicting_repeat,), snapshot=record, authority=authority)


def test_tampered_record_hash_fails_closed(portfolio_events) -> None:
    record, authority = checkpoint(portfolio_events[:3])

    with pytest.raises(PortfolioReplayError, match="state hash"):
        replay_portfolio(
            portfolio_events[3:],
            snapshot=record.model_copy(update={"state_hash": "0" * 64}),
            authority=authority,
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
    record, authority = checkpoint(portfolio_events[:3])
    applied = record.state.applied_events[0].model_copy(update={"digest": "0" * 64})
    bad_applied = record.model_copy(
        update={"state": record.state.model_copy(update={"applied_events": (applied, *record.state.applied_events[1:])})}
    )
    with pytest.raises(PortfolioReplayError, match="state hash"):
        replay_portfolio((), snapshot=bad_applied, authority=authority)

    effect = record.state.active_effects[0].model_copy(update={"logical_sequence": 1})
    bad_effect = record.model_copy(
        update={"state": record.state.model_copy(update={"active_effects": (effect,)})}
    )
    with pytest.raises(PortfolioReplayError, match="effect"):
        replay_portfolio((), snapshot=bad_effect, authority=authority)


def test_snapshot_rejects_hash_consistent_forged_execution_economics() -> None:
    normal = fill(number=2)
    prefix = (opening(), normal, mark(number=3))
    record, authority = checkpoint(prefix)
    effect = record.state.active_effects[0]
    forged_effect = effect.model_copy(
        update={"cash_deltas": (money("-1"),)}
    )
    forged_record = hash_consistent_record_with_state(
        record,
        record.state.model_copy(update={"active_effects": (forged_effect,)}),
    )
    bust_fill = fill(number=4).payload.fill.model_copy(
        update={
            "status": FillReportStatus.BUST,
            "bust_of_execution_id": normal.payload.fill.execution_id,
        }
    )
    bust = fill(number=4).model_copy(
        update={
            "payload": fill(number=4).payload.model_copy(update={"fill": bust_fill})
        }
    )

    with pytest.raises(PortfolioReplayError, match="effect"):
        replay_portfolio((bust,), snapshot=forged_record, authority=authority)


def test_snapshot_wraps_nested_position_validation_failures() -> None:
    record, authority = checkpoint((opening(), fill(number=2), mark(number=3)))
    position = record.state.snapshot.positions[0]
    assert position.mark is not None
    future_mark = position.mark.model_copy(
        update={"marked_at": record.state.snapshot.observed_at + timedelta(seconds=1)}
    )
    forged_position = position.model_copy(update={"mark": future_mark})
    forged_state = record.state.model_copy(
        update={
            "snapshot": record.state.snapshot.model_copy(
                update={"positions": (forged_position,)}
            )
        }
    )

    with pytest.raises(PortfolioReplayError, match="canonical portfolio state"):
        replay_portfolio(
            (),
            snapshot=record.model_copy(update={"state": forged_state}),
            authority=authority,
        )


@pytest.mark.parametrize("with_canonical_tail", [False, True])
def test_snapshot_rejects_hash_consistent_foreign_reconciliation(
    with_canonical_tail: bool, portfolio_events
) -> None:
    record, authority = checkpoint(portfolio_events[:3])
    foreign_record = hash_consistent_record_with_state(
        record,
        record.state.model_copy(update={"reconciliation": foreign_reconciliation()}),
    )

    selected_tail = portfolio_events[3:] if with_canonical_tail else ()
    with pytest.raises(PortfolioReplayError, match="reconciliation account"):
        replay_portfolio(selected_tail, snapshot=foreign_record, authority=authority)


def test_snapshot_tail_rejects_old_and_conflicting_events(portfolio_events) -> None:
    record, authority = checkpoint(portfolio_events[:3])
    old = portfolio_events[2]
    conflict = old.model_copy(update={"source": "another-source"})

    with pytest.raises(PortfolioReplayError, match="cursor"):
        replay_portfolio((old,), snapshot=record, authority=authority)
    with pytest.raises(PortfolioReplayError, match="conflicting"):
        replay_portfolio((conflict,), snapshot=record, authority=authority)


def test_snapshot_tail_rejects_foreign_scope_gap_and_regression(portfolio_events) -> None:
    record, authority = checkpoint(portfolio_events[:3])

    with pytest.raises(PortfolioReplayError, match="stream"):
        replay_portfolio(
            (fill(number=4, stream=OTHER_STREAM),),
            snapshot=record,
            authority=authority,
        )
    with pytest.raises(PortfolioReplayError, match="account"):
        replay_portfolio(
            (fill(number=4, account="account-2"),),
            snapshot=record,
            authority=authority,
        )
    with pytest.raises(PortfolioReplayError, match="sequence"):
        replay_portfolio((fill(number=5),), snapshot=record, authority=authority)
    with pytest.raises(PortfolioReplayError, match="cursor"):
        replay_portfolio(
            (fill(number=2).model_copy(update={"event_id": uid(9_999)}),),
            snapshot=record,
            authority=authority,
        )


def test_full_replay_rejects_distinct_canonical_bytes_for_one_event_id(portfolio_events) -> None:
    conflicting = portfolio_events[1].model_copy(update={"source": "other-source"})

    with pytest.raises(PortfolioReplayError, match="conflicting"):
        replay_portfolio((portfolio_events[0], portfolio_events[1], conflicting))


def test_full_replay_rejects_sequence_regression_with_a_distinct_event_id(
    portfolio_events,
) -> None:
    regressing = portfolio_events[1].model_copy(update={"event_id": uid(9_999)})

    with pytest.raises(PortfolioReplayError, match="sequence"):
        replay_portfolio((portfolio_events[0], portfolio_events[1], regressing))


def test_replay_rejects_caller_reordering(portfolio_events) -> None:
    with pytest.raises(PortfolioReplayError, match="ordered"):
        replay_portfolio((portfolio_events[1], portfolio_events[0], *portfolio_events[2:]))


def test_replay_does_not_call_persistence_or_a_provider(portfolio_events, monkeypatch) -> None:
    def forbidden(*_args, **_kwargs) -> None:
        raise AssertionError("pure portfolio replay must not perform external work")

    monkeypatch.setattr(InMemoryEventLedger, "append", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    assert replay_portfolio(portfolio_events).state.cursor[0].sequence == 5
