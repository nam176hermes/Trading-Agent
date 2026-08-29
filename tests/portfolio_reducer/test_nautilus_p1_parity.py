from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import (
    AccountBalanceSnapshot,
    AssetClass,
    Currency,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderQuantity,
    PortfolioOpeningEntry,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.engine_contracts import canonical_json_bytes
from packages.engine_event_ledger import EngineEventTypeCount, EngineRunProjection
from packages.engine_portfolio_projection.models import ProjectionAuthority
from packages.engine_portfolio_projection.parity import (
    P1PortfolioParityError,
    P1PortfolioParityReceipt,
    verify_p1_portfolio_parity,
)
from packages.nautilus_runtime_contracts.artifacts import P1InstrumentCatalogV1
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


NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)
RUN_ID = UUID("10000000-0000-4000-8000-000000000001")
REQUEST_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_REQUEST_ID = UUID("20000000-0000-4000-8000-000000000002")
BATCH_SHA256 = "a" * 64
CLOSURE_SHA256 = "b" * 64
CATALOG_PROVENANCE = "c" * 64
LAST_DIGEST = "d" * 64
FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/p1_nautilus/scenarios.json").read_text()
)


def _money(value: str) -> Money:
    return Money(Decimal(value), Currency.USDT)


def _catalog() -> P1InstrumentCatalogV1:
    return P1InstrumentCatalogV1(
        schema_version="nautilus-p1-instrument-catalog-v1",
        instrument_id="BTCUSDT.BINANCE",
        product_type="crypto_spot",
        symbol="BTCUSDT",
        base_currency="BTC",
        quote_currency="USDT",
        venue="BINANCE",
        price_precision=2,
        size_precision=6,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.000001"),
        min_quantity=Decimal("0.000001"),
        min_notional=Decimal("0.01"),
        provenance_sha256=CATALOG_PROVENANCE,
    )


def _instrument() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE"),
        raw_symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=Currency.USDT,
        settlement_currency=Currency.USDT,
        tick_size=Price(Decimal("0.01"), Currency.USDT),
        size_increment=OrderQuantity(Decimal("0.000001"), 6),
        minimum_quantity=OrderQuantity(Decimal("0.000001"), 6),
        maximum_quantity=OrderQuantity(Decimal("1000000"), 6),
        minimum_notional=_money("0.01"),
        maximum_notional=_money("100000000"),
        multiplier=Decimal(1),
        margin=None,
        session_calendar="24X7",
        provenance=InstrumentProvenance(
            "P1CATALOG", CATALOG_PROVENANCE[:32], NOW
        ),
    )


def _opening(*, cash: str = "1000") -> PortfolioOpeningEntry:
    zero = _money("0")
    balance = AccountBalanceSnapshot(
        account_id="account-1",
        currency=Currency.USDT,
        cash=_money(cash),
        locked_funds=zero,
        margin_used=zero,
        realized_pnl=zero,
        unrealized_pnl=zero,
        fees=zero,
        funding=zero,
        observed_at=NOW,
        schema_version="balance-v1",
    )
    return PortfolioOpeningEntry(
        account_id="account-1",
        reporting_currency=Currency.USDT,
        balances=(balance,),
        source_id="p1-opening",
        source_revision="r1",
        effective_at=NOW,
        schema_version="portfolio-entry-v1",
    )


def _authority(*, opening: PortfolioOpeningEntry | None = None) -> ProjectionAuthority:
    return ProjectionAuthority(
        request_message_id=REQUEST_ID,
        catalog=_catalog(),
        instrument=_instrument(),
        opening=opening or _opening(),
        strategy_id="strategy-1",
        liquidity_side=LiquiditySide.TAKER,
        reconciliation_source=ReconciliationSource.VENUE,
    )


def _time(sequence: int) -> datetime:
    return NOW + timedelta(seconds=sequence)


