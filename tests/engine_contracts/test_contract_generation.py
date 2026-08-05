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
}
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
    command_names = {
        name
        for name in schema["$defs"]
        if name
        in {
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
        }
    }
    assert len(command_names) == 17
    assert all("Live" not in name for name in schema["$defs"])
