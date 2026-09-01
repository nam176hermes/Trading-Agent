from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest

import scripts.check_critical_coverage as critical_coverage
import scripts.check_test_governance as test_governance
import scripts.test_governance_pytest as governance_plugin

from scripts.check_critical_coverage import (
    CoverageGateError,
    DEFAULT_POLICY,
    _evaluate_python,
    _evaluate_required_cases,
    _validate_policy,
    _write_json as write_coverage_json,
    evaluate_ratio,
    main as coverage_main,
)
from scripts.check_test_governance import (
    GovernanceError,
    _parse_dashboard_tap,
    _write_json as write_governance_json,
    build_governed_report,
    compare_inventory,
    validate_allowlist_document,
)


REQUIRED_FIELDS = {
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

REPORT_WRITERS = [
    pytest.param(write_coverage_json, id="critical-coverage"),
    pytest.param(write_governance_json, id="test-governance"),
]
REPORT_WRITER_FAILURES = [
    pytest.param(
        write_coverage_json,
        CoverageGateError,
        "coverage report directory is not a private owned directory",
        id="critical-coverage",
    ),
    pytest.param(
        write_governance_json,
        GovernanceError,
        "report directory is not a private owned directory",
        id="test-governance",
    ),
]


def _lstat_identity(path: Path) -> tuple[int, int, int, int]:
    metadata = path.lstat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
    )


def test_final_governed_summary_contract_is_canonical_and_timestamp_independent() -> None:
    builder = getattr(test_governance, "build_final_governed_summary", None)
    assert callable(builder), "P0-10 governed summary projection is missing"
    report = build_governed_report(
        [{
            "test_node_id": "tests/test_safe.py::test_one",
            "component": "root",
            "outcome": "passed",
            "reason": "",
            "phase": "call",
        }],
        [],
    )
    report.update({
        "capability_topology": {"portable_source_status": "PASS"},
        "generated_at_utc": "2026-08-13T12:00:00+00:00",
        "suite_exit_codes": {"root": 0},
        "allowlist": "tests/skip-allowlist.yaml",
        "status": "pass",
    })
    raw, semantic = builder(report)
    parsed = json.loads(raw)
    assert raw == json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert parsed["schema_version"] == "test-governance-final-summary/v1"
    assert semantic == [{
        "component": "root",
        "node_id": "tests/test_safe.py::test_one",
        "outcome": "passed",
        "phase": "call",
    }]
    rerun = dict(report, generated_at_utc="2026-08-14T00:00:00+00:00")
    _, rerun_semantic = builder(rerun)
    assert rerun_semantic == semantic


def test_final_governed_error_contract_is_closed_and_never_copies_raw_error() -> None:
    builder = getattr(test_governance, "build_final_governed_error", None)
    assert callable(builder), "P0-10 governed error projection is missing"
    raw, semantic = builder({
        "schema_version": 1,
        "status": "error",
        "generated_at_utc": "2026-08-13T12:00:00+00:00",
        "error": "test suites failed: nested password=must-not-leak",
        "suite_exit_codes": {"legacy": 1},
    })
    parsed = json.loads(raw)
    assert parsed["schema_version"] == "test-governance-final-error/v1"
    assert parsed["error_code"] == "SUITE_FAILURE"
    assert "must-not-leak" not in raw.decode()
    assert semantic == {"error_code": "SUITE_FAILURE", "suite_exit_codes": {"legacy": 1}}


def entry(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "test_node_id": "tests/jobs/test_runtime.py::test_requires_database",
        "component": "root",
        "reason_category": "DISPOSABLE_POSTGRES_REQUIRED",
        "reason": "explicit disposable PostgreSQL authority is not present",
        "owner": "job-plane",
        "approval_record_type": "disposable-postgres-test-approval-v1",
        "required_binary_or_service": "PostgreSQL 16 disposable cluster",
        "target_phase": "Package 6 paper runtime validation",
        "review_by": "2026-10-31",
        "security_critical": True,
        "allowed_in_ci": True,
        "outcome": "skipped",
    }
    document.update(changes)
    return document


def record(
    outcome: str = "skipped",
    *,
    node_id: str = "tests/jobs/test_runtime.py::test_requires_database",
    component: str = "root",
    reason: str = "explicit disposable PostgreSQL authority is not present",
) -> dict[str, object]:
    return {
        "test_node_id": node_id,
        "component": component,
        "outcome": outcome,
        "reason": reason,
        "phase": "setup" if outcome == "skipped" else "collection",
    }


def allowlist(*entries: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 1, "entries": list(entries)}