def _stream(name: str) -> tuple[object, ...]:
    fixture = FIXTURES[name]
    terminal = fixture["terminal"]
    sequence = 2
    events: list[object] = [
        P1RunStarted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="RunStarted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=sequence,
            simulation_time=_time(sequence),
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit="e" * 40,
            closure_digest=CLOSURE_SHA256,
            config_digest="f" * 64,
            catalog_digest=sha256(canonical_json_bytes(_catalog())).hexdigest(),
            data_digest="1" * 64,
        )
    ]
    orders = fixture["orders"]
    for order_index, order in enumerate(orders or ({"quantity": "0"},), start=1):
        sequence += 1
        target_id = f"target-{order_index}"
        signal_id = f"signal-{order_index}"
        events.append(
            P1TargetAccepted(
                schema_version="nautilus-p1-event-stream-v1",
                event_type="TargetAccepted",
                origin="CONTROL_PLANE",
                native_type=None,
                sequence=sequence,
                simulation_time=_time(sequence),
                target_id=target_id,
                source_signal_ids=(signal_id,),
                target_weight=Decimal("0" if not orders or order.get("side") == "SELL" else "1"),
            )
        )
        sequence += 1
        events.append(
            P1TargetQuantityPlanned(
                schema_version="nautilus-p1-event-stream-v1",
                event_type="TargetQuantityPlanned",
                origin="CONTROL_PLANE",
                native_type=None,
                sequence=sequence,
                simulation_time=_time(sequence),
                target_id=target_id,
                quantity=Decimal(order["quantity"]),
            )
        )
        if not orders:
            continue
        sequence += 1
        order_id = f"order-{order_index}"
        events.append(
            P1OrderSubmitted(
                schema_version="nautilus-p1-event-stream-v1",
                event_type="OrderSubmitted",
                origin="CONTROL_PLANE",
                native_type="Order",
                sequence=sequence,
                simulation_time=_time(sequence),
                client_order_id=order_id,
                native_order_id=f"native-order-{order_index}",
                target_id=target_id,
                source_signal_ids=(signal_id,),
                side=order["side"],
                quantity=Decimal(order["quantity"]),
                order_type="MARKET",
            )
        )
        for fill_index, fill in enumerate(order["fills"], start=1):
            sequence += 1
            events.append(
                P1Fill(
                    schema_version="nautilus-p1-event-stream-v1",
                    event_type="Fill",
                    origin="NAUTILUS_CALLBACK",
                    native_type="OrderFilled",
                    sequence=sequence,
                    simulation_time=_time(sequence),
                    client_order_id=order_id,
                    native_fill_id=f"native-fill-{order_index}-{fill_index}",
                    side=order["side"],
                    quantity=Decimal(fill["quantity"]),
                    price=Decimal(fill["price"]),
                    fee=Decimal(fill["fee"]),
                    fee_currency="USDT",
                )
            )
    sequence += 1
    events.append(
        P1PositionObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="PositionObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Position",
            sequence=sequence,
            simulation_time=_time(sequence),
            quantity=Decimal(terminal["position"]),
            average_entry_price=Decimal(terminal["average"]),
            realized_pnl=Decimal(terminal["realized"]),
            unrealized_pnl=Decimal(terminal["unrealized"]),
        )
    )
    sequence += 1
    events.append(
        P1AccountObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="AccountObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Account",
            sequence=sequence,
            simulation_time=_time(sequence),
            cash_balance=Decimal(terminal["cash"]),
            fees=Decimal(terminal["fees"]),
            realized_pnl=Decimal(terminal["realized"]),
            unrealized_pnl=Decimal(terminal["unrealized"]),
        )
    )
    sequence += 1
    events.append(
        P1RunCompleted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="RunCompleted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=sequence,
            simulation_time=_time(sequence),
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit="e" * 40,
            closure_digest=CLOSURE_SHA256,
            target_count=max(1, len(orders)),
            order_count=len(orders),
            fill_count=sum(len(order["fills"]) for order in orders),
            final_cash=Decimal(terminal["cash"]),
            final_position=Decimal(terminal["position"]),
            fees=Decimal(terminal["fees"]),
            realized_pnl=Decimal(terminal["realized"]),
            unrealized_pnl=Decimal(terminal["unrealized"]),
            semantic_digest="0" * 64,
        )
    )
    typed = tuple(events)
    return typed[:-1] + (
        typed[-1].model_copy(update={"semantic_digest": semantic_digest(typed)}),
    )


def _run_projection(events: tuple[object, ...]) -> EngineRunProjection:
    counts = Counter(event.event_type for event in events)
    return EngineRunProjection(
        engine_run_id=RUN_ID,
        event_count=len(events),
        event_type_counts=tuple(
            EngineEventTypeCount(event_type=name, count=counts[name])
            for name in sorted(counts)
        ),
        last_sequence=events[-1].sequence,
        last_digest=LAST_DIGEST,
        batch_sha256=BATCH_SHA256,
        semantic_digest=events[-1].semantic_digest,
        request_message_id=REQUEST_ID,
    )


def _verify(
    events: tuple[object, ...],
    *,
    authority: ProjectionAuthority | None = None,
    projection: EngineRunProjection | None = None,
    batch_sha256: str = BATCH_SHA256,
) -> P1PortfolioParityReceipt:
    return verify_p1_portfolio_parity(
        events,
        authority or _authority(),
        projection or _run_projection(events),
        batch_sha256=batch_sha256,
    )


