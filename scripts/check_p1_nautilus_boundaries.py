#!/usr/bin/env python3
"""Enforce P1 Nautilus ownership, runtime-family and growth boundaries."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BUDGET = ROOT / "docs/implementation/p1-real-nautilus/growth-budget.json"
_RUNTIME_ALLOWED_MODULES = {
    "__future__",
    "argparse",
    "collections",
    "contextlib",
    "dataclasses",
    "datetime",
    "decimal",
    "errno",
    "hashlib",
    "hmac",
    "json",
    "os",
    "pathlib",
    "re",
    "signal",
    "stat",
    "sys",
    "sysconfig",
    "tempfile",
    "time",
    "typing",
    "uuid",
    "zipfile",
}
_RUNTIME_ALLOWED_NAUTILUS_PREFIXES = (
    "nautilus_trader.backtest",
    "nautilus_trader.common",
    "nautilus_trader.config",
    "nautilus_trader.model",
    "nautilus_trader.trading",
)
_PROFILE_NAMES = {"p1-local-paper", "p1-real-backtest"}
_LEGACY_NAUTILUS_IMPORTS = {
    "engines/nautilus/launcher/nautilus_backtest.py",
    "engines/nautilus/launcher/nautilus_paper_compat.py",
    "engines/nautilus/launcher/target_portfolio_strategy.py",
}
_FROZEN_FILES = {
    "engines/nautilus/launcher/nautilus_backtest.py",
    "engines/nautilus/launcher/target_portfolio_strategy.py",
}
_SOURCE_GROWTH_KEYS = {
    "default_max_logical_lines",
    "exact_files",
    "overrides",
    "roots",
}
_BASELINE = "docs/implementation/p1-real-nautilus/upgrade/p1-engine-baseline-receipt.json"
_GENERATION = "docs/implementation/p1-real-nautilus/upgrade/candidate-generations/NT1231-U04-G1.json"
_INVENTORY = "docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json"
_P1_POLICY = "engines/nautilus/p1-runtime-closure-policy.json"
_LEGACY_POLICIES = {
    "execution_simulation_policy_sha256": "engines/nautilus/runtime-closure-policy.json",
    "paper_compatibility_policy_sha256": "engines/nautilus/paper-compatibility-runtime-closure-policy.json",
    "job_worker_loader_sha256": "services/job_worker/nautilus_closure.py",
}


class BoundaryError(ValueError):
    """A P1 ownership or source boundary was crossed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _closed_json(path: Path) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise BoundaryError(f"duplicate lineage key: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=pairs)
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundaryError(f"P1 lineage input is invalid: {path.name}") from exc
    if type(value) is not dict or "latest" in json.dumps(value, sort_keys=True).lower():
        raise BoundaryError(f"P1 lineage input is moving or invalid: {path.name}")
    return value


def _git_identity(root: Path) -> tuple[str, str]:
    try:
        values = tuple(
            subprocess.check_output(
                ("/usr/bin/git", "rev-parse", revision), cwd=root, text=True
            ).strip()
            for revision in ("HEAD", "HEAD^{tree}")
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BoundaryError("P1 lineage source identity is unavailable") from exc
    if any(
        len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
        for value in values
    ):
        raise BoundaryError("P1 lineage source identity is invalid")
    return values[0], values[1]


def p1_lineage_report(
    root: Path, *, source_identity: tuple[str, str] | None = None
) -> dict[str, object]:
    baseline = _closed_json(root / _BASELINE)
    generation = _closed_json(root / _GENERATION)
    policy = _closed_json(root / _P1_POLICY)
    inventory = _closed_json(root / _INVENTORY)
    legacy = baseline.get("legacy_phase4_authority")
    engine = generation.get("engine_identity")
    artifact = generation.get("artifact")
    closure = generation.get("closure")
    wheel = policy.get("engine_wheel")
    safe_limits = {
        "live_authorized": False,
        "network_trading_authorized": False,
        "production_authorized": False,
    }
    if (
        baseline.get("schema") != "trading-agent-p1-engine-baseline-receipt/v1"
        or baseline.get("status") != "P1_BASELINE_APPROVED"
        or baseline.get("operator_decision") != "PROMOTE_1_231_FOR_P1"
        or baseline.get("engine_version") != "1.231.0"
        or baseline.get("legacy_phase4_profiles_unchanged") is not True
        or baseline.get("p1_product_closure_schema") != 8
        or generation.get("schema") != "trading-agent-nautilus-candidate-generation/v1"
        or generation.get("generation_id") != baseline.get("candidate_generation_id")
        or _sha256(root / _GENERATION) != baseline.get("candidate_generation_sha256")
        or not isinstance(engine, dict)
        or engine.get("version") != "1.231.0"
        or not isinstance(artifact, dict)
        or not isinstance(closure, dict)
        or not isinstance(wheel, dict)
        or closure.get("manifest_sha256") != baseline.get("candidate_closure_sha256")
        or policy.get("schema") != "trading-agent-p1-runtime-closure-policy/v1"
        or policy.get("engine_version") != engine.get("version")
        or policy.get("engine_upstream_commit") != engine.get("upstream_commit")
        or policy.get("artifact_manifest_sha256")
        != artifact.get("artifact_manifest_sha256")
        or wheel.get("sha256") != artifact.get("wheel_sha256")
        or wheel.get("size") != artifact.get("wheel_size")
        or policy.get("candidate_generation_sha256")
        != baseline.get("candidate_generation_sha256")
        or policy.get("candidate_closure_sha256")
        != baseline.get("candidate_closure_sha256")
        or policy.get("p1_baseline_receipt_sha256") != _sha256(root / _BASELINE)
        or policy.get("profile_manifest_schema_version") != 8
        or policy.get("authority_limits") != safe_limits
        or generation.get("authority_limits")
        != {
            "candidate_active": False,
            "candidate_promoted": False,
            **safe_limits,
        }
        or inventory.get("schema") != "nautilus-pin-inventory/v4"
        or not isinstance(legacy, dict)
        or legacy.get("active_policy_changes") != 0
        or legacy.get("engine_version") != "1.227.0"
        or legacy.get("schema_version") != 6
        or any(
            _sha256(root / relative) != legacy.get(field)
            for field, relative in _LEGACY_POLICIES.items()
        )
    ):
        raise BoundaryError("P1 candidate lineage or rollback authority drifted")
    for relative in _LEGACY_POLICIES.values():
        if relative.endswith(".json"):
            legacy_policy = _closed_json(root / relative)
            if (
                legacy_policy.get("engine_version") != "1.227.0"
                or legacy_policy.get("profile_manifest_schema_version") != 6
            ):
                raise BoundaryError("P1 legacy runtime became active or drifted")
    commit, tree = source_identity or _git_identity(root)
    return {
        "authority_limits": safe_limits,
        "candidate_generation_id": baseline["candidate_generation_id"],
        "candidate_generation_sha256": baseline["candidate_generation_sha256"],
        "legacy_engine_version": "1.227.0",
        "legacy_profiles_unchanged": True,
        "p1_engine_version": "1.231.0",
        "p1_product_closure_sha256": _sha256(root / _P1_POLICY),
        "pin_inventory_sha256": _sha256(root / _INVENTORY),
        "qualification_source_commit": commit,
        "qualification_source_tree": tree,
        "schema": "trading-agent-p1-candidate-lineage/v1",
        "verdict": "PASS",
    }


def _python_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return [root / name for name in result.stdout.splitlines()]
    return sorted(root.rglob("*.py"))


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            if node.module == "nautilus_trader":
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _uses_dynamic_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {
            "__builtins__",
            "__import__",
            "compile",
            "eval",
            "exec",
            "getattr",
            "globals",
            "locals",
            "vars",
        }:
            return True
        if isinstance(node, ast.Attribute) and node.attr == "import_module":
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("__")
            and node.attr.endswith("__")
            and node.attr != "__name__"
        ):
            return True
        if isinstance(node, ast.Constant) and node.value == "__import__":
            return True
    return False


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = _constant_string(node.left)
        if isinstance(node.op, ast.Add):
            right = _constant_string(node.right)
            if left is not None and right is not None:
                return left + right
        elif left is not None:
            right_nodes = node.right.elts if isinstance(node.right, ast.Tuple) else (node.right,)
            values = tuple(_constant_string(item) for item in right_nodes)
            if all(value is not None for value in values):
                try:
                    operand: object = values if isinstance(node.right, ast.Tuple) else values[0]
                    return left % operand
                except (TypeError, ValueError):
                    return None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for item in node.values:
            value = _constant_string(item.value) if isinstance(item, ast.FormattedValue) else _constant_string(item)
            if value is None:
                return None
            parts.append(value)
        return "".join(parts)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        separator = _constant_string(node.func.value)
        values = tuple(_constant_string(item) for item in node.args[0].elts)
        if separator is not None and all(value is not None for value in values):
            return separator.join(value for value in values if value is not None)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and not node.keywords
    ):
        template = _constant_string(node.func.value)
        values = tuple(_constant_string(item) for item in node.args)
        if template is not None and all(value is not None for value in values):
            try:
                return template.format(*values)
            except (IndexError, KeyError, ValueError):
                return None
    return None