def test_allowlist_schema_is_exact_and_unknown_is_never_accepted() -> None:
    approved = entry()
    validated = validate_allowlist_document(
        allowlist(approved),
        today=date(2026, 7, 24),
    )
    assert set(validated[0]) == REQUIRED_FIELDS

    unknown = entry(reason_category="UNKNOWN")
    with pytest.raises(GovernanceError, match="UNKNOWN"):
        validate_allowlist_document(allowlist(unknown), today=date(2026, 7, 24))

    extra = entry(unreviewed_field="not allowed")
    with pytest.raises(GovernanceError, match="fields"):
        validate_allowlist_document(allowlist(extra), today=date(2026, 7, 24))

    tracked = validate_allowlist_document(
        json.loads((test_governance.ROOT / "tests/skip-allowlist.yaml").read_text()),
        today=date(2026, 8, 14),
    )
    migrated_pg_skips = [
        item for item in tracked
        if item["component"] == "root"
        and item["outcome"] == "skipped"
        and item["reason_category"] == "DISPOSABLE_POSTGRES_REQUIRED"
        and item["approval_record_type"] == "disposable-postgres-test-approval-v1"
    ]
    assert migrated_pg_skips == []
    assert len(tracked) == 39
    assert sum(
        item["component"] == "root" and item["outcome"] == "deselected"
        for item in tracked
    ) == 37
    assert sum(
        item["component"] == "legacy" and item["outcome"] == "skipped"
        for item in tracked
    ) == 2


def test_expired_and_unapproved_security_critical_entries_fail_closed() -> None:
    with pytest.raises(GovernanceError, match="expired"):
        validate_allowlist_document(
            allowlist(entry(review_by="2026-07-23")),
            today=date(2026, 7, 24),
        )

    for changes in (
        {"approval_record_type": "NONE"},
        {"reason": ""},
    ):
        with pytest.raises(GovernanceError, match="security-critical"):
            validate_allowlist_document(
                allowlist(entry(**changes)),
                today=date(2026, 7, 24),
            )


def test_new_skip_and_stale_allowlist_entry_both_fail() -> None:
    approved = entry()
    actual = record()
    compare_inventory([actual], [approved])

    with pytest.raises(GovernanceError, match="new unapproved"):
        compare_inventory(
            [actual, record(node_id="tests/jobs/test_runtime.py::test_new_skip")],
            [approved],
        )

    with pytest.raises(GovernanceError, match="stale"):
        compare_inventory([], [approved])

    with pytest.raises(GovernanceError, match="reasons changed"):
        compare_inventory(
            [record(reason="different runtime prerequisite")],
            [approved],
        )

    with pytest.raises(GovernanceError, match="outcomes changed"):
        compare_inventory([record("deselected")], [approved])


def test_security_master_disposable_postgres_skip_is_governed() -> None:
    node_id = (
        "tests/security_master/test_postgres_runtime.py::"
        "test_runtime_migration_and_pit_correction_are_fail_closed"
    )
    entries = validate_allowlist_document(
        json.loads(test_governance.DEFAULT_ALLOWLIST.read_text(encoding="utf-8")),
        today=date(2026, 9, 1),
    )
    approved = [item for item in entries if item["test_node_id"] == node_id]

    compare_inventory(
        [{
            "test_node_id": node_id,
            "component": "root",
            "outcome": "deselected",
            "reason": "marker expression deselected: runtime_postgres",
            "phase": "collection",
        }],
        approved,
    )


def test_not_run_observation_always_fails_inventory() -> None:
    with pytest.raises(GovernanceError, match="not executed"):
        compare_inventory(
            [record("not_run", reason="collected but not executed")],
            [],
        )


@pytest.mark.parametrize(
    "outcome",
    ["failed", "unknown", "expired", "changed", "unauthorized", ""],
)
def test_non_inventory_observations_fail_closed(outcome: str) -> None:
    with pytest.raises(GovernanceError, match="invalid observed outcome"):
        compare_inventory([record(outcome, reason="synthetic observation")], [])


def test_observation_schema_requires_exact_identity_fields() -> None:
    malformed = record("passed")
    del malformed["test_node_id"]

    with pytest.raises(GovernanceError, match="invalid observed test record"):
        compare_inventory([malformed], [])


def test_allowlist_reason_must_already_be_normalized() -> None:
    with pytest.raises(GovernanceError, match="normalized reason"):
        validate_allowlist_document(
            allowlist(entry(reason="repeated   whitespace\tinside")),
            today=date(2026, 7, 24),
        )


def test_disallowed_ci_entry_fails_when_observed() -> None:
    approved = entry(allowed_in_ci=False)
    with pytest.raises(GovernanceError, match="not allowed in CI"):
        compare_inventory([record()], [approved])


def test_report_distinguishes_raw_and_governed_outcomes() -> None:
    postgres = entry()
    host = entry(
        test_node_id="tests/runtime_release/test_build.py::test_host_build",
        reason_category="MISSING_HOST_CAPABILITY",
        reason="Requires the sealed host wheelhouse.",
        owner="release-engineering",
        approval_record_type="host-capability-review-v1",
        required_binary_or_service="sealed runtime wheelhouse",
        target_phase="Host release proof",
        security_critical=False,
        outcome="deselected",
    )
    records = [
        record("passed", node_id="tests/domain/test_clock.py::test_clock"),
        record(),
        record(
            "deselected",
            node_id="tests/runtime_release/test_build.py::test_host_build",
            reason="marker expression deselected: host_coupled",
        ),
    ]

    report = build_governed_report(records, [postgres, host])

    assert report["summary"] == {
        "executed": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 1,
        "deselected": 1,
        "approval_blocked": 1,
        "not_run": 0,
    }
    assert report["postgres_disclosure"] == {
        "approval_blocked_count": 1,
        "production_postgres_mutation": {
            "decision": "FORBIDDEN",
            "requires_separate_authority": True,
        },
        "runtime_proof": {
            "blocks_runtime_release": True,
            "decision": "BLOCKED_PENDING_EXACT_COMMIT_AUTHORITY",
            "required_lifecycle_authorities": [
                "INITDB",
                "START",
                "RESTORE",
                "STOP",
                "DELETE",
            ],
        },
        "source_upgrade": {
            "blocks_source_upgrade": False,
            "decision": "PASS_WITH_POSTGRES_RUNTIME_DEFERRED",
        },
    }
    governed = {item["test_node_id"]: item for item in report["tests"]}
    assert governed[postgres["test_node_id"]]["raw_outcome"] == "skipped"
    assert governed[postgres["test_node_id"]]["governed_outcome"] == "approval_blocked"
    assert governed[host["test_node_id"]]["governed_outcome"] == "deselected"


