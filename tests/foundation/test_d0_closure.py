from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/implementation/d0-closure-matrix.json"
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github/workflows/foundation.yml"
PYPROJECT = ROOT / "pyproject.toml"

SOURCE_REQUIREMENTS = {
    "D0-PROPERTY-TESTS",
    "D0-CONTRACT-DRIFT",
    "D0.1-INVARIANTS",
    "D0.2-INVARIANTS",
    "D0-IMMUTABLE-SET-HASH",
    "D0-SNAPSHOT-SEMANTICS",
    "D0-OUTBOX-SEMANTICS",
    "D0-INBOX-SEMANTICS",
    "D0-END-TO-END-REPLAY",
    "D0-FULL-GATES",
}
RUNTIME_REQUIREMENT = "D0-POSTGRES-RUNTIME-PARITY"
REQUIRED_COMMANDS = {
    "make audit-release",
    "make check-contracts",
    "make test-all",
    "UV_CACHE_DIR=/home/thenam176/.cache/uv uv run pytest -q tests/runtime_release",
    "make build-dashboard",
    "make ci",
}


def _assert_no_blank(value: object, location: str = "matrix") -> None:
    if value is None or value == "" or value == [] or value == {}:
        raise AssertionError(f"blank closure evidence at {location}")
    if isinstance(value, dict):
        for key, nested in value.items():
            assert isinstance(key, str) and key, f"blank key at {location}"
            _assert_no_blank(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_blank(nested, f"{location}[{index}]")


def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _load_matrix() -> dict[str, Any]:
    document = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "phase",
        "source_readiness",
        "runtime_postgres_parity",
        "requirements",
        "final_proof_commands",
    }
    _assert_no_blank(document)
    return document


def test_d0_closure_matrix_has_no_blank_or_unresolved_source_requirement() -> None:
    document = _load_matrix()
    assert document["schema_version"] == 1
    assert document["phase"] == "D0"
    assert document["source_readiness"] == "PASS"
    assert document["runtime_postgres_parity"] in {"PASS", "PENDING_APPROVAL"}

    rows = document["requirements"]
    assert isinstance(rows, list)
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == SOURCE_REQUIREMENTS | {RUNTIME_REQUIREMENT}
    assert all(by_id[requirement]["status"] == "PASS" for requirement in SOURCE_REQUIREMENTS)
    runtime = by_id[RUNTIME_REQUIREMENT]
    assert runtime["status"] == document["runtime_postgres_parity"]
    assert runtime["status"] in {"PASS", "PENDING_APPROVAL"}
    if runtime["status"] == "PENDING_APPROVAL":
        assert runtime["approval_boundary"]
        assert runtime["command"] == "make test-event-ledger-runtime-postgres"


def test_d0_closure_matrix_references_existing_implementation_and_test_proofs() -> None:
    document = _load_matrix()
    for row in document["requirements"]:
        for relative in row["implementation"]:
            assert (ROOT / relative).exists(), f"missing implementation proof: {relative}"
        for proof in row["proofs"]:
            path = ROOT / proof["path"]
            assert path.is_file(), f"missing proof path: {proof['path']}"
            if "test" in proof:
                assert proof["test"] in _test_functions(path), (
                    f"missing executable proof: {proof['path']}::{proof['test']}"
                )


def test_property_contract_and_closure_gates_are_collected_by_ci() -> None:
    document = _load_matrix()
    makefile = MAKEFILE.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    property_source = (ROOT / "tests/domain/test_decimal_properties.py").read_text(
        encoding="utf-8"
    )
    replay_source = (ROOT / "tests/event_ledger/test_replay.py").read_text(
        encoding="utf-8"
    )

    assert '"hypothesis==6.151.9"' in pyproject
    assert "@given" in property_source
    assert "@given" in replay_source
    assert 'testpaths = ["tests"]' in pyproject
    assert 'pytest -q -m "not runtime_postgres and not host_coupled" tests' in makefile
    assert "test-all: audit check-d0-closure check-contracts" in makefile
    assert "check-d0-closure:" in makefile
    assert "uv run pytest -q tests/foundation/test_d0_closure.py" in makefile
    assert "run: make ci" in workflow
    assert "ci: test-all build-dashboard audit-python-source audit-dependencies" in makefile
    assert set(document["final_proof_commands"]) == REQUIRED_COMMANDS
