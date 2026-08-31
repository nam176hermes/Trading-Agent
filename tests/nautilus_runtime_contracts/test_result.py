from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid5

import pytest

from packages.engine_contracts import (
    EngineEvent,
    EngineEventEnvelope,
    EventAttribute,
    EventFamily,
    canonical_json_bytes,
    payload_digest,
)
from packages.job_contracts import EngineBacktestPayload
from packages.nautilus_runtime_contracts import (
    P1_EVENT_ADAPTER,
    event_message_id,
    semantic_digest,
)
from services.job_worker.engine_authority import BacktestEngineAuthorityFactory

from tests.jobs.test_engine_result_validation import CODE_COMMIT, NOW, _claim


_FAMILY = {
    "RunStarted": EventFamily.ENGINE_LIFECYCLE,
    "TargetAccepted": EventFamily.STRATEGY_LIFECYCLE,
    "TargetQuantityPlanned": EventFamily.STRATEGY_LIFECYCLE,
    "OrderSubmitted": EventFamily.ORDER_LIFECYCLE,
    "Fill": EventFamily.FILLS,
    "PositionObserved": EventFamily.POSITIONS,
    "AccountObserved": EventFamily.ACCOUNT_STATE,
    "RunCompleted": EventFamily.ENGINE_LIFECYCLE,
}
_GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "fixtures/p1_nautilus/golden/positive/event_stream.jsonl"
)


def _p1_claim():
    claim = _claim()
    assert isinstance(claim.payload, EngineBacktestPayload)
    engine_input = claim.payload.engine_backtest.model_copy(
        update={
            "start_time": datetime(2026, 8, 5, 12, tzinfo=UTC),
            "end_time": datetime(2026, 8, 5, 12, 2, tzinfo=UTC),
        }
    )
    return replace(
        claim,
        payload=claim.payload.model_copy(update={"engine_backtest": engine_input}),
    )


def _p1_request():
    return BacktestEngineAuthorityFactory(
        code_commit=CODE_COMMIT, clock=lambda: NOW
    ).from_claim(_p1_claim())


def _batch() -> tuple[bytes, tuple[EngineEventEnvelope, ...]]:
    request = _p1_request()
    loaded = tuple(
        P1_EVENT_ADAPTER.validate_json(line) for line in _GOLDEN.read_bytes().splitlines()
    )
    start = loaded[0].model_copy(
        update={
            "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
            "config_digest": request.config_digest,
            "catalog_digest": request.payload.instrument_catalog.sha256,
            "data_digest": request.payload.market_data.sha256,
        }
    )
    completion = loaded[-1].model_copy(
        update={
            "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
            "semantic_digest": "0" * 64,
        }
    )
    undigested = (start, *loaded[1:-1], completion)
    p1_events = undigested[:-1] + (
        completion.model_copy(update={"semantic_digest": semantic_digest(undigested)}),
    )
    envelopes = []
    for event in p1_events:
        document = event.model_dump(mode="json")
        attributes = tuple(
            EventAttribute(
                name=name,
                value=(
                    canonical_json_bytes(value).decode("utf-8")
                    if type(value) is list
                    else value
                ),
            )
            for name, value in document.items()
            if name != "event_type" and value is not None
        )
        payload = EngineEvent(
            event_type=event.event_type,
            family=_FAMILY[event.event_type],
            attributes=attributes,
        )
        envelopes.append(
            EngineEventEnvelope(
                message_id=event_message_id(request.message_id, event),
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                engine_run_id=request.engine_run_id,
                stream_sequence=event.sequence,
                event_time=request.event_time,
                initialization_time=request.initialization_time,
                schema_version=request.schema_version,
                producer_identity=request.producer_identity,
                source_commit=request.source_commit,
                config_digest=request.config_digest,
                payload_digest=payload_digest(payload),
                payload=payload,
            )
        )
    raw = b"".join(canonical_json_bytes(event) + b"\n" for event in envelopes)
    return raw, tuple(envelopes)


def _with_attribute(
    envelope: EngineEventEnvelope, name: str, value: str | int
) -> EngineEventEnvelope:
    payload = envelope.payload.model_copy(
        update={
            "attributes": tuple(
                attribute.model_copy(update={"value": value})
                if attribute.name == name
                else attribute
                for attribute in envelope.payload.attributes
            )
        }
    )
    return envelope.model_copy(
        update={"payload": payload, "payload_digest": payload_digest(payload)}
    )


def _raw(envelopes: tuple[EngineEventEnvelope, ...]) -> bytes:
    return b"".join(canonical_json_bytes(event) + b"\n" for event in envelopes)


def test_validates_complete_p1_result_and_returns_exact_summary() -> None:
    from packages.nautilus_runtime_contracts.result import validate_p1_result

    raw, envelopes = _batch()
    result = validate_p1_result(
        _p1_request(),
        envelopes,
        raw=raw,
        expected_closure_digest="a" * 64,
    )

    assert result.batch_sha256 == sha256(raw).hexdigest()
    assert result.semantic_sha256 == (
        "454890c4511611b9aa11f695b87e60f2eb2e5fdfe40934c8d7ed59645429d032"
    )
    assert result.event_count == 8
    assert str(result.final_cash) == "999899.9"
    assert str(result.final_position) == "1"
    assert str(result.fees) == "0.1"
    assert result.events[-1].event_type == "RunCompleted"


