"""Pure reduction of immutable canonical event envelopes."""
from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import TypeAlias
from uuid import UUID

from packages.domain.events import EventEnvelope

from .models import (
    REDUCER_VERSION, REPLAY_SCHEMA_VERSION, AggregateReplayState, AppliedEvent, EventTypeCount, ReplayIssue, ReplayIssueCode,
    ReplayResult, ReplayStatus, SnapshotRecord, StoredEvent, StreamProjection,
)
from .replay import ReplayError, _validate_snapshot, canonical_state_json, event_digest


class ReducerPolicy(str, Enum):
    DEFAULT = "DEFAULT"
    DEGRADED = "DEGRADED"


class ConflictingEventError(ReplayError):
    pass


class SequenceError(ReplayError):
    pass


EventInput: TypeAlias = EventEnvelope[object]


def _initial_maps(initial: SnapshotRecord | None) -> tuple[dict[UUID, StoredEvent | None], dict[UUID, int], dict[str, int], dict[UUID, str], int]:
    if initial is None:
        return {}, {}, {}, {}, 0
    streams: dict[UUID, StoredEvent | None] = {}
    expected: dict[UUID, int] = {}
    for stream in initial.state.streams:
        expected[stream.stream_id] = stream.last_sequence + 1
        streams[stream.stream_id] = None
    type_counts = {entry.event_type: entry.count for entry in initial.state.type_counts}
    applied = {entry.event_id: entry.digest for entry in initial.state.applied_events}
    return streams, expected, type_counts, applied, initial.state.event_count


def reduce_events(events: Iterable[EventInput], *, policy: ReducerPolicy = ReducerPolicy.DEFAULT, initial: SnapshotRecord | None = None) -> ReplayResult:
    """Reduce a canonical immutable event set independent of caller order."""
    if type(policy) is not ReducerPolicy:
        raise ReplayError("policy must be an exact ReducerPolicy member")
    if initial is not None:
        initial = _validate_snapshot(initial)
    incoming = tuple(StoredEvent.from_envelope(event) for event in events)
    _, expected, type_counts, applied, starting_count = _initial_maps(initial)
    unique: list[StoredEvent] = []
    seen_incoming: dict[UUID, str] = {}
    unresolved = {issue.event_id: issue for issue in initial.issues} if initial is not None else {}
    for record in incoming:
        previous_digest = applied.get(record.event_id)
        if previous_digest is not None:
            if previous_digest != record.digest:
                raise ConflictingEventError(f"conflicting content for event_id {record.event_id}")
            continue
        previous_issue = unresolved.get(record.event_id)
        if previous_issue is not None and previous_issue.digest != record.digest:
            raise ConflictingEventError(f"conflicting retry for skipped event_id {record.event_id}")
        previous_digest = seen_incoming.get(record.event_id)
        if previous_digest is not None:
            if previous_digest != record.digest:
                raise ConflictingEventError(f"conflicting content for event_id {record.event_id}")
            continue
        seen_incoming[record.event_id] = record.digest
        unique.append(record)

    canonical = tuple(
        sorted(
            unique,
            key=lambda event: (
                event.stream_id.bytes,
                event.sequence,
                event.event_id.bytes,
            ),
        )
    )
    valid: list[StoredEvent] = []
    for record in canonical:
        wanted = expected.get(record.stream_id, 1)
        if record.sequence != wanted:
            code = ReplayIssueCode.SEQUENCE_GAP if record.sequence > wanted else ReplayIssueCode.SEQUENCE_REGRESSION
            if policy is ReducerPolicy.DEFAULT:
                raise SequenceError(f"{code.value} in stream {record.stream_id}: expected {wanted}, got {record.sequence}")
            unresolved[record.event_id] = ReplayIssue(code=code, stream_id=record.stream_id, event_id=record.event_id, sequence=record.sequence, expected_sequence=wanted, digest=record.digest)
            continue
        expected[record.stream_id] = wanted + 1
        unresolved.pop(record.event_id, None)
        valid.append(record)

    normalized = tuple(valid)
    stream_events: dict[UUID, list[StoredEvent]] = {}
    for record in normalized:
        stream_events.setdefault(record.stream_id, []).append(record)
        type_counts[record.event_type] = type_counts.get(record.event_type, 0) + 1
        applied[record.event_id] = record.digest
    streams: list[StreamProjection] = []
    for stream_id in sorted(expected, key=lambda value: value.bytes):
        records = stream_events.get(stream_id, [])
        if initial is not None:
            previous = next((stream for stream in initial.state.streams if stream.stream_id == stream_id), None)
        else:
            previous = None
        count = (previous.event_count if previous else 0) + len(records)
        if records:
            last_sequence, last_digest = records[-1].sequence, records[-1].digest
        elif previous:
            last_sequence, last_digest = previous.last_sequence, previous.last_digest
        else:
            last_sequence, last_digest = 0, None
        streams.append(StreamProjection(stream_id=stream_id, event_count=count, last_sequence=last_sequence, last_digest=last_digest))
    state = AggregateReplayState(
        event_count=starting_count + len(normalized),
        type_counts=tuple(EventTypeCount(event_type=kind, count=type_counts[kind]) for kind in sorted(type_counts)),
        streams=tuple(streams),
        applied_events=tuple(AppliedEvent(event_id=event_id, digest=applied[event_id]) for event_id in sorted(applied, key=lambda value: value.bytes)),
    )
    ordered_issues = tuple(sorted(unresolved.values(), key=lambda issue: (issue.stream_id.bytes, issue.sequence, issue.event_id.bytes)))
    status = ReplayStatus.DEGRADED if ordered_issues else ReplayStatus.COMPLETE
    state_json = canonical_state_json(
        state,
        status,
        ordered_issues,
        schema_version=REPLAY_SCHEMA_VERSION,
        reducer_version=REDUCER_VERSION,
    )
    return ReplayResult(
        schema_version=REPLAY_SCHEMA_VERSION,
        reducer_version=REDUCER_VERSION,
        status=status,
        state=state,
        issues=ordered_issues,
        canonical_state_json=state_json,
        state_hash=event_digest(state_json),
    )
