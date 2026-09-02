from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from scripts.check_hwc_boundaries import evaluate_hwc_boundaries
from tests.hwc.fixtures.boundary_repo import commit_all, make_boundary_repo


ROOT = Path(__file__).resolve().parents[2]


def _codes(root: Path, *, final: bool = False) -> set[str]:
    return {item.code for item in evaluate_hwc_boundaries(root, final=final).violations}


def test_current_tree_passes_only_with_declared_debt() -> None:
    report = evaluate_hwc_boundaries(ROOT)
    assert report.passed is True
    assert report.grandfathered_debt == 4
    assert _codes(ROOT, final=True) == {"HWC_E_GRANDFATHER_REMAINS_FINAL"}


def test_fixture_baseline_and_final_gate(tmp_path: Path) -> None:
    root = make_boundary_repo(tmp_path)
    assert evaluate_hwc_boundaries(root).passed is True
    assert _codes(root, final=True) == {"HWC_E_GRANDFATHER_REMAINS_FINAL"}


@pytest.mark.parametrize(
    ("relative", "source", "code"),
    [
        ("packages/domain/bad.py", "import apps.operator_api\n", "HWC_E_CORE_IMPORTS_INTERFACE"),
        ("apps/operator_cli/bad.py", "import services.operator_control\n", "HWC_E_CLI_BYPASSES_API"),
        ("apps/dashboard/src/bad.ts", "import cp from 'node:child_process';\n", "HWC_E_DASHBOARD_PROCESS_EXECUTION"),
        ("apps/dashboard/src/db.ts", "import pg from 'pg';\n", "HWC_E_DASHBOARD_DIRECT_DATABASE"),
        ("apps/dashboard/src/broker.ts", "import ccxt from 'ccxt';\n", "HWC_E_DASHBOARD_DIRECT_BROKER"),
        ("apps/operator_api/app.py", "@app.post('/v1/start')\ndef start(): pass\n", "HWC_E_OPERATOR_API_SCOPE_EXPANSION"),
    ],
)
def test_source_boundary_violation_has_stable_code(
    tmp_path: Path, relative: str, source: str, code: str
) -> None:
    root = make_boundary_repo(tmp_path)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    assert code in _codes(root)


def test_systemd_cannot_depend_on_dashboard(tmp_path: Path) -> None:
    root = make_boundary_repo(tmp_path)
    (root / "ops/systemd/core.service").write_text(
        "[Unit]\nRequires=dashboard.service\n", encoding="utf-8"
    )
    assert "HWC_E_SYSTEMD_DASHBOARD_DEPENDENCY" in _codes(root)


def test_new_dashboard_writer_is_rejected(tmp_path: Path) -> None:
    root = make_boundary_repo(tmp_path)
    target = root / "apps/dashboard/src/new-writer.ts"
    target.write_text("writePrivateLocalStateFile('.kill_switch', 'x');\n", encoding="utf-8")
    assert "HWC_E_UNDECLARED_DASHBOARD_WRITE" in _codes(root)


def test_grandfather_hash_drift_and_missing_target_are_distinct(tmp_path: Path) -> None:
    root = make_boundary_repo(tmp_path)
    target = root / "apps/dashboard/src/app/api/trading/mode/route.ts"
    target.write_text(target.read_text(encoding="utf-8") + "// drift\n", encoding="utf-8")
    commit_all(root)
    assert "HWC_E_GRANDFATHER_HASH_DRIFT" in _codes(root)
    target.unlink()
    commit_all(root, "remove target")
    assert "HWC_E_GRANDFATHER_TARGET_MISSING" in _codes(root)


def test_route_inventory_drift_is_rejected(tmp_path: Path) -> None:
    root = make_boundary_repo(tmp_path)
    target = root / "apps/dashboard/src/app/api/trading/new/route.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export function GET() {}\n", encoding="utf-8")
    assert "HWC_E_ROUTE_INVENTORY_DRIFT" in _codes(root)


def test_invalid_policy_is_typed(tmp_path: Path) -> None:
    root = make_boundary_repo(tmp_path)
    (root / "docs/implementation/hwc/hwc-boundary-policy-v1.json").write_text(
        json.dumps({"schema_version": "wrong"}), encoding="utf-8"
    )
    assert _codes(root) == {"HWC_E_POLICY_INVALID"}


def test_portable_make_routes_cannot_bypass_hwc_boundaries() -> None:
    """Break caught: portable CI can omit the executable HWC boundary gate."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(
        r"^check-hwc-boundaries: check-hwc-status\n\t\$\(PYTHON\) scripts/check_hwc_boundaries.py$",
        source,
        re.MULTILINE,
    )
    contracts = re.search(r"^check-contracts:[^\n]*\n((?:\t.*\n)+)", source, re.MULTILINE)
    assert contracts is not None
    assert contracts.group(1).splitlines()[-1] == (
        "\t$(PYTHON) scripts/check_hwc_boundaries.py"
    )
    for target in ("test-all-private", "test-all-portable-private"):
        match = re.search(rf"^{target}:(.*)$", source, re.MULTILINE)
        assert match is not None and "check-contracts" in match.group(1).split()
    common = re.search(r"^ci-common-private:\n((?:\t.*\n)+)", source, re.MULTILINE)
    assert common is not None and "check-contracts" in common.group(1)
