"""Run once and scalarize the fixed P1 Nautilus backtest."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from importlib.metadata import version as package_version

from nautilus_trader.model.events import OrderFilled

from .input_loader import RuntimeInputs
from .instrument_factory import build_instrument
from .market_data_loader import load_market_data
from .session import BacktestRunError, BacktestSessionFactory, create_session
from .target_planner import TargetPlan
from .target_strategy import (
    FilledOrderFact,
    QuoteFact,
    RejectedOrderFact,
    StrategyState,
    SubmittedOrderFact,
)


NativeScalar = str | int | None


@dataclass(frozen=True, slots=True)
class NativeFact:
    kind: str
    attributes: tuple[tuple[str, NativeScalar], ...]


@dataclass(frozen=True, slots=True)
class BacktestRun:
    engine_version: str
    iterations: int
    total_events: int
    total_orders: int
    total_positions: int
    result_summary: tuple[tuple[str, str], ...]
    account_count: int
    account_event_count: int
    instrument_ids: tuple[str, ...]
    strategy_state: str
    processed_target_ids: tuple[str, ...]
    pending_order_ids: tuple[str, ...]
    rejected_order_ids: tuple[str, ...]
    native_order_ids: tuple[str, ...]
    native_fill_ids: tuple[str, ...]
    order_count: int
    fill_count: int
    order_facts: tuple[tuple[str, str, str, str, str], ...]
    position_quantity: str
    balance_currencies: tuple[str, ...]
    balance_facts: tuple[tuple[str, str, str, str], ...]
    commission_facts: tuple[tuple[str, str], ...]
    native_facts: tuple[NativeFact, ...]
    position_average_entry: str
    position_realized_pnl: str
    position_unrealized_pnl: str
    final_market_price: str
    last_market_timestamp: int


def _text(value: Decimal) -> str:
    if not value.is_finite():
        raise BacktestRunError("native financial value is invalid")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _signals(values: tuple[str, ...]) -> tuple[tuple[str, NativeScalar], ...]:
    return (("source_signal_count", len(values)),) + tuple(
        (f"source_signal_id_{index}", value)
        for index, value in enumerate(values)
    )


def _submitted(value: SubmittedOrderFact) -> tuple[tuple[str, NativeScalar], ...]:
    return (
        ("client_order_id", value.client_order_id),
        ("target_id", value.target_id),
        *_signals(value.source_signal_ids),
        ("side", value.side),
        ("quantity", value.quantity),
        ("order_type", value.order_type),
    )


def _native_facts(records: tuple[tuple[str, object], ...]) -> tuple[NativeFact, ...]:
    facts: list[NativeFact] = []
    for kind, value in records:
        if kind == "quote" and type(value) is QuoteFact:
            attributes = (
                ("instrument_id", value.instrument_id),
                ("bid", value.bid),
                ("ask", value.ask),
                ("bid_size", value.bid_size),
                ("ask_size", value.ask_size),
                ("ts_event", value.ts_event),
            )
        elif kind == "target_planned" and type(value) is TargetPlan:
            attributes = (
                ("target_id", value.target_id),
                *_signals(value.source_signal_ids),
                ("effective_at", value.effective_at),
                ("instrument_id", value.instrument_id),
                ("current_quantity", value.current_quantity),
                ("target_quantity", value.target_quantity),
                ("delta", value.delta),
                ("side", value.side),
                ("price_basis", value.price_basis),
                ("notional", value.notional),
                ("reason", value.reason),
            )
        elif kind in {"order_submitted", "stop_pending"} and type(
            value
        ) is SubmittedOrderFact:
            attributes = _submitted(value)
        elif kind == "order_filled" and type(value) is FilledOrderFact:
            attributes = (
                ("client_order_id", value.client_order_id),
                ("trade_id", value.trade_id),
                ("side", value.side),
                ("quantity", value.quantity),
                ("price", value.price),
                ("commission", value.commission),
                ("commission_currency", value.commission_currency),
                ("ts_event", value.ts_event),
            )
        elif kind == "order_rejected" and type(value) is RejectedOrderFact:
            attributes = (
                ("client_order_id", value.client_order_id),
                ("reason", value.reason),
                ("ts_event", value.ts_event),
            )
        elif kind == "target_quantity_planned" and type(value) is str:
            attributes = (("quantity", value),)
        elif kind in {"stopped", "strategy_failed"} and type(value) is str:
            attributes = (("state" if kind == "stopped" else "reason", value),)
        else:
            raise BacktestRunError("strategy collector contains an unknown fact")
        if any(
            type(name) is not str or type(item) not in {str, int, type(None)}
            for name, item in attributes
        ):
            raise BacktestRunError("strategy collector contains a non-scalar fact")
        facts.append(NativeFact(kind, attributes))
    return tuple(facts)


def _fill_position(
    fills: tuple[FilledOrderFact, ...],
) -> tuple[str, str, str]:
    with localcontext() as context:
        context.prec = 96
        position = Decimal(0)
        average = Decimal(0)
        realized = Decimal(0)
        for fact in fills:
            quantity = Decimal(fact.quantity)
            price = Decimal(fact.price)
            if quantity <= 0 or price <= 0:
                raise BacktestRunError("native fill ledger is invalid")
            if fact.side == "BUY":
                new_position = position + quantity
                average = (position * average + quantity * price) / new_position
                position = new_position
            elif fact.side == "SELL" and quantity <= position:
                realized += (price - average) * quantity
                position -= quantity
                if position == 0:
                    average = Decimal(0)
            else:
                raise BacktestRunError("native fill ledger is invalid")
        return _text(position), _text(average), _text(realized)


def _callback_fill(value: FilledOrderFact) -> tuple[str, ...]:
    return (
        value.client_order_id,
        value.trade_id,
        value.side,
        value.quantity,
        value.price,
        value.commission,
        value.commission_currency,
        str(value.ts_event),
    )


def _cached_fill(value: OrderFilled) -> tuple[str, ...]:
    return (
        str(value.client_order_id),
        str(value.trade_id),
        value.order_side.name,
        _text(value.last_qty.as_decimal()),
        _text(value.last_px.as_decimal()),
        _text(value.commission.as_decimal()),
        str(value.commission.currency),
        str(value.ts_event),
    )


def _snapshot(engine, strategy, batch) -> BacktestRun:
    result = engine.get_result()
    accounts = tuple(engine.cache.accounts())
    orders = tuple(engine.cache.orders())
    positions = tuple(engine.cache.positions())
    instruments = tuple(engine.cache.instruments())
    records = strategy.collector.snapshot()
    native_facts = _native_facts(records)
    fill_facts = tuple(
        value
        for kind, value in records
        if kind == "order_filled" and type(value) is FilledOrderFact
    )
    cached_fills = tuple(
        event
        for order in orders
        for event in order.events
        if type(event) is OrderFilled
    )
    callback_fill_records = tuple(_callback_fill(value) for value in fill_facts)
    cached_fill_records = tuple(_cached_fill(value) for value in cached_fills)
    if (
        len(callback_fill_records) != len(cached_fill_records)
        or len({record[:2] for record in callback_fill_records})
        != len(callback_fill_records)
        or len({record[:2] for record in cached_fill_records})
        != len(cached_fill_records)
        or set(callback_fill_records) != set(cached_fill_records)
    ):
        raise BacktestRunError("native fill callback/cache proof is inconsistent")
    rejected = tuple(
        value.client_order_id
        for kind, value in records
        if kind == "order_rejected"
    )
    pending = tuple(str(order.client_order_id) for order in engine.cache.orders_open())
    expected_targets = tuple(item[0] for item in strategy.config.target_schedule)
    order_ids = tuple(str(order.client_order_id) for order in orders)
    fill_ids = tuple(fact.trade_id for fact in fill_facts)
    contradictions = tuple(
        label
        for label, invalid in (
            ("strategy", strategy.state != StrategyState.COMPLETED),
            ("strategy_pending", strategy.pending_order),
            ("targets", strategy.processed_target_ids != expected_targets),
            ("open_orders", bool(pending)),
            ("rejections", bool(rejected)),
            ("accounts", len(accounts) != 1),
            ("instruments", len(instruments) != 1),
            ("positions", len(positions) > 1),
            (
                "order_ids",
                len({str(order.client_order_id) for order in orders}) != len(orders),
            ),
            (
                "order_terminal",
                any(
                    not order.is_closed or order.filled_qty != order.quantity
                    for order in orders
                ),
            ),
            ("fills", len(fill_facts) != len(orders)),
            (
                "fill_orders",
                {fact.client_order_id for fact in fill_facts} != set(order_ids),
            ),
            (
                "fill_ids",
                any(not trade_id for trade_id in fill_ids)
                or len(set(fill_ids)) != len(fill_ids),
            ),
            ("result_orders", int(result.total_orders) != len(orders)),
            ("result_positions", int(result.total_positions) != len(positions)),
            ("iterations", int(result.iterations) != len(batch.data)),
            ("events", int(result.total_events) <= 0),
        )
        if invalid
    )
    if contradictions:
        raise BacktestRunError(
            "native terminal state is inconsistent: " + ",".join(contradictions)
        )
    position_quantity, position_average, position_realized = _fill_position(
        fill_facts
    )
    final_market_price = batch.data[-1].close.as_decimal()
    expected_unrealized = (final_market_price - Decimal(position_average)) * Decimal(
        position_quantity
    )
    if positions:
        position = positions[0]
        cached_quantity = position.quantity.as_decimal()
        if position.side.name == "SHORT":
            cached_quantity = -cached_quantity
        elif position.side.name == "FLAT":
            cached_quantity = Decimal(0)
        cached_average = (
            Decimal(0)
            if cached_quantity == 0
            else Decimal(str(position.avg_px_open))
        )
        native_unrealized = position.unrealized_pnl(
            batch.data[-1].close
        ).as_decimal()
    else:
        cached_quantity = cached_average = native_unrealized = Decimal(0)
    if (
        _text(cached_quantity) != position_quantity
        or _text(cached_average) != position_average
        or native_unrealized != expected_unrealized
    ):
        raise BacktestRunError("native terminal position proof is inconsistent")
    account = accounts[0]
    balances = account.balances_total()
    commissions = account.commissions()
    if any(
        not money.as_decimal().is_finite() or money.as_decimal() < 0
        for money in (*balances.values(), *commissions.values())
    ):
        raise BacktestRunError("native account contains an invalid financial value")
    locked = account.balances_locked()
    free = account.balances_free()
    if set(balances) != set(locked) or set(balances) != set(free):
        raise BacktestRunError("native account balance currencies are inconsistent")
    if any(
        not money.as_decimal().is_finite() or money.as_decimal() < 0
        for money in (*locked.values(), *free.values())
    ):
        raise BacktestRunError("native account contains an invalid financial value")
    balance_facts = tuple(
        sorted(
            (
                str(currency),
                _text(total.as_decimal()),
                _text(locked[currency].as_decimal()),
                _text(free[currency].as_decimal()),
            )
            for currency, total in balances.items()
        )
    )
    if any(
        Decimal(total) != Decimal(locked_value) + Decimal(free_value)
        for _, total, locked_value, free_value in balance_facts
    ):
        raise BacktestRunError("native account balance arithmetic is inconsistent")
    commission_facts = tuple(
        sorted((str(currency), _text(money.as_decimal())) for currency, money in commissions.items())
    )
    collected_commissions: dict[str, Decimal] = {}
    for fact in fill_facts:
        collected_commissions[fact.commission_currency] = collected_commissions.get(
            fact.commission_currency, Decimal(0)
        ) + Decimal(fact.commission)
    if {
        str(currency): money.as_decimal() for currency, money in commissions.items()
    } != collected_commissions:
        raise BacktestRunError("native commission proof is inconsistent")
    currencies = tuple(currency for currency, *_ in balance_facts)
    if set(currencies) - {
        str(instruments[0].base_currency),
        str(instruments[0].quote_currency),
    }:
        raise BacktestRunError("native account contains an unexpected currency")
    with localcontext() as context:
        context.prec = 96
        expected = {
            str(currency): money.as_decimal()
            for currency, money in account.starting_balances().items()
        }
        base = str(instruments[0].base_currency)
        quote = str(instruments[0].quote_currency)
        expected.setdefault(base, Decimal(0))
        expected.setdefault(quote, Decimal(0))
        for fact in fill_facts:
            quantity = Decimal(fact.quantity)
            notional = quantity * Decimal(fact.price)
            expected[base] += quantity if fact.side == "BUY" else -quantity
            expected[quote] += -notional if fact.side == "BUY" else notional
            expected[fact.commission_currency] -= Decimal(fact.commission)
        observed = {
            str(currency): money.as_decimal() for currency, money in balances.items()
        }
        if observed != expected:
            raise BacktestRunError("native account balance proof is inconsistent")
    return BacktestRun(
        engine_version=package_version("nautilus_trader"),
        iterations=int(result.iterations),
        total_events=int(result.total_events),
        total_orders=int(result.total_orders),
        total_positions=int(result.total_positions),
        result_summary=tuple(
            sorted((str(key), str(value)) for key, value in result.summary.items())
        ),
        account_count=len(accounts),
        account_event_count=int(account.event_count),
        instrument_ids=tuple(sorted(str(item.id) for item in instruments)),
        strategy_state=strategy.state,
        processed_target_ids=strategy.processed_target_ids,
        pending_order_ids=pending,
        rejected_order_ids=rejected,
        native_order_ids=order_ids,
        native_fill_ids=fill_ids,
        order_count=len(orders),
        fill_count=len(fill_facts),
        order_facts=tuple(
            (
                str(order.client_order_id),
                order.side.name,
                _text(order.quantity.as_decimal()),
                _text(order.filled_qty.as_decimal()),
                order.status.name,
            )
            for order in orders
        ),
        position_quantity=position_quantity,
        balance_currencies=currencies,
        balance_facts=balance_facts,
        commission_facts=commission_facts,
        native_facts=native_facts,
        position_average_entry=position_average,
        position_realized_pnl=position_realized,
        position_unrealized_pnl=_text(expected_unrealized),
        final_market_price=_text(final_market_price),
        last_market_timestamp=max(int(item.ts_event) for item in batch.data),
    )


def run_backtest(
    inputs: RuntimeInputs,
    session_factory: BacktestSessionFactory = create_session,
) -> BacktestRun:
    """Run the exact fixed profile once and return no native object."""

    instrument = build_instrument(inputs.instrument_catalog)
    batch = load_market_data(inputs, instrument)
    session = session_factory(inputs, instrument, batch)
    try:
        session.run()
        snapshot = _snapshot(session.engine, session.strategy, session.batch)
    except BaseException as primary:
        session.dispose(primary)
        raise AssertionError("unreachable")
    session.dispose()
    return snapshot


__all__ = ["BacktestRun", "BacktestRunError", "NativeFact", "run_backtest"]
