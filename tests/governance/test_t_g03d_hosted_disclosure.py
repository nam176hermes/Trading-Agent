from __future__ import annotations

import re
import json
import shlex
import tempfile
import subprocess
import os
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


def _make_module_commands(source: str, module: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return every direct Make recipe command that launches one Python module."""
    target: str | None = None
    commands: list[tuple[str, tuple[str, ...]]] = []
    pending: tuple[str, str] | None = None
    target_pattern = re.compile(r"^([A-Za-z0-9_-]+)[ \t]*:(?:[^=\n]*)$")
    prefix = ("uv", "run", "python", "-m", module)
    invocation = " ".join(prefix)

    def record(command_target: str, command: str) -> None:
        command = command.removesuffix(";").rstrip()
        tokens = tuple(shlex.split(command))
        assert tokens[: len(prefix)] == prefix
        commands.append((command_target, tokens[len(prefix):]))

    for line in source.splitlines():
        match = target_pattern.match(line)
        if match is not None:
            assert pending is None
            target = match.group(1)
            continue
        if not line.startswith("\t"):
            continue
        fragment = line.lstrip("\t").strip()
        if pending is not None:
            command_target, command = pending
            fragment_without_continuation = fragment.removesuffix("\\").rstrip()
            command = f"{command} {fragment_without_continuation}"
            if fragment.endswith("\\"):
                pending = (command_target, command)
            else:
                record(command_target, command)
                pending = None
            continue
        if invocation not in fragment:
            continue
        assert target is not None
        command = fragment.removesuffix("\\").rstrip()
        if fragment.endswith("\\") and not command.endswith(";"):
            pending = (target, command)
        else:
            record(target, command)
    assert pending is None
    return commands


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
    assert "uv run python -m scripts.check_test_governance" in recipe
    assert "--topology-audit" in recipe
    assert '$(TEST_EVIDENCE_DIR)/test-governance-topology' in recipe
    assert '$(TEST_EVIDENCE_DIR)' in recipe
    assert 'tests/fixtures/t-g03a-hosted-failure-inventory.tsv' in recipe
    assert '"$$FOUNDATION_CONTEXT_PATH"' in recipe
    assert "--foundation-run-id" not in recipe
    assert "--foundation-head-sha" not in recipe
    topology_recipe = re.search(
        r"^ci-portable-topology:\n((?:\t.*\n)+)", makefile, re.MULTILINE,
    )
    assert topology_recipe is not None
    sequence = topology_recipe.group(1)
    assert sequence.index("$(MAKE) test-portable-root-remainder") < sequence.index("run-lane --lane portable-source")
    remainder_target = re.search(
        r"^test-portable-root-remainder:\n((?:\t.*\n)+)", makefile, re.MULTILINE,
    )
    assert remainder_target is not None
    remainder_sequence = remainder_target.group(1)
    assert remainder_sequence.index("collect-baseline") < remainder_sequence.index("prepare-remainder")
    assert remainder_sequence.index("prepare-remainder") < remainder_sequence.index("run-remainder")
    portable_source_target = re.search(
        r"^test-portable-source:\n((?:\t.*\n)+)", makefile, re.MULTILINE,
    )
    assert portable_source_target is not None
    portable_source_sequence = portable_source_target.group(1)
    assert "native/package6_custodian" in portable_source_sequence
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_PATH" in portable_source_sequence
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256" in portable_source_sequence
    assert portable_source_sequence.index("collect-baseline") < portable_source_sequence.index("run-lane --lane portable-source")


def test_t_g03_make_launches_use_the_complete_canonical_module_contract() -> None:
    """Break caught: a T-G03 Make launch bypasses the package module boundary."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    topology_commands = _make_module_commands(
        makefile, "scripts.t_g03_capability_topology",
    )
    governance_commands = _make_module_commands(
        makefile, "scripts.check_test_governance",
    )

    evidence_root = "$(TEST_EVIDENCE_DIR)"
    context_path = "$$FOUNDATION_CONTEXT_PATH"
    topology_arguments = ("--evidence-root", evidence_root, "--foundation-context-path", context_path)
    assert topology_commands == [
        ("test-portable-source", ("reserve", *topology_arguments)),
        ("test-portable-source", ("collect-baseline", *topology_arguments)),
        ("test-portable-source", ("run-lane", "--lane", "portable-source", *topology_arguments)),
        ("test-native-capabilities", ("reserve", *topology_arguments)),
        ("test-native-capabilities", ("run-lane", "--lane", "native-capabilities", *topology_arguments)),
        ("test-external-authorities", ("reserve", *topology_arguments)),
        ("test-external-authorities", ("run-lane", "--lane", "external-authorities", *topology_arguments)),
        ("test-portable-root-remainder", ("collect-baseline", *topology_arguments)),
        ("test-portable-root-remainder", ("prepare-remainder", *topology_arguments)),
        ("test-portable-root-remainder", ("run-remainder", *topology_arguments)),
        ("ci-portable-topology", ("reserve", *topology_arguments)),
        ("ci-portable-topology", ("run-lane", "--lane", "portable-source", *topology_arguments)),
        ("ci-portable-topology", ("run-lane", "--lane", "native-capabilities", *topology_arguments)),
        ("ci-portable-topology", ("run-lane", "--lane", "external-authorities", *topology_arguments)),
        ("ci-portable-topology", ("aggregate", *topology_arguments)),
    ]
    assert governance_commands == [
        ("check-test-skips", ("--report-dir", "$(TEST_EVIDENCE_DIR)/test-governance")),
        (
            "check-test-governance-topology",
            (
                "--topology-audit",
                "--report-dir", "$(TEST_EVIDENCE_DIR)/test-governance-topology",
                "--topology-evidence-root", evidence_root,
                "--inventory", "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                "--foundation-context-path", context_path,
            ),
        ),
    ]
    assert len(topology_commands) == 15
    assert len(governance_commands) == 2
    assert "uv run python scripts/t_g03_capability_topology.py" not in makefile
    assert "uv run python scripts/check_test_governance.py" not in makefile


