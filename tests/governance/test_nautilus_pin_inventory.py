"""Canonical recovery-contract guard; detailed behavior controls live nearby."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = Path(__file__).parent / "nautilus_pin_inventory" / "required_identities.py"
ORACLE_MODULE = "tests.governance.nautilus_pin_inventory.required_identities"
PRODUCTION_PYTHON_ROOTS = (
    "alembic", "apps", "engines", "legacy", "ops", "packages", "scripts", "services",
)
NON_PRODUCTION_PARTS = frozenset({
    ".git", ".hypothesis", ".pytest_cache", ".venv", "__pycache__",
    "generated", "tests", "third_party",
})


def _oracle():
    spec = importlib.util.spec_from_file_location("p1_u00_independent_oracle", ORACLE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(value: object) -> str:
    canonical = {key: sorted(values) for key, values in value.items()}  # type: ignore[union-attr]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def test_oracle_has_exact_rollback_and_candidate_context_roles() -> None:
    """Break caught: any reviewed identity role is deleted, altered, or merged into production authority."""
    oracle = _oracle()
    assert _digest(oracle.ROLLBACK_IDENTITIES) == "e729f10d2d1094abf7227d8bc15f77e5f772e941c93451d81d74126b82d60358"
    assert _digest(oracle.CANDIDATE_CONTEXT_IDENTITIES) == "b91cfe4e0ddae61ed2603df96cdd32e4230d439e1d08f3cfb3e82a8c065fef0e"
    assert set(oracle.ROLLBACK_IDENTITIES) == {
        "engine_version", "upstream_commit", "tag_object", "rust", "cython", "setuptools",
        "closure_schema", "rollback_sha256", "generation", "profile", "semantic_profile", "validator", "selected_source",
    }
    assert set(oracle.CANDIDATE_CONTEXT_IDENTITIES) == {
        "engine_version", "upstream_commit", "tag_object", "sdist_sha256", "wheel_sha256",
    }


def _import_targets(tree: ast.AST) -> set[str]:
    """Resolve absolute targets for every import spelling that can reach the oracle."""
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module)
            targets.update(f"{node.module}.{alias.name}" for alias in node.names)
    return targets


def _production_python_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    for root_name in PRODUCTION_PYTHON_ROOTS:
        root = ROOT / root_name
        if root.exists():
            paths.extend(path for path in root.rglob("*.py") if not NON_PRODUCTION_PARTS.intersection(path.relative_to(ROOT).parts))
    return tuple(sorted(paths))


@pytest.mark.parametrize("statement", (
    "import tests.governance.nautilus_pin_inventory.required_identities",
    "import tests.governance.nautilus_pin_inventory.required_identities as identities",
    "from tests.governance.nautilus_pin_inventory import required_identities",
    "from tests.governance.nautilus_pin_inventory import required_identities as identities",
    "from tests.governance.nautilus_pin_inventory.required_identities import ROLLBACK_IDENTITIES",
    "from tests.governance.nautilus_pin_inventory.required_identities import *",
))
def test_oracle_import_resolver_blocks_every_absolute_alias_form(statement: str) -> None:
    """Break caught: a production import spelling can consume the test-owned identity oracle."""
    assert ORACLE_MODULE in _import_targets(ast.parse(statement))


def test_production_python_cannot_import_the_test_oracle() -> None:
    """Break caught: production imports test-owned rollback or candidate identity definitions."""
    offenders: list[str] = []
    for path in _production_python_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(f"{path}:{target}" for target in _import_targets(tree) if target == ORACLE_MODULE)
    assert not offenders
