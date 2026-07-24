#!/usr/bin/env python3
"""Measure critical Python and dashboard branch coverage and enforce ratchets."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "tests/critical-coverage-policy.json"
DEFAULT_REPORT_DIR = Path("/tmp/trading-agent-test-evidence/critical-coverage")


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


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _remove_artifacts(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CoverageGateError(f"cannot remove stale coverage artifact {path}: {exc}") from exc


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
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout, encoding="utf-8")
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
        line_covered = sum(int(summary["covered_lines"]) for summary in summaries)
        line_total = sum(int(summary["num_statements"]) for summary in summaries)
        branch_covered = sum(int(summary["covered_branches"]) for summary in summaries)
        branch_total = sum(int(summary["num_branches"]) for summary in summaries)
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


def _evaluate_required_cases(
    required_cases: list[dict[str, Any]],
    python_test_report: dict[str, Any],
    dashboard_log_path: Path,
) -> list[dict[str, object]]:
    records = python_test_report.get("tests")
    if not isinstance(records, list):
        raise CoverageGateError("Python coverage test report has no tests")
    python_outcomes = {
        str(record["test_node_id"]): str(record.get("outcome", "unknown"))
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("test_node_id"), str)
    }
    try:
        dashboard_log = dashboard_log_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoverageGateError(f"cannot read dashboard coverage test log: {exc}") from exc
    dashboard_outcomes: dict[str, list[str]] = {}
    for line in dashboard_log.splitlines():
        match = re.match(
            r"^\s*(not )?ok\s+\d+\s+-\s+(.+?)(?:\s+#\s+(SKIP|TODO)\b.*)?$",
            line,
        )
        if match is not None:
            failed, name, directive = match.groups()
            outcome = "failed" if failed else "passed"
            if directive is not None:
                outcome = directive.lower()
            dashboard_outcomes.setdefault(name.strip(), []).append(outcome)
    if not dashboard_outcomes:
        raise CoverageGateError("dashboard coverage run emitted no test observations")

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
                outcomes = dashboard_outcomes.get(node.split("::", 1)[1], [])
                present = bool(outcomes) and all(outcome == "passed" for outcome in outcomes)
            else:
                outcomes = [
                    outcome
                    for candidate, outcome in python_outcomes.items()
                    if candidate == node or candidate.startswith(node + "[")
                ]
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
    report_dir.mkdir(parents=True, exist_ok=True)
    coverage_data = report_dir / ".critical-coverage"
    python_json = report_dir / "critical-coverage-python.json"
    python_tests_json = report_dir / "critical-coverage-python-tests.json"
    lcov_path = report_dir / "critical-coverage-dashboard.lcov"
    report_path = report_dir / "critical-coverage.json"
    _remove_artifacts(
        coverage_data,
        python_json,
        python_tests_json,
        lcov_path,
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
        env={
            **os.environ,
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
        },
        log_path=report_dir / "critical-coverage-dashboard-tests.log",
    )
    dashboard_result = _evaluate_dashboard(
        _parse_lcov(lcov_path), dashboard_policy["unit"]
    )
    required_case_results = _evaluate_required_cases(
        policy["required_cases"],
        _read_json(python_tests_json),
        report_dir / "critical-coverage-dashboard-tests.log",
    )
    try:
        policy_display = str(policy_path.relative_to(ROOT))
    except ValueError:
        policy_display = str(policy_path)
    failed = {
        "python_tests": python_test_exit,
        "python_json": python_json_exit,
        "dashboard_tests": dashboard_exit,
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
        },
        "python": python_results,
        "dashboard": dashboard_result,
        "required_cases": required_case_results,
    }
    _write_json(report_path, report)
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
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "critical-coverage.json"
    error_path = report_dir / "critical-coverage-error.json"
    try:
        _remove_artifacts(report_path, error_path)
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
        _write_json(
            error_path,
            {
                "schema_version": 1,
                "status": "error",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            },
        )
        print(f"CRITICAL_COVERAGE_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
