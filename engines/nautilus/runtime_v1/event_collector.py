"""Validate the scalar native fact stream before event projection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


_SCALAR = {str, int, type(None)}
_QUOTE = ("instrument_id", "bid", "ask", "bid_size", "ask_size", "ts_event")
_PLAN = (
    "target_id",
    "effective_at",
    "instrument_id",
    "current_quantity",
    "target_quantity",
    "delta",
    "side",
    "price_basis",
    "notional",
    "reason",
)
_ORDER = ("client_order_id", "target_id", "side", "quantity", "order_type")
_FILL = (
    "client_order_id",
    "trade_id",
    "side",
    "quantity",
    "price",
    "commission",
    "commission_currency",
    "ts_event",
)


@dataclass(frozen=True, slots=True)
class CollectedExecution:
    plan: tuple[tuple[str, str | int | None], ...]
    planned_quantity: str
    order: tuple[tuple[str, str | int | None], ...] | None
    fill: tuple[tuple[str, str | int | None], ...] | None


def _attributes(fact: object) -> tuple[tuple[str, str | int | None], ...]:
    attributes = fact.attributes
    if (
        type(attributes) is not tuple
        or not attributes
        or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) not in _SCALAR
            for item in attributes
        )
        or len({name for name, _ in attributes}) != len(attributes)
    ):
        raise ValueError("native fact stream is invalid")
    return attributes


def _document(
    fact: object,
    kind: str,
    fixed_names: tuple[str, ...],
    *,
    signals: bool = False,
) -> tuple[tuple[str, str | int | None], ...]:
    if fact.kind != kind:
        raise ValueError("native fact stream is invalid")
    attributes = _attributes(fact)
    values = dict(attributes)
    names = set(fixed_names)
    if signals:
        count = values.get("source_signal_count")
        if type(count) is not int or not 1 <= count <= 64:
            raise ValueError("native fact stream is invalid")
        names |= {"source_signal_count"} | {
            f"source_signal_id_{index}" for index in range(count)
        }
    if set(values) != names:
        raise ValueError("native fact stream is invalid")
    return attributes


def _text(values: tuple[tuple[str, str | int | None], ...], name: str) -> str:
    value = dict(values)[name]
    if type(value) is not str or not value:
        raise ValueError("native fact stream is invalid")
    return value


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def collect_executions(run: object) -> tuple[CollectedExecution, ...]:
    """Return validated immutable executions; never retain native objects."""

    facts = run.native_facts
    if (
        type(facts) is not tuple
        or not facts
        or len(facts) < 4
        or len(facts) > 4096
        or run.engine_version != "1.231.0"
        or run.strategy_state != "COMPLETED"
        or run.pending_order_ids != ()
        or run.rejected_order_ids != ()
    ):
        raise ValueError("native fact stream is invalid")

    executions: list[CollectedExecution] = []
    target_ids: list[str] = []
    native_order_ids: list[str] = []
    native_fill_ids: list[str] = []
    index = 0
    while index < len(facts) - 1:
        _document(facts[index], "quote", _QUOTE)
        plan = _document(facts[index + 1], "target_planned", _PLAN, signals=True)
        quantity_fact = _document(
            facts[index + 2], "target_quantity_planned", ("quantity",)
        )
        target_id = _text(plan, "target_id")
        planned_quantity = _text(quantity_fact, "quantity")
        try:
            delta = Decimal(_text(plan, "delta"))
        except InvalidOperation as exc:
            raise ValueError("native fact stream is invalid") from exc
        if (
            not delta.is_finite()
            or planned_quantity != _decimal_text(abs(delta))
            or target_id in target_ids
        ):
            raise ValueError("native fact stream is invalid")
        target_ids.append(target_id)
        index += 3
        order = fill = None
        if index < len(facts) - 1 and facts[index].kind == "order_submitted":
            order = _document(facts[index], "order_submitted", _ORDER, signals=True)
            fill = _document(facts[index + 1], "order_filled", _FILL)
            if (
                _text(order, "target_id") != target_id
                or _text(order, "quantity") != planned_quantity
                or _text(order, "side") != _text(plan, "side")
                or _text(order, "order_type") != "MARKET"
                or _text(fill, "client_order_id") != _text(order, "client_order_id")
                or _text(fill, "side") != _text(order, "side")
                or _text(fill, "quantity") != planned_quantity
                or _text(fill, "commission_currency") != "USDT"
            ):
                raise ValueError("native fact stream is invalid")
            native_order_ids.append(_text(order, "client_order_id"))
            native_fill_ids.append(_text(fill, "trade_id"))
            index += 2
        elif planned_quantity != "0" or dict(plan)["side"] is not None:
            raise ValueError("native fact stream is invalid")
        executions.append(CollectedExecution(plan, planned_quantity, order, fill))

    stopped = _document(facts[-1], "stopped", ("state",))
    if dict(stopped)["state"] != "COMPLETED" or index != len(facts) - 1:
        raise ValueError("native fact stream is invalid")
    if (
        tuple(target_ids) != run.processed_target_ids
        or tuple(native_order_ids) != run.native_order_ids
        or tuple(native_fill_ids) != run.native_fill_ids
        or len(native_order_ids) != run.order_count
        or len(native_fill_ids) != run.fill_count
        or len(set(native_order_ids)) != len(native_order_ids)
        or len(set(native_fill_ids)) != len(native_fill_ids)
    ):
        raise ValueError("native fact stream is invalid")
    return tuple(executions)


__all__ = ["CollectedExecution", "collect_executions"]