@pytest.mark.parametrize(
    "mutation",
    (
        "duplicate_id",
        "sequence_gap",
        "wrong_causation",
        "wrong_run",
        "wrong_config",
        "wrong_event_time",
        "wrong_producer",
        "wrong_source",
        "extra_after_completion",
        "wrong_counter",
        "wrong_cash",
        "wrong_position",
        "wrong_fees",
        "semantic_mismatch",
    ),
)
def test_rejects_every_request_state_and_financial_mutation(mutation: str) -> None:
    from packages.nautilus_runtime_contracts.result import validate_p1_result

    _original, original = _batch()
    events = list(original)
    if mutation == "duplicate_id":
        events[1] = events[1].model_copy(update={"message_id": events[0].message_id})
    elif mutation == "sequence_gap":
        events[1] = _with_attribute(events[1], "sequence", 4).model_copy(
            update={
                "stream_sequence": 4,
                "message_id": uuid5(
                    _p1_request().message_id,
                    "nautilus-p1-event-stream-v1:4:TargetAccepted",
                ),
            }
        )
    elif mutation == "wrong_causation":
        events[0] = events[0].model_copy(update={"causation_id": events[0].message_id})
    elif mutation == "wrong_run":
        events[0] = events[0].model_copy(update={"engine_run_id": events[0].message_id})
    elif mutation == "wrong_config":
        events[0] = events[0].model_copy(update={"config_digest": "f" * 64})
    elif mutation == "wrong_event_time":
        events[0] = events[0].model_copy(
            update={"event_time": events[0].event_time.replace(microsecond=1)}
        )
    elif mutation == "wrong_producer":
        events[0] = events[0].model_copy(update={"producer_identity": "other-worker"})
    elif mutation == "wrong_source":
        events[0] = events[0].model_copy(update={"source_commit": "f" * 40})
    elif mutation == "extra_after_completion":
        extra = _with_attribute(events[-1], "sequence", 10).model_copy(
            update={
                "stream_sequence": 10,
                "message_id": uuid5(
                    _p1_request().message_id,
                    "nautilus-p1-event-stream-v1:10:RunCompleted",
                ),
            }
        )
        events.append(extra)
    else:
        field, value = {
            "wrong_counter": ("fill_count", 2),
            "wrong_cash": ("final_cash", "999900"),
            "wrong_position": ("final_position", "0"),
            "wrong_fees": ("fees", "0.2"),
            "semantic_mismatch": ("semantic_digest", "f" * 64),
        }[mutation]
        events[-1] = _with_attribute(events[-1], field, value)
    batch = tuple(events)

    with pytest.raises(ValueError):
        validate_p1_result(
            _p1_request(),
            batch,
            raw=_raw(batch),
            expected_closure_digest="a" * 64,
        )


def test_rejects_wrong_product_lineage_and_artifact_binding() -> None:
    from packages.nautilus_runtime_contracts.result import validate_p1_result

    _original, original = _batch()
    for field, value in (
        ("upstream_commit", "b" * 40),
        ("closure_digest", "b" * 64),
        ("config_digest", "b" * 64),
        ("catalog_digest", "b" * 64),
        ("data_digest", "b" * 64),
    ):
        events = list(original)
        events[0] = _with_attribute(events[0], field, value)
        batch = tuple(events)
        with pytest.raises(ValueError):
            validate_p1_result(
                _p1_request(),
                batch,
                raw=_raw(batch),
                expected_closure_digest="a" * 64,
            )


def test_rejects_duplicate_event_attribute_names_even_when_values_match() -> None:
    from packages.nautilus_runtime_contracts.result import validate_p1_result

    _original, original = _batch()
    first = original[0]
    payload = first.payload.model_copy(
        update={"attributes": first.payload.attributes + (first.payload.attributes[0],)}
    )
    changed = first.model_copy(
        update={"payload": payload, "payload_digest": payload_digest(payload)}
    )
    batch = (changed, *original[1:])

    with pytest.raises(ValueError, match="attribute"):
        validate_p1_result(
            _p1_request(),
            batch,
            raw=_raw(batch),
            expected_closure_digest="a" * 64,
        )


@pytest.mark.parametrize(
    ("name", "value"),
    (("event_type", "RunStarted"), ("native_type", "Order")),
)
def test_rejects_attribute_collision_with_code_owned_fields(
    name: str, value: str
) -> None:
    from packages.nautilus_runtime_contracts.result import validate_p1_result

    _original, original = _batch()
    first = original[0]
    payload = first.payload.model_copy(
        update={
            "attributes": first.payload.attributes
            + (EventAttribute(name=name, value=value),)
        }
    )
    changed = first.model_copy(
        update={"payload": payload, "payload_digest": payload_digest(payload)}
    )
    batch = (changed, *original[1:])

    with pytest.raises(ValueError, match="attribute"):
        validate_p1_result(
            _p1_request(),
            batch,
            raw=_raw(batch),
            expected_closure_digest="a" * 64,
        )


def test_normalizes_deep_source_signal_nesting_to_value_error() -> None:
    from packages.nautilus_runtime_contracts.result import validate_p1_result

    _original, original = _batch()
    events = list(original)
    events[1] = _with_attribute(
        events[1], "source_signal_ids", "[" * 2_000 + '"signal"' + "]" * 2_000
    )
    batch = tuple(events)

    with pytest.raises(ValueError):
        validate_p1_result(
            _p1_request(),
            batch,
            raw=_raw(batch),
            expected_closure_digest="a" * 64,
        )
