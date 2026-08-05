from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError


NOW = datetime(2026, 8, 4, 18, 30, tzinfo=UTC)


def command_envelope_values() -> dict[str, object]:
    contracts = import_module("packages.engine_contracts")
    command = contracts.DescribeEngineCapabilities(
        command_type="DescribeEngineCapabilities"
    )
    return {
        "message_id": uuid4(),
        "correlation_id": uuid4(),
        "causation_id": uuid4(),
        "engine_run_id": uuid4(),
        "stream_sequence": 1,
        "event_time": NOW,
        "initialization_time": NOW - timedelta(seconds=1),
        "schema_version": "1.0.0",
        "producer_identity": "trading-agent-control-plane",
        "source_commit": "a" * 40,
        "config_digest": "b" * 64,
        "payload_digest": contracts.payload_digest(command),
        "payload": command,
    }


def test_command_envelope_requires_all_authority_metadata_and_is_immutable() -> None:
    contracts = import_module("packages.engine_contracts")
    values = command_envelope_values()
    envelope = contracts.EngineCommandEnvelope.model_validate(values)

    assert set(envelope.model_fields_set) == {
        "message_id",
        "correlation_id",
        "causation_id",
        "engine_run_id",
        "stream_sequence",
        "event_time",
        "initialization_time",
        "schema_version",
        "producer_identity",
        "source_commit",
        "config_digest",
        "payload_digest",
        "payload",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        contracts.EngineCommandEnvelope.model_validate({**values, "unknown": True})
    with pytest.raises(ValidationError, match="frozen"):
        envelope.stream_sequence = 2
    with pytest.raises(ValidationError, match="128|string_too_long"):
        contracts.EngineCommandEnvelope.model_validate(
            {**values, "producer_identity": "p" * 129}
        )


def test_command_envelope_rejects_unknown_version() -> None:
    contracts = import_module("packages.engine_contracts")

    with pytest.raises(ValidationError, match="1.0.0"):
        contracts.EngineCommandEnvelope.model_validate(
            {**command_envelope_values(), "schema_version": "2.0.0"}
        )


@pytest.mark.parametrize("sequence", [0, -1, True, 1.0, "1"])
def test_command_envelope_rejects_invalid_stream_sequence(sequence: object) -> None:
    contracts = import_module("packages.engine_contracts")

    with pytest.raises(ValidationError):
        contracts.EngineCommandEnvelope.model_validate(
            {**command_envelope_values(), "stream_sequence": sequence}
        )


@pytest.mark.parametrize(
    "invalid_time",
    [
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=-4))),
        "2026-08-04T18:30:00+00:00",
    ],
)
def test_command_envelope_rejects_noncanonical_timestamps(
    invalid_time: object,
) -> None:
    contracts = import_module("packages.engine_contracts")

    with pytest.raises(ValidationError, match="UTC|canonical"):
        contracts.EngineCommandEnvelope.model_validate(
            {**command_envelope_values(), "event_time": invalid_time}
        )


def test_command_envelope_rejects_initialization_after_event_time() -> None:
    contracts = import_module("packages.engine_contracts")

    with pytest.raises(ValidationError, match="initialization_time"):
        contracts.EngineCommandEnvelope.model_validate(
            {
                **command_envelope_values(),
                "initialization_time": NOW + timedelta(microseconds=1),
            }
        )


def test_command_envelope_rejects_payload_digest_mismatch() -> None:
    contracts = import_module("packages.engine_contracts")

    with pytest.raises(ValidationError, match="payload_digest"):
        contracts.EngineCommandEnvelope.model_validate(
            {**command_envelope_values(), "payload_digest": "0" * 64}
        )


def test_validate_envelope_batch_rejects_duplicate_message_id() -> None:
    contracts = import_module("packages.engine_contracts")
    first = contracts.EngineCommandEnvelope.model_validate(command_envelope_values())
    second_values = command_envelope_values()
    second_values["message_id"] = first.message_id
    second = contracts.EngineCommandEnvelope.model_validate(second_values)

    with pytest.raises(ValueError, match="duplicate message_id"):
        contracts.validate_envelope_batch((first, second))


def test_envelope_json_round_trip_uses_canonical_utc_z_timestamps() -> None:
    contracts = import_module("packages.engine_contracts")
    envelope = contracts.EngineCommandEnvelope.model_validate(command_envelope_values())
    serialized = json.loads(envelope.model_dump_json())

    assert serialized["event_time"] == "2026-08-04T18:30:00Z"
    assert serialized["initialization_time"] == "2026-08-04T18:29:59Z"
    assert contracts.EngineCommandEnvelope.model_validate_json(
        envelope.model_dump_json()
    ) == envelope


def test_canonical_json_and_digest_are_order_independent_and_reject_floats() -> None:
    contracts = import_module("packages.engine_contracts")

    assert contracts.canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert contracts.payload_digest({"b": 2, "a": 1}) == contracts.payload_digest(
        {"a": 1, "b": 2}
    )
    with pytest.raises(ValueError, match="float"):
        contracts.canonical_json_bytes({"value": 1.0})
