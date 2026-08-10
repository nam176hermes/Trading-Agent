from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

from packages.event_ledger import (
    ConflictingEventError,
    REDUCER_VERSION,
    REPLAY_SCHEMA_VERSION,
    ReducerPolicy,
    ReplayError,
    deserialize_event,
    reduce_events,
    replay,
    serialize_event,
    snapshot_from_result,
)
from packages.event_ledger.replay import canonical_state_json, event_digest

from tests.event_ledger.test_reducer import envelope, fill, order_event, order_intent, risk, signal, target
from tests.runtime_risk.test_approval import runtime_risk_event


@pytest.mark.parametrize("payload", [signal, target, risk, order_intent, order_event, fill])
def test_all_registered_payload_types_round_trip_exact_concrete_type(payload: object) -> None:
    event = envelope(payload(), event_number=100 + len(type(payload()).__name__))  # type: ignore[operator]
    assert deserialize_event(serialize_event(event)) == event
    assert type(deserialize_event(serialize_event(event)).payload) is type(event.payload)


def test_runtime_risk_decision_replay_round_trip_is_canonical_and_concrete() -> None:
    event = runtime_risk_event()
    canonical = serialize_event(event)

    restored = deserialize_event(canonical)

    assert restored == event
    assert type(restored.payload) is type(event.payload)
    assert serialize_event(restored) == canonical


def test_accepted_base_order_event_wire_shape_is_a_rejected_preproduction_break() -> None:
    accepted_base_document = {
        "causation_id": "00000000-0000-0000-0000-00000000001f",
        "correlation_id": "00000000-0000-0000-0000-00000000001e",
        "effective_at": "2026-07-20T12:00:02Z",
        "event_id": "00000000-0000-0000-0000-000000000064",
        "event_type": "OrderEvent",
        "expires_at": "2026-07-20T12:05:00Z",
        "ingested_at": "2026-07-20T12:00:01Z",
        "observed_at": "2026-07-20T12:00:00Z",
        "payload": {
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTC-USD",
                "venue": "ALPACA",
            },
            "intent_id": "00000000-0000-0000-0000-000000000013",
            "limit_price": {"amount": "100", "currency": "USD"},
            "occurred_at": "2026-07-20T12:00:00Z",
            "order_id": "00000000-0000-0000-0000-000000000014",
            "order_type": "limit",
            "quantity": {"precision": 2, "value": "1.25"},
            "schema_version": "1.0",
            "side": "buy",
            "status": "accepted",
            "time_in_force": "day",
        },
        "produced_at": "2026-07-20T12:00:02Z",
        "schema_version": "1",
        "sequence": 1,
        "source": "test",
        "stream_id": "00000000-0000-0000-0000-000000000064",
        "trace_id": "00000000-0000-0000-0000-000000000020",
    }
    accepted_base_json = json.dumps(
        accepted_base_document, sort_keys=True, separators=(",", ":")
    )

    with pytest.raises(ReplayError, match="invalid canonical event JSON"):
        deserialize_event(accepted_base_json)


@given(order=st.permutations((0, 1, 2, 3)))
def test_replay_treats_input_as_canonical_immutable_event_set(
    order: list[int],
) -> None:
    events = (
        envelope(signal(), event_number=1, stream_number=100, sequence=1),
        envelope(fill(), event_number=2, stream_number=100, sequence=2),
        envelope(signal(), event_number=3, stream_number=200, sequence=1),
        envelope(fill(), event_number=4, stream_number=200, sequence=2),
    )
    expected = replay(events)

    actual = replay(tuple(events[index] for index in order))

    assert actual.canonical_state_json == expected.canonical_state_json
    assert actual.state_hash == expected.state_hash


def test_replay_same_history_is_byte_identical_and_snapshot_suffix_matches_full_history() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    second = envelope(fill(), event_number=2, sequence=2)
    full = replay((first, second))
    again = replay((first, second))
    assert full.canonical_state_json == again.canonical_state_json
    assert full.state_hash == again.state_hash
    snap = snapshot_from_result(replay((first,)))
    assert replay((second,), snapshot=snap) == full


@pytest.mark.parametrize("policy", (ReducerPolicy.DEFAULT, ReducerPolicy.DEGRADED))
def test_complete_and_degraded_replay_hashes_include_explicit_versions(policy: ReducerPolicy) -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    events = (first,) if policy is ReducerPolicy.DEFAULT else (first, envelope(fill(), event_number=2, sequence=3))

    result = replay(events, policy=policy)
    canonical = json.loads(result.canonical_state_json)

    assert result.schema_version == REPLAY_SCHEMA_VERSION
    assert result.reducer_version == REDUCER_VERSION
    assert canonical["schema_version"] == REPLAY_SCHEMA_VERSION
    assert canonical["reducer_version"] == REDUCER_VERSION
    assert result.state_hash == event_digest(result.canonical_state_json)


