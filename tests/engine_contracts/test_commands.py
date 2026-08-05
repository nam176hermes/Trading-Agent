from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError

EXPECTED_COMMAND_TYPES = (
    "DescribeEngineCapabilities",
    "ValidateEngineConfiguration",
    "ValidateInstrumentCatalog",
    "ValidateStrategyConfiguration",
    "InspectEngineRun",
    "RunBacktest",
    "CancelBacktest",
    "ExportBacktestReport",
    "StartPaperEngine",
    "StopPaperEngine",
    "SubmitTargetPortfolio",
    "SubmitOrderIntent",
    "ModifyOrderIntent",
    "CancelOrderIntent",
    "CancelAllOrders",
    "ClosePositionIntent",
    "RequestExecutionReconciliation",
)


def test_v1_command_registry_is_closed_and_has_no_live_command() -> None:
    contracts = import_module("packages.engine_contracts")
    command_types = contracts.COMMAND_TYPES

    assert command_types == EXPECTED_COMMAND_TYPES
    assert all("live" not in command_type.casefold() for command_type in command_types)


def test_parse_command_selects_the_registered_strict_immutable_model() -> None:
    contracts = import_module("packages.engine_contracts")
    command = contracts.parse_command(
        {"command_type": "DescribeEngineCapabilities"}
    )

    assert command == contracts.DescribeEngineCapabilities(
        command_type="DescribeEngineCapabilities"
    )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        contracts.parse_command(
            {
                "command_type": "DescribeEngineCapabilities",
                "provider_payload": {},
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        command.command_type = "DescribeEngineCapabilities"  # type: ignore[misc]


def test_parse_command_rejects_an_unsupported_command() -> None:
    contracts = import_module("packages.engine_contracts")

    with pytest.raises(ValueError, match="unsupported engine command"):
        contracts.parse_command({"command_type": "StartLiveEngine"})


def test_public_command_schemas_do_not_leak_provider_or_nautilus_types() -> None:
    contracts = import_module("packages.engine_contracts")
    schemas = json.dumps(
        [model.model_json_schema() for model in contracts.COMMAND_MODELS.values()],
        sort_keys=True,
    ).casefold()

    assert "nautilus" not in schemas
    assert "binance" not in schemas
    assert "coinbase" not in schemas
    assert "provider_payload" not in schemas


def test_backtest_window_accepts_only_canonical_utc_z_json() -> None:
    contracts = import_module("packages.engine_contracts")
    artifact = {
        "artifact_id": str(uuid4()),
        "sha256": "a" * 64,
        "media_type": "application/json",
    }
    payload = {
        "command_type": "RunBacktest",
        "engine_configuration": artifact,
        "instrument_catalog": artifact,
        "strategy_configuration": artifact,
        "market_data": {**artifact, "media_type": "application/jsonl"},
        "start_time": "2026-08-04T18:30:00Z",
        "end_time": "2026-08-04T18:31:00Z",
    }
    command = contracts.RunBacktest.model_validate_json(json.dumps(payload))

    assert command.start_time == datetime(2026, 8, 4, 18, 30, tzinfo=UTC)
    assert command.end_time - command.start_time == timedelta(minutes=1)
    with pytest.raises(ValidationError, match="canonical"):
        contracts.RunBacktest.model_validate_json(
            json.dumps({**payload, "start_time": "2026-08-04T18:30:00+00:00"})
        )
