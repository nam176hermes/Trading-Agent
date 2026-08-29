"""Project scalar Nautilus evidence into the closed P1 event stream."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
import hashlib
import hmac
import json
from uuid import UUID, uuid5

from .event_collector import CollectedExecution, collect_executions
from .currency_metadata import currency_quanta
from .generated_protocol import (
    DOCUMENT_FIELDS,
    P1_EVENT_SCHEMA,
    canonical_json_bytes,
    validate_document,
)
from .target_planner import plan_target


_UPSTREAM = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
_FAMILY = {
    "RunStarted": "ENGINE_LIFECYCLE",
    "TargetAccepted": "STRATEGY_LIFECYCLE",
    "TargetQuantityPlanned": "STRATEGY_LIFECYCLE",
    "OrderSubmitted": "ORDER_LIFECYCLE",
    "Fill": "FILLS",
    "PositionObserved": "POSITIONS",
    "AccountObserved": "ACCOUNT_STATE",
    "RunCompleted": "ENGINE_LIFECYCLE",
}
_CUSTODY_ONLY = {"native_fill_id", "native_order_id", "semantic_digest"}
_AUTHORITY_FIELDS = (
    "correlation_id",
    "causation_id",
    "engine_run_id",
    "event_time",
    "initialization_time",
    "schema_version",
    "producer_identity",
    "source_commit",
    "config_digest",
)
_ENVELOPE_FIELDS = {
    "message_id",
    "correlation_id",
    "causation_id",
    "engine_run_id",
    "stream_sequence",
    "event_time",
    "initialization_time",
    "schema_version",
    "producer_identity",
    "source_commit",
    "config_digest",
    "payload_digest",
    "payload",
}
_MARKET_ROW_FIELDS = {
    "ask",
    "bid",
    "close",
    "event_time",
    "high",
    "low",
    "open",
    "quote_time",
    "sequence",
    "volume",
}


@dataclass(frozen=True, slots=True)
class CompletionAuthority:
    target_count: int
    order_count: int
    fill_count: int
    final_cash: str
    final_position: str
    fees: str
    realized_pnl: str
    unrealized_pnl: str


@dataclass(frozen=True, slots=True)
class ProjectedEventStream:
    events: tuple[dict[str, object], ...]
    envelopes: tuple[dict[str, object], ...]
    jsonl: bytes
    raw_sha256: str
    semantic_sha256: str
    request_message_id: str
    request_authority: tuple[tuple[str, object], ...]


def _instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except (AttributeError, ValueError) as exc:
        raise ValueError("P1 event time is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("P1 event time is invalid")
    parsed = parsed.astimezone(UTC)
    rendered = parsed.isoformat().replace("+00:00", "Z")
    if rendered != value:
        raise ValueError("P1 event time is invalid")
    return parsed


def _time(value: str) -> str:
    return _instant(value).isoformat().replace("+00:00", "Z")


def _native_time(value: object) -> str:
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ValueError("P1 native event time is invalid")
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    if nanoseconds % 1_000:
        raise ValueError("P1 native event time is not microsecond-exact")
    return (
        datetime.fromtimestamp(seconds, UTC)
        .replace(microsecond=nanoseconds // 1_000)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _decimal(value: object) -> str:
    try:
        number = Decimal(str(value))
    except ArithmeticError as exc:
        raise ValueError("P1 decimal is invalid") from exc
    if not number.is_finite():
        raise ValueError("P1 decimal is invalid")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _request_authority(request: object) -> tuple[tuple[str, object], ...]:
    return (
        ("correlation_id", request.correlation_id),
        ("causation_id", request.causation_id),
        ("engine_run_id", request.engine_run_id),
        ("event_time", _time(request.event_time)),
        ("initialization_time", _time(request.initialization_time)),
        ("schema_version", request.schema_version),
        ("producer_identity", request.producer_identity),
        ("source_commit", request.source_commit),
        ("config_digest", request.config_digest),
    )


def _expected_message_id(
    request_message_id: str, sequence: object, event_type: object
) -> str:
    try:
        namespace = UUID(request_message_id)
    except (AttributeError, ValueError) as exc:
        raise ValueError("P1 event envelope authority is invalid") from exc
    if str(namespace) != request_message_id:
        raise ValueError("P1 event envelope authority is invalid")
    return str(uuid5(namespace, f"{P1_EVENT_SCHEMA}:{sequence}:{event_type}"))


def _observed_usdt(run: object) -> tuple[str, str]:
    balances = run.balance_facts
    commissions = run.commission_facts
    if type(balances) is not tuple or type(commissions) is not tuple:
        raise ValueError("P1 completion authority is not observation-bound")
    balance_values: dict[str, str] = {}
    for fact in balances:
        if (
            type(fact) is not tuple
            or len(fact) != 4
            or any(type(value) is not str for value in fact)
        ):
            raise ValueError("P1 completion authority is not observation-bound")
        currency, total, locked, free = fact
        if currency in balance_values or any(
            _decimal(value) != value for value in (total, locked, free)
        ):
            raise ValueError("P1 completion authority is not observation-bound")
        with localcontext() as context:
            context.prec = 96
            if (
                Decimal(total) < 0
                or Decimal(locked) < 0
                or Decimal(free) < 0
                or Decimal(total) != Decimal(locked) + Decimal(free)
            ):
                raise ValueError("P1 completion authority is not observation-bound")
        balance_values[currency] = total
    commission_values: dict[str, str] = {}
    for fact in commissions:
        if (
            type(fact) is not tuple
            or len(fact) != 2
            or any(type(value) is not str for value in fact)
        ):
            raise ValueError("P1 completion authority is not observation-bound")
        currency, amount = fact
        if (
            currency in commission_values
            or _decimal(amount) != amount
            or Decimal(amount) < 0
        ):
            raise ValueError("P1 completion authority is not observation-bound")
        commission_values[currency] = amount
    try:
        final_cash = balance_values["USDT"]
    except KeyError as exc:
        raise ValueError("P1 completion authority is not observation-bound") from exc
    if not commission_values and run.order_count == 0 and run.fill_count == 0:
        return final_cash, "0"
    if set(commission_values) != {"USDT"}:
        raise ValueError("P1 completion authority is not observation-bound")
    return final_cash, commission_values["USDT"]


def _validate_completion(run: object, completion: CompletionAuthority) -> None:
    final_cash, fees = _observed_usdt(run)
    counts = (
        completion.target_count,
        completion.order_count,
        completion.fill_count,
    )
    scalar_values = (
        completion.final_cash,
        completion.final_position,
        completion.fees,
        completion.realized_pnl,
        completion.unrealized_pnl,
        run.position_quantity,
        run.position_realized_pnl,
        run.position_unrealized_pnl,
    )
    if (
        any(type(value) is not int or value < 0 for value in counts)
        or any(
            type(value) is not str or _decimal(value) != value
            for value in scalar_values
        )
        or completion.target_count != len(run.processed_target_ids)
        or completion.order_count != run.order_count
        or completion.fill_count != run.fill_count
        or completion.final_cash != final_cash
        or completion.final_position != run.position_quantity
        or completion.fees != fees
        or completion.realized_pnl != run.position_realized_pnl
        or completion.unrealized_pnl != run.position_unrealized_pnl
    ):
        raise ValueError("P1 completion authority is not observation-bound")


def _event(
    event_type: str, sequence: int, simulation_time: str, **values: object
) -> dict[str, object]:
    event = {
        "schema_version": P1_EVENT_SCHEMA,
        "sequence": sequence,
        "simulation_time": _time(simulation_time),
        "event_type": event_type,
        **values,
    }
    validate_document(event_type, event)
    return event


def _values(fact: tuple[tuple[str, str | int | None], ...]) -> dict[str, object]:
    return dict(fact)


def _signals(values: dict[str, object]) -> tuple[str, ...]:
    count = values["source_signal_count"]
    if type(count) is not int:
        raise ValueError("P1 source signals are invalid")
    return tuple(str(values[f"source_signal_id_{index}"]) for index in range(count))


def _schedule(
    inputs: object,
) -> dict[str, tuple[tuple[str, ...], str, str, str]]:
    schedule = dict(inputs.target_schedule)
    targets = schedule.get("targets")
    if type(targets) is not tuple:
        raise ValueError("P1 target schedule is invalid")
    result: dict[str, tuple[tuple[str, ...], str, str, str]] = {}
    for frozen in targets:
        target = dict(frozen)
        positions = target.get("positions")
        signals = target.get("source_signal_ids")
        if (
            type(positions) is not tuple
            or len(positions) != 1
            or type(signals) is not tuple
        ):
            raise ValueError("P1 target schedule is invalid")
        target_id = target.get("target_id")
        effective_at = target.get("effective_at")
        position = dict(positions[0])
        weight = position.get("target_weight")
        frozen_instrument = position.get("instrument")
        if type(frozen_instrument) is not tuple:
            raise ValueError("P1 target schedule is invalid")
        instrument = dict(frozen_instrument)
        symbol = instrument.get("symbol")
        venue = instrument.get("venue")
        if not all(
            type(item) is str and item
            for item in (target_id, effective_at, weight, symbol, venue)
        ):
            raise ValueError("P1 target schedule is invalid")
        if (
            target_id in result
            or any(type(item) is not str for item in signals)
            or instrument.get("product_type") != "crypto_spot"
        ):
            raise ValueError("P1 target schedule is invalid")
        result[target_id] = (signals, effective_at, weight, f"{symbol}.{venue}")
    return result


def _market_rows(inputs: object) -> dict[str, dict[str, object]]:
    def reject_number(_value: str) -> object:
        raise ValueError("P1 native business facts are not input-bound")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for name, value in items:
            if name in document:
                raise ValueError("P1 native business facts are not input-bound")
            document[name] = value
        return document

    raw = inputs.market_data
    reference = inputs.request.market_data
    if (
        type(raw) is not bytes
        or not raw
        or not raw.endswith(b"\n")
        or raw.endswith(b"\n\n")
        or reference.media_type != "application/jsonl"
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), reference.sha256)
    ):
        raise ValueError("P1 native business facts are not input-bound")
    rows: dict[str, dict[str, object]] = {}
    for line in raw.splitlines(keepends=True):
        try:
            row = json.loads(
                line,
                object_pairs_hook=pairs,
                parse_float=reject_number,
                parse_constant=reject_number,
            )
            if (
                type(row) is not dict
                or set(row) != _MARKET_ROW_FIELDS
                or canonical_json_bytes(row) + b"\n" != line
            ):
                raise ValueError
            quote_time = _time(row["quote_time"])
            values = (row["bid"], row["ask"], row["volume"])
            if (
                quote_time in rows
                or any(
                    type(value) is not str or _decimal(value) != value
                    for value in values
                )
                or Decimal(row["bid"]) <= 0
                or Decimal(row["ask"]) <= 0
                or Decimal(row["volume"]) < 0
            ):
                raise ValueError
        except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("P1 native business facts are not input-bound") from exc
        rows[quote_time] = row
    return rows


def _validate_business_facts(
    inputs: object,
    run: object,
    quotes: tuple[tuple[tuple[str, str | int | None], ...], ...],
    executions: tuple[CollectedExecution, ...],
    schedule: dict[str, tuple[tuple[str, ...], str, str, str]],
    completion: CompletionAuthority,
) -> None:
    try:
        configuration = dict(inputs.engine_configuration)
        catalog = dict(inputs.instrument_catalog)
        rows = _market_rows(inputs)
        instrument_id = catalog["instrument_id"]
        if (
            type(instrument_id) is not str
            or configuration.get("starting_currency") != "USDT"
            or len(rows) != len(quotes)
        ):
            raise ValueError
        starting_balance = Decimal(configuration["starting_balance"])
        fee_rate = Decimal(configuration["fee_rate"])
        tick_size = Decimal(catalog["tick_size"])
        step_size = Decimal(catalog["step_size"])
        min_quantity = Decimal(catalog["min_quantity"])
        min_notional = Decimal(catalog["min_notional"])
        base_quantum, quote_quantum = currency_quanta(
            catalog.get("base_currency"), catalog.get("quote_currency")
        )
        canonical_numbers = (
            (configuration["starting_balance"], starting_balance),
            (configuration["fee_rate"], fee_rate),
            (catalog["tick_size"], tick_size),
            (catalog["step_size"], step_size),
            (catalog["min_quantity"], min_quantity),
            (catalog["min_notional"], min_notional),
        )
        if any(
            type(text) is not str or _decimal(number) != text
            for text, number in canonical_numbers
        ):
            raise ValueError

        cash = starting_balance
        position = Decimal(0)
        average = Decimal(0)
        realized = Decimal(0)
        commissions: dict[str, Decimal] = {}
        used_quote_times: set[str] = set()
        with localcontext() as context:
            context.prec = 96
            for scalar_quote in quotes:
                quote = _values(scalar_quote)
                quote_time = _native_time(quote["ts_event"])
                row = rows.get(quote_time)
                expected_quote = {
                    "instrument_id": instrument_id,
                    "bid": row["bid"] if row is not None else None,
                    "ask": row["ask"] if row is not None else None,
                    "bid_size": row["volume"] if row is not None else None,
                    "ask_size": row["volume"] if row is not None else None,
                }
                if (
                    row is None
                    or quote_time in used_quote_times
                    or any(
                        quote.get(name) != value
                        for name, value in expected_quote.items()
                    )
                ):
                    raise ValueError
                used_quote_times.add(quote_time)
            if used_quote_times != set(rows):
                raise ValueError

            for execution in executions:
                quote = _values(execution.quote)
                row = rows[_native_time(quote["ts_event"])]
                plan = _values(execution.plan)
                target_id = plan.get("target_id")
                scheduled = schedule.get(str(target_id))
                if scheduled is None:
                    raise ValueError
                signals, effective_at, weight, target_instrument_id = scheduled
                expected = plan_target(
                    target_id=str(target_id),
                    source_signal_ids=signals,
                    effective_at=effective_at,
                    target_instrument_id=target_instrument_id,
                    instrument_id=instrument_id,
                    target_weight=Decimal(weight),
                    account_equity=cash + position * Decimal(row["bid"]),
                    available_cash=cash,
                    current_quantity=position,
                    ask_price=Decimal(row["ask"]) + tick_size,
                    fee_rate=fee_rate,
                    step_size=step_size,
                    min_quantity=min_quantity,
                    min_notional=min_notional,
                    leverage=Decimal(1),
                )
                expected_plan: dict[str, object] = {
                    "target_id": expected.target_id,
                    "effective_at": expected.effective_at,
                    "instrument_id": expected.instrument_id,
                    "current_quantity": expected.current_quantity,
                    "target_quantity": expected.target_quantity,
                    "delta": expected.delta,
                    "side": expected.side,
                    "price_basis": expected.price_basis,
                    "notional": expected.notional,
                    "reason": expected.reason,
                }
                if _signals(plan) != expected.source_signal_ids or any(
                    plan.get(name) != value for name, value in expected_plan.items()
                ):
                    raise ValueError

                expected_fills: list[tuple[str, Decimal, Decimal]] = []
                if execution.order is not None:
                    order = _values(execution.order)
                    order_quantity = Decimal(str(order["quantity"]))
                    available_quantity = Decimal(row["volume"])
                    side = order["side"]
                    if side == "BUY":
                        first_price = Decimal(row["ask"])
                        remainder_price = first_price + tick_size
                    elif side == "SELL":
                        first_price = Decimal(row["bid"])
                        remainder_price = first_price - tick_size
                    else:
                        raise ValueError
                    first_quantity = min(order_quantity, available_quantity)
                    if first_quantity > 0:
                        expected_fills.append((side, first_quantity, first_price))
                    remainder_quantity = order_quantity - first_quantity
                    if remainder_quantity > 0:
                        if remainder_price <= 0:
                            raise ValueError
                        expected_fills.append(
                            (side, remainder_quantity, remainder_price)
                        )
                if len(execution.fills) != len(expected_fills):
                    raise ValueError

                for fill_fact, expected_fill in zip(
                    execution.fills, expected_fills, strict=True
                ):
                    fill = _values(fill_fact)
                    quantity = Decimal(str(fill["quantity"]))
                    price_text = fill["price"]
                    price = Decimal(str(price_text))
                    commission = Decimal(str(fill["commission"]))
                    commission_currency = fill["commission_currency"]
                    expected_side, expected_quantity, expected_price = expected_fill
                    expected_commission = (
                        expected_quantity * expected_price * fee_rate
                    ).quantize(quote_quantum)
                    if (
                        type(price_text) is not str
                        or _decimal(price) != price_text
                        or type(commission_currency) is not str
                        or price <= 0
                        or price % tick_size != 0
                        or commission < 0
                        or fill["side"] != expected_side
                        or fill["ts_event"] != quote["ts_event"]
                        or quantity != expected_quantity
                        or price != expected_price
                        or commission != expected_commission
                    ):
                        raise ValueError
                    commissions[commission_currency] = (
                        commissions.get(commission_currency, Decimal(0)) + commission
                    )
                    if fill["side"] == "BUY":
                        new_position = (position + quantity).quantize(
                            base_quantum, rounding=ROUND_HALF_EVEN
                        )
                        average = (
                            position * average + quantity * price
                        ) / new_position
                        position = new_position
                        cash = (cash - quantity * price - commission).quantize(
                            quote_quantum, rounding=ROUND_HALF_EVEN
                        )
                    elif fill["side"] == "SELL":
                        realized += (price - average) * quantity
                        position = (position - quantity).quantize(
                            base_quantum, rounding=ROUND_HALF_EVEN
                        )
                        cash = (cash + quantity * price - commission).quantize(
                            quote_quantum, rounding=ROUND_HALF_EVEN
                        )
                        if position == 0:
                            average = Decimal(0)
                    else:
                        raise ValueError
                    if cash < 0 or position < 0:
                        raise ValueError
            quote_currency = catalog["quote_currency"]
            final_row = next(reversed(rows.values()))
            unrealized = (
                (Decimal(final_row["close"]) - average) * position
            ).quantize(quote_quantum, rounding=ROUND_HALF_EVEN)
            if (
                cash != Decimal(completion.final_cash)
                or position != Decimal(completion.final_position)
                or average.quantize(quote_quantum, rounding=ROUND_HALF_EVEN)
                != Decimal(run.position_average_entry)
                or realized.quantize(quote_quantum, rounding=ROUND_HALF_EVEN)
                != Decimal(completion.realized_pnl)
                or unrealized != Decimal(completion.unrealized_pnl)
                or set(commissions) - {quote_currency}
                or commissions.get(quote_currency, Decimal(0))
                != Decimal(completion.fees)
            ):
                raise ValueError
    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("P1 native business facts are not input-bound") from exc


def _target_events(
    execution: CollectedExecution,
    schedule: dict[str, tuple[tuple[str, ...], str, str, str]],
    sequence: int,
) -> tuple[dict[str, object], ...]:
    plan = _values(execution.plan)
    target_id = str(plan["target_id"])
    signals = _signals(plan)
    scheduled = schedule.get(target_id)
    if scheduled is None or scheduled[:2] != (signals, plan["effective_at"]):
        raise ValueError("P1 native target does not match schedule authority")
    simulation_time = str(plan["effective_at"])
    events = [
        _event(
            "TargetAccepted",
            sequence,
            simulation_time,
            origin="CONTROL_PLANE",
            native_type=None,
            target_id=target_id,
            source_signal_ids=list(signals),
            target_weight=_decimal(scheduled[2]),
        ),
        _event(
            "TargetQuantityPlanned",
            sequence + 1,
            simulation_time,
            origin="CONTROL_PLANE",
            native_type=None,
            target_id=target_id,
            quantity=_decimal(execution.planned_quantity),
        ),
    ]
    if execution.order is None:
        return tuple(events)
    if not execution.fills:
        raise ValueError("P1 native order is missing its fill")
    order = _values(execution.order)
    events.append(
        _event(
            "OrderSubmitted",
            sequence + 2,
            simulation_time,
            origin="CONTROL_PLANE",
            native_type="Order",
            client_order_id=target_id,
            native_order_id=str(order["client_order_id"]),
            target_id=target_id,
            source_signal_ids=list(signals),
            side=str(order["side"]),
            quantity=_decimal(order["quantity"]),
            order_type="MARKET",
        )
    )
    for fill_fact in execution.fills:
        fill = _values(fill_fact)
        events.append(
            _event(
                "Fill",
                sequence + len(events),
                _native_time(fill["ts_event"]),
                origin="NAUTILUS_CALLBACK",
                native_type="OrderFilled",
                client_order_id=target_id,
                native_fill_id=str(fill["trade_id"]),
                side=str(fill["side"]),
                quantity=_decimal(fill["quantity"]),
                price=_decimal(fill["price"]),
                fee=_decimal(fill["commission"]),
                fee_currency="USDT",
            )
        )
    return tuple(events)


def _semantic_digest(events: tuple[dict[str, object], ...]) -> str:
    projection = tuple(
        {key: value for key, value in event.items() if key not in _CUSTODY_ONLY}
        for event in events
    )
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def _payload(event: dict[str, object]) -> dict[str, object]:
    attributes = []
    for name in DOCUMENT_FIELDS[str(event["event_type"])]:
        if name == "event_type" or event[name] is None:
            continue
        value = event[name]
        if type(value) is list:
            value = canonical_json_bytes(value).decode("utf-8")
        if type(value) not in {str, int, bool}:
            raise ValueError("P1 event cannot be represented by the root contract")
        attributes.append({"name": name, "value": value})
    return {
        "event_type": event["event_type"],
        "family": _FAMILY[str(event["event_type"])],
        "attributes": attributes,
    }


def _envelope(request: object, event: dict[str, object]) -> dict[str, object]:
    payload = _payload(event)
    sequence = event["sequence"]
    event_type = event["event_type"]
    return {
        "message_id": _expected_message_id(request.message_id, sequence, event_type),
        "correlation_id": request.correlation_id,
        "causation_id": request.causation_id,
        "engine_run_id": request.engine_run_id,
        "stream_sequence": sequence,
        "event_time": _time(request.event_time),
        "initialization_time": _time(request.initialization_time),
        "schema_version": request.schema_version,
        "producer_identity": request.producer_identity,
        "source_commit": request.source_commit,
        "config_digest": request.config_digest,
        "payload_digest": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        "payload": payload,
    }


def validate_projected_stream(
    events: tuple[dict[str, object], ...],
    envelopes: tuple[dict[str, object], ...],
    request_message_id: str,
    request_authority: tuple[tuple[str, object], ...],
) -> None:
    """Fail closed on an incomplete or internally inconsistent stream."""

    if (
        not 5 <= len(events) == len(envelopes) <= 4096
        or events[0]["event_type"] != "RunStarted"
        or events[-1]["event_type"] != "RunCompleted"
        or sum(event.get("event_type") == "RunStarted" for event in events) != 1
        or sum(event.get("event_type") == "RunCompleted" for event in events) != 1
    ):
        raise ValueError("P1 event stream lifecycle is invalid")
    targets: dict[str, dict[str, object]] = {}
    plans: dict[str, dict[str, object]] = {}
    orders: dict[str, dict[str, object]] = {}
    submitted_targets: set[str] = set()
    native_orders: set[str] = set()
    fills: set[str] = set()
    filled_quantities: dict[str, Decimal] = {}
    previous_time = ""
    if (
        type(request_authority) is not tuple
        or tuple(name for name, _ in request_authority) != _AUTHORITY_FIELDS
    ):
        raise ValueError("P1 event envelope authority is invalid")
    expected_authority = dict(request_authority)
    for sequence, (event, envelope) in enumerate(
        zip(events, envelopes, strict=True), start=2
    ):
        validate_document(str(event.get("event_type")), event)
        if type(envelope) is not dict or set(envelope) != _ENVELOPE_FIELDS:
            raise ValueError("P1 event envelope authority is invalid")
        if event["sequence"] != sequence or envelope["stream_sequence"] != sequence:
            raise ValueError("P1 event sequence is invalid")
        time = str(event["simulation_time"])
        if previous_time and _instant(time) < _instant(previous_time):
            raise ValueError("P1 simulation time regressed")
        previous_time = time
        payload = envelope.get("payload")
        if (
            type(payload) is not dict
            or payload != _payload(event)
            or envelope.get("payload_digest")
            != hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
            or any(
                envelope.get(name) != expected_authority[name]
                for name in _AUTHORITY_FIELDS
            )
            or envelope.get("message_id")
            != _expected_message_id(request_message_id, sequence, event["event_type"])
        ):
            raise ValueError("P1 event envelope authority is invalid")
        kind = event["event_type"]
        if kind == "TargetAccepted":
            target_id = str(event["target_id"])
            if target_id in targets:
                raise ValueError("P1 target was accepted twice")
            targets[target_id] = event
        elif kind == "TargetQuantityPlanned":
            target_id = str(event["target_id"])
            if target_id not in targets or target_id in plans:
                raise ValueError("P1 target plan is invalid")
            plans[target_id] = event
        elif kind == "OrderSubmitted":
            target_id = str(event["target_id"])
            target = targets.get(target_id)
            client_order_id = str(event["client_order_id"])
            native_order_id = str(event["native_order_id"])
            if (
                target is None
                or target_id not in plans
                or target["source_signal_ids"] != event["source_signal_ids"]
                or plans[target_id]["quantity"] != event["quantity"]
                or client_order_id != target_id
                or target_id in submitted_targets
                or client_order_id in orders
                or native_order_id in native_orders
            ):
                raise ValueError("P1 order is not target-bound")
            orders[client_order_id] = event
            submitted_targets.add(target_id)
            native_orders.add(native_order_id)
        elif kind == "Fill":
            client_order_id = str(event["client_order_id"])
            native_fill_id = str(event["native_fill_id"])
            order = orders.get(client_order_id)
            if (
                order is None
                or order["side"] != event["side"]
                or native_fill_id in fills
            ):
                raise ValueError("P1 fill is not order-bound")
            quantity = Decimal(str(event["quantity"]))
            if quantity <= 0:
                raise ValueError("P1 fill is not order-bound")
            fills.add(native_fill_id)
            with localcontext() as context:
                context.prec = 96
                filled_quantities[client_order_id] = (
                    filled_quantities.get(client_order_id, Decimal(0)) + quantity
                )
    completion = events[-1]
    if (
        set(targets) != set(plans)
        or events[-3]["event_type"] != "PositionObserved"
        or events[-2]["event_type"] != "AccountObserved"
        or completion["target_count"] != len(targets)
        or completion["order_count"] != len(orders)
        or completion["fill_count"] != len(fills)
        or set(orders) != set(filled_quantities)
        or any(
            filled_quantities[order_id] != Decimal(str(order["quantity"]))
            for order_id, order in orders.items()
        )
        or completion["upstream_commit"] != events[0]["upstream_commit"]
        or completion["closure_digest"] != events[0]["closure_digest"]
        or completion["final_position"] != events[-3]["quantity"]
        or completion["final_cash"] != events[-2]["cash_balance"]
        or completion["fees"] != events[-2]["fees"]
        or completion["realized_pnl"] != events[-2]["realized_pnl"]
        or completion["unrealized_pnl"] != events[-2]["unrealized_pnl"]
        or events[-3]["realized_pnl"] != events[-2]["realized_pnl"]
        or events[-3]["unrealized_pnl"] != events[-2]["unrealized_pnl"]
        or completion["semantic_digest"] != _semantic_digest(events)
    ):
        raise ValueError("P1 completion authority is inconsistent")


def project_event_stream(
    inputs: object,
    run: object,
    completion: CompletionAuthority,
    *,
    closure_digest: str,
    upstream_commit: str,
) -> ProjectedEventStream:
    """Build and validate one complete exact-authority event stream."""

    if type(completion) is not CompletionAuthority or upstream_commit != _UPSTREAM:
        raise ValueError("P1 completion or engine authority is invalid")
    _validate_completion(run, completion)
    request = inputs.request
    request_authority = _request_authority(request)
    events = [
        _event(
            "RunStarted",
            2,
            request.start_time,
            origin="CONTROL_PLANE",
            native_type=None,
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit=upstream_commit,
            closure_digest=closure_digest,
            config_digest=request.config_digest,
            catalog_digest=request.instrument_catalog.sha256,
            data_digest=request.market_data.sha256,
        )
    ]
    schedule = _schedule(inputs)
    quotes, executions = collect_executions(run)
    schedule_target_ids = tuple(schedule)
    execution_target_ids = tuple(
        str(_values(execution.plan)["target_id"]) for execution in executions
    )
    if (
        execution_target_ids != schedule_target_ids
        or run.processed_target_ids != schedule_target_ids
    ):
        raise ValueError("P1 native targets do not match schedule authority")
    _validate_business_facts(inputs, run, quotes, executions, schedule, completion)
    for execution in executions:
        events.extend(_target_events(execution, schedule, len(events) + 2))
    final_time = _native_time(run.last_market_timestamp)
    events.extend(
        (
            _event(
                "PositionObserved",
                len(events) + 2,
                final_time,
                origin="NAUTILUS_CACHE_OBSERVATION",
                native_type="Position",
                quantity=_decimal(completion.final_position),
                average_entry_price=_decimal(run.position_average_entry),
                realized_pnl=_decimal(completion.realized_pnl),
                unrealized_pnl=_decimal(completion.unrealized_pnl),
            ),
            _event(
                "AccountObserved",
                len(events) + 3,
                final_time,
                origin="NAUTILUS_CACHE_OBSERVATION",
                native_type="Account",
                cash_balance=_decimal(completion.final_cash),
                fees=_decimal(completion.fees),
                realized_pnl=_decimal(completion.realized_pnl),
                unrealized_pnl=_decimal(completion.unrealized_pnl),
            ),
        )
    )
    completion_event = _event(
        "RunCompleted",
        len(events) + 2,
        final_time,
        origin="CONTROL_PLANE",
        native_type=None,
        runtime_family="cython-v1",
        engine_version="1.231.0",
        upstream_commit=upstream_commit,
        closure_digest=closure_digest,
        target_count=completion.target_count,
        order_count=completion.order_count,
        fill_count=completion.fill_count,
        final_cash=_decimal(completion.final_cash),
        final_position=_decimal(completion.final_position),
        fees=_decimal(completion.fees),
        realized_pnl=_decimal(completion.realized_pnl),
        unrealized_pnl=_decimal(completion.unrealized_pnl),
        semantic_digest="0" * 64,
    )
    complete = tuple(events) + (completion_event,)
    completion_event["semantic_digest"] = _semantic_digest(complete)
    validate_document("RunCompleted", completion_event)
    envelopes = tuple(_envelope(request, event) for event in complete)
    validate_projected_stream(
        complete, envelopes, request.message_id, request_authority
    )
    raw = b"".join(canonical_json_bytes(envelope) + b"\n" for envelope in envelopes)
    return ProjectedEventStream(
        events=complete,
        envelopes=envelopes,
        jsonl=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        semantic_sha256=str(completion_event["semantic_digest"]),
        request_message_id=request.message_id,
        request_authority=request_authority,
    )


__all__ = [
    "CompletionAuthority",
    "ProjectedEventStream",
    "project_event_stream",
    "validate_projected_stream",
]
