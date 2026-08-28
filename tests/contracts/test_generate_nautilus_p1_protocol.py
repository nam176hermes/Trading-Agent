from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.nautilus_runtime_contracts import (
    P1EngineConfigurationV1,
    P1InstrumentCatalogV1,
    P1MarketDataManifestV1,
    P1TargetScheduleV1,
)


ROOT = Path(__file__).parents[2]
GENERATOR = ROOT / "scripts/generate_nautilus_p1_protocol.py"
MODELS = {
    "engine_configuration": P1EngineConfigurationV1,
    "instrument_catalog": P1InstrumentCatalogV1,
    "market_data_manifest": P1MarketDataManifestV1,
    "target_schedule": P1TargetScheduleV1,
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


def test_generation_is_repeatable_and_check_detects_stale_output(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 0
    first = _generated_files(tmp_path)
    assert _run(tmp_path).returncode == 0
    assert _generated_files(tmp_path) == first
    assert _run(tmp_path, check=True).returncode == 0
    generated = tmp_path / "engines/nautilus/runtime_v1/generated_protocol.py"
    generated.write_bytes(generated.read_bytes() + b"# drift\n")
    assert _run(tmp_path, check=True).returncode == 1


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


def test_golden_fixtures_agree_on_stable_protocol_error_class() -> None:
    path = ROOT / "engines/nautilus/runtime_v1/generated_protocol.py"
    spec = importlib.util.spec_from_file_location("generated_protocol", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    golden = ROOT / "tests/fixtures/p1_nautilus/golden"
    for kind, model in MODELS.items():
        positive = (golden / "positive" / f"{kind}.json").read_bytes()
        value = module.load_canonical_json(positive)
        module.validate_document(kind, value)
        model.model_validate_json(positive)

        negative = (golden / "negative" / f"{kind}.json").read_bytes()
        invalid = json.loads(negative)
        with pytest.raises(ValidationError):
            model.model_validate(invalid)
        with pytest.raises(module.ProtocolValidationError) as error:
            module.validate_document(kind, invalid)
        assert error.value.code == "E_PROTOCOL"


def test_committed_generation_is_current_and_schemas_are_present() -> None:
    assert _run(ROOT, check=True).returncode == 0
    for schema_path in ROOT.glob("schemas/nautilus-p1-*.schema.json"):
        assert json.loads(schema_path.read_text(encoding="utf-8"))
