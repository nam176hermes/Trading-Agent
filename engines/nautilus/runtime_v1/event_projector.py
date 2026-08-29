"""Project scalar Nautilus evidence into the closed P1 event stream."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
from uuid import UUID, uuid5

from .event_collector import CollectedExecution, collect_executions
from .generated_protocol import (
    DOCUMENT_FIELDS,
    P1_EVENT_SCHEMA,
    canonical_json_bytes,
    validate_document,
)


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
        return balance_values["USDT"], commission_values["USDT"]
    except KeyError as exc:
        raise ValueError("P1 completion authority is not observation-bound") from exc


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


def _schedule(inputs: object) -> dict[str, tuple[tuple[str, ...], str, str]]:
    schedule = dict(inputs.target_schedule)
    targets = schedule.get("targets")
    if type(targets) is not tuple:
        raise ValueError("P1 target schedule is invalid")
    result: dict[str, tuple[tuple[str, ...], str, str]] = {}
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
        weight = dict(positions[0]).get("target_weight")
        if not all(
            type(item) is str and item for item in (target_id, effective_at, weight)
        ):
            raise ValueError("P1 target schedule is invalid")
        if target_id in result or any(type(item) is not str for item in signals):
            raise ValueError("P1 target schedule is invalid")
        result[target_id] = (signals, effective_at, weight)
    return result


def _target_events(
    execution: CollectedExecution,
    schedule: dict[str, tuple[tuple[str, ...], str, str]],
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
    if execution.fill is None:
        raise ValueError("P1 native order is missing its fill")
    order = _values(execution.order)
    fill = _values(execution.fill)
    events.extend(
        (
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
            ),
            _event(
                "Fill",
                sequence + 3,
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
            ),
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
        "message_id": str(
            uuid5(
                UUID(request.message_id), f"{P1_EVENT_SCHEMA}:{sequence}:{event_type}"
            )
        ),
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
    events: tuple[dict[str, object], ...], envelopes: tuple[dict[str, object], ...]
) -> None:
    """Fail closed on an incomplete or internally inconsistent stream."""

    if (
        not 5 <= len(events) == len(envelopes) <= 4096
        or events[0]["event_type"] != "RunStarted"
        or events[-1]["event_type"] != "RunCompleted"
    ):
        raise ValueError("P1 event stream lifecycle is invalid")
    targets: dict[str, dict[str, object]] = {}
    plans: dict[str, dict[str, object]] = {}
    orders: dict[str, dict[str, object]] = {}
    native_orders: set[str] = set()
    fills: set[str] = set()
    filled_orders: set[str] = set()
    message_ids: set[object] = set()
    previous_time = ""
    authority_fields = (
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
    for sequence, (event, envelope) in enumerate(
        zip(events, envelopes, strict=True), start=2
    ):
        validate_document(str(event.get("event_type")), event)
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
                envelope.get(name) != envelopes[0].get(name)
                for name in authority_fields
            )
            or envelope.get("message_id") in message_ids
        ):
            raise ValueError("P1 event envelope authority is invalid")
        message_ids.add(envelope.get("message_id"))
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
                or client_order_id in orders
                or native_order_id in native_orders
            ):
                raise ValueError("P1 order is not target-bound")
            orders[client_order_id] = event
            native_orders.add(native_order_id)
        elif kind == "Fill":
            client_order_id = str(event["client_order_id"])
            native_fill_id = str(event["native_fill_id"])
            order = orders.get(client_order_id)
            if (
                order is None
                or order["side"] != event["side"]
                or order["quantity"] != event["quantity"]
                or client_order_id in filled_orders
                or native_fill_id in fills
            ):
                raise ValueError("P1 fill is not order-bound")
            fills.add(native_fill_id)
            filled_orders.add(client_order_id)
    completion = events[-1]
    if (
        set(targets) != set(plans)
        or events[-3]["event_type"] != "PositionObserved"
        or events[-2]["event_type"] != "AccountObserved"
        or completion["target_count"] != len(targets)
        or completion["order_count"] != len(orders)
        or completion["fill_count"] != len(fills)
        or set(orders) != filled_orders
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
    for execution in collect_executions(run):
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
    validate_projected_stream(complete, envelopes)
    raw = b"".join(canonical_json_bytes(envelope) + b"\n" for envelope in envelopes)
    return ProjectedEventStream(
        complete,
        envelopes,
        raw,
        hashlib.sha256(raw).hexdigest(),
        str(completion_event["semantic_digest"]),
    )


__all__ = [
    "CompletionAuthority",
    "ProjectedEventStream",
    "project_event_stream",
    "validate_projected_stream",
]
