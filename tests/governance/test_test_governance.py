from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from scripts.check_critical_coverage import (
    CoverageGateError,
    _evaluate_required_cases,
    evaluate_ratio,
)
from scripts.check_test_governance import (
    GovernanceError,
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
}


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
    governed = {item["test_node_id"]: item for item in report["tests"]}
    assert governed[postgres["test_node_id"]]["raw_outcome"] == "skipped"
    assert governed[postgres["test_node_id"]]["governed_outcome"] == "approval_blocked"
    assert governed[host["test_node_id"]]["governed_outcome"] == "deselected"


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
    dashboard_log = tmp_path / "dashboard.tap"
    dashboard_log.write_text(
        "# Subtest: rejects unsafe origin\nnot ok 1 - rejects unsafe origin\n",
        encoding="utf-8",
    )
    cases = [
        {
            "case": "Python fail-closed proof",
            "tests": ["tests/jobs/test_safety.py::test_gate"],
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
        _evaluate_required_cases(cases, failed_report, dashboard_log)

    dashboard_log.write_text(
        "# Subtest: rejects unsafe origin\nok 1 - rejects unsafe origin\n",
        encoding="utf-8",
    )
    passed_report = deepcopy(failed_report)
    for item in passed_report["tests"]:
        item["outcome"] = "passed"

    results = _evaluate_required_cases(cases, passed_report, dashboard_log)

    assert [result["status"] for result in results] == ["pass", "pass"]
