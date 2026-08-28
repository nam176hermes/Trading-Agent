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
    "dataclasses",
    "datetime",
    "decimal",
    "errno",
    "hashlib",
    "hmac",
    "json",
    "os",
    "re",
    "signal",
    "stat",
    "sys",
    "time",
    "typing",
    "uuid",
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


class BoundaryError(ValueError):
    """A P1 ownership or source boundary was crossed."""


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
    return names


def _uses_dynamic_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__import__":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "import_module":
            return True
    return False


def _runtime_import_is_allowed(module: str) -> bool:
    top_level = module.split(".", 1)[0]
    if top_level in _RUNTIME_ALLOWED_MODULES:
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
        or set(budget) != {"schema", "runtime_family", "frozen_files"}
        or budget.get("schema") != "trading-agent-p1-growth-budget/v1"
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
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if not (is_test or is_profile_policy or is_boundary_checker) and any(
            profile in source or profile in string_values for profile in _PROFILE_NAMES
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
    args = parser.parse_args()
    try:
        check_boundaries(args.root.resolve(), args.budget.resolve())
    except (BoundaryError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
