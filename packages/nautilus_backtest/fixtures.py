"""Canonical source-owned fixtures for isolated Nautilus simulation parity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    RunBacktestSimulation,
    payload_digest,
)

from .scenarios import ScenarioId


SCENARIO_IDS: tuple[
    ScenarioId,
    ScenarioId,
    ScenarioId,
    ScenarioId,
    ScenarioId,
    ScenarioId,
    ScenarioId,
    ScenarioId,
] = (
    "long-accounting",
    "short-accounting",
    "partial-fill",
    "same-bar-stop-take-profit",
    "stale-quote",
    "zero-liquidity",
    "session-boundary",
    "event-digest",
)


@dataclass(frozen=True, slots=True)
class CanonicalSimulationFixtureV1:
    scenario_id: ScenarioId
    engine_configuration: bytes
    instrument_catalog: bytes
    strategy_configuration: bytes
    market_data: bytes
    simulation_scenario: bytes

    @property
    def artifacts(self) -> tuple[bytes, bytes, bytes, bytes, bytes]:
        return (
            self.engine_configuration,
            self.instrument_catalog,
            self.strategy_configuration,
            self.market_data,
            self.simulation_scenario,
        )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_canonical_simulation_fixture(
    scenario_id: ScenarioId,
) -> CanonicalSimulationFixtureV1:
    """Build one of the eight sealed five-artifact simulation fixtures."""
    if scenario_id not in SCENARIO_IDS:
        raise ValueError("supported canonical simulation scenario is required")
    target_quantity = "-2" if scenario_id == "short-accounting" else "1"
    if scenario_id == "long-accounting":
        target_quantity = "2"
    elif scenario_id == "partial-fill":
        target_quantity = "3"
    configuration = _canonical(
        {
            "execution_mode": "execution-simulation",
            "run_analysis": False,
            "schema_version": "nautilus-backtest-engine-config-v1",
        }
    )
    strategy = _canonical(
        {
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [
                {
                    "instrument": {
                        "product_type": "crypto_spot",
                        "symbol": "BTCUSDT",
                        "venue": "BINANCE",
                    },
                    "target_quantity": target_quantity,
                }
            ],
            "schema_version": "nautilus-execution-target-v1",
        }
    )
    events: list[dict[str, object]] = [
        {
            "ask": "100",
            "bid": "99",
            "close": "101",
            "event_time": "2026-08-05T12:00:00Z",
            "high": "102",
            "low": "98",
            "open": "100",
            "quote_time": "2026-08-05T12:00:00Z",
            "sequence": 1,
            "session_open": True,
            "volume": "2",
        }
    ]
    liquidity_limit = "10"
    stop_price: str | None = None
    take_profit_price: str | None = None
    if scenario_id == "partial-fill":
        events[0]["volume"] = "1"
        liquidity_limit = "1"
    elif scenario_id == "same-bar-stop-take-profit":
        events[0]["high"] = "103"
        events[0]["low"] = "97"
        stop_price = "98"
        take_profit_price = "102"
    elif scenario_id == "stale-quote":
        events[0]["quote_time"] = "2026-08-05T11:58:00Z"
    elif scenario_id == "zero-liquidity":
        liquidity_limit = "0"
    elif scenario_id == "session-boundary":
        events[0]["session_open"] = False
        events.append(
            {
                "ask": "102",
                "bid": "101",
                "close": "102",
                "event_time": "2026-08-05T12:01:00Z",
                "high": "103",
                "low": "100",
                "open": "101",
                "quote_time": "2026-08-05T12:01:00Z",
                "sequence": 2,
                "session_open": True,
                "volume": "2",
            }
        )
    market_rows = [
        {
            "close": event["close"],
            "high": event["high"],
            "low": event["low"],
            "open": event["open"],
            "open_time": event["event_time"],
            "volume": event["volume"],
        }
        for event in events
    ]
    market = b"".join(_canonical(row) + b"\n" for row in market_rows)
    catalog = _canonical(
        {
            "canonical_rows_sha256": hashlib.sha256(
                _canonical(market_rows)
            ).hexdigest(),
            "content_digest": "a" * 64,
            "continuity": {
                "duplicate_report": [],
                "gap_report": [],
                "timeframe": "1m",
            },
            "fetched_at": "2026-08-05T12:02:00Z",
            "first_event_at": events[0]["event_time"],
            "importer_version": "fixture-catalog-v1",
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTCUSDT",
                "venue": "BINANCE",
            },
            "known_at": "2026-08-05T12:02:00Z",
            "last_event_at": events[-1]["event_time"],
            "normalization_version": "market-normalization-v1",
            "observed_at": "2026-08-05T12:02:00Z",
            "parquet_sha256": "b" * 64,
            "provider": "deterministic-fixture-v1",
            "provenance_schema_version": "market-data-v1",
            "raw_evidence_sha256": "c" * 64,
            "row_count": len(events),
            "schema_version": "market-dataset-manifest-v1",
            "snapshot_schema_version": "market-snapshot-v1",
            "timeframe": "1m",
        }
    )
    scenario = _canonical(
        {
            "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
            "events": events,
            "fee_rate": "0.001",
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTCUSDT",
                "venue": "BINANCE",
            },
            "liquidity_limit": liquidity_limit,
            "market_data_sha256": hashlib.sha256(market).hexdigest(),
            "scenario_id": scenario_id,
            "schema_version": "nautilus-execution-scenario-v1",
            "session_policy": "explicit-open-flag-v1",
            "slippage_bps": "0",
            "stale_quote_threshold_seconds": 30,
            "stop_price": stop_price,
            "stop_take_profit_precedence": "stop-first",
            "strategy_sha256": hashlib.sha256(strategy).hexdigest(),
            "take_profit_price": take_profit_price,
        }
    )
    return CanonicalSimulationFixtureV1(
        scenario_id=scenario_id,
        engine_configuration=configuration,
        instrument_catalog=catalog,
        strategy_configuration=strategy,
        market_data=market,
        simulation_scenario=scenario,
    )


def build_simulation_envelope(
    fixture: CanonicalSimulationFixtureV1,
) -> EngineCommandEnvelope:
    """Bind one exact canonical fixture into a simulation command envelope."""
    if type(fixture) is not CanonicalSimulationFixtureV1:
        raise TypeError("exact CanonicalSimulationFixtureV1 is required")
    references = tuple(
        ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}"
                "-1111-4111-8111-111111111111"
            ),
            sha256=hashlib.sha256(value).hexdigest(),
            media_type="application/jsonl" if index == 4 else "application/json",
        )
        for index, value in enumerate(fixture.artifacts, start=1)
    )
    command = RunBacktestSimulation(
        command_type="RunBacktestSimulation",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        simulation_scenario=references[4],
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


__all__ = [
    "SCENARIO_IDS",
    "CanonicalSimulationFixtureV1",
    "build_canonical_simulation_fixture",
    "build_simulation_envelope",
]
