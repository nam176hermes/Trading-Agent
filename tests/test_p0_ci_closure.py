from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/implementation/p0-ci-closure-matrix.json"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.check_p0_ci_closure", *arguments],
        cwd=ROOT, text=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _matrix(tmp_path: Path, mutate) -> Path:
    document = json.loads(MATRIX.read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "matrix.json"
    path.write_bytes((json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return path


def test_pending_source_matrix_is_an_executable_closed_contract() -> None:
    """Break caught: checker accepts no real matrix or fabricates qualification."""
    result = _run("--matrix", str(MATRIX))
    assert result.returncode == 0, result.stderr
    assert "QUALIFICATION_PENDING" in result.stdout


def test_checker_rejects_unknown_fields_and_duplicate_requirement_ids(tmp_path: Path) -> None:
    """Break caught: schema/identity drift silently widens the closure."""
    unknown = _matrix(tmp_path, lambda value: value.__setitem__("unknown", True))
    unknown_result = _run("--matrix", str(unknown))
    assert unknown_result.returncode != 0
    assert unknown_result.stderr.strip() == "P0_CLOSURE_SCHEMA_INVALID"
    duplicate = _matrix(tmp_path, lambda value: value["requirements"].append(value["requirements"][0]))
    duplicate_result = _run("--matrix", str(duplicate))
    assert duplicate_result.returncode != 0
    assert duplicate_result.stderr.strip() == "P0_CLOSURE_REQUIREMENT_SET_DRIFT"


def test_checker_rejects_noncanonical_and_unsafe_bindings(tmp_path: Path) -> None:
    """Break caught: reordered JSON or symlinked source is accepted as authority."""
    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(" " + MATRIX.read_text(encoding="utf-8"), encoding="utf-8")
    assert _run("--matrix", str(noncanonical)).returncode != 0
    unsafe = _matrix(tmp_path, lambda value: value["requirements"][0]["implementation_paths"].__setitem__(0, "../README.md"))
    unsafe_result = _run("--matrix", str(unsafe))
    assert unsafe_result.returncode != 0
    assert unsafe_result.stderr.strip() == "P0_CLOSURE_IMPLEMENTATION_PATH_UNSAFE"


def test_checker_rejects_uncollected_node_unknown_target_and_host_only_mapping(tmp_path: Path) -> None:
    """Break caught: a binding that CI cannot execute is accepted."""
    node = _matrix(tmp_path, lambda value: value["requirements"][0]["test_node_ids"].__setitem__(0, "tests/test_p0_ci_closure.py::missing"))
    node_result = _run("--matrix", str(node))
    assert node_result.returncode != 0
    assert node_result.stderr.strip() == "P0_CLOSURE_TEST_NODE_UNCOLLECTED"
    target = _matrix(tmp_path, lambda value: value["requirements"][0].__setitem__("make_target", "unknown-target"))
    target_result = _run("--matrix", str(target))
    assert target_result.returncode != 0
    assert target_result.stderr.strip() == "P0_CLOSURE_MAKE_TARGET_UNKNOWN"
    host = _matrix(tmp_path, lambda value: value["requirements"][0].__setitem__("workflow", ".github/workflows/host-authority.yml"))
    host_result = _run("--matrix", str(host))
    assert host_result.returncode != 0
    assert host_result.stderr.strip() == "P0_CLOSURE_PORTABLE_WORKFLOW_INVALID"


def test_checker_rejects_live_baseline_and_unearned_completion(tmp_path: Path) -> None:
    """Break caught: source declaration becomes a final qualification verdict."""
    baseline = ROOT / "ops/consolidation/p0-canonical-baseline.json"
    original = baseline.read_bytes()
    document = json.loads(original)
    document["qualified_sha"] = "0" * 40
    baseline.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode())
    try:
        assert _run("--matrix", str(MATRIX)).returncode != 0
    finally:
        baseline.write_bytes(original)
    complete = _matrix(tmp_path, lambda value: value.__setitem__("state", "P0_SOURCE_COMPLETE"))
    assert _run("--matrix", str(complete)).returncode != 0


def test_checker_rejects_pending_source_invariant(tmp_path: Path) -> None:
    """Break caught: an implemented P0 invariant is downgraded to pending."""
    changed = _matrix(tmp_path, lambda value: value["requirements"][0].__setitem__("required_status", "PENDING"))
    result = _run("--matrix", str(changed))
    assert result.returncode != 0
    assert result.stderr.strip() == "P0_CLOSURE_REQUIRED_STATUS_INVALID"
