from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError


EXPECTED_EVENT_FAMILIES = (
    "ENGINE_LIFECYCLE",
    "MARKET_DATA_CONTINUITY",
    "STRATEGY_LIFECYCLE",
    "ORDER_LIFECYCLE",
    "FILLS",
    "POSITIONS",
    "ACCOUNT_STATE",
    "RUNTIME_RISK",
    "RECONCILIATION",
    "HEALTH",
    "HALT",
)


def test_event_family_classification_is_closed() -> None:
    contracts = import_module("packages.engine_contracts")

    assert tuple(family.value for family in contracts.EventFamily) == (
        EXPECTED_EVENT_FAMILIES
    )


def test_engine_event_is_strict_immutable_and_rejects_duplicate_attributes() -> None:
    contracts = import_module("packages.engine_contracts")
    event = contracts.EngineEvent(
        event_type="EngineStarted",
        family=contracts.EventFamily.ENGINE_LIFECYCLE,
        attributes=(
            contracts.EventAttribute(name="mode", value="PAPER"),
            contracts.EventAttribute(name="ready", value=True),
        ),
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        contracts.EngineEvent.model_validate(
            {**event.model_dump(), "provider_payload": {}}
        )
    with pytest.raises(ValidationError, match="duplicate event attribute"):
        contracts.EngineEvent(
            event_type="EngineStarted",
            family=contracts.EventFamily.ENGINE_LIFECYCLE,
            attributes=(
                contracts.EventAttribute(name="mode", value="PAPER"),
                contracts.EventAttribute(name="mode", value="PAPER"),
            ),
        )
    with pytest.raises(ValidationError, match="frozen"):
        event.event_type = "EngineStopped"


def test_event_envelope_validates_payload_digest_and_round_trips_json() -> None:
    contracts = import_module("packages.engine_contracts")
    event = contracts.EngineEvent(
        event_type="EngineHalted",
        family=contracts.EventFamily.HALT,
        attributes=(
            contracts.EventAttribute(name="reason_code", value="RUNTIME_RISK"),
        ),
    )
    values = {
        "message_id": uuid4(),
        "correlation_id": uuid4(),
        "causation_id": uuid4(),
        "engine_run_id": uuid4(),
        "stream_sequence": 7,
        "event_time": datetime(2026, 8, 4, 18, 30, tzinfo=UTC),
        "initialization_time": datetime(2026, 8, 4, 18, 29, tzinfo=UTC),
        "schema_version": "1.0.0",
        "producer_identity": "engine-fixture",
        "source_commit": "a" * 40,
        "config_digest": "b" * 64,
        "payload_digest": contracts.payload_digest(event),
        "payload": event,
    }
    envelope = contracts.EngineEventEnvelope.model_validate(values)

    assert contracts.EngineEventEnvelope.model_validate_json(
        envelope.model_dump_json()
    ) == envelope
    with pytest.raises(ValidationError, match="payload_digest"):
        contracts.EngineEventEnvelope.model_validate(
            {**values, "payload_digest": "0" * 64}
        )
    with pytest.raises(ValidationError, match="initialization_time"):
        contracts.EngineEventEnvelope.model_validate(
            {
                **values,
                "initialization_time": values["event_time"]
                + timedelta(microseconds=1),  # type: ignore[operator]
            }
        )


def test_public_event_schema_has_no_engine_implementation_or_provider_leakage() -> None:
    contracts = import_module("packages.engine_contracts")
    schema = json.dumps(
        contracts.EngineEventEnvelope.model_json_schema(), sort_keys=True
    ).casefold()

    assert "nautilus" not in schema
    assert "binance" not in schema
    assert "coinbase" not in schema
    assert "provider_payload" not in schema