def test_postgres_disclosure_never_hides_source_test_failures() -> None:
    postgres = entry()
    report = build_governed_report(
        [
            record("failed", node_id="tests/domain/test_clock.py::test_clock"),
            record(),
        ],
        [postgres],
    )

    disclosure = report["postgres_disclosure"]
    assert isinstance(disclosure, dict)
    assert disclosure["source_upgrade"] == {
        "blocks_source_upgrade": True,
        "decision": "FAIL_SOURCE_TESTS",
    }
    runtime_proof = disclosure["runtime_proof"]
    assert isinstance(runtime_proof, dict)
    assert runtime_proof["decision"] == "BLOCKED_PENDING_EXACT_COMMIT_AUTHORITY"


def test_coverage_ratchet_uses_exact_ratios_and_never_rounds_down() -> None:
    assert evaluate_ratio(
        covered=665,
        total=699,
        minimum_covered=665,
        minimum_total=699,
        label="domain lines",
    ) == pytest.approx(95.1359, rel=1e-4)

    with pytest.raises(CoverageGateError, match="regressed"):
        evaluate_ratio(
            covered=664,
            total=699,
            minimum_covered=665,
            minimum_total=699,
            label="domain lines",
        )

    with pytest.raises(CoverageGateError, match="invalid"):
        evaluate_ratio(
            covered=1,
            total=0,
            minimum_covered=1,
            minimum_total=1,
            label="invalid scope",
        )


@pytest.mark.parametrize(
    ("covered", "total", "message"),
    [(664, 699, "covered count"), (665, 698, "coverage scope")],
)
def test_coverage_ratchet_rejects_absolute_covered_or_denominator_shrinkage(
    covered: int, total: int, message: str,
) -> None:
    with pytest.raises(CoverageGateError, match=message):
        evaluate_ratio(
            covered=covered,
            total=total,
            minimum_covered=665,
            minimum_total=699,
            label="domain lines",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy: policy["python"].update({"sources": ["scripts"]}),
        lambda policy: policy["python"].update({"pytest_paths": ["tests/governance"]}),
        lambda policy: policy["python"]["units"].pop(),
        lambda policy: policy["python"]["units"][0].update({"name": "weakened"}),
        lambda policy: policy["python"]["units"][0].update(
            {"line_minimum": {"covered": 1, "total": 1}}
        ),
        lambda policy: policy["dashboard"].update(
            {"include_files": ["src/lib/trading/auth.ts"]}
        ),
        lambda policy: policy["dashboard"].update(
            {"test_files": ["tests/session-policy.test.mjs"]}
        ),
        lambda policy: policy["dashboard"]["unit"].update(
            {"files": ["src/lib/trading/auth.ts"]}
        ),
        lambda policy: policy["required_cases"].pop(),
        lambda policy: policy["required_cases"][0].update({"case": "renamed"}),
        lambda policy: policy["required_cases"][0].update(
            {"tests": ["tests/domain/test_models.py::test_trivial"]}
        ),
    ],
)
def test_critical_coverage_policy_cannot_weaken_the_sealed_program(mutation) -> None:
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    mutation(policy)

    with pytest.raises(CoverageGateError, match="sealed critical coverage"):
        _validate_policy(policy)


def test_inventory_comparison_does_not_mutate_inputs() -> None:
    approved = entry()
    actual = record()
    before_allowlist = deepcopy(approved)
    before_record = deepcopy(actual)

    compare_inventory([actual], [approved])

    assert approved == before_allowlist
    assert actual == before_record


def test_duplicate_observed_skip_node_ids_fail_closed() -> None:
    observed = record()

    with pytest.raises(GovernanceError, match="duplicate observed"):
        compare_inventory([observed, deepcopy(observed)], [entry()])


