"""Prove the scalar native terminal state before event projection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
import json

from .bootstrap import RuntimeBootstrapError, require_product_lineage
from .currency_metadata import currency_quanta
from .event_collector import collect_executions
from .event_projector import CompletionAuthority


class FinalStateError(ValueError):
    """The native scalar snapshot did not prove one complete run."""


def _text(number: Decimal) -> str:
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _decimal(value: object) -> Decimal:
    if type(value) is not str:
        raise FinalStateError("runtime final state is inconsistent")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise FinalStateError("runtime final state is inconsistent") from exc
    if not number.is_finite() or _text(number) != value:
        raise FinalStateError("runtime final state is inconsistent")
    return number


def _mapping(value: object) -> dict[str, object]:
    if type(value) is not tuple:
        raise FinalStateError("runtime final state is inconsistent")
    try:
        result = dict(value)
    except (TypeError, ValueError) as exc:
        raise FinalStateError("runtime final state is inconsistent") from exc
    if len(result) != len(value) or any(type(key) is not str for key in result):
        raise FinalStateError("runtime final state is inconsistent")
    return result


def _integer(summary: dict[str, object], name: str) -> int:
    value = summary.get(name)
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        raise FinalStateError("runtime final state is inconsistent")
    return int(value)


def _target_ids(inputs: object) -> tuple[str, ...]:
    schedule = _mapping(inputs.target_schedule)
    targets = schedule.get("targets")
    if (
        schedule.get("schema_version") != "nautilus-p1-target-schedule-v1"
        or type(targets) is not tuple
        or not targets
    ):
        raise FinalStateError("runtime final state is inconsistent")
    result: list[str] = []
    for frozen in targets:
        target = _mapping(frozen)
        target_id = target.get("target_id")
        if type(target_id) is not str or not target_id or target_id in result:
            raise FinalStateError("runtime final state is inconsistent")
        result.append(target_id)
    return tuple(result)


def _market_facts(inputs: object) -> tuple[int, Decimal, int]:
    raw = inputs.market_data
    if type(raw) is not bytes or not raw or not raw.endswith(b"\n"):
        raise FinalStateError("runtime final state is inconsistent")
    try:
        lines = raw.splitlines()
        if not lines:
            raise FinalStateError("runtime final state is inconsistent")
        rows = tuple(json.loads(line) for line in lines)
        last = rows[-1]
        close = _decimal(last["close"])
        timestamp = datetime.fromisoformat(
            str(last["event_time"]).removesuffix("Z") + "+00:00"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FinalStateError("runtime final state is inconsistent") from exc
    if (
        any(type(row) is not dict for row in rows)
        or timestamp.tzinfo is None
        or timestamp.utcoffset() is None
        or timestamp.astimezone(UTC) != timestamp
    ):
        raise FinalStateError("runtime final state is inconsistent")
    delta = timestamp - datetime(1970, 1, 1, tzinfo=UTC)
    native_time = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )
    return len(rows), close, native_time


def _money_facts(run: object) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    balances: dict[str, Decimal] = {}
    for fact in run.balance_facts:
        if type(fact) is not tuple or len(fact) != 4:
            raise FinalStateError("runtime final state is inconsistent")
        currency, total, locked, free = fact
        if type(currency) is not str or currency in balances:
            raise FinalStateError("runtime final state is inconsistent")
        values = tuple(_decimal(item) for item in (total, locked, free))
        if any(item < 0 for item in values) or values[0] != values[1] + values[2]:
            raise FinalStateError("runtime final state is inconsistent")
        balances[currency] = values[0]
    commissions: dict[str, Decimal] = {}
    for fact in run.commission_facts:
        if type(fact) is not tuple or len(fact) != 2:
            raise FinalStateError("runtime final state is inconsistent")
        currency, amount = fact
        if type(currency) is not str or currency in commissions:
            raise FinalStateError("runtime final state is inconsistent")
        commissions[currency] = _decimal(amount)
        if commissions[currency] < 0:
            raise FinalStateError("runtime final state is inconsistent")
    return balances, commissions


def _ledger(
    executions: object,
    starting_cash: Decimal,
    quote_currency: str,
    base_quantum: Decimal,
    quote_quantum: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal, dict[str, Decimal]]:
    cash = starting_cash
    position = average = realized = Decimal(0)
    commissions: dict[str, Decimal] = {}
    with localcontext() as context:
        context.prec = 96
        for execution in executions:
            for fill_fact in execution.fills:
                fill = dict(fill_fact)
                quantity = _decimal(fill["quantity"])
                price = _decimal(fill["price"])
                fee = _decimal(fill["commission"])
                currency = fill["commission_currency"]
                if (
                    quantity <= 0
                    or price <= 0
                    or fee < 0
                    or type(currency) is not str
                    or currency != quote_currency
                ):
                    raise FinalStateError("runtime final state is inconsistent")
                commissions[currency] = commissions.get(currency, Decimal(0)) + fee
                if fill["side"] == "BUY":
                    new_position = (position + quantity).quantize(
                        base_quantum, rounding=ROUND_HALF_EVEN
                    )
                    average = (position * average + quantity * price) / new_position
                    position = new_position
                    cash = (cash - quantity * price - fee).quantize(
                        quote_quantum, rounding=ROUND_HALF_EVEN
                    )
                elif fill["side"] == "SELL" and quantity <= position:
                    realized += (price - average) * quantity
                    position = (position - quantity).quantize(
                        base_quantum, rounding=ROUND_HALF_EVEN
                    )
                    cash = (cash + quantity * price - fee).quantize(
                        quote_quantum, rounding=ROUND_HALF_EVEN
                    )
                    if position == 0:
                        average = Decimal(0)
                else:
                    raise FinalStateError("runtime final state is inconsistent")
    return cash, position, average, realized, commissions


def _validate(
    inputs: object, lineage: object, run: object
) -> CompletionAuthority:
    try:
        require_product_lineage(lineage)
    except RuntimeBootstrapError as exc:
        raise FinalStateError("runtime final state lineage is inconsistent") from exc
    if type(lineage) is not dict:
        raise FinalStateError("runtime final state lineage is inconsistent")
    configuration = _mapping(inputs.engine_configuration)
    catalog = _mapping(inputs.instrument_catalog)
    target_ids = _target_ids(inputs)
    row_count, final_price, final_timestamp = _market_facts(inputs)
    summary = _mapping(run.result_summary)
    quotes, executions = collect_executions(run)
    expected_order_facts = tuple(
        (
            str(dict(execution.order)["client_order_id"]),
            str(dict(execution.order)["side"]),
            str(dict(execution.order)["quantity"]),
            str(dict(execution.order)["quantity"]),
            "FILLED",
        )
        for execution in executions
        if execution.order is not None and execution.fills
    )
    base_currency = catalog.get("base_currency")
    quote_currency = catalog.get("quote_currency")
    starting_currency = configuration.get("starting_currency")
    instrument_id = catalog.get("instrument_id")
    if not all(
        type(value) is str and value
        for value in (base_currency, quote_currency, starting_currency, instrument_id)
    ) or starting_currency != quote_currency:
        raise FinalStateError("runtime final state is inconsistent")
    starting_cash = _decimal(configuration.get("starting_balance"))
    balances, observed_commissions = _money_facts(run)
    try:
        base_quantum, quote_quantum = currency_quanta(base_currency, quote_currency)
    except ValueError as exc:
        raise FinalStateError("runtime final state is inconsistent") from exc
    cash, position, average, realized, expected_commissions = _ledger(
        executions,
        starting_cash,
        quote_currency,
        base_quantum,
        quote_quantum,
    )
    expected_positions = 1 if run.order_count else 0
    expected_open = 1 if position else 0
    integers = (
        run.iterations,
        run.total_events,
        run.total_orders,
        run.total_positions,
        run.account_count,
        run.account_event_count,
        run.order_count,
        run.fill_count,
    )
    if (
        any(type(value) is not int or value < 0 for value in integers)
        or run.engine_version != lineage["engine_version"]
        or run.strategy_state != "COMPLETED"
        or run.processed_target_ids != target_ids
        or run.pending_order_ids != ()
        or run.rejected_order_ids != ()
        or run.account_count != 1
        or run.instrument_ids != (instrument_id,)
        or run.iterations != row_count * 2
        or run.iterations != _integer(summary, "iterations")
        or run.total_events != run.order_count + run.fill_count
        or run.total_events != _integer(summary, "total_events")
        or run.total_orders != run.order_count
        or run.total_orders != _integer(summary, "orders.total")
        or run.total_positions != expected_positions
        or run.total_positions != _integer(summary, "positions.total_with_snapshots")
        or run.order_count != len(run.order_facts)
        or run.order_facts != expected_order_facts
        or run.order_count != len(run.native_order_ids)
        or run.fill_count != len(run.native_fill_ids)
        or _integer(summary, "orders.open") != 0
        or _integer(summary, "orders.closed") != run.order_count
        or _integer(summary, "orders.emulated") != 0
        or _integer(summary, "orders.inflight") != 0
        or _integer(summary, "positions.open") != expected_open
        or _integer(summary, "positions.closed") != expected_positions - expected_open
        or _integer(summary, "positions.snapshots") != 0
        or _integer(summary, "positions.total") != expected_positions
        or _integer(summary, "venues.total") != 1
        or summary.get("account.BINANCE.type") != "CASH"
        or summary.get("account.BINANCE.base_currency") != "None"
        or run.account_event_count != 1 + run.fill_count
        or run.account_event_count != _integer(summary, "account.BINANCE.event_count")
        or tuple(balances) != run.balance_currencies
        or set(balances) - {base_currency, quote_currency}
        or quote_currency not in balances
        or balances.get(base_currency, Decimal(0)) != position
        or balances[quote_currency] != cash
        or observed_commissions != expected_commissions
        or _decimal(run.position_quantity) != position
        or _decimal(run.position_average_entry)
        != average.quantize(quote_quantum, rounding=ROUND_HALF_EVEN)
        or _decimal(run.position_realized_pnl)
        != realized.quantize(quote_quantum, rounding=ROUND_HALF_EVEN)
        or _decimal(run.final_market_price) != final_price
        or run.last_market_timestamp != final_timestamp
        or len(quotes) != row_count
    ):
        raise FinalStateError("runtime final state is inconsistent")
    unrealized = ((final_price - average) * position).quantize(
        quote_quantum, rounding=ROUND_HALF_EVEN
    )
    if _decimal(run.position_unrealized_pnl) != unrealized:
        raise FinalStateError("runtime final state is inconsistent")
    return CompletionAuthority(
        target_count=len(target_ids),
        order_count=run.order_count,
        fill_count=run.fill_count,
        final_cash=_text(cash),
        final_position=run.position_quantity,
        fees=_text(expected_commissions.get(quote_currency, Decimal(0))),
        realized_pnl=run.position_realized_pnl,
        unrealized_pnl=run.position_unrealized_pnl,
    )


def validate_final_state(
    inputs: object, lineage: object, run: object
) -> CompletionAuthority:
    try:
        return _validate(inputs, lineage, run)
    except FinalStateError:
        raise
    except (
        ArithmeticError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise FinalStateError("runtime final state is inconsistent") from exc


__all__ = ["FinalStateError", "validate_final_state"]
