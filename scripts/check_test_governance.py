#!/usr/bin/env python3
"""Run test suites, govern every skipped/deselected node, and emit JSON evidence."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from scripts import t_g03_capability_topology as capability_topology
from scripts.trusted_test_tmp import prepare_trusted_test_tmp
from scripts.validate_disposable_postgres_approval import (
    DisposablePostgresApprovalRejected,
    load_protected_approval_record,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = ROOT / "tests/skip-allowlist.yaml"
DEFAULT_REPORT_DIR = Path("/tmp/trading-agent-test-evidence/test-governance")
APPROVAL_RECORD_BY_CATEGORY = {
    "DISPOSABLE_POSTGRES_REQUIRED": "disposable-postgres-test-approval-v1",
    "MISSING_HOST_CAPABILITY": "host-capability-review-v1",
    "PROVIDER_CREDENTIAL_REQUIRED": "provider-credential-review-v1",
}
APPROVED_CATEGORIES = frozenset((*APPROVAL_RECORD_BY_CATEGORY, "UNKNOWN"))
OWNERS_BY_COMPONENT = {
    "root": frozenset({"control-plane", "event-ledger", "job-plane", "release-engineering"}),
    "legacy": frozenset({"research-backend"}),
    "dashboard": frozenset({"dashboard-security"}),
}
SECURITY_PATH_PREFIXES = (
    "tests/jobs/", "tests/control_api/", "tests/event_ledger/",
    "tests/production/", "tests/runtime_release/", "tests/security/",
    "apps/dashboard/tests/",
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
        "outcome",
    }
)
INVENTORY_OUTCOMES = frozenset({"skipped", "deselected"})
OBSERVED_OUTCOMES = frozenset({"passed", "failed", "skipped", "deselected", "not_run"})
OBSERVATION_PHASES = frozenset({"collection", "setup", "call", "teardown", "report"})
DEFAULT_POSTGRES_LANE_PLAN = ROOT / "tests/postgres-authority-lanes.json"
POSTGRES_LANE_SCOPES = frozenset({"DISPOSABLE_PG_GREEN", "DISPOSABLE_PG_RED"})
POSTGRES_LANE_INPUT_KEYS = frozenset({
    "TRADING_TEST_DISPOSABLE_APPROVAL_RECORD",
    "TRADING_TEST_DISPOSABLE_FIXTURE_PLAN",
    "TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR",
    "TRADING_TEST_POSTGRES_EVIDENCE_DIR",
})
POSTGRES_AUTHORITY_ENVIRONMENT = POSTGRES_LANE_INPUT_KEYS | {
    "TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES",
    "TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE",
    "TRADING_TEST_DISPOSABLE_FIXTURE_PLAN_REQUIRED",
}
POSTGRES_LANE_PAPER_ENVIRONMENT = {
    "TRADING_TEST_REQUESTED_MODE": "paper",
    "TRADING_TEST_EFFECTIVE_MODE": "paper",
    "LIVE_EXECUTION_ENABLED": "false",
    "LIVE_TRADING_APPROVED": "false",
    "LIVE_TRADING_ENABLED": "false",
    "TRADING_TEST_KILL_SWITCH": "INACTIVE",
}
POSTGRES_AUTHORITY_ABSENT_REASONS = frozenset({     # the harness's exact skips when a run carries no authority
    "explicit disposable PostgreSQL authority is not present",
    "exact disposable PostgreSQL authority is not present",
    "exact disposable PostgreSQL GREEN authority is not present",
    "exact disposable PostgreSQL RED authority is not present",
    "exact disposable PostgreSQL RED evidence authority is not present",
    "exact disposable PostgreSQL GREEN restore authority is absent",
})
ROOT_PYTEST_MARKERS = "not runtime_postgres and not host_coupled"
POSTGRES_LANES_ENV = "TEST_GOVERNANCE_POSTGRES_LANES"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class GovernanceError(RuntimeError):
    """A fail-closed test-governance policy violation."""


class AllowlistValidationError(GovernanceError):
    """A stable redacted class for topology diagnostics; message stays legacy-compatible."""

    def __init__(self, policy_class: str, message: str) -> None:
        self.policy_class = policy_class
        super().__init__(message)


def _entry_key(item: dict[str, object]) -> tuple[str, str]:
    return str(item["component"]), str(item["test_node_id"])


def _normalize_reason(reason: str) -> str:
    return " ".join(reason.split())


def _require_nonempty_string(entry: dict[str, object], field: str, index: int) -> None:
    value = entry[field]
    if not isinstance(value, str) or not value.strip():
        raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID", f"allowlist entry {index} has invalid {field}")


def validate_allowlist_document(
    document: object,
    *,
    today: date | None = None,
) -> list[dict[str, object]]:
    """Validate the JSON-compatible YAML inventory without third-party parsers."""

    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise AllowlistValidationError("POLICY_SCHEMA_INVALID", "allowlist schema_version must be 1")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise AllowlistValidationError("POLICY_SCHEMA_INVALID", "allowlist entries must be a list")
    current_date = today or date.today()
    entries: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict) or set(raw) != REQUIRED_FIELDS:
            raise AllowlistValidationError("POLICY_SCHEMA_INVALID", f"allowlist entry {index} has invalid fields")
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
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID",
                f"allowlist entry {index} has unapproved category {category}"
            )
        if category == "UNKNOWN":
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID", f"allowlist entry {index} uses forbidden UNKNOWN category")
        component = str(entry["component"])
        owner = str(entry["owner"])
        if component not in OWNERS_BY_COMPONENT:
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID", f"allowlist entry {index} has unapproved component")
        if owner not in OWNERS_BY_COMPONENT[component]:
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID", f"allowlist entry {index} has unapproved owner")
        try:
            review_by = date.fromisoformat(str(entry["review_by"]))
        except ValueError as exc:
            raise AllowlistValidationError("POLICY_REVIEW_DATE_INVALID",
                f"allowlist entry {index} has invalid review_by"
            ) from exc
        if review_by < current_date:
            raise AllowlistValidationError("POLICY_REVIEW_DATE_EXPIRED",
                f"allowlist entry {index} expired on {review_by.isoformat()}"
            )
        if not isinstance(entry["security_critical"], bool):
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID",
                f"allowlist entry {index} has invalid security_critical"
            )
        if not isinstance(entry["allowed_in_ci"], bool):
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID", f"allowlist entry {index} has invalid allowed_in_ci")
        if entry["outcome"] not in INVENTORY_OUTCOMES:
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID", f"allowlist entry {index} has invalid outcome")
        derived_security_critical = (
            category == "DISPOSABLE_POSTGRES_REQUIRED"
            or str(entry["test_node_id"]).startswith(SECURITY_PATH_PREFIXES)
        )
        if entry["security_critical"] is not derived_security_critical:
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID",
                f"allowlist entry {index} has invalid derived security criticality"
            )
        if derived_security_critical and (
            str(entry["approval_record_type"]).strip().upper() == "NONE"
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
        ):
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID",
                f"allowlist entry {index} is security-critical without explicit approval reason"
            )
        expected_approval = APPROVAL_RECORD_BY_CATEGORY.get(category)
        if entry["approval_record_type"] != expected_approval:
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID",
                f"allowlist entry {index} has invalid category approval record"
            )
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise AllowlistValidationError("POLICY_FIELD_TYPE_INVALID", f"allowlist entry {index} has invalid reason")
        if entry["reason"] != _normalize_reason(entry["reason"]):
            raise AllowlistValidationError("POLICY_REASON_NORMALIZATION_INVALID", f"allowlist entry {index} has non-normalized reason")
        key = _entry_key(entry)
        if key in seen:
            raise AllowlistValidationError("POLICY_DUPLICATE_ENTRY", f"duplicate allowlist entry: {key[0]}::{key[1]}")
        seen.add(key)
        entries.append(entry)
    return entries


def _validate_observation(item: object, index: int) -> dict[str, object]:
    required = {"test_node_id", "component", "outcome", "reason", "phase"}
    if not isinstance(item, dict) or not required.issubset(item):
        raise GovernanceError(f"invalid observed test record at index {index}")
    record = dict(item)
    component = record["component"]
    node_id = record["test_node_id"]
    outcome = record["outcome"]
    reason = record["reason"]
    phase = record["phase"]
    if component not in OWNERS_BY_COMPONENT:
        raise GovernanceError(f"invalid observed test record at index {index}: component")
    if not isinstance(node_id, str) or not node_id.strip() or node_id != node_id.strip():
        raise GovernanceError(f"invalid observed test record at index {index}: node ID")
    if not isinstance(outcome, str) or outcome not in OBSERVED_OUTCOMES:
        raise GovernanceError(f"invalid observed outcome at index {index}: {outcome!r}")
    if outcome == "failed":
        raise GovernanceError(f"invalid observed outcome at index {index}: failed")
    if not isinstance(reason, str) or reason != _normalize_reason(reason):
        raise GovernanceError(f"invalid observed test record at index {index}: reason")
    if not isinstance(phase, str) or phase not in OBSERVATION_PHASES:
        raise GovernanceError(f"invalid observed test record at index {index}: phase")
    return record


def compare_inventory(
    records: Sequence[dict[str, object]],
    allowlist_entries: Sequence[dict[str, object]],
) -> None:
    """Require exact equality between observed skips/deselections and approvals."""

    actual: dict[tuple[str, str], dict[str, object]] = {}
    all_seen: set[tuple[str, str]] = set()
    duplicate_observations: list[tuple[str, str]] = []
    not_run: list[tuple[str, str]] = []
    for index, raw in enumerate(records):
        item = _validate_observation(raw, index)
        key = _entry_key(item)
        if key in all_seen:
            duplicate_observations.append(key)
        all_seen.add(key)
        if item.get("outcome") == "not_run":
            not_run.append(key)
        if item.get("outcome") not in INVENTORY_OUTCOMES:
            continue
        actual[key] = item
    if duplicate_observations:
        formatted = ", ".join(
            f"{component}::{node}"
            for component, node in sorted(set(duplicate_observations))
        )
        raise GovernanceError(f"duplicate observed test node IDs: {formatted}")
    if not_run:
        formatted = ", ".join(
            f"{component}::{node}" for component, node in sorted(not_run)
        )
        raise GovernanceError(f"collected tests were not executed: {formatted}")
    approved = {_entry_key(item): item for item in allowlist_entries}
    new = sorted(set(actual) - set(approved))
    stale = sorted(set(approved) - set(actual))
    disallowed = sorted(
        key
        for key in set(actual) & set(approved)
        if type(approved[key].get("allowed_in_ci")) is not bool
        or approved[key].get("allowed_in_ci") is not True
    )
    changed_reasons = sorted(
        key
        for key in set(actual) & set(approved)
        if _normalize_reason(str(actual[key].get("reason", "")))
        != _normalize_reason(str(approved[key].get("reason", "")))
    )
    changed_outcomes = sorted(
        key
        for key in set(actual) & set(approved)
        if actual[key].get("outcome") != approved[key].get("outcome")
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
    if changed_outcomes:
        problems.append(
            "observed skip/deselection outcomes changed: "
            + ", ".join(f"{component}::{node}" for component, node in changed_outcomes)
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

    def _is_postgres_approval_blocked(item: dict[str, object]) -> bool:
        governance = item.get("governance")
        return (
            item.get("governed_outcome") == "approval_blocked"
            and isinstance(governance, dict)
            and governance.get("reason_category")
            == "DISPOSABLE_POSTGRES_REQUIRED"
        )

    postgres_approval_blocked = sum(
        1 for item in tests if _is_postgres_approval_blocked(item)
    )
    source_failed = summary["failed"] > 0 or summary["not_run"] > 0
    postgres_disclosure = {
        "approval_blocked_count": postgres_approval_blocked,
        "production_postgres_mutation": {
            "decision": "FORBIDDEN",
            "requires_separate_authority": True,
        },
        "runtime_proof": {
            "blocks_runtime_release": postgres_approval_blocked > 0,
            "decision": (
                "BLOCKED_PENDING_EXACT_COMMIT_AUTHORITY"
                if postgres_approval_blocked
                else "NOT_BLOCKED_BY_DISCLOSURE"
            ),
            "required_lifecycle_authorities": [
                "INITDB",
                "START",
                "RESTORE",
                "STOP",
                "DELETE",
            ],
        },
        "source_upgrade": {
            "blocks_source_upgrade": source_failed,
            "decision": (
                "FAIL_SOURCE_TESTS"
                if source_failed
                else "PASS_WITH_POSTGRES_RUNTIME_DEFERRED"
            ),
        },
    }
    return {
        "schema_version": 1,
        "summary": summary,
        "postgres_disclosure": postgres_disclosure,
        "tests": tests,
    }


def validate_postgres_lane_plan(document: object) -> dict[str, Any]:
    """Every authority node in exactly one lane whose scope and single RED SQL binding fit its operations."""
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "lanes", "nodes"}
        or document["schema_version"] != "postgres-authority-lanes/v1"
        or not isinstance(document["lanes"], list)
        or not isinstance(document["nodes"], list)
    ):
        raise GovernanceError("lane plan fields are not exact")
    lanes: dict[str, dict[str, Any]] = {}
    for lane in document["lanes"]:
        if (
            not isinstance(lane, dict)
            or set(lane) != {"lane_id", "scope", "red_sql_operation", "red_sql_path"}
            or not isinstance(lane["lane_id"], str)
            or lane["scope"] not in POSTGRES_LANE_SCOPES
            or (lane["red_sql_operation"] is None) != (lane["red_sql_path"] is None)
        ):
            raise GovernanceError("lane plan fields are not exact")
        if lane["lane_id"] in lanes:
            raise GovernanceError("lane ids must be unique")
        if lane["scope"] == "DISPOSABLE_PG_GREEN" and lane["red_sql_operation"] is not None:
            raise GovernanceError(f"GREEN lane {lane['lane_id']} cannot carry a RED SQL binding")
        lanes[lane["lane_id"]] = lane
    bindings: dict[str, set[tuple[str, str]]] = {lane_id: set() for lane_id in lanes}
    assigned: set[str] = set()
    for node in document["nodes"]:
        if (
            not isinstance(node, dict)
            or set(node) != {"test_node_id", "lane_id", "operations"}
            or not isinstance(node["test_node_id"], str)
            or not isinstance(node["operations"], list)
            or not node["operations"]
        ):
            raise GovernanceError("lane plan fields are not exact")
        node_id = node["test_node_id"]
        if node_id in assigned:
            raise GovernanceError(f"{node_id} is assigned more than once")
        assigned.add(node_id)
        lane = lanes.get(node["lane_id"])
        if lane is None:
            raise GovernanceError(f"{node_id} names unknown lane {node['lane_id']}")
        for operation in node["operations"]:
            if (
                not isinstance(operation, dict)
                or set(operation) != {"operation_id", "scope", "red_sql_path"}
                or not isinstance(operation["operation_id"], str)
                or operation["scope"] not in POSTGRES_LANE_SCOPES | {"GREEN_OR_RED"}
                or (operation["red_sql_path"] is not None and operation["scope"] != "DISPOSABLE_PG_RED")
            ):
                raise GovernanceError("lane plan fields are not exact")
            if operation["scope"] not in {lane["scope"], "GREEN_OR_RED"}:
                raise GovernanceError(
                    f"{node_id} requires {operation['scope']} but lane {lane['lane_id']} is {lane['scope']}"
                )
            if operation["red_sql_path"] is not None:
                bindings[lane["lane_id"]].add((operation["operation_id"], operation["red_sql_path"]))
    for lane_id, lane in lanes.items():
        declared = set() if lane["red_sql_operation"] is None else {(lane["red_sql_operation"], lane["red_sql_path"])}
        if bindings[lane_id] != declared:
            needed = ", ".join(sorted(operation for operation, _ in bindings[lane_id])) or "none"
            raise GovernanceError(
                f"lane {lane_id} needs RED SQL bindings for {needed} but declares {lane['red_sql_operation']}"
            )
    return document


_LABEL = "{0}::{1}".format
_BASE_FIELDS = frozenset({"test_node_id", "component", "outcome", "reason", "phase"})


def _postgres_lane_problems(
    lane: dict[str, Any],
    result: dict[str, Any],
    assigned: set[tuple[str, str]],
    source: dict[str, str],
) -> tuple[list[str], list[dict[str, object]]]:
    """Every problem one lane shows, plus its PASS records; the runner stops on any problem."""
    lane_id = lane["lane_id"]
    if "not_started" in result:
        return [f"lane {lane_id} was not started: {result['not_started']}"], []
    problems: list[str] = []
    exit_code = result.get("exit_code")
    if exit_code != 0:
        problems.append(f"lane {lane_id} exited {exit_code}")
    if result.get("source_before") != source or result.get("source_after") != source:
        problems.append(f"lane {lane_id} did not run on the candidate source")
    if result.get("approval_scope") != lane["scope"]:
        problems.append(f"lane {lane_id} approval scope {result.get('approval_scope')} differs from {lane['scope']}")
    if result.get("approval_red_sql_operation") != lane["red_sql_operation"]:
        problems.append(
            f"lane {lane_id} approval binds {result.get('approval_red_sql_operation')}, "
            f"not {lane['red_sql_operation']}"
        )
    if result.get("new_cluster_roots"):
        problems.append(f"lane {lane_id} left disposable cluster roots: {', '.join(result['new_cluster_roots'])}")
    report = result.get("report")
    if not isinstance(report, dict) or not isinstance(report.get("tests"), list):
        problems.append(f"lane {lane_id} has no raw report")
        return problems, []
    if report.get("component") != "root" or report.get("collection_only") is not False:
        problems.append(f"lane {lane_id} raw report is not a root execution report")
    if report.get("pytest_exit_status") != exit_code:
        problems.append(
            f"lane {lane_id} report exit status {report.get('pytest_exit_status')} "
            f"differs from process exit {exit_code}"
        )
    passed: list[dict[str, object]] = []
    observed: set[tuple[str, str]] = set()
    for index, item in enumerate(report["tests"]):
        if not isinstance(item, dict) or not {"test_node_id", "component", "outcome", "phase"} <= set(item):
            problems.append(f"lane {lane_id} raw record {index} is invalid")
            continue
        record = dict(item)
        key = _entry_key(record)
        if key in observed:
            problems.append(f"lane {lane_id} observed {_LABEL(*key)} more than once")
            continue
        observed.add(key)
        if key not in assigned:
            problems.append(f"lane {lane_id} observed unassigned {_LABEL(*key)}")
        elif record["outcome"] != "passed" or record["phase"] != "call":
            problems.append(f"lane {lane_id} {_LABEL(*key)} is {record['outcome']} in {record['phase']}")
        else:
            passed.append({**record, "postgres_lane": lane_id})
    problems.extend(f"lane {lane_id} did not observe {_LABEL(*key)}" for key in sorted(assigned - observed))
    return problems, passed


def _assigned_nodes(plan: dict[str, Any], lane_id: str) -> set[tuple[str, str]]:
    return {("root", node["test_node_id"]) for node in plan["nodes"] if node["lane_id"] == lane_id}


def merge_postgres_lanes(
    base_records: Sequence[dict[str, object]],
    plan: dict[str, Any],
    lane_results: dict[str, dict[str, Any]],
    *,
    source: dict[str, str],
) -> list[dict[str, object]]:
    """Replace each planned authority-absent skip with a PASS from its own lane; report every gap together."""
    problems: list[str] = []
    planned = {("root", node["test_node_id"]) for node in plan["nodes"]}
    merged: list[dict[str, object]] = []
    in_base: set[tuple[str, str]] = set()
    for index, raw in enumerate(base_records):
        if not isinstance(raw, dict) or not _BASE_FIELDS <= set(raw):
            problems.append(f"base run record {index} is invalid")
            continue
        key = _entry_key(raw)
        if key not in planned:
            merged.append(dict(raw))
            continue
        if key in in_base:
            problems.append(f"base run observed {_LABEL(*key)} more than once")
            continue
        in_base.add(key)
        if (
            raw["outcome"] != "skipped"
            or raw["reason"] not in POSTGRES_AUTHORITY_ABSENT_REASONS
            or raw["phase"] not in {"setup", "call"}
        ):
            problems.append(
                f"base run observed {_LABEL(*key)} as {raw['outcome']} in {raw['phase']}, "
                "not the authority-absent skip"
            )
    problems.extend(f"base run did not observe {_LABEL(*key)}" for key in sorted(planned - in_base))
    problems.extend(
        f"result for unplanned lane {lane_id}"
        for lane_id in sorted(set(lane_results) - {lane["lane_id"] for lane in plan["lanes"]})
    )
    for lane in plan["lanes"]:
        result = lane_results.get(lane["lane_id"])
        if result is None:
            problems.append(f"lane {lane['lane_id']} has no result")
            continue
        lane_problems, passed = _postgres_lane_problems(lane, result, _assigned_nodes(plan, lane["lane_id"]), source)
        problems.extend(lane_problems)
        merged.extend(passed)
    if problems:
        raise GovernanceError("postgres authority lanes failed: " + "; ".join(problems))
    return merged


def build_final_governed_summary(
    report: dict[str, object],
) -> tuple[bytes, list[dict[str, str]]]:
    """Validate and project the governed report used by the final evidence set."""
    required = {
        "schema_version", "summary", "postgres_disclosure", "tests",
        "capability_topology", "generated_at_utc", "suite_exit_codes",
        "allowlist", "status",
    }
    if set(report) != required or report.get("schema_version") != 1:
        raise GovernanceError("final governed report schema is invalid")
    if report.get("status") != "pass":
        raise GovernanceError("final governed report is not PASS")
    exit_codes = report.get("suite_exit_codes")
    tests = report.get("tests")
    if (
        not isinstance(exit_codes, dict)
        or any(not isinstance(key, str) or not isinstance(value, int) or value != 0 for key, value in exit_codes.items())
        or not isinstance(tests, list)
        or not isinstance(report.get("summary"), dict)
        or not isinstance(report.get("postgres_disclosure"), dict)
        or not isinstance(report.get("capability_topology"), dict)
        or not isinstance(report.get("generated_at_utc"), str)
        or not isinstance(report.get("allowlist"), str)
    ):
        raise GovernanceError("final governed report fields are invalid")
    semantic: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in tests:
        if not isinstance(item, dict):
            raise GovernanceError("final governed test record is invalid")
        node_id = item.get("test_node_id")
        component = item.get("component")
        phase = item.get("phase")
        outcome = item.get("governed_outcome", item.get("raw_outcome", item.get("outcome")))
        if (
            not isinstance(node_id, str) or not node_id
            or not isinstance(component, str) or not component
            or not isinstance(phase, str) or phase not in OBSERVATION_PHASES
            or not isinstance(outcome, str) or outcome not in {
                *OBSERVED_OUTCOMES, "approval_blocked",
            }
            or (component, node_id) in seen
        ):
            raise GovernanceError("final governed test meaning is invalid")
        seen.add((component, node_id))
        semantic.append({
            "component": component,
            "node_id": node_id,
            "outcome": outcome,
            "phase": phase,
        })
    semantic.sort(key=lambda item: (item["component"], item["node_id"]))
    summary = {
        "schema_version": "test-governance-final-summary/v1",
        "status": report["status"],
        "summary": report["summary"],
        "postgres_disclosure": report["postgres_disclosure"],
        "capability_topology": report["capability_topology"],
        "tests": tests,
        "suite_exit_codes": exit_codes,
        "allowlist": report["allowlist"],
        "generated_at_utc": report["generated_at_utc"],
    }
    return json.dumps(
        summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"), semantic


def build_final_governed_error(
    error_report: dict[str, object],
) -> tuple[bytes, dict[str, object]]:
    """Project an error without copying its potentially sensitive raw message."""
    required = {
        "schema_version", "status", "generated_at_utc", "error",
        "suite_exit_codes",
    }
    received = set(error_report)
    if (
        received not in (required, required | {"date_context_sources"})
        or error_report.get("schema_version") != 1
        or error_report.get("status") != "error"
        or not isinstance(error_report.get("generated_at_utc"), str)
        or not isinstance(error_report.get("error"), str)
        or not isinstance(error_report.get("suite_exit_codes"), dict)
    ):
        raise GovernanceError("final governed error report is invalid")
    exit_codes = error_report["suite_exit_codes"]
    assert isinstance(exit_codes, dict)
    if any(
        not isinstance(key, str) or not isinstance(value, int)
        for key, value in exit_codes.items()
    ):
        raise GovernanceError("final governed error exit codes are invalid")
    message = str(error_report["error"])
    if message.startswith("policy validation failed: "):
        candidate = message.removeprefix("policy validation failed: ")
        error_code = candidate if re.fullmatch(r"[A-Z0-9_]+", candidate) else "POLICY_VALIDATION_FAILURE"
    elif message.startswith("test suites failed:"):
        error_code = "SUITE_FAILURE"
    else:
        error_code = "GOVERNANCE_FAILURE"
    semantic: dict[str, object] = {
        "error_code": error_code,
        "suite_exit_codes": dict(exit_codes),
    }
    document = {
        "schema_version": "test-governance-final-error/v1",
        "status": "error",
        "generated_at_utc": error_report["generated_at_utc"],
        **semantic,
    }
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"), semantic


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"cannot read strict JSON document {path}: {exc}") from exc


def _prepare_private_directory(path: Path) -> None:
    try:
        absolute = path.absolute()
        lineage = [absolute, *absolute.parents]
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
                and ancestor == absolute
            ):
                os.chmod(ancestor, 0o700)
                metadata = ancestor.lstat()
            writable = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            if writable and not trusted_sticky:
                raise OSError
        os.chmod(path, 0o700)
    except OSError as exc:
        raise GovernanceError("report directory is not a private owned directory") from exc


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
        raise GovernanceError(f"cannot write report artifact {path.name}") from exc
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
    _write_bytes(log_path, result.stdout.encode("utf-8"))
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


def _parse_dashboard_tap(output: str, test_file: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    pattern = re.compile(r"^(not )?ok\s+\d+\s+-\s+(.+?)(?:\s+#\s+(SKIP|TODO)\b(.*))?$")
    parents: dict[int, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.lstrip()
        indent = len(raw_line) - len(line)
        subtest = re.match(r"^# Subtest:\s+(.+)$", line)
        if subtest is not None:
            parents[indent] = subtest.group(1).strip()
            for level in tuple(parents):
                if level > indent:
                    del parents[level]
            continue
        match = pattern.match(line)
        if match is None:
            continue
        failed, name, directive, detail = match.groups()
        if name.startswith("tests ") or name.startswith("pass "):
            continue
        outcome = "failed" if failed else "passed"
        reason = ""
        if directive == "SKIP":
            outcome = "skipped"
            reason = _normalize_reason(detail) or "node:test skip directive"
        elif directive == "TODO":
            outcome = "skipped"
            reason = _normalize_reason(detail) or "node:test todo directive"
        hierarchy = [parents[level] for level in sorted(parents) if level < indent]
        full_name = "::".join([*hierarchy, name])
        records.append(
            {
                "test_node_id": f"apps/dashboard/{test_file}::{full_name}",
                "component": "dashboard",
                "outcome": outcome,
                "reason": reason,
                "phase": "call",
            }
        )
    return records


def _load_dashboard_inventory(
    dashboard: Path,
    dashboard_env: dict[str, str],
) -> tuple[list[Path], list[Path]]:
    result = subprocess.run(
        ["node", "tests/run-test-inventory.mjs", "--list-json"],
        cwd=dashboard,
        env=dashboard_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise GovernanceError(
            "dashboard canonical test inventory failed: " + result.stdout.strip()
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GovernanceError("dashboard canonical test inventory returned invalid JSON") from exc
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "node_tests", "integration_tests"
    } or document.get("schema_version") != 1:
        raise GovernanceError("dashboard canonical test inventory has invalid schema")

    def paths(field: str, suffix: str) -> list[Path]:
        values = document.get(field)
        if (
            not isinstance(values, list)
            or not values
            or values != sorted(values)
            or len(values) != len(set(values))
            or any(
                not isinstance(value, str)
                or not value.startswith("tests/")
                or not value.endswith(suffix)
                or Path(value).is_absolute()
                or ".." in Path(value).parts
                for value in values
            )
        ):
            raise GovernanceError(f"dashboard canonical test inventory has invalid {field}")
        resolved = [dashboard / value for value in values]
        if any(not path.is_file() or path.is_symlink() for path in resolved):
            raise GovernanceError(f"dashboard canonical test inventory has unsafe {field}")
        return resolved

    return paths("node_tests", ".test.mjs"), paths(
        "integration_tests", ".integration.sh"
    )


def _failure_status(*codes: int) -> int:
    """0 only when every child exited 0; a signal (negative) status is a failure, never 0 (QG01)."""
    return max((code if code > 0 else 1 for code in codes if code != 0), default=0)


def _run_dashboard(report_dir: Path) -> tuple[int, Path]:
    dashboard = ROOT / "apps/dashboard"
    dashboard_env = {
        **os.environ,
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
    }
    test_files, integration_files = _load_dashboard_inventory(
        dashboard, dashboard_env
    )
    log_path = report_dir / "dashboard.log"
    records: list[dict[str, object]] = []
    outputs: list[str] = []
    node_exit_status = 0
    node_exit_statuses: dict[str, int] = {}
    for path in test_files:
        test_file = str(path.relative_to(dashboard))
        result = subprocess.run(
            ["node", "--test", "--test-reporter=tap", test_file],
            cwd=dashboard,
            env=dashboard_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        outputs.append(f"# file: {test_file}\n{result.stdout}")
        file_records = _parse_dashboard_tap(result.stdout, test_file)
        if not file_records:
            file_records.append(
                {
                    "test_node_id": (
                        f"apps/dashboard/{test_file}::static-test-inventory"
                    ),
                    "component": "dashboard",
                    "outcome": "failed",
                    "reason": "node:test TAP report contained no test records for file",
                    "phase": "report",
                }
            )
            node_exit_status = max(node_exit_status, 1)
        records.extend(file_records)
        node_exit_statuses[test_file] = result.returncode
        node_exit_status = _failure_status(node_exit_status, result.returncode)
    if not test_files and not integration_files:
        records.append(
            {
                "test_node_id": "apps/dashboard/tests::static-test-inventory",
                "component": "dashboard",
                "outcome": "failed",
                "reason": "dashboard test inventory contained no test files",
                "phase": "report",
            }
        )
        node_exit_status = max(node_exit_status, 1)
    print("".join(outputs), end="")
    integration_exit_statuses: dict[str, int] = {}
    for path in integration_files:
        test_file = str(path.relative_to(dashboard))
        integration = subprocess.run(
            ["bash", test_file],
            cwd=dashboard,
            env=dashboard_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        outputs.append(f"# file: {test_file}\n{integration.stdout}")
        print(integration.stdout, end="")
        integration_exit_statuses[test_file] = integration.returncode
        records.append(
            {
                "test_node_id": (
                    f"apps/dashboard/{test_file}::isolated integration script"
                ),
                "component": "dashboard",
                "outcome": "passed" if integration.returncode == 0 else "failed",
                "reason": "" if integration.returncode == 0 else "integration script failed",
                "phase": "call",
            }
        )
    _write_bytes(log_path, "".join(outputs).encode("utf-8"))
    integration_exit_status = _failure_status(*integration_exit_statuses.values())

    counts: dict[str, int] = {}
    for record in records:
        counts[str(record["outcome"])] = counts.get(str(record["outcome"]), 0) + 1
    report_path = report_dir / "dashboard-raw.json"
    _write_json(
        report_path,
        {
            "schema_version": 1,
            "component": "dashboard",
            "node_exit_status": node_exit_status,
            "node_exit_statuses": node_exit_statuses,
            "integration_exit_status": integration_exit_status,
            "integration_exit_statuses": integration_exit_statuses,
            "summary": counts,
            "tests": records,
        },
    )
    return max(node_exit_status, integration_exit_status), report_path


def load_postgres_lane_inputs(path: Path, plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    """External per-lane authority inputs: exactly the planned lanes, only harness input keys, absolute paths."""
    if path.resolve().is_relative_to(ROOT.resolve()):
        raise GovernanceError("lane inputs must remain outside the source checkout")
    document = _read_json(path)
    if not isinstance(document, dict) or set(document) != {lane["lane_id"] for lane in plan["lanes"]}:
        raise GovernanceError("lane inputs must name exactly the planned lanes")
    for lane_id, inputs in document.items():
        if (
            not isinstance(inputs, dict)
            or not set(inputs) <= POSTGRES_LANE_INPUT_KEYS
            or not {"TRADING_TEST_DISPOSABLE_APPROVAL_RECORD", "TRADING_TEST_DISPOSABLE_FIXTURE_PLAN"} <= set(inputs)
            or not all(
                isinstance(value, str)
                and Path(value).is_absolute()
                and not Path(value).resolve().is_relative_to(ROOT.resolve())
                for value in inputs.values()
            )
        ):
            raise GovernanceError(f"lane {lane_id} inputs are not exact")
    return document


def resolve_postgres_lanes(cli: Path | None) -> Path | None:
    """The lane inputs from --postgres-lanes or TEST_GOVERNANCE_POSTGRES_LANES (the Makefile stays byte-identical).

    Neither: no lanes (unchanged behavior). Both: they must name the same path. An empty variable is an error. The
    variable is removed from this process's environment either way, so no suite, lane or nested governance run that
    copies os.environ can pick lanes up by accident.
    """
    raw = os.environ.pop(POSTGRES_LANES_ENV, None)
    if raw is None:
        return cli
    if not raw:
        raise GovernanceError(f"{POSTGRES_LANES_ENV} is set but empty")
    if cli is not None and cli != Path(raw):
        raise GovernanceError(f"--postgres-lanes and {POSTGRES_LANES_ENV} differ")
    return Path(raw)


def _require_authority_free_base() -> None:
    if POSTGRES_AUTHORITY_ENVIRONMENT & set(os.environ):
        raise GovernanceError("base run must not inherit disposable PostgreSQL authority")


def _postgres_lane_environment(lane: dict[str, Any], inputs: dict[str, str], report: Path,
                               private: Path) -> dict[str, str]:
    env = _pytest_environment("root", report)
    for name in tuple(env):
        if name.startswith(("TRADING_DATABASE_", "TRADING_DB_", "PG")) or name in (
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "USERPROFILE",
        ):
            env.pop(name)
    for name in ("home", "uv-cache", "tmp"):
        (private / name).mkdir(mode=0o700)
    env.update(HOME=str(private / "home"), UV_CACHE_DIR=str(private / "uv-cache"),
               TRADING_TEST_TMP_ROOT=str(private / "tmp"))
    for name in POSTGRES_AUTHORITY_ENVIRONMENT:
        env.pop(name, None)
    env.update(inputs)
    env.update(POSTGRES_LANE_PAPER_ENVIRONMENT)
    env["TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES"] = "YES"
    env["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"] = lane["scope"]
    env["TRADING_TEST_DISPOSABLE_FIXTURE_PLAN_REQUIRED"] = "YES"      # every session takes an approved plan slot
    return env


def _candidate_source() -> dict[str, str]:
    def git(*args: str) -> bytes:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout

    return {
        "commit_sha": git("rev-parse", "HEAD").decode("ascii").strip(),
        "tree_sha": git("rev-parse", "HEAD^{tree}").decode("ascii").strip(),
        "status_sha256": hashlib.sha256(git("status", "--porcelain=v1", "-z", "--untracked-files=all")).hexdigest(),
    }


def _disposable_cluster_roots() -> set[str]:
    return set(glob.glob("/tmp/phase4-postgres-*"))


def _approval_binding(record_path: str) -> tuple[object, object]:
    try:
        record = load_protected_approval_record(Path(record_path))
    except DisposablePostgresApprovalRejected:
        return None, None
    binding = record.get("red_sql_binding")
    return record.get("scope"), binding.get("operation_id") if isinstance(binding, dict) else None


def run_postgres_lanes(
    report_dir: Path,
    plan: dict[str, Any],
    lane_inputs: dict[str, dict[str, str]],
    *,
    source: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Run each lane on exactly its planned nodes; keep every raw report, log and exit status.

    A lane that is not clean (exit, signal, report, source, record or leftover root) stops the run: no further
    PostgreSQL lane is launched to collect more errors, and the remaining lanes are recorded as not started.
    """
    _prepare_private_directory(report_dir)
    results: dict[str, dict[str, Any]] = {}
    exit_codes: dict[str, int] = {}
    for lane in plan["lanes"]:
        lane_id = lane["lane_id"]
        inputs = lane_inputs[lane_id]
        raw = report_dir / f"root-postgres-{lane_id}-raw.json"
        _remove_artifacts(raw)
        nodes = [node["test_node_id"] for node in plan["nodes"] if node["lane_id"] == lane_id]
        scope, red_operation = _approval_binding(inputs["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"])
        roots_before = _disposable_cluster_roots()
        source_before = _candidate_source()
        private = prepare_trusted_test_tmp(f"postgres-lane-{lane_id}")
        try:
            exit_code = _run(
                ["uv", "run", "--frozen", "--offline", "pytest", "-q", "-m", ROOT_PYTEST_MARKERS,
                 "-p", "scripts.test_governance_pytest", *nodes],
                cwd=ROOT,
                env=_postgres_lane_environment(lane, inputs, raw, private.path),
                log_path=report_dir / f"root-postgres-{lane_id}.log",
            )
        finally:
            private.cleanup()
        exit_codes[f"root-postgres-{lane_id}"] = exit_code
        try:
            report = _read_json(raw)
        except GovernanceError:
            report = None
        results[lane_id] = {
            "exit_code": exit_code,
            "report": report,
            "source_before": source_before,
            "source_after": _candidate_source(),
            "approval_scope": scope,
            "approval_red_sql_operation": red_operation,
            "new_cluster_roots": sorted(_disposable_cluster_roots() - roots_before),
        }
        problems, _passed = _postgres_lane_problems(lane, results[lane_id], _assigned_nodes(plan, lane_id), source)
        if problems:
            for remaining in plan["lanes"][len(results):]:
                results[remaining["lane_id"]] = {"not_started": f"lane {lane_id} stopped the run"}
            break
    return results, exit_codes


