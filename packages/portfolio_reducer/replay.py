"""Strict canonical portfolio replay and hash-bound snapshot records."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from packages.domain.events import EventEnvelope
from packages.domain.portfolio_events import (
    PortfolioConversionEntry,
    PortfolioFillEntry,
    PortfolioFundingEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PortfolioReconciliationEntry,
    PortfolioValuationRateEntry,
)
from packages.event_ledger.replay import (
    ReplayError,
    _canonical_json,
    deserialize_event,
    event_digest,
    serialize_event,
)

from .models import (
    PORTFOLIO_REDUCER_VERSION,
    PORTFOLIO_REPLAY_SCHEMA_VERSION,
    PortfolioReplayError,
    PortfolioReplayResult,
    PortfolioReplayState,
    PortfolioSnapshotRecord,
)
from .reducer import apply_portfolio_event, derive_account_snapshot, reduce_portfolio_events


_PORTFOLIO_PAYLOAD_TYPES = (
    PortfolioOpeningEntry,
    PortfolioFillEntry,
    PortfolioMarkEntry,
    PortfolioFundingEntry,
    PortfolioConversionEntry,
    PortfolioValuationRateEntry,
    PortfolioReconciliationEntry,
)


def _canonical_event(event: object) -> EventEnvelope[object]:
    """Round-trip the generic ledger codec before portfolio-specific validation."""

    try:
        canonical = deserialize_event(serialize_event(event))
    except (ReplayError, ValueError, ValidationError) as exc:
        raise PortfolioReplayError("portfolio event is not a canonical ledger envelope") from exc
    if type(canonical.payload) not in _PORTFOLIO_PAYLOAD_TYPES:
        raise PortfolioReplayError("event payload is not supported by portfolio accounting")
    return canonical


def _canonical_events(events: Iterable[object]) -> tuple[EventEnvelope[object], ...]:
    try:
        batch = tuple(events)
    except TypeError as exc:
        raise PortfolioReplayError("events must be iterable") from exc
    canonical = tuple(_canonical_event(event) for event in batch)
    ordered = tuple(
        sorted(
            canonical,
            key=lambda event: (event.stream_id.bytes, event.sequence, event.event_id.bytes),
        )
    )
    if canonical != ordered:
        raise PortfolioReplayError("portfolio events must be supplied in canonical ordered sequence")
    return canonical


def _canonical_state_document(
    state: PortfolioReplayState,
    canonical_snapshot: object,
    cursor: object,
    *,
    schema_version: str,
    reducer_version: str,
) -> str:
    try:
        return _canonical_json(
            {
                "schema_version": schema_version,
                "reducer_version": reducer_version,
                "state": state.model_dump(mode="json"),
                "canonical_snapshot": canonical_snapshot.model_dump(mode="json"),
                "cursor": [item.model_dump(mode="json") for item in cursor],
            }
        )
    except (AttributeError, ReplayError, ValueError) as exc:
        raise PortfolioReplayError("portfolio replay state cannot be canonically represented") from exc


def _validated_state(state: object) -> PortfolioReplayState:
    try:
        state = PortfolioReplayState.model_validate(state)
    except (AttributeError, ValidationError, ValueError) as exc:
        raise PortfolioReplayError("snapshot state is invalid") from exc
    if len(state.cursor) != 1:
        raise PortfolioReplayError("snapshot cursor must contain exactly one portfolio stream")
    if len(state.applied_events) != state.cursor[0].sequence:
        raise PortfolioReplayError("snapshot applied event count does not match cursor")
    account_id = state.snapshot.account_id
    for effect in state.active_effects:
        if effect.account_id != account_id or effect.entry.account_id != account_id:
            raise PortfolioReplayError("snapshot effect account does not match portfolio account")
        if effect.logical_sequence > state.cursor[0].sequence:
            raise PortfolioReplayError("snapshot effect sequence exceeds cursor")
    reconciliation = state.reconciliation
    if reconciliation is not None and (
        reconciliation.account_id != account_id
        or reconciliation.snapshot.account_id != account_id
    ):
        raise PortfolioReplayError("snapshot reconciliation account does not match portfolio account")
    return state


def _validate_document(document: object, *, record: bool) -> PortfolioReplayResult | PortfolioSnapshotRecord:
    model = PortfolioSnapshotRecord if record else PortfolioReplayResult
    try:
        document = model.model_validate(document)
    except (AttributeError, ValidationError, ValueError) as exc:
        raise PortfolioReplayError("portfolio snapshot record is invalid") from exc
    if (
        document.schema_version != PORTFOLIO_REPLAY_SCHEMA_VERSION
        or document.reducer_version != PORTFOLIO_REDUCER_VERSION
    ):
        raise PortfolioReplayError("snapshot uses unsupported replay or reducer version")
    state = _validated_state(document.state)
    if document.cursor != state.cursor:
        raise PortfolioReplayError("snapshot cursor does not match replay state")
    if document.canonical_snapshot.account_id != state.snapshot.account_id:
        raise PortfolioReplayError("snapshot canonical account does not match replay state")
    try:
        expected_snapshot = derive_account_snapshot(state, state.snapshot.observed_at)
    except PortfolioReplayError as exc:
        raise PortfolioReplayError("snapshot canonical portfolio state is invalid") from exc
    expected_json = _canonical_state_document(
        state,
        expected_snapshot,
        state.cursor,
        schema_version=document.schema_version,
        reducer_version=document.reducer_version,
    )
    if document.canonical_state_json != expected_json or document.state_hash != event_digest(expected_json):
        raise PortfolioReplayError("snapshot state hash does not match canonical state")
    if document.canonical_snapshot != expected_snapshot:
        raise PortfolioReplayError("snapshot canonical portfolio state does not match replay state")
    return document


def _result(state: PortfolioReplayState) -> PortfolioReplayResult:
    state = _validated_state(state)
    canonical_snapshot = derive_account_snapshot(state, state.snapshot.observed_at)
    canonical_json = _canonical_state_document(
        state,
        canonical_snapshot,
        state.cursor,
        schema_version=PORTFOLIO_REPLAY_SCHEMA_VERSION,
        reducer_version=PORTFOLIO_REDUCER_VERSION,
    )
    return PortfolioReplayResult(
        schema_version=PORTFOLIO_REPLAY_SCHEMA_VERSION,
        reducer_version=PORTFOLIO_REDUCER_VERSION,
        state=state,
        canonical_snapshot=canonical_snapshot,
        cursor=state.cursor,
        canonical_state_json=canonical_json,
        state_hash=event_digest(canonical_json),
    )


def _validate_full_history(events: tuple[EventEnvelope[object], ...]) -> None:
    if not events:
        raise PortfolioReplayError("portfolio event history requires an opening entry")
    first = events[0]
    if type(first.payload) is not PortfolioOpeningEntry:
        raise PortfolioReplayError("first portfolio event must be an opening entry")
    account_id = first.payload.account_id
    stream_id = first.stream_id
    seen: dict[object, str] = {}
    for expected_sequence, event in enumerate(events, start=1):
        digest = event_digest(serialize_event(event))
        previous = seen.get(event.event_id)
        if previous is not None:
            if previous != digest:
                raise PortfolioReplayError("conflicting event identity")
            raise PortfolioReplayError("duplicate event identity")
        seen[event.event_id] = digest
        if event.stream_id != stream_id:
            raise PortfolioReplayError("portfolio event stream does not match opening stream")
        if event.sequence != expected_sequence:
            raise PortfolioReplayError(
                f"portfolio sequence expected {expected_sequence}, got {event.sequence}"
            )
        if event.payload.account_id != account_id:
            raise PortfolioReplayError("portfolio event account does not match opening account")
        if expected_sequence > 1 and type(event.payload) is PortfolioOpeningEntry:
            raise PortfolioReplayError("portfolio opening may only appear once")


def _validate_tail(
    events: tuple[EventEnvelope[object], ...], snapshot: PortfolioSnapshotRecord
) -> None:
    state = snapshot.state
    cursor = state.cursor[0]
    applied = {item.event_id: item.digest for item in state.applied_events}
    seen: dict[object, str] = {}
    expected_sequence = cursor.sequence + 1
    for event in events:
        digest = event_digest(serialize_event(event))
        known = applied.get(event.event_id)
        if known is not None and known != digest:
            raise PortfolioReplayError("conflicting event identity")
        if event.stream_id != cursor.stream_id:
            raise PortfolioReplayError("tail event stream does not match snapshot cursor")
        if event.sequence <= cursor.sequence:
            raise PortfolioReplayError("tail event is not newer than snapshot cursor")
        if known is not None:
            raise PortfolioReplayError("tail event duplicates snapshot history")
        previous = seen.get(event.event_id)
        if previous is not None:
            if previous != digest:
                raise PortfolioReplayError("conflicting event identity")
            raise PortfolioReplayError("duplicate tail event identity")
        seen[event.event_id] = digest
        if event.sequence != expected_sequence:
            raise PortfolioReplayError(
                f"tail sequence expected {expected_sequence}, got {event.sequence}"
            )
        if event.payload.account_id != state.snapshot.account_id:
            raise PortfolioReplayError("tail event account does not match portfolio account")
        if type(event.payload) is PortfolioOpeningEntry:
            raise PortfolioReplayError("portfolio opening may not appear in snapshot tail")
        expected_sequence += 1


def replay_portfolio(
    events: Iterable[object], *, snapshot: PortfolioSnapshotRecord | None = None
) -> PortfolioReplayResult:
    """Replay one strictly ordered portfolio stream without persistence or external input."""

    batch = _canonical_events(events)
    if snapshot is None:
        _validate_full_history(batch)
        try:
            return _result(reduce_portfolio_events(batch))
        except PortfolioReplayError:
            raise
        except (ValueError, ValidationError) as exc:
            raise PortfolioReplayError("portfolio replay failed") from exc
    validated_snapshot = _validate_document(snapshot, record=True)
    assert isinstance(validated_snapshot, PortfolioSnapshotRecord)
    _validate_tail(batch, validated_snapshot)
    state = validated_snapshot.state
    try:
        for event in batch:
            state = apply_portfolio_event(state, event)
        return _result(state)
    except PortfolioReplayError:
        raise
    except (ValueError, ValidationError) as exc:
        raise PortfolioReplayError("portfolio replay failed") from exc


def snapshot_from_portfolio_result(result: PortfolioReplayResult) -> PortfolioSnapshotRecord:
    """Return a fully revalidated, hash-bound cache record for strict tail replay."""

    validated = _validate_document(result, record=False)
    assert isinstance(validated, PortfolioReplayResult)
    record = PortfolioSnapshotRecord(
        schema_version=validated.schema_version,
        reducer_version=validated.reducer_version,
        state=validated.state,
        canonical_snapshot=validated.canonical_snapshot,
        cursor=validated.cursor,
        canonical_state_json=validated.canonical_state_json,
        state_hash=validated.state_hash,
    )
    validated_record = _validate_document(record, record=True)
    assert isinstance(validated_record, PortfolioSnapshotRecord)
    return validated_record
