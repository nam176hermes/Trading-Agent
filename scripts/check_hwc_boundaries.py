from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


POLICY_PATH = Path("docs/implementation/hwc/hwc-boundary-policy-v1.json")
WRITER_CALL = re.compile(
    r"(?:writePrivateLocalStateFile|updatePrivateLocalStateFile|"
    r"removePrivateLocalStateFile|activateKillSwitch|clearKillSwitch)\s*\("
)
DATABASE_IMPORT = re.compile(r"(?:from\s+['\"](?:pg|postgres|prisma|drizzle|mysql)|require\(['\"](?:pg|postgres|mysql))")
BROKER_IMPORT = re.compile(r"(?:from\s+['\"](?:ccxt|alpaca|ibkr)|require\(['\"](?:ccxt|alpaca|ibkr))")
ALLOWED_OPERATOR_ENDPOINTS = {
    "/health/live",
    "/health/ready",
    "/v1/state",
    "/v1/commands",
}


@dataclass(frozen=True, slots=True)
class HwcBoundaryViolation:
    code: str
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class HwcBoundaryReport:
    violations: tuple[HwcBoundaryViolation, ...]
    grandfathered_debt: int

    @property
    def passed(self) -> bool:
        return not self.violations


def _violation(code: str, path: Path | str, detail: str) -> HwcBoundaryViolation:
    return HwcBoundaryViolation(code, str(path), detail)


def _strict_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("document must be an object")
    return value


