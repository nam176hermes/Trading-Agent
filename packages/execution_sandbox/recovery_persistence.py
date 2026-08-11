"""Pure canonical bridges for registered sandbox recovery checkpoint records."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from hashlib import sha256
from types import UnionType
from typing import Any, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, TypeAdapter, ValidationError

from packages.domain.events import EventEnvelope
from packages.domain.recovery import SandboxRecoveryCheckpointRecorded
from packages.event_ledger.replay import (
    ReplayError,
    _canonical_json,
    deserialize_event,
    serialize_event,
)
from packages.event_ledger.repository import EventLedgerRepository
from packages.runtime_risk import canonical_model_digest, canonical_model_json

from .recovery import (
    SandboxRecoveryCheckpoint,
    SandboxRecoveryMalformedInput,
    _canonical_checkpoint_input,
)


_EVENT_TYPE = "SandboxRecoveryCheckpointRecorded"
_EVENT_SCHEMA_VERSION = "sandbox-recovery-checkpoint-recorded-event-v1"
_EVENT_SOURCE = "execution-sandbox"
_CHECKPOINT_SCHEMA_VERSION = "sandbox-recovery-checkpoint-v1"
_RECORD_SCHEMA_VERSION = "sandbox-recovery-checkpoint-recorded-v1"


class SandboxRecoveryPersistenceError(ValueError):
    """Supplied checkpoint persistence evidence failed closed validation."""


def _concrete_uuid(value: object, field_name: str) -> UUID:
    if type(value) is not UUID:
        raise SandboxRecoveryPersistenceError(
            f"{field_name} must be a concrete UUID"
        )
    return value


def _require_exact_model_root(
    value: object,
    expected_type: type[BaseModel],
    field_name: str,
) -> None:
    """Reject incomplete or surplus exact-model state without calling value hooks."""

    if type(value) is not expected_type:
        raise SandboxRecoveryPersistenceError(
            f"{field_name} must be a concrete {expected_type.__name__}"
        )
    try:
        state = object.__getattribute__(value, "__dict__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
    except AttributeError as exc:
        raise SandboxRecoveryPersistenceError(
            f"{field_name} model state is incomplete"
        ) from exc
    if type(state) is not dict or type(fields_set) is not set:
        raise SandboxRecoveryPersistenceError(
            f"{field_name} model state must be concrete"
        )
    state_names = tuple(dict.__iter__(state))
    fields_set_names = tuple(set.__iter__(fields_set))
    if any(type(name) is not str for name in state_names):
        raise SandboxRecoveryPersistenceError(
            f"{field_name} field names must be concrete strings"
        )
    if any(type(name) is not str for name in fields_set_names):
        raise SandboxRecoveryPersistenceError(
            f"{field_name} field-set names must be concrete strings"
        )
    declared_names = expected_type.model_fields
    if (
        len(state_names) != len(declared_names)
        or any(name not in declared_names for name in state_names)
        or any(name not in state for name in declared_names)
        or any(name not in declared_names for name in fields_set_names)
    ):
        raise SandboxRecoveryPersistenceError(
            f"{field_name} model fields must be exact"
        )
    if extra is not None and (type(extra) is not dict or dict.__len__(extra) != 0):
        raise SandboxRecoveryPersistenceError(
            f"{field_name} model extras must be empty"
        )


def _canonical_record(
    value: object,
) -> SandboxRecoveryCheckpointRecorded:
    _require_exact_model_root(
        value,
        SandboxRecoveryCheckpointRecorded,
        "record",
    )
    try:
        canonical = SandboxRecoveryCheckpointRecorded.model_validate(value)
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise SandboxRecoveryPersistenceError("record is not canonical") from exc
    _require_exact_model_root(
        canonical,
        SandboxRecoveryCheckpointRecorded,
        "canonical record",
    )
    return canonical


def encode_recovery_checkpoint(
    *,
    recovery_session_id: UUID,
    checkpoint: SandboxRecoveryCheckpoint,
) -> SandboxRecoveryCheckpointRecorded:
    """Encode one freshly revalidated checkpoint without mutating caller evidence."""

    session_id = _concrete_uuid(recovery_session_id, "recovery_session_id")
    try:
        _require_exact_model_root(
            checkpoint,
            SandboxRecoveryCheckpoint,
            "checkpoint",
        )
        canonical_checkpoint = _canonical_checkpoint_input(checkpoint)
        _require_exact_model_root(
            canonical_checkpoint,
            SandboxRecoveryCheckpoint,
            "canonical checkpoint",
        )
        checkpoint_json = canonical_model_json(canonical_checkpoint)
        checkpoint_digest = canonical_model_digest(canonical_checkpoint)
        stored_digest = sha256(str.encode(checkpoint_json, "utf-8")).hexdigest()
        if stored_digest != checkpoint_digest:
            raise ValueError("canonical checkpoint digest sanity check failed")
        if type(canonical_checkpoint.checkpoint_id) is not UUID:
            raise ValueError("checkpoint_id must be a concrete UUID")
        if type(canonical_checkpoint.schema_version) is not str:
            raise ValueError("checkpoint schema_version must be concrete text")
        return _canonical_record(
            SandboxRecoveryCheckpointRecorded(
                recovery_session_id=session_id,
                checkpoint_id=canonical_checkpoint.checkpoint_id,
                checkpoint_digest=checkpoint_digest,
                checkpoint_json=checkpoint_json,
                checkpoint_schema_version=canonical_checkpoint.schema_version,
                schema_version=_RECORD_SCHEMA_VERSION,
            )
        )
    except SandboxRecoveryPersistenceError:
        raise
    except (
        AttributeError,
        SandboxRecoveryMalformedInput,
        TypeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise SandboxRecoveryPersistenceError(
            "checkpoint cannot be canonically encoded"
        ) from exc


def _decode_union(value: object, annotation: object, field_name: str) -> object:
    if value is None and type(None) in get_args(annotation):
        return None
    failures: list[Exception] = []
    for candidate in get_args(annotation):
        if candidate is type(None):
            continue
        try:
            return _decode_json_value(value, candidate, field_name)
        except (TypeError, ValidationError, ValueError) as exc:
            failures.append(exc)
    raise ValueError(f"{field_name} does not match its checkpoint field type") from (
        failures[-1] if failures else None
    )


def _decode_json_model(
    document: object,
    model_type: type[BaseModel],
    field_name: str,
) -> BaseModel:
    if type(document) is not dict:
        raise ValueError(f"{field_name} must be a JSON object")
    values: dict[str, object] = {}
    for name, value in document.items():
        field = model_type.model_fields.get(name)
        values[name] = (
            value
            if field is None
            else _decode_json_value(value, field.annotation, f"{field_name}.{name}")
        )
    return model_type.model_validate(values)


def _decode_json_value(
    value: object,
    annotation: object,
    field_name: str,
) -> object:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return _decode_union(value, annotation, field_name)
    if origin is tuple:
        if type(value) is not list:
            raise ValueError(f"{field_name} must be a JSON array")
        arguments = get_args(annotation)
        item_type = arguments[0] if arguments else Any
        return tuple(
            _decode_json_value(item, item_type, f"{field_name}.{index}")
            for index, item in enumerate(value)
        )
    if isinstance(annotation, type) and issubclass(annotation, EventEnvelope):
        if type(value) is not dict:
            raise ValueError(f"{field_name} must be an event JSON object")
        return deserialize_event(_canonical_json(value))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _decode_json_model(value, annotation, field_name)
    if annotation is UUID:
        if type(value) is not str:
            raise ValueError(f"{field_name} must be a UUID string")
        return UUID(value)
    if annotation is datetime:
        if type(value) is not str:
            raise ValueError(f"{field_name} must be a datetime string")
        decoded = TypeAdapter(datetime).validate_json(
            _canonical_json(value),
            strict=True,
        )
        if type(decoded) is not datetime:
            raise ValueError(f"{field_name} must decode to a concrete datetime")
        return decoded
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if annotation in (Any, object):
        return value
    return TypeAdapter(annotation).validate_json(
        _canonical_json(value),
        strict=True,
    )


def _checkpoint_from_stored_json(checkpoint_json: str) -> SandboxRecoveryCheckpoint:
    try:
        document = json.loads(checkpoint_json)
        checkpoint = _decode_json_model(
            document,
            SandboxRecoveryCheckpoint,
            "checkpoint",
        )
    except (
        AttributeError,
        json.JSONDecodeError,
        ReplayError,
        TypeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise SandboxRecoveryPersistenceError(
            "checkpoint_json does not contain a valid checkpoint"
        ) from exc
    if type(checkpoint) is not SandboxRecoveryCheckpoint:
        raise SandboxRecoveryPersistenceError(
            "checkpoint_json did not decode to the exact checkpoint type"
        )
    _require_exact_model_root(
        checkpoint,
        SandboxRecoveryCheckpoint,
        "decoded checkpoint",
    )
    return checkpoint


def decode_recovery_checkpoint(
    record: SandboxRecoveryCheckpointRecorded,
) -> SandboxRecoveryCheckpoint:
    """Decode only exact canonical checkpoint bytes from a rebuilt record."""

    canonical_record = _canonical_record(record)
    checkpoint = _checkpoint_from_stored_json(canonical_record.checkpoint_json)
    try:
        canonical_json = canonical_model_json(checkpoint)
        decoded_digest = canonical_model_digest(checkpoint)
        stored_digest = sha256(
            str.encode(canonical_record.checkpoint_json, "utf-8")
        ).hexdigest()
        if canonical_json != canonical_record.checkpoint_json:
            raise ValueError("checkpoint_json is not the canonical checkpoint encoding")
        if (
            decoded_digest != canonical_record.checkpoint_digest
            or stored_digest != canonical_record.checkpoint_digest
        ):
            raise ValueError("checkpoint digest does not match decoded checkpoint")
        if type(checkpoint.checkpoint_id) is not UUID:
            raise ValueError("decoded checkpoint_id must be a concrete UUID")
        if checkpoint.checkpoint_id != canonical_record.checkpoint_id:
            raise ValueError("decoded checkpoint_id does not match record checkpoint_id")
        if type(checkpoint.schema_version) is not str:
            raise ValueError("decoded checkpoint schema_version must be concrete text")
        if checkpoint.schema_version != canonical_record.checkpoint_schema_version:
            raise ValueError("decoded checkpoint schema does not match record schema")
        return checkpoint
    except SandboxRecoveryPersistenceError:
        raise
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise SandboxRecoveryPersistenceError(
            "record does not contain one canonical bound checkpoint"
        ) from exc


def _require_concrete_envelope(
    event: object,
) -> EventEnvelope[SandboxRecoveryCheckpointRecorded]:
    expected_type = EventEnvelope[SandboxRecoveryCheckpointRecorded]
    _require_exact_model_root(event, expected_type, "event")
    try:
        for name in (
            "event_id",
            "stream_id",
            "correlation_id",
            "causation_id",
            "trace_id",
        ):
            _concrete_uuid(object.__getattribute__(event, name), f"event {name}")
        for name in ("event_type", "schema_version", "source"):
            if type(object.__getattribute__(event, name)) is not str:
                raise SandboxRecoveryPersistenceError(
                    f"event {name} must be a concrete string"
                )
        for name in (
            "observed_at",
            "ingested_at",
            "produced_at",
            "effective_at",
            "expires_at",
        ):
            if type(object.__getattribute__(event, name)) is not datetime:
                raise SandboxRecoveryPersistenceError(
                    f"event {name} must be a concrete datetime"
                )
        if type(object.__getattribute__(event, "sequence")) is not int:
            raise SandboxRecoveryPersistenceError(
                "event sequence must be a concrete integer"
            )
        if (
            type(object.__getattribute__(event, "payload"))
            is not SandboxRecoveryCheckpointRecorded
        ):
            raise SandboxRecoveryPersistenceError(
                "event payload must be a concrete recovery checkpoint record"
            )
    except AttributeError as exc:
        raise SandboxRecoveryPersistenceError(
            "event model fields are incomplete"
        ) from exc
    return event


def validate_recovery_checkpoint_event(
    event: EventEnvelope[SandboxRecoveryCheckpointRecorded],
) -> SandboxRecoveryCheckpoint:
    """Validate one supplied event; never create, select, append, or execute it."""

    supplied = _require_concrete_envelope(event)
    _canonical_record(object.__getattribute__(supplied, "payload"))
    try:
        canonical_text = serialize_event(supplied)
        canonical = deserialize_event(canonical_text)
        canonical = _require_concrete_envelope(canonical)
        if serialize_event(canonical) != canonical_text:
            raise ValueError("recovery checkpoint event did not round-trip canonically")
        record = _canonical_record(canonical.payload)
        if canonical.event_type != _EVENT_TYPE:
            raise ValueError("recovery checkpoint event_type is invalid")
        if canonical.schema_version != _EVENT_SCHEMA_VERSION:
            raise ValueError("recovery checkpoint event schema is invalid")
        if canonical.source != _EVENT_SOURCE:
            raise ValueError("recovery checkpoint event source is invalid")
        if canonical.event_id != record.checkpoint_id:
            raise ValueError("event_id must equal record checkpoint_id")
        if canonical.stream_id != record.recovery_session_id:
            raise ValueError("stream_id must equal record recovery_session_id")
        checkpoint = decode_recovery_checkpoint(record)
        if checkpoint.checkpoint_id != canonical.event_id:
            raise ValueError("embedded checkpoint_id must equal event_id")
        return checkpoint
    except SandboxRecoveryPersistenceError:
        raise
    except (AttributeError, ReplayError, TypeError, ValidationError, ValueError) as exc:
        raise SandboxRecoveryPersistenceError(
            "event is not a canonical bound recovery checkpoint record"
        ) from exc


def load_recovery_checkpoint(
    *,
    repository: EventLedgerRepository,
    recovery_session_id: UUID,
    checkpoint_id: UUID,
) -> SandboxRecoveryCheckpoint | None:
    """Load one exact checkpoint only after validating its complete session stream."""

    session_id = _concrete_uuid(recovery_session_id, "recovery_session_id")
    requested_checkpoint_id = _concrete_uuid(checkpoint_id, "checkpoint_id")
    try:
        load_stream_events = object.__getattribute__(
            repository,
            "load_stream_events",
        )
    except AttributeError as exc:
        raise SandboxRecoveryPersistenceError(
            "repository must provide load_stream_events"
        ) from exc
    if not callable(load_stream_events):
        raise SandboxRecoveryPersistenceError(
            "repository load_stream_events must be callable"
        )

    events = load_stream_events(session_id)
    if type(events) is not tuple:
        raise SandboxRecoveryPersistenceError(
            "repository load_stream_events must return a concrete tuple"
        )

    seen_event_ids: set[UUID] = set()
    selected: SandboxRecoveryCheckpoint | None = None
    for event in tuple.__iter__(events):
        checkpoint = validate_recovery_checkpoint_event(event)
        record = object.__getattribute__(event, "payload")
        event_id = object.__getattribute__(event, "event_id")
        stream_id = object.__getattribute__(event, "stream_id")
        record_session_id = object.__getattribute__(record, "recovery_session_id")
        record_checkpoint_id = object.__getattribute__(record, "checkpoint_id")
        if stream_id != session_id or record_session_id != session_id:
            raise SandboxRecoveryPersistenceError(
                "recovery checkpoint event does not belong to requested session"
            )
        if event_id in seen_event_ids:
            raise SandboxRecoveryPersistenceError(
                "recovery checkpoint stream contains a duplicate event_id"
            )
        seen_event_ids.add(event_id)
        if (
            event_id != record_checkpoint_id
            or record_checkpoint_id != checkpoint.checkpoint_id
        ):
            raise SandboxRecoveryPersistenceError(
                "recovery checkpoint identities are not exactly bound"
            )
        if event_id == requested_checkpoint_id:
            selected = checkpoint
    return selected


__all__ = [
    "SandboxRecoveryPersistenceError",
    "decode_recovery_checkpoint",
    "encode_recovery_checkpoint",
    "load_recovery_checkpoint",
    "validate_recovery_checkpoint_event",
]
