from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from uuid import UUID, uuid5

import pytest

from engines.nautilus.runtime_v1.event_projector import (
    CompletionAuthority,
    project_event_stream,
)
from engines.nautilus.runtime_v1.event_collector import collect_executions
from engines.nautilus.runtime_v1.input_loader import (
    ArtifactReference,
    RunBacktestRequest,
    RuntimeInputs,
)
from engines.nautilus.runtime_v1.jsonl_writer import write_jsonl
from packages.engine_contracts import EngineEventEnvelope, canonical_json_bytes
from packages.nautilus_runtime_contracts.events import P1_EVENT_ADAPTER
from packages.nautilus_runtime_contracts.semantic import semantic_digest
from packages.nautilus_runtime_contracts.state_machine import validate_event_stream


@dataclass(frozen=True, slots=True)
class Fact:
    kind: str
    attributes: tuple[tuple[str, str | int | None], ...]


def _signals(value: str) -> tuple[tuple[str, str | int], ...]:
    return (("source_signal_count", 1), ("source_signal_id_0", value))


def _facts(first_bid: str = "99") -> tuple[Fact, ...]:
    first_target = "11111111-1111-4111-8111-111111111111"
    second_target = "44444444-4444-4444-8444-444444444444"
    first_signal = "22222222-2222-4222-8222-222222222222"
    second_signal = "33333333-3333-4333-8333-333333333333"
    return (
        Fact(
            "quote",
            (
                ("instrument_id", "BTCUSDT.BINANCE"),
                ("bid", first_bid),
                ("ask", "100"),
                ("bid_size", "1000000"),
                ("ask_size", "1000000"),
                ("ts_event", 1785931200000000000),
            ),
        ),
        Fact(
            "target_planned",
            (
                ("target_id", first_target),
                *_signals(first_signal),
                ("effective_at", "2026-08-05T12:00:00Z"),
                ("instrument_id", "BTCUSDT.BINANCE"),
                ("current_quantity", "0"),
                ("target_quantity", "9989.011088"),
                ("delta", "9989.011088"),
                ("side", "BUY"),
                ("price_basis", "100.01"),
                ("notional", "999000.99891088"),
                ("reason", "ORDER"),
            ),
        ),
        Fact("target_quantity_planned", (("quantity", "9989.011088"),)),
        Fact(
            "order_submitted",
            (
                ("client_order_id", "O-1"),
                ("target_id", first_target),
                *_signals(first_signal),
                ("side", "BUY"),
                ("quantity", "9989.011088"),
                ("order_type", "MARKET"),
            ),
        ),
        Fact(
            "order_filled",
            (
                ("client_order_id", "O-1"),
                ("trade_id", "T-1"),
                ("side", "BUY"),
                ("quantity", "9989.011088"),
                ("price", "100"),
                ("commission", "998.901109"),
                ("commission_currency", "USDT"),
                ("ts_event", 1785931200000000000),
            ),
        ),
        Fact(
            "quote",
            (
                ("instrument_id", "BTCUSDT.BINANCE"),
                ("bid", "101"),
                ("ask", "102"),
                ("bid_size", "1000000"),
                ("ask_size", "1000000"),
                ("ts_event", 1785931260000000000),
            ),
        ),
        Fact(
            "target_planned",
            (
                ("target_id", second_target),
                *_signals(second_signal),
                ("effective_at", "2026-08-05T12:01:00Z"),
                ("instrument_id", "BTCUSDT.BINANCE"),
                ("current_quantity", "9989.011088"),
                ("target_quantity", "0"),
                ("delta", "-9989.011088"),
                ("side", "SELL"),
                ("price_basis", "102.01"),
                ("notional", "1018979.02108688"),
                ("reason", "ORDER"),
            ),
        ),
        Fact("target_quantity_planned", (("quantity", "9989.011088"),)),
        Fact(
            "order_submitted",
            (
                ("client_order_id", "O-2"),
                ("target_id", second_target),
                *_signals(second_signal),
                ("side", "SELL"),
                ("quantity", "9989.011088"),
                ("order_type", "MARKET"),
            ),
        ),
        Fact(
            "order_filled",
            (
                ("client_order_id", "O-2"),
                ("trade_id", "T-2"),
                ("side", "SELL"),
                ("quantity", "9989.011088"),
                ("price", "101"),
                ("commission", "1008.89012"),
                ("commission_currency", "USDT"),
                ("ts_event", 1785931260000000000),
            ),
        ),
        Fact("stopped", (("state", "COMPLETED"),)),
    )