def _imports(path: Path) -> Iterable[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _dashboard_routes(root: Path) -> set[str]:
    base = root / "apps/dashboard/src/app/api/trading"
    return {
        f"/{path.parent.relative_to(base).as_posix()}".replace("/.", "")
        for path in base.glob("**/route.ts")
    }


def _operator_endpoints(path: Path) -> set[str]:
    endpoints: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            function = decorator.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr in {"get", "post", "put", "patch", "delete"}
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                endpoints.add(decorator.args[0].value)
    return endpoints


def evaluate_hwc_boundaries(root: Path, *, final: bool = False) -> HwcBoundaryReport:
    root = root.resolve()
    try:
        policy = _strict_json(root / POLICY_PATH)
        if policy.get("schema_version") != "hwc-boundary-policy-v1":
            raise ValueError("unsupported boundary policy")
        inventory_relative = policy["route_inventory"]
        if not isinstance(inventory_relative, str):
            raise ValueError("route inventory path is invalid")
        inventory = _strict_json(root / inventory_relative)
        if inventory.get("schema_version") != "hwc-authority-inventory-v1":
            raise ValueError("unsupported authority inventory")
        debts = policy["grandfathered_state_writes"]
        routes = inventory["dashboard_routes"]
        if not isinstance(debts, list) or not isinstance(routes, list):
            raise ValueError("policy lists are invalid")
        debt_paths = {
            item["path"]
            for item in debts
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if len(debt_paths) != len(debts):
            raise ValueError("grandfather entries are invalid or duplicated")
        inventoried_routes = {
            item["route"]
            for item in routes
            if isinstance(item, dict) and isinstance(item.get("route"), str)
        }
        if len(inventoried_routes) != len(routes):
            raise ValueError("route entries are invalid or duplicated")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return HwcBoundaryReport(
            (_violation("HWC_E_POLICY_INVALID", POLICY_PATH, str(exc)),), 0
        )

    violations: list[HwcBoundaryViolation] = []
    if inventoried_routes != _dashboard_routes(root):
        violations.append(
            _violation(
                "HWC_E_ROUTE_INVENTORY_DRIFT",
                inventory_relative,
                "dashboard route inventory differs from source",
            )
        )

    for item in debts:
        assert isinstance(item, dict)
        relative = item["path"]
        assert isinstance(relative, str)
        target = root / relative
        if not target.is_file() or target.is_symlink():
            violations.append(
                _violation("HWC_E_GRANDFATHER_TARGET_MISSING", relative, "tracked regular file is absent")
            )
            continue
        try:
            blob = _git(root, "rev-parse", f"HEAD:{relative}")
        except subprocess.CalledProcessError:
            blob = ""
        source_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if blob != item.get("git_blob_sha") or source_sha != item.get("source_sha256"):
            violations.append(
                _violation("HWC_E_GRANDFATHER_HASH_DRIFT", relative, "grandfathered bytes changed")
            )

    if final and debts:
        violations.append(
            _violation(
                "HWC_E_GRANDFATHER_REMAINS_FINAL",
                POLICY_PATH,
                f"{len(debts)} grandfathered state writes remain",
            )
        )

    allowed_local = policy.get("allowed_dashboard_local_write")
    scan_root = root / "apps/dashboard/src"
    helper = "apps/dashboard/src/lib/trading/local-state.ts"
    for path in scan_root.glob("**/*"):
        if path.suffix not in {".ts", ".tsx"} or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        if (
            WRITER_CALL.search(source)
            and relative not in debt_paths
            and relative not in {allowed_local, helper}
        ):
            violations.append(
                _violation("HWC_E_UNDECLARED_DASHBOARD_WRITE", relative, "undeclared local state writer")
            )
        if any(pattern in source for pattern in policy.get("dashboard_forbidden_patterns", [])):
            violations.append(
                _violation("HWC_E_DASHBOARD_PROCESS_EXECUTION", relative, "process execution import")
            )
        if DATABASE_IMPORT.search(source):
            violations.append(
                _violation("HWC_E_DASHBOARD_DIRECT_DATABASE", relative, "direct database import")
            )
        if BROKER_IMPORT.search(source):
            violations.append(
                _violation("HWC_E_DASHBOARD_DIRECT_BROKER", relative, "direct broker import")
            )

    for relative in policy.get("python_interface_import_forbidden", []):
        for path in (root / relative).glob("**/*.py"):
            try:
                bad = next((name for name in _imports(path) if name == "apps" or name.startswith("apps.")), None)
            except (OSError, SyntaxError):
                bad = "unparseable"
            if bad:
                violations.append(
                    _violation("HWC_E_CORE_IMPORTS_INTERFACE", path.relative_to(root), f"forbidden import {bad}")
                )

    cli_prefixes = tuple(policy.get("operator_cli_forbidden_import_prefixes", []))
    for path in (root / "apps/operator_cli").glob("**/*.py"):
        try:
            bad = next(
                (name for name in _imports(path) if any(name == prefix or name.startswith(f"{prefix}.") for prefix in cli_prefixes)),
                None,
            )
        except (OSError, SyntaxError):
            bad = "unparseable"
        if bad:
            violations.append(
                _violation("HWC_E_CLI_BYPASSES_API", path.relative_to(root), f"forbidden import {bad}")
            )

    for path in (root / "ops/systemd").glob("*.service"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"^(?:Requires|Wants|PartOf)=.*dashboard", source, re.MULTILINE) or re.search(
            r"^ExecStart=.*dashboard", source, re.MULTILINE
        ):
            violations.append(
                _violation("HWC_E_SYSTEMD_DASHBOARD_DEPENDENCY", path.relative_to(root), "backend depends on dashboard")
            )

    for path in (root / "apps/operator_api").glob("**/*.py"):
        try:
            expanded = _operator_endpoints(path) - ALLOWED_OPERATOR_ENDPOINTS
        except (OSError, SyntaxError):
            expanded = {"unparseable"}
        if expanded:
            violations.append(
                _violation("HWC_E_OPERATOR_API_SCOPE_EXPANSION", path.relative_to(root), f"unexpected endpoints {sorted(expanded)}")
            )

    unique = {(item.code, item.path, item.detail): item for item in violations}
    ordered = tuple(unique[key] for key in sorted(unique))
    return HwcBoundaryReport(ordered, len(debts))


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check HWC source boundaries")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--final", action="store_true")
    options = parser.parse_args(arguments)
    report = evaluate_hwc_boundaries(options.root, final=options.final)
    print(
        json.dumps(
            {
                "grandfathered_debt": report.grandfathered_debt,
                "status": "PASS" if report.passed else "FAIL",
                "violations": [
                    {"code": item.code, "detail": item.detail, "path": item.path}
                    for item in report.violations
                ],
            },
            sort_keys=True,
        )
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
