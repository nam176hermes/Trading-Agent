from __future__ import annotations

import tomllib
from pathlib import Path

from scripts.operator_contract_generation import (
    operator_openapi_contract,
    render_operator_contracts,
)


ROOT = Path(__file__).resolve().parents[2]


def test_operator_openapi_is_exact_and_binds_auth_capabilities() -> None:
    document = operator_openapi_contract()
    assert set(document["paths"]) == {
        "/health/live",
        "/health/ready",
        "/v1/state",
        "/v1/commands",
    }
    assert document["components"]["securitySchemes"] == {
        "OperatorBearerAuth": {"type": "http", "scheme": "bearer"}
    }
    state = document["paths"]["/v1/state"]["get"]
    commands = document["paths"]["/v1/commands"]["post"]
    assert state["security"] == [{"OperatorBearerAuth": []}]
    assert commands["security"] == [{"OperatorBearerAuth": []}]
    assert state["x-operator-interfaces"] == ["CLI"]
    assert commands["x-operator-interfaces"] == ["WEB", "CLI"]
    assert set(state["responses"]) == {"200", "401", "403", "413", "500", "503"}
    assert set(commands["responses"]) == {
        "200",
        "401",
        "403",
        "409",
        "413",
        "422",
        "500",
        "503",
    }


def test_wheel_packages_operator_api_and_independent_cli_entrypoint() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = project["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]
    assert force_include["apps/operator_api"] == "apps/operator_api"
    assert force_include["apps/operator_cli"] == "apps/operator_cli"
    assert project["project"]["scripts"] == {
        "trading-agent": "apps.operator_cli.cli:main",
        "trading-agent-nautilus": "packages.nautilus_engine_cli.cli:main",
    }


def test_operator_contract_artifacts_render_reproducibly(tmp_path: Path) -> None:
    namespace = tmp_path / "operator-api"
    typescript = render_operator_contracts(namespace, ROOT / "apps/dashboard")
    first = {
        path.relative_to(namespace): path.read_bytes()
        for path in namespace.rglob("*")
        if path.is_file()
    }
    render_operator_contracts(namespace, ROOT / "apps/dashboard")
    second = {
        path.relative_to(namespace): path.read_bytes()
        for path in namespace.rglob("*")
        if path.is_file()
    }
    assert first == second
    assert typescript == namespace / "dashboard/api-types.ts"
    assert "export interface paths" in typescript.read_text(encoding="utf-8")
