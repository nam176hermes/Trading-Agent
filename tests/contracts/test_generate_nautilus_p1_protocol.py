from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts import generate_nautilus_p1_protocol as generator

from packages.nautilus_runtime_contracts import (
    P1EngineConfigurationV1,
    P1InstrumentCatalogV1,
    P1MarketDataManifestV1,
    P1TargetScheduleV1,
)
from packages.nautilus_runtime_contracts.events import P1_EVENT_ADAPTER
from packages.nautilus_runtime_contracts.versions import (
    MAX_ENGINE_CONFIGURATION_BYTES,
    MAX_INSTRUMENT_CATALOG_BYTES,
    MAX_MARKET_DATA_MANIFEST_BYTES,
    MAX_TARGET_SCHEDULE_BYTES,
    P1_ENGINE_CONFIGURATION_SCHEMA,
    P1_INSTRUMENT_CATALOG_SCHEMA,
    P1_MARKET_DATA_MANIFEST_SCHEMA,
    P1_TARGET_SCHEDULE_SCHEMA,
)


ROOT = Path(__file__).parents[2]
GENERATOR = ROOT / "scripts/generate_nautilus_p1_protocol.py"
MODELS = {
    "engine_configuration": P1EngineConfigurationV1,
    "instrument_catalog": P1InstrumentCatalogV1,
    "market_data_manifest": P1MarketDataManifestV1,
    "target_schedule": P1TargetScheduleV1,
}
EXPECTED_SCHEMAS = {
    "engine_configuration": P1_ENGINE_CONFIGURATION_SCHEMA,
    "instrument_catalog": P1_INSTRUMENT_CATALOG_SCHEMA,
    "market_data_manifest": P1_MARKET_DATA_MANIFEST_SCHEMA,
    "target_schedule": P1_TARGET_SCHEDULE_SCHEMA,
}
EXPECTED_MAX_BYTES = {
    "engine_configuration": MAX_ENGINE_CONFIGURATION_BYTES,
    "instrument_catalog": MAX_INSTRUMENT_CATALOG_BYTES,
    "market_data_manifest": MAX_MARKET_DATA_MANIFEST_BYTES,
    "target_schedule": MAX_TARGET_SCHEDULE_BYTES,
}


def _run(root: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(GENERATOR), "--output-root", str(root)]
    if check:
        command.append("--check")
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)


def _generated_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_generation_is_repeatable_and_check_detects_stale_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(tmp_path).returncode == 0
    first = _generated_files(tmp_path)
    assert _run(tmp_path).returncode == 0
    assert _generated_files(tmp_path) == first
    assert _run(tmp_path, check=True).returncode == 0
    generated = tmp_path / "engines/nautilus/runtime_v1/generated_protocol.py"
    generated.write_bytes(generated.read_bytes() + b"# drift\n")
    assert _run(tmp_path, check=True).returncode == 1
    assert _run(tmp_path).returncode == 0
    monkeypatch.setitem(
        generator.ARTIFACT_SCHEMAS,
        "engine_configuration",
        "nautilus-p1-engine-configuration-v999",
    )
    assert generator.generate(tmp_path, check=True) == 1


