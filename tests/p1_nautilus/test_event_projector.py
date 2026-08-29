from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest

from engines.nautilus.runtime_v1.event_projector import (
    CompletionAuthority,
    project_event_stream,
)
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


def _facts() -> tuple[Fact, ...]:
    first_target = "11111111-1111-4111-8111-111111111111"
    second_target = "44444444-4444-4444-8444-444444444444"
    first_signal = "22222222-2222-4222-8222-222222222222"
    second_signal = "33333333-3333-4333-8333-333333333333"
    return (
        Fact(
            "quote",
            (
                ("instrument_id", "BTCUSDT.BINANCE"),
                ("bid", "99"),
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
                ("target_quantity", "9990.00999"),
                ("delta", "9990.00999"),
                ("side", "BUY"),
                ("price_basis", "100"),
                ("notional", "999000.999"),
                ("reason", "ORDER"),
            ),
        ),
        Fact("target_quantity_planned", (("quantity", "9990.00999"),)),
        Fact(
            "order_submitted",
            (
                ("client_order_id", "O-1"),
                ("target_id", first_target),
                *_signals(first_signal),
                ("side", "BUY"),
                ("quantity", "9990.00999"),
                ("order_type", "MARKET"),
            ),
        ),
        Fact(
            "order_filled",
            (
                ("client_order_id", "O-1"),
                ("trade_id", "T-1"),
                ("side", "BUY"),
                ("quantity", "9990.00999"),
                ("price", "100"),
                ("commission", "999.000999"),
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
                ("current_quantity", "9990.00999"),
                ("target_quantity", "0"),
                ("delta", "-9990.00999"),
                ("side", "SELL"),
                ("price_basis", "102"),
                ("notional", "1018981.01898"),
                ("reason", "ORDER"),
            ),
        ),
        Fact("target_quantity_planned", (("quantity", "9990.00999"),)),
        Fact(
            "order_submitted",
            (
                ("client_order_id", "O-2"),
                ("target_id", second_target),
                *_signals(second_signal),
                ("side", "SELL"),
                ("quantity", "9990.00999"),
                ("order_type", "MARKET"),
            ),
        ),
        Fact(
            "order_filled",
            (
                ("client_order_id", "O-2"),
                ("trade_id", "T-2"),
                ("side", "SELL"),
                ("quantity", "9990.00999"),
                ("price", "102"),
                ("commission", "1018.98101898"),
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


def _inputs(
    *,
    suffix: str = "a",
    event_time: str = "2026-08-05T12:00:00Z",
    market_data: bytes = b"market-data",
    second_weight: str = "0",
) -> RuntimeInputs:
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
    schedule = _freeze(
        {
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
                            "target_weight": "1",
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
    )
    return RuntimeInputs(request, (), (), schedule, market_data)


def _run(
    *,
    facts: tuple[Fact, ...] | None = None,
    balance_facts: tuple[tuple[str, ...], ...] = (
        ("BTC", "0", "0", "0"),
        ("USDT", "1007972.02797202", "0", "1007972.02797202"),
    ),
    commission_facts: tuple[tuple[str, ...], ...] = (("USDT", "2017.98201798"),),
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
        position_realized_pnl="9990.00999",
        position_unrealized_pnl="0",
        last_market_timestamp=1785931260000000000,
    )


AUTHORITY = CompletionAuthority(
    target_count=2,
    order_count=2,
    fill_count=2,
    final_cash="1007972.02797202",
    final_position="0",
    fees="2017.98201798",
    realized_pnl="9990.00999",
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
    assert [str(event.quantity) for event in planned] == ["9990.00999", "9990.00999"]
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

    changed_quote_raw = b"market-data-with-one-changed-quote"
    assert (
        first.semantic_sha256
        != _project(_inputs(market_data=changed_quote_raw)).semantic_sha256
    )
    assert (
        first.semantic_sha256 != _project(_inputs(second_weight="0.1")).semantic_sha256
    )

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
    ),
)
def test_completion_rejects_malformed_missing_or_duplicate_usdt_observations(
    balances: tuple[tuple[str, ...], ...],
    commissions: tuple[tuple[str, ...], ...],
) -> None:
    with pytest.raises(ValueError, match="completion authority"):
        _project(run=_run(balance_facts=balances, commission_facts=commissions))