@pytest.mark.parametrize("scenario", tuple(sorted(FIXTURES)))
def test_exact_parity_for_p1_accounting_scenarios(scenario: str) -> None:
    events = _stream(scenario)
    terminal = FIXTURES[scenario]["terminal"]

    receipt = _verify(events)

    assert receipt.schema_version == "nautilus-p1-portfolio-parity-v1"
    assert receipt.normalization_version == "nautilus-p1-portfolio-normalization-v1"
    assert receipt.batch_sha256 == BATCH_SHA256
    assert receipt.semantic_digest == events[-1].semantic_digest
    assert receipt.request_message_id == REQUEST_ID
    assert receipt.engine_event_count == len(events)
    assert receipt.engine_last_sequence == events[-1].sequence
    assert receipt.engine_last_digest == LAST_DIGEST
    assert receipt.portfolio_event_count >= 2
    assert receipt.portfolio_last_sequence == receipt.portfolio_event_count
    assert receipt.restart_prefix_sequence == 1
    assert receipt.account_currency is Currency.USDT
    assert receipt.terminal_position == Decimal(terminal["position"])
    assert receipt.terminal_average_entry_price == (
        None if terminal["average"] == "0" else Decimal(terminal["average"])
    )
    assert receipt.terminal_mark_price == (
        None if terminal["mark"] is None else Decimal(terminal["mark"])
    )
    assert receipt.terminal_cash == Decimal(terminal["cash"])
    assert receipt.terminal_fees == Decimal(terminal["fees"])
    assert receipt.terminal_realized_pnl == Decimal(terminal["realized"])
    assert receipt.terminal_unrealized_pnl == Decimal(terminal["unrealized"])
    assert len(receipt.portfolio_state_hash) == 64
    assert len(receipt.portfolio_prefix_history_hash) == 64
    assert _verify(events) == receipt


@pytest.mark.parametrize("mutation", ("quantity", "fee", "price", "side", "sequence"))
def test_event_fact_mutation_fails_closed(mutation: str) -> None:
    events = list(_stream("flatten"))
    fill_index = next(index for index, event in enumerate(events) if isinstance(event, P1Fill))
    fill = events[fill_index]
    updates = {
        "quantity": {"quantity": Decimal("0.5")},
        "fee": {"fee": Decimal("0.2")},
        "price": {"price": Decimal("101")},
        "side": {"side": "SELL"},
        "sequence": {"sequence": fill.sequence + 1},
    }
    events[fill_index] = fill.model_copy(update=updates[mutation])
    mutated = tuple(events)
    if mutation != "sequence":
        mutated = mutated[:-1] + (
            mutated[-1].model_copy(update={"semantic_digest": semantic_digest(mutated)}),
        )

    with pytest.raises(P1PortfolioParityError):
        _verify(mutated, projection=_run_projection(mutated))


def test_opening_balance_mutation_fails_closed() -> None:
    events = _stream("long_entry")

    with pytest.raises(P1PortfolioParityError):
        _verify(events, authority=_authority(opening=_opening(cash="999")))


@pytest.mark.parametrize("field", ("batch_sha256", "semantic_digest", "request_message_id"))
def test_durable_authority_mutation_fails_closed(field: str) -> None:
    events = _stream("netting_close_reopen")
    projection = _run_projection(events)
    values = {
        "batch_sha256": "0" * 64,
        "semantic_digest": "1" * 64,
        "request_message_id": OTHER_REQUEST_ID,
    }

    with pytest.raises(P1PortfolioParityError):
        _verify(events, projection=projection.model_copy(update={field: values[field]}))


def test_projection_event_counts_are_bound_to_the_typed_stream() -> None:
    events = _stream("partial_fill")
    projection = _run_projection(events)
    wrong_count = projection.event_type_counts[0].model_copy(update={"count": 2})

    with pytest.raises(P1PortfolioParityError):
        _verify(
            events,
            projection=projection.model_copy(
                update={"event_type_counts": (wrong_count, *projection.event_type_counts[1:])}
            ),
        )


def test_receipt_is_immutable_and_forbids_unknown_fields() -> None:
    receipt = _verify(_stream("hold"))

    with pytest.raises(ValidationError):
        receipt.terminal_cash = Decimal("0")  # type: ignore[misc]
    with pytest.raises(ValidationError):
        P1PortfolioParityReceipt.model_validate(
            {**receipt.model_dump(), "ambient_authority": "forbidden"}
        )
