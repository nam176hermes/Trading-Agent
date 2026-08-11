"""Injected PostgreSQL adapter for the general domain event ledger contract."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from datetime import datetime
import json
import re
from typing import Protocol
from uuid import UUID

from psycopg import Error as PostgresError
from pydantic import BaseModel, ValidationError

from packages.domain.events import EventEnvelope

from .models import (
    AggregateReplayState,
    AppendOutcome,
    AppliedEvent,
    EventTypeCount,
    OutboxIntent,
    ReplayIssue,
    ReplayIssueCode,
    ReplayStatus,
    SnapshotRecord,
    StoredEvent,
    StreamProjection,
)
from .reducer import SequenceError
from .replay import ReplayError, _validate_snapshot, deserialize_event
from .repository import EventConflictError, PostgresLedgerSql


_SEQUENCE_CONFLICT = re.compile(
    r"^expected sequence [1-9][0-9]* for stream "
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}, "
    r"got [1-9][0-9]*$",
    re.ASCII,
)


class _Cursor(Protocol):
    def fetchone(self) -> object: ...

    def fetchall(self) -> list[object]: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[object]: ...

    def execute(self, statement: str, params: Mapping[str, object]) -> _Cursor: ...


class _Pool(Protocol):
    def connection(self) -> AbstractContextManager[_Connection]: ...


class PostgresEventLedgerRepository:
    """Durable adapter with an injected pool and repository-owned write transactions.

    One connection is borrowed per operation. Reads issue one query without opening
    an explicit transaction; each mutation owns one transaction around one existing
    database-authority statement. Connections and their lifecycle remain pool-owned.
    """

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    @staticmethod
    def _row_value(
        row: object,
        name: str,
        index: int,
        *,
        width: int,
        error: str,
    ) -> object:
        try:
            if type(row) is dict:
                keys = tuple(dict.__iter__(row))
                if len(keys) != width or any(type(key) is not str for key in keys):
                    raise ReplayError(error)
                return dict.__getitem__(row, name)
            if type(row) is tuple and len(row) == width:
                return tuple.__getitem__(row, index)
        except KeyError as exc:
            raise ReplayError(error) from exc
        raise ReplayError(error)

    @staticmethod
    def _exact_model_state(
        value: object,
        expected_type: type[BaseModel],
        *,
        allow_event_generic: bool = False,
    ) -> dict[str, object]:
        value_type = type(value)
        if allow_event_generic:
            try:
                metadata = value_type.__pydantic_generic_metadata__
                origin = metadata.get("origin")
                arguments = metadata.get("args", ())
                exact_event_type = (
                    EventEnvelope
                    if value_type is EventEnvelope
                    else EventEnvelope[arguments[0]]
                    if origin is EventEnvelope and len(arguments) == 1
                    else None
                )
            except (AttributeError, IndexError, TypeError) as exc:
                raise ReplayError("event model type is invalid") from exc
            if value_type is not exact_event_type:
                raise ReplayError("event must use an exact EventEnvelope type")
        elif value_type is not expected_type:
            raise ReplayError(
                f"value must be an exact {expected_type.__name__}"
            )
        try:
            state = object.__getattribute__(value, "__dict__")
            fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
            extra = object.__getattribute__(value, "__pydantic_extra__")
        except AttributeError as exc:
            raise ReplayError("model state is incomplete") from exc
        if type(state) is not dict or type(fields_set) is not set:
            raise ReplayError("model state must use concrete containers")
        state_names = tuple(dict.__iter__(state))
        fields_set_names = tuple(set.__iter__(fields_set))
        if any(type(name) is not str for name in state_names) or any(
            type(name) is not str for name in fields_set_names
        ):
            raise ReplayError("model field names must be concrete strings")
        declared_names = expected_type.model_fields
        if (
            len(state_names) != len(declared_names)
            or any(name not in declared_names for name in state_names)
            or any(name not in state for name in declared_names)
            or any(name not in declared_names for name in fields_set_names)
        ):
            raise ReplayError("model fields must be exact")
        if extra is not None and (type(extra) is not dict or dict.__len__(extra) != 0):
            raise ReplayError("model extras must be empty")
        return dict.copy(state)

    @classmethod
    def _validated_event(cls, event: object) -> StoredEvent:
        state = cls._exact_model_state(
            event,
            EventEnvelope,
            allow_event_generic=True,
        )
        for name in (
            "event_id",
            "stream_id",
            "correlation_id",
            "causation_id",
            "trace_id",
        ):
            if type(dict.__getitem__(state, name)) is not UUID:
                raise ReplayError(f"event {name} must be an exact UUID")
        for name in ("event_type", "schema_version", "source"):
            if type(dict.__getitem__(state, name)) is not str:
                raise ReplayError(f"event {name} must be a concrete string")
        if type(dict.__getitem__(state, "sequence")) is not int:
            raise ReplayError("event sequence must be a concrete integer")
        for name in (
            "observed_at",
            "ingested_at",
            "produced_at",
            "effective_at",
            "expires_at",
        ):
            if type(dict.__getitem__(state, name)) is not datetime:
                raise ReplayError(f"event {name} must be a concrete datetime")
        return StoredEvent.from_envelope(event)

    @classmethod
    def _validated_outbox(cls, outbox: object) -> OutboxIntent:
        try:
            state = cls._exact_model_state(outbox, OutboxIntent)
            if type(dict.__getitem__(state, "event_id")) is not UUID:
                raise ReplayError("outbox event_id must be an exact UUID")
            for name in ("topic", "payload_json"):
                if type(dict.__getitem__(state, name)) is not str:
                    raise ReplayError(f"outbox {name} must be a concrete string")
            return OutboxIntent.model_validate(state)
        except (ReplayError, ValidationError) as exc:
            raise EventConflictError("invalid outbox intent") from exc

    @classmethod
    def _boolean_outcome(cls, row: object, name: str, error: str) -> bool:
        value = cls._row_value(row, name, 0, width=1, error=error)
        if type(value) is not bool:
            raise ReplayError(error)
        return value

    @classmethod
    def _validated_snapshot(cls, snapshot: object) -> SnapshotRecord:
        root = cls._exact_model_state(snapshot, SnapshotRecord)
        for name in (
            "schema_version",
            "reducer_version",
            "canonical_state_json",
            "state_hash",
        ):
            if type(dict.__getitem__(root, name)) is not str:
                raise ReplayError(f"snapshot {name} must be concrete text")
        if type(dict.__getitem__(root, "status")) is not ReplayStatus:
            raise ReplayError("snapshot status must be an exact ReplayStatus")

        state = dict.__getitem__(root, "state")
        state_fields = cls._exact_model_state(state, AggregateReplayState)
        if type(dict.__getitem__(state_fields, "event_count")) is not int:
            raise ReplayError("snapshot event_count must be a concrete integer")

        type_counts = dict.__getitem__(state_fields, "type_counts")
        if type(type_counts) is not tuple:
            raise ReplayError("snapshot type_counts must be a concrete tuple")
        for entry in tuple.__iter__(type_counts):
            fields = cls._exact_model_state(entry, EventTypeCount)
            if type(dict.__getitem__(fields, "event_type")) is not str:
                raise ReplayError("snapshot event_type must be concrete text")
            if type(dict.__getitem__(fields, "count")) is not int:
                raise ReplayError("snapshot type count must be a concrete integer")

        streams = dict.__getitem__(state_fields, "streams")
        if type(streams) is not tuple:
            raise ReplayError("snapshot streams must be a concrete tuple")
        for stream in tuple.__iter__(streams):
            fields = cls._exact_model_state(stream, StreamProjection)
            if type(dict.__getitem__(fields, "stream_id")) is not UUID:
                raise ReplayError("snapshot stream_id must be an exact UUID")
            for name in ("event_count", "last_sequence"):
                if type(dict.__getitem__(fields, name)) is not int:
                    raise ReplayError(f"snapshot {name} must be a concrete integer")
            digest = dict.__getitem__(fields, "last_digest")
            if digest is not None and type(digest) is not str:
                raise ReplayError("snapshot last_digest must be concrete text")

        applied_events = dict.__getitem__(state_fields, "applied_events")
        if type(applied_events) is not tuple:
            raise ReplayError("snapshot applied_events must be a concrete tuple")
        for event in tuple.__iter__(applied_events):
            fields = cls._exact_model_state(event, AppliedEvent)
            if type(dict.__getitem__(fields, "event_id")) is not UUID:
                raise ReplayError("snapshot applied event_id must be an exact UUID")
            if type(dict.__getitem__(fields, "digest")) is not str:
                raise ReplayError("snapshot applied digest must be concrete text")

        issues = dict.__getitem__(root, "issues")
        if type(issues) is not tuple:
            raise ReplayError("snapshot issues must be a concrete tuple")
        for issue in tuple.__iter__(issues):
            fields = cls._exact_model_state(issue, ReplayIssue)
            if type(dict.__getitem__(fields, "code")) is not ReplayIssueCode:
                raise ReplayError("snapshot issue code must be exact")
            for name in ("stream_id", "event_id"):
                if type(dict.__getitem__(fields, name)) is not UUID:
                    raise ReplayError(f"snapshot issue {name} must be an exact UUID")
            for name in ("sequence", "expected_sequence"):
                if type(dict.__getitem__(fields, name)) is not int:
                    raise ReplayError(
                        f"snapshot issue {name} must be a concrete integer"
                    )
            if type(dict.__getitem__(fields, "digest")) is not str:
                raise ReplayError("snapshot issue digest must be concrete text")

        return _validate_snapshot(snapshot)

    @staticmethod
    def _validated_event_id(event_id: object) -> UUID:
        if type(event_id) is not UUID:
            raise ReplayError("event_id must be an exact UUID")
        return event_id

    @staticmethod
    def _raise_append_database_error(exc: PostgresError) -> None:
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate == "23505":
            raise EventConflictError("durable event identity conflict") from exc
        if sqlstate == "23514":
            primary = getattr(getattr(exc, "diag", None), "message_primary", None)
            if type(primary) is str and _SEQUENCE_CONFLICT.fullmatch(primary) is not None:
                raise SequenceError(
                    "durable ledger rejected the proposed sequence"
                ) from exc
            raise ReplayError("database rejected canonical append input") from exc
        raise exc

    @staticmethod
    def _raise_mutation_database_error(
        exc: PostgresError,
        *,
        operation: str,
        sqlstates: frozenset[str],
    ) -> None:
        if getattr(exc, "sqlstate", None) in sqlstates:
            raise EventConflictError(f"durable {operation} conflict") from exc
        raise exc

    def append(
        self,
        event: EventEnvelope[object],
        outbox: OutboxIntent,
    ) -> AppendOutcome:
        record = self._validated_event(event)
        validated_outbox = self._validated_outbox(outbox)
        if validated_outbox.event_id != record.event_id:
            raise EventConflictError(
                "outbox intent must reference the appended event"
            )
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        PostgresLedgerSql.APPEND_EVENT_AND_OUTBOX,
                        {
                            "event_id": record.event_id,
                            "stream_id": record.stream_id,
                            "sequence": record.sequence,
                            "event_type": record.event_type,
                            "canonical_event_text": record.canonical_json,
                            "topic": validated_outbox.topic,
                            "payload_json": validated_outbox.payload_json,
                        },
                    ).fetchone()
                    inserted = self._boolean_outcome(
                        row,
                        "append_domain_event",
                        "database returned an invalid append outcome",
                    )
        except PostgresError as exc:
            self._raise_append_database_error(exc)
            raise AssertionError("unreachable")
        return AppendOutcome(event_id=record.event_id, inserted=inserted)

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        with self._pool.connection() as connection:
            rows = connection.execute(PostgresLedgerSql.LOAD_EVENTS, {}).fetchall()
        events: list[EventEnvelope[object]] = []
        for row in rows:
            canonical_text = self._row_value(
                row,
                "canonical_event_text",
                0,
                width=1,
                error="database returned an invalid stored event row",
            )
            if type(canonical_text) is not str:
                raise ReplayError("database returned invalid stored event bytes")
            events.append(deserialize_event(canonical_text))
        return tuple(events)

    def load_stream_events(
        self,
        stream_id: UUID,
    ) -> tuple[EventEnvelope[object], ...]:
        if type(stream_id) is not UUID:
            raise ReplayError("stream_id must be an exact UUID")
        with self._pool.connection() as connection:
            rows = connection.execute(
                PostgresLedgerSql.LOAD_STREAM_EVENTS,
                {"stream_id": stream_id},
            ).fetchall()
        events: list[EventEnvelope[object]] = []
        for row in rows:
            canonical_text = self._row_value(
                row,
                "canonical_event_text",
                0,
                width=1,
                error="database returned an invalid stored event row",
            )
            if type(canonical_text) is not str:
                raise ReplayError("database returned invalid stored event bytes")
            event = deserialize_event(canonical_text)
            if type(event.stream_id) is not UUID or event.stream_id != stream_id:
                raise ReplayError("stored event does not belong to requested stream")
            events.append(event)
        return tuple(events)

    def claim_inbox(self, consumer: str, event_id: UUID) -> bool:
        if type(consumer) is not str or not consumer or len(consumer) > 256:
            raise ReplayError("consumer must be bounded non-empty text")
        validated_event_id = self._validated_event_id(event_id)
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        PostgresLedgerSql.CLAIM_INBOX,
                        {"consumer": consumer, "event_id": validated_event_id},
                    ).fetchone()
                    return self._boolean_outcome(
                        row,
                        "claimed",
                        "database returned an invalid inbox claim outcome",
                    )
        except PostgresError as exc:
            self._raise_mutation_database_error(
                exc,
                operation="inbox claim",
                sqlstates=frozenset({"23503", "23505", "23514"}),
            )
            raise AssertionError("unreachable")

    def acknowledge_outbox(self, event_id: UUID) -> bool:
        validated_event_id = self._validated_event_id(event_id)
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        PostgresLedgerSql.ACKNOWLEDGE_OUTBOX,
                        {"event_id": validated_event_id},
                    ).fetchone()
                    return self._boolean_outcome(
                        row,
                        "acknowledge_domain_publication",
                        "database returned an invalid outbox acknowledgement outcome",
                    )
        except PostgresError as exc:
            self._raise_mutation_database_error(
                exc,
                operation="outbox acknowledgement",
                sqlstates=frozenset({"23503", "23505", "23514"}),
            )
            raise AssertionError("unreachable")

    def save_snapshot(self, snapshot: SnapshotRecord) -> None:
        try:
            validated = self._validated_snapshot(snapshot)
        except ReplayError as exc:
            raise EventConflictError("invalid snapshot") from exc
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        PostgresLedgerSql.SAVE_SNAPSHOT,
                        {"canonical_state_json": validated.canonical_state_json},
                    ).fetchone()
                    self._boolean_outcome(
                        row,
                        "save_domain_snapshot",
                        "database returned an invalid snapshot save outcome",
                    )
        except PostgresError as exc:
            self._raise_mutation_database_error(
                exc,
                operation="snapshot save",
                sqlstates=frozenset({"22023", "23505", "23514"}),
            )
            raise AssertionError("unreachable")

    @staticmethod
    def _validated_state_hash(state_hash: object) -> str:
        if (
            type(state_hash) is not str
            or len(state_hash) != 64
            or any(character not in "0123456789abcdef" for character in state_hash)
        ):
            raise ReplayError(
                "snapshot state_hash must be a lowercase sha256 hex digest"
            )
        return state_hash

    def load_snapshot(self, state_hash: str) -> SnapshotRecord | None:
        validated_hash = self._validated_state_hash(state_hash)
        with self._pool.connection() as connection:
            row = connection.execute(
                PostgresLedgerSql.LOAD_SNAPSHOT,
                {"state_hash": validated_hash},
            ).fetchone()
        if row is None:
            return None
        row_error = "database returned an invalid stored snapshot row"
        canonical_text = self._row_value(
            row, "canonical_state_json", 0, width=4, error=row_error
        )
        replay_version = self._row_value(
            row, "replay_schema_version", 1, width=4, error=row_error
        )
        reducer_version = self._row_value(
            row, "reducer_version", 2, width=4, error=row_error
        )
        stored_hash = self._row_value(
            row, "state_hash", 3, width=4, error=row_error
        )
        if any(
            type(value) is not str
            for value in (
                canonical_text,
                replay_version,
                reducer_version,
                stored_hash,
            )
        ):
            raise ReplayError(row_error)
        if stored_hash != validated_hash:
            raise ReplayError("stored snapshot hash differs from lookup identity")
        try:
            document = json.loads(canonical_text)
            if type(document) is not dict:
                raise TypeError("snapshot document must be an object")
            snapshot = SnapshotRecord.model_validate_json(
                json.dumps(
                    {
                        **document,
                        "canonical_state_json": canonical_text,
                        "state_hash": stored_hash,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            snapshot = _validate_snapshot(snapshot)
        except ReplayError:
            raise
        except (TypeError, ValidationError, ValueError) as exc:
            raise ReplayError("stored snapshot is invalid") from exc
        if (
            snapshot.schema_version != replay_version
            or snapshot.reducer_version != reducer_version
        ):
            raise ReplayError("stored snapshot version columns disagree with bytes")
        return snapshot


__all__ = ["PostgresEventLedgerRepository"]