def _write_topology_evidence(evidence: Path, *, malformed_root_record: bool = False) -> tuple[str, str]:
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    inventory = ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv"
    rows = topology.load_inventory(inventory)
    topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head_sha)
    (evidence / "t-g03a-hosted-failure-inventory.tsv").write_bytes(inventory.read_bytes())
    topology_root = evidence / "capability-topology"
    ordinary = "tests/ordinary/test_portable.py::test_ordinary"
    candidates = tuple(sorted([*(row.node_id for row in rows), ordinary]))
    candidate_bytes = topology._candidate_file_bytes(candidates)
    collection = {
        "schema_version": 1,
        "component": "root",
        "collection_only": True,
        "pytest_exit_status": 0,
        "tests": [{
            "test_node_id": node, "component": "root", "outcome": "collected", "reason": "", "phase": "collection",
        } for node in candidates],
    }
    collection_bytes = json.dumps(collection, sort_keys=True).encode("utf-8")
    (topology_root / "portable-root-collection.governance.json").write_bytes(collection_bytes)
    baseline: dict[str, object] = {
        "schema_version": topology.BASELINE_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "collector_policy": {
            **topology.PORTABLE_ROOT_POLICY,
            "native_custody_extension_identity": "1:2:1000:600:1",
            "native_custody_extension_sha256": "0" * 64,
        },
        "candidate_node_ids": list(candidates),
        "candidate_file_sha256": topology.hashlib.sha256(candidate_bytes).hexdigest(),
        "collection_report_sha256": topology.hashlib.sha256(collection_bytes).hexdigest(),
        "baseline_sha256": "",
    }
    baseline["baseline_sha256"] = topology._baseline_payload_sha256(baseline)
    (topology_root / "portable-root-candidates.txt").write_bytes(candidate_bytes)
    (topology_root / "portable-root-baseline.json").write_bytes(topology.canonical_json_bytes(baseline))
    remainder_bytes = topology._candidate_file_bytes((ordinary,))
    remainder: dict[str, object] = {
        "schema_version": topology.REMAINDER_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "baseline_sha256": baseline["baseline_sha256"],
        "remainder_node_ids": [ordinary],
        "remainder_file_sha256": topology.hashlib.sha256(remainder_bytes).hexdigest(),
        "remainder_sha256": "",
    }
    remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
    (topology_root / "portable-root-remainder.txt").write_bytes(remainder_bytes)
    (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))
    (topology_root / "portable-root-remainder.governance.json").write_text(
        json.dumps({"schema_version": 1, "component": "root", "pytest_exit_status": 0, "custody_policy": baseline["collector_policy"], "tests": [{
            "test_node_id": ordinary, "component": "root", "outcome": "passed", "reason": "", "phase": "call",
        }]}),
        encoding="utf-8",
    )
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
                        "custody_policy": baseline["collector_policy"],
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
    assert len(root_records) == 33
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


def test_dynamic_baseline_includes_a_new_ordinary_root_node_and_derives_the_exact_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: portable CI freezes the historical 62 IDs and loses a new root test."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    rows = topology.load_inventory(ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
    candidates = tuple(sorted([*(row.node_id for row in rows), "tests/ordinary/test_new.py::test_new"] ))
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"verified custody fixture")
        digest = topology.hashlib.sha256(extension.read_bytes()).hexdigest()
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", digest)
        topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head_sha)
        baseline = topology.collect_portable_root_baseline(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            collector=lambda: candidates,
        )
        remainder = topology.prepare_portable_root_remainder(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
        )

    assert baseline["candidate_node_ids"] == list(candidates)
    assert remainder["remainder_node_ids"] == ["tests/ordinary/test_new.py::test_new"]


