#!/usr/bin/env python3
"""Measure critical Python and dashboard branch coverage and enforce ratchets."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "tests/critical-coverage-policy.json"
DEFAULT_REPORT_DIR = Path("/tmp/trading-agent-test-evidence/critical-coverage")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_GENERATED_ARTIFACTS = (
    ".critical-coverage",
    "critical-coverage-python.json",
    "critical-coverage-python-tests.json",
    "critical-coverage-dashboard.lcov",
    "critical-coverage.json",
    "critical-coverage-error.json",
    "critical-coverage-python-tests.log",
    "critical-coverage-python-json.log",
    "critical-coverage-dashboard-tests.log",
    "critical-coverage-dashboard-tests.json",
    ".critical-coverage-dashboard-aggregate.log",
)
_TRANSIENT_ARTIFACT_PATTERNS = (
    ".critical-coverage.*",
    ".critical-coverage-python.*.json",
    ".critical-coverage-python-tests.*.json",
    ".critical-coverage-dashboard.*.lcov",
)
_SEALED_PYTHON_SOURCES = (
    "packages/domain", "packages/event_ledger", "services/job_worker",
    "packages/job_contracts", "packages/job_authority", "services/job_store",
)
_SEALED_PYTEST_PATHS = (
    "tests/domain", "tests/event_ledger", "tests/jobs/test_state_machine.py",
    "tests/jobs/test_worker_safety.py", "tests/jobs/test_safety_state.py",
    "tests/jobs/test_child_environment.py",
    "tests/jobs/test_job_authority_verifier.py",
    "tests/jobs/test_repository_transition_capabilities.py",
    "tests/jobs/test_repository_cancel_acl.py",
    "tests/jobs/test_repository_enqueue.py", "tests/jobs/test_repository_queries.py",
    "tests/jobs/test_repository_transactions.py", "tests/jobs/test_worker_leases.py",
    "tests/jobs/test_worker_lifecycle.py",
)
_SEALED_PYTHON_UNITS = {
    "packages/domain": (("packages/domain/",), (), (), (665, 699), (173, 198)),
    "packages/event_ledger": (("packages/event_ledger/",), (), (), (422, 443), (137, 152)),
    "services/job_worker/safety.py": ((), ("services/job_worker/safety.py",), (), (119, 137), (45, 56)),
    "job state machine": ((), ("packages/job_contracts/enums.py", "packages/job_contracts/transitions.py"), (), (67, 67), (16, 16)),
    "transition authority": (("packages/job_authority/",), (), (), (209, 242), (72, 92)),
    "transition repositories": ((), ("services/job_store/repository.py", "services/job_store/worker_repository.py"), (), (251, 427), (34, 118)),
}
_SEALED_DASHBOARD_FILES = (
    "src/lib/trading/access-policy.ts", "src/lib/trading/auth.ts",
    "src/lib/trading/request-body.ts", "src/lib/trading/session.ts", "src/proxy.ts",
)
_SEALED_DASHBOARD_TESTS = (
    "tests/session-policy.test.mjs", "tests/mutation-policy.test.mjs",
    "tests/api-access-boundary.test.mjs", "tests/dashboard-request-hardening.test.mjs",
    "tests/browser-auth-source.test.mjs",
)
_SEALED_DASHBOARD_UNIT = (
    "dashboard auth and mutation policy", _SEALED_DASHBOARD_FILES, (430, 449), (141, 159),
)
def _exact_variants(base: str, *variants: str) -> tuple[str, ...]:
    return tuple(f"{base}[{variant}]" for variant in variants)


_SAFETY_EVIDENCE_VARIANTS = _exact_variants(
    "tests/jobs/test_worker_safety.py::test_every_missing_or_noncanonical_safety_evidence_blocks",
    "changes0-SAFETY_REQUESTED_MODE_UNKNOWN",
    "changes1-SAFETY_REQUESTED_MODE_NOT_PAPER",
    "changes2-SAFETY_EFFECTIVE_MODE_UNKNOWN",
    "changes3-SAFETY_LIVE_EXECUTION_GATE_UNKNOWN",
    "changes4-SAFETY_LIVE_EXECUTION_GATE_ENABLED",
    "changes5-SAFETY_LIVE_APPROVAL_GATE_UNKNOWN",
    "changes6-SAFETY_LIVE_APPROVAL_GATE_ENABLED",
    "changes7-SAFETY_KILL_SWITCH_UNKNOWN",
    "changes8-SAFETY_KILL_SWITCH_ACTIVE",
)
_STALE_SAFETY_VARIANTS = _exact_variants(
    "tests/jobs/test_safety_state.py::test_client_rejects_stale_mismatched_or_non_safe_snapshots",
    "changes0-SAFETY_STATE_EXPORTER_COMMIT_MISMATCH",
    "changes1-SAFETY_STATE_SOURCE_MISMATCH",
    "changes2-SAFETY_STATE_FROM_FUTURE",
    "changes3-SAFETY_STATE_STALE",
    "changes4-SAFETY_STATE_WINDOW_INVALID",
    "changes5-SAFETY_REQUESTED_MODE_NOT_PAPER",
    "changes6-SAFETY_KILL_SWITCH_ACTIVE",
)
_CHILD_OVERRIDE_VARIANTS = _exact_variants(
    "tests/jobs/test_child_environment.py::test_path_and_root_overrides_are_rejected",
    "TRADING_DATA_ROOT",
    "TRADING_REPORTS_DIR",
    "TRADING_SIGNAL_OUTPUT_DIR",
    "TRADING_JOB_ID",
    "TRADING_JOB_ATTEMPT_ID",
    "TRADING_ATTEMPT_ID",
    "TRADING_RESEARCH_BACKEND_COMMIT",
    "TRADING_RESEARCH_SCRATCHPAD_ROOT",
)
_MANIFEST_DIGEST_VARIANTS = _exact_variants(
    "tests/jobs/test_job_authority_verifier.py::test_authority_manifest_rejects_valid_shape_with_wrong_catalog_digest",
    "pre_sha256-catalog_0007_snapshot-pre",
    "post_sha256-catalog_0006_snapshot-post",
)
_SEALED_REQUIRED_CASES = {
    "paper mode": (
        "tests/jobs/test_worker_safety.py::test_only_complete_explicit_paper_evidence_passes",
    ),
    "unknown mode and both live gates": _SAFETY_EVIDENCE_VARIANTS,
    "kill switch active and unknown": _SAFETY_EVIDENCE_VARIANTS[-2:],
    "invalid manifest": (
        "tests/jobs/test_job_authority_verifier.py::test_authority_manifest_rejects_placeholder_input_hash",
        *_MANIFEST_DIGEST_VARIANTS,
    ),
    "stale safety evidence": _STALE_SAFETY_VARIANTS,
    "unsafe child environment": (
        *_CHILD_OVERRIDE_VARIANTS,
        "tests/jobs/test_child_environment.py::test_child_can_only_see_bound_semantic_tree_and_exact_output_roots",
    ),
    "cancellation during heartbeat": (
        "tests/jobs/test_worker_lifecycle.py::test_cancel_race_after_popen_and_before_start_finalizes_claimed_attempt",
    ),
    "lease and fence mismatch": (
        "tests/jobs/test_repository_transition_capabilities.py::test_worker_lifecycle_calls_fail_closed_when_the_lease_fence_is_stale",
    ),
    "transition and event atomic failure": (
        "tests/jobs/test_repository_cancel_acl.py::test_cancel_event_failure_escapes_the_same_transaction_for_rollback",
    ),
    "dashboard auth timeout, origin and body-size failures": (
        "apps/dashboard/tests/browser-auth-source.test.mjs::auth guard stays fail-closed on timeout and network errors",
        "apps/dashboard/tests/api-access-boundary.test.mjs::rejects absent and cross-origin mutation origins",
        "apps/dashboard/tests/dashboard-request-hardening.test.mjs::shared reader rejects declared, chunked, and invalid UTF-8 request bodies",
    ),
}


class CoverageGateError(RuntimeError):
    """Critical coverage is absent, malformed, or below its ratchet."""


def evaluate_ratio(
    *,
    covered: int,
    total: int,
    minimum_covered: int,
    minimum_total: int,
    label: str,
) -> float:
    """Compare ratios exactly before returning a display percentage."""

    values = (covered, total, minimum_covered, minimum_total)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise CoverageGateError(f"{label} has invalid non-integer coverage counts")
    if total <= 0 or minimum_total <= 0:
        raise CoverageGateError(f"{label} has invalid zero-sized coverage scope")
    if covered < 0 or covered > total or minimum_covered < 0 or minimum_covered > minimum_total:
        raise CoverageGateError(f"{label} has invalid coverage counts")
    if covered < minimum_covered:
        raise CoverageGateError(f"{label} covered count regressed below its floor")
    if total < minimum_total:
        raise CoverageGateError(f"{label} coverage scope shrank below its floor")
    if covered * minimum_total < minimum_covered * total:
        actual = 100.0 * covered / total
        minimum = 100.0 * minimum_covered / minimum_total
        raise CoverageGateError(
            f"{label} regressed: {actual:.4f}% is below {minimum:.4f}%"
        )
    return 100.0 * covered / total


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoverageGateError(f"cannot read coverage document {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise CoverageGateError(f"coverage document must be an object: {path}")
    return document


def _prepare_private_directory(path: Path) -> None:
    try:
        absolute = path.absolute()
        lineage = [absolute, *absolute.parents]
        below_trusted_sticky_root = False
        for ancestor in reversed(lineage):
            try:
                metadata = ancestor.lstat()
            except FileNotFoundError:
                ancestor.mkdir(mode=0o700)
                metadata = ancestor.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError
            if metadata.st_uid not in {0, os.getuid()}:
                raise OSError
            trusted_sticky = (
                metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
            )
            writable = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            if (
                writable
                and metadata.st_uid == os.getuid()
                and (below_trusted_sticky_root or ancestor == absolute)
            ):
                os.chmod(ancestor, 0o700)
                metadata = ancestor.lstat()
            writable = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            if writable and not trusted_sticky:
                raise OSError
            below_trusted_sticky_root = below_trusted_sticky_root or trusted_sticky
        os.chmod(path, 0o700)
    except OSError as exc:
        raise CoverageGateError("coverage report directory is not a private owned directory") from exc


def _write_bytes(path: Path, content: bytes) -> None:
    _prepare_private_directory(path.parent)
    expected = path.parent.lstat()
    temporary_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
    directory: int | None = None
    descriptor: int | None = None
    published = False
    try:
        directory = os.open(
            path.parent,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
        )
        actual = os.fstat(directory)
        if (
            (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
            or actual.st_uid != os.getuid()
            or actual.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise OSError
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        published = True
        os.fsync(directory)
        current = path.parent.lstat()
        if (current.st_dev, current.st_ino) != (actual.st_dev, actual.st_ino):
            raise OSError
    except OSError as exc:
        if directory is not None:
            try:
                os.unlink(path.name if published else temporary_name, dir_fd=directory)
            except OSError:
                pass
        raise CoverageGateError(f"cannot write coverage artifact {path.name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _write_json(path: Path, document: object) -> None:
    _write_bytes(path, (json.dumps(document, indent=2, sort_keys=True) + "\n").encode())


def _remove_artifacts(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CoverageGateError(f"cannot remove stale coverage artifact {path}: {exc}") from exc


def _remove_transient_artifacts(report_dir: Path) -> None:
    for pattern in _TRANSIENT_ARTIFACT_PATTERNS:
        for path in report_dir.glob(pattern):
            try:
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    raise OSError
                path.unlink()
            except OSError as exc:
                raise CoverageGateError(
                    f"cannot remove stale transient coverage artifact {path.name}"
                ) from exc


def _node_major() -> int:
    result = subprocess.run(
        ["node", "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    match = re.fullmatch(r"v(\d+)(?:\.\d+){2}\s*", result.stdout)
    if result.returncode != 0 or match is None:
        raise CoverageGateError(f"cannot determine supported Node version: {result.stdout.strip()}")
    return int(match.group(1))


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> int:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    _write_bytes(log_path, result.stdout.encode("utf-8"))
    print(result.stdout, end="")
    return result.returncode


def _matches(path: str, unit: dict[str, Any]) -> bool:
    files = unit.get("files", [])
    prefixes = unit.get("prefixes", [])
    patterns = unit.get("patterns", [])
    return (
        path in files
        or any(path.startswith(prefix) for prefix in prefixes)
        or any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
    )


def _minimum(unit: dict[str, Any], metric: str) -> tuple[int, int]:
    minimum = unit.get(f"{metric}_minimum")
    if not isinstance(minimum, dict):
        raise CoverageGateError(f"{unit.get('name')} lacks {metric}_minimum")
    covered = minimum.get("covered")
    total = minimum.get("total")
    if not isinstance(covered, int) or not isinstance(total, int):
        raise CoverageGateError(f"{unit.get('name')} has invalid {metric}_minimum")
    return covered, total


def _evaluate_python(
    coverage_document: dict[str, Any],
    units: list[dict[str, Any]],
) -> list[dict[str, object]]:
    files = coverage_document.get("files")
    if not isinstance(files, dict):
        raise CoverageGateError("Python coverage JSON has no files map")
    results: list[dict[str, object]] = []
    for unit in units:
        explicit_files = unit.get("files", [])
        if not isinstance(explicit_files, list):
            raise CoverageGateError(f"{unit.get('name')} has invalid files scope")
        missing_files = sorted(set(explicit_files) - set(files))
        if missing_files:
            raise CoverageGateError(
                f"critical Python unit is missing measured files: {unit['name']}: "
                + ", ".join(missing_files)
            )
        selected = [
            (path, document)
            for path, document in files.items()
            if isinstance(document, dict) and _matches(path, unit)
        ]
        if not selected:
            raise CoverageGateError(f"critical Python unit has no measured files: {unit['name']}")
        summaries: list[dict[str, Any]] = []
        for _, document in selected:
            summary = document.get("summary")
            if not isinstance(summary, dict):
                raise CoverageGateError(
                    f"invalid Python coverage summary: {unit['name']}"
                )
            summaries.append(summary)
        counter_names = (
            "covered_lines", "num_statements", "covered_branches", "num_branches"
        )
        for summary in summaries:
            if any(type(summary.get(name)) is not int for name in counter_names):
                raise CoverageGateError(
                    f"invalid Python coverage counters: {unit['name']}"
                )
        line_covered = sum(summary["covered_lines"] for summary in summaries)
        line_total = sum(summary["num_statements"] for summary in summaries)
        branch_covered = sum(summary["covered_branches"] for summary in summaries)
        branch_total = sum(summary["num_branches"] for summary in summaries)
        line_minimum = _minimum(unit, "line")
        branch_minimum = _minimum(unit, "branch")
        line_percent = evaluate_ratio(
            covered=line_covered,
            total=line_total,
            minimum_covered=line_minimum[0],
            minimum_total=line_minimum[1],
            label=f"{unit['name']} lines",
        )
        branch_percent = evaluate_ratio(
            covered=branch_covered,
            total=branch_total,
            minimum_covered=branch_minimum[0],
            minimum_total=branch_minimum[1],
            label=f"{unit['name']} branches",
        )
        results.append(
            {
                "name": unit["name"],
                "files": sorted(path for path, _ in selected),
                "lines": {
                    "covered": line_covered,
                    "total": line_total,
                    "percent": round(line_percent, 4),
                    "goal_percent": unit["line_goal_percent"],
                    "next_step_percent": unit["line_next_step_percent"],
                },
                "branches": {
                    "covered": branch_covered,
                    "total": branch_total,
                    "percent": round(branch_percent, 4),
                    "goal_percent": unit["branch_goal_percent"],
                    "next_step_percent": unit["branch_next_step_percent"],
                },
            }
        )
    return results


def _parse_lcov(path: Path) -> dict[str, dict[str, int]]:
    if not path.is_file():
        raise CoverageGateError(f"dashboard LCOV report is missing: {path}")
    files: dict[str, dict[str, int]] = {}
    current: dict[str, int] = {}
    source: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:"):
            source = line[3:]
            current = {}
        elif line.startswith(("LF:", "LH:", "BRF:", "BRH:")):
            key, value = line.split(":", 1)
            current[key] = int(value)
        elif line == "end_of_record" and source is not None:
            files[source] = current
            source = None
            current = {}
    if not files:
        raise CoverageGateError("dashboard LCOV report contains no files")
    return files


def _evaluate_dashboard(
    lcov_files: dict[str, dict[str, int]],
    unit: dict[str, Any],
) -> dict[str, object]:
    explicit_files = unit.get("files", [])
    if not isinstance(explicit_files, list):
        raise CoverageGateError("dashboard critical coverage files scope is invalid")
    missing_files = sorted(set(explicit_files) - set(lcov_files))
    if missing_files:
        raise CoverageGateError(
            "dashboard critical coverage is missing files: " + ", ".join(missing_files)
        )
    selected = [(path, summary) for path, summary in lcov_files.items() if _matches(path, unit)]
    if not selected:
        raise CoverageGateError("dashboard critical coverage scope has no measured files")
    line_covered = sum(summary.get("LH", 0) for _, summary in selected)
    line_total = sum(summary.get("LF", 0) for _, summary in selected)
    branch_covered = sum(summary.get("BRH", 0) for _, summary in selected)
    branch_total = sum(summary.get("BRF", 0) for _, summary in selected)
    line_minimum = _minimum(unit, "line")
    branch_minimum = _minimum(unit, "branch")
    line_percent = evaluate_ratio(
        covered=line_covered,
        total=line_total,
        minimum_covered=line_minimum[0],
        minimum_total=line_minimum[1],
        label="dashboard auth/mutation lines",
    )
    branch_percent = evaluate_ratio(
        covered=branch_covered,
        total=branch_total,
        minimum_covered=branch_minimum[0],
        minimum_total=branch_minimum[1],
        label="dashboard auth/mutation branches",
    )
    return {
        "name": unit["name"],
        "files": sorted(path for path, _ in selected),
        "lines": {
            "covered": line_covered,
            "total": line_total,
            "percent": round(line_percent, 4),
            "goal_percent": unit["line_goal_percent"],
            "next_step_percent": unit["line_next_step_percent"],
        },
        "branches": {
            "covered": branch_covered,
            "total": branch_total,
            "percent": round(branch_percent, 4),
            "goal_percent": unit["branch_goal_percent"],
            "next_step_percent": unit["branch_next_step_percent"],
        },
    }


def _require_string_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise CoverageGateError(f"coverage policy has invalid {label}")
    return value


def _validate_unit(unit: object, label: str) -> None:
    if not isinstance(unit, dict) or not isinstance(unit.get("name"), str):
        raise CoverageGateError(f"coverage policy has invalid {label}")
    selectors = 0
    for selector in ("files", "prefixes", "patterns"):
        value = unit.get(selector, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise CoverageGateError(f"{unit['name']} has invalid {selector}")
        selectors += len(value)
    if selectors == 0:
        raise CoverageGateError(f"{unit['name']} has no source selectors")
    for metric in ("line", "branch"):
        covered, total = _minimum(unit, metric)
        evaluate_ratio(
            covered=covered,
            total=total,
            minimum_covered=covered,
            minimum_total=total,
            label=f"{unit['name']} {metric} minimum",
        )
        for field in (f"{metric}_goal_percent", f"{metric}_next_step_percent"):
            value = unit.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                or value > 100
            ):
                raise CoverageGateError(f"{unit['name']} has invalid {field}")


def _validate_sealed_program(policy: dict[str, Any]) -> None:
    python = policy["python"]
    dashboard = policy["dashboard"]
    if tuple(python["sources"]) != _SEALED_PYTHON_SOURCES:
        raise CoverageGateError("sealed critical coverage Python sources changed")
    if tuple(python["pytest_paths"]) != _SEALED_PYTEST_PATHS:
        raise CoverageGateError("sealed critical coverage pytest paths changed")
    units = python["units"]
    by_name = {unit.get("name"): unit for unit in units if isinstance(unit, dict)}
    if set(by_name) != set(_SEALED_PYTHON_UNITS) or len(by_name) != len(units):
        raise CoverageGateError("sealed critical coverage Python units changed")
    for name, (prefixes, files, patterns, line_floor, branch_floor) in _SEALED_PYTHON_UNITS.items():
        unit = by_name[name]
        if (
            tuple(unit.get("prefixes", ())) != prefixes
            or tuple(unit.get("files", ())) != files
            or tuple(unit.get("patterns", ())) != patterns
        ):
            raise CoverageGateError(f"sealed critical coverage selectors changed: {name}")
        for metric, floor in (("line", line_floor), ("branch", branch_floor)):
            covered, total = _minimum(unit, metric)
            if covered < floor[0] or total < floor[1]:
                raise CoverageGateError(
                    f"sealed critical coverage minimum dropped: {name} {metric}"
                )
    if tuple(dashboard["include_files"]) != _SEALED_DASHBOARD_FILES:
        raise CoverageGateError("sealed critical coverage dashboard includes changed")
    if tuple(dashboard["test_files"]) != _SEALED_DASHBOARD_TESTS:
        raise CoverageGateError("sealed critical coverage dashboard tests changed")
    dashboard_unit = dashboard["unit"]
    dashboard_name, dashboard_files, line_floor, branch_floor = _SEALED_DASHBOARD_UNIT
    if (
        dashboard_unit.get("name") != dashboard_name
        or tuple(dashboard_unit.get("files", ())) != dashboard_files
        or tuple(dashboard_unit.get("prefixes", ()))
        or tuple(dashboard_unit.get("patterns", ()))
    ):
        raise CoverageGateError("sealed critical coverage dashboard unit changed")
    for metric, floor in (("line", line_floor), ("branch", branch_floor)):
        covered, total = _minimum(dashboard_unit, metric)
        if covered < floor[0] or total < floor[1]:
            raise CoverageGateError(
                f"sealed critical coverage dashboard {metric} minimum dropped"
            )
    cases = policy["required_cases"]
    actual_cases = {
        case.get("case"): tuple(case.get("tests", ()))
        for case in cases
        if isinstance(case, dict)
    }
    if actual_cases != _SEALED_REQUIRED_CASES or len(actual_cases) != len(cases):
        raise CoverageGateError("sealed critical coverage required cases changed")


def _validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != 1:
        raise CoverageGateError("coverage policy schema_version must be 1")
    goals = policy.get("long_term_goals")
    if not isinstance(goals, dict):
        raise CoverageGateError("coverage policy has invalid long-term goals")
    line_goal = goals.get("line_percent")
    branch_goal = goals.get("branch_percent")
    if (
        isinstance(line_goal, bool)
        or not isinstance(line_goal, (int, float))
        or line_goal < 95
        or line_goal > 100
        or isinstance(branch_goal, bool)
        or not isinstance(branch_goal, (int, float))
        or branch_goal < 90
        or branch_goal > 100
    ):
        raise CoverageGateError("coverage policy goals cannot drop below 95% lines and 90% branches")
    python = policy.get("python")
    dashboard = policy.get("dashboard")
    if not isinstance(python, dict) or not isinstance(dashboard, dict):
        raise CoverageGateError("coverage policy must define Python and dashboard scopes")
    if not isinstance(python.get("coverage_version"), str) or not python["coverage_version"]:
        raise CoverageGateError("coverage policy has invalid Coverage.py version")
    _require_string_list(python.get("sources"), "Python sources")
    _require_string_list(python.get("pytest_paths"), "Python pytest paths")
    units = python.get("units")
    if not isinstance(units, list) or not units:
        raise CoverageGateError("coverage policy has no Python units")
    for index, unit in enumerate(units):
        _validate_unit(unit, f"Python unit {index}")
    dashboard_unit = dashboard.get("unit")
    _validate_unit(dashboard_unit, "dashboard unit")
    _require_string_list(dashboard.get("include_files"), "dashboard include files")
    _require_string_list(dashboard.get("test_files"), "dashboard test files")
    required_cases = policy.get("required_cases")
    if not isinstance(required_cases, list) or not required_cases:
        raise CoverageGateError("coverage policy has no required safety cases")
    for case in required_cases:
        if not isinstance(case, dict) or not isinstance(case.get("case"), str):
            raise CoverageGateError("coverage policy has an invalid required case")
        tests = _require_string_list(case.get("tests"), f"required case {case['case']}")
        if any("::" not in node for node in tests):
            raise CoverageGateError(f"required case {case['case']} has invalid node IDs")
    _validate_sealed_program(policy)


def _dashboard_records(output: str, test_file: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in output.splitlines():
        match = re.match(
            r"^\s*(not )?ok\s+\d+\s+-\s+(.+?)(?:\s+#\s+(SKIP|TODO)\b.*)?$",
            line,
        )
        if match is None:
            continue
        failed, name, directive = match.groups()
        outcome = "failed" if failed else "passed"
        if directive is not None:
            outcome = directive.lower()
        records.append(
            {
                "test_node_id": f"apps/dashboard/{test_file}::{name.strip()}",
                "outcome": outcome,
            }
        )
    return records


def _run_dashboard_observations(
    dashboard: Path,
    test_files: list[str],
    report_dir: Path,
    env: dict[str, str],
) -> tuple[int, Path]:
    env = {
        **env,
        "TMPDIR": "/tmp",
        "TEMP": "/tmp",
        "TMP": "/tmp",
    }
    records: list[dict[str, str]] = []
    logs: list[str] = []
    exit_codes: list[int] = []
    for test_file in test_files:
        result = subprocess.run(
            ["node", "--test", "--test-reporter=tap", test_file],
            cwd=dashboard,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        logs.append(f"# file: {test_file}\n{result.stdout}")
        file_records = _dashboard_records(result.stdout, test_file)
        if not file_records:
            file_records.append(
                {
                    "test_node_id": (
                        f"apps/dashboard/{test_file}::static-test-inventory"
                    ),
                    "outcome": "failed",
                }
            )
            exit_codes.append(1)
        else:
            exit_codes.append(result.returncode)
        records.extend(file_records)
    log_path = report_dir / "critical-coverage-dashboard-tests.log"
    _write_bytes(log_path, "".join(logs).encode("utf-8"))
    print("".join(logs), end="")
    report_path = report_dir / "critical-coverage-dashboard-tests.json"
    _write_json(report_path, {"schema_version": 1, "tests": records})
    return max(exit_codes, default=1), report_path


def _evaluate_required_cases(
    required_cases: list[dict[str, Any]],
    python_test_report: dict[str, Any],
    dashboard_report_path: Path,
) -> list[dict[str, object]]:
    records = python_test_report.get("tests")
    if not isinstance(records, list):
        raise CoverageGateError("Python coverage test report has no tests")
    python_outcomes: dict[str, list[str]] = {}
    valid_outcomes = {"passed", "failed", "skipped", "deselected", "not_run"}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("test_node_id"), str):
            raise CoverageGateError("Python coverage test report has an invalid test record")
        outcome = record.get("outcome")
        if not isinstance(outcome, str) or outcome not in valid_outcomes:
            raise CoverageGateError("Python coverage test report has an invalid outcome")
        python_outcomes.setdefault(str(record["test_node_id"]), []).append(outcome)
    duplicates = sorted(node for node, outcomes in python_outcomes.items() if len(outcomes) != 1)
    if duplicates:
        raise CoverageGateError("duplicate Python test observations: " + ", ".join(duplicates))
    dashboard_document = _read_json(dashboard_report_path)
    dashboard_records = dashboard_document.get("tests")
    if not isinstance(dashboard_records, list):
        raise CoverageGateError("dashboard coverage report has no tests")
    dashboard_outcomes: dict[str, list[str]] = {}
    for record in dashboard_records:
        if not isinstance(record, dict) or not isinstance(record.get("test_node_id"), str):
            raise CoverageGateError("dashboard coverage report has an invalid test record")
        node_id = str(record["test_node_id"])
        dashboard_outcomes.setdefault(node_id, []).append(
            str(record.get("outcome", "unknown"))
        )
    if not dashboard_outcomes:
        raise CoverageGateError("dashboard coverage run emitted no test observations")
    duplicates = sorted(node for node, outcomes in dashboard_outcomes.items() if len(outcomes) != 1)
    if duplicates:
        raise CoverageGateError("duplicate dashboard test observations: " + ", ".join(duplicates))

    results: list[dict[str, object]] = []
    missing: list[str] = []
    for case in required_cases:
        case_name = str(case["case"])
        tests = case["tests"]
        if not isinstance(tests, list):
            raise CoverageGateError(f"required case {case_name} has invalid tests")
        observed: list[str] = []
        for node in tests:
            if not isinstance(node, str) or "::" not in node:
                raise CoverageGateError(f"required case {case_name} has invalid node ID")
            if node.startswith("apps/dashboard/"):
                outcomes = dashboard_outcomes.get(node, [])
                present = bool(outcomes) and all(outcome == "passed" for outcome in outcomes)
            else:
                outcomes = python_outcomes.get(node, [])
                present = bool(outcomes) and all(outcome == "passed" for outcome in outcomes)
            if present:
                observed.append(node)
            else:
                missing.append(f"{case_name}: {node}")
        results.append(
            {
                "case": case_name,
                "required_tests": tests,
                "observed_tests": observed,
                "status": "pass" if len(observed) == len(tests) else "fail",
            }
        )
    if missing:
        raise CoverageGateError(
            "required safety cases were not executed: " + "; ".join(missing)
        )
    return results


def run_coverage(
    policy: dict[str, Any],
    report_dir: Path,
    *,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, object]:
    python_policy = policy["python"]
    dashboard_policy = policy["dashboard"]
    _prepare_private_directory(report_dir)
    run_token = secrets.token_hex(16)
    coverage_data = report_dir / f".critical-coverage.{run_token}"
    python_json = report_dir / f".critical-coverage-python.{run_token}.json"
    python_tests_json = report_dir / f".critical-coverage-python-tests.{run_token}.json"
    lcov_path = report_dir / f".critical-coverage-dashboard.{run_token}.lcov"
    report_path = report_dir / "critical-coverage.json"
    _remove_artifacts(
        report_path,
        report_dir / "critical-coverage-error.json",
    )
    node_major = _node_major()
    minimum_node_major = dashboard_policy.get("minimum_node_major")
    if not isinstance(minimum_node_major, int) or isinstance(minimum_node_major, bool):
        raise CoverageGateError("dashboard minimum_node_major is invalid")
    if node_major < minimum_node_major:
        raise CoverageGateError(
            f"Node {node_major} is below required major {minimum_node_major}"
        )
    env = {
        **os.environ,
        "COVERAGE_FILE": str(coverage_data),
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
        "TEST_GOVERNANCE_COMPONENT": "root",
        "TEST_GOVERNANCE_REPORT": str(python_tests_json),
    }
    python_command = [
        "uv",
        "run",
        "--with",
        f"coverage=={python_policy['coverage_version']}",
        "coverage",
        "run",
        "--branch",
        f"--source={','.join(python_policy['sources'])}",
        "-m",
        "pytest",
        "-q",
        "-m",
        "not runtime_postgres and not host_coupled",
        "-p",
        "scripts.test_governance_pytest",
        *python_policy["pytest_paths"],
    ]
    python_test_exit = _run(
        python_command,
        cwd=ROOT,
        env=env,
        log_path=report_dir / "critical-coverage-python-tests.log",
    )
    python_json_exit = _run(
        [
            "uv",
            "run",
            "--with",
            f"coverage=={python_policy['coverage_version']}",
            "coverage",
            "json",
            "-o",
            str(python_json),
            "--pretty-print",
        ],
        cwd=ROOT,
        env=env,
        log_path=report_dir / "critical-coverage-python-json.log",
    )
    python_results = _evaluate_python(_read_json(python_json), python_policy["units"])

    dashboard = ROOT / "apps/dashboard"
    dashboard_env = {
        **os.environ,
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "TMPDIR": "/tmp",
        "TEMP": "/tmp",
        "TMP": "/tmp",
    }
    node_command = [
        "node",
        "--experimental-test-coverage",
        "--test-reporter=tap",
        "--test-reporter-destination=stdout",
        "--test-reporter=lcov",
        f"--test-reporter-destination={lcov_path}",
        *(f"--test-coverage-include={path}" for path in dashboard_policy["include_files"]),
        "--test",
        *dashboard_policy["test_files"],
    ]
    dashboard_exit = _run(
        node_command,
        cwd=dashboard,
        env=dashboard_env,
        log_path=report_dir / ".critical-coverage-dashboard-aggregate.log",
    )
    dashboard_result = _evaluate_dashboard(
        _parse_lcov(lcov_path), dashboard_policy["unit"]
    )
    dashboard_evidence_exit, dashboard_evidence_path = _run_dashboard_observations(
        dashboard,
        dashboard_policy["test_files"],
        report_dir,
        dashboard_env,
    )
    required_case_results = _evaluate_required_cases(
        policy["required_cases"],
        _read_json(python_tests_json),
        dashboard_evidence_path,
    )
    try:
        policy_display = str(policy_path.relative_to(ROOT))
    except ValueError:
        policy_display = str(policy_path)
    failed = {
        "python_tests": python_test_exit,
        "python_json": python_json_exit,
        "dashboard_tests": dashboard_exit,
        "dashboard_evidence": dashboard_evidence_exit,
    }
    failed = {name: code for name, code in failed.items() if code != 0}
    report = {
        "schema_version": 1,
        "status": "fail" if failed else "pass",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": policy_display,
        "node_major": node_major,
        "suite_exit_codes": {
            "python_tests": python_test_exit,
            "python_json": python_json_exit,
            "dashboard_tests": dashboard_exit,
            "dashboard_evidence": dashboard_evidence_exit,
        },
        "python": python_results,
        "dashboard": dashboard_result,
        "required_cases": required_case_results,
    }
    _write_json(report_path, report)
    _write_bytes(report_dir / "critical-coverage-python.json", python_json.read_bytes())
    _write_bytes(
        report_dir / "critical-coverage-python-tests.json", python_tests_json.read_bytes()
    )
    _write_bytes(report_dir / "critical-coverage-dashboard.lcov", lcov_path.read_bytes())
    _remove_artifacts(coverage_data, python_json, python_tests_json, lcov_path)
    if failed:
        raise CoverageGateError(f"critical coverage suites failed: {failed}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args(argv)
    policy_path = args.policy if args.policy.is_absolute() else ROOT / args.policy
    report_dir = args.report_dir if args.report_dir.is_absolute() else ROOT / args.report_dir
    report_path = report_dir / "critical-coverage.json"
    error_path = report_dir / "critical-coverage-error.json"
    try:
        _prepare_private_directory(report_dir)
        _remove_artifacts(*(report_dir / name for name in _GENERATED_ARTIFACTS))
        _remove_transient_artifacts(report_dir)
        policy = _read_json(policy_path)
        _validate_policy(policy)
        report = run_coverage(policy, report_dir, policy_path=policy_path)
        python_results = report["python"]
        dashboard_result = report["dashboard"]
        if not isinstance(python_results, list) or not isinstance(dashboard_result, dict):
            raise CoverageGateError("critical coverage report has invalid results")
        for result in [*python_results, dashboard_result]:
            print(
                f"{result['name']}: lines={result['lines']['percent']:.4f}% "
                f"branches={result['branches']['percent']:.4f}%"
            )
        print(f"machine report: {report_path}")
    except CoverageGateError as exc:
        try:
            if not report_dir.is_symlink():
                _write_json(
                    error_path,
                    {
                        "schema_version": 1,
                        "status": "error",
                        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "error": str(exc),
                    },
                )
        except CoverageGateError:
            pass
        print(f"CRITICAL_COVERAGE_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
