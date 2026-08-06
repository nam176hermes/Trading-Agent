"""Fail-closed root-side validation of isolated Nautilus backtest results."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from packages.engine_contracts import (
    EngineCommandEnvelope,
    EngineEventEnvelope,
    EventFamily,
    RunBacktest,
    canonical_json_bytes,
    payload_digest,
)


_EVENT_TYPE = "NautilusBacktestCompleted"
_EXPECTED_RESULT_FIELDS = {
    "input_artifacts_sha256",
    "iterations",
    "total_events",
    "total_orders",
    "total_positions",
}


class NautilusBacktestError(ValueError):
    """A result does not prove the exact isolated, zero-order backtest."""


def _input_artifacts_sha256(request: RunBacktest) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "engine_configuration": request.engine_configuration.sha256,
                "instrument_catalog": request.instrument_catalog.sha256,
                "strategy_configuration": request.strategy_configuration.sha256,
                "market_data": request.market_data.sha256,
            }
        )
    ).hexdigest()


def _result_attributes(event: EngineEventEnvelope) -> dict[str, object]:
    payload = event.payload
    if payload.event_type != _EVENT_TYPE or payload.family is not EventFamily.ENGINE_LIFECYCLE:
        raise NautilusBacktestError("result event is not a Nautilus backtest completion")
    attributes = {attribute.name: attribute.value for attribute in payload.attributes}
    if set(attributes) != _EXPECTED_RESULT_FIELDS:
        raise NautilusBacktestError("result event has an incomplete attribute set")
    for name in ("iterations", "total_events", "total_orders", "total_positions"):
        value = attributes[name]
        if type(value) is not int or value < 0:
            raise NautilusBacktestError("result counters are invalid")
    if attributes["total_orders"] != 0 or attributes["total_positions"] != 0:
        raise NautilusBacktestError("zero-order backtest emitted an execution effect")
    input_digest = attributes["input_artifacts_sha256"]
    if not isinstance(input_digest, str) or len(input_digest) != 64:
        raise NautilusBacktestError("result input artifact digest is invalid")
    return attributes


@dataclass(frozen=True, slots=True)
class NautilusBacktestResult:
    """One deterministic, paper-only result bound to a command and event."""

    result_sha256: str
    input_artifacts_sha256: str
    iterations: int
    total_events: int
    total_orders: int
    total_positions: int


def validate_isolated_backtest_result(
    request: EngineCommandEnvelope, event: EngineEventEnvelope
) -> NautilusBacktestResult:
    """Accept only the event produced for this exact command's four inputs."""

    if type(request) is not EngineCommandEnvelope or type(request.payload) is not RunBacktest:
        raise NautilusBacktestError("exact RunBacktest command envelope is required")
    if type(event) is not EngineEventEnvelope:
        raise NautilusBacktestError("exact engine event envelope is required")
    if (
        payload_digest(request.payload) != request.payload_digest
        or payload_digest(event.payload) != event.payload_digest
    ):
        raise NautilusBacktestError("command or result payload digest is invalid")
    if (
        event.correlation_id != request.correlation_id
        or event.causation_id != request.message_id
        or event.engine_run_id != request.engine_run_id
        or event.stream_sequence != request.stream_sequence + 1
        or event.schema_version != request.schema_version
        or event.producer_identity != request.producer_identity
        or event.source_commit != request.source_commit
        or event.config_digest != request.config_digest
    ):
        raise NautilusBacktestError("result envelope is not bound to the command")
    attributes = _result_attributes(event)
    expected_inputs = _input_artifacts_sha256(request.payload)
    observed_inputs = attributes["input_artifacts_sha256"]
    assert isinstance(observed_inputs, str)
    if not hmac.compare_digest(observed_inputs, expected_inputs):
        raise NautilusBacktestError("result input artifact digest does not match command")
    request_sha256 = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
    event_sha256 = hashlib.sha256(canonical_json_bytes(event)).hexdigest()
    result_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "event_sha256": event_sha256,
                "input_artifacts_sha256": expected_inputs,
                "request_sha256": request_sha256,
            }
        )
    ).hexdigest()
    return NautilusBacktestResult(
        result_sha256=result_sha256,
        input_artifacts_sha256=expected_inputs,
        iterations=attributes["iterations"],
        total_events=attributes["total_events"],
        total_orders=attributes["total_orders"],
        total_positions=attributes["total_positions"],
    )