def test_changing_replay_or_reducer_version_changes_canonical_bytes_and_hash() -> None:
    result = replay((envelope(signal(), event_number=1),))

    changed_schema = canonical_state_json(
        result.state, result.status, result.issues, schema_version="replay-schema-test", reducer_version=result.reducer_version
    )
    changed_reducer = canonical_state_json(
        result.state, result.status, result.issues, schema_version=result.schema_version, reducer_version="reducer-test"
    )

    assert changed_schema != result.canonical_state_json
    assert changed_reducer != result.canonical_state_json
    assert event_digest(changed_schema) != result.state_hash
    assert event_digest(changed_reducer) != result.state_hash


def test_snapshot_with_unsupported_version_and_recomputed_hash_fails_closed() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    snapshot = snapshot_from_result(replay((first,)))
    forged_schema_version = "unsupported-replay-schema"
    forged_json = canonical_state_json(
        snapshot.state,
        snapshot.status,
        snapshot.issues,
        schema_version=forged_schema_version,
        reducer_version=snapshot.reducer_version,
    )
    forged = snapshot.model_copy(
        update={
            "schema_version": forged_schema_version,
            "canonical_state_json": forged_json,
            "state_hash": event_digest(forged_json),
        }
    )

    with pytest.raises(ReplayError):
        replay((), snapshot=forged)


def test_tampered_snapshot_fails_closed_and_global_snapshot_accepts_new_stream() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    second = envelope(fill(), event_number=2, sequence=2)
    snapshot = snapshot_from_result(replay((first,)))
    with pytest.raises(ReplayError):
        replay((second,), snapshot=snapshot.model_copy(update={"state_hash": "0" * 64}))
    other_stream = envelope(fill(), event_number=3, stream_number=999, sequence=1)
    assert replay((other_stream,), snapshot=snapshot) == replay((first, other_stream))


def test_snapshot_suffix_rejects_non_envelopes_with_typed_error() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    snapshot = snapshot_from_result(replay((first,)))

    with pytest.raises(ReplayError):
        replay((object(),), snapshot=snapshot)  # type: ignore[arg-type]


def test_snapshot_from_result_rejects_forged_replay_result() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    result = replay((first,))
    forged = result.model_copy(update={"state_hash": "0" * 64})

    with pytest.raises(ReplayError):
        snapshot_from_result(forged)


@pytest.mark.parametrize("policy", ("DEGRADED", True, 1, object()))
def test_replay_rejects_non_enum_policy_values(policy: object) -> None:
    with pytest.raises(ReplayError):
        replay((envelope(signal(), event_number=1),), policy=policy)


def test_replay_none_policy_is_the_default_enum_member() -> None:
    event = envelope(signal(), event_number=1)
    assert replay((event,), policy=None) == replay((event,), policy=ReducerPolicy.DEFAULT)


@pytest.mark.parametrize("updates", (
    {"sequence": 0},
    {"observed_at": envelope(signal(), event_number=1).produced_at + timedelta(seconds=1)},
    {"event_type": "FillEvent"},
    {"payload": fill()},
))
def test_serialize_event_revalidates_forged_envelope_copies(updates: dict[str, object]) -> None:
    forged = envelope(signal(), event_number=1).model_copy(update=updates)
    with pytest.raises(ReplayError):
        serialize_event(forged)


@pytest.mark.parametrize("source", ("bad\x00source", "bad\ud800source", "bad\udc00source"))
def test_serialization_rejects_strings_postgres_jsonb_cannot_store(source: str) -> None:
    event = envelope(signal(), event_number=1).model_copy(update={"source": source})

    with pytest.raises(ReplayError):
        serialize_event(event)


def test_codec_rejects_noncanonical_decimal_unknown_fields_and_non_utc_json() -> None:
    encoded = serialize_event(envelope(signal(), event_number=1))
    document = json.loads(encoded)
    document["payload"]["score"] = "1E-1"
    with pytest.raises(ReplayError):
        deserialize_event(json.dumps(document))
    document = json.loads(encoded)
    document["unknown"] = True
    with pytest.raises(ReplayError):
        deserialize_event(json.dumps(document))
    document = json.loads(encoded)
    document["observed_at"] = "2026-07-20T08:00:00-04:00"
    with pytest.raises(ReplayError):
        deserialize_event(json.dumps(document))