def test_required_cases_only_accept_successful_execution(tmp_path) -> None:
    dashboard_report = tmp_path / "dashboard.json"
    dashboard_report.write_text(
        json.dumps({"tests": [{
            "test_node_id": "apps/dashboard/tests/policy.test.mjs::rejects unsafe origin",
            "outcome": "failed",
        }]}),
        encoding="utf-8",
    )
    cases = [
        {
            "case": "Python fail-closed proof",
            "tests": [
                "tests/jobs/test_safety.py::test_gate[first]",
                "tests/jobs/test_safety.py::test_gate[second]",
            ],
        },
        {
            "case": "dashboard fail-closed proof",
            "tests": ["apps/dashboard/tests/policy.test.mjs::rejects unsafe origin"],
        },
    ]
    failed_report = {
        "tests": [
            {
                "test_node_id": "tests/jobs/test_safety.py::test_gate[first]",
                "outcome": "passed",
            },
            {
                "test_node_id": "tests/jobs/test_safety.py::test_gate[second]",
                "outcome": "skipped",
            },
        ]
    }

    with pytest.raises(CoverageGateError, match="were not executed"):
        _evaluate_required_cases(cases, failed_report, dashboard_report)

    dashboard_report.write_text(
        json.dumps({"tests": [{
            "test_node_id": "apps/dashboard/tests/policy.test.mjs::rejects unsafe origin",
            "outcome": "passed",
        }]}),
        encoding="utf-8",
    )
    passed_report = deepcopy(failed_report)
    for item in passed_report["tests"]:
        item["outcome"] = "passed"

    results = _evaluate_required_cases(cases, passed_report, dashboard_report)

    assert [result["status"] for result in results] == ["pass", "pass"]


def test_dashboard_required_cases_require_unique_full_file_and_title_identity(
    tmp_path: Path,
) -> None:
    required = "apps/dashboard/tests/policy.test.mjs::rejects unsafe origin"
    report = tmp_path / "dashboard.json"
    python = {"tests": []}
    report.write_text(json.dumps({"tests": [{
        "test_node_id": "apps/dashboard/tests/clone.test.mjs::rejects unsafe origin",
        "outcome": "passed",
    }]}), encoding="utf-8")
    with pytest.raises(CoverageGateError, match="were not executed"):
        _evaluate_required_cases([{"case": "origin", "tests": [required]}], python, report)

    duplicate = {"test_node_id": required, "outcome": "passed"}
    report.write_text(json.dumps({"tests": [duplicate, duplicate]}), encoding="utf-8")
    with pytest.raises(CoverageGateError, match="duplicate dashboard"):
        _evaluate_required_cases([{"case": "origin", "tests": [required]}], python, report)


def test_python_required_cases_reject_duplicate_exact_observations(tmp_path: Path) -> None:
    node = "tests/jobs/test_safety.py::test_gate"
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text(json.dumps({"tests": []}), encoding="utf-8")
    python = {"tests": [
        {"test_node_id": node, "outcome": "failed"},
        {"test_node_id": node, "outcome": "passed"},
    ]}

    with pytest.raises(CoverageGateError, match="duplicate Python"):
        _evaluate_required_cases(
            [{"case": "safety", "tests": [node]}], python, dashboard
        )


def test_python_required_case_base_cannot_hide_missing_parameter_variants(
    tmp_path: Path,
) -> None:
    base = "tests/jobs/test_gate.py::test_gate"
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text(
        json.dumps({"tests": [{
            "test_node_id": "apps/dashboard/tests/x.test.mjs::x",
            "outcome": "passed",
        }]}),
        encoding="utf-8",
    )
    python = {"tests": [{
        "test_node_id": f"{base}[first]",
        "outcome": "passed",
    }]}

    with pytest.raises(CoverageGateError, match="were not executed"):
        _evaluate_required_cases(
            [{"case": "all variants", "tests": [base]}], python, dashboard
        )


def test_dashboard_runner_fails_each_file_with_zero_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = tmp_path / "apps" / "dashboard"
    tests = dashboard / "tests"
    tests.mkdir(parents=True)
    (tests / "empty.test.mjs").write_text("", encoding="utf-8")
    (tests / "seen.test.mjs").write_text("", encoding="utf-8")
    (tests / "dashboard-security.integration.sh").write_text("", encoding="utf-8")
    (tests / "mode-auth.integration.sh").write_text("", encoding="utf-8")

    def fake_run(command, **kwargs):
        if "--list-json" in command:
            output = json.dumps({
                "schema_version": 1,
                "node_tests": ["tests/empty.test.mjs", "tests/seen.test.mjs"],
                "integration_tests": [
                    "tests/dashboard-security.integration.sh",
                    "tests/mode-auth.integration.sh",
                ],
            })
        elif command[0] == "node" and command[-1] == "tests/empty.test.mjs":
            output = "TAP version 13\n1..0\n"
        elif command[0] == "node":
            output = "TAP version 13\n# Subtest: seen\nok 1 - seen\n1..1\n"
        else:
            output = "dashboard security integration: PASS\n"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(test_governance, "ROOT", tmp_path)
    monkeypatch.setattr(test_governance.subprocess, "run", fake_run)
    report_dir = tmp_path / "reports"
    report_dir.mkdir(mode=0o700)

    exit_status, report_path = test_governance._run_dashboard(report_dir)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_status == 1
    assert {
        "test_node_id": "apps/dashboard/tests/empty.test.mjs::static-test-inventory",
        "component": "dashboard",
        "outcome": "failed",
        "reason": "node:test TAP report contained no test records for file",
        "phase": "report",
    } in report["tests"]
    integration_nodes = {
        item["test_node_id"]
        for item in report["tests"]
        if str(item["test_node_id"]).endswith("::isolated integration script")
    }
    assert integration_nodes == {
        "apps/dashboard/tests/dashboard-security.integration.sh::isolated integration script",
        "apps/dashboard/tests/mode-auth.integration.sh::isolated integration script",
    }


