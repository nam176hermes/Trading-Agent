"""Canonical codec and public deterministic replay entrypoints."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from packages.domain.events import (
    EVENT_TYPE_BY_PAYLOAD,
    EventEnvelope,
    validate_execution_report_events,
)

from .models import (
    REDUCER_VERSION,
    REPLAY_SCHEMA_VERSION,
    ReplayResult,
    ReplayStatus,
    SnapshotRecord,
    _validate_postgres_json_strings,
)


class ReplayError(ValueError):
    """Fail-closed error for malformed ledger input and snapshots."""


def _canonical_json(value: object) -> str:
    try:
        _validate_postgres_json_strings(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReplayError("value cannot be represented by canonical PostgreSQL jsonb") from exc


def event_digest(canonical_json: str) -> str:
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _require_canonical_utc(document: dict[str, Any]) -> None:
    for name in ("observed_at", "ingested_at", "produced_at", "effective_at", "expires_at"):
        value = document.get(name)
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ReplayError(f"{name} must be a canonical UTC timestamp")


def _payload_type_for(event_type: object) -> type[object]:
    if not isinstance(event_type, str):
        raise ReplayError("event_type must be a registered string")
    for payload_type, registered_type in EVENT_TYPE_BY_PAYLOAD.items():
        if registered_type == event_type:
            return payload_type
    raise ReplayError(f"unregistered event_type: {event_type!r}")


def serialize_event(event: object) -> str:
    """Encode a registered envelope using the one canonical JSON representation."""
    if not isinstance(event, EventEnvelope):
        raise ReplayError("event must be an EventEnvelope")
    payload_type = _payload_type_for(event.event_type)
    if type(event.payload) is not payload_type:
        raise ReplayError("event type and concrete payload type do not match")
    try:
        raw_fields = {
            field_name: getattr(event, field_name)
            for field_name in event.__class__.model_fields
        }
        validated = EventEnvelope[payload_type].model_validate(raw_fields)
        validate_execution_report_events((validated,))
    except (ValidationError, ValueError) as exc:
        raise ReplayError("invalid event envelope") from exc
    document = validated.model_dump(mode="json")
    _require_canonical_utc(document)
    return _canonical_json(document)


def deserialize_event(canonical_json: str) -> EventEnvelope[object]:
    """Decode canonical JSON to the exact registered typed envelope."""
    try:
        document = json.loads(canonical_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReplayError("malformed canonical event JSON") from exc
    if not isinstance(document, dict):
        raise ReplayError("canonical event JSON must be an object")
    _require_canonical_utc(document)
    payload_type = _payload_type_for(document.get("event_type"))
    try:
        event = EventEnvelope[payload_type].model_validate_json(canonical_json)
    except ValidationError as exc:
        raise ReplayError("invalid canonical event JSON") from exc
    if serialize_event(event) != canonical_json:
        raise ReplayError("event JSON is not canonical")
    return event  # type: ignore[return-value]


def canonical_state_json(
    state: object,
    status: object,
    issues: object,
    *,
    schema_version: str = REPLAY_SCHEMA_VERSION,
    reducer_version: str = REDUCER_VERSION,
) -> str:
    if not hasattr(state, "model_dump"):
        raise ReplayError("state must be a ledger model")
    return _canonical_json({
        "issues": [issue.model_dump(mode="json") for issue in issues],
        "reducer_version": reducer_version,
        "schema_version": schema_version,
        "state": state.model_dump(mode="json"),
        "status": status.value,
    })


def snapshot_from_result(result: ReplayResult) -> SnapshotRecord:
    try:
        result = ReplayResult.model_validate(result.model_dump(mode="python"))
    except (AttributeError, ValidationError) as exc:
        raise ReplayError("invalid replay result") from exc
    snapshot = SnapshotRecord(
        schema_version=result.schema_version,
        reducer_version=result.reducer_version,
        state=result.state,
        status=result.status,
        issues=result.issues,
        canonical_state_json=result.canonical_state_json,
        state_hash=result.state_hash,
    )
    return _validate_snapshot(snapshot)


def _validate_snapshot_structure(snapshot: SnapshotRecord) -> None:
    state = snapshot.state
    if state.event_count != len(state.applied_events):
        raise ReplayError("snapshot event_count does not match applied events")
    if len({event.event_id for event in state.applied_events}) != len(state.applied_events):
        raise ReplayError("snapshot has duplicate applied event ids")
    if tuple(sorted(state.applied_events, key=lambda event: event.event_id.bytes)) != state.applied_events:
        raise ReplayError("snapshot applied events are not canonically ordered")

    if sum(entry.count for entry in state.type_counts) != state.event_count:
        raise ReplayError("snapshot type counts do not match event_count")
    if len({entry.event_type for entry in state.type_counts}) != len(state.type_counts):
        raise ReplayError("snapshot has duplicate event type counts")
    if tuple(sorted(state.type_counts, key=lambda entry: entry.event_type)) != state.type_counts:
        raise ReplayError("snapshot type counts are not canonically ordered")
    if any(entry.event_type not in EVENT_TYPE_BY_PAYLOAD.values() for entry in state.type_counts):
        raise ReplayError("snapshot has an unregistered event type count")

    if sum(stream.event_count for stream in state.streams) != state.event_count:
        raise ReplayError("snapshot stream counts do not match event_count")
    if len({stream.stream_id for stream in state.streams}) != len(state.streams):
        raise ReplayError("snapshot has duplicate stream projections")
    if tuple(sorted(state.streams, key=lambda stream: stream.stream_id.bytes)) != state.streams:
        raise ReplayError("snapshot stream projections are not canonically ordered")
    for stream in state.streams:
        if stream.event_count <= 0 or stream.last_sequence != stream.event_count or stream.last_digest is None:
            raise ReplayError("snapshot stream projection is structurally inconsistent")

    if len({issue.event_id for issue in snapshot.issues}) != len(snapshot.issues):
        raise ReplayError("snapshot has duplicate replay issues")
    if {issue.event_id for issue in snapshot.issues} & {event.event_id for event in state.applied_events}:
        raise ReplayError("snapshot issue is also marked applied")
    if tuple(sorted(snapshot.issues, key=lambda issue: (issue.stream_id.bytes, issue.sequence, issue.event_id.bytes))) != snapshot.issues:
        raise ReplayError("snapshot issues are not canonically ordered")
    for issue in snapshot.issues:
        if (issue.code.value == "SEQUENCE_GAP" and issue.sequence <= issue.expected_sequence) or (
            issue.code.value == "SEQUENCE_REGRESSION" and issue.sequence >= issue.expected_sequence
        ):
            raise ReplayError("snapshot replay issue is structurally inconsistent")
    if (snapshot.status is ReplayStatus.DEGRADED) != bool(snapshot.issues):
        raise ReplayError("snapshot status does not match replay issues")


def _validate_snapshot(snapshot: SnapshotRecord) -> SnapshotRecord:
    try:
        snapshot = SnapshotRecord.model_validate(snapshot.model_dump(mode="python"))
    except (AttributeError, ValidationError) as exc:
        raise ReplayError("invalid snapshot") from exc
    if snapshot.schema_version != REPLAY_SCHEMA_VERSION or snapshot.reducer_version != REDUCER_VERSION:
        raise ReplayError("snapshot uses unsupported replay or reducer version")
    _validate_snapshot_structure(snapshot)
    expected_json = canonical_state_json(
        snapshot.state,
        snapshot.status,
        snapshot.issues,
        schema_version=snapshot.schema_version,
        reducer_version=snapshot.reducer_version,
    )
    if snapshot.canonical_state_json != expected_json or snapshot.state_hash != event_digest(expected_json):
        raise ReplayError("snapshot state hash does not match canonical state")
    return snapshot


def replay(events: Iterable[EventEnvelope[object]], *, policy: object = None, snapshot: SnapshotRecord | None = None) -> ReplayResult:
    """Replay one immutable event set in canonical order with no I/O or clock input."""
    from .reducer import ReducerPolicy, reduce_events

    immutable_event_set = tuple(events)
    if snapshot is not None:
        snapshot = _validate_snapshot(snapshot)
        for event in immutable_event_set:
            if not isinstance(event, EventEnvelope):
                raise ReplayError("snapshot suffix event must be an EventEnvelope")
    selected_policy = ReducerPolicy.DEFAULT if policy is None else policy
    if type(selected_policy) is not ReducerPolicy:
        raise ReplayError("policy must be an exact ReducerPolicy member")
    return reduce_events(immutable_event_set, policy=selected_policy, initial=snapshot)
