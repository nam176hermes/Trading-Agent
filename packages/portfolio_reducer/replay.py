"""Strict canonical portfolio replay and hash-bound snapshot records."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from packages.domain.events import EventEnvelope
from packages.domain.orders import FillReportStatus
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
from .reducer import (
    apply_portfolio_event,
    derive_account_snapshot,
    reduce_portfolio_events,
    _payload_digest,
    _sum,
    validate_execution_effect,
)


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
    reconciliation = state.reconciliation
    if reconciliation is not None and (
        reconciliation.account_id != account_id
        or reconciliation.snapshot.account_id != account_id
    ):
        raise PortfolioReplayError(
            "snapshot reconciliation account does not match portfolio account"
        )
    applied = {item.event_id: item.digest for item in state.applied_events}
    identity_event_ids: set[object] = set()
    identity_sequences: set[int] = set()
    for identities in (
        state.execution_identities,
        state.funding_identities,
        state.reconciliation_identities,
    ):
        for identity in identities:
            if applied.get(identity.event_id) != identity.event_digest:
                raise PortfolioReplayError(
                    "snapshot business identity is not bound to an applied event"
                )
            if identity.stream_id != state.cursor[0].stream_id:
                raise PortfolioReplayError(
                    "snapshot business identity stream does not match cursor"
                )
            if identity.sequence > state.cursor[0].sequence:
                raise PortfolioReplayError(
                    "snapshot business identity sequence exceeds cursor"
                )
            if (
                identity.event_id in identity_event_ids
                or identity.sequence in identity_sequences
            ):
                raise PortfolioReplayError("snapshot business identity lineage is duplicated")
            identity_event_ids.add(identity.event_id)
            identity_sequences.add(identity.sequence)
    execution_identities = {
        item.identity_id: item for item in state.execution_identities
    }
    latest_reconciliation = (
        max(state.reconciliation_identities, key=lambda item: item.sequence)
        if state.reconciliation_identities
        else None
    )
    if (state.reconciliation is None) != (latest_reconciliation is None):
        raise PortfolioReplayError("snapshot reconciliation identity is incomplete")
    if (
        state.reconciliation is not None
        and latest_reconciliation is not None
        and latest_reconciliation.payload_digest
        != _payload_digest(state.reconciliation)
    ):
        raise PortfolioReplayError("snapshot reconciliation identity does not match state")
    effect_event_ids: set[object] = set()
    logical_sequences: set[int] = set()
    for effect in state.active_effects:
        if effect.account_id != account_id or effect.entry.account_id != account_id:
            raise PortfolioReplayError("snapshot effect account does not match portfolio account")
        if effect.logical_sequence > state.cursor[0].sequence:
            raise PortfolioReplayError("snapshot effect sequence exceeds cursor")
        source = _canonical_event(effect.source_event)
        if source != effect.source_event or source.payload != effect.entry:
            raise PortfolioReplayError("snapshot effect source event is invalid")
        source_digest = event_digest(serialize_event(source))
        if applied.get(source.event_id) != source_digest:
            raise PortfolioReplayError("snapshot effect is not bound to an applied event")
        if source.stream_id != state.cursor[0].stream_id:
            raise PortfolioReplayError("snapshot effect stream does not match cursor")
        if source.sequence > state.cursor[0].sequence:
            raise PortfolioReplayError("snapshot effect source sequence exceeds cursor")
        identity = execution_identities.get(effect.execution_id)
        if (
            identity is None
            or identity.event_id != source.event_id
            or identity.event_digest != source_digest
            or identity.payload_digest != _payload_digest(effect.entry)
            or identity.sequence != source.sequence
        ):
            raise PortfolioReplayError(
                "snapshot effect is not bound to its execution identity"
            )
        if latest_reconciliation is not None and (
            source.sequence <= latest_reconciliation.sequence
            or effect.logical_sequence <= latest_reconciliation.sequence
        ):
            raise PortfolioReplayError(
                "snapshot effect predates the latest reconciliation"
            )
        if source.event_id in effect_event_ids or effect.logical_sequence in logical_sequences:
            raise PortfolioReplayError("snapshot effects have duplicate lineage")
        effect_event_ids.add(source.event_id)
        logical_sequences.add(effect.logical_sequence)
        if source.payload.fill.status in (
            FillReportStatus.PARTIALLY_FILLED,
            FillReportStatus.FILLED,
        ) and effect.logical_sequence != source.sequence:
            raise PortfolioReplayError("snapshot effect logical sequence does not match source")
        if (
            source.payload.fill.status is FillReportStatus.CORRECTION
            and effect.logical_sequence >= source.sequence
        ):
            raise PortfolioReplayError("snapshot correction effect lineage is invalid")
        validate_execution_effect(effect)
    effects_by_position: dict[tuple[str, str], list] = {}
    for effect in state.active_effects:
        effects_by_position.setdefault(effect.position_key, []).append(effect)
    for position_key, effects in effects_by_position.items():
        ordered = sorted(effects, key=lambda item: item.logical_sequence)
        expected_quantity = ordered[0].quantity_before.value
        expected_average = ordered[0].average_before
        for effect in ordered:
            if (
                effect.quantity_before.value != expected_quantity
                or effect.average_before != expected_average
            ):
                raise PortfolioReplayError("snapshot effect lineage is not contiguous")
            expected_quantity = _sum(
                expected_quantity,
                effect.quantity_delta.value,
                field="snapshot effect lineage quantity",
            )
            expected_average = effect.average_after
        position = next(
            (
                item
                for item in state.snapshot.positions
                if (item.strategy_id, item.instrument.canonical) == position_key
            ),
            None,
        )
        if (
            position is None
            or position.quantity.value != expected_quantity
            or position.average_entry_price != expected_average
        ):
            raise PortfolioReplayError("snapshot effect lineage does not match current position")
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
    except (PortfolioReplayError, ValidationError, ValueError) as exc:
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