def _literal_stream(tree: ast.AST) -> str:
    literals = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return "".join(node.value for node in literals)


def _uses_parent_relative_import(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and node.level > 1 for node in ast.walk(tree)
    )


def _uses_forbidden_metadata_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "importlib.metadata" for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "importlib.metadata":
            if (
                node.level != 0
                or len(node.names) != 1
                or node.names[0].name != "version"
                or node.names[0].asname != "package_version"
            ):
                return True
    return False


def _runtime_import_is_allowed(module: str) -> bool:
    top_level = module.split(".", 1)[0]
    if top_level in _RUNTIME_ALLOWED_MODULES or module == "importlib.metadata":
        return True
    return module == "nautilus_trader" or any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in _RUNTIME_ALLOWED_NAUTILUS_PREFIXES
    )


def check_boundaries(root: Path, budget_path: Path) -> None:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise BoundaryError(f"duplicate growth-budget key: {key}")
            value[key] = item
        return value

    budget = json.loads(budget_path.read_bytes(), object_pairs_hook=pairs)
    if (
        type(budget) is not dict
        or set(budget)
        != {"frozen_files", "runtime_family", "schema", "source_growth"}
        or budget.get("schema") != "trading-agent-p1-growth-budget/v2"
        or budget.get("runtime_family") != "cython-v1"
    ):
        raise BoundaryError("P1 growth budget is invalid")
    frozen_files = budget.get("frozen_files")
    if type(frozen_files) is not dict or set(frozen_files) != _FROZEN_FILES:
        raise BoundaryError("P1 frozen file inventory is invalid")
    for relative, record in frozen_files.items():
        if (
            type(relative) is not str
            or type(record) is not dict
            or set(record) != {"maximum_bytes", "sha256"}
            or type(record.get("maximum_bytes")) is not int
            or record["maximum_bytes"] <= 0
            or type(record.get("sha256")) is not str
            or len(record["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in record["sha256"])
        ):
            raise BoundaryError(f"P1 frozen file record is invalid: {relative}")
        raw = (root / relative).read_bytes()
        if len(raw) > record["maximum_bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise BoundaryError(f"frozen launcher changed: {relative}")
    source_growth = budget.get("source_growth")
    if type(source_growth) is not dict or set(source_growth) != _SOURCE_GROWTH_KEYS:
        raise BoundaryError("P1 source growth budget is invalid")
    default_max = source_growth.get("default_max_logical_lines")
    roots = source_growth.get("roots")
    exact_files = source_growth.get("exact_files")
    overrides = source_growth.get("overrides")
    if (
        type(default_max) is not int
        or default_max < 300
        or default_max > 500
        or type(roots) is not list
        or not roots
        or len(roots) != len(set(roots))
        or any(type(root) is not str or not root or Path(root).is_absolute() or ".." in Path(root).parts for root in roots)
        or type(exact_files) is not list
        or len(exact_files) != len(set(exact_files))
        or any(type(path) is not str or not path.endswith(".py") for path in exact_files)
        or type(overrides) is not dict
        or any(
            type(path) is not str
            or not path.endswith(".py")
            or type(limit) is not int
            or limit <= default_max
            for path, limit in overrides.items()
        )
    ):
        raise BoundaryError("P1 source growth budget is invalid")
    governed = {
        path.relative_to(root).as_posix(): path
        for relative in roots
        for path in (root / relative).glob("*.py")
    }
    for relative in exact_files:
        governed[relative] = root / relative
    if not set(overrides).issubset(governed):
        raise BoundaryError("P1 source growth override is stale")
    for relative, path in governed.items():
        try:
            logical_lines = sum(
                bool(line := raw_line.strip()) and not line.startswith("#")
                for raw_line in path.read_text(encoding="utf-8").splitlines()
            )
        except (OSError, UnicodeError) as exc:
            raise BoundaryError(f"cannot inspect {relative}") from exc
        if logical_lines > overrides.get(relative, default_max):
            raise BoundaryError(f"P1 source growth budget exceeded: {relative}")
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        if "/.venv/" in f"/{relative}" or "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise BoundaryError(f"cannot inspect {relative}") from exc
        imports = _imports(tree)
        is_test = relative.startswith("tests/") or "/tests/" in relative
        is_runtime_v1 = relative.startswith("engines/nautilus/runtime_v1/")
        is_legacy_reference = relative in _LEGACY_NAUTILUS_IMPORTS
        imports_nautilus = any(
            module == "nautilus_trader" or module.startswith("nautilus_trader.")
            for module in imports
        )
        if imports_nautilus and not (is_test or is_runtime_v1 or is_legacy_reference):
            raise BoundaryError(f"root Nautilus import is forbidden: {relative}")
        if is_runtime_v1 and (
            any(not _runtime_import_is_allowed(module) for module in imports)
            or _uses_dynamic_import(tree)
            or _uses_parent_relative_import(tree)
            or _uses_forbidden_metadata_import(tree)
        ):
            raise BoundaryError(f"runtime network/client import is forbidden: {relative}")
        is_boundary_checker = relative == "scripts/check_p1_nautilus_boundaries.py"
        if (
            "runtime_v1" in source
            and "runtime_v2" in source
            and not (is_test or is_boundary_checker)
        ):
            raise BoundaryError(f"mixed runtime families are forbidden: {relative}")
        is_profile_policy = relative == "engines/nautilus/runtime_v1/profile.py"
        string_values = {
            value
            for node in ast.walk(tree)
            if (value := _constant_string(node)) is not None
        }
        if not (is_test or is_profile_policy or is_boundary_checker) and any(
            profile in source
            or profile in string_values
            or profile in _literal_stream(tree)
            for profile in _PROFILE_NAMES
        ):
            raise BoundaryError(f"profile name duplicated outside policy: {relative}")
        if not (is_test or is_profile_policy or is_boundary_checker) and (
            ("p1-" in string_values and "real-backtest" in string_values)
            or ("p1-" in string_values and "local-paper" in string_values)
        ):
            raise BoundaryError(f"profile name duplicated outside policy: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--budget", type=Path, default=BUDGET)
    parser.add_argument("--lineage-report", action="store_true")
    args = parser.parse_args()
    try:
        check_boundaries(args.root.resolve(), args.budget.resolve())
        if args.lineage_report:
            print(json.dumps(p1_lineage_report(args.root.resolve()), sort_keys=True))
    except (BoundaryError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
