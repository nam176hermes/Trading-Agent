from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import EventEnvelope, SandboxRecoveryCheckpointRecorded
from packages.domain.events import EVENT_TYPE_BY_PAYLOAD
from packages.event_ledger import deserialize_event, replay, serialize_event
from packages.execution_sandbox import (
    SandboxRecoveryCheckpoint,
    SandboxRecoveryPersistenceError,
    decode_recovery_checkpoint,
    encode_recovery_checkpoint,
    validate_recovery_checkpoint_event,
)
from packages.execution_sandbox import recovery_persistence
from packages.runtime_risk import canonical_model_json

from tests.execution_sandbox.test_recovery_contracts import checkpoint_values, uid


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
RECORD_SCHEMA = "sandbox-recovery-checkpoint-recorded-v1"
CHECKPOINT_SCHEMA = "sandbox-recovery-checkpoint-v1"
EVENT_SCHEMA = "sandbox-recovery-checkpoint-recorded-event-v1"


class AdversarialUUID(UUID):
    operations: list[str] = []

    def __eq__(self, other: object) -> bool:
        type(self).operations.append("equality")
        raise AssertionError("UUID equality must not run")

    def __hash__(self) -> int:
        type(self).operations.append("hashing")
        raise AssertionError("UUID hashing must not run")


class AdversarialString(str):
    operations: list[str] = []

    def __eq__(self, other: object) -> bool:
        type(self).operations.append("equality")
        raise AssertionError("string equality must not run")

    def __hash__(self) -> int:
        type(self).operations.append("hashing")
        raise AssertionError("string hashing must not run")


class AdversarialInt(int):
    def __eq__(self, other: object) -> bool:
        raise AssertionError("integer equality must not run")

    def __hash__(self) -> int:
        raise AssertionError("integer hashing must not run")


def checkpoint(prepared_case: Any) -> SandboxRecoveryCheckpoint:
    return SandboxRecoveryCheckpoint(**checkpoint_values(prepared_case))


def record_values(
    value: SandboxRecoveryCheckpointRecorded,
    **changes: object,
) -> dict[str, object]:
    values = {
        name: object.__getattribute__(value, name)
        for name in SandboxRecoveryCheckpointRecorded.model_fields
    }
    values.update(changes)
    return values


def record_with_json(
    value: SandboxRecoveryCheckpointRecorded,
    checkpoint_json: str,
) -> SandboxRecoveryCheckpointRecorded:
    return SandboxRecoveryCheckpointRecorded(
        **record_values(
            value,
            checkpoint_json=checkpoint_json,
            checkpoint_digest=sha256(checkpoint_json.encode("utf-8")).hexdigest(),
        )
    )


def checkpoint_event(
    record: SandboxRecoveryCheckpointRecorded,
    **changes: object,
) -> EventEnvelope[SandboxRecoveryCheckpointRecorded]:
    values: dict[str, object] = {
        "event_id": record.checkpoint_id,
        "event_type": "SandboxRecoveryCheckpointRecorded",
        "schema_version": EVENT_SCHEMA,
        "source": "execution-sandbox",
        "stream_id": record.recovery_session_id,
        "sequence": 1,
        "observed_at": NOW,
        "ingested_at": NOW,
        "produced_at": NOW,
        "effective_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "correlation_id": uid(700),
        "causation_id": uid(701),
        "trace_id": uid(702),
        "payload": record,
    }
    values.update(changes)
    return EventEnvelope[SandboxRecoveryCheckpointRecorded](**values)


def test_encode_decode_uses_exact_canonical_checkpoint_bytes_without_mutation(
    prepared_case: Any,
) -> None:
    source = checkpoint(prepared_case)
    source_before = canonical_model_json(source)
    session_id = uid(600)

    record = encode_recovery_checkpoint(
        recovery_session_id=session_id,
        checkpoint=source,
    )
    decoded = decode_recovery_checkpoint(record)

    assert type(record) is SandboxRecoveryCheckpointRecorded
    assert tuple(SandboxRecoveryCheckpointRecorded.model_fields) == (
        "recovery_session_id",
        "checkpoint_id",
        "checkpoint_digest",
        "checkpoint_json",
        "checkpoint_schema_version",
        "schema_version",
    )
    assert record.recovery_session_id == session_id
    assert record.checkpoint_id == source.checkpoint_id
    assert record.checkpoint_json == source_before
    assert record.checkpoint_digest == sha256(source_before.encode("utf-8")).hexdigest()
    assert record.checkpoint_schema_version == CHECKPOINT_SCHEMA
    assert record.schema_version == RECORD_SCHEMA
    assert type(decoded) is SandboxRecoveryCheckpoint
    assert decoded == source
    assert decoded is not source
    assert canonical_model_json(decoded) == source_before
    assert canonical_model_json(source) == source_before