def test_remainder_executor_uses_only_the_verified_generated_node_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the ordinary-root executor broad-runs a directory or replaces its generated list."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha = _write_topology_evidence(evidence)
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"custody")
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
        )
        selected: list[tuple[str, ...]] = []

        def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            selected.append(nodes)
            report.write_text(json.dumps({"schema_version": 1, "component": "root", "pytest_exit_status": 0, "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]), "tests": [{
                "test_node_id": node, "component": "root", "outcome": "passed", "reason": "", "phase": "call",
            } for node in nodes]}), encoding="utf-8")
            return nodes

        # Rebuild the sealed baseline with the real custody identity expected by the executor.
        topology_root = evidence / "capability-topology"
        baseline = json.loads((topology_root / "portable-root-baseline.json").read_text(encoding="utf-8"))
        baseline["collector_policy"] = topology._native_custody_policy()
        baseline["baseline_sha256"] = topology._baseline_payload_sha256(baseline)
        (topology_root / "portable-root-baseline.json").write_bytes(topology.canonical_json_bytes(baseline))
        remainder = json.loads((topology_root / "portable-root-remainder.json").read_text(encoding="utf-8"))
        remainder["baseline_sha256"] = baseline["baseline_sha256"]
        remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
        (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))
        (topology_root / "portable-root-remainder.governance.json").unlink()
        executed = topology.execute_portable_root_remainder(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            exact_runner=exact,
        )

    assert selected == [("tests/ordinary/test_portable.py::test_ordinary",)]
    assert executed == selected[0]


def test_extension_drift_after_remainder_blocks_the_next_pass_lane_and_closed_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a replaced custody extension permits a later green inventory lane."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha = _write_topology_evidence(evidence)
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"sealed custody")
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
        )
        topology_root = evidence / "capability-topology"
        baseline = json.loads((topology_root / "portable-root-baseline.json").read_text(encoding="utf-8"))
        baseline["collector_policy"] = topology._native_custody_policy()
        baseline["baseline_sha256"] = topology._baseline_payload_sha256(baseline)
        (topology_root / "portable-root-baseline.json").write_bytes(topology.canonical_json_bytes(baseline))
        remainder = json.loads((topology_root / "portable-root-remainder.json").read_text(encoding="utf-8"))
        remainder["baseline_sha256"] = baseline["baseline_sha256"]
        remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
        (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))
        for report in [
            topology_root / "portable-root-remainder.governance.json",
            *(
                topology_root / f"{code}.governance.json"
                for code, classification in topology.CODE_CLASSIFICATION.items()
                if classification == "PORTABLE_SOURCE_DEFECT"
            ),
        ]:
            document = json.loads(report.read_text(encoding="utf-8"))
            if document.get("component") == "root" and document.get("pytest_exit_status") == 0:
                document["custody_policy"] = baseline["collector_policy"]
                report.write_text(json.dumps(document), encoding="utf-8")

        remainder_report = topology_root / "portable-root-remainder.governance.json"
        remainder_report.unlink()

        def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1,
                "component": "root",
                "pytest_exit_status": 0,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node,
                    "component": "root",
                    "outcome": "passed",
                    "reason": "",
                    "phase": "call",
                } for node in nodes],
            }), encoding="utf-8")
            return nodes

        topology.execute_portable_root_remainder(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            exact_runner=exact,
        )
        for code, classification in topology.CODE_CLASSIFICATION.items():
            if classification == "PORTABLE_SOURCE_DEFECT":
                (topology_root / f"{code}.json").unlink()
                (topology_root / f"{code}.governance.json").unlink()

        extension.write_bytes(b"replaced custody")
        invoked = False

        def must_not_run(_nodes: tuple[str, ...], _report: Path) -> tuple[str, ...]:
            nonlocal invoked
            invoked = True
            raise AssertionError("custody drift must fail before pytest")

        with pytest.raises(topology.TopologyError, match="custody extension digest drift"):
            topology.run_lane(
                lane="portable-source",
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
                exact_runner=must_not_run,
            )
        assert not invoked
        assert not any(topology_root.glob("SRC-*.json"))
        with pytest.raises(topology.TopologyError):
            topology.reconcile_portable_root_accounting(
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
            )


def test_closed_root_accounting_rejects_duplicate_execution_between_remainder_and_inventory() -> None:
    """Break caught: an inventory node is also counted as an ordinary-root execution."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw)
        run_id, head_sha = _write_topology_evidence(evidence)
        topology_root = evidence / "capability-topology"
        remainder = json.loads((topology_root / "portable-root-remainder.json").read_text(encoding="utf-8"))
        duplicate = topology.load_inventory(
            ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
        )[0].node_id
        remainder["remainder_node_ids"] = sorted([*remainder["remainder_node_ids"], duplicate])
        contents = topology._candidate_file_bytes(tuple(remainder["remainder_node_ids"]))
        remainder["remainder_file_sha256"] = topology.hashlib.sha256(contents).hexdigest()
        remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
        (topology_root / "portable-root-remainder.txt").write_bytes(contents)
        (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))

        with pytest.raises(topology.TopologyError, match="baseline minus inventory"):
            topology.reconcile_portable_root_accounting(
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
            )
