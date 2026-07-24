from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.event_ledger import ReducerPolicy, ReplayError, ReplayStatus, replay, snapshot_from_result
from packages.event_ledger.models import EventTypeCount, SnapshotRecord, StoredEvent, StreamProjection
from packages.event_ledger.replay import _canonical_json, canonical_state_json, event_digest

from tests.event_ledger.test_reducer import envelope, fill, signal, uid

BIGINT_MAX = 9223372036854775807


def _forge(snapshot: SnapshotRecord, *, state=None, status=None, issues=None) -> SnapshotRecord:
    state = snapshot.state if state is None else state
    status = snapshot.status if status is None else status
    issues = snapshot.issues if issues is None else issues
    canonical = canonical_state_json(state, status, issues)
    return snapshot.model_copy(update={"state": state, "status": status, "issues": issues, "canonical_state_json": canonical, "state_hash": event_digest(canonical)})


def test_snapshot_is_global_aggregate_and_unicode_canonical_bytes_are_content_addressed() -> None:
    first = envelope(signal(), event_number=1, stream_number=100, sequence=1).model_copy(update={"source": "雪☃"})
    second = envelope(fill(), event_number=2, stream_number=200, sequence=1).model_copy(update={"source": "emoji🚀"})

    snapshot = snapshot_from_result(replay((second, first)))
    wrapper = json.loads(snapshot.canonical_state_json)

    assert set(SnapshotRecord.model_fields) == {"schema_version", "reducer_version", "state", "status", "issues", "canonical_state_json", "state_hash"}
    assert "stream_id" not in wrapper
    assert "last_sequence" not in wrapper
    assert "\\u96ea" in first.model_dump_json() or first.source == "雪☃"
    assert snapshot.state.event_count == 2
    assert snapshot.state_hash == event_digest(snapshot.canonical_state_json)


def test_snapshot_resume_accepts_multi_stream_suffix_and_rejects_non_envelope_typed() -> None:
    first = envelope(signal(), event_number=1, stream_number=100, sequence=1)
    second = envelope(fill(), event_number=2, stream_number=200, sequence=1)
    suffix_a = envelope(fill(), event_number=3, stream_number=100, sequence=2)
    suffix_b = envelope(signal(), event_number=4, stream_number=200, sequence=2)
    snapshot = snapshot_from_result(replay((first, second)))

    assert replay((suffix_b, suffix_a), snapshot=snapshot) == replay((first, suffix_a, second, suffix_b))
    with pytest.raises(ReplayError):
        replay((object(),), snapshot=snapshot)  # type: ignore[arg-type]


def test_bigint_bounds_for_persisted_sequences_and_counts() -> None:
    StoredEvent(event_id=uid(1), stream_id=uid(2), sequence=BIGINT_MAX, event_type="SignalProposal", canonical_json="{}", digest="0" * 64)
    EventTypeCount(event_type="SignalProposal", count=BIGINT_MAX)
    StreamProjection(stream_id=uid(2), event_count=BIGINT_MAX, last_sequence=BIGINT_MAX, last_digest="0" * 64)
    with pytest.raises(ValidationError):
        StoredEvent(event_id=uid(1), stream_id=uid(2), sequence=BIGINT_MAX + 1, event_type="SignalProposal", canonical_json="{}", digest="0" * 64)
    with pytest.raises(ValidationError):
        EventTypeCount(event_type="SignalProposal", count=BIGINT_MAX + 1)
    with pytest.raises(ValidationError):
        StreamProjection(stream_id=uid(2), event_count=BIGINT_MAX + 1, last_sequence=BIGINT_MAX, last_digest="0" * 64)


def test_empty_global_snapshot_is_valid_and_content_addressed() -> None:
    snapshot = snapshot_from_result(replay(()))

    assert snapshot.state.event_count == 0
    assert snapshot.state.applied_events == ()
    assert snapshot.state.type_counts == ()
    assert snapshot.state.streams == ()
    assert snapshot.issues == ()
    assert snapshot.status is ReplayStatus.COMPLETE
    assert snapshot.state_hash == event_digest(snapshot.canonical_state_json)


@pytest.mark.parametrize("mutate", [
    lambda s: _forge(s, state=s.state.model_copy(update={"event_count": s.state.event_count + 1})),
    lambda s: _forge(s, state=s.state.model_copy(update={"applied_events": tuple(reversed(s.state.applied_events))})),
    lambda s: _forge(s, state=s.state.model_copy(update={"applied_events": (s.state.applied_events[0], s.state.applied_events[0])})),
    lambda s: _forge(s, state=s.state.model_copy(update={"type_counts": (EventTypeCount(event_type="SignalProposal", count=s.state.event_count + 1),)})),
    lambda s: _forge(s, state=s.state.model_copy(update={"type_counts": (EventTypeCount(event_type="SignalProposal", count=1), EventTypeCount(event_type="SignalProposal", count=1))})),
    lambda s: _forge(s, state=s.state.model_copy(update={"type_counts": tuple(reversed(s.state.type_counts))})),
    lambda s: _forge(s, state=s.state.model_copy(update={"type_counts": (EventTypeCount(event_type="Unregistered", count=s.state.event_count),)})),
    lambda s: _forge(s, state=s.state.model_copy(update={"streams": (s.state.streams[0].model_copy(update={"event_count": s.state.event_count + 1}),)})),
    lambda s: _forge(s, state=s.state.model_copy(update={"streams": (s.state.streams[0], s.state.streams[0])})),
    lambda s: _forge(s, state=s.state.model_copy(update={"streams": tuple(reversed(s.state.streams))})),
    lambda s: _forge(s, state=s.state.model_copy(update={"streams": (s.state.streams[0].model_copy(update={"last_sequence": s.state.streams[0].last_sequence + 1}),) + s.state.streams[1:]})),
])
def test_snapshot_golden_vectors_reject_state_count_type_stream_forgeries(mutate) -> None:
    first = envelope(signal(), event_number=1, stream_number=100, sequence=1)
    second = envelope(fill(), event_number=2, stream_number=200, sequence=1)
    snapshot = snapshot_from_result(replay((first, second)))

    with pytest.raises(ReplayError):
        replay((), snapshot=mutate(snapshot))


def test_snapshot_golden_vectors_reject_issue_forgeries_even_with_recomputed_hash() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    gap = envelope(fill(), event_number=2, sequence=3)
    snapshot = snapshot_from_result(replay((first, gap), policy=ReducerPolicy.DEGRADED))
    issue = snapshot.issues[0]

    cases = [
        _forge(snapshot, issues=(issue, issue)),
        _forge(snapshot, issues=(issue.model_copy(update={"event_id": snapshot.state.applied_events[0].event_id}),)),
        _forge(snapshot, issues=(issue.model_copy(update={"event_id": UUID(int=999)}), issue)),
        _forge(snapshot, issues=(issue.model_copy(update={"sequence": issue.expected_sequence}),)),
        _forge(snapshot, status=ReplayStatus.COMPLETE),
        _forge(snapshot, issues=()),
    ]
    for forged in cases:
        with pytest.raises(ReplayError):
            replay((), snapshot=forged)


def test_unicode_golden_vector_matches_python_postgresql_canonical_contract() -> None:
    assert _canonical_json({"é": "中😀", "quote": "\"\\\b\t\n\f\r"}) == (
        '{"quote":"\\\"\\\\\\b\\t\\n\\f\\r","\\u00e9":"\\u4e2d\\ud83d\\ude00"}'
    )
    for value in ("\x00", "\ud800", "\udc00"):
        with pytest.raises(ReplayError):
            _canonical_json({"value": value})
