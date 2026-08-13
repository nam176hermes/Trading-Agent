from __future__ import annotations

import re
import json
import tempfile
from pathlib import Path

import pytest

from scripts import t_g03_capability_topology as topology
import scripts.check_test_governance as test_governance
from scripts.check_test_governance import GovernanceError, audit_topology_root_records


ROOT = Path(__file__).resolve().parents[2]


def _make_targets(source: str) -> dict[str, tuple[str, ...]]:
    return {
        match.group(1): tuple(match.group(2).split())
        for match in re.finditer(
            r"^([A-Za-z0-9_-]+)[ \t]*:([^=\n]*)$", source, re.MULTILINE
        )
    }


def _reachable(targets: dict[str, tuple[str, ...]], root: str) -> set[str]:
    found: set[str] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(targets.get(current, ()))
    return found


def test_hosted_portable_route_uses_only_exact_topology_root_lanes() -> None:
    """Break caught: Foundation reintroduces generic root pytest below ci-portable."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets = _make_targets(makefile)

    assert targets["ci-portable-private"] == ()
    assert targets["test-all-portable-topology-private"] == (
        "audit-portable",
        "check-d0-closure",
        "check-contracts",
        "check-secrets",
        "test-backend",
        "test-dashboard",
        "typecheck-dashboard",
        "lint-dashboard",
        "ci-portable-topology",
    )

    portable_private = re.search(
        r"^ci-portable-private:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert portable_private is not None
    recipe = portable_private.group(1)
    assert recipe.splitlines() == [
        "\t$(MAKE) prepare-root-test-install",
        "\t$(MAKE) test-all-portable-topology-private check-test-governance-topology check-critical-coverage build-dashboard audit-python-source audit-dependencies",
    ]

    route = _reachable(targets, "test-all-portable-topology-private")
    assert not route & {
        "test",
        "test-portable-embedded-proof",
        "test-all-portable-private",
        "check-test-skips",
    }
    assert "ci-portable-topology" in route
    assert "check-test-governance-topology" in recipe

    topology_target = re.search(
        r"^check-test-governance-topology:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert topology_target is not None
    recipe = topology_target.group(1)
    assert "scripts/check_test_governance.py" in recipe
    assert "--topology-audit" in recipe
    assert '$(TEST_EVIDENCE_DIR)/test-governance-topology' in recipe
    assert '$(TEST_EVIDENCE_DIR)' in recipe
    assert 'tests/fixtures/t-g03a-hosted-failure-inventory.tsv' in recipe
    assert '"$$GITHUB_RUN_ID"' in recipe


def _write_topology_evidence(evidence: Path, *, malformed_root_record: bool = False) -> tuple[str, str]:
    run_id = "31641536482"
    head_sha = "18f22198c65c7bc735aeb848d8fda55209d01e78"
    inventory = ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv"
    rows = topology.load_inventory(inventory)
    topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head_sha)
    (evidence / "t-g03a-hosted-failure-inventory.tsv").write_bytes(inventory.read_bytes())
    topology_root = evidence / "capability-topology"
    for code in topology.CODE_CLASSIFICATION:
        lane, expected = topology._expected_rows(rows, code)
        state, outcome = {
            "portable-source": ("AVAILABLE", "PASS"),
            "native-capabilities": ("UNAVAILABLE", "DEFERRED"),
            "external-authorities": ("ABSENT", "DEFERRED"),
        }[lane]
        receipt = topology.make_receipt(
            run_id=run_id,
            head_sha=head_sha,
            lane=lane,
            code=code,
            expected=expected,
            collected=expected if outcome == "PASS" else (),
            state=state,
            fact="SOURCE_TEST_EXECUTED" if lane == "portable-source" else (
                "NATIVE_COMPONENT_ABSENT" if lane == "native-capabilities" else "AUTHORITY_ROOT_ABSENT"
            ),
            outcome=outcome,
        )
        (topology_root / f"{code}.json").write_bytes(topology.canonical_json_bytes(receipt))
        if outcome == "PASS":
            observed = list(expected)
            if malformed_root_record:
                observed[-1] = "tests/hidden.py::test_not_selected"
            (topology_root / f"{code}.governance.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "component": "root",
                        "pytest_exit_status": 0,
                        "tests": [
                            {
                                "test_node_id": node,
                                "component": "root",
                                "outcome": "passed",
                                "reason": "",
                                "phase": "call",
                            }
                            for node in observed
                        ],
                    }
                ),
                encoding="utf-8",
            )
    return run_id, head_sha


def test_topology_audit_discloses_deferred_receipts_without_claiming_pass() -> None:
    """Break caught: deferred runtime proofs become PASS or omit exact root evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw)
        run_id, head_sha = _write_topology_evidence(evidence)
        disclosure, root_records = audit_topology_root_records(
            evidence_root=evidence,
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            foundation_run_id=run_id,
            foundation_head_sha=head_sha,
        )

    assert disclosure == {
        "portable_source_status": "PASS",
        "native_capabilities_status": "DEFERRED",
        "external_authorities_status": "DEFERRED",
        "runtime_proof": "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS",
    }
    assert len(root_records) == 32
    assert {record["outcome"] for record in root_records} == {"passed"}


