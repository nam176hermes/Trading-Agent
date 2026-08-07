"""Fail-closed root-side validation of isolated Nautilus backtest results."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

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
_SIMULATION_RESULT_FIELDS = {
    "input_artifacts_sha256", "scenario_digest", "scenario_id", "event_digest",
    "iterations", "total_events", "total_orders", "total_fills", "total_positions",
    "filled_quantity", "remaining_quantity", "position_quantity", "average_entry_price",
    "fees", "realized_pnl", "unrealized_pnl", "stop_take_profit_precedence",
}
_SIMULATION_EVENT_TYPE = "NautilusBacktestSimulationCompleted"


class NautilusBacktestError(ValueError):
    """A result does not prove the exact isolated, zero-order backtest."""


class BacktestExpectedOutcomeV1(BaseModel):
    """Independent expected outcome used to reject simulation parity drift."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scenario_id: Literal[
        "long-accounting", "short-accounting", "partial-fill",
        "same-bar-stop-take-profit", "stale-quote", "zero-liquidity",
        "session-boundary", "event-digest",
    ]
    scenario_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    iterations: StrictInt
    total_events: StrictInt
    total_orders: StrictInt
    total_fills: StrictInt
    total_positions: StrictInt
    filled_quantity: Decimal
    remaining_quantity: Decimal
    position_quantity: Decimal
    average_entry_price: Decimal
    fees: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    stop_take_profit_precedence: Literal["stop-first"]


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


def _simulation_input_artifacts_sha256(request: object) -> str:
    names = (
        "engine_configuration", "instrument_catalog", "strategy_configuration",
        "market_data", "simulation_scenario",
    )
    return hashlib.sha256(canonical_json_bytes({name: getattr(request, name).sha256 for name in names})).hexdigest()


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
    total_fills: int | None = None
    scenario_id: str | None = None
    scenario_digest: str | None = None
    event_digest: str | None = None
    filled_quantity: Decimal | None = None
    remaining_quantity: Decimal | None = None
    position_quantity: Decimal | None = None
    average_entry_price: Decimal | None = None
    fees: Decimal | None = None
    realized_pnl: Decimal | None = None
    unrealized_pnl: Decimal | None = None


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


def validate_isolated_simulation_result(
    request: EngineCommandEnvelope,
    event: EngineEventEnvelope,
    expected: BacktestExpectedOutcomeV1,
) -> NautilusBacktestResult:
    """Accept only a complete, five-input bound simulation equal to its oracle."""
    from packages.engine_contracts import RunBacktestSimulation

    if type(request) is not EngineCommandEnvelope or type(request.payload) is not RunBacktestSimulation:
        raise NautilusBacktestError("exact RunBacktestSimulation command envelope is required")
    if type(event) is not EngineEventEnvelope or type(expected) is not BacktestExpectedOutcomeV1:
        raise NautilusBacktestError("exact simulation result authority is required")
    if payload_digest(request.payload) != request.payload_digest or payload_digest(event.payload) != event.payload_digest:
        raise NautilusBacktestError("command or result payload digest is invalid")
    if (
        event.correlation_id != request.correlation_id or event.causation_id != request.message_id
        or event.engine_run_id != request.engine_run_id or event.stream_sequence != request.stream_sequence + 1
        or event.schema_version != request.schema_version or event.producer_identity != request.producer_identity
        or event.source_commit != request.source_commit or event.config_digest != request.config_digest
        or event.payload.event_type != _SIMULATION_EVENT_TYPE or event.payload.family is not EventFamily.ENGINE_LIFECYCLE
    ):
        raise NautilusBacktestError("simulation result envelope is not bound to the command")
    attributes = {attribute.name: attribute.value for attribute in event.payload.attributes}
    if set(attributes) != _SIMULATION_RESULT_FIELDS:
        raise NautilusBacktestError("simulation result attributes are incomplete")
    expected_inputs = _simulation_input_artifacts_sha256(request.payload)
    if not isinstance(attributes["input_artifacts_sha256"], str) or not hmac.compare_digest(attributes["input_artifacts_sha256"], expected_inputs):
        raise NautilusBacktestError("simulation input artifact digest does not match command")
    if not isinstance(attributes["scenario_digest"], str) or not hmac.compare_digest(attributes["scenario_digest"], expected.scenario_digest) or not hmac.compare_digest(expected.scenario_digest, request.payload.simulation_scenario.sha256):
        raise NautilusBacktestError("simulation scenario digest does not match expected inputs")
    for name in ("iterations", "total_events", "total_orders", "total_fills", "total_positions"):
        if type(attributes[name]) is not int or attributes[name] < 0:
            raise NautilusBacktestError("simulation counters are invalid")
    decimal_names = ("filled_quantity", "remaining_quantity", "position_quantity", "average_entry_price", "fees", "realized_pnl", "unrealized_pnl")
    observed_decimals: dict[str, Decimal] = {}
    for name in decimal_names:
        value = attributes[name]
        if not isinstance(value, str):
            raise NautilusBacktestError("simulation Decimal attributes are invalid")
        try:
            observed_decimals[name] = Decimal(value)
        except Exception as exc:
            raise NautilusBacktestError("simulation Decimal attributes are invalid") from exc
    for name in ("scenario_id", "event_digest", "stop_take_profit_precedence", "iterations", "total_events", "total_orders", "total_fills", "total_positions"):
        if attributes[name] != getattr(expected, name):
            raise NautilusBacktestError("simulation result does not equal expected outcome")
    for name, value in observed_decimals.items():
        if value != getattr(expected, name):
            raise NautilusBacktestError("simulation result does not equal expected outcome")
    request_sha256 = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
    event_sha256 = hashlib.sha256(canonical_json_bytes(event)).hexdigest()
    return NautilusBacktestResult(
        result_sha256=hashlib.sha256(canonical_json_bytes({"event_sha256": event_sha256, "input_artifacts_sha256": expected_inputs, "request_sha256": request_sha256})).hexdigest(),
        input_artifacts_sha256=expected_inputs, iterations=attributes["iterations"], total_events=attributes["total_events"], total_orders=attributes["total_orders"], total_positions=attributes["total_positions"], total_fills=attributes["total_fills"], scenario_id=expected.scenario_id, scenario_digest=expected.scenario_digest, event_digest=expected.event_digest,
        **observed_decimals,
    )
