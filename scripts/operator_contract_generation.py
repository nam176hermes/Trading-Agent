"""Pure Operator API contract rendering helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from apps.operator_api.app import create_app
from apps.operator_api.config import OperatorApiSettings
from apps.operator_api.contracts import (
    OperatorApiErrorEnvelope,
    OperatorCommandEnvelope,
    OperatorHealthEnvelope,
    OperatorStateEnvelope,
)
from packages.operator_control.contracts import (
    CommandExecutionResultV1,
    OperatorSourceStateV1,
    SubmitOperatorCommandV1,
)


OPERATOR_SCHEMA_MODELS = (
    SubmitOperatorCommandV1,
    OperatorSourceStateV1,
    CommandExecutionResultV1,
    OperatorApiErrorEnvelope,
    OperatorStateEnvelope,
    OperatorCommandEnvelope,
    OperatorHealthEnvelope,
)


def operator_openapi_contract() -> dict[str, Any]:
    settings = OperatorApiSettings(
        Path("/contract/web.token"),
        "contract.web",
        Path("/contract/cli.token"),
        "contract.cli",
    )
    document = create_app(settings, object(), object()).openapi()
    components = document.setdefault("components", {})
    components["securitySchemes"] = {
        "OperatorBearerAuth": {"type": "http", "scheme": "bearer"}
    }
    for path, method, interfaces in (
        ("/v1/state", "get", ["CLI"]),
        ("/v1/commands", "post", ["WEB", "CLI"]),
    ):
        operation = document["paths"][path][method]
        operation["security"] = [{"OperatorBearerAuth": []}]
        operation["x-operator-interfaces"] = interfaces
    return document


def render_operator_contracts(namespace: Path, tool_root: Path) -> Path:
    openapi_path = namespace / "openapi/openapi.json"
    _write_json(openapi_path, operator_openapi_contract())
    for model in OPERATOR_SCHEMA_MODELS:
        _write_json(
            namespace / "json-schema" / f"{model.__name__}.json",
            model.model_json_schema(),
        )
    dashboard_output = namespace / "dashboard/api-types.ts"
    dashboard_output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(tool_root / "node_modules/.bin/openapi-typescript"),
            str(openapi_path),
            "--output",
            str(dashboard_output),
            "--immutable",
            "--alphabetize",
        ],
        check=True,
    )
    dashboard_output.chmod(0o644)
    return dashboard_output


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    path.chmod(0o644)


__all__ = [
    "OPERATOR_SCHEMA_MODELS",
    "operator_openapi_contract",
    "render_operator_contracts",
]
