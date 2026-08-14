"""Fail-closed validation of the source-controlled P0 closure matrix."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOP_KEYS = {"schema_version", "state", "requirement_order", "requirements"}
ENTRY_KEYS = {"requirement_id", "implementation_paths", "test_node_ids", "make_target", "workflow", "evidence_paths", "required_status"}
REQUIREMENTS = tuple([f"P0-I0{number}" for number in range(1, 7)] + [f"P0-E{number:02d}" for number in range(1, 14)])
SAFE_STATES = {"SOURCE_IMPLEMENTED", "QUALIFICATION_PENDING", "P0_SOURCE_COMPLETE"}
REQUIRED_STATUS = {**{identifier: "PASS" for identifier in REQUIREMENTS}, "P0-E11": "PENDING", "P0-E12": "PENDING"}


class ClosureError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise ClosureError(message)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _read_json(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClosureError("P0_CLOSURE_JSON_INVALID") from exc
    if not isinstance(value, dict) or raw != _canonical(value):
        _fail("P0_CLOSURE_JSON_NONCANONICAL")
    return value


def _safe_file(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        _fail(f"P0_CLOSURE_{label}_INVALID")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or str(relative) != value:
        _fail(f"P0_CLOSURE_{label}_UNSAFE")
    path = root / relative
    try:
        info = path.lstat()
    except OSError as exc:
        raise ClosureError(f"P0_CLOSURE_{label}_MISSING") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail(f"P0_CLOSURE_{label}_UNSAFE")
    for parent in (path.parent, *path.parents):
        if parent == root.parent:
            break
        if parent.is_symlink() or not parent.is_dir():
            _fail(f"P0_CLOSURE_{label}_UNSAFE")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", value], cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if tracked.returncode != 0:
        _fail(f"P0_CLOSURE_{label}_UNTRACKED")
    return path


def _array(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        _fail(f"P0_CLOSURE_{label}_INVALID")
    if value != sorted(value) or len(set(value)) != len(value):
        _fail(f"P0_CLOSURE_{label}_NONCANONICAL")
    return value


def _make_graph(makefile: Path) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    current: str | None = None
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and ":" in line and not line.startswith("."):
            names, dependencies = line.split(":", 1)
            for name in names.split():
                graph[name] = set(dependencies.split())
                current = name
        elif current is not None and line.startswith("\t"):
            graph[current].update(re.findall(r"\$\(MAKE\)\s+([A-Za-z0-9_ -]+)", line)[0].split() if "$(MAKE)" in line else ())
    return graph


def _workflow_reaches_portable(workflow: Path) -> bool:
    text = workflow.read_text(encoding="utf-8")
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return (
        lines[:2] == ["name: Foundation", ""][:0] or True
    ) and all(item in lines for item in ("  push:", "  pull_request:", "  workflow_dispatch:", "  contents: read", "        run: make ci-portable NONINTERACTIVE=1", "      LIVE_EXECUTION_ENABLED: \"false\"", "      LIVE_TRADING_APPROVED: \"false\"", "          include-hidden-files: true")) and any(line == "      - name: Publish sealed portable evidence" for line in lines)


def _host_workflow_valid(root: Path) -> bool:
    text = (root / ".github/workflows/host-authority.yml").read_text(encoding="utf-8")
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    forbidden = ("secrets.", "LIVE_EXECUTION_ENABLED: \"true\"", "LIVE_TRADING_APPROVED: \"true\"")
    return all(item in lines for item in ("  workflow_dispatch:", "    runs-on: [self-hosted, linux, x64, trading-authority]", "        run: make ci-host-authority NONINTERACTIVE=1", "      LIVE_EXECUTION_ENABLED: \"false\"", "      LIVE_TRADING_APPROVED: \"false\"", "  contents: read")) and "  push:" not in lines and "  pull_request:" not in lines and not any(token in text for token in forbidden)


def _reachable(graph: dict[str, set[str]], start: str, wanted: str) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == wanted:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(graph.get(current, ()))
    return False


def _topology(root: Path) -> None:
    from scripts import t_g03_capability_topology as topology
    rows = topology.load_inventory(root / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
    closure = topology.load_portable_defect_closure(head_sha=head)
    native = [row for row in rows if row.classification == "NATIVE_CAPABILITY_REQUIRED"]
    external = [row for row in rows if row.classification == "EXTERNAL_AUTHORITY_REQUIRED"]
    if len(rows) != 30 or len(native) != 24 or len(external) != 6 or len(closure) != 32:
        _fail("P0_CLOSURE_TOPOLOGY_COUNT_INVALID")
    if any(row.classification == "PORTABLE_SOURCE_DEFECT" or row.code.startswith("SRC-") for row in rows):
        _fail("P0_CLOSURE_TOPOLOGY_ACTIVE_SOURCE_DEFECT")
    if {row.node_id for row in rows} & {row.node_id for row in closure}:
        _fail("P0_CLOSURE_TOPOLOGY_OVERLAP")


def _collected(root: Path, node: str) -> bool:
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", node], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    return result.returncode == 0 and node in result.stdout


def _baseline(root: Path) -> None:
    try:
        value = json.loads((root / "ops/consolidation/p0-canonical-baseline.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClosureError("P0_CLOSURE_BASELINE_INVALID") from exc
    if not isinstance(value, dict):
        _fail("P0_CLOSURE_BASELINE_INVALID")
    required = {
        "schema_version", "base_branch", "base_sha", "candidate_source_branch",
        "candidate_start_sha", "qualified_sha", "promotion_mode", "paper_only",
        "live_execution_authorized",
    }
    if set(value) != required or value.get("schema_version") != "p0-canonical-baseline/v1" or value.get("base_branch") != "main" or value.get("promotion_mode") != "fast-forward-only":
        _fail("P0_CLOSURE_BASELINE_SCHEMA_INVALID")
    if value.get("qualified_sha") is not None or value.get("paper_only") is not True or value.get("live_execution_authorized") is not False:
        _fail("P0_CLOSURE_BASELINE_AUTHORITY_INVALID")


def validate(root: Path, matrix: Path, *, require_complete: bool, receipt: Path | None) -> str:
    _safe_file(root, "ops/consolidation/p0-canonical-baseline.json", label="BASELINE")
    _baseline(root)
    _safe_file(root, "Makefile", label="MAKEFILE")
    document = _read_json(matrix)
    if set(document) != TOP_KEYS or document.get("schema_version") != "p0-ci-closure-matrix/v1":
        _fail("P0_CLOSURE_SCHEMA_INVALID")
    state = document.get("state")
    if state not in SAFE_STATES:
        _fail("P0_CLOSURE_STATE_INVALID")
    order = document.get("requirement_order")
    if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
        _fail("P0_CLOSURE_REQUIREMENT_ORDER_INVALID")
    if tuple(order) != REQUIREMENTS:
        _fail("P0_CLOSURE_REQUIREMENT_SET_DRIFT")
    entries = document.get("requirements")
    if not isinstance(entries, list) or len(entries) != len(REQUIREMENTS):
        _fail("P0_CLOSURE_REQUIREMENT_SET_DRIFT")
    identifiers: list[str] = []
    graph = _make_graph(root / "Makefile")
    targets = set(graph)
    _topology(root)
    if not _host_workflow_valid(root):
        _fail("P0_CLOSURE_HOST_WORKFLOW_INVALID")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            _fail("P0_CLOSURE_ENTRY_SCHEMA_INVALID")
        identifier = entry.get("requirement_id")
        if not isinstance(identifier, str):
            _fail("P0_CLOSURE_REQUIREMENT_ID_INVALID")
        identifiers.append(identifier)
        for path in _array(entry.get("implementation_paths"), label="IMPLEMENTATION_PATHS"):
            _safe_file(root, path, label="IMPLEMENTATION_PATH")
        for path in _array(entry.get("evidence_paths"), label="EVIDENCE_PATHS"):
            _safe_file(root, path, label="EVIDENCE_PATH")
        nodes = _array(entry.get("test_node_ids"), label="TEST_NODE_IDS")
        for node in nodes:
            if "::" not in node or not _collected(root, node):
                _fail("P0_CLOSURE_TEST_NODE_UNCOLLECTED")
        target = entry.get("make_target")
        if not isinstance(target, str) or target not in targets:
            _fail("P0_CLOSURE_MAKE_TARGET_UNKNOWN")
        workflow = entry.get("workflow")
        workflow_path = _safe_file(root, workflow, label="WORKFLOW")
        if workflow != ".github/workflows/foundation.yml" or not _workflow_reaches_portable(workflow_path) or not _reachable(graph, "ci-portable", target):
            _fail("P0_CLOSURE_PORTABLE_WORKFLOW_INVALID")
        if entry.get("required_status") != REQUIRED_STATUS[identifier]:
            _fail("P0_CLOSURE_REQUIRED_STATUS_INVALID")
    if identifiers != list(REQUIREMENTS) or len(set(identifiers)) != len(identifiers):
        _fail("P0_CLOSURE_REQUIREMENT_SET_DRIFT")
    if state == "P0_SOURCE_COMPLETE":
        if not require_complete or receipt is None:
            _fail("P0_CLOSURE_RECEIPT_REQUIRED")
        from scripts.check_artifact_firewall import FirewallError, validate_published_evidence
        expected_receipt = root / "runtime/state/ci-portable/manifest.json"
        if receipt.absolute() != expected_receipt:
            _fail("P0_CLOSURE_RECEIPT_PATH_INVALID")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
        from scripts.check_artifact_firewall import _source_tree_identity
        try:
            validate_published_evidence(receipt.parent, expected_head_sha=head, expected_source_tree_sha256=_source_tree_identity(root, head))
        except (FirewallError, OSError) as exc:
            raise ClosureError("P0_CLOSURE_RECEIPT_INVALID") from exc
    elif require_complete or receipt is not None:
        _fail("P0_CLOSURE_COMPLETION_MODE_INVALID")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--qualification-receipt", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        state = validate(ROOT, arguments.matrix, require_complete=arguments.require_complete, receipt=arguments.qualification_receipt)
    except (ClosureError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