def _freeze(value: object) -> object:
    if type(value) is dict:
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _market_data(first_bid: str = "99") -> bytes:
    rows = (
        {
            "ask": "100",
            "bid": first_bid,
            "close": "100",
            "event_time": "2026-08-05T12:00:00Z",
            "high": "101",
            "low": "98",
            "open": "99",
            "quote_time": "2026-08-05T12:00:00Z",
            "sequence": 1,
            "volume": "1000000",
        },
        {
            "ask": "102",
            "bid": "101",
            "close": "102",
            "event_time": "2026-08-05T12:01:00Z",
            "high": "103",
            "low": "100",
            "open": "101",
            "quote_time": "2026-08-05T12:01:00Z",
            "sequence": 2,
            "volume": "1000000",
        },
    )
    return b"".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        for row in rows
    )


def _inputs(
    *,
    suffix: str = "a",
    event_time: str = "2026-08-05T12:00:00Z",
    first_weight: str = "1",
    market_data: bytes | None = None,
    second_weight: str = "0",
    target_count: int = 2,
) -> RuntimeInputs:
    market_data = _market_data() if market_data is None else market_data
    reference = lambda identity, digest, media: ArtifactReference(
        identity, digest, media
    )
    request = RunBacktestRequest(
        message_id=f"{suffix * 8}-{suffix * 4}-4{suffix * 3}-8{suffix * 3}-{suffix * 12}",
        correlation_id=f"{suffix * 8}-{suffix * 4}-4{suffix * 3}-9{suffix * 3}-{suffix * 12}",
        causation_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        engine_run_id=f"{suffix * 8}-{suffix * 4}-4{suffix * 3}-a{suffix * 3}-{suffix * 12}",
        stream_sequence=1,
        event_time=event_time,
        initialization_time="2026-08-05T11:59:00Z",
        schema_version="1.0.0",
        producer_identity="worker-authority-1",
        source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest="1" * 64,
        payload_digest="2" * 64,
        command_type="RunBacktest",
        engine_configuration=reference(
            "11111111-1111-4111-8111-111111111111", "3" * 64, "application/json"
        ),
        instrument_catalog=reference(
            "22222222-2222-4222-8222-222222222222", "4" * 64, "application/json"
        ),
        strategy_configuration=reference(
            "33333333-3333-4333-8333-333333333333", "5" * 64, "application/json"
        ),
        market_data=reference(
            "44444444-4444-4444-8444-444444444444",
            sha256(market_data).hexdigest(),
            "application/jsonl",
        ),
        start_time="2026-08-05T12:00:00Z",
        end_time="2026-08-05T12:01:00Z",
    )
    schedule_document = {
        "schema_version": "nautilus-p1-target-schedule-v1",
        "targets": [
            {
                "effective_at": "2026-08-05T12:00:00Z",
                "positions": [
                    {
                        "instrument": {
                            "product_type": "crypto_spot",
                            "symbol": "BTCUSDT",
                            "venue": "BINANCE",
                        },
                        "target_weight": first_weight,
                    }
                ],
                "schema_version": "1.0.0",
                "source_signal_ids": ["22222222-2222-4222-8222-222222222222"],
                "target_id": "11111111-1111-4111-8111-111111111111",
            },
            {
                "effective_at": "2026-08-05T12:01:00Z",
                "positions": [
                    {
                        "instrument": {
                            "product_type": "crypto_spot",
                            "symbol": "BTCUSDT",
                            "venue": "BINANCE",
                        },
                        "target_weight": second_weight,
                    }
                ],
                "schema_version": "1.0.0",
                "source_signal_ids": ["33333333-3333-4333-8333-333333333333"],
                "target_id": "44444444-4444-4444-8444-444444444444",
            },
        ],
    }
    schedule = _freeze(
        {
            **schedule_document,
            "targets": schedule_document["targets"][:target_count],
        }
    )
    configuration = (
        ("fee_rate", "0.001"),
        ("starting_balance", "1000000"),
        ("starting_currency", "USDT"),
    )
    catalog = (
        ("instrument_id", "BTCUSDT.BINANCE"),
        ("min_notional", "10"),
        ("min_quantity", "0.000001"),
        ("step_size", "0.000001"),
        ("tick_size", "0.01"),
    )
    return RuntimeInputs(request, configuration, catalog, schedule, market_data)


