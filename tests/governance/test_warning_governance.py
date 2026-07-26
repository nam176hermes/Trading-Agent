from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "apps" / "dashboard"
WARNING_PATTERN = re.compile(
    r"(?:DeprecationWarning|MODULE_TYPELESS_PACKAGE_JSON|StarletteDeprecationWarning|ExperimentalWarning)"
)


def _run(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _assert_clean(result: subprocess.CompletedProcess[str]) -> None:
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert WARNING_PATTERN.search(output) is None, output


def test_starlette_test_client_import_is_warning_free() -> None:
    result = _run(
        [
            sys.executable,
            "-W",
            "error",
            "-c",
            "from fastapi.testclient import TestClient; print(TestClient.__name__)",
        ]
    )
    _assert_clean(result)


def test_contract_generation_is_warning_free() -> None:
    result = _run([sys.executable, "scripts/generate_contracts.py", "--check"])
    _assert_clean(result)


def test_dashboard_typescript_test_import_is_warning_free() -> None:
    result = _run(
        ["node", "--test", "tests/trading-paths.test.mjs"],
        cwd=DASHBOARD,
    )
    _assert_clean(result)


def test_dashboard_declares_es_module_metadata() -> None:
    package = json.loads((DASHBOARD / "package.json").read_text())
    assert package.get("type") == "module"