def test_record_validates_only_metadata_and_stored_utf8_digest() -> None:
    checkpoint_json = "not-json"
    record = SandboxRecoveryCheckpointRecorded(
        recovery_session_id=uid(600),
        checkpoint_id=uid(601),
        checkpoint_digest=sha256(checkpoint_json.encode("utf-8")).hexdigest(),
        checkpoint_json=checkpoint_json,
        checkpoint_schema_version=CHECKPOINT_SCHEMA,
        schema_version=RECORD_SCHEMA,
    )

    assert record.checkpoint_json == checkpoint_json
    with pytest.raises(SandboxRecoveryPersistenceError):
        decode_recovery_checkpoint(record)


def test_record_is_strict_frozen_extra_forbid_and_revalidates_forged_copies(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    with pytest.raises(ValidationError, match="frozen"):
        record.checkpoint_json = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SandboxRecoveryCheckpointRecorded(
            **record_values(record),
            unexpected=True,
        )

    forged = record.model_copy(update={"checkpoint_digest": "0" * 64})
    constructed = SandboxRecoveryCheckpointRecorded.model_construct(
        **record_values(record, schema_version="forged-record-version")
    )
    with pytest.raises(ValidationError):
        SandboxRecoveryCheckpointRecorded.model_validate(forged)
    with pytest.raises(ValidationError):
        SandboxRecoveryCheckpointRecorded.model_validate(constructed)
    with pytest.raises(SandboxRecoveryPersistenceError):
        decode_recovery_checkpoint(forged)
    with pytest.raises(SandboxRecoveryPersistenceError):
        decode_recovery_checkpoint(constructed)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("checkpoint_digest", "0" * 64),
        ("checkpoint_json", ""),
        ("checkpoint_schema_version", "sandbox-recovery-checkpoint-v2"),
        ("schema_version", "sandbox-recovery-checkpoint-recorded-v2"),
    ),
)
def test_record_rejects_digest_empty_text_and_schema_variants(
    field_name: str,
    replacement: str,
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )

    with pytest.raises(ValidationError):
        SandboxRecoveryCheckpointRecorded(
            **record_values(record, **{field_name: replacement})
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "checkpoint_digest",
        "checkpoint_json",
        "checkpoint_schema_version",
        "schema_version",
    ),
)
def test_record_rejects_string_subclasses_before_comparison_or_hashing(
    field_name: str,
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    AdversarialString.operations.clear()

    with pytest.raises(ValidationError):
        SandboxRecoveryCheckpointRecorded(
            **record_values(
                record,
                **{field_name: AdversarialString(getattr(record, field_name))},
            )
        )
    assert not AdversarialString.operations


@pytest.mark.parametrize("field_name", ("recovery_session_id", "checkpoint_id"))
def test_record_rejects_uuid_subclasses_before_comparison_or_hashing(
    field_name: str,
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    AdversarialUUID.operations.clear()

    with pytest.raises(ValidationError):
        SandboxRecoveryCheckpointRecorded(
            **record_values(
                record,
                **{field_name: AdversarialUUID(str(getattr(record, field_name)))},
            )
        )
    assert not AdversarialUUID.operations


def test_encode_rejects_hostile_session_and_copy_forged_checkpoint_without_mutation(
    prepared_case: Any,
) -> None:
    source = checkpoint(prepared_case)
    forged = source.model_copy(update={"scenario_digest": "not-a-digest"})
    AdversarialUUID.operations.clear()

    with pytest.raises(SandboxRecoveryPersistenceError):
        encode_recovery_checkpoint(
            recovery_session_id=AdversarialUUID(int=600),
            checkpoint=source,
        )
    assert not AdversarialUUID.operations
    with pytest.raises(SandboxRecoveryPersistenceError):
        encode_recovery_checkpoint(
            recovery_session_id=uid(600),
            checkpoint=forged,
        )


def test_encode_rejects_copy_forged_checkpoint_unknown_root_field(
    prepared_case: Any,
) -> None:
    forged = checkpoint(prepared_case).model_copy(update={"restore": True})
    assert object.__getattribute__(forged, "__dict__")["restore"] is True
    assert "restore" in object.__getattribute__(forged, "__pydantic_fields_set__")

    with pytest.raises(SandboxRecoveryPersistenceError):
        encode_recovery_checkpoint(
            recovery_session_id=uid(600),
            checkpoint=forged,
        )


@pytest.mark.parametrize(
    "variant",
    (
        "trailing-whitespace",
        "key-order",
        "duplicate-key",
        "numeric-token",
        "unicode-escape",
        "malformed",
        "unknown-field",
        "checkpoint-schema",
    ),
)
def test_decode_rejects_every_noncanonical_or_invalid_checkpoint_json_variant(
    variant: str,
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    text = record.checkpoint_json
    if variant == "trailing-whitespace":
        changed = text + " "
    elif variant == "key-order":
        document = json.loads(text)
        changed = json.dumps(
            dict(reversed(tuple(document.items()))),
            separators=(",", ":"),
        )
    elif variant == "duplicate-key":
        changed = "{" + f'"checkpoint_id":"{record.checkpoint_id}",' + text[1:]
    elif variant == "numeric-token":
        changed = text.replace('"halt_generation":1', '"halt_generation":1.0', 1)
        assert changed != text
    elif variant == "unicode-escape":
        changed = text.replace("sandbox-client-1", r"sandbox-client-\u0031", 1)
        assert changed != text
    elif variant == "malformed":
        changed = "{"
    else:
        document = json.loads(text)
        if variant == "unknown-field":
            document["unexpected"] = True
        else:
            document["schema_version"] = "sandbox-recovery-checkpoint-v2"
        changed = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    with pytest.raises(SandboxRecoveryPersistenceError):
        decode_recovery_checkpoint(record_with_json(record, changed))


def test_decode_rejects_record_to_embedded_checkpoint_identity_conflict(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    document = json.loads(record.checkpoint_json)
    document["checkpoint_id"] = str(uid(999))
    changed = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    with pytest.raises(SandboxRecoveryPersistenceError):
        decode_recovery_checkpoint(record_with_json(record, changed))


def test_carrier_and_decode_reject_copy_forged_unknown_root_field(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    forged = record.model_copy(update={"restore": True})
    assert object.__getattribute__(forged, "__dict__")["restore"] is True
    assert "restore" in object.__getattribute__(forged, "__pydantic_fields_set__")

    with pytest.raises((ValidationError, ValueError)):
        SandboxRecoveryCheckpointRecorded.model_validate(forged)
    with pytest.raises(SandboxRecoveryPersistenceError):
        decode_recovery_checkpoint(forged)


def test_registered_record_round_trips_with_exact_concrete_codec_and_event_binding(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record)
    canonical = serialize_event(event)
    restored = deserialize_event(canonical)

    assert EVENT_TYPE_BY_PAYLOAD[SandboxRecoveryCheckpointRecorded] == (
        "SandboxRecoveryCheckpointRecorded"
    )
    assert type(restored) is EventEnvelope[SandboxRecoveryCheckpointRecorded]
    assert type(restored.payload) is SandboxRecoveryCheckpointRecorded
    assert restored == event
    assert serialize_event(restored) == canonical
    decoded = validate_recovery_checkpoint_event(restored)
    assert decoded.checkpoint_id == record.checkpoint_id
    assert canonical_model_json(decoded) == record.checkpoint_json


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("event_id", uid(999)),
        ("stream_id", uid(998)),
        ("source", "other-source"),
        ("schema_version", "sandbox-recovery-checkpoint-recorded-event-v2"),
    ),
)
def test_event_validator_rejects_identity_and_fixed_metadata_conflicts(
    field_name: str,
    replacement: object,
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record, **{field_name: replacement})

    with pytest.raises(SandboxRecoveryPersistenceError):
        validate_recovery_checkpoint_event(event)


def test_event_validator_rejects_generic_forged_and_subclass_envelopes(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record)
    values = {
        name: object.__getattribute__(event, name)
        for name in EventEnvelope.model_fields
    }
    generic = EventEnvelope[object](**values)
    forged = event.model_copy(update={"source": ""})

    class EnvelopeSubclass(EventEnvelope[SandboxRecoveryCheckpointRecorded]):
        pass

    subclass = EnvelopeSubclass(**values)
    for supplied in (generic, forged, subclass):
        with pytest.raises(SandboxRecoveryPersistenceError):
            validate_recovery_checkpoint_event(supplied)


def test_event_validator_rejects_copy_forged_unknown_root_field(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    forged = checkpoint_event(record).model_copy(update={"restore": True})
    assert object.__getattribute__(forged, "__dict__")["restore"] is True
    assert "restore" in object.__getattribute__(forged, "__pydantic_fields_set__")

    with pytest.raises(SandboxRecoveryPersistenceError):
        validate_recovery_checkpoint_event(forged)


def test_event_validator_maps_incomplete_constructed_envelope_to_narrow_error(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record)
    values = {
        name: object.__getattribute__(event, name)
        for name in EventEnvelope.model_fields
        if name != "trace_id"
    }
    incomplete = EventEnvelope[SandboxRecoveryCheckpointRecorded].model_construct(
        **values
    )

    with pytest.raises(SandboxRecoveryPersistenceError):
        validate_recovery_checkpoint_event(incomplete)


def test_event_validator_rebuilds_record_before_entering_event_codec(
    prepared_case: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    forged_record = record.model_copy(update={"checkpoint_digest": "0" * 64})
    event = checkpoint_event(record).model_copy(update={"payload": forged_record})
    codec_calls: list[object] = []

    def forbidden_codec(value: object) -> str:
        codec_calls.append(value)
        raise AssertionError("event codec must not receive an unvalidated record")

    monkeypatch.setattr(recovery_persistence, "serialize_event", forbidden_codec)

    with pytest.raises(SandboxRecoveryPersistenceError):
        validate_recovery_checkpoint_event(event)
    assert codec_calls == []


@pytest.mark.parametrize(
    "field_name",
    (
        "event_id",
        "stream_id",
        "correlation_id",
        "causation_id",
        "trace_id",
    ),
)
def test_event_validator_rejects_hostile_uuid_before_codec_or_binding(
    field_name: str,
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record).model_copy()
    object.__setattr__(
        event,
        field_name,
        AdversarialUUID(str(object.__getattribute__(event, field_name))),
    )
    AdversarialUUID.operations.clear()

    with pytest.raises(SandboxRecoveryPersistenceError):
        validate_recovery_checkpoint_event(event)
    assert not AdversarialUUID.operations


@pytest.mark.parametrize(
    "field_name",
    ("event_type", "schema_version", "source"),
)
def test_event_validator_rejects_hostile_string_before_codec_or_binding(
    field_name: str,
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record).model_copy()
    object.__setattr__(
        event,
        field_name,
        AdversarialString(object.__getattribute__(event, field_name)),
    )
    AdversarialString.operations.clear()

    with pytest.raises(SandboxRecoveryPersistenceError):
        validate_recovery_checkpoint_event(event)
    assert not AdversarialString.operations


def test_event_validator_rejects_hostile_sequence_before_codec(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record).model_copy()
    object.__setattr__(event, "sequence", AdversarialInt(1))

    with pytest.raises(SandboxRecoveryPersistenceError):
        validate_recovery_checkpoint_event(event)


def test_domain_record_import_does_not_import_execution_sandbox() -> None:
    program = r'''
import importlib.abc
import sys

class BlockExecutionSandbox(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith("packages.execution_sandbox"):
            raise AssertionError("domain recovery imported execution_sandbox")
        return None

sys.meta_path.insert(0, BlockExecutionSandbox())
from packages.domain.recovery import SandboxRecoveryCheckpointRecorded
assert SandboxRecoveryCheckpointRecorded.__name__ == "SandboxRecoveryCheckpointRecorded"
'''
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_record_and_typed_envelope_schemas_are_strict() -> None:
    schema_root = ROOT / "generated" / "domain" / "json-schema"
    record_schema = json.loads(
        (schema_root / "SandboxRecoveryCheckpointRecorded.json").read_text(
            encoding="utf-8"
        )
    )
    envelope_schema = json.loads(
        (
            schema_root
            / "EventEnvelope_SandboxRecoveryCheckpointRecorded_.json"
        ).read_text(encoding="utf-8")
    )

    assert record_schema["additionalProperties"] is False
    assert set(record_schema["required"]) == set(
        SandboxRecoveryCheckpointRecorded.model_fields
    )
    assert record_schema["properties"]["schema_version"]["const"] == RECORD_SCHEMA
    assert envelope_schema["additionalProperties"] is False
    assert envelope_schema["properties"]["event_type"] == {
        "const": "SandboxRecoveryCheckpointRecorded",
        "type": "string",
    }


def test_replay_records_checkpoint_event_without_execution_action(
    prepared_case: Any,
) -> None:
    record = encode_recovery_checkpoint(
        recovery_session_id=uid(600),
        checkpoint=checkpoint(prepared_case),
    )
    event = checkpoint_event(record)

    result = replay((event,))

    assert result.status.value == "COMPLETE"
    assert result.state.event_count == 1
    assert tuple((entry.event_type, entry.count) for entry in result.state.type_counts) == (
        ("SandboxRecoveryCheckpointRecorded", 1),
    )
    assert result.state.streams[0].stream_id == record.recovery_session_id