def test_dashboard_runner_recursively_observes_nested_supported_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = tmp_path / "apps" / "dashboard"
    nested = dashboard / "tests" / "nested"
    nested.mkdir(parents=True)
    (nested / "policy.test.mjs").write_text("", encoding="utf-8")
    (nested / "policy.integration.sh").write_text("", encoding="utf-8")

    def fake_run(command, **kwargs):
        if "--list-json" in command:
            output = json.dumps({
                "schema_version": 1,
                "node_tests": ["tests/nested/policy.test.mjs"],
                "integration_tests": ["tests/nested/policy.integration.sh"],
            })
        elif command[0] == "node":
            output = "TAP version 13\n# Subtest: nested\nok 1 - nested\n1..1\n"
        else:
            output = "nested integration: PASS\n"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(test_governance, "ROOT", tmp_path)
    monkeypatch.setattr(test_governance.subprocess, "run", fake_run)
    report_dir = tmp_path / "reports"
    report_dir.mkdir(mode=0o700)

    exit_status, report_path = test_governance._run_dashboard(report_dir)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_status == 0
    assert {item["test_node_id"] for item in report["tests"]} == {
        "apps/dashboard/tests/nested/policy.test.mjs::nested",
        "apps/dashboard/tests/nested/policy.integration.sh::isolated integration script",
    }