def test_end_to_end_research_signal_to_fill_replay() -> None:
    events = (
        envelope(signal(), event_number=1, sequence=1),
        envelope(target(), event_number=2, sequence=2),
        envelope(risk(), event_number=3, sequence=3),
        envelope(order_intent(), event_number=4, sequence=4),
        envelope(order_event(), event_number=5, sequence=5),
        envelope(fill(), event_number=6, sequence=6),
    )
    result = replay(events)
    assert result.state.event_count == 6
    assert result.status.value == "COMPLETE"


def test_degraded_snapshot_resume_preserves_issues_and_hash_deterministically() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    gap = envelope(fill(), event_number=2, sequence=3)
    suffix = envelope(fill(), event_number=3, sequence=2)
    snapshot = snapshot_from_result(replay((first, gap), policy=ReducerPolicy.DEGRADED))

    resumed = replay((suffix,), snapshot=snapshot)
    repeated = replay((suffix,), snapshot=snapshot)

    assert resumed.status.value == "DEGRADED"
    assert resumed.issues == snapshot.issues
    assert resumed.canonical_state_json == repeated.canonical_state_json
    assert resumed.state_hash == repeated.state_hash


def test_public_reducer_rejects_forged_initial_snapshot() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    snapshot = snapshot_from_result(replay((first,)))
    forged = snapshot.model_copy(update={"state_hash": "0" * 64})

    with pytest.raises(ReplayError):
        reduce_events((), initial=forged)


def test_public_reducer_uses_normalized_initial_snapshot() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    snapshot = snapshot_from_result(replay((first,)))
    raw_fields = {
        field_name: getattr(snapshot, field_name)
        for field_name in type(snapshot).model_fields
    }
    raw_fields["state"] = snapshot.state.model_dump(mode="python")
    forged = type(snapshot).model_construct(**raw_fields)

    with pytest.warns(UserWarning, match="PydanticSerializationUnexpectedValue"):
        normalized = reduce_events((), initial=forged)
    assert normalized == reduce_events((), initial=snapshot)


def test_skipped_gap_event_can_be_retried_after_gap_is_repaired() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    skipped = envelope(fill(), event_number=3, sequence=3)
    middle = envelope(fill(), event_number=2, sequence=2)
    degraded = replay((first, skipped), policy=ReducerPolicy.DEGRADED)
    after_middle = replay(
        (middle,),
        snapshot=snapshot_from_result(degraded),
    )

    repaired = replay(
        (skipped,),
        snapshot=snapshot_from_result(after_middle),
    )

    assert repaired == replay((first, middle, skipped))
    assert repaired.status.value == "COMPLETE"
    assert repaired.issues == ()


def test_conflicting_retry_of_skipped_gap_event_fails_closed() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    skipped = envelope(fill(), event_number=3, sequence=3)
    middle = envelope(fill(), event_number=2, sequence=2)
    degraded = replay((first, skipped), policy=ReducerPolicy.DEGRADED)
    after_middle = replay(
        (middle,),
        snapshot=snapshot_from_result(degraded),
    )
    conflicting = skipped.model_copy(update={"source": "conflicting-retry"})

    with pytest.raises(ConflictingEventError):
        replay(
            (conflicting,),
            snapshot=snapshot_from_result(after_middle),
        )


def test_global_halt_replay_empty_stream_has_no_implicit_active_state() -> None:
    from packages.runtime_risk import replay_global_halt_authority

    result = replay_global_halt_authority(events=(), stream_id=UUID(int=900))

    assert result.state is None
    assert result.head_sequence == 0
    assert result.head_event_id is None
    assert result.head_event_digest is None


def test_global_halt_replay_retains_prepared_permit_without_rotating_generation() -> None:
    from packages.event_ledger import InMemoryEventLedger
    from packages.runtime_risk import replay_global_halt_authority
    from tests.runtime_risk.test_global_halt import initialize, prepared_event

    repository = InMemoryEventLedger()
    state = initialize(repository)
    transition = repository.load_events()[0]
    prepared = prepared_event(state)

    result = replay_global_halt_authority(
        events=(transition, prepared), stream_id=state.stream_id
    )

    assert result.state == state
    assert result.prepared[0].permit_id == prepared.payload.permit_id
    assert result.consumed_permit_ids == ()
