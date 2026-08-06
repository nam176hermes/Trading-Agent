from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest

from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    EngineEvent,
    EngineEventEnvelope,
    EventAttribute,
    EventFamily,
    RunBacktest,
    canonical_json_bytes,
    payload_digest,
)
from packages.nautilus_backtest import (
    NautilusBacktestError,
    validate_isolated_backtest_result,
)


def _request() -> EngineCommandEnvelope:
    references = tuple(
        ArtifactReference(
            artifact_id=UUID(f"{number}{number}{number}{number}{number}{number}{number}{number}-1111-4111-8111-111111111111"),
            sha256=hashlib.sha256(f"artifact-{number}".encode("ascii")).hexdigest(),
            media_type="application/jsonl" if number == 4 else "application/json",
        )
        for number in range(1, 5)
    )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return EngineCommandEnvelope(
        message_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        correlation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        causation_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        engine_run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        stream_sequence=1,
        event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        initialization_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        schema_version=CURRENT_SCHEMA_VERSION,
        producer_identity="worker-authority-1",
        source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest=payload_digest(
            {
                "engine_configuration": command.engine_configuration,
                "instrument_catalog": command.instrument_catalog,
                "strategy_configuration": command.strategy_configuration,
            }
        ),
        payload_digest=payload_digest(command),
        payload=command,
    )


def _event(request: EngineCommandEnvelope) -> EngineEventEnvelope:
    assert isinstance(request.payload, RunBacktest)
    inputs_digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "engine_configuration": request.payload.engine_configuration.sha256,
                "instrument_catalog": request.payload.instrument_catalog.sha256,
                "strategy_configuration": request.payload.strategy_configuration.sha256,
                "market_data": request.payload.market_data.sha256,
            }
        )
    ).hexdigest()
    payload = EngineEvent(
        event_type="NautilusBacktestCompleted",
        family=EventFamily.ENGINE_LIFECYCLE,
        attributes=(
            EventAttribute(name="input_artifacts_sha256", value=inputs_digest),
            EventAttribute(name="iterations", value=0),
            EventAttribute(name="total_events", value=0),
            EventAttribute(name="total_orders", value=0),
            EventAttribute(name="total_positions", value=0),
        ),
    )
    return EngineEventEnvelope(
        message_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        engine_run_id=request.engine_run_id,
        stream_sequence=request.stream_sequence + 1,
        event_time=request.event_time,
        initialization_time=request.initialization_time,
        schema_version=request.schema_version,
        producer_identity=request.producer_identity,
        source_commit=request.source_commit,
        config_digest=request.config_digest,
        payload_digest=payload_digest(payload),
        payload=payload,
    )


def test_same_hash_bound_request_and_zero_order_event_have_one_result_digest() -> None:
    request = _request()
    event = _event(request)

    first = validate_isolated_backtest_result(request, event)
    second = validate_isolated_backtest_result(request, event)

    assert first.result_sha256 == second.result_sha256
    assert first.total_orders == 0
    assert first.total_positions == 0


def test_result_rejects_catalog_or_target_digest_drift() -> None:
    request = _request()
    event = _event(request)
    drifted_payload = event.payload.model_copy(
        update={
            "attributes": tuple(
                EventAttribute(name=item.name, value="0" * 64)
                if item.name == "input_artifacts_sha256"
                else item
                for item in event.payload.attributes
            )
        }
    )
    drifted = event.model_copy(
        update={
            "payload": drifted_payload,
            "payload_digest": payload_digest(drifted_payload),
        }
    )

    with pytest.raises(NautilusBacktestError, match="input artifact"):
        validate_isolated_backtest_result(request, drifted)