def test_dashboard_canonical_inventory_rejects_unclassified_files(tmp_path: Path) -> None:
    source = Path("apps/dashboard/tests")
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in (
        "run-test-inventory.mjs",
        "test-inventory.json",
        "trusted-test-tmp.mjs",
    ):
        (tests / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tests / "visible.test.mjs").write_text("", encoding="utf-8")
    (tests / "visible.integration.sh").write_text("", encoding="utf-8")
    (tests / "hidden.spec.mjs").write_text("", encoding="utf-8")

    result = subprocess.run(
        ["node", "tests/run-test-inventory.mjs", "--list-json"],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode != 0
    assert "unclassified dashboard test files" in result.stdout


def test_critical_dashboard_runner_fails_each_file_with_zero_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()

    def fake_run(command, **kwargs):
        assert kwargs["env"]["TMPDIR"] == "/tmp"
        assert kwargs["env"]["TEMP"] == "/tmp"
        assert kwargs["env"]["TMP"] == "/tmp"
        output = (
            "TAP version 13\n1..0\n"
            if command[-1] == "tests/empty.test.mjs"
            else "TAP version 13\n# Subtest: seen\nok 1 - seen\n1..1\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(critical_coverage.subprocess, "run", fake_run)
    with TemporaryDirectory(dir="/tmp") as temporary_directory:
        report_dir = Path(temporary_directory)
        exit_status, report_path = critical_coverage._run_dashboard_observations(
            dashboard,
            ["tests/empty.test.mjs", "tests/seen.test.mjs"],
            report_dir,
            {},
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))

        assert exit_status == 1
        assert {
            "test_node_id": "apps/dashboard/tests/empty.test.mjs::static-test-inventory",
            "outcome": "failed",
        } in report["tests"]


def _run_governed_fixture(
    tmp_path: Path,
    conftest: str,
    *,
    collection_only: bool = False,
    custody_policy_json: str | None = None,
) -> tuple[subprocess.CompletedProcess, dict, bytes]:
    tests = tmp_path / "fixture-tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(conftest, encoding="utf-8")
    (tests / "test_two.py").write_text(
        "def test_visible():\n    assert True\n\n"
        "def test_hidden():\n    assert False\n",
        encoding="utf-8",
    )
    report = tmp_path / "governance.json"
    env = os.environ.copy()
    env.update({
        "TEST_GOVERNANCE_REPORT": str(report),
        "TEST_GOVERNANCE_COMPONENT": "root",
    })
    env.pop("TEST_GOVERNANCE_CUSTODY_POLICY", None)
    if collection_only:
        env["TEST_GOVERNANCE_COLLECTION_ONLY"] = "1"
    if custody_policy_json is not None:
        env["TEST_GOVERNANCE_CUSTODY_POLICY"] = custody_policy_json
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(tests),
            "-p",
            "scripts.test_governance_pytest",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    raw = report.read_bytes() if report.exists() else b""
    return result, json.loads(raw) if raw else {}, raw


def test_collection_hook_cannot_remove_one_test_without_failure(tmp_path: Path) -> None:
    result, report, raw = _run_governed_fixture(
        tmp_path,
        "def pytest_collection_modifyitems(items):\n    items[:] = items[:1]\n",
        collection_only=True,
    )

    assert result.returncode != 0
    assert any(
        item["test_node_id"].endswith("::test_hidden")
        and item["outcome"] == "failed"
        for item in report["tests"]
    )
    assert raw == json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def test_collected_test_suppressed_by_runtest_hook_fails_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TEST_GOVERNANCE_CUSTODY_POLICY",
        '{"ambient":"must-not-authorize-a-nested-report"}',
    )
    custody_case = tmp_path / "custody"
    custody_case.mkdir()
    result, report, raw = _run_governed_fixture(
        custody_case,
        "def pytest_runtest_protocol(item, nextitem):\n"
        "    if item.name == 'test_hidden':\n        return True\n",
        custody_policy_json=json.dumps(
            {"descriptor_custody": "retained", "schema_version": 1},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )

    assert result.returncode != 0
    assert any(
        item["test_node_id"].endswith("::test_hidden")
        and item["outcome"] == "not_run"
        for item in report["tests"]
    )
    assert raw == json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")

    ordinary_case = tmp_path / "ordinary"
    ordinary_case.mkdir()
    ordinary_result, ordinary_report, ordinary_raw = _run_governed_fixture(
        ordinary_case,
        "def pytest_runtest_protocol(item, nextitem):\n"
        "    if item.name == 'test_hidden':\n        return True\n",
    )
    assert ordinary_result.returncode != 0
    assert ordinary_raw == (
        json.dumps(ordinary_report, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    for name, custody_policy_json in (("malformed", "{"), ("non-object", "[]")):
        malformed_case = tmp_path / name
        malformed_case.mkdir()
        malformed_result, malformed_report, malformed_raw = _run_governed_fixture(
            malformed_case,
            "",
            custody_policy_json=custody_policy_json,
        )
        assert malformed_result.returncode != 0
        assert malformed_report == {}
        assert malformed_raw == b""
        assert "test governance custody policy is malformed" in malformed_result.stdout


def test_dashboard_tap_identity_includes_file_and_parent_suite() -> None:
    tap = """# Subtest: parent suite
    # Subtest: null
    ok 1 - null
    1..1
ok 1 - parent suite
"""
    first = _parse_dashboard_tap(tap, "tests/first.test.mjs")
    second = _parse_dashboard_tap(tap, "tests/second.test.mjs")

    assert first[0]["test_node_id"] == (
        "apps/dashboard/tests/first.test.mjs::parent suite::null"
    )
    assert {item["test_node_id"] for item in first}.isdisjoint(
        {item["test_node_id"] for item in second}
    )


def test_dashboard_tap_skip_reason_uses_canonical_whitespace() -> None:
    tap = "ok 1 - deferred # SKIP repeated   whitespace\tinside\n"

    records = _parse_dashboard_tap(tap, "tests/policy.test.mjs")

    assert records[0]["outcome"] == "skipped"
    assert records[0]["reason"] == "repeated whitespace inside"


def test_python_coverage_rejects_non_integer_summary_counters() -> None:
    coverage = {"files": {"critical.py": {"summary": {
        "covered_lines": "5",
        "num_statements": "5",
        "covered_branches": "1",
        "num_branches": "1",
    }}}}
    unit = {
        "name": "critical",
        "files": ["critical.py"],
        "line_minimum": {"covered": 1, "total": 1},
        "branch_minimum": {"covered": 1, "total": 1},
        "line_goal_percent": 95,
        "line_next_step_percent": 95,
        "branch_goal_percent": 90,
        "branch_next_step_percent": 90,
    }

    with pytest.raises(CoverageGateError, match="invalid Python coverage counters"):
        _evaluate_python(coverage, [unit])


@pytest.mark.parametrize(
    "changes",
    [
        {"component": "invented"},
        {"owner": "x"},
        {"approval_record_type": "x"},
        {"reason_category": "INTENTIONALLY_DEFERRED"},
        {"security_critical": False},
    ],
)
def test_allowlist_rejects_unsealed_metadata_and_security_downgrades(
    changes: dict[str, object],
) -> None:
    with pytest.raises(GovernanceError):
        validate_allowlist_document(
            allowlist(entry(**changes)), today=date(2026, 7, 24)
        )


def test_dashboard_security_node_cannot_downgrade_derived_criticality() -> None:
    dashboard_entry = entry(
        test_node_id="apps/dashboard/tests/security.test.mjs::security proof",
        component="dashboard",
        owner="dashboard-security",
        reason_category="MISSING_HOST_CAPABILITY",
        approval_record_type="host-capability-review-v1",
        required_binary_or_service="isolated capability",
        target_phase="security proof",
        security_critical=False,
    )

    with pytest.raises(GovernanceError, match="derived security criticality"):
        validate_allowlist_document(
            allowlist(dashboard_entry), today=date(2026, 7, 24)
        )


def test_runtime_release_node_cannot_downgrade_derived_criticality() -> None:
    release_entry = entry(
        test_node_id=(
            "tests/runtime_release/test_v2.py::"
            "test_pinned_uv_projection_identity_matches_builder_probe"
        ),
        owner="release-engineering",
        reason_category="MISSING_HOST_CAPABILITY",
        approval_record_type="host-capability-review-v1",
        required_binary_or_service="exact pinned uv executable",
        target_phase="Host release proof",
        security_critical=False,
        outcome="deselected",
    )

    with pytest.raises(GovernanceError, match="derived security criticality"):
        validate_allowlist_document(
            allowlist(release_entry), today=date(2026, 7, 24)
        )


@pytest.mark.parametrize(
    ("hidden_name", "hidden_source"),
    [
        ("test_hidden.py", 'globals()["test_hidden"] = lambda: None\n'),
        ("hidden_test.py", 'globals()["test_hidden"] = lambda: None\n'),
    ],
)
def test_pytest_governance_rejects_any_candidate_module_hidden_at_collection(
    tmp_path: Path, hidden_name: str, hidden_source: str,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(
        f'collect_ignore = ["{hidden_name}"]\n', encoding="utf-8"
    )
    (tests / "test_visible.py").write_text(
        "def test_visible():\n    assert True\n", encoding="utf-8"
    )
    (tests / hidden_name).write_text(hidden_source, encoding="utf-8")
    report = tmp_path / "report.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "TEST_GOVERNANCE_COMPONENT": "root",
        "TEST_GOVERNANCE_REPORT": str(report),
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "scripts.test_governance_pytest", "tests"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["pytest_exit_status"] == result.returncode
    assert any(
        item["test_node_id"] == f"tests/{hidden_name}::static-test-inventory"
        and item["outcome"] == "failed"
        for item in document["tests"]
    )


def test_critical_coverage_early_policy_failure_removes_all_stale_artifacts(
    tmp_path: Path,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    stale_names = (
        ".critical-coverage",
        "critical-coverage-python.json",
        "critical-coverage-python-tests.json",
        "critical-coverage-dashboard.lcov",
        "critical-coverage.json",
        "critical-coverage-python-tests.log",
        "critical-coverage-python-json.log",
        "critical-coverage-dashboard-tests.log",
        "critical-coverage-dashboard-tests.json",
        ".critical-coverage-python.deadbeef.json",
    )
    for name in stale_names:
        (report_dir / name).write_text("stale", encoding="utf-8")
    malformed = tmp_path / "policy.json"
    malformed.write_text('{"schema_version":0}', encoding="utf-8")

    assert coverage_main(["--policy", str(malformed), "--report-dir", str(report_dir)]) == 1
    assert sorted(path.name for path in report_dir.iterdir()) == [
        "critical-coverage-error.json"
    ]


@pytest.mark.parametrize(
    "writer",
    [write_coverage_json, write_governance_json, governance_plugin._atomic_json],
)
def test_atomic_report_writer_does_not_follow_predictable_temp_symlink(
    tmp_path: Path, writer,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    (report_dir / "report.json.tmp").symlink_to(victim)

    writer(report_dir / "report.json", {"status": "fresh"})

    assert victim.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize(
    "writer",
    [write_coverage_json, write_governance_json, governance_plugin._atomic_json],
)
def test_atomic_report_writer_binds_validated_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(mode=0o700)
    displaced = tmp_path / "validated-reports"
    real_open = os.open
    swapped = False

    def swap_parent() -> None:
        nonlocal swapped
        if swapped:
            return
        report_dir.rename(displaced)
        report_dir.mkdir(mode=0o700)
        swapped = True

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        if not swapped and Path(path) == report_dir:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            swap_parent()
            return descriptor
        if not swapped:
            swap_parent()
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises((CoverageGateError, GovernanceError, RuntimeError)):
        writer(report_dir / "report.json", {"status": "fresh"})

    assert not (report_dir / "report.json").exists()


def test_critical_coverage_rejects_symlink_report_directory(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    malformed = tmp_path / "policy.json"
    malformed.write_text('{"schema_version":0}', encoding="utf-8")

    assert coverage_main(["--policy", str(malformed), "--report-dir", str(alias)]) == 1
    assert list(actual.iterdir()) == []


def test_critical_coverage_tightens_current_user_owned_legacy_report_root(
    tmp_path: Path,
) -> None:
    legacy_report_root = tmp_path / "legacy-evidence-root"
    legacy_report_root.mkdir(mode=0o777)
    legacy_report_root.chmod(0o777)
    malformed = tmp_path / "policy.json"
    malformed.write_text('{"schema_version":0}', encoding="utf-8")

    assert coverage_main([
        "--policy", str(malformed), "--report-dir", str(legacy_report_root)
    ]) == 1
    assert legacy_report_root.stat().st_mode & 0o777 == 0o700
    assert [path.name for path in legacy_report_root.iterdir()] == [
        "critical-coverage-error.json"
    ]


def test_critical_coverage_rejects_current_user_owned_writable_intermediate(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-evidence-root"
    private_root.mkdir(mode=0o700)
    unsafe_intermediate = private_root / "legacy-evidence-intermediate"
    unsafe_intermediate.mkdir(mode=0o777)
    unsafe_intermediate.chmod(0o777)
    intermediate_identity = _lstat_identity(unsafe_intermediate)
    malformed = tmp_path / "policy.json"
    malformed.write_text('{"schema_version":0}', encoding="utf-8")
    report_dir = unsafe_intermediate / "reports"

    assert coverage_main([
        "--policy", str(malformed), "--report-dir", str(report_dir)
    ]) == 1
    assert unsafe_intermediate.stat().st_mode & 0o777 == 0o777
    assert not report_dir.exists()
    assert list(unsafe_intermediate.iterdir()) == []
    assert _lstat_identity(unsafe_intermediate) == intermediate_identity


def test_test_governance_rejects_writable_intermediate_without_mutation(
    tmp_path: Path,
) -> None:
    """Break caught: governance silently tightens an unsafe parent and creates its child."""
    private_root = tmp_path / "private-evidence-root"
    private_root.mkdir(mode=0o700)
    unsafe_intermediate = private_root / "legacy-evidence-intermediate"
    unsafe_intermediate.mkdir(mode=0o777)
    unsafe_intermediate.chmod(0o777)
    intermediate_identity = _lstat_identity(unsafe_intermediate)
    report_dir = unsafe_intermediate / "reports"
    rejected = False

    try:
        test_governance._prepare_private_directory(report_dir)
    except GovernanceError as exc:
        rejected = True
        assert str(exc) == "report directory is not a private owned directory"

    assert unsafe_intermediate.stat().st_mode & 0o777 == 0o777
    assert not report_dir.exists()
    assert list(unsafe_intermediate.iterdir()) == []
    assert rejected
    assert _lstat_identity(unsafe_intermediate) == intermediate_identity


@pytest.mark.parametrize("writer", REPORT_WRITERS)
def test_report_writer_tightens_only_the_exact_writable_report_root(
    tmp_path: Path, writer,
) -> None:
    """Break caught: exact legacy report roots become unusable instead of tightening."""
    report_dir = tmp_path / "legacy-report-root"
    report_dir.mkdir(mode=0o777)
    report_dir.chmod(0o777)
    artifact = report_dir / "result.json"

    writer(artifact, {"status": "ok"})

    assert report_dir.stat().st_mode & 0o777 == 0o700
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"status": "ok"}


@pytest.mark.parametrize(
    ("writer", "error_type", "error_message"),
    REPORT_WRITER_FAILURES,
)
def test_report_writer_rejects_writable_intermediate_without_mutation_or_child(
    tmp_path: Path, writer, error_type, error_message: str,
) -> None:
    """Break caught: a writer chmods a writable intermediate and publishes below it."""
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    unsafe_intermediate = private_root / "writable-intermediate"
    unsafe_intermediate.mkdir(mode=0o777)
    unsafe_intermediate.chmod(0o777)
    intermediate_identity = _lstat_identity(unsafe_intermediate)
    report_dir = unsafe_intermediate / "reports"
    rejected = False

    try:
        writer(report_dir / "result.json", {"status": "unsafe"})
    except error_type as exc:
        rejected = True
        assert str(exc) == error_message

    assert unsafe_intermediate.stat().st_mode & 0o777 == 0o777
    assert not report_dir.exists()
    assert list(unsafe_intermediate.iterdir()) == []
    assert rejected
    assert _lstat_identity(unsafe_intermediate) == intermediate_identity


@pytest.mark.parametrize("writer", REPORT_WRITERS)
def test_report_writer_creates_missing_exact_leaf_private_and_usable(
    tmp_path: Path, writer,
) -> None:
    """Break caught: safe missing report leaves are rejected or created writable."""
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    report_dir = private_root / "reports"
    artifact = report_dir / "result.json"

    writer(artifact, {"status": "ok"})

    assert report_dir.stat().st_mode & 0o777 == 0o700
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"status": "ok"}


@pytest.mark.parametrize(
    ("writer", "error_type", "error_message"),
    REPORT_WRITER_FAILURES,
)
@pytest.mark.parametrize("parent_kind", ["symlink", "fifo"])
def test_report_writer_rejects_symlink_and_special_parents(
    tmp_path: Path, writer, error_type, error_message: str, parent_kind: str,
) -> None:
    """Break caught: publication traverses a symlink or non-directory parent."""
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    unsafe_parent = private_root / "unsafe-parent"
    actual = private_root / "actual"
    if parent_kind == "symlink":
        actual.mkdir(mode=0o700)
        unsafe_parent.symlink_to(actual, target_is_directory=True)
    else:
        os.mkfifo(unsafe_parent, mode=0o600)

    with pytest.raises(error_type) as caught:
        writer(unsafe_parent / "reports" / "result.json", {"status": "unsafe"})

    assert str(caught.value) == error_message
    if parent_kind == "symlink":
        assert list(actual.iterdir()) == []
    assert not (unsafe_parent / "reports").exists()


@pytest.mark.parametrize(
    ("writer", "error_type", "error_message"),
    REPORT_WRITER_FAILURES,
)
def test_report_writer_rejects_foreign_owned_parent_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer,
    error_type,
    error_message: str,
) -> None:
    """Break caught: publication accepts ancestry not owned by root or the caller."""
    foreign_parent = tmp_path / "foreign-parent"
    foreign_parent.mkdir(mode=0o700)
    original_mode = foreign_parent.stat().st_mode & 0o777
    monkeypatch.setattr(os, "getuid", lambda: foreign_parent.stat().st_uid + 1)

    with pytest.raises(error_type) as caught:
        writer(foreign_parent / "reports" / "result.json", {"status": "unsafe"})

    assert str(caught.value) == error_message
    assert foreign_parent.stat().st_mode & 0o777 == original_mode
    assert list(foreign_parent.iterdir()) == []
