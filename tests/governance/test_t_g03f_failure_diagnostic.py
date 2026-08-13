from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts import t_g03_capability_topology as topology


INVENTORY = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")


def _seal_remainder(monkeypatch: pytest.MonkeyPatch, evidence: Path, raw: str) -> tuple[str, str, tuple[str, ...]]:
    run_id = "31641536482"
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    extension = Path(raw) / "custody.so"
    extension.write_bytes(b"T-G03F custody fixture")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", topology.hashlib.sha256(extension.read_bytes()).hexdigest())
    rows = topology.load_inventory(INVENTORY)
    candidates = tuple(sorted([*(row.node_id for row in rows), "tests/ordinary/test_failure.py::test_failure"]))
    topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head_sha)
    topology.collect_portable_root_baseline(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        collector=lambda: candidates,
    )
    remainder = topology.prepare_portable_root_remainder(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
    )
    return run_id, head_sha, tuple(remainder["remainder_node_ids"])


def test_complete_nonpass_remainder_publishes_redacted_diagnostic_only_after_custody_postcheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a complete failed exact run loses its node evidence or mints a PASS record."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, nodes = _seal_remainder(monkeypatch, evidence, raw)

        def failing_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1,
                "component": "root",
                "pytest_exit_status": 1,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": selected[0], "component": "root", "outcome": "failed",
                    "reason": "assertion failed", "phase": "call",
                }],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="EXACT_EXECUTION_NONPASS"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=failing_exact,
            )

        diagnostic = evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json"
        document = topology.read_failure_diagnostic(
            diagnostic, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        )
        assert document["diagnostic_only"] is True
        assert document["custody_postcheck_status"] == "PASS"
        assert document["pytest_exit_status"] == "1"
        assert document["observations"] == [{
            "test_node_id": nodes[0], "component": "root", "outcome": "failed", "phase": "call",
            "xfail_state": "NOT_WAS_XFAIL", "reason_class": "PYTEST_FAILURE_REASON",
            "reason_provenance": "PYTEST_REPORT",
            "normalized_reason_commitment_sha256": topology.reason_commitment_sha256("assertion failed"),
            "policy_match_result": "NOT_APPLICABLE", "existing_policy_entry_sha256": "",
        }]
        assert not (evidence / "capability-topology/portable-root-remainder.governance.json").exists()
        with pytest.raises(topology.TopologyError, match="failure diagnostic is present"):
            topology.reconcile_portable_root_accounting(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
            )


@pytest.mark.parametrize("outcome,phase,wasxfail,reason", [
    ("passed", "call", False, ""),
    ("skipped", "setup", False, "approved skip"),
    ("skipped", "call", False, "approved skip"),
    ("skipped", "teardown", False, "approved skip"),
    ("xfailed", "call", True, "expected failure"),
    ("xpassed", "call", True, "unexpected pass"),
    ("deselected", "collection", False, "marker deselected"),
    ("failed", "collection", False, "collection failure"),
    ("failed", "call", False, "assertion failed"),
    ("error", "setup", False, "fixture error"),
    ("error", "teardown", False, "fixture error"),
    ("error", "collection", False, "collector error"),
    ("not_run", "session", False, "session incomplete"),
])
def test_raw_observation_mapping_accepts_only_the_closed_v1_domain(
    outcome: str, phase: str, wasxfail: bool, reason: str,
) -> None:
    """Break caught: an unreviewed outcome/phase/xfail tuple enters diagnostic evidence."""
    item = {"outcome": outcome, "phase": phase, "wasxfail": wasxfail}
    mapped = topology._raw_observation_domain(item)
    assert mapped[0] in {"passed", "skipped", "xfailed", "xpassed", "deselected", "failed", "error", "not_run"}
    assert topology._reason_is_safe(reason)
    with pytest.raises(topology.TopologyError, match="closed domain"):
        topology._raw_observation_domain({"outcome": outcome, "phase": "report", "wasxfail": wasxfail})


def test_unsafe_or_duplicate_raw_failure_evidence_does_not_publish_a_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a secret/path-like reason or duplicate selected node becomes retained evidence."""
    for reason, tests in (
        ("token /private/secret", lambda node: [{"test_node_id": node, "component": "root", "outcome": "failed", "reason": "token /private/secret", "phase": "call"}]),
        ("assertion failed", lambda node: [
            {"test_node_id": node, "component": "root", "outcome": "failed", "reason": "assertion failed", "phase": "call"},
            {"test_node_id": node, "component": "root", "outcome": "failed", "reason": "assertion failed", "phase": "call"},
        ]),
    ):
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            evidence = Path(raw) / "evidence"
            run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw)

            def malformed_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
                report.write_text(json.dumps({
                    "schema_version": 1, "component": "root", "pytest_exit_status": 1,
                    "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                    "tests": tests(selected[0]),
                }), encoding="utf-8")
                return selected

            with pytest.raises(topology.TopologyError):
                topology.execute_portable_root_remainder(
                    inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                    exact_runner=malformed_exact,
                )
            assert not (evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json").exists(), reason
