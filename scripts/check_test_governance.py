#!/usr/bin/env python3
"""Run test suites, govern every skipped/deselected node, and emit JSON evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = ROOT / "tests/skip-allowlist.yaml"
DEFAULT_REPORT_DIR = Path("/tmp/trading-agent-test-evidence/test-governance")
APPROVED_CATEGORIES = frozenset(
    {
        "APPROVAL_REQUIRED",
        "DISPOSABLE_POSTGRES_REQUIRED",
        "MISSING_HOST_BINARY",
        "MISSING_HOST_CAPABILITY",
        "EXTERNAL_INTEGRATION",
        "PROVIDER_CREDENTIAL_REQUIRED",
        "PLATFORM_SPECIFIC",
        "INTENTIONALLY_DEFERRED",
        "QUARANTINED_FLAKY",
        "UNKNOWN",
    }
)
APPROVAL_BLOCKED_CATEGORIES = frozenset(
    {"APPROVAL_REQUIRED", "DISPOSABLE_POSTGRES_REQUIRED"}
)
REQUIRED_FIELDS = frozenset(
    {
        "test_node_id",
        "component",
        "reason_category",
        "reason",
        "owner",
        "approval_record_type",
        "required_binary_or_service",
        "target_phase",
        "review_by",
        "security_critical",
        "allowed_in_ci",
    }
)
INVENTORY_OUTCOMES = frozenset({"skipped", "deselected"})


class GovernanceError(RuntimeError):
    """A fail-closed test-governance policy violation."""


def _entry_key(item: dict[str, object]) -> tuple[str, str]:
    return str(item["component"]), str(item["test_node_id"])


def _require_nonempty_string(entry: dict[str, object], field: str, index: int) -> None:
    value = entry[field]
    if not isinstance(value, str) or not value.strip():
        raise GovernanceError(f"allowlist entry {index} has invalid {field}")


def validate_allowlist_document(
    document: object,
    *,
    today: date | None = None,
) -> list[dict[str, object]]:
    """Validate the JSON-compatible YAML inventory without third-party parsers."""

    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise GovernanceError("allowlist schema_version must be 1")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise GovernanceError("allowlist entries must be a list")
    current_date = today or date.today()
    entries: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict) or set(raw) != REQUIRED_FIELDS:
            raise GovernanceError(f"allowlist entry {index} has invalid fields")
        entry = dict(raw)
        for field in (
            "test_node_id",
            "component",
            "reason_category",
            "owner",
            "approval_record_type",
            "required_binary_or_service",
            "target_phase",
            "review_by",
        ):
            _require_nonempty_string(entry, field, index)
        category = str(entry["reason_category"])
        if category not in APPROVED_CATEGORIES:
            raise GovernanceError(
                f"allowlist entry {index} has unapproved category {category}"
            )
        if category == "UNKNOWN":
            raise GovernanceError(f"allowlist entry {index} uses forbidden UNKNOWN category")
        try:
            review_by = date.fromisoformat(str(entry["review_by"]))
        except ValueError as exc:
            raise GovernanceError(
                f"allowlist entry {index} has invalid review_by"
            ) from exc
        if review_by < current_date:
            raise GovernanceError(
                f"allowlist entry {index} expired on {review_by.isoformat()}"
            )
        if not isinstance(entry["security_critical"], bool):
            raise GovernanceError(
                f"allowlist entry {index} has invalid security_critical"
            )
        if not isinstance(entry["allowed_in_ci"], bool):
            raise GovernanceError(f"allowlist entry {index} has invalid allowed_in_ci")
        if entry["security_critical"] and (
            str(entry["approval_record_type"]).strip().upper() == "NONE"
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
        ):
            raise GovernanceError(
                f"allowlist entry {index} is security-critical without explicit approval reason"
            )
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise GovernanceError(f"allowlist entry {index} has invalid reason")
        key = _entry_key(entry)
        if key in seen:
            raise GovernanceError(f"duplicate allowlist entry: {key[0]}::{key[1]}")
        seen.add(key)
        entries.append(entry)
    return entries


def compare_inventory(
    records: Sequence[dict[str, object]],
    allowlist_entries: Sequence[dict[str, object]],
) -> None:
    """Require exact equality between observed skips/deselections and approvals."""

    actual: dict[tuple[str, str], dict[str, object]] = {}
    duplicate_observations: list[tuple[str, str]] = []
    for item in records:
        if item.get("outcome") not in INVENTORY_OUTCOMES:
            continue
        key = _entry_key(item)
        if key in actual:
            duplicate_observations.append(key)
        actual[key] = item
    if duplicate_observations:
        formatted = ", ".join(
            f"{component}::{node}"
            for component, node in sorted(set(duplicate_observations))
        )
        raise GovernanceError(f"duplicate observed skip/deselection node IDs: {formatted}")
    approved = {_entry_key(item): item for item in allowlist_entries}
    new = sorted(set(actual) - set(approved))
    stale = sorted(set(approved) - set(actual))
    disallowed = sorted(
        key for key in set(actual) & set(approved) if not approved[key]["allowed_in_ci"]
    )
    changed_reasons = sorted(
        key
        for key in set(actual) & set(approved)
        if str(actual[key].get("reason", "")).strip()
        != str(approved[key].get("reason", "")).strip()
    )
    problems: list[str] = []
    if new:
        problems.append(
            "new unapproved skips/deselections: "
            + ", ".join(f"{component}::{node}" for component, node in new)
        )
    if stale:
        problems.append(
            "stale allowlist entries: "
            + ", ".join(f"{component}::{node}" for component, node in stale)
        )
    if disallowed:
        problems.append(
            "observed entries not allowed in CI: "
            + ", ".join(f"{component}::{node}" for component, node in disallowed)
        )
    if changed_reasons:
        problems.append(
            "observed skip/deselection reasons changed: "
            + ", ".join(f"{component}::{node}" for component, node in changed_reasons)
        )
    if problems:
        raise GovernanceError("; ".join(problems))


def build_governed_report(
    records: Sequence[dict[str, object]],
    allowlist_entries: Sequence[dict[str, object]],
) -> dict[str, object]:
    approvals = {_entry_key(item): item for item in allowlist_entries}
    summary = {
        "executed": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "deselected": 0,
        "approval_blocked": 0,
        "not_run": 0,
    }
    tests: list[dict[str, object]] = []
    for raw in sorted(records, key=_entry_key):
        item = dict(raw)
        outcome = str(item["outcome"])
        governed_outcome = outcome
        approval = approvals.get(_entry_key(item))
        if approval is not None:
            item["governance"] = dict(approval)
            if (
                outcome in INVENTORY_OUTCOMES
                and approval["reason_category"] in APPROVAL_BLOCKED_CATEGORIES
            ):
                governed_outcome = "approval_blocked"
                summary["approval_blocked"] += 1
        if outcome in {"passed", "failed"}:
            summary["executed"] += 1
        if outcome in summary:
            summary[outcome] += 1
        item["raw_outcome"] = outcome
        item["governed_outcome"] = governed_outcome
        tests.append(item)
    return {"schema_version": 1, "summary": summary, "tests": tests}


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"cannot read strict JSON document {path}: {exc}") from exc


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
            raise GovernanceError(f"cannot remove stale report {path}: {exc}") from exc


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


def _pytest_environment(component: str, report: Path) -> dict[str, str]:
    env = dict(os.environ)
    prior_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(ROOT) if not prior_pythonpath else os.pathsep.join((str(ROOT), prior_pythonpath))
    )
    env["TEST_GOVERNANCE_COMPONENT"] = component
    env["TEST_GOVERNANCE_REPORT"] = str(report)
    env["LIVE_EXECUTION_ENABLED"] = "false"
    env["LIVE_TRADING_APPROVED"] = "false"
    return env


def _parse_dashboard_tap(output: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    pattern = re.compile(r"^(not )?ok\s+\d+\s+-\s+(.+?)(?:\s+#\s+(SKIP|TODO)\b(.*))?$")
    for line in output.splitlines():
        match = pattern.match(line.strip())
        if match is None:
            continue
        failed, name, directive, detail = match.groups()
        if name.startswith("tests ") or name.startswith("pass "):
            continue
        outcome = "failed" if failed else "passed"
        reason = ""
        if directive == "SKIP":
            outcome = "skipped"
            reason = detail.strip() or "node:test skip directive"
        elif directive == "TODO":
            outcome = "skipped"
            reason = detail.strip() or "node:test todo directive"
        records.append(
            {
                "test_node_id": f"dashboard::{name}",
                "component": "dashboard",
                "outcome": outcome,
                "reason": reason,
                "phase": "call",
            }
        )
    return records


def _run_dashboard(report_dir: Path) -> tuple[int, Path]:
    dashboard = ROOT / "apps/dashboard"
    test_files = sorted((dashboard / "tests").glob("*.test.mjs"))
    command = [
        "node",
        "--test",
        "--test-reporter=tap",
        *(str(path.relative_to(dashboard)) for path in test_files),
    ]
    result = subprocess.run(
        command,
        cwd=dashboard,
        env={
            **os.environ,
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path = report_dir / "dashboard.log"
    log_path.write_text(result.stdout, encoding="utf-8")
    print(result.stdout, end="")
    records = _parse_dashboard_tap(result.stdout)
    integration = subprocess.run(
        ["bash", "tests/dashboard-security.integration.sh"],
        cwd=dashboard,
        env={
            **os.environ,
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(integration.stdout)
    print(integration.stdout, end="")
    records.append(
        {
            "test_node_id": "tests/dashboard-security.integration.sh::isolated security server",
            "component": "dashboard",
            "outcome": "passed" if integration.returncode == 0 else "failed",
            "reason": "" if integration.returncode == 0 else "integration script failed",
            "phase": "call",
        }
    )
    if not records:
        records.append(
            {
                "test_node_id": "dashboard::test reporter",
                "component": "dashboard",
                "outcome": "failed",
                "reason": "node:test TAP report contained no test records",
                "phase": "report",
            }
        )
    counts: dict[str, int] = {}
    for record in records:
        counts[str(record["outcome"])] = counts.get(str(record["outcome"]), 0) + 1
    report_path = report_dir / "dashboard-raw.json"
    _write_json(
        report_path,
        {
            "schema_version": 1,
            "component": "dashboard",
            "node_exit_status": result.returncode,
            "integration_exit_status": integration.returncode,
            "summary": counts,
            "tests": records,
        },
    )
    return max(result.returncode, integration.returncode), report_path


def run_suites(report_dir: Path) -> tuple[list[dict[str, object]], dict[str, int]]:
    report_dir.mkdir(parents=True, exist_ok=True)
    raw_paths = {
        "root": report_dir / "root-raw.json",
        "legacy": report_dir / "legacy-raw.json",
    }
    exit_codes: dict[str, int] = {}
    exit_codes["root"] = _run(
        [
            "uv",
            "run",
            "pytest",
            "-q",
            "-m",
            "not runtime_postgres and not host_coupled",
            "-p",
            "scripts.test_governance_pytest",
            "tests",
        ],
        cwd=ROOT,
        env=_pytest_environment("root", raw_paths["root"]),
        log_path=report_dir / "root.log",
    )
    legacy = ROOT / "legacy/research-backend"
    exit_codes["legacy"] = _run(
        [
            "uv",
            "run",
            "--frozen",
            "--extra",
            "test",
            "pytest",
            "-q",
            "-p",
            "scripts.test_governance_pytest",
        ],
        cwd=legacy,
        env=_pytest_environment("legacy", raw_paths["legacy"]),
        log_path=report_dir / "legacy.log",
    )
    exit_codes["dashboard"], dashboard_path = _run_dashboard(report_dir)
    paths = [*raw_paths.values(), dashboard_path]
    records: list[dict[str, object]] = []
    for path in paths:
        document = _read_json(path)
        if not isinstance(document, dict) or not isinstance(document.get("tests"), list):
            raise GovernanceError(f"invalid raw test report: {path}")
        records.extend(dict(item) for item in document["tests"])
    return records, exit_codes


def _bootstrap_classification(record: dict[str, object]) -> dict[str, object]:
    node_id = str(record["test_node_id"])
    component = str(record["component"])
    reason = str(record.get("reason", ""))
    lowered = f"{node_id} {reason}".lower()
    category = "UNKNOWN"
    required = "unclassified"
    approval = "NONE"
    target = "Governance triage"
    if "postgresql 16 test binaries are unavailable" in lowered:
        category = "MISSING_HOST_BINARY"
        required = "PostgreSQL 16 client and server test binaries"
        approval = "host-binary-review-v1"
        target = "Disposable PostgreSQL portability"
    elif "postgres" in lowered and any(
        token in lowered for token in ("authority", "disposable", "runtime_postgres")
    ):
        category = "DISPOSABLE_POSTGRES_REQUIRED"
        required = "approved disposable PostgreSQL cluster"
        approval = "disposable-postgres-test-approval-v1"
        target = "Package 6 paper runtime validation"
    elif "credential" in lowered or "alpaca" in lowered:
        category = "PROVIDER_CREDENTIAL_REQUIRED"
        required = "reviewed provider test credentials and isolated sandbox"
        approval = "provider-credential-review-v1"
        target = "External provider integration phase"
    elif "xattr" in lowered or "extended attribute" in lowered:
        category = "MISSING_HOST_CAPABILITY"
        required = "filesystem user extended attributes"
        approval = "host-capability-review-v1"
        target = "Host release proof"
    elif "host_coupled" in lowered or "wheelhouse" in lowered:
        category = "MISSING_HOST_CAPABILITY"
        required = "sealed host release wheelhouse and host capability"
        approval = "host-capability-review-v1"
        target = "Host release proof"
    elif "python 3.11" in lowered:
        category = "PLATFORM_SPECIFIC"
        required = "Python 3.11 interpreter"
        approval = "platform-support-review-v1"
        target = "Supported platform matrix"
    elif "implementation artifacts not present" in lowered:
        category = "INTENTIONALLY_DEFERRED"
        required = "reviewed PostgreSQL recovery implementation artifacts"
        approval = "deferred-test-review-v1"
        target = "PostgreSQL recovery implementation"
    owner = "quality-engineering"
    if component == "legacy":
        owner = "research-backend"
    elif "runtime_release" in node_id:
        owner = "release-engineering"
    elif "/jobs/" in node_id or node_id.startswith("tests/jobs"):
        owner = "job-plane"
    elif "/event_ledger/" in node_id or node_id.startswith("tests/event_ledger"):
        owner = "event-ledger"
    elif "/control_api/" in node_id or node_id.startswith("tests/control_api"):
        owner = "control-plane"
    elif "/production/" in node_id or node_id.startswith("tests/production"):
        owner = "production-safety"
    elif component == "dashboard":
        owner = "dashboard-security"
    security_critical = any(
        token in node_id
        for token in (
            "tests/jobs/",
            "tests/control_api/",
            "tests/event_ledger/",
            "tests/production/",
            "tests/security/",
            "dashboard::",
        )
    )
    governed_reason = reason or "Test is not executable in canonical portable CI."
    return {
        "test_node_id": node_id,
        "component": component,
        "reason_category": category,
        "reason": governed_reason,
        "owner": owner,
        "approval_record_type": approval,
        "required_binary_or_service": required,
        "target_phase": target,
        "review_by": "2026-10-31",
        "security_critical": security_critical,
        "allowed_in_ci": True,
    }


def bootstrap_allowlist(records: Iterable[dict[str, object]]) -> dict[str, object]:
    entries = [
        _bootstrap_classification(record)
        for record in records
        if record.get("outcome") in INVENTORY_OUTCOMES
    ]
    entries.sort(key=_entry_key)
    return {"schema_version": 1, "entries": entries}


def _load_allowlist(path: Path, current_date: date) -> list[dict[str, object]]:
    return validate_allowlist_document(_read_json(path), today=current_date)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--bootstrap-allowlist", type=Path)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)

    report_dir = args.report_dir if args.report_dir.is_absolute() else ROOT / args.report_dir
    output = report_dir / "test-governance.json"
    error_output = report_dir / "test-governance-error.json"
    records: list[dict[str, object]] = []
    exit_codes: dict[str, int] = {}
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        _remove_artifacts(
            output,
            error_output,
            report_dir / "root-raw.json",
            report_dir / "legacy-raw.json",
            report_dir / "dashboard-raw.json",
        )
        allowlist_path = args.allowlist if args.allowlist.is_absolute() else ROOT / args.allowlist
        bootstrap_path = args.bootstrap_allowlist
        if bootstrap_path is not None and not bootstrap_path.is_absolute():
            bootstrap_path = ROOT / bootstrap_path
        records, exit_codes = run_suites(report_dir)
        if args.bootstrap_allowlist is not None:
            candidate = bootstrap_allowlist(records)
            if bootstrap_path is None:
                raise GovernanceError("bootstrap path resolution failed")
            candidate_entries = candidate["entries"]
            if not isinstance(candidate_entries, list):
                raise GovernanceError("bootstrap produced invalid entries")
            unknown = [
                item
                for item in candidate_entries
                if item["reason_category"] == "UNKNOWN"
            ]
            if unknown:
                raise GovernanceError(
                    f"bootstrap produced {len(unknown)} UNKNOWN entries"
                )
            validate_allowlist_document(candidate, today=args.today)
            _write_json(bootstrap_path, candidate)
            print(f"wrote candidate allowlist: {bootstrap_path}")
        entries = _load_allowlist(allowlist_path, args.today)
        compare_inventory(records, entries)
        report = build_governed_report(records, entries)
        report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["suite_exit_codes"] = exit_codes
        try:
            report["allowlist"] = str(allowlist_path.relative_to(ROOT))
        except ValueError:
            report["allowlist"] = str(allowlist_path)
        failed_suites = {name: code for name, code in exit_codes.items() if code != 0}
        report["status"] = "fail" if failed_suites else "pass"
        _write_json(output, report)
        print(json.dumps(report["summary"], sort_keys=True))
        print(f"machine report: {output}")
        if failed_suites:
            raise GovernanceError(f"test suites failed: {failed_suites}")
    except GovernanceError as exc:
        _write_json(
            error_output,
            {
                "schema_version": 1,
                "status": "error",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "suite_exit_codes": exit_codes,
            },
        )
        print(f"TEST_GOVERNANCE_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
