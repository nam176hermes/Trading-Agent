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


def target_portfolio_command_json() -> dict[str, object]:
    return {
        "command_type": "SubmitTargetPortfolio",
        "target_portfolio": {
            "target_id": str(uuid4()),
            "positions": [
                {
                    "instrument": {
                        "symbol": "BTC-USD",
                        "product_type": "crypto_spot",
                        "venue": "ALPACA",
                    },
                    "target_weight": "0.25",
                }
            ],
            "source_signal_ids": [str(uuid4())],
            "effective_at": "2026-08-04T18:30:00Z",
            "schema_version": "1.0.0",
        },
    }


def order_intent_command_json(*, modify: bool = False) -> dict[str, object]:
    intent = {
        "intent_id": str(uuid4()),
        "risk_decision_id": str(uuid4()),
        "instrument": {
            "symbol": "BTC-USD",
            "product_type": "crypto_spot",
            "venue": "ALPACA",
        },
        "side": "buy",
        "order_type": "limit",
        "time_in_force": "day",
        "quantity": {"value": "1.25", "precision": 2},
        "limit_price": {"amount": "100", "currency": "USD"},
        "requested_at": "2026-08-04T18:30:00Z",
        "schema_version": "1.0.0",
    }
    if modify:
        return {
            "command_type": "ModifyOrderIntent",
            "order_id": str(uuid4()),
            "replacement_order_intent": intent,
        }
    return {"command_type": "SubmitOrderIntent", "order_intent": intent}


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


def test_target_portfolio_rejects_unknown_nested_instrument_field() -> None:
    contracts = import_module("packages.engine_contracts")
    payload = target_portfolio_command_json()
    portfolio = payload["target_portfolio"]
    assert isinstance(portfolio, dict)
    positions = portfolio["positions"]
    assert isinstance(positions, list)
    instrument = positions[0]["instrument"]
    assert isinstance(instrument, dict)
    instrument["provider_symbol"] = "private"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        contracts.SubmitTargetPortfolio.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("modify", [False, True], ids=["submit", "modify"])
@pytest.mark.parametrize("nested_object", ["instrument", "quantity", "limit_price"])
def test_order_submission_rejects_unknown_recursive_field(
    modify: bool, nested_object: str
) -> None:
    contracts = import_module("packages.engine_contracts")
    payload = order_intent_command_json(modify=modify)
    intent_key = "replacement_order_intent" if modify else "order_intent"
    intent = payload[intent_key]
    assert isinstance(intent, dict)
    nested = intent[nested_object]
    assert isinstance(nested, dict)
    nested["provider_value"] = "private"
    model = contracts.ModifyOrderIntent if modify else contracts.SubmitOrderIntent

    with pytest.raises(ValidationError, match="extra_forbidden"):
        model.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("effective_at", "2026-08-04T18:30:00+00:00"),
        ("schema_version", "2.0.0"),
    ],
)
def test_target_portfolio_submission_rejects_noncanonical_nested_authority(
    field: str, invalid: str
) -> None:
    contracts = import_module("packages.engine_contracts")
    payload = target_portfolio_command_json()
    portfolio = payload["target_portfolio"]
    assert isinstance(portfolio, dict)
    portfolio[field] = invalid

    with pytest.raises(ValidationError, match="canonical|1.0.0"):
        contracts.SubmitTargetPortfolio.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("modify", [False, True], ids=["submit", "modify"])
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("requested_at", "2026-08-04T18:30:00+00:00"),
        ("schema_version", "2.0.0"),
    ],
)
def test_order_submission_rejects_noncanonical_nested_authority(
    modify: bool, field: str, invalid: str
) -> None:
    contracts = import_module("packages.engine_contracts")
    payload = order_intent_command_json(modify=modify)
    intent_key = "replacement_order_intent" if modify else "order_intent"
    intent = payload[intent_key]
    assert isinstance(intent, dict)
    intent[field] = invalid
    model = contracts.ModifyOrderIntent if modify else contracts.SubmitOrderIntent

    with pytest.raises(ValidationError, match="canonical|1.0.0"):
        model.model_validate_json(json.dumps(payload))