def run_suites(report_dir: Path) -> tuple[list[dict[str, object]], dict[str, int]]:
    _prepare_private_directory(report_dir)
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


def audit_topology_root_records(
    *,
    evidence_root: Path,
    inventory: Path,
    foundation_run_id: str,
    foundation_head_sha: str,
    foundation_context_path: Path | None = None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Bind every root topology receipt to its exact no-clobber observation."""
    try:
        topology_root = evidence_root / "capability-topology"
        if capability_topology._unsafe_raw_reason_nonacceptance_instances(topology_root):
            raise GovernanceError("unsafe raw reason nonacceptance is present; topology aggregation is forbidden")
        if os.path.lexists(topology_root / "policy-validation-nonacceptance.json"):
            raise GovernanceError("policy validation nonacceptance is present; topology aggregation is forbidden")
        if os.path.lexists(topology_root / "portable-root-remainder.failure-diagnostic.json"):
            raise GovernanceError("failure diagnostic is present; topology aggregation is forbidden")
        context_path = (
            foundation_context_path
            if foundation_context_path is not None
            else evidence_root / "capability-topology/foundation-context.json"
        )
        context = capability_topology._validated_foundation_context(
            context_path,
            run_id=foundation_run_id,
            head_sha=foundation_head_sha,
        )
        capability_topology._require_topology_reservation(
            evidence_root, foundation_run_id, foundation_head_sha, context,
        )
        rows, closure = capability_topology._installed_governance_state(
            inventory, evidence_root, head_sha=foundation_head_sha,
        )
        baseline = capability_topology.load_portable_root_baseline(
            inventory=inventory,
            evidence_root=evidence_root,
            run_id=foundation_run_id,
            head_sha=foundation_head_sha,
            foundation_context_path=context_path,
        )
        sealed_custody = capability_topology._validate_custody_policy(
            baseline["collector_policy"],
        )
        collection_deselections = capability_topology._validate_collection_record(
            topology_root / "portable-root-collection.governance.json",
            tuple(baseline["candidate_node_ids"]),
        )
        _, remainder = capability_topology._load_portable_root_remainder(
            inventory=inventory,
            evidence_root=evidence_root,
            run_id=foundation_run_id,
            head_sha=foundation_head_sha,
            foundation_context_path=context_path,
        )
        remainder_records = capability_topology._validate_exact_governance_record(
            topology_root / "portable-root-remainder.governance.json", remainder, sealed_custody,
        )
        receipts = [
            topology_root / f"{code}.json"
            for code in sorted(capability_topology.CODE_CLASSIFICATION)
        ]
        disclosure = capability_topology.aggregate_receipts(
            receipts,
            rows=rows,
            foundation_run_id=foundation_run_id,
            foundation_head_sha=foundation_head_sha,
            foundation_context=context,
            closure_proof_path=topology_root / "portable-defect-closure-proof.json",
            sealed_custody=sealed_custody,
        )
        root_records: list[dict[str, object]] = [
            {
                "test_node_id": node,
                "component": "root",
                "outcome": "passed",
                "reason": "",
                "phase": "call",
            }
            for node in remainder_records
        ]
        root_records.extend(collection_deselections)
        closure_proof = capability_topology.validate_portable_closure_proof(
            topology_root / "portable-defect-closure-proof.json",
            foundation_run_id=foundation_run_id,
            foundation_head_sha=foundation_head_sha,
            foundation_context=context,
            sealed_custody=sealed_custody,
        )
        closure_nodes = tuple(closure_proof["closure_node_ids"])
        root_records.extend({
            "test_node_id": node,
            "component": "root",
            "outcome": "passed",
            "reason": "",
            "phase": "call",
        } for node in closure_nodes)
        accounted = [*remainder_records, *closure_nodes]
        for receipt_path, expected_code in capability_topology._canonical_receipt_artifacts(
            receipts,
        ):
            receipt, artifact_executed = capability_topology._validate_bound_receipt_artifact(
                receipt_path, expected_code, rows=rows,
                foundation_run_id=foundation_run_id,
                foundation_head_sha=foundation_head_sha,
                foundation_context=context, sealed_custody=sealed_custody,
            )
            code = str(receipt["capability_or_authority_code"])
            expected = tuple(receipt["expected_node_ids"])
            governance_path = topology_root / f"{code}.governance.json"
            if receipt["outcome"] == "DEFERRED":
                if os.path.lexists(governance_path):
                    raise GovernanceError(
                        f"deferred receipt {code} has a root governance record"
                    )
                accounted.extend(expected)
                continue
            if capability_topology.CODE_CLASSIFICATION[expected_code] not in {
                "NATIVE_CAPABILITY_REQUIRED", "EXTERNAL_AUTHORITY_REQUIRED",
            }:
                raise GovernanceError(
                    f"root topology receipt {code} lacks an artifact execution boundary"
                )
            root_records.extend({
                "test_node_id": node,
                "component": "root",
                "outcome": "passed",
                "reason": "",
                "phase": "call",
            } for node in artifact_executed)
            accounted.extend(artifact_executed)
        if len(accounted) != len(set(accounted)):
            raise GovernanceError("root topology governance records overlap")
        if tuple(sorted(accounted)) != tuple(baseline["candidate_node_ids"]):
            raise GovernanceError("root topology governance accounting does not equal baseline")
        return {
            **disclosure,
            "portable_root_remainder_status": "PASS",
            "baseline_candidate_count": str(len(baseline["candidate_node_ids"])),
        }, root_records
    except (capability_topology.TopologyError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"root topology governance audit failed: {exc}") from exc


def run_topology_suites(
    report_dir: Path,
    *,
    topology_evidence_root: Path,
    inventory: Path,
    foundation_run_id: str,
    foundation_head_sha: str,
    foundation_context_path: Path | None = None,
) -> tuple[list[dict[str, object]], dict[str, int], dict[str, object]]:
    """Audit sealed root lanes, then retain the existing legacy/dashboard governance."""
    _prepare_private_directory(report_dir)
    disclosure, root_records = audit_topology_root_records(
        evidence_root=topology_evidence_root,
        inventory=inventory,
        foundation_run_id=foundation_run_id,
        foundation_head_sha=foundation_head_sha,
        foundation_context_path=foundation_context_path,
    )
    legacy_raw = report_dir / "legacy-raw.json"
    exit_codes = {
        "legacy": _run(
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
            cwd=ROOT / "legacy/research-backend",
            env=_pytest_environment("legacy", legacy_raw),
            log_path=report_dir / "legacy.log",
        ),
    }
    exit_codes["dashboard"], dashboard_raw = _run_dashboard(report_dir)
    records = list(root_records)
    for path in (legacy_raw, dashboard_raw):
        document = _read_json(path)
        if not isinstance(document, dict) or not isinstance(document.get("tests"), list):
            raise GovernanceError(f"invalid raw test report: {path}")
        records.extend(dict(item) for item in document["tests"])
    return records, exit_codes, disclosure


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
    security_critical = node_id.startswith(SECURITY_PATH_PREFIXES)
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


def _topology_policy_error(exc: GovernanceError) -> GovernanceError:
    message = str(exc).lower()
    if "expired" in message:
        code = "POLICY_REVIEW_DATE_EXPIRED"
    elif "schema" in message:
        code = "POLICY_SCHEMA_INVALID"
    elif "review_by" in message:
        code = "POLICY_REVIEW_DATE_INVALID"
    elif "non-normalized" in message:
        code = "POLICY_REASON_NORMALIZATION_INVALID"
    elif "duplicate" in message:
        code = "POLICY_DUPLICATE_ENTRY"
    elif "invalid" in message or "unapproved" in message:
        code = "POLICY_FIELD_TYPE_INVALID"
    else:
        code = "POLICY_VALIDATION_INVALID"
    return GovernanceError(f"policy validation failed: {code}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--bootstrap-allowlist", type=Path)
    parser.add_argument("--today", type=date.fromisoformat)
    parser.add_argument("--topology-audit", action="store_true")
    parser.add_argument("--topology-context-preflight", action="store_true")
    parser.add_argument("--topology-evidence-root", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--foundation-context-path", type=Path)
    parser.add_argument("--postgres-lanes", type=Path)
    args = parser.parse_args(argv)

    report_dir = args.report_dir if args.report_dir.is_absolute() else ROOT / args.report_dir
    output = report_dir / "test-governance.json"
    error_output = report_dir / "test-governance-error.json"
    records: list[dict[str, object]] = []
    exit_codes: dict[str, int] = {}
    topology_disclosure: dict[str, object] | None = None
    date_context_sources = {
        "cli_today_present": args.today is not None,
        "environment_override_present": "FOUNDATION_VALIDATION_DATE" in os.environ,
        "sealed_context_present": False,
        "sealed_context_valid": False,
    }
    try:
        _prepare_private_directory(report_dir)
        _remove_artifacts(
            output,
            error_output,
            report_dir / "root-raw.json",
            report_dir / "legacy-raw.json",
            report_dir / "dashboard-raw.json",
        )
        allowlist_argument = args.allowlist or DEFAULT_ALLOWLIST
        allowlist_path = (
            allowlist_argument
            if allowlist_argument.is_absolute()
            else ROOT / allowlist_argument
        )
        bootstrap_path = args.bootstrap_allowlist
        if bootstrap_path is not None and not bootstrap_path.is_absolute():
            bootstrap_path = ROOT / bootstrap_path
        if args.topology_context_preflight and not args.topology_audit:
            raise GovernanceError("topology context preflight requires topology audit")
        args.postgres_lanes = resolve_postgres_lanes(args.postgres_lanes)
        if args.postgres_lanes is not None and args.topology_audit:
            raise GovernanceError("postgres lanes are not part of the topology audit")
        if args.topology_audit:
            context_path = args.foundation_context_path
            if context_path is not None:
                if not context_path.is_absolute():
                    context_path = ROOT / context_path
                date_context_sources["sealed_context_present"] = os.path.lexists(
                    context_path
                )
            if (
                date_context_sources["cli_today_present"]
                or date_context_sources["environment_override_present"]
            ):
                if context_path is not None:
                    try:
                        diagnostic_run_id, diagnostic_head_sha = (
                            capability_topology._active_foundation_identity()
                        )
                    except capability_topology.TopologyError:
                        pass
                    else:
                        date_context_sources["sealed_context_valid"] = (
                            capability_topology._foundation_context_is_valid_for_diagnostics(
                                context_path,
                                run_id=diagnostic_run_id,
                                head_sha=diagnostic_head_sha,
                            )
                        )
                raise GovernanceError("policy validation failed: POLICY_DATE_CONTEXT_MISMATCH")
            if args.bootstrap_allowlist is not None:
                raise GovernanceError("topology audit cannot bootstrap an allowlist")
            if args.topology_context_preflight and (
                args.allowlist is None
                or args.topology_evidence_root is None
                or args.inventory is None
                or args.foundation_context_path is None
            ):
                raise GovernanceError(
                    "topology context preflight requires explicit allowlist, inventory, "
                    "evidence, and Foundation context inputs"
                )
            if (
                args.topology_evidence_root is None
                or args.inventory is None
                or args.foundation_context_path is None
            ):
                raise GovernanceError("topology audit requires evidence, inventory, Foundation run, and Foundation head")
            topology_evidence_root = args.topology_evidence_root
            if not topology_evidence_root.is_absolute():
                topology_evidence_root = ROOT / topology_evidence_root
            inventory = args.inventory if args.inventory.is_absolute() else ROOT / args.inventory
            foundation_run_id, foundation_head_sha = capability_topology._active_foundation_identity()
            context_path = args.foundation_context_path
            assert context_path is not None
            if not context_path.is_absolute():
                context_path = ROOT / context_path
            context = capability_topology.load_foundation_context(
                context_path,
                run_id=foundation_run_id,
                head_sha=foundation_head_sha,
            )
            date_context_sources["sealed_context_present"] = True
            date_context_sources["sealed_context_valid"] = True
            if args.topology_context_preflight:
                try:
                    capability_topology.load_inventory(inventory)
                except capability_topology.TopologyError as exc:
                    raise GovernanceError(
                        f"topology context preflight failed: {exc}"
                    ) from exc
                validation_date = capability_topology.parse_foundation_validation_date(
                    context["foundation_validation_date"]
                )
                try:
                    _load_allowlist(allowlist_path, validation_date)
                except GovernanceError as exc:
                    raise _topology_policy_error(exc) from exc
                print("topology context preflight: PASS")
                return 0
            records, exit_codes, topology_disclosure = run_topology_suites(
                report_dir,
                topology_evidence_root=topology_evidence_root,
                inventory=inventory,
                foundation_run_id=foundation_run_id,
                foundation_head_sha=foundation_head_sha,
                foundation_context_path=context_path,
            )
        elif args.postgres_lanes is not None:
            plan = validate_postgres_lane_plan(_read_json(DEFAULT_POSTGRES_LANE_PLAN))
            lane_inputs = load_postgres_lane_inputs(args.postgres_lanes.absolute(), plan)
            _require_authority_free_base()
            source = _candidate_source()
            records, exit_codes = run_suites(report_dir)
            lane_results, lane_exit_codes = run_postgres_lanes(report_dir, plan, lane_inputs, source=source)
            exit_codes.update(lane_exit_codes)
            records = merge_postgres_lanes(records, plan, lane_results, source=source)
            context = None
        else:
            records, exit_codes = run_suites(report_dir)
            context = None
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
            validate_allowlist_document(candidate, today=args.today or date.today())
            _write_json(bootstrap_path, candidate)
            print(f"wrote candidate allowlist: {bootstrap_path}")
        validation_date = (
            capability_topology.parse_foundation_validation_date(context["foundation_validation_date"])
            if context is not None else args.today or date.today()
        )
        try:
            entries = _load_allowlist(allowlist_path, validation_date)
        except GovernanceError as exc:
            if args.topology_audit:
                raise _topology_policy_error(exc) from exc
            raise
        compare_inventory(records, entries)
        report = build_governed_report(records, entries)
        if topology_disclosure is not None:
            report["capability_topology"] = topology_disclosure
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
        print(json.dumps(report["postgres_disclosure"], sort_keys=True))
        print(f"machine report: {output}")
        if failed_suites:
            raise GovernanceError(f"test suites failed: {failed_suites}")
    except GovernanceError as exc:
        error_document: dict[str, object] = {
            "schema_version": 1,
            "status": "error",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
            "suite_exit_codes": exit_codes,
        }
        if str(exc) == "policy validation failed: POLICY_DATE_CONTEXT_MISMATCH":
            error_document["date_context_sources"] = date_context_sources
        _write_json(
            error_output,
            error_document,
        )
        print(f"TEST_GOVERNANCE_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