def _run(
    *,
    facts: tuple[Fact, ...] | None = None,
    balance_facts: tuple[tuple[str, ...], ...] = (
        ("BTC", "0", "0", "0"),
        ("USDT", "1007981.219859", "0", "1007981.219859"),
    ),
    commission_facts: tuple[tuple[str, ...], ...] = (("USDT", "2007.791229"),),
) -> SimpleNamespace:
    return SimpleNamespace(
        engine_version="1.231.0",
        strategy_state="COMPLETED",
        processed_target_ids=(
            "11111111-1111-4111-8111-111111111111",
            "44444444-4444-4444-8444-444444444444",
        ),
        pending_order_ids=(),
        rejected_order_ids=(),
        native_order_ids=("O-1", "O-2"),
        native_fill_ids=("T-1", "T-2"),
        order_count=2,
        fill_count=2,
        native_facts=facts or _facts(),
        balance_facts=balance_facts,
        commission_facts=commission_facts,
        position_quantity="0",
        position_average_entry="0",
        position_realized_pnl="9989.011088",
        position_unrealized_pnl="0",
        last_market_timestamp=1785931260000000000,
    )


def _partial_fill_run() -> SimpleNamespace:
    facts = list(_facts())
    first = facts[4]
    first_values = dict(first.attributes)
    first_values.update(trade_id="T-1a", quantity="2", commission="0.2")
    second_values = dict(first.attributes)
    second_values.update(
        trade_id="T-1b", quantity="9987.011088", commission="998.701109"
    )
    facts[4] = replace(first, attributes=tuple(first_values.items()))
    facts.insert(5, replace(first, attributes=tuple(second_values.items())))
    run = _run(facts=tuple(facts))
    return SimpleNamespace(
        **{
            **vars(run),
            "native_fill_ids": ("T-1a", "T-1b", "T-2"),
            "fill_count": 3,
        }
    )


AUTHORITY = CompletionAuthority(
    target_count=2,
    order_count=2,
    fill_count=2,
    final_cash="1007981.219859",
    final_position="0",
    fees="2007.791229",
    realized_pnl="9989.011088",
    unrealized_pnl="0",
)


def _project(inputs: RuntimeInputs | None = None, run: object | None = None):
    return project_event_stream(
        inputs or _inputs(),
        run or _run(),
        AUTHORITY,
        closure_digest="a" * 64,
        upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )


def _typed_events(stream) -> tuple:
    return tuple(
        P1_EVENT_ADAPTER.validate_json(canonical_json_bytes(event))
        for event in stream.events
    )


def _attributes(envelope: dict[str, object]) -> dict[str, object]:
    payload = envelope["payload"]
    assert type(payload) is dict
    attributes = payload["attributes"]
    assert type(attributes) is list
    return {str(item["name"]): item["value"] for item in attributes}


