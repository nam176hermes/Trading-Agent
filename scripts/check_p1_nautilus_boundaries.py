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
_NETWORK_MODULES = {"aiohttp", "ccxt", "httpx", "requests", "socket", "websockets"}
_PROFILE_NAMES = {"p1-local-paper", "p1-real-backtest"}


class BoundaryError(ValueError):
    """A P1 ownership or source boundary was crossed."""


def _python_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
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
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def check_boundaries(root: Path, budget_path: Path) -> None:
    budget = json.loads(budget_path.read_bytes())
    if budget.get("schema") != "trading-agent-p1-growth-budget/v1":
        raise BoundaryError("P1 growth budget is invalid")
    for relative, record in budget["frozen_files"].items():
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
        is_legacy_reference = relative.startswith("engines/nautilus/launcher/")
        if "nautilus_trader" in imports and not (is_test or is_runtime_v1 or is_legacy_reference):
            raise BoundaryError(f"root Nautilus import is forbidden: {relative}")
        if is_runtime_v1 and imports & _NETWORK_MODULES:
            raise BoundaryError(f"runtime network import is forbidden: {relative}")
        is_boundary_checker = relative == "scripts/check_p1_nautilus_boundaries.py"
        if (
            "runtime_v1" in source
            and "runtime_v2" in source
            and not (is_test or is_boundary_checker)
        ):
            raise BoundaryError(f"mixed runtime families are forbidden: {relative}")
        if is_runtime_v1 and relative != "engines/nautilus/runtime_v1/profile.py":
            if any(profile in source for profile in _PROFILE_NAMES):
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