def test_generated_module_imports_with_isolated_stdlib_python() -> None:
    result = subprocess.run(
        [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "-c",
            "import runpy,sys; runpy.run_path(sys.argv[1], run_name='generated_protocol')",
            str(ROOT / "engines/nautilus/runtime_v1/generated_protocol.py"),
        ],
        cwd="/tmp",
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def _module():
    path = ROOT / "engines/nautilus/runtime_v1/generated_protocol.py"
    spec = importlib.util.spec_from_file_location("generated_protocol", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_golden_fixtures_agree_on_stable_protocol_error_class() -> None:
    module = _module()
    golden = ROOT / "tests/fixtures/p1_nautilus/golden"
    for kind, model in MODELS.items():
        positive = (golden / "positive" / f"{kind}.json").read_bytes()
        value = module.load_document(kind, positive)
        model.model_validate_json(positive)

        negative = (golden / "negative" / f"{kind}.json").read_bytes()
        invalid = json.loads(negative)
        with pytest.raises(ValidationError):
            model.model_validate(invalid)
        with pytest.raises(module.ProtocolValidationError) as error:
            module.validate_document(kind, invalid)
        assert error.value.code == "E_PROTOCOL"

    positive_events = (golden / "positive" / "event_stream.jsonl").read_bytes()
    negative_events = (golden / "negative" / "event_stream.jsonl").read_bytes()
    for raw in positive_events.splitlines():
        value = module.load_event(raw + b"\n")
        P1_EVENT_ADAPTER.validate_json(raw)
    invalid_event = json.loads(negative_events)
    with pytest.raises(ValidationError):
        P1_EVENT_ADAPTER.validate_python(invalid_event)
    with pytest.raises(module.ProtocolValidationError):
        module.validate_document(invalid_event["event_type"], invalid_event)


def test_generated_constants_and_semantic_validation_match_root_contracts() -> None:
    module = _module()
    assert module.ARTIFACT_SCHEMAS == EXPECTED_SCHEMAS
    assert module.MAX_DOCUMENT_BYTES == EXPECTED_MAX_BYTES
    golden = ROOT / "tests/fixtures/p1_nautilus/golden/positive"

    artifact_mutations = (
        ("engine_configuration", {"starting_balance": "1"}),
        ("engine_configuration", {"fee_rate": "0"}),
        ("instrument_catalog", {"tick_size": "0"}),
        (
            "market_data_manifest",
            {"first_timestamp": "2026-08-05T12:02:00Z"},
        ),
        (
            "market_data_manifest",
            {"first_timestamp": "2026-02-30T12:00:00Z"},
        ),
    )
    for kind, updates in artifact_mutations:
        value = json.loads((golden / f"{kind}.json").read_bytes())
        value.update(updates)
        with pytest.raises(ValidationError):
            MODELS[kind].model_validate(value)
        with pytest.raises(module.ProtocolValidationError):
            module.validate_document(kind, value)

    with pytest.raises(module.ProtocolValidationError, match="size"):
        module.load_document(
            "engine_configuration", b" " * (MAX_ENGINE_CONFIGURATION_BYTES + 1)
        )
    with pytest.raises(module.ProtocolValidationError):
        module.load_event(b'{"event_type":"RunStarted","x":"\\ud800"}\n')
    unicode_decimal = json.loads((golden / "instrument_catalog.json").read_bytes())
    unicode_decimal["tick_size"] = "١"
    with pytest.raises(ValidationError):
        P1InstrumentCatalogV1.model_validate(unicode_decimal)
    with pytest.raises(module.ProtocolValidationError):
        module.validate_document("instrument_catalog", unicode_decimal)

    schedule = json.loads((golden / "target_schedule.json").read_bytes())
    schedule_mutations = []
    duplicate_target = json.loads(json.dumps(schedule))
    duplicate_target["targets"][1]["target_id"] = duplicate_target["targets"][0]["target_id"]
    schedule_mutations.append(duplicate_target)
    duplicate_signal = json.loads(json.dumps(schedule))
    duplicate_signal["targets"][0]["source_signal_ids"] *= 2
    schedule_mutations.append(duplicate_signal)
    duplicate_time = json.loads(json.dumps(schedule))
    duplicate_time["targets"][1]["effective_at"] = duplicate_time["targets"][0]["effective_at"]
    schedule_mutations.append(duplicate_time)
    unordered = json.loads(json.dumps(schedule))
    unordered["targets"].reverse()
    schedule_mutations.append(unordered)
    empty_positions = json.loads(json.dumps(schedule))
    empty_positions["targets"][0]["positions"] = []
    schedule_mutations.append(empty_positions)
    unsupported = json.loads(json.dumps(schedule))
    unsupported["targets"][0]["positions"][0]["instrument"]["symbol"] = "ETHUSDT"
    schedule_mutations.append(unsupported)
    negative_weight = json.loads(json.dumps(schedule))
    negative_weight["targets"][0]["positions"][0]["target_weight"] = "-1"
    schedule_mutations.append(negative_weight)
    for value in schedule_mutations:
        with pytest.raises(ValidationError):
            P1TargetScheduleV1.model_validate(value)
        with pytest.raises(module.ProtocolValidationError):
            module.validate_document("target_schedule", value)

    events = [
        json.loads(raw)
        for raw in (golden / "event_stream.jsonl").read_bytes().splitlines()
    ]
    event_mutations = (
        (1, {"target_weight": "2"}),
        (1, {"source_signal_ids": ["signal-1", "signal-1"]}),
        (2, {"quantity": "-1"}),
        (3, {"quantity": "0"}),
        (4, {"quantity": "0"}),
        (4, {"price": "0"}),
        (4, {"fee": "-1"}),
        (5, {"quantity": "-1"}),
        (6, {"cash_balance": "-1"}),
        (7, {"final_cash": "-1"}),
        (0, {"simulation_time": "2026-02-30T12:00:00Z"}),
    )
    for index, updates in event_mutations:
        value = {**events[index], **updates}
        with pytest.raises(ValidationError):
            P1_EVENT_ADAPTER.validate_python(value)
        with pytest.raises(module.ProtocolValidationError):
            module.validate_document(value["event_type"], value)


def test_committed_generation_is_current_and_schemas_are_present() -> None:
    assert _run(ROOT, check=True).returncode == 0
    for schema_path in ROOT.glob("schemas/nautilus-p1-*.schema.json"):
        assert json.loads(schema_path.read_text(encoding="utf-8"))