def test_partial_fills_are_projected_once_in_observed_order() -> None:
    run = _partial_fill_run()
    _, executions = collect_executions(run)

    assert [dict(fill)["trade_id"] for fill in executions[0].fills] == [
        "T-1a",
        "T-1b",
    ]
    completion = replace(AUTHORITY, fill_count=3)
    stream = project_event_stream(
        _inputs(),
        run,
        completion,
        closure_digest="a" * 64,
        upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )

    assert [
        event["native_fill_id"]
        for event in stream.events
        if event["event_type"] == "Fill"
    ] == ["T-1a", "T-1b", "T-2"]


@pytest.mark.parametrize("mutation", ("dropped", "duplicated", "under", "over"))
def test_partial_fill_sum_and_identity_mutations_fail_closed(mutation: str) -> None:
    run = _partial_fill_run()
    facts = list(run.native_facts)
    fill_indexes = [index for index, fact in enumerate(facts) if fact.kind == "order_filled"]
    if mutation == "dropped":
        facts.pop(fill_indexes[1])
    elif mutation == "duplicated":
        facts.insert(fill_indexes[1], facts[fill_indexes[0]])
    else:
        fact = facts[fill_indexes[1]]
        values = dict(fact.attributes)
        values["quantity"] = "9987.011087" if mutation == "under" else "9987.011089"
        facts[fill_indexes[1]] = replace(fact, attributes=tuple(values.items()))
    mutated = SimpleNamespace(**{**vars(run), "native_facts": tuple(facts)})

    with pytest.raises(ValueError, match="native fact stream"):
        collect_executions(mutated)


def _rebuild_stream(
    stream: object,
    events: tuple[dict[str, object], ...],
    source_envelopes: tuple[dict[str, object], ...] | None = None,
) -> object:
    rebuilt_events = tuple(
        {**event, "sequence": sequence}
        for sequence, event in enumerate(events, start=2)
    )
    semantic = sha256(
        canonical_json_bytes(
            tuple(
                {
                    key: value
                    for key, value in event.items()
                    if key
                    not in {"native_fill_id", "native_order_id", "semantic_digest"}
                }
                for event in rebuilt_events
            )
        )
    ).hexdigest()
    rebuilt_events = rebuilt_events[:-1] + (
        {**rebuilt_events[-1], "semantic_digest": semantic},
    )
    sources = source_envelopes or stream.envelopes
    envelopes = []
    for event, source in zip(rebuilt_events, sources, strict=True):
        source_payload = source["payload"]
        assert type(source_payload) is dict
        source_attributes = source_payload["attributes"]
        assert type(source_attributes) is list
        attributes = []
        for attribute in source_attributes:
            name = str(attribute["name"])
            value = event[name]
            if type(value) is list:
                value = canonical_json_bytes(value).decode()
            attributes.append({"name": name, "value": value})
        payload = {**source_payload, "attributes": attributes}
        sequence = event["sequence"]
        envelopes.append(
            {
                **source,
                "message_id": str(
                    uuid5(
                        UUID(stream.request_message_id),
                        f"nautilus-p1-event-stream-v1:{sequence}:{event['event_type']}",
                    )
                ),
                "stream_sequence": sequence,
                "payload_digest": sha256(canonical_json_bytes(payload)).hexdigest(),
                "payload": payload,
            }
        )
    raw = b"".join(canonical_json_bytes(envelope) + b"\n" for envelope in envelopes)
    return replace(
        stream,
        events=rebuilt_events,
        envelopes=tuple(envelopes),
        jsonl=raw,
        raw_sha256=sha256(raw).hexdigest(),
        semantic_sha256=semantic,
    )


