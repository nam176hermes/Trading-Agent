from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = ROOT / "generated" / "engine" / "json-schema"
EXPECTED_SCHEMAS = {
    "EngineCapabilities.json",
    "EngineCommandEnvelope.json",
    "EngineEventEnvelope.json",
    "EngineRunManifest.json",
    "PaperCompatibilityResultV1.json",
    "ValidatePaperCompatibility.json",
}
EXPECTED_COMMAND_NAMES = (
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
REQUIRED_ENVELOPE_FIELDS = {
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


def test_canonical_generator_publishes_only_top_level_engine_contracts() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_contracts.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path.name for path in SCHEMA_ROOT.glob("*.json")} == EXPECTED_SCHEMAS

    check = subprocess.run(
        [sys.executable, "scripts/generate_contracts.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr


def test_generated_envelopes_are_strict_versioned_and_require_authority_fields() -> None:
    for filename in ("EngineCommandEnvelope.json", "EngineEventEnvelope.json"):
        schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == REQUIRED_ENVELOPE_FIELDS
        assert schema["properties"]["schema_version"]["const"] == "1.0.0"
        assert schema["properties"]["stream_sequence"]["exclusiveMinimum"] == 0


def test_generated_command_envelope_contains_the_closed_v1_union() -> None:
    schema = json.loads(
        (SCHEMA_ROOT / "EngineCommandEnvelope.json").read_text(encoding="utf-8")
    )
    payload = schema["properties"]["payload"]
    assert payload["discriminator"] == {
        "mapping": {
            name: f"#/$defs/{name}" for name in EXPECTED_COMMAND_NAMES
        },
        "propertyName": "command_type",
    }
    assert payload["oneOf"] == [
        {"$ref": f"#/$defs/{name}"} for name in EXPECTED_COMMAND_NAMES
    ]


def test_generated_submission_dtos_are_recursively_strict_and_v1_canonical() -> None:
    schema = json.loads(
        (SCHEMA_ROOT / "EngineCommandEnvelope.json").read_text(encoding="utf-8")
    )
    definitions = schema["$defs"]
    expected_engine_dtos = {
        "EngineInstrumentId",
        "EngineOrderIntent",
        "EnginePrice",
        "EngineQuantity",
        "EngineTargetPortfolio",
        "EngineTargetPosition",
    }

    for name in expected_engine_dtos:
        assert definitions[name]["additionalProperties"] is False
    assert not {
        "InstrumentId",
        "OrderIntent",
        "Price",
        "Quantity",
        "TargetPortfolio",
        "TargetPosition",
    } & set(definitions)

    target = definitions["EngineTargetPortfolio"]["properties"]
    assert target["effective_at"]["pattern"].endswith("Z$")
    assert target["schema_version"]["const"] == "1.0.0"
    order = definitions["EngineOrderIntent"]["properties"]
    assert order["requested_at"]["pattern"].endswith("Z$")
    assert order["schema_version"]["const"] == "1.0.0"
    assert {
        "client_order_id",
        "strategy_id",
        "trader_id",
        "account_id",
        "execution_client_id",
    } <= set(definitions["EngineOrderIntent"]["required"])
    quantity_value = definitions["EngineQuantity"]["properties"]["value"]
    assert quantity_value["maxLength"] == 129
    assert "-?" not in quantity_value["pattern"]
