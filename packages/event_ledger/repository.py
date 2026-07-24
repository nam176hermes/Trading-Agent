"""Hermetic repository boundary for append-only ledger persistence."""
from __future__ import annotations

from hashlib import sha256
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from packages.domain.events import EventEnvelope

from .models import AppendOutcome, OutboxIntent, SnapshotRecord, StoredEvent
from .reducer import ConflictingEventError, SequenceError
from .replay import ReplayError, _validate_snapshot


class EventConflictError(ConflictingEventError):
    pass


class EventLedgerRepository(Protocol):
    def append(self, event: EventEnvelope[object], outbox: OutboxIntent) -> AppendOutcome: ...
    def load_events(self) -> tuple[EventEnvelope[object], ...]: ...
    def claim_inbox(self, consumer: str, event_id: UUID) -> bool: ...
    def acknowledge_outbox(self, event_id: UUID) -> bool: ...
    def save_snapshot(self, snapshot: SnapshotRecord) -> None: ...
    def load_snapshot(self, state_hash: str) -> SnapshotRecord | None: ...


class InMemoryEventLedger:
    """A deterministic fake boundary; all state lives only in this instance."""

    def __init__(self) -> None:
        self._events: dict[UUID, StoredEvent] = {}
        self._append_idempotency: dict[UUID, str] = {}
        self._outbox: dict[UUID, OutboxIntent] = {}
        self._publications: set[UUID] = set()
        self._inbox: set[tuple[str, UUID]] = set()
        self._snapshots: dict[str, SnapshotRecord] = {}

    def _validate_delivery_order(self, record: StoredEvent) -> None:
        """Enforce contiguous arrival order only at the ingest boundary."""
        sequences = [
            stored.sequence
            for stored in self._events.values()
            if stored.stream_id == record.stream_id
        ]
        expected = max(sequences, default=0) + 1
        if record.sequence != expected:
            raise SequenceError(
                f"stream {record.stream_id} expected sequence {expected}, got {record.sequence}"
            )

    def _append_request_digest(self, record: StoredEvent, outbox: OutboxIntent) -> str:
        parts = (
            record.event_id.bytes,
            record.stream_id.bytes,
            str(record.sequence).encode("ascii"),
            record.event_type.encode("utf-8"),
            record.canonical_json.encode("utf-8"),
            outbox.topic.encode("utf-8"),
            outbox.payload_json.encode("utf-8"),
        )
        h = sha256()
        for part in parts:
            h.update(len(part).to_bytes(8, "big"))
            h.update(part)
        return h.hexdigest()

    def append(self, event: EventEnvelope[object], outbox: OutboxIntent) -> AppendOutcome:
        record = StoredEvent.from_envelope(event)
        try:
            outbox = OutboxIntent.model_validate(outbox.model_dump(mode="python"))
        except (AttributeError, ValidationError) as exc:
            raise EventConflictError("invalid outbox intent") from exc
        if outbox.event_id != record.event_id:
            raise EventConflictError("outbox intent must reference the appended event")
        request_digest = self._append_request_digest(record, outbox)
        prior_digest = self._append_idempotency.get(record.event_id)
        if prior_digest is not None:
            if prior_digest == request_digest:
                return AppendOutcome(event_id=record.event_id, inserted=False)
            raise EventConflictError(f"conflicting content for event_id {record.event_id}")
        if record.event_id in self._events:
            raise EventConflictError(f"conflicting content for event_id {record.event_id}")
        self._validate_delivery_order(record)
        self._events[record.event_id] = record
        self._append_idempotency[record.event_id] = request_digest
        self._outbox[record.event_id] = outbox
        return AppendOutcome(event_id=record.event_id, inserted=True)

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        from .replay import deserialize_event
        return tuple(deserialize_event(record.canonical_json) for record in sorted(self._events.values(), key=lambda item: (item.stream_id.bytes, item.sequence, item.event_id.bytes)))

    def load_outbox(self) -> tuple[OutboxIntent, ...]:
        return tuple(self._outbox[event_id] for event_id in sorted(self._outbox, key=lambda value: value.bytes))

    def acknowledge_outbox(self, event_id: UUID) -> bool:
        if event_id in self._publications:
            return False
        if event_id not in self._outbox:
            raise EventConflictError(f"event_id {event_id} has no pending outbox work")
        self._publications.add(event_id)
        del self._outbox[event_id]
        return True

    def claim_inbox(self, consumer: str, event_id: UUID) -> bool:
        if type(consumer) is not str or not consumer or len(consumer) > 256:
            raise ReplayError("consumer must be bounded non-empty text")
        if event_id not in self._events:
            raise EventConflictError(f"event_id {event_id} does not exist in the ledger")
        claim = (consumer, event_id)
        if claim in self._inbox:
            return False
        self._inbox.add(claim)
        return True

    def save_snapshot(self, snapshot: SnapshotRecord) -> None:
        try:
            snapshot = _validate_snapshot(snapshot)
        except ReplayError as exc:
            raise EventConflictError("invalid snapshot") from exc
        key = snapshot.state_hash
        prior = self._snapshots.get(key)
        if prior is not None:
            if prior.canonical_state_json == snapshot.canonical_state_json:
                return
            raise EventConflictError(f"conflicting content for snapshot hash {snapshot.state_hash}")
        self._snapshots[key] = snapshot

    def load_snapshot(self, state_hash: str) -> SnapshotRecord | None:
        if type(state_hash) is not str or len(state_hash) != 64 or any(character not in "0123456789abcdef" for character in state_hash):
            raise ReplayError("snapshot state_hash must be a lowercase sha256 hex digest")
        snapshot = self._snapshots.get(state_hash)
        if snapshot is not None:
            return _validate_snapshot(snapshot)
        return None


class PostgresLedgerSql:
    """Source-only SQL contract; callers execute it in a transaction.

    The function owns the transaction-scoped stream lock and all append
    constraints, so the single statement is safe without client-side batches.
    """

    APPEND_EVENT_AND_OUTBOX = """SELECT public.append_domain_event(
    %(event_id)s, %(stream_id)s, %(sequence)s, %(event_type)s,
    %(canonical_event_text)s, %(topic)s, %(payload_json)s
);"""
    LOAD_EVENTS = "SELECT canonical_event_text FROM public.domain_events ORDER BY stream_id, sequence, event_id"
    SAVE_SNAPSHOT = """SELECT public.save_domain_snapshot(
    %(canonical_state_json)s
);"""
    LOAD_SNAPSHOT = """SELECT canonical_state_json, replay_schema_version, reducer_version, state_hash
FROM public.aggregate_snapshots
WHERE state_hash = %(state_hash)s;"""
    CLAIM_INBOX = """WITH inserted AS (
    INSERT INTO public.consumer_inbox (consumer, event_id)
    VALUES (%(consumer)s, %(event_id)s)
    ON CONFLICT DO NOTHING
    RETURNING 1
)
SELECT EXISTS (SELECT 1 FROM inserted) AS claimed;"""
    ACKNOWLEDGE_OUTBOX = """SELECT public.acknowledge_domain_publication(
    %(event_id)s
);"""