def test_projects_complete_request_bound_stream_without_native_objects() -> None:
    stream = _project()
    typed = _typed_events(stream)

    assert validate_event_stream(typed) == typed
    assert tuple(event.sequence for event in typed) == tuple(range(2, 14))
    assert typed[-1].event_type == "RunCompleted"
    assert stream.semantic_sha256 == typed[-1].semantic_digest
    assert stream.raw_sha256 == sha256(stream.jsonl).hexdigest()
    assert len(stream.events) == len(stream.envelopes) <= 4096
    assert all(type(item) is bytes for item in stream.jsonl.splitlines(keepends=True))
    assert all(line.endswith(b"\n") for line in stream.jsonl.splitlines(keepends=True))

    planned = [event for event in typed if event.event_type == "TargetQuantityPlanned"]
    assert [str(event.quantity) for event in planned] == ["9989.011088", "9989.011088"]
    submitted = [event for event in typed if event.event_type == "OrderSubmitted"]
    fills = [event for event in typed if event.event_type == "Fill"]
    assert [(event.client_order_id, event.native_order_id) for event in submitted] == [
        ("11111111-1111-4111-8111-111111111111", "O-1"),
        ("44444444-4444-4444-8444-444444444444", "O-2"),
    ]
    assert [(event.client_order_id, event.native_fill_id) for event in fills] == [
        ("11111111-1111-4111-8111-111111111111", "T-1"),
        ("44444444-4444-4444-8444-444444444444", "T-2"),
    ]
    for expected, envelope in enumerate(stream.envelopes, start=2):
        parsed = EngineEventEnvelope.model_validate_json(canonical_json_bytes(envelope))
        assert parsed.stream_sequence == expected
        assert str(parsed.causation_id) == _inputs().request.causation_id
        assert (
            parsed.payload_digest
            == sha256(canonical_json_bytes(parsed.payload)).hexdigest()
        )
        assert _attributes(envelope)["sequence"] == expected
    assert _attributes(stream.envelopes[4])["origin"] == "NAUTILUS_CALLBACK"
    assert _attributes(stream.envelopes[-3])["origin"] == "NAUTILUS_CACHE_OBSERVATION"


def test_raw_custody_changes_but_semantics_change_only_for_business_facts() -> None:
    first = _project()
    changed_custody = _project(_inputs(suffix="b", event_time="2026-08-05T12:02:00Z"))
    third_custody = _project(_inputs(suffix="c", event_time="2026-08-05T12:03:00Z"))
    assert (
        len({first.raw_sha256, changed_custody.raw_sha256, third_custody.raw_sha256})
        == 3
    )
    assert (
        first.semantic_sha256
        == changed_custody.semantic_sha256
        == third_custody.semantic_sha256
    )

    changed_quote = _project(
        _inputs(market_data=_market_data("98")), _run(facts=_facts("98"))
    )
    assert first.semantic_sha256 != changed_quote.semantic_sha256

    fee_facts = list(_facts())
    fill = fee_facts[4]
    fee_facts[4] = replace(
        fill,
        attributes=tuple(
            (name, "1" if name == "commission" else value)
            for name, value in fill.attributes
        ),
    )
    changed_fee = replace(AUTHORITY, fees="1019.98101898")
    fee_stream = project_event_stream(
        _inputs(),
        _run(
            facts=tuple(fee_facts),
            commission_facts=(("USDT", "1019.98101898"),),
        ),
        changed_fee,
        closure_digest="a" * 64,
        upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )
    assert first.semantic_sha256 != fee_stream.semantic_sha256

    typed = _typed_events(first)
    target_index = next(
        index
        for index, event in enumerate(typed)
        if event.event_type == "TargetAccepted"
    )
    changed_target = (
        typed[:target_index]
        + (typed[target_index].model_copy(update={"target_weight": Decimal("0.1")}),)
        + typed[target_index + 1 :]
    )
    assert semantic_digest(changed_target) != first.semantic_sha256
    fill_index = next(
        index for index, event in enumerate(typed) if event.event_type == "Fill"
    )
    changed_fill = (
        typed[:fill_index]
        + (
            typed[fill_index].model_copy(
                update={"quantity": typed[fill_index].quantity + 1}
            ),
        )
        + typed[fill_index + 1 :]
    )
    assert semantic_digest(changed_fill) != first.semantic_sha256
    changed_order = typed[:1] + typed[5:9] + typed[1:5] + typed[9:]
    assert semantic_digest(changed_order) != first.semantic_sha256
    custody_only = tuple(
        event.model_copy(update={"native_order_id": "changed-order"})
        if event.event_type == "OrderSubmitted"
        else event.model_copy(update={"native_fill_id": "changed-fill"})
        if event.event_type == "Fill"
        else event
        for event in typed
    )
    assert semantic_digest(custody_only) == first.semantic_sha256


