from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

EXPECTED_COMMAND_TYPES = (
    "DescribeEngineCapabilities",
    "ValidateEngineConfiguration",
    "ValidateInstrumentCatalog",
    "ValidateStrategyConfiguration",
    "InspectEngineRun",
    "RunBacktest",
    "RunBacktestSimulation",
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
        "client_order_id": "client-1",
        "venue_order_id": None,
        "strategy_id": "strategy-1",
        "trader_id": "trader-1",
        "account_id": "account-1",
        "execution_client_id": "execution-client-1",
        "order_list_id": None,
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
        "trigger_price": None,
        "trailing_offset": None,
        "gtd_expiry": None,
        "post_only": False,
        "reduce_only": False,
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


def test_parse_command_accepts_a_decoded_uuid_bearing_wire_mapping() -> None:
    contracts = import_module("packages.engine_contracts")
    engine_run_id = uuid4()
    wire = json.loads(
        json.dumps(
            {
                "command_type": "CancelBacktest",
                "target_engine_run_id": str(engine_run_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    command = contracts.parse_command(wire)

    assert isinstance(command, contracts.CancelBacktest)
    assert command.target_engine_run_id == engine_run_id


def test_parse_command_accepts_a_decoded_nested_order_wire_mapping() -> None:
    contracts = import_module("packages.engine_contracts")
    wire = json.loads(
        json.dumps(
            order_intent_command_json(),
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    command = contracts.parse_command(wire)

    assert isinstance(command, contracts.SubmitOrderIntent)
    assert isinstance(command.order_intent.intent_id, UUID)
    assert command.order_intent.quantity.value == Decimal("1.25")
    assert command.order_intent.side.value == "buy"


def test_engine_quantity_accepts_128_coefficient_digits_and_rejects_129() -> None:
    contracts = import_module("packages.engine_contracts")
    maximum = Decimal("9" * 128)

    quantity = contracts.EngineQuantity(value=maximum, precision=0)

    assert quantity.value == maximum
    with pytest.raises(ValidationError, match="maximum quantity magnitude"):
        contracts.EngineQuantity(value=Decimal("9" * 129), precision=0)


def test_engine_order_quantity_rejects_signed_values_at_the_wire_boundary() -> None:
    contracts = import_module("packages.engine_contracts")
    accepted = ("9" * 110) + "." + ("9" * 18)
    negative = "-" + accepted
    oversized = ("9" * 111) + "." + ("9" * 18)

    assert contracts.EngineQuantity(value=Decimal(accepted), precision=18).value == Decimal(
        accepted
    )
    assert contracts.EngineQuantity.model_validate_json(
        json.dumps({"value": accepted, "precision": 18})
    ).value == Decimal(accepted)

    with pytest.raises(ValidationError, match="non-negative"):
        contracts.EngineQuantity(value=Decimal(negative), precision=18)
    with pytest.raises(ValidationError, match="non-negative"):
        contracts.EngineQuantity.model_validate_json(
            json.dumps({"value": negative, "precision": 18})
        )
    with pytest.raises(ValidationError, match="maximum quantity magnitude"):
        contracts.EngineQuantity(value=Decimal(oversized), precision=18)
    with pytest.raises(ValidationError, match="maximum quantity magnitude"):
        contracts.EngineQuantity.model_validate_json(
            json.dumps({"value": oversized, "precision": 18})
        )


def test_engine_order_instruction_mirrors_typed_trigger_gtd_flag_and_identity_data() -> None:
    contracts = import_module("packages.engine_contracts")
    payload = order_intent_command_json()
    intent = payload["order_intent"]
    assert isinstance(intent, dict)
    intent.update(
        {
            "order_type": "stop_limit",
            "time_in_force": "gtd",
            "trigger_price": {"amount": "99", "currency": "USD"},
            "gtd_expiry": "2026-08-05T18:30:00Z",
            "post_only": True,
            "reduce_only": True,
        }
    )

    command = contracts.SubmitOrderIntent.model_validate_json(json.dumps(payload))

    assert command.order_intent.client_order_id == "client-1"
    assert command.order_intent.order_type.value == "stop_limit"
    assert command.order_intent.gtd_expiry == datetime(2026, 8, 5, 18, 30, tzinfo=UTC)
    assert command.order_intent.post_only is True
    assert command.order_intent.reduce_only is True

    intent["client_order_id"] = "bad/value"
    with pytest.raises(ValidationError, match="client_order_id"):
        contracts.SubmitOrderIntent.model_validate_json(json.dumps(payload))


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


def _simulation_command_json() -> dict[str, object]:
    references = [
        {
            "artifact_id": f"{number}" * 8 + "-1111-4111-8111-111111111111",
            "sha256": f"{number}" * 64,
            "media_type": "application/jsonl" if number == 4 else "application/json",
        }
        for number in range(1, 6)
    ]
    return {
        "command_type": "RunBacktestSimulation",
        "engine_configuration": references[0],
        "instrument_catalog": references[1],
        "strategy_configuration": references[2],
        "market_data": references[3],
        "simulation_scenario": references[4],
        "start_time": "2026-08-05T12:00:00Z",
        "end_time": "2026-08-05T12:30:00Z",
    }


def test_run_backtest_simulation_is_a_distinct_five_artifact_command() -> None:
    contracts = import_module("packages.engine_contracts")
    payload = _simulation_command_json()

    command = contracts.parse_command(payload)

    assert isinstance(command, contracts.RunBacktestSimulation)
    assert command.simulation_scenario.sha256 == "5" * 64
    with pytest.raises(ValidationError, match="simulation_scenario"):
        contracts.RunBacktestSimulation.model_validate_json(
            json.dumps({key: value for key, value in payload.items() if key != "simulation_scenario"})
        )
    with pytest.raises(ValidationError, match="duplicate artifact"):
        contracts.RunBacktestSimulation.model_validate_json(
            json.dumps({**payload, "simulation_scenario": payload["engine_configuration"]})
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        contracts.RunBacktestSimulation.model_validate_json(
            json.dumps({**payload, "profile": "execution-simulation"})
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "A" * 64, "string_pattern_mismatch"),
        ("media_type", "application/octet-stream", "literal_error"),
    ],
)
def test_run_backtest_simulation_rejects_changed_scenario_identity(
    field: str, value: str, message: str
) -> None:
    contracts = import_module("packages.engine_contracts")
    payload = _simulation_command_json()
    scenario = payload["simulation_scenario"]
    assert isinstance(scenario, dict)
    scenario[field] = value

    with pytest.raises(ValidationError, match=message):
        contracts.RunBacktestSimulation.model_validate_json(json.dumps(payload))


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