def test_topology_audit_rejects_a_root_record_that_does_not_match_its_receipt() -> None:
    """Break caught: a partial, extra, or unbound root lane record passes governance."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw)
        run_id, head_sha = _write_topology_evidence(evidence, malformed_root_record=True)
        with pytest.raises(GovernanceError, match="root topology governance"):
            audit_topology_root_records(
                evidence_root=evidence,
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                foundation_run_id=run_id,
                foundation_head_sha=head_sha,
            )


def test_topology_audit_never_reaches_the_generic_root_pytest_runner() -> None:
    """Break caught: topology governance calls run_suites and executes pytest tests broadly."""
    source = (ROOT / "scripts/check_test_governance.py").read_text(encoding="utf-8")
    topology_runner = re.search(
        r"^def run_topology_suites\(.+?(?=^def |\Z)", source, re.MULTILINE | re.DOTALL
    )

    assert topology_runner is not None
    body = topology_runner.group(0)
    assert "audit_topology_root_records" in body
    assert "run_suites(" not in body
    assert '"-m", "pytest", "-q", "-p", "scripts.test_governance_pytest", "tests"' not in body

    topology_source = (ROOT / "scripts/t_g03_capability_topology.py").read_text(encoding="utf-8")
    exact_runner = re.search(
        r"^def _run_exact\(.+?(?=^def |\Z)", topology_source, re.MULTILINE | re.DOTALL
    )
    assert exact_runner is not None
    assert "*nodes" in exact_runner.group(0)
    assert ', "tests"' not in exact_runner.group(0)


def test_topology_runner_merges_sealed_root_with_retained_component_governance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: topology mode drops legacy/dashboard governance after replacing root pytest."""
    with tempfile.TemporaryDirectory(dir="/tmp") as evidence_raw, tempfile.TemporaryDirectory(dir="/tmp") as report_raw:
        evidence = Path(evidence_raw)
        report_dir = Path(report_raw)
        run_id, head_sha = _write_topology_evidence(evidence)
        commands: list[tuple[str, ...]] = []

        def fake_run(command, *, env, **_kwargs):
            commands.append(tuple(command))
            Path(env["TEST_GOVERNANCE_REPORT"]).write_text(
                json.dumps({"tests": [{
                    "test_node_id": "legacy/tests/test_receipt.py::test_retained",
                    "component": "legacy",
                    "outcome": "passed",
                    "reason": "",
                    "phase": "call",
                }]}),
                encoding="utf-8",
            )
            return 0

        def fake_dashboard(directory: Path):
            report = directory / "dashboard-raw.json"
            report.write_text(
                json.dumps({"tests": [{
                    "test_node_id": "apps/dashboard/tests/policy.test.mjs::retained",
                    "component": "dashboard",
                    "outcome": "passed",
                    "reason": "",
                    "phase": "call",
                }]}),
                encoding="utf-8",
            )
            return 0, report

        monkeypatch.setattr(test_governance, "_run", fake_run)
        monkeypatch.setattr(test_governance, "_run_dashboard", fake_dashboard)
        records, exit_codes, disclosure = test_governance.run_topology_suites(
            report_dir,
            topology_evidence_root=evidence,
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            foundation_run_id=run_id,
            foundation_head_sha=head_sha,
        )

    assert commands == [("uv", "run", "--frozen", "--extra", "test", "pytest", "-q", "-p", "scripts.test_governance_pytest")]
    assert exit_codes == {"legacy": 0, "dashboard": 0}
    assert disclosure["runtime_proof"] == "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS"
    assert {str(record["component"]) for record in records} == {"root", "legacy", "dashboard"}
