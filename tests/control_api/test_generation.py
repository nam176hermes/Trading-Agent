import json
import os
import subprocess
from pathlib import Path

from scripts import generate_contracts


ROOT = Path(__file__).resolve().parents[2]


def test_contract_generator_uses_the_canonical_dashboard_toolchain() -> None:
    assert generate_contracts.DEFAULT_TOOL_ROOT == ROOT / "apps" / "dashboard"

    invalid_external_root = ROOT.parent / "invalid-external-contract-tool-root"
    assert not invalid_external_root.exists()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DASHBOARD_ROOT", "PYTHONPATH"}
    }
    env["CONTRACT_TOOL_ROOT"] = str(invalid_external_root)
    result = subprocess.run(
        ["uv", "run", "python", "scripts/generate_contracts.py", "--check"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_contracts_are_present_and_current() -> None:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/generate_contracts.py", "--check"],
        cwd=ROOT,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    openapi = json.loads((ROOT / "generated" / "openapi" / "openapi.json").read_text(encoding="utf-8"))
    assert openapi["openapi"].startswith("3.1.")
    assert "/v1/market/latest" in openapi["paths"]
    assert "/v1/market-data/latest" in openapi["paths"]
    assert "/v1/market-data/snapshots/{snapshot_digest}" in openapi["paths"]
    market_data_parameters = openapi["paths"]["/v1/market-data/latest"]["get"]["parameters"]
    assert [parameter["schema"]["const"] for parameter in market_data_parameters] == [
        "crypto_spot:FIXTURE:BTC",
        "1m",
    ]
    snapshot_parameter = openapi["paths"]["/v1/market-data/snapshots/{snapshot_digest}"]["get"]["parameters"][0]
    assert snapshot_parameter["schema"]["pattern"] == "^[0-9a-f]{64}$"
    job_openapi = json.loads(
        (ROOT / "generated" / "job-api" / "openapi" / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    assert job_openapi["openapi"].startswith("3.1.")
    assert job_openapi["info"]["title"] == "Trading Agent Job Command API"
    assert set(job_openapi["paths"]) == {
        "/health/live",
        "/health/ready",
        "/v1/jobs",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/cancel",
    }
    assert job_openapi["components"]["securitySchemes"] == {
        "ServiceBearerAuth": {"type": "http", "scheme": "bearer"}
    }
    engine_schema_names = [
        name
        for name in job_openapi["components"]["schemas"]
        if name.startswith("EngineBacktestInput")
    ]
    assert engine_schema_names == ["EngineBacktestInput"]
    engine_input = job_openapi["components"]["schemas"]["EngineBacktestInput"]
    for field in ("start_time", "end_time"):
        assert engine_input["properties"][field] == {
            "format": "date-time",
            "pattern": (
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
                r"(?:\.\d{1,6})?Z$"
            ),
            "title": field.replace("_", " ").title(),
            "type": "string",
        }
    for path, methods in job_openapi["paths"].items():
        for operation in methods.values():
            if path.startswith("/v1/"):
                assert operation["security"] == [{"ServiceBearerAuth": []}]
            else:
                assert "security" not in operation
    assert (
        ROOT / "generated" / "job-api" / "json-schema" / "EnqueueJobRequest.json"
    ).is_file()
    assert (
        ROOT / "generated" / "job-api" / "json-schema" / "JobDetail.json"
    ).is_file()
    assert (
        ROOT
        / "generated"
        / "job-api"
        / "json-schema"
        / "EngineBacktestInput.json"
    ).is_file()
    assert (
        ROOT
        / "generated"
        / "job-api"
        / "json-schema"
        / "EngineBacktestPayload.json"
    ).is_file()
    assert (
        ROOT
        / "generated"
        / "job-api"
        / "json-schema"
        / "EngineBacktestSimulationInput.json"
    ).is_file()
    assert (
        ROOT
        / "generated"
        / "job-api"
        / "json-schema"
        / "EngineBacktestSimulationPayload.json"
    ).is_file()
    assert (ROOT / "generated" / "dashboard" / "api-types.ts").is_file()
    assert (ROOT / "generated" / "dashboard" / "api-schemas.ts").is_file()
    job_dashboard_types = (
        ROOT / "generated" / "job-api" / "dashboard" / "api-types.ts"
    )
    assert job_dashboard_types.is_file()
    assert "export interface components" in job_dashboard_types.read_text(
        encoding="utf-8"
    )
    assert (
        ROOT / "apps" / "dashboard" / "src" / "generated" / "job-api-types.ts"
    ).read_bytes() == job_dashboard_types.read_bytes()