def test_writer_never_exposes_completion_after_partial_failure() -> None:
    stream = _project()
    output = BytesIO()
    completion = stream.jsonl.splitlines(keepends=True)[-1]

    def fail_before_completion(_fd: int, data: bytes) -> int:
        prefix = data.find(completion)
        output.write(data[:prefix])
        raise OSError("injected")

    with pytest.raises(OSError, match="injected"):
        write_jsonl(stream, writer=fail_before_completion)
    assert completion not in output.getvalue()

    output = BytesIO()

    def short_writer(_fd: int, data: bytes) -> int:
        size = min(97, len(data))
        output.write(data[:size])
        return size

    assert write_jsonl(stream, writer=short_writer) == len(stream.jsonl)
    assert output.getvalue() == stream.jsonl


@pytest.mark.parametrize(
    ("scope", "field", "value"),
    (
        ("first", "message_id", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        ("all", "causation_id", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
    ),
)
def test_writer_rejects_mutated_expected_request_authority(
    scope: str, field: str, value: str
) -> None:
    stream = _project()
    envelopes = tuple(
        {**envelope, field: value} if scope == "all" or index == 0 else envelope
        for index, envelope in enumerate(stream.envelopes)
    )
    raw = b"".join(canonical_json_bytes(envelope) + b"\n" for envelope in envelopes)
    mutated = replace(
        stream,
        envelopes=envelopes,
        jsonl=raw,
        raw_sha256=sha256(raw).hexdigest(),
    )
    with pytest.raises(ValueError, match="authority"):
        write_jsonl(mutated, writer=lambda _fd, data: len(data))


@pytest.mark.parametrize("field", ("raw_sha256", "semantic_sha256"))
def test_writer_rejects_forged_stream_digests(field: str) -> None:
    stream = _project()
    with pytest.raises(ValueError, match="digest"):
        write_jsonl(
            replace(stream, **{field: "f" * 64}),
            writer=lambda _fd, data: len(data),
        )


def test_writer_rejects_rehashed_duplicate_run_start() -> None:
    stream = _project()
    events = (stream.events[0], stream.events[0], *stream.events[1:])
    sources = (stream.envelopes[0], stream.envelopes[0], *stream.envelopes[1:])
    with pytest.raises(ValueError, match="lifecycle"):
        write_jsonl(
            _rebuild_stream(stream, events, sources),
            writer=lambda _fd, data: len(data),
        )


def test_writer_rejects_second_order_for_the_same_target() -> None:
    stream = _project()
    events = tuple(dict(event) for event in stream.events)
    orders = [
        index
        for index, event in enumerate(events)
        if event["event_type"] == "OrderSubmitted"
    ]
    first = events[orders[0]]
    events[orders[1]]["target_id"] = first["target_id"]
    events[orders[1]]["source_signal_ids"] = first["source_signal_ids"]
    with pytest.raises(ValueError, match="target"):
        write_jsonl(
            _rebuild_stream(stream, events),
            writer=lambda _fd, data: len(data),
        )


def test_writer_rejects_unknown_envelope_field() -> None:
    stream = _project()
    envelopes = ({**stream.envelopes[0], "unexpected": "value"}, *stream.envelopes[1:])
    raw = b"".join(canonical_json_bytes(envelope) + b"\n" for envelope in envelopes)
    with pytest.raises(ValueError, match="envelope"):
        write_jsonl(
            replace(
                stream,
                envelopes=envelopes,
                jsonl=raw,
                raw_sha256=sha256(raw).hexdigest(),
            ),
            writer=lambda _fd, data: len(data),
        )


def test_rejects_unknown_or_misordered_native_facts() -> None:
    with pytest.raises(ValueError, match="fact stream"):
        _project(run=_run(facts=_facts()[1:]))
    bad = list(_facts())
    bad[4], bad[5] = bad[5], bad[4]
    with pytest.raises(ValueError, match="fact stream"):
        _project(run=_run(facts=tuple(bad)))
    non_scalar = list(_facts())
    non_scalar[0] = SimpleNamespace(
        kind="quote", attributes=(("instrument_id", object()),)
    )
    with pytest.raises(ValueError, match="fact stream"):
        _project(run=_run(facts=tuple(non_scalar)))


def test_native_order_source_signals_must_match_the_plan() -> None:
    facts = list(_facts())
    order = facts[3]
    facts[3] = replace(
        order,
        attributes=tuple(
            (
                name,
                "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
                if name == "source_signal_id_0"
                else value,
            )
            for name, value in order.attributes
        ),
    )
    with pytest.raises(ValueError, match="fact stream"):
        _project(run=_run(facts=tuple(facts)))


@pytest.mark.parametrize(
    ("fact_index", "field", "value"),
    (
        (0, "bid", "98"),
        (1, "target_quantity", "1"),
    ),
)
def test_native_quote_and_plan_facts_are_hash_bound(
    fact_index: int, field: str, value: str
) -> None:
    facts = list(_facts())
    fact = facts[fact_index]
    facts[fact_index] = replace(
        fact,
        attributes=tuple(
            (name, value if name == field else item) for name, item in fact.attributes
        ),
    )
    with pytest.raises(ValueError, match="business facts"):
        _project(run=_run(facts=tuple(facts)))


def _terminal_long_case() -> tuple[
    tuple[Fact, ...], SimpleNamespace, CompletionAuthority
]:
    facts = _facts()
    terminal_long_facts = facts[:5] + (facts[5], facts[-1])
    run = _run(
        facts=terminal_long_facts,
        balance_facts=(
            ("BTC", "9990.00999", "0", "9990.00999"),
            ("USDT", "0.000001", "0", "0.000001"),
        ),
        commission_facts=(("USDT", "999.000999"),),
    )
    run.processed_target_ids = ("11111111-1111-4111-8111-111111111111",)
    run.native_order_ids = ("O-1",)
    run.native_fill_ids = ("T-1",)
    run.order_count = run.fill_count = 1
    run.position_quantity = "9990.00999"
    run.position_average_entry = "100"
    run.position_realized_pnl = "0"
    run.position_unrealized_pnl = "19980.01998"
    authority = CompletionAuthority(
        target_count=1,
        order_count=1,
        fill_count=1,
        final_cash="0.000001",
        final_position="9990.00999",
        fees="999.000999",
        realized_pnl="0",
        unrealized_pnl="19980.01998",
    )

    return terminal_long_facts, run, authority


def test_quote_without_a_target_before_stopped_is_still_hash_bound() -> None:
    terminal_long_facts, run, authority = _terminal_long_case()
    inputs = _inputs(target_count=1)
    stream = project_event_stream(
        inputs,
        run,
        authority,
        closure_digest="a" * 64,
        upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )
    assert [event["event_type"] for event in stream.events].count("TargetAccepted") == 1

    terminal_quote = terminal_long_facts[-2]
    mutated = replace(
        terminal_quote,
        attributes=tuple(
            (name, "100" if name == "bid" else value)
            for name, value in terminal_quote.attributes
        ),
    )
    run.native_facts = terminal_long_facts[:-2] + (mutated, terminal_long_facts[-1])
    with pytest.raises(ValueError, match="business facts"):
        project_event_stream(
            inputs,
            run,
            authority,
            closure_digest="a" * 64,
            upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        )


def test_rejects_result_from_a_different_target_schedule() -> None:
    _, run, authority = _terminal_long_case()
    with pytest.raises(ValueError, match="schedule"):
        project_event_stream(
            _inputs(),
            run,
            authority,
            closure_digest="a" * 64,
            upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("target_count", 1),
        ("order_count", 1),
        ("fill_count", 1),
        ("final_cash", "1"),
        ("final_position", "1"),
        ("fees", "1"),
        ("realized_pnl", "1"),
        ("unrealized_pnl", "1"),
    ),
)
def test_completion_authority_must_match_the_run_snapshot(
    field: str, value: str | int
) -> None:
    with pytest.raises(ValueError, match="completion authority"):
        project_event_stream(
            _inputs(),
            _run(),
            replace(AUTHORITY, **{field: value}),
            closure_digest="a" * 64,
            upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        )


@pytest.mark.parametrize(
    ("balances", "commissions"),
    (
        (("invalid",), (("USDT", "2017.98201798"),)),
        ((("BTC", "0", "0", "0"),), (("USDT", "2017.98201798"),)),
        (
            (
                ("USDT", "1007972.02797202", "0", "1007972.02797202"),
                ("USDT", "1007972.02797202", "0", "1007972.02797202"),
            ),
            (("USDT", "2017.98201798"),),
        ),
        (
            (("USDT", "1007972.02797202", "1", "1007972.02797202"),),
            (("USDT", "2017.98201798"),),
        ),
        (
            (("USDT", "1007972.02797202", "0", "1007972.02797202"),),
            (("invalid",),),
        ),
        (
            (("USDT", "1007972.02797202", "0", "1007972.02797202"),),
            (),
        ),
        (
            (("USDT", "1007972.02797202", "0", "1007972.02797202"),),
            (("USDT", "2017.98201798"), ("USDT", "2017.98201798")),
        ),
        (
            (("USDT", "1007972.02797202", "0", "1007972.02797202"),),
            (("USDT", "2017.98201798"), ("BTC", "1")),
        ),
    ),
)
def test_completion_rejects_malformed_missing_or_duplicate_usdt_observations(
    balances: tuple[tuple[str, ...], ...],
    commissions: tuple[tuple[str, ...], ...],
) -> None:
    with pytest.raises(ValueError, match="completion authority"):
        _project(run=_run(balance_facts=balances, commission_facts=commissions))


def test_zero_order_completion_accepts_empty_commission_observation() -> None:
    target_id = "11111111-1111-4111-8111-111111111111"
    signal_id = "22222222-2222-4222-8222-222222222222"
    facts = (
        _facts()[0],
        Fact(
            "target_planned",
            (
                ("target_id", target_id),
                *_signals(signal_id),
                ("effective_at", "2026-08-05T12:00:00Z"),
                ("instrument_id", "BTCUSDT.BINANCE"),
                ("current_quantity", "0"),
                ("target_quantity", "0"),
                ("delta", "0"),
                ("side", None),
                ("price_basis", "100.01"),
                ("notional", "0"),
                ("reason", "ALREADY_AT_TARGET"),
            ),
        ),
        Fact("target_quantity_planned", (("quantity", "0"),)),
        Fact("stopped", (("state", "COMPLETED"),)),
    )
    run = SimpleNamespace(
        **{
            **vars(_run(facts=facts)),
            "processed_target_ids": (target_id,),
            "native_order_ids": (),
            "native_fill_ids": (),
            "order_count": 0,
            "fill_count": 0,
            "balance_facts": (("USDT", "1000000", "0", "1000000"),),
            "commission_facts": (),
            "position_realized_pnl": "0",
            "last_market_timestamp": 1785931200000000000,
        }
    )
    completion = CompletionAuthority(1, 0, 0, "1000000", "0", "0", "0", "0")

    stream = project_event_stream(
        _inputs(
            first_weight="0",
            market_data=_market_data().splitlines(keepends=True)[0],
            target_count=1,
        ),
        run,
        completion,
        closure_digest="a" * 64,
        upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )

    assert stream.events[-1]["event_type"] == "RunCompleted"
    assert stream.events[-1]["fees"] == "0"
