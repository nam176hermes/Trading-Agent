"""Fail-closed validation of the source-controlled P0 closure matrix."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MATRIX_RELATIVE = "docs/implementation/p0-ci-closure-matrix.json"
MAKEFILE_RELATIVE = "Makefile"
FOUNDATION_WORKFLOW_RELATIVE = ".github/workflows/foundation.yml"
HOST_WORKFLOW_RELATIVE = ".github/workflows/host-authority.yml"
INVENTORY_RELATIVE = "tests/fixtures/t-g03a-hosted-failure-inventory.tsv"
CLOSURE_RELATIVE = "docs/implementation/foundation-portable-defect-closure.tsv"
BASELINE_RELATIVE = "ops/consolidation/p0-canonical-baseline.json"
RECEIPT_RELATIVE = "runtime/state/ci-portable/manifest.json"

TOP_KEYS = {"schema_version", "state", "requirement_order", "requirements"}
ENTRY_KEYS = {
    "requirement_id", "implementation_paths", "test_node_ids", "make_target",
    "workflow", "evidence_paths", "required_status",
}
REQUIREMENTS = tuple(
    [f"P0-I0{number}" for number in range(1, 7)]
    + [f"P0-E{number:02d}" for number in range(1, 14)]
)
SAFE_STATES = {"SOURCE_IMPLEMENTED", "QUALIFICATION_PENDING", "P0_SOURCE_COMPLETE"}
PENDING_STATUS = {
    **{identifier: "PASS" for identifier in REQUIREMENTS},
    "P0-E11": "PENDING",
    "P0-E12": "PENDING",
}
COMPLETE_STATUS = {**PENDING_STATUS, "P0-E11": "PASS"}
HEAD_SHA = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ClosureError(RuntimeError):
    """A source-closure authority contract failed."""


@dataclass(frozen=True)
class _ValidationContext:
    """Private immutable validation inputs; the public CLI creates only ROOT."""

    root: Path
    head_sha: str
    source_tree_sha256: str | None
    active_rows: tuple[object, ...]
    closed_rows: tuple[object, ...]
    collected_node_ids: frozenset[str] | None = None
    receipt_relative: str | None = None


_Binding = tuple[tuple[str, ...], str, str, tuple[str, ...]]
END_STATE_BINDINGS: dict[str, _Binding] = {
    "P0-E01": (
        ("scripts/audit_canonical_repo.py",),
        "tests/consolidation/test_p0_canonical_baseline.py::test_p0_baseline_manifest_is_approved_and_passes_the_portable_audit",
        "check-p0-baseline", ("ops/consolidation/p0-canonical-baseline.json",),
    ),
    "P0-E02": (
        ("scripts/t_g03_capability_topology.py",),
        "tests/governance/test_t_g03f_validation_date.py::test_foundation_context_seals_the_capture_date_and_rejects_date_environment",
        "ci-portable-topology", ("docs/implementation/foundation-test-governance-evidence.md",),
    ),
    "P0-E03": (
        ("scripts/t_g03_capability_topology.py",),
        "tests/governance/test_t_g03_portable_defect_closure.py::test_closure_proof_is_required_directly_and_accounts_every_node_once",
        "check-portable-defect-closure",
        ("docs/implementation/foundation-portable-defect-closure.tsv",),
    ),
    "P0-E04": (
        ("scripts/t_g03_capability_topology.py",),
        "tests/governance/test_t_g03_portable_defect_closure.py::test_portable_source_defects_are_closed_not_unresolved",
        "check-portable-defect-closure",
        (
            "docs/implementation/foundation-portable-defect-closure.tsv",
            "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
        ),
    ),
    "P0-E05": (
        ("scripts/t_g03_capability_topology.py",),
        "tests/governance/test_t_g03_capability_topology.py::test_native_v2_receipt_rejects_every_foreign_binding",
        "ci-portable-topology", ("docs/implementation/foundation-test-governance-evidence.md",),
    ),
    "P0-E06": (
        ("scripts/t_g03_capability_topology.py",),
        "tests/governance/test_t_g03_capability_topology.py::test_external_authority_inventory_classifies_missing_uv_with_closure_and_symlinked_corpus_child",
        "ci-portable-topology", ("docs/implementation/foundation-test-governance-evidence.md",),
    ),
    "P0-E07": (
        ("Makefile",),
        "tests/test_test_all_host_split.py::test_ci_routes_only_to_the_portable_gate_and_never_host_authority",
        "ci-portable", ("Makefile",),
    ),
    "P0-E08": (
        (FOUNDATION_WORKFLOW_RELATIVE, HOST_WORKFLOW_RELATIVE),
        "tests/test_test_all_host_split.py::test_workflows_are_partitioned_into_portable_and_dispatch_only_host_authority",
        "ci-portable", (FOUNDATION_WORKFLOW_RELATIVE, HOST_WORKFLOW_RELATIVE),
    ),
    "P0-E09": (
        ("scripts/check_artifact_firewall.py",),
        "tests/test_artifact_firewall.py::test_publisher_creates_canonical_manifest_and_exact_checksums",
        "artifact-firewall-check", ("docs/implementation/foundation-test-governance-evidence.md",),
    ),
    "P0-E10": (
        ("scripts/check_p0_ci_closure.py",),
        "tests/test_p0_ci_closure.py::test_pending_source_matrix_is_an_executable_closed_contract",
        "check-p0-ci-closure", ("docs/implementation/p0-ci-closure.md",),
    ),
    "P0-E11": (
        ("scripts/check_artifact_firewall.py", "scripts/check_p0_ci_closure.py"),
        "tests/test_p0_ci_closure.py::test_valid_strict_p0_10_fixture_exercises_completion_mode",
        "artifact-firewall-check", ("docs/implementation/p0-ci-closure.md",),
    ),
    "P0-E12": (
        ("scripts/audit_canonical_repo.py", "scripts/check_p0_ci_closure.py"),
        "tests/test_p0_ci_closure.py::test_completion_rejects_wrong_path_stale_tree_and_partial_receipt",
        "check-p0-ci-closure", ("docs/implementation/p0-ci-closure.md",),
    ),
    "P0-E13": (
        (
            FOUNDATION_WORKFLOW_RELATIVE, HOST_WORKFLOW_RELATIVE,
            "scripts/check_p0_ci_closure.py",
        ),
        "tests/test_p0_ci_closure.py::test_structural_workflow_contract_attacks_fail_closed",
        "check-p0-ci-closure", ("docs/implementation/p0-ci-closure.md",),
    ),
}


def _fail(message: str) -> None:
    raise ClosureError(message)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _relative(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        _fail(f"P0_CLOSURE_{label}_INVALID")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or str(relative) != value
    ):
        _fail(f"P0_CLOSURE_{label}_UNSAFE")
    return value


def _safe_read(context: _ValidationContext, value: object, *, label: str) -> bytes:
    """Read one tracked regular file through no-follow descriptors and revalidate."""
    relative = _relative(value, label=label)
    parts = PurePosixPath(relative).parts
    descriptors: list[int] = []
    leaf_descriptor: int | None = None
    try:
        root_before = context.root.lstat()
        if not stat.S_ISDIR(root_before.st_mode) or context.root.is_symlink():
            _fail(f"P0_CLOSURE_{label}_UNSAFE")
        current = os.open(
            context.root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        descriptors.append(current)
        for component in parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=current,
            )
            descriptors.append(current)
        leaf_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=current,
        )
        before = os.fstat(leaf_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail(f"P0_CLOSURE_{label}_UNSAFE")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(leaf_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(leaf_descriptor)
        path_after = (context.root / relative).lstat()
        if _identity(before) != _identity(after) or _identity(before) != _identity(path_after):
            _fail(f"P0_CLOSURE_{label}_UNSAFE")
    except ClosureError:
        raise
    except FileNotFoundError as exc:
        raise ClosureError(f"P0_CLOSURE_{label}_MISSING") from exc
    except OSError as exc:
        raise ClosureError(f"P0_CLOSURE_{label}_UNSAFE") from exc
    finally:
        if leaf_descriptor is not None:
            os.close(leaf_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=context.root, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if tracked.returncode != 0:
        _fail(f"P0_CLOSURE_{label}_UNTRACKED")
    try:
        root_final = context.root.lstat()
        final = (context.root / relative).lstat()
    except OSError as exc:
        raise ClosureError(f"P0_CLOSURE_{label}_UNSAFE") from exc
    if _identity(root_final) != _identity(root_before) or _identity(final) != _identity(before):
        _fail(f"P0_CLOSURE_{label}_UNSAFE")
    return b"".join(chunks)


class _DuplicateKey(ValueError):
    pass


def _json_object(raw: bytes, *, canonical: bool, label: str) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise _DuplicateKey(key)
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, _DuplicateKey) as exc:
        raise ClosureError(f"P0_CLOSURE_{label}_INVALID") from exc
    if not isinstance(value, dict):
        _fail(f"P0_CLOSURE_{label}_INVALID")
    if canonical and raw != _canonical(value):
        _fail(f"P0_CLOSURE_{label}_NONCANONICAL")
    return value


def _array(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        _fail(f"P0_CLOSURE_{label}_INVALID")
    if value != sorted(value) or len(set(value)) != len(value):
        _fail(f"P0_CLOSURE_{label}_NONCANONICAL")
    return value


def _strip_shell_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote is not None and character == quote:
            quote = None
        elif quote is None and character in {"'", '"'}:
            quote = character
        elif quote is None and character == "#":
            return line[:index]
    return line


def _make_graph(raw: bytes) -> dict[str, set[str]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ClosureError("P0_CLOSURE_MAKEFILE_INVALID") from exc
    graph: dict[str, set[str]] = {}
    current_targets: tuple[str, ...] = ()
    for source_line in lines:
        line = _strip_shell_comment(source_line).rstrip()
        if not line:
            continue
        if not line[0].isspace() and ":" in line and "=" not in line.split(":", 1)[0]:
            names_raw, dependencies_raw = line.split(":", 1)
            names = tuple(name for name in names_raw.split() if re.fullmatch(r"[A-Za-z0-9_.-]+", name))
            dependencies = {
                item for item in dependencies_raw.split()
                if re.fullmatch(r"[A-Za-z0-9_.-]+", item)
            }
            for name in names:
                graph.setdefault(name, set()).update(dependencies)
            current_targets = names
            continue
        if not current_targets or not source_line.startswith("\t"):
            continue
        for match in re.finditer(r"\$\(\s*MAKE\s*\)\s+([^;&|]+)", line):
            words = match.group(1).replace("\\", " ").split()
            called: list[str] = []
            for word in words:
                if word.startswith("-") or "=" in word or word.startswith("$(") or word.startswith("\"$"):
                    continue
                if re.fullmatch(r"[A-Za-z0-9_.-]+", word):
                    called.append(word)
            for target in current_targets:
                graph[target].update(called)
    return graph


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


@dataclass(frozen=True)
class _YamlLine:
    indent: int
    text: str


def _yaml_lines(raw: bytes) -> tuple[_YamlLine, ...]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ClosureError("P0_CLOSURE_WORKFLOW_INVALID") from exc
    if "\t" in text or re.search(r"(?:^|\s)[&*][A-Za-z0-9_-]+", text):
        _fail("P0_CLOSURE_WORKFLOW_INVALID")
    result: list[_YamlLine] = []
    for source in text.splitlines():
        stripped = _strip_shell_comment(source).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        if indent % 2:
            _fail("P0_CLOSURE_WORKFLOW_INVALID")
        result.append(_YamlLine(indent, stripped.strip()))
    return tuple(result)


def _unique_line(lines: Iterable[_YamlLine], indent: int, text: str) -> int:
    matches = [index for index, line in enumerate(lines) if line.indent == indent and line.text == text]
    if len(matches) != 1:
        raise ValueError(text)
    return matches[0]


def _block(lines: tuple[_YamlLine, ...], indent: int, key: str) -> tuple[_YamlLine, ...]:
    start = _unique_line(lines, indent, f"{key}:")
    end = start + 1
    while end < len(lines) and lines[end].indent > indent:
        end += 1
    return lines[start + 1:end]


def _direct_map(lines: Iterable[_YamlLine], indent: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if line.indent != indent or line.text.startswith("-") or ":" not in line.text:
            continue
        key, value = line.text.split(":", 1)
        if key in result:
            raise ValueError(key)
        result[key] = value.strip()
    return result


def _steps(job: tuple[_YamlLine, ...]) -> list[tuple[_YamlLine, ...]]:
    lines = _block(job, 4, "steps")
    starts = [index for index, line in enumerate(lines) if line.indent == 6 and line.text.startswith("- ")]
    if not starts:
        raise ValueError("steps")
    result: list[tuple[_YamlLine, ...]] = []
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(lines)
        first = lines[start]
        result.append((_YamlLine(8, first.text[2:]), *lines[start + 1:end]))
    return result


def _workflow_common(lines: tuple[_YamlLine, ...]) -> tuple[dict[str, str], tuple[_YamlLine, ...]]:
    top = _direct_map(lines, 0)
    if not {"name", "on", "permissions", "jobs"} <= set(top) or set(top) - {
        "name", "on", "permissions", "concurrency", "jobs",
    }:
        raise ValueError("top")
    permissions = _direct_map(_block(lines, 0, "permissions"), 2)
    if permissions != {"contents": "read"}:
        raise ValueError("permissions")
    uncommented = "\n".join(line.text for line in lines)
    forbidden = (
        "secrets.", "LIVE_EXECUTION_ENABLED: \"true\"",
        "LIVE_TRADING_APPROVED: \"true\"", "deploy", "migration",
        "broker", "exchange", "service restart", "scheduler",
    )
    if any(token.lower() in uncommented.lower() for token in forbidden):
        raise ValueError("forbidden")
    jobs = _block(lines, 0, "jobs")
    job_ids = [line.text[:-1] for line in jobs if line.indent == 2 and line.text.endswith(":")]
    if len(job_ids) != 1:
        raise ValueError("jobs")
    return top, tuple(jobs)


def _foundation_workflow_valid(raw: bytes) -> bool:
    try:
        lines = _yaml_lines(raw)
        _workflow_common(lines)
        triggers = _direct_map(_block(lines, 0, "on"), 2)
        if triggers != {"push": "", "pull_request": "", "workflow_dispatch": ""}:
            return False
        job = _block(lines, 2, "verify")
        direct = _direct_map(job, 4)
        if direct.get("runs-on") != "ubuntu-24.04" or "environment" in direct:
            return False
        env = _direct_map(_block(job, 4, "env"), 6)
        if env != {
            "CI": '"true"', "LIVE_EXECUTION_ENABLED": '"false"',
            "LIVE_TRADING_APPROVED": '"false"',
        }:
            return False
        steps = _steps(job)
        routes = [step for step in steps if _direct_map(step, 8).get("run") == "make ci-portable NONINTERACTIVE=1"]
        if len(routes) != 1:
            return False
        uploads = [step for step in steps if _direct_map(step, 8).get("uses") == "actions/upload-artifact@v4"]
        if len(uploads) != 1:
            return False
        upload = uploads[0]
        direct_upload = _direct_map(upload, 8)
        if direct_upload.get("name") != "Publish sealed portable evidence" or direct_upload.get("if") != "always()":
            return False
        with_values = _direct_map(_block(upload, 8, "with"), 10)
        return with_values == {
            "name": "ci-portable-${{ github.run_id }}-${{ github.run_attempt }}",
            "path": "runtime/state/ci-portable/**",
            "include-hidden-files": "true",
            "if-no-files-found": "warn",
            "retention-days": "14",
        }
    except (ClosureError, ValueError):
        return False


def _host_workflow_valid(raw: bytes) -> bool:
    try:
        lines = _yaml_lines(raw)
        _workflow_common(lines)
        triggers = _direct_map(_block(lines, 0, "on"), 2)
        if triggers != {"workflow_dispatch": ""}:
            return False
        job = _block(lines, 2, "qualify")
        direct = _direct_map(job, 4)
        if direct.get("runs-on") != "[self-hosted, linux, x64, trading-authority]":
            return False
        if direct.get("environment") != "trading-authority":
            return False
        env = _direct_map(_block(job, 4, "env"), 6)
        if env != {
            "CI": '"true"', "LIVE_EXECUTION_ENABLED": '"false"',
            "LIVE_TRADING_APPROVED": '"false"',
        }:
            return False
        steps = _steps(job)
        routes = [step for step in steps if _direct_map(step, 8).get("run") == "make ci-host-authority NONINTERACTIVE=1"]
        return len(routes) == 1 and not any(
            _direct_map(step, 8).get("uses", "").startswith("actions/upload-artifact@")
            for step in steps
        )
    except (ClosureError, ValueError):
        return False


def _topology(context: _ValidationContext) -> None:
    from scripts import t_g03_capability_topology as topology

    active = context.active_rows
    closed = context.closed_rows
    if any(
        getattr(row, "classification", None) == "PORTABLE_SOURCE_DEFECT"
        or str(getattr(row, "code", "")).startswith("SRC-")
        for row in active
    ):
        _fail("P0_CLOSURE_TOPOLOGY_ACTIVE_SOURCE_DEFECT")
    if any(
        topology.CODE_CLASSIFICATION.get(str(getattr(row, "code", "")))
        != getattr(row, "classification", None)
        for row in active
    ):
        _fail("P0_CLOSURE_TOPOLOGY_CLASSIFICATION_INVALID")
    if {getattr(row, "node_id", None) for row in active} & {
        getattr(row, "node_id", None) for row in closed
    }:
        _fail("P0_CLOSURE_TOPOLOGY_OVERLAP")
    if len(active) != 30 or len(closed) != 32:
        _fail("P0_CLOSURE_TOPOLOGY_COUNT_INVALID")
    native = sum(getattr(row, "classification", None) == "NATIVE_CAPABILITY_REQUIRED" for row in active)
    external = sum(getattr(row, "classification", None) == "EXTERNAL_AUTHORITY_REQUIRED" for row in active)
    if (native, external) != (24, 6):
        _fail("P0_CLOSURE_TOPOLOGY_COUNT_INVALID")
    if any(getattr(row, "proof_command", None) != topology.CLOSURE_PROOF_COMMAND for row in closed):
        _fail("P0_CLOSURE_TOPOLOGY_BROAD_SKIP")


def _baseline(context: _ValidationContext) -> None:
    raw = _safe_read(context, BASELINE_RELATIVE, label="BASELINE")
    value = _json_object(raw, canonical=False, label="BASELINE_JSON")
    required = {
        "schema_version", "base_branch", "base_sha", "candidate_source_branch",
        "candidate_start_sha", "qualified_sha", "promotion_mode", "paper_only",
        "live_execution_authorized",
    }
    if (
        set(value) != required
        or value.get("schema_version") != "p0-canonical-baseline/v1"
        or value.get("base_branch") != "main"
        or not isinstance(value.get("candidate_source_branch"), str)
        or not HEAD_SHA.fullmatch(str(value.get("base_sha", "")))
        or not HEAD_SHA.fullmatch(str(value.get("candidate_start_sha", "")))
        or value.get("promotion_mode") != "fast-forward-only"
    ):
        _fail("P0_CLOSURE_BASELINE_SCHEMA_INVALID")
    if (
        value.get("qualified_sha") is not None
        or value.get("paper_only") is not True
        or value.get("live_execution_authorized") is not False
    ):
        _fail("P0_CLOSURE_BASELINE_AUTHORITY_INVALID")


def _collected(context: _ValidationContext, node: str) -> bool:
    if context.collected_node_ids is not None:
        return node in context.collected_node_ids
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", node],
        cwd=context.root, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45,
    )
    return result.returncode == 0 and any(
        line == node or line.startswith(f"{node}[") for line in result.stdout.splitlines()
    )


def _production_context() -> _ValidationContext:
    from scripts import t_g03_capability_topology as topology

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=10,
        ).stdout.strip()
        active, closed = topology.load_governance_state(
            ROOT / INVENTORY_RELATIVE,
            ROOT / CLOSURE_RELATIVE,
            head_sha=head,
        )
    except (OSError, subprocess.SubprocessError, topology.TopologyError) as exc:
        raise ClosureError("P0_CLOSURE_TOPOLOGY_AUTHORITY_INVALID") from exc
    return _ValidationContext(
        root=ROOT,
        head_sha=head,
        source_tree_sha256=None,
        active_rows=active,
        closed_rows=closed,
    )


def _validate_completion(context: _ValidationContext) -> None:
    if context.receipt_relative is None:
        _fail("P0_CLOSURE_RECEIPT_REQUIRED")
    if context.receipt_relative != RECEIPT_RELATIVE:
        _fail("P0_CLOSURE_RECEIPT_PATH_INVALID")
    from scripts.check_artifact_firewall import (
        FirewallError, _source_tree_identity, validate_published_evidence,
    )
    expected_tree = context.source_tree_sha256
    try:
        if expected_tree is None:
            expected_tree = _source_tree_identity(context.root, context.head_sha)
        if not HEAD_SHA.fullmatch(context.head_sha) or not HEX64.fullmatch(expected_tree):
            _fail("P0_CLOSURE_RECEIPT_INVALID")
        receipt = context.root / RECEIPT_RELATIVE
        manifest = validate_published_evidence(
            receipt.parent,
            expected_head_sha=context.head_sha,
            expected_source_tree_sha256=expected_tree,
        )
        semantic = manifest.get("semantic_projection")
        statuses = semantic.get("statuses") if isinstance(semantic, dict) else None
        if (
            not isinstance(statuses, dict)
            or statuses.get("portable_source_status") != "PASS"
            or "governance_error" in semantic
            or not isinstance(semantic.get("selected_tests"), list)
        ):
            _fail("P0_CLOSURE_RECEIPT_INVALID")
    except ClosureError:
        raise
    except (FirewallError, OSError, subprocess.SubprocessError) as exc:
        raise ClosureError("P0_CLOSURE_RECEIPT_INVALID") from exc


def _validate(context: _ValidationContext, *, require_complete: bool) -> str:
    _baseline(context)
    matrix_raw = _safe_read(context, MATRIX_RELATIVE, label="MATRIX")
    document = _json_object(matrix_raw, canonical=True, label="JSON")
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

    make_raw = _safe_read(context, MAKEFILE_RELATIVE, label="MAKEFILE")
    foundation_raw = _safe_read(context, FOUNDATION_WORKFLOW_RELATIVE, label="FOUNDATION_WORKFLOW")
    host_raw = _safe_read(context, HOST_WORKFLOW_RELATIVE, label="HOST_WORKFLOW")
    graph = _make_graph(make_raw)
    if not _foundation_workflow_valid(foundation_raw):
        _fail("P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID")
    if not _host_workflow_valid(host_raw):
        _fail("P0_CLOSURE_HOST_WORKFLOW_INVALID")
    _topology(context)

    expected_status = COMPLETE_STATUS if state == "P0_SOURCE_COMPLETE" else PENDING_STATUS
    identifiers: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            _fail("P0_CLOSURE_ENTRY_SCHEMA_INVALID")
        identifier = entry.get("requirement_id")
        if not isinstance(identifier, str) or identifier not in expected_status:
            _fail("P0_CLOSURE_REQUIREMENT_SET_DRIFT")
        identifiers.append(identifier)
        implementation_paths = _array(entry.get("implementation_paths"), label="IMPLEMENTATION_PATHS")
        for path in implementation_paths:
            _safe_read(context, path, label="IMPLEMENTATION_PATH")
        evidence_paths = _array(entry.get("evidence_paths"), label="EVIDENCE_PATHS")
        for path in evidence_paths:
            if path.startswith("runtime/") or "skip" in PurePosixPath(path).name.lower():
                _fail("P0_CLOSURE_EVIDENCE_LAYOUT_INVALID")
            _safe_read(context, path, label="EVIDENCE_PATH")
        nodes = _array(entry.get("test_node_ids"), label="TEST_NODE_IDS")
        for node in nodes:
            if "::" not in node or not _collected(context, node):
                _fail("P0_CLOSURE_TEST_NODE_UNCOLLECTED")
            _safe_read(context, node.split("::", 1)[0], label="TEST_PATH")
        target = entry.get("make_target")
        if not isinstance(target, str) or target not in graph:
            _fail("P0_CLOSURE_MAKE_TARGET_UNKNOWN")
        workflow = entry.get("workflow")
        _relative(workflow, label="WORKFLOW")
        if workflow != FOUNDATION_WORKFLOW_RELATIVE:
            _fail("P0_CLOSURE_PORTABLE_WORKFLOW_INVALID")
        if not _reachable(graph, "ci-portable", target):
            _fail("P0_CLOSURE_MAKE_TARGET_UNREACHABLE")
        if entry.get("required_status") != expected_status[identifier]:
            _fail("P0_CLOSURE_REQUIRED_STATUS_INVALID")
        if identifier in END_STATE_BINDINGS and (
            tuple(implementation_paths),
            nodes[0] if len(nodes) == 1 else "",
            target,
            tuple(evidence_paths),
        ) != END_STATE_BINDINGS[identifier]:
            _fail("P0_CLOSURE_END_STATE_BINDING_INVALID")
    if identifiers != list(REQUIREMENTS) or len(set(identifiers)) != len(identifiers):
        _fail("P0_CLOSURE_REQUIREMENT_SET_DRIFT")

    if state == "P0_SOURCE_COMPLETE":
        if not require_complete:
            _fail("P0_CLOSURE_RECEIPT_REQUIRED")
        _validate_completion(context)
    elif require_complete or context.receipt_relative is not None:
        _fail("P0_CLOSURE_COMPLETION_MODE_INVALID")
    return str(state)


def _canonical_cli_path(value: Path, relative: str) -> bool:
    expected = ROOT / relative
    if value.is_absolute():
        return value == expected
    return value == Path(relative)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--qualification-receipt", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if not _canonical_cli_path(arguments.matrix, MATRIX_RELATIVE):
            _fail("P0_CLOSURE_MATRIX_PATH_INVALID")
        context = _production_context()
        if arguments.qualification_receipt is not None:
            if not _canonical_cli_path(arguments.qualification_receipt, RECEIPT_RELATIVE):
                _fail("P0_CLOSURE_RECEIPT_PATH_INVALID")
            context = replace(context, receipt_relative=RECEIPT_RELATIVE)
        state = _validate(context, require_complete=arguments.require_complete)
    except (ClosureError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
