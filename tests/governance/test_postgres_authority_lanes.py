"""Owner merge of the disposable PostgreSQL authority lanes into one check-test-skips inventory."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

import scripts.check_test_governance as governance
from scripts.check_test_governance import GovernanceError

ROOT = Path(__file__).resolve().parents[2]
ABSENT = "explicit disposable PostgreSQL authority is not present"
SQL = "ops/postgres/job-plane-authority/acl-repair-v1.sql"
SOURCE = {"commit_sha": "a" * 40, "tree_sha": "b" * 40}


def _op(operation_id, scope, red_sql_path=None):
    return {"operation_id": operation_id, "scope": scope, "red_sql_path": red_sql_path}


def _plan():
    return {
        "schema_version": "postgres-authority-lanes/v1",
        "lanes": [
            {"lane_id": "green", "scope": "DISPOSABLE_PG_GREEN", "red_sql_operation": None, "red_sql_path": None},
            {"lane_id": "red-catalog", "scope": "DISPOSABLE_PG_RED",
             "red_sql_operation": "catalog-derivation-red-v1", "red_sql_path": SQL},
            {"lane_id": "red-evidence", "scope": "DISPOSABLE_PG_RED",
             "red_sql_operation": "evidence-derivation-red-v1", "red_sql_path": SQL},
        ],
        "nodes": [
            {"test_node_id": "tests/jobs/test_a.py::test_green", "lane_id": "green",
             "operations": [_op("green-v1", "DISPOSABLE_PG_GREEN")]},
            {"test_node_id": "tests/jobs/test_a.py::test_any", "lane_id": "green",
             "operations": [_op("any-v1", "GREEN_OR_RED")]},
            {"test_node_id": "tests/jobs/test_b.py::test_catalog", "lane_id": "red-catalog",
             "operations": [_op("catalog-red-v1", "DISPOSABLE_PG_RED")]},
            {"test_node_id": "tests/jobs/test_b.py::test_derivation", "lane_id": "red-catalog",
             "operations": [_op("catalog-derivation-red-v1", "DISPOSABLE_PG_RED", SQL)]},
            {"test_node_id": "tests/jobs/test_b.py::test_evidence", "lane_id": "red-evidence",
             "operations": [_op("evidence-capture-red-v1", "DISPOSABLE_PG_RED"),
                            _op("evidence-derivation-red-v1", "DISPOSABLE_PG_RED", SQL)]},
        ],
    }


def _record(node_id, outcome, *, reason="", phase="call"):
    return {"test_node_id": node_id, "component": "root", "outcome": outcome, "reason": reason, "phase": phase}


def _base(plan):
    return [_record("tests/unit/test_plain.py::test_plain", "passed")] + [
        _record(node["test_node_id"], "skipped", reason=ABSENT, phase="setup") for node in plan["nodes"]
    ]


def _results(plan):
    results = {}
    for lane in plan["lanes"]:
        tests = [_record(node["test_node_id"], "passed") for node in plan["nodes"] if node["lane_id"] == lane["lane_id"]]
        results[lane["lane_id"]] = {
            "exit_code": 0,
            "report": {"schema_version": 1, "component": "root", "collection_only": False,
                       "pytest_exit_status": 0, "tests": tests},
            "source_before": dict(SOURCE),
            "source_after": dict(SOURCE),
            "approval_scope": lane["scope"],
            "approval_red_sql_operation": lane["red_sql_operation"],
            "new_cluster_roots": [],
        }
    return results


def _merge(plan=None, base=None, results=None):
    plan = _plan() if plan is None else plan
    validated = governance.validate_postgres_lane_plan(plan)
    return governance.merge_postgres_lanes(
        _base(plan) if base is None else base, validated, _results(plan) if results is None else results,
        source=SOURCE,
    )


def _problems(**kwargs):
    with pytest.raises(GovernanceError) as error:
        _merge(**kwargs)
    return str(error.value)


def test_all_lanes_passing_replace_exactly_the_planned_authority_skips():
    merged = _merge()
    by_node = {item["test_node_id"]: item for item in merged}
    assert len(merged) == 6
    assert {item["outcome"] for item in merged} == {"passed"}
    assert by_node["tests/jobs/test_b.py::test_evidence"]["postgres_lane"] == "red-evidence"
    assert "postgres_lane" not in by_node["tests/unit/test_plain.py::test_plain"]
    governance.compare_inventory(merged, [])                     # nothing left to allowlist


def test_a_missing_lane_is_missing_proof():
    results = _results(_plan())
    del results["red-evidence"]
    assert "lane red-evidence has no result" in _problems(results=results)


def test_a_missing_report_is_missing_proof():
    results = _results(_plan())
    results["green"]["report"] = None
    assert "lane green has no raw report" in _problems(results=results)


def test_a_planned_node_missing_from_its_lane_is_missing_proof():
    results = _results(_plan())
    results["red-catalog"]["report"]["tests"].pop()
    assert "lane red-catalog did not observe root::tests/jobs/test_b.py::test_derivation" in _problems(results=results)


def test_a_node_outside_the_lane_assignment_is_rejected():
    results = _results(_plan())
    results["green"]["report"]["tests"].append(_record("tests/jobs/test_b.py::test_catalog", "passed"))
    assert "lane green observed unassigned root::tests/jobs/test_b.py::test_catalog" in _problems(results=results)


@pytest.mark.parametrize("phase", ["collection", "setup", "call"])
def test_a_skip_inside_the_assigned_lane_is_not_proof(phase):
    results = _results(_plan())
    results["green"]["report"]["tests"][0] = _record(
        "tests/jobs/test_a.py::test_green", "skipped", reason="approval record belongs to other scope", phase=phase)
    assert "lane green root::tests/jobs/test_a.py::test_green is skipped in " + phase in _problems(results=results)


@pytest.mark.parametrize("phase", ["collection", "setup", "call", "teardown"])
def test_failures_in_every_phase_are_kept(phase):
    results = _results(_plan())
    results["green"]["exit_code"] = 1
    results["green"]["report"]["pytest_exit_status"] = 1
    results["green"]["report"]["tests"][1] = _record(
        "tests/jobs/test_a.py::test_any", "failed", reason=f"pytest {phase} failure", phase=phase)
    problems = _problems(results=results)
    assert "lane green root::tests/jobs/test_a.py::test_any is failed in " + phase in problems
    assert "lane green exited 1" in problems


def test_a_signal_is_kept_even_when_every_record_passed():
    results = _results(_plan())
    results["red-catalog"]["exit_code"] = -9
    assert "lane red-catalog exited -9" in _problems(results=results)


def test_a_report_exit_status_that_contradicts_the_process_is_rejected():
    results = _results(_plan())
    results["green"]["report"]["pytest_exit_status"] = 1
    assert "lane green report exit status 1 differs from process exit 0" in _problems(results=results)


def test_pass_and_fail_for_the_same_node_are_not_merged_away():
    results = _results(_plan())
    results["green"]["report"]["tests"].append(_record("tests/jobs/test_a.py::test_green", "failed", phase="teardown"))
    assert "lane green observed root::tests/jobs/test_a.py::test_green more than once" in _problems(results=results)


def test_a_planned_node_that_did_not_skip_in_the_base_run_is_contradictory():
    plan = _plan()
    base = _base(plan)
    base[1] = _record("tests/jobs/test_a.py::test_green", "passed")
    assert ("base run observed root::tests/jobs/test_a.py::test_green as passed in call, "
            "not the authority-absent skip") in _problems(base=base)


def test_a_routing_exclusion_is_only_recognized_for_planned_nodes():
    plan = _plan()
    base = _base(plan) + [_record("tests/jobs/test_c.py::test_unplanned", "skipped", reason=ABSENT, phase="setup")]
    merged = _merge(base=base)
    with pytest.raises(GovernanceError, match="new unapproved skips/deselections: root::tests/jobs/test_c.py"):
        governance.compare_inventory(merged, [])


def test_every_problem_is_reported_not_only_the_first():
    results = _results(_plan())
    results["green"]["exit_code"] = 2
    results["red-evidence"]["report"] = None
    problems = _problems(results=results)
    assert "lane green exited 2" in problems and "lane red-evidence has no raw report" in problems


def test_a_lane_on_another_source_is_rejected():
    results = _results(_plan())
    results["red-catalog"]["source_after"] = {"commit_sha": "c" * 40, "tree_sha": "b" * 40}
    assert "lane red-catalog did not run on the candidate source" in _problems(results=results)


def test_a_lane_with_an_approval_for_another_scope_is_rejected():
    results = _results(_plan())
    results["green"]["approval_scope"] = "DISPOSABLE_PG_RED"
    assert "lane green approval scope DISPOSABLE_PG_RED differs from DISPOSABLE_PG_GREEN" in _problems(results=results)


def test_a_lane_whose_approval_binds_another_red_operation_is_rejected():
    results = _results(_plan())
    results["red-evidence"]["approval_red_sql_operation"] = "catalog-derivation-red-v1"
    assert "lane red-evidence approval binds catalog-derivation-red-v1" in _problems(results=results)


def test_a_lane_that_leaves_a_disposable_cluster_root_is_rejected():
    results = _results(_plan())
    results["green"]["new_cluster_roots"] = ["/tmp/phase4-postgres-leftover"]
    assert "lane green left disposable cluster roots: /tmp/phase4-postgres-leftover" in _problems(results=results)


def test_two_red_operations_needing_different_bindings_cannot_share_a_lane():
    plan = _plan()
    plan["nodes"][4]["lane_id"] = "red-catalog"
    with pytest.raises(GovernanceError, match="lane red-catalog needs RED SQL bindings for "
                                              "catalog-derivation-red-v1, evidence-derivation-red-v1"):
        governance.validate_postgres_lane_plan(plan)


def test_a_lane_binding_that_no_node_uses_is_rejected():
    plan = _plan()
    plan["nodes"] = [node for node in plan["nodes"] if node["lane_id"] != "red-evidence"]
    with pytest.raises(GovernanceError, match="lane red-evidence needs RED SQL bindings for none"):
        governance.validate_postgres_lane_plan(plan)


@pytest.mark.parametrize("lane_id", ["red-catalog", "green"])
def test_a_node_in_a_lane_of_the_wrong_scope_is_rejected(lane_id):
    plan = _plan()
    plan["nodes"][0 if lane_id == "red-catalog" else 2]["lane_id"] = lane_id
    with pytest.raises(GovernanceError, match="requires .* but lane " + lane_id):
        governance.validate_postgres_lane_plan(plan)


@pytest.mark.parametrize("mutate, message", [
    (lambda plan: plan["nodes"].append(deepcopy(plan["nodes"][0])), "assigned more than once"),
    (lambda plan: plan["lanes"].append(deepcopy(plan["lanes"][0])), "lane ids must be unique"),
    (lambda plan: plan["nodes"][0].update(lane_id="nowhere"), "unknown lane nowhere"),
    (lambda plan: plan["lanes"][0].update(red_sql_operation="x-v1", red_sql_path=SQL), "GREEN lane green cannot carry a RED SQL binding"),
    (lambda plan: plan.update(extra=1), "lane plan fields are not exact"),
])
def test_the_lane_plan_is_exact(mutate, message):
    plan = _plan()
    mutate(plan)
    with pytest.raises(GovernanceError, match=message):
        governance.validate_postgres_lane_plan(plan)


def test_the_committed_lane_plan_is_valid_and_covers_the_251_authority_nodes():
    plan = governance.validate_postgres_lane_plan(
        json.loads((ROOT / governance.DEFAULT_POSTGRES_LANE_PLAN).read_text(encoding="utf-8")))
    lanes = {}
    for node in plan["nodes"]:
        lanes[node["lane_id"]] = lanes.get(node["lane_id"], 0) + 1
    assert lanes == {"green": 206, "red-catalog": 44, "red-evidence": 1}


def _write_record(path, scope, red_operation):
    binding = None if red_operation is None else {"operation_id": red_operation, "sql_path": SQL, "sql_sha256": "0" * 64}
    path.write_text(json.dumps({"scope": scope, "red_sql_binding": binding}))
    path.chmod(0o600)


def _lane_inputs(tmp_path, plan, **overrides):
    inputs = {}
    for lane in plan["lanes"]:
        record = tmp_path / f"{lane['lane_id']}-record.json"
        _write_record(record, lane["scope"], lane["red_sql_operation"])
        inputs[lane["lane_id"]] = {"TRADING_TEST_DISPOSABLE_APPROVAL_RECORD": str(record),
                                   "TRADING_TEST_DISPOSABLE_FIXTURE_PLAN": str(tmp_path / f"{lane['lane_id']}-plan.json")}
    inputs.update(overrides)
    path = tmp_path / "lane-inputs.json"
    path.write_text(json.dumps(inputs))
    return path


@pytest.mark.parametrize("mutate, message", [
    (lambda inputs: inputs.pop("red-evidence"), "lane inputs must name exactly the planned lanes"),
    (lambda inputs: inputs.update(extra={}), "lane inputs must name exactly the planned lanes"),
    (lambda inputs: inputs["green"].update(TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE="DISPOSABLE_PG_RED"),
     "lane green inputs are not exact"),
    (lambda inputs: inputs["green"].pop("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"), "lane green inputs are not exact"),
    (lambda inputs: inputs["green"].pop("TRADING_TEST_DISPOSABLE_FIXTURE_PLAN"), "lane green inputs are not exact"),
    (lambda inputs: inputs["green"].update(TRADING_TEST_DISPOSABLE_FIXTURE_PLAN_REQUIRED="NO"),
     "lane green inputs are not exact"),
    (lambda inputs: inputs["green"].update(TRADING_TEST_DISPOSABLE_FIXTURE_PLAN="relative.json"),
     "lane green inputs are not exact"),
    (lambda inputs: inputs["green"].update(TRADING_TEST_DISPOSABLE_FIXTURE_PLAN=str(ROOT / "plan.json")),
     "lane green inputs are not exact"),
])
def test_lane_inputs_are_exact_external_paths(tmp_path, mutate, message):
    plan = _plan()
    path = _lane_inputs(tmp_path, plan)
    inputs = json.loads(path.read_text())
    mutate(inputs)
    path.write_text(json.dumps(inputs))
    with pytest.raises(GovernanceError, match=message):
        governance.load_postgres_lane_inputs(path, plan)


def test_lane_inputs_must_live_outside_the_checkout(tmp_path, monkeypatch):
    plan = _plan()
    path = _lane_inputs(tmp_path, plan)
    monkeypatch.setattr(governance, "ROOT", tmp_path)
    with pytest.raises(GovernanceError, match="lane inputs must remain outside the source checkout"):
        governance.load_postgres_lane_inputs(path, plan)


def test_the_lane_environment_is_built_only_from_the_plan_and_its_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE", "DISPOSABLE_PG_GREEN")
    monkeypatch.setenv("TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR", "/tmp/inherited")
    monkeypatch.setenv("LIVE_TRADING_APPROVED", "true")
    lane = _plan()["lanes"][1]
    env = governance._postgres_lane_environment(
        lane, {"TRADING_TEST_DISPOSABLE_APPROVAL_RECORD": "/tmp/r.json"}, tmp_path / "raw.json")
    assert env["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"] == "DISPOSABLE_PG_RED"
    assert env["TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES"] == "YES"
    assert env["TRADING_TEST_DISPOSABLE_FIXTURE_PLAN_REQUIRED"] == "YES"
    assert env["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"] == "/tmp/r.json"
    assert "TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR" not in env
    assert env["LIVE_TRADING_APPROVED"] == "false" and env["TEST_GOVERNANCE_COMPONENT"] == "root"


@pytest.mark.parametrize("name", ["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE", "TRADING_TEST_DISPOSABLE_FIXTURE_PLAN_REQUIRED"])
def test_lane_mode_refuses_a_base_run_that_inherits_authority(monkeypatch, name):
    monkeypatch.setenv(name, "YES")
    with pytest.raises(GovernanceError, match="base run must not inherit disposable PostgreSQL authority"):
        governance._require_authority_free_base()


def _fake_runner(monkeypatch, outcomes, roots_after=(), *, no_report=(), source_change=()):
    """Stand in for pytest: write the raw report the plugin would write, return the scripted exit status."""
    calls = []
    roots: set[str] = set()                                   # the /tmp/phase4-postgres-* roots that exist now
    source = dict(SOURCE)

    def run(command, *, cwd, env, log_path):
        calls.append((command, env))
        nodes = [arg for arg in command if "::" in arg]
        lane_id = env["TEST_GOVERNANCE_REPORT"].rsplit("root-postgres-", 1)[1].removesuffix("-raw.json")
        outcome, code = outcomes.get(lane_id, ("passed", 0))
        tests = [_record(node, outcome, phase="call") for node in nodes]
        if lane_id not in no_report:
            Path(env["TEST_GOVERNANCE_REPORT"]).write_text(json.dumps(
                {"schema_version": 1, "component": "root", "collection_only": False,
                 "pytest_exit_status": code, "tests": tests}))
        log_path.write_text("log")
        if lane_id == "green":
            roots.update(roots_after)
        if lane_id in source_change:
            source["tree_sha"] = "d" * 40
        return code

    monkeypatch.setattr(governance, "_run", run)
    monkeypatch.setattr(governance, "_candidate_source", lambda: dict(source))
    monkeypatch.setattr(governance, "_disposable_cluster_roots", lambda: set(roots))
    return calls


def _run_lanes(tmp_path, plan):
    inputs = governance.load_postgres_lane_inputs(_lane_inputs(tmp_path, plan), plan)
    return governance.run_postgres_lanes(tmp_path / "reports", plan, inputs, source=SOURCE)


@pytest.mark.parametrize("stop, kwargs, message", [
    ("signal", dict(outcomes={"green": ("passed", -9)}), "lane green exited -9"),
    ("failure", dict(outcomes={"green": ("failed", 1)}), "lane green exited 1"),
    ("no report", dict(outcomes={}, no_report=("green",)), "lane green has no raw report"),
    ("leftover root", dict(outcomes={}, roots_after=["/tmp/phase4-postgres-leftover"]),
     "lane green left disposable cluster roots"),
    ("source change", dict(outcomes={}, source_change=("green",)), "lane green did not run on the candidate source"),
])
def test_the_runner_stops_after_a_lane_that_is_not_clean_and_records_the_rest_as_not_started(
        tmp_path, monkeypatch, stop, kwargs, message):
    plan = _plan()
    outcomes = kwargs.pop("outcomes")
    calls = _fake_runner(monkeypatch, outcomes, **kwargs)
    results, exit_codes = _run_lanes(tmp_path, plan)
    assert len(calls) == 1                                        # no further PostgreSQL lane was launched
    assert list(exit_codes) == ["root-postgres-green"]
    assert (tmp_path / "reports" / "root-postgres-green.log").is_file()
    for lane_id in ("red-catalog", "red-evidence"):
        assert results[lane_id] == {"not_started": "lane green stopped the run"}
    with pytest.raises(GovernanceError) as error:
        governance.merge_postgres_lanes(_base(plan), plan, results, source=SOURCE)
    assert message in str(error.value)
    assert "lane red-catalog was not started: lane green stopped the run" in str(error.value)
    assert "lane red-evidence was not started: lane green stopped the run" in str(error.value)


def test_each_lane_runs_exactly_its_nodes_and_keeps_raw_evidence(tmp_path, monkeypatch):
    plan = _plan()
    calls = _fake_runner(monkeypatch, {"red-evidence": ("failed", 1)})
    results, exit_codes = _run_lanes(tmp_path, plan)
    assert exit_codes == {"root-postgres-green": 0, "root-postgres-red-catalog": 0, "root-postgres-red-evidence": 1}
    commands = {env["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"] + ":" + ",".join(a for a in command if "::" in a)
                for command, env in calls}
    assert "DISPOSABLE_PG_RED:tests/jobs/test_b.py::test_evidence" in commands
    assert all(governance.ROOT_PYTEST_MARKERS in command and "tests" not in command for command, _ in calls)
    for lane_id in ("green", "red-catalog", "red-evidence"):
        assert (tmp_path / "reports" / f"root-postgres-{lane_id}-raw.json").is_file()
        assert (tmp_path / "reports" / f"root-postgres-{lane_id}.log").is_file()
    assert results["red-evidence"]["approval_red_sql_operation"] == "evidence-derivation-red-v1"
    with pytest.raises(GovernanceError, match="lane red-evidence exited 1.*is failed in call"):
        governance.merge_postgres_lanes(_base(plan), plan, results, source=SOURCE)


def test_a_cluster_root_left_by_a_lane_is_observed(tmp_path, monkeypatch):
    plan = _plan()
    _fake_runner(monkeypatch, {}, roots_after=["/tmp/phase4-postgres-leftover"])
    results, _ = _run_lanes(tmp_path, plan)
    assert results["green"]["new_cluster_roots"] == ["/tmp/phase4-postgres-leftover"]


def test_an_unreadable_approval_record_is_reported_not_trusted(tmp_path, monkeypatch):
    plan = _plan()
    _fake_runner(monkeypatch, {})
    inputs = governance.load_postgres_lane_inputs(_lane_inputs(tmp_path, plan), plan)
    Path(inputs["green"]["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"]).chmod(0o644)
    results, _ = governance.run_postgres_lanes(tmp_path / "reports", plan, inputs, source=SOURCE)
    assert results["green"]["approval_scope"] is None


def _main_with_lanes(tmp_path, monkeypatch, results):
    plan = _plan()
    monkeypatch.setattr(governance, "validate_postgres_lane_plan", lambda document: plan)
    monkeypatch.setattr(governance, "_read_json", lambda path: plan if path == governance.DEFAULT_POSTGRES_LANE_PLAN
                        else json.loads(Path(path).read_text()))
    monkeypatch.setattr(governance, "run_suites", lambda report_dir: (_base(plan), {"root": 0, "legacy": 0, "dashboard": 0}))
    monkeypatch.setattr(governance, "run_postgres_lanes", lambda report_dir, plan, inputs, *, source: (
        results, {f"root-postgres-{lane_id}": result["exit_code"] for lane_id, result in results.items()}))
    monkeypatch.setattr(governance, "_candidate_source", lambda: dict(SOURCE))
    monkeypatch.setattr(governance, "_load_allowlist", lambda path, today: [])
    for name in governance.POSTGRES_AUTHORITY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    report_dir = tmp_path / "reports"
    code = governance.main(["--report-dir", str(report_dir), "--postgres-lanes", str(_lane_inputs(tmp_path, plan))])
    return code, report_dir


def test_one_check_test_skips_entrypoint_merges_the_lanes_into_a_pass(tmp_path, monkeypatch):
    code, report_dir = _main_with_lanes(tmp_path, monkeypatch, _results(_plan()))
    report = json.loads((report_dir / "test-governance.json").read_text())
    assert code == 0 and report["status"] == "pass"
    assert report["summary"]["passed"] == 6 and report["summary"]["skipped"] == 0
    assert report["suite_exit_codes"]["root-postgres-red-evidence"] == 0


def test_a_failing_lane_fails_the_entrypoint_and_keeps_its_exit_status(tmp_path, monkeypatch):
    results = _results(_plan())
    results["red-catalog"]["exit_code"] = -15
    code, report_dir = _main_with_lanes(tmp_path, monkeypatch, results)
    error = json.loads((report_dir / "test-governance-error.json").read_text())
    assert code == 1 and error["suite_exit_codes"]["root-postgres-red-catalog"] == -15
    assert "lane red-catalog exited -15" in error["error"]


def test_postgres_lanes_are_not_part_of_the_topology_audit(tmp_path):
    assert governance.main(["--report-dir", str(tmp_path / "r"), "--topology-audit",
                            "--postgres-lanes", str(tmp_path / "inputs.json")]) == 1
    assert "postgres lanes are not part of the topology audit" in (tmp_path / "r" / "test-governance-error.json").read_text()


OBSERVED_ABSENT_REASONS = {                                     # the 251 base-run skips on 57c2b84 (R7 round 3)
    "explicit disposable PostgreSQL authority is not present",
    "exact disposable PostgreSQL authority is not present",
    "exact disposable PostgreSQL GREEN authority is not present",
    "exact disposable PostgreSQL RED authority is not present",
    "exact disposable PostgreSQL RED evidence authority is not present",
    "exact disposable PostgreSQL GREEN restore authority is absent",
}


@pytest.mark.parametrize("reason", sorted(OBSERVED_ABSENT_REASONS))
def test_every_observed_authority_absent_reason_is_a_routing_exclusion(reason):
    plan = _plan()
    base = _base(plan)
    base[1] = _record("tests/jobs/test_a.py::test_green", "skipped", reason=reason, phase="call")
    assert len(_merge(base=base)) == 6


def test_a_base_skip_for_another_reason_is_not_a_routing_exclusion():
    plan = _plan()
    base = _base(plan)
    base[1] = _record("tests/jobs/test_a.py::test_green", "skipped", reason="PostgreSQL 16 test binaries are unavailable")
    assert "base run observed root::tests/jobs/test_a.py::test_green as skipped" in _problems(base=base)


@pytest.mark.parametrize("mutate, message", [
    (lambda base: base.append(_record("tests/jobs/test_a.py::test_green", "skipped", reason=ABSENT, phase="setup")),
     "base run observed root::tests/jobs/test_a.py::test_green more than once"),
    (lambda base: base.__setitem__(1, _record("tests/jobs/test_a.py::test_green", "skipped", reason=ABSENT,
                                                phase="collection")),
     "base run observed root::tests/jobs/test_a.py::test_green as skipped in collection"),
    (lambda base: base[1].pop("phase"), "base run record 1 is invalid"),
    (lambda base: base[1].update(component="dashboard"), "base run did not observe root::tests/jobs/test_a.py::test_green"),
])
def test_base_records_for_planned_nodes_are_validated_before_they_are_replaced(mutate, message):
    base = _base(_plan())
    mutate(base)
    assert message in _problems(base=base)


# --- lane selection without touching the protected Makefile: CLI or TEST_GOVERNANCE_POSTGRES_LANES, never both apart ---
LANES_ENV = "TEST_GOVERNANCE_POSTGRES_LANES"


@pytest.mark.parametrize(("cli", "env", "expected"), [
    (None, None, None),
    (None, "/x/inputs.json", Path("/x/inputs.json")),
    (Path("/x/inputs.json"), None, Path("/x/inputs.json")),
    (Path("/x/inputs.json"), "/x/inputs.json", Path("/x/inputs.json")),
])
def test_lane_selection_resolves_cli_and_environment(monkeypatch, cli, env, expected):
    if env is None:
        monkeypatch.delenv(LANES_ENV, raising=False)
    else:
        monkeypatch.setenv(LANES_ENV, env)
    assert governance.resolve_postgres_lanes(cli) == expected
    assert LANES_ENV not in os.environ                      # taken, never passed down to a suite or lane


@pytest.mark.parametrize(("cli", "env", "message"), [
    (Path("/x/a.json"), "/x/b.json", "differ"),
    (None, "", "set but empty"),
    (Path("/x/a.json"), "", "set but empty"),
])
def test_conflicting_or_empty_lane_selection_is_an_error(monkeypatch, cli, env, message):
    monkeypatch.setenv(LANES_ENV, env)
    with pytest.raises(GovernanceError, match=message):
        governance.resolve_postgres_lanes(cli)
    assert LANES_ENV not in os.environ


def test_a_conflicting_selection_fails_before_any_suite_runs(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(governance, "run_suites", lambda report_dir: ran.append("suites") or ([], {}))
    monkeypatch.setenv(LANES_ENV, str(tmp_path / "b.json"))
    code = governance.main(["--report-dir", str(tmp_path / "r"), "--postgres-lanes", str(tmp_path / "a.json")])
    assert code == 1 and ran == []
    assert "differ" in (tmp_path / "r" / "test-governance-error.json").read_text()


def test_the_environment_selects_lanes_and_no_child_inherits_it(tmp_path, monkeypatch):
    plan = _plan()
    inputs = _lane_inputs(tmp_path, plan)
    seen = {}
    monkeypatch.setattr(governance, "validate_postgres_lane_plan", lambda document: plan)
    monkeypatch.setattr(governance, "_read_json", lambda path: plan if path == governance.DEFAULT_POSTGRES_LANE_PLAN
                        else json.loads(Path(path).read_text()))

    def suites(report_dir):
        seen["suite_os_environ"] = LANES_ENV in os.environ
        seen["pytest_child"] = LANES_ENV in governance._pytest_environment("root", tmp_path / "raw.json")
        return _base(plan), {"root": 0, "legacy": 0, "dashboard": 0}

    def lanes(report_dir, plan_, lane_inputs, *, source):
        seen["inputs"] = sorted(lane_inputs)
        seen["lane_child"] = LANES_ENV in governance._postgres_lane_environment(
            plan["lanes"][0], lane_inputs["green"], tmp_path / "lane.json")
        results = _results(plan)
        return results, {f"root-postgres-{lane}": 0 for lane in results}

    monkeypatch.setattr(governance, "run_suites", suites)
    monkeypatch.setattr(governance, "run_postgres_lanes", lanes)
    monkeypatch.setattr(governance, "_candidate_source", lambda: dict(SOURCE))
    monkeypatch.setattr(governance, "_load_allowlist", lambda path, today: [])
    for name in governance.POSTGRES_AUTHORITY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(LANES_ENV, str(inputs))
    assert governance.main(["--report-dir", str(tmp_path / "reports")]) == 0
    assert seen == {"suite_os_environ": False, "pytest_child": False, "inputs": ["green", "red-catalog", "red-evidence"],
                    "lane_child": False}


def test_the_environment_selection_is_not_part_of_the_topology_audit(tmp_path, monkeypatch):
    monkeypatch.setenv(LANES_ENV, str(tmp_path / "inputs.json"))
    assert governance.main(["--report-dir", str(tmp_path / "r"), "--topology-audit"]) == 1
    assert "postgres lanes are not part of the topology audit" in (tmp_path / "r" / "test-governance-error.json").read_text()
