from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import runpy
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts import t_g03_capability_topology as topology
from scripts import check_test_governance as governance


INVENTORY = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
HOSTED_SPACE_NODE_ID = (
    "tests/control_api/test_deployment_evidence.py::"
    "test_observed_at_requires_exact_rfc3339_utc_before_parsing[2026-07-16 12:00:00Z]"
)


def _seal_remainder(
    monkeypatch: pytest.MonkeyPatch, evidence: Path, raw: str, *, with_context: bool = False,
    extra_node_ids: tuple[str, ...] = (),
) -> tuple[str, str, tuple[str, ...]]:
    run_id = "31641536482"
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    extension = Path(raw) / "custody.so"
    extension.write_bytes(b"T-G03F custody fixture")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", topology.hashlib.sha256(extension.read_bytes()).hexdigest())
    context_path = (
        topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        ) if with_context else None
    )
    rows = topology.load_inventory(INVENTORY)
    closure = topology.load_portable_defect_closure(head_sha=head_sha)
    candidates = tuple(sorted([
        *(row.node_id for row in rows), *(row.node_id for row in closure),
        "tests/ordinary/test_failure.py::test_failure", *extra_node_ids,
    ]))
    topology.reserve_topology_evidence(
        evidence, run_id=run_id, head_sha=head_sha, foundation_context_path=context_path,
    )
    topology.collect_portable_root_baseline(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        collector=lambda: candidates, foundation_context_path=context_path,
    )
    remainder = topology.prepare_portable_root_remainder(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        foundation_context_path=context_path,
    )
    return run_id, head_sha, tuple(remainder["remainder_node_ids"])


def _valid_unsafe_nonacceptance_bytes(
    evidence: Path, *, run_id: str, head_sha: str,
) -> bytes:
    baseline = topology.load_portable_root_baseline(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        foundation_context_path=evidence / "capability-topology/foundation-context.json",
    )
    remainder, nodes = topology._load_portable_root_remainder(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        foundation_context_path=evidence / "capability-topology/foundation-context.json",
    )
    payload = topology._unsafe_raw_reason_nonacceptance_payload(
        baseline=baseline, remainder=remainder, nodes=nodes,
        custody=topology._validate_custody_policy(baseline["collector_policy"]), pytest_exit_status="0",
    )
    payload["nonacceptance_sha256"] = topology._sha256(payload)
    return topology.canonical_json_bytes(payload)


def test_space_bearing_portable_root_node_id_normalizes_and_round_trips_as_diagnostic_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: candidate-valid node IDs with spaces are rejected only by diagnostics."""
    raw_observation = {
        "test_node_id": HOSTED_SPACE_NODE_ID, "component": "root", "outcome": "passed",
        "reason": "", "phase": "call",
    }
    observations, exit_status = topology._diagnostic_observations(
        {"component": "root", "pytest_exit_status": 0, "tests": [raw_observation]},
        (HOSTED_SPACE_NODE_ID,), {"entries": []},
    )
    assert exit_status == "0"
    assert observations[0]["test_node_id"] == HOSTED_SPACE_NODE_ID

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, nodes = _seal_remainder(
            monkeypatch, evidence, raw, extra_node_ids=(HOSTED_SPACE_NODE_ID,),
        )
        assert HOSTED_SPACE_NODE_ID in nodes

        def nonpassing_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1,
                "component": "root",
                "pytest_exit_status": 1,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node,
                    "component": "root",
                    "outcome": "failed" if node == HOSTED_SPACE_NODE_ID else "passed",
                    "reason": "assertion failed" if node == HOSTED_SPACE_NODE_ID else "",
                    "phase": "call",
                } for node in selected],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="EXACT_EXECUTION_NONPASS"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=nonpassing_exact,
            )

        diagnostic = evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json"
        document = topology.read_failure_diagnostic(
            diagnostic, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        )
        assert topology.parse_failure_diagnostic(diagnostic.read_bytes()) == document
        hosted = next(item for item in document["observations"] if item["test_node_id"] == HOSTED_SPACE_NODE_ID)
        assert hosted["outcome"] == "failed"
        assert not (evidence / "capability-topology/portable-root-remainder.governance.json").exists()


@pytest.mark.parametrize("node_id", ["", "other/test.py::test_outside_root", "tests/test_bad.py::test_bad\x1f"])
def test_portable_root_diagnostic_rejects_empty_nonroot_and_control_node_ids(node_id: str) -> None:
    """Break caught: widening diagnostic IDs accepts non-root or non-printable observations."""
    with pytest.raises(topology.TopologyError, match="raw diagnostic observation"):
        topology._diagnostic_observations(
            {"component": "root", "pytest_exit_status": 1, "tests": [{
                "test_node_id": node_id, "component": "root", "outcome": "failed",
                "reason": "assertion failed", "phase": "call",
            }]},
            (node_id,), {"entries": []},
        )


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
        with pytest.raises(governance.GovernanceError, match="failure diagnostic is present"):
            governance.audit_topology_root_records(
                evidence_root=evidence, inventory=INVENTORY,
                foundation_run_id=run_id, foundation_head_sha=head_sha,
            )

        invoked = False

        def must_not_publish_pass(_selected: tuple[str, ...], _report: Path) -> tuple[str, ...]:
            nonlocal invoked
            invoked = True
            raise AssertionError("diagnostic must block a retry PASS publication")

        with pytest.raises(topology.TopologyError, match="staging record"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=must_not_publish_pass,
            )
        assert not invoked
        with pytest.raises(topology.TopologyError, match="lane publication"):
            topology.run_lane(
                lane="native-capabilities", inventory=INVENTORY, evidence_root=evidence,
                run_id=run_id, head_sha=head_sha,
            )

        # A non-passed row requires a typed hash, not an empty commitment.
        forged = dict(document)
        forged["observations"] = [dict(document["observations"][0])]
        forged["observations"][0]["normalized_reason_commitment_sha256"] = ""
        forged["diagnostic_sha256"] = topology._sha256({
            key: value for key, value in forged.items() if key != "diagnostic_sha256"
        })
        with pytest.raises(topology.TopologyError, match="non-policy binding"):
            topology.parse_failure_diagnostic(topology.canonical_json_bytes(forged))

        self_hash_forged = dict(document)
        self_hash_forged["pytest_exit_status"] = "2"
        with pytest.raises(topology.TopologyError, match="self-hash"):
            topology.parse_failure_diagnostic(topology.canonical_json_bytes(self_hash_forged))
        with pytest.raises(topology.TopologyError, match="canonical"):
            topology.parse_failure_diagnostic(diagnostic.read_bytes() + b"\n")
        with pytest.raises(topology.TopologyError, match="strict UTF-8 JSON"):
            topology.parse_failure_diagnostic(b'{"schema_version":"x","schema_version":"x"}')
        with pytest.raises(topology.TopologyError, match="missing"):
            topology.read_failure_diagnostic(
                diagnostic.with_name("missing.failure-diagnostic.json"), inventory=INVENTORY,
                evidence_root=evidence, run_id=run_id, head_sha=head_sha,
            )
        with pytest.raises(topology.TopologyError):
            topology.read_failure_diagnostic(
                diagnostic, inventory=INVENTORY, evidence_root=evidence,
                run_id="31641536483", head_sha=head_sha,
            )

        foreign = dict(document)
        foreign["observations"] = [dict(document["observations"][0])]
        foreign["observations"][0]["test_node_id"] = "tests/ordinary/test_foreign.py::test_foreign"
        foreign["diagnostic_sha256"] = topology._sha256({
            key: value for key, value in foreign.items() if key != "diagnostic_sha256"
        })
        foreign_path = diagnostic.with_name("foreign.failure-diagnostic.json")
        foreign_path.write_bytes(topology.canonical_json_bytes(foreign))
        with pytest.raises(topology.TopologyError, match="foreign or incomplete"):
            topology.read_failure_diagnostic(
                foreign_path, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
            )


def test_post_custody_source_drift_is_redacted_and_publishes_no_acceptance_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a changed valid second policy read leaks detail or publishes acceptance evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _nodes = _seal_remainder(monkeypatch, evidence, raw)
        source = topology._allowlist_bytes_at_head(head_sha)
        reads = 0

        def divergent_second_read(_head_sha: str) -> bytes:
            nonlocal reads
            reads += 1
            return source if reads == 1 else source + b" "

        def passing_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
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
                } for node in selected],
            }), encoding="utf-8")
            return selected

        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", divergent_second_read)
        with pytest.raises(topology.TopologyError) as raised:
            topology.execute_portable_root_remainder(
                inventory=INVENTORY,
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
                exact_runner=passing_exact,
            )

        assert reads == 2
        assert str(raised.value) == "policy validation failed: POLICY_SOURCE_DRIFT"
        assert "allowlist" not in str(raised.value).lower()
        topology_root = evidence / "capability-topology"
        assert not (topology_root / "portable-root-remainder.governance.json").exists()
        assert not (topology_root / "portable-root-remainder.failure-diagnostic.json").exists()
        assert not (topology_root / ".portable-root-remainder.governance.json.executing").exists()
        assert not any((topology_root / f"{code}.json").exists() for code in topology.CODE_CLASSIFICATION)
        with pytest.raises(topology.TopologyError):
            topology.reconcile_portable_root_accounting(
                inventory=INVENTORY,
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
            )


def test_failure_diagnostic_writer_is_no_clobber_and_rejects_staging_collision() -> None:
    """Break caught: a retry replaces published evidence or reuses a hostile staging name."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        path = Path(raw) / "portable-root-remainder.failure-diagnostic.json"
        topology._publish_failure_diagnostic(path, b"first")
        with pytest.raises(FileExistsError):
            topology._publish_failure_diagnostic(path, b"second")
        assert path.read_bytes() == b"first"
        other = Path(raw) / "second.failure-diagnostic.json"
        other.with_name(f".{other.name}.staging").write_bytes(b"hostile")
        with pytest.raises(FileExistsError):
            topology._publish_failure_diagnostic(other, b"second")
        assert not other.exists()


def test_failed_custody_postcheck_and_final_reread_never_accept_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: publication happens before custody exit or accepts a replaced final file."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw)
        extension = Path(os.environ["PACKAGE6_FD_CUSTODY_EXTENSION_PATH"])
        replacement = Path(raw) / "replacement.so"
        replacement.write_bytes(extension.read_bytes())
        published = False

        def spy_publish(path: Path, content: bytes) -> None:
            nonlocal published
            published = True
            topology._publish_no_clobber(path, content)

        def custody_drift(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "component": "root", "pytest_exit_status": 1,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{"test_node_id": selected[0], "component": "root", "outcome": "failed", "reason": "assertion failed", "phase": "call"}],
            }), encoding="utf-8")
            os.replace(replacement, extension)
            return selected

        monkeypatch.setattr(topology, "_publish_failure_diagnostic", spy_publish)
        with pytest.raises(topology.TopologyError, match="custody"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=custody_drift,
            )
        assert not published
        assert not (evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json").exists()


def test_final_reread_rejects_a_replaced_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: a post-write replacement is treated as the writer's verified evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw)
        real_publish = topology._publish_failure_diagnostic

        def replace_after_publish(path: Path, content: bytes) -> None:
            real_publish(path, content)
            path.write_bytes(b"tampered")

        def failed(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "component": "root", "pytest_exit_status": 1,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{"test_node_id": selected[0], "component": "root", "outcome": "failed", "reason": "assertion failed", "phase": "call"}],
            }), encoding="utf-8")
            return selected

        monkeypatch.setattr(topology, "_publish_failure_diagnostic", replace_after_publish)
        with pytest.raises(topology.TopologyError, match="post-write reread"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=failed,
            )
        diagnostic = evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json"
        with pytest.raises(topology.TopologyError):
            topology.parse_failure_diagnostic(diagnostic.read_bytes())


def test_receipt_first_complete_nonpass_does_not_publish_a_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a pre-existing deferred receipt can coexist with new failure evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw)
        receipts = topology.run_lane(
            lane="external-authorities", inventory=INVENTORY, evidence_root=evidence,
            run_id=run_id, head_sha=head_sha,
            external_preflight=lambda _code: ("ABSENT", "AUTHORITY_ROOT_ABSENT"),
        )
        before = {path: path.read_bytes() for path in receipts}

        def failed(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "component": "root", "pytest_exit_status": 1,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{"test_node_id": selected[0], "component": "root", "outcome": "failed", "reason": "assertion failed", "phase": "call"}],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="acceptance artifact"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=failed,
            )
        assert not (evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json").exists()
        assert {path: path.read_bytes() for path in receipts} == before


def test_preexecution_policy_snapshot_failure_publishes_only_the_redacted_nonacceptance_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a masked pre-execution policy failure loses its structural stage."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        private_value = "policy-content-that-must-not-leak"
        monkeypatch.setattr(
            topology,
            "_allowlist_bytes_at_head",
            lambda _head: (_ for _ in ()).throw(topology.TopologyError(private_value)),
        )

        with pytest.raises(topology.TopologyError, match="POLICY_VALIDATION_INVALID"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=lambda *_args: (_ for _ in ()).throw(AssertionError("runner must not start")),
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )

        record = evidence / "capability-topology/policy-validation-nonacceptance.json"
        assert record.is_file()
        assert private_value.encode("utf-8") not in record.read_bytes()
        assert not (evidence / "capability-topology/portable-root-remainder.governance.json").exists()
        assert not (evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json").exists()


def test_policy_nonacceptance_parser_and_receipt_writer_close_the_source_and_acceptance_matrices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: an illegal source convention or direct receipt writer accepts diagnostic evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        monkeypatch.setattr(
            topology, "_allowlist_bytes_at_head",
            lambda _head: (_ for _ in ()).throw(topology.TopologyError("private")),
        )
        with pytest.raises(topology.TopologyError, match="POLICY_VALIDATION_INVALID"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        path = evidence / "capability-topology/policy-validation-nonacceptance.json"
        document = topology.parse_policy_validation_nonacceptance(path.read_bytes())
        assert document["policy_source_hash_status"] == "UNAVAILABLE"
        for status, digest in (("CURRENT_STAGE_BYTES", "a" * 64), ("PRE_EXECUTION_SNAPSHOT", "a" * 64)):
            forged = dict(document, policy_source_hash_status=status, policy_source_sha256=digest)
            forged["nonacceptance_sha256"] = topology._sha256({key: value for key, value in forged.items() if key != "nonacceptance_sha256"})
            with pytest.raises(topology.TopologyError, match="source binding"):
                topology.parse_policy_validation_nonacceptance(topology.canonical_json_bytes(forged))

        receipt = topology.make_receipt(
            run_id=run_id, head_sha=head_sha, lane="portable-source",
            code="SRC-SEMANTIC-FIXTURE-IDENTITY", expected=("tests/runtime_release/test_semantic.py::test_fixture_uses_current_identity",),
            collected=("tests/runtime_release/test_semantic.py::test_fixture_uses_current_identity",),
            state="AVAILABLE", fact="SOURCE_TEST_EXECUTED", outcome="PASS",
        )
        with pytest.raises(topology.TopologyError, match="nonacceptance is present"):
            topology.publish_receipt(receipt, evidence)


@pytest.mark.parametrize("field,value", [
    ("policy_validation_stage", []), ("policy_validation_stage", {}),
    ("policy_validation_class", []), ("custody_status", {}),
    ("policy_source_hash_status", []), ("policy_source_sha256", {}),
])
def test_policy_nonacceptance_parser_rejects_untyped_closed_domain_values(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    """Break caught: a canonical hostile artifact leaks a Python container exception."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(topology.TopologyError):
            topology.execute_portable_root_remainder(inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha, foundation_context_path=evidence / "capability-topology/foundation-context.json")
        document = topology.parse_policy_validation_nonacceptance((evidence / "capability-topology/policy-validation-nonacceptance.json").read_bytes())
        document[field] = value
        document["nonacceptance_sha256"] = topology._sha256({key: item for key, item in document.items() if key != "nonacceptance_sha256"})
        with pytest.raises(topology.TopologyError):
            topology.parse_policy_validation_nonacceptance(topology.canonical_json_bytes(document))


def test_policy_nonacceptance_public_parser_exhausts_stage_class_and_hash_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a canonical record accepts a pair outside A2's closed matrix."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(topology.TopologyError):
            topology.execute_portable_root_remainder(inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha, foundation_context_path=evidence / "capability-topology/foundation-context.json")
        base = topology.parse_policy_validation_nonacceptance((evidence / "capability-topology/policy-validation-nonacceptance.json").read_bytes())
        for stage, classes in topology.POLICY_STAGE_CLASSES.items():
            for public_class in classes:
                document = dict(base, policy_validation_stage=stage, policy_validation_class=public_class)
                if stage == "SOURCE_ACQUISITION_HEAD_BINDING":
                    document.update(policy_source_hash_status="UNAVAILABLE", policy_source_sha256="", custody_status="PRE_EXECUTION_VALIDATED")
                elif stage == "POST_CUSTODY_REREAD_COMPARISON":
                    document.update(policy_source_hash_status="PRE_EXECUTION_SNAPSHOT", policy_source_sha256="a" * 64, custody_status="POST_CUSTODY_POSTCHECK_PASS")
                else:
                    document.update(policy_source_hash_status="CURRENT_STAGE_BYTES", policy_source_sha256="a" * 64, custody_status="PRE_EXECUTION_VALIDATED")
                document["nonacceptance_sha256"] = topology._sha256({key: value for key, value in document.items() if key != "nonacceptance_sha256"})
                assert topology.parse_policy_validation_nonacceptance(topology.canonical_json_bytes(document))["policy_validation_stage"] == stage
        invalid = dict(base, policy_validation_stage="SHARED_VALIDATOR_IMPORT", policy_validation_class="POLICY_SOURCE_DRIFT", policy_source_hash_status="CURRENT_STAGE_BYTES", policy_source_sha256="a" * 64)
        invalid["nonacceptance_sha256"] = topology._sha256({key: value for key, value in invalid.items() if key != "nonacceptance_sha256"})
        with pytest.raises(topology.TopologyError, match="stage/class"):
            topology.parse_policy_validation_nonacceptance(topology.canonical_json_bytes(invalid))


def test_policy_nonacceptance_presence_blocks_every_named_public_acceptance_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: any named acceptance path treats this diagnostic as green evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))
        context = evidence / "capability-topology/foundation-context.json"
        with pytest.raises(topology.TopologyError):
            topology.execute_portable_root_remainder(inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha, foundation_context_path=context)
        with pytest.raises(topology.TopologyError, match="nonacceptance"):
            topology.run_lane(lane="native-capabilities", inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha, foundation_context_path=context)
        with pytest.raises(topology.TopologyError, match="nonacceptance"):
            topology.reconcile_portable_root_accounting(inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha, foundation_context_path=context)
        with pytest.raises(governance.GovernanceError, match="nonacceptance"):
            governance.audit_topology_root_records(evidence_root=evidence, inventory=INVENTORY, foundation_run_id=run_id, foundation_head_sha=head_sha, foundation_context_path=context)
        with pytest.raises(topology.TopologyError, match="nonacceptance"):
            topology.read_failure_diagnostic(evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json", inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha, foundation_context_path=context)
        runner_started = False

        def must_not_run(_nodes: tuple[str, ...], _report: Path) -> tuple[str, ...]:
            nonlocal runner_started
            runner_started = True
            return ()

        with pytest.raises(topology.TopologyError, match="staging record"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                head_sha=head_sha, exact_runner=must_not_run,
                foundation_context_path=context,
            )
        assert not runner_started


def test_policy_nonacceptance_emits_and_reads_every_allowed_matrix_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a structurally permitted row parses but cannot become verified public evidence."""
    def passing(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
        report.write_text(json.dumps({
            "component": "root", "pytest_exit_status": 0,
            "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
            "tests": [{
                "test_node_id": node, "component": "root", "outcome": "passed",
                "reason": "", "phase": "call",
            } for node in nodes],
        }), encoding="utf-8")
        return nodes

    for stage, classes in topology.POLICY_STAGE_CLASSES.items():
        for public_class in classes:
            with tempfile.TemporaryDirectory(dir="/tmp") as raw:
                evidence = Path(raw) / "evidence"
                run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
                context = evidence / "capability-topology/foundation-context.json"
                source = topology._allowlist_bytes_at_head(head_sha)
                source_status = (
                    "UNAVAILABLE" if stage == "SOURCE_ACQUISITION_HEAD_BINDING"
                    else "PRE_EXECUTION_SNAPSHOT" if stage == "POST_CUSTODY_REREAD_COMPARISON"
                    else "CURRENT_STAGE_BYTES"
                )
                source_digest = "" if source_status == "UNAVAILABLE" else topology.hashlib.sha256(source).hexdigest()
                failure = topology._PolicyStageFailure(stage, public_class, source_status, source_digest)
                original_snapshot = topology._policy_snapshot_for_nonacceptance

                if stage == "POST_CUSTODY_REREAD_COMPARISON":
                    reads = 0

                    def snapshot_after_custody(head: str, validation_date: topology.date) -> tuple[dict[str, object], bytes]:
                        nonlocal reads
                        reads += 1
                        if reads == 1:
                            return original_snapshot(head, validation_date)
                        raise topology._PolicyStageError(failure)

                    monkeypatch.setattr(topology, "_policy_snapshot_for_nonacceptance", snapshot_after_custody)
                    runner = passing
                else:
                    monkeypatch.setattr(
                        topology, "_policy_snapshot_for_nonacceptance",
                        lambda _head, _date: (_ for _ in ()).throw(topology._PolicyStageError(failure)),
                    )
                    runner = lambda *_args: (_ for _ in ()).throw(AssertionError("pre-execution runner must not start"))

                with pytest.raises(topology.TopologyError, match=public_class):
                    topology.execute_portable_root_remainder(
                        inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                        head_sha=head_sha, exact_runner=runner, foundation_context_path=context,
                    )
                monkeypatch.setattr(topology, "_policy_snapshot_for_nonacceptance", original_snapshot)
                record_path = evidence / "capability-topology/policy-validation-nonacceptance.json"
                record = topology.parse_policy_validation_nonacceptance(record_path.read_bytes())
                assert record["policy_validation_stage"] == stage
                assert record["policy_validation_class"] == public_class
                assert topology.read_policy_validation_nonacceptance(
                    record_path, inventory=INVENTORY, evidence_root=evidence,
                    run_id=run_id, head_sha=head_sha, foundation_context_path=context,
                ) == record


def test_policy_nonacceptance_parser_rejects_every_matrix_invalid_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a canonical but cross-stage public class becomes accepted evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(topology.TopologyError):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        base = topology.parse_policy_validation_nonacceptance(
            (evidence / "capability-topology/policy-validation-nonacceptance.json").read_bytes(),
        )
        all_classes = set().union(*topology.POLICY_STAGE_CLASSES.values())
        for stage, classes in topology.POLICY_STAGE_CLASSES.items():
            for public_class in all_classes - classes:
                forged = dict(base, policy_validation_stage=stage, policy_validation_class=public_class)
                if stage == "SOURCE_ACQUISITION_HEAD_BINDING":
                    forged.update(policy_source_hash_status="UNAVAILABLE", policy_source_sha256="")
                elif stage == "POST_CUSTODY_REREAD_COMPARISON":
                    forged.update(policy_source_hash_status="PRE_EXECUTION_SNAPSHOT", policy_source_sha256="a" * 64)
                else:
                    forged.update(policy_source_hash_status="CURRENT_STAGE_BYTES", policy_source_sha256="a" * 64)
                forged["nonacceptance_sha256"] = topology._sha256({
                    key: value for key, value in forged.items() if key != "nonacceptance_sha256"
                })
                with pytest.raises(topology.TopologyError, match="stage/class"):
                    topology.parse_policy_validation_nonacceptance(topology.canonical_json_bytes(forged))


def test_policy_nonacceptance_writer_and_reader_reject_public_artifact_hostility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: this record can clobber, survive tamper, or bind a foreign Foundation run."""
    def emit_source_failure(evidence: Path, raw: str) -> tuple[str, str, Path]:
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        monkeypatch.setattr(
            topology, "_allowlist_bytes_at_head",
            lambda _head: (_ for _ in ()).throw(RuntimeError("private-policy-value")),
        )
        with pytest.raises(topology.TopologyError, match="POLICY_VALIDATION_INVALID"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        return run_id, head_sha, evidence / "capability-topology/policy-validation-nonacceptance.json"

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, record = emit_source_failure(evidence, raw)
        valid = record.read_bytes()
        for hostile in (
            b"not-json", valid + b"\n", topology.canonical_json_bytes({"schema_version": "forged"}),
        ):
            record.write_bytes(hostile)
            with pytest.raises(topology.TopologyError):
                topology.read_policy_validation_nonacceptance(
                    record, inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                    head_sha=head_sha, foundation_context_path=evidence / "capability-topology/foundation-context.json",
                )
        record.write_bytes(valid)
        payload = {
            key: value for key, value in topology.parse_policy_validation_nonacceptance(valid).items()
            if key != "nonacceptance_sha256"
        }
        with pytest.raises(FileExistsError):
            topology._publish_policy_nonacceptance(record, payload)
        assert record.read_bytes() == valid
        self_hash_invalid = topology.parse_policy_validation_nonacceptance(valid)
        self_hash_invalid["foundation_run_id"] = "31641536481"
        record.write_bytes(topology.canonical_json_bytes(self_hash_invalid))
        with pytest.raises(topology.TopologyError, match="self-hash"):
            topology.read_policy_validation_nonacceptance(
                record, inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                head_sha=head_sha, foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        foreign = topology.parse_policy_validation_nonacceptance(valid)
        foreign["foundation_head_sha"] = "b" * 40
        foreign["nonacceptance_sha256"] = topology._sha256({
            key: value for key, value in foreign.items() if key != "nonacceptance_sha256"
        })
        record.write_bytes(topology.canonical_json_bytes(foreign))
        with pytest.raises(topology.TopologyError, match="binding drift"):
            topology.read_policy_validation_nonacceptance(
                record, inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                head_sha=head_sha, foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        record.write_bytes(valid)
        with pytest.raises(topology.TopologyError):
            topology.read_policy_validation_nonacceptance(
                record, inventory=INVENTORY, evidence_root=evidence, run_id="31641536483",
                head_sha=head_sha, foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        target = evidence / "capability-topology/policy-validation-nonacceptance.json"
        target.write_bytes(b"pre-existing")
        with pytest.raises(topology.TopologyError, match="staging record"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        assert target.read_bytes() == b"pre-existing"

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        target = evidence / "capability-topology/policy-validation-nonacceptance.json"
        target.with_name(f".{target.name}.staging").write_bytes(b"pre-existing-staging")
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(FileExistsError):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        assert not target.exists()

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        original_publish = topology._publish_failure_diagnostic

        def tampering_publish(path: Path, content: bytes) -> None:
            original_publish(path, content)
            path.write_bytes(b"tampered")

        monkeypatch.setattr(topology, "_publish_failure_diagnostic", tampering_publish)
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(topology.TopologyError, match="post-write reread"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )


def test_valid_context_receipt_first_blocks_policy_nonacceptance_and_nonremainder_cannot_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: accepted receipt precedes diagnostic publication or a normal lane emits one."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        context = evidence / "capability-topology/foundation-context.json"
        receipt = topology.make_receipt(
            run_id=run_id, head_sha=head_sha, lane="portable-source",
            code="SRC-SEMANTIC-FIXTURE-IDENTITY",
            expected=("tests/runtime_release/test_semantic.py::test_fixture_uses_current_identity",),
            collected=("tests/runtime_release/test_semantic.py::test_fixture_uses_current_identity",),
            state="AVAILABLE", fact="SOURCE_TEST_EXECUTED", outcome="PASS",
        )
        topology.publish_receipt(receipt, evidence)
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(topology.TopologyError, match="stale closed-code"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=context,
            )
        assert not (evidence / "capability-topology/policy-validation-nonacceptance.json").exists()

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        context = evidence / "capability-topology/foundation-context.json"
        monkeypatch.setattr(
            topology, "_validated_policy_snapshot",
            lambda _head, _date: (_ for _ in ()).throw(topology.TopologyError("snapshot failed")),
        )
        monkeypatch.setattr(
            topology, "_policy_snapshot_for_nonacceptance",
            lambda _head, _date: (_ for _ in ()).throw(AssertionError("non-remainder lane must not enter writer mode")),
        )
        with pytest.raises(topology.TopologyError, match="snapshot failed"):
            topology.run_lane(
                lane="portable-source", inventory=INVENTORY, evidence_root=evidence,
                run_id=run_id, head_sha=head_sha, foundation_context_path=context,
            )
        assert not (evidence / "capability-topology/policy-validation-nonacceptance.json").exists()


def test_policy_nonacceptance_reader_rebinds_present_source_before_other_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a drifted source reaches baseline parsing before its own digest check."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        source = topology._allowlist_bytes_at_head(head_sha)
        record = evidence / "capability-topology/policy-validation-nonacceptance.json"
        document: dict[str, object] = {
                "schema_version": topology.POLICY_NONACCEPTANCE_SCHEMA, "diagnostic_only": True,
                "foundation_run_id": run_id, "foundation_head_sha": head_sha,
                "foundation_validation_date": "2026-08-13",
                "foundation_context_sha256": topology.load_foundation_context(
                    evidence / "capability-topology/foundation-context.json", run_id=run_id, head_sha=head_sha,
                )["foundation_context_sha256"],
                "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
                "baseline_sha256": "a" * 64, "baseline_candidate_ids_sha256": "a" * 64,
                "baseline_node_list_sha256": "a" * 64, "remainder_sha256": "a" * 64,
                "remainder_candidate_ids_sha256": "a" * 64, "remainder_node_list_sha256": "a" * 64,
                "custody_policy_sha256": "a" * 64, "custody_status": "PRE_EXECUTION_VALIDATED",
                "policy_validation_stage": "SHARED_VALIDATOR_IMPORT", "policy_validation_class": "POLICY_VALIDATION_INVALID",
                "policy_source_hash_status": "CURRENT_STAGE_BYTES", "policy_source_sha256": topology.hashlib.sha256(source).hexdigest(),
                "nonacceptance_sha256": "",
            }
        document["nonacceptance_sha256"] = topology._sha256({key: value for key, value in document.items() if key != "nonacceptance_sha256"})
        record.write_bytes(topology.canonical_json_bytes(document))
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: b"drift")
        monkeypatch.setattr(
            topology, "load_portable_root_baseline",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("baseline must not be read first")),
        )
        with pytest.raises(topology.TopologyError, match="source binding drift"):
            topology.read_policy_validation_nonacceptance(
                record, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )


def test_policy_nonacceptance_stages_are_structural_and_post_custody_is_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: snapshot boundaries infer a stage from text or collapse the second read."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    source = topology._allowlist_bytes_at_head(head)
    cases = [
        ("SOURCE_ACQUISITION_HEAD_BINDING", lambda patch: patch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: (_ for _ in ()).throw(RuntimeError("x")))),
        ("STRICT_JSON_PARSE", lambda patch: patch.setattr(topology, "_allowlist_bytes_at_head", lambda _head: b"{")),
        ("ROOT_PROJECTION_REASON_NORMALIZATION", lambda patch: patch.setattr(topology, "reason_commitment_sha256", lambda _reason: (_ for _ in ()).throw(RuntimeError("x")))),
    ]
    for expected, prepare in cases:
        with pytest.MonkeyPatch.context() as isolated:
            prepare(isolated)
            with pytest.raises(topology._PolicyStageError) as raised:
                topology._policy_snapshot_for_nonacceptance(head, topology.date(2026, 8, 13))
            assert raised.value.failure.stage == expected
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        reads = 0
        def source_then_drift(_head: str) -> bytes:
            nonlocal reads
            reads += 1
            return source if reads == 1 else b"{"
        monkeypatch.setattr(topology, "_allowlist_bytes_at_head", source_then_drift)
        def passing(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({"component": "root", "pytest_exit_status": 0, "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]), "tests": [{"test_node_id": node, "component": "root", "outcome": "passed", "reason": "", "phase": "call"} for node in nodes]}), encoding="utf-8")
            return nodes
        with pytest.raises(topology.TopologyError, match="POLICY_VALIDATION_INVALID"):
            topology.execute_portable_root_remainder(inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha, exact_runner=passing, foundation_context_path=evidence / "capability-topology/foundation-context.json")
        record = topology.parse_policy_validation_nonacceptance((evidence / "capability-topology/policy-validation-nonacceptance.json").read_bytes())
        assert record["policy_validation_stage"] == "POST_CUSTODY_REREAD_COMPARISON"
        assert record["policy_source_hash_status"] == "PRE_EXECUTION_SNAPSHOT"


@pytest.mark.parametrize("allowed", [False, None, "true", 1, 0, [], {}])
def test_public_comparator_rejects_every_nonliteral_true_ci_approval(allowed: object) -> None:
    """Break caught: a direct comparator caller bypasses CI approval with truthy data."""
    records = [{
        "test_node_id": "tests/ordinary/test_policy.py::test_skip", "component": "root",
        "outcome": "skipped", "reason": "approved skip", "phase": "call",
    }]
    approval: dict[str, object] = {
        "test_node_id": "tests/ordinary/test_policy.py::test_skip", "component": "root",
        "outcome": "skipped", "reason": "approved skip",
    }
    if allowed is not None:
        approval["allowed_in_ci"] = allowed
    with pytest.raises(governance.GovernanceError, match="not allowed in CI"):
        governance.compare_inventory(records, [approval])

    approval["allowed_in_ci"] = True
    governance.compare_inventory(records, [approval])


@pytest.mark.parametrize("raw", ["not-a-bool", 1, [], {}, object()])
def test_raw_wasxfail_rejects_nonboolean_values(raw: object) -> None:
    """Break caught: truthy malformed raw xfail data mints an xpass/xfail diagnostic."""
    with pytest.raises(topology.TopologyError, match="wasxfail"):
        topology._raw_observation_domain({"outcome": "passed", "phase": "call", "wasxfail": raw})


def test_policy_snapshot_hashes_and_public_policy_link_precedence_are_closed() -> None:
    """Break caught: snapshot provenance drifts or a policy link infers/counts an approval."""
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    snapshot, source = topology._validated_policy_snapshot(
        head_sha, topology.date(2026, 10, 31),
    )
    entries = snapshot["entries"]
    assert snapshot["allowlist_source_sha256"] == topology.hashlib.sha256(source).hexdigest()
    assert entries == sorted(entries, key=lambda item: (item["component"].encode(), item["test_node_id"].encode()))
    root_entries = governance.validate_allowlist_document(json.loads(source.decode("utf-8")))
    source_entry = next(entry for entry in root_entries if entry["component"] == "root")
    snapshot_entry = next(entry for entry in entries if entry["test_node_id"] == source_entry["test_node_id"])
    assert snapshot_entry["normalized_reason_commitment_sha256"] == topology.reason_commitment_sha256(source_entry["reason"])
    assert snapshot_entry["policy_entry_sha256"] == topology._sha256(topology._policy_entry_payload(source_entry))

    reason = topology.reason_commitment_sha256("approved skip")
    fixture: dict[str, object] = {"entries": [{
        "component": "root", "test_node_id": "tests/ordinary/test_policy.py::test_skip",
        "outcome": "skipped", "allowed_in_ci": True, "reason_class": "POLICY_SKIP_REASON",
        "normalized_reason_commitment_sha256": reason, "policy_entry_sha256": "a" * 64,
    }]}
    assert topology.compare_failure_policy_link(fixture, component="root", test_node_id="missing", outcome="skipped", normalized_reason_commitment_sha256=reason) == ("NO_POLICY_ENTRY", "")
    assert topology.compare_failure_policy_link(fixture, component="root", test_node_id="tests/ordinary/test_policy.py::test_skip", outcome="deselected", normalized_reason_commitment_sha256=reason) == ("OUTCOME_MISMATCH", "a" * 64)
    assert topology.compare_failure_policy_link(fixture, component="root", test_node_id="tests/ordinary/test_policy.py::test_skip", outcome="skipped", normalized_reason_commitment_sha256="b" * 64) == ("REASON_MISMATCH", "a" * 64)
    fixture["entries"][0]["allowed_in_ci"] = False
    assert topology.compare_failure_policy_link(fixture, component="root", test_node_id="tests/ordinary/test_policy.py::test_skip", outcome="skipped", normalized_reason_commitment_sha256=reason) == ("CI_DISALLOWED", "a" * 64)
    fixture["entries"][0]["allowed_in_ci"] = True
    assert topology.compare_failure_policy_link(fixture, component="root", test_node_id="tests/ordinary/test_policy.py::test_skip", outcome="skipped", normalized_reason_commitment_sha256=reason) == ("EXACT_POLICY_MATCH", "a" * 64)
    for invalid in (None, "true", 1, 0, [], {}):
        fixture["entries"][0]["allowed_in_ci"] = invalid
        with pytest.raises(topology.TopologyError, match="non-boolean"):
            topology.compare_failure_policy_link(fixture, component="root", test_node_id="tests/ordinary/test_policy.py::test_skip", outcome="skipped", normalized_reason_commitment_sha256=reason)


def test_failure_reader_requires_empty_passed_commitment_and_hashes_every_other_domain() -> None:
    """Break caught: malformed reason commitments are read as verified failure evidence."""
    base = {
            "schema_version": topology.FAILURE_DIAGNOSTIC_SCHEMA, "diagnostic_only": True,
            "foundation_run_id": "31641536482", "foundation_head_sha": "a" * 40,
            "foundation_validation_date": "2026-10-31", "foundation_context_sha256": "f" * 64,
            "inventory_sha256": "0" * 64, "baseline_candidate_ids_sha256": "1" * 64,
        "baseline_node_list_sha256": "2" * 64, "remainder_candidate_ids_sha256": "3" * 64,
        "remainder_node_list_sha256": "4" * 64, "custody_policy_sha256": "5" * 64,
        "custody_postcheck_status": "PASS", "pytest_exit_status": "1",
        "policy_snapshot": {
            "snapshot_schema_version": topology.POLICY_SNAPSHOT_SCHEMA, "allowlist_schema_version": "1",
            "allowlist_source_sha256": "6" * 64, "policy_entry_schema_version": topology.POLICY_ENTRY_SCHEMA,
            "entries": [],
        }, "policy_snapshot_sha256": "", "observations": [], "diagnostic_sha256": "",
    }

    def document(observation: dict[str, object]) -> bytes:
        value = dict(base)
        value["observations"] = [observation]
        value["policy_snapshot_sha256"] = topology._sha256(value["policy_snapshot"])
        value["diagnostic_sha256"] = topology._sha256({key: item for key, item in value.items() if key != "diagnostic_sha256"})
        return topology.canonical_json_bytes(value)

    passed = {
        "test_node_id": "tests/ordinary/test_policy.py::test_pass", "component": "root", "outcome": "passed", "phase": "call",
        "xfail_state": "NOT_WAS_XFAIL", "reason_class": "NONE", "reason_provenance": "NONE",
        "normalized_reason_commitment_sha256": "", "policy_match_result": "NOT_APPLICABLE", "existing_policy_entry_sha256": "",
    }
    assert topology.parse_failure_diagnostic(document(passed))["observations"] == [passed]
    for hostile_node_id in ("", "other/test.py::test_outside_root", "tests/test_bad.py::test_bad\x1f"):
        with pytest.raises(topology.TopologyError, match="duplicate or unordered"):
            topology.parse_failure_diagnostic(
                document(dict(passed, test_node_id=hostile_node_id)),
            )
    malformed_passed = dict(passed, normalized_reason_commitment_sha256="7" * 64)
    with pytest.raises(topology.TopologyError, match="non-policy binding"):
        topology.parse_failure_diagnostic(document(malformed_passed))
    failed = dict(passed, outcome="failed", reason_class="PYTEST_FAILURE_REASON", reason_provenance="PYTEST_REPORT", normalized_reason_commitment_sha256="")
    with pytest.raises(topology.TopologyError, match="non-policy binding"):
        topology.parse_failure_diagnostic(document(failed))
    no_policy = dict(passed, outcome="skipped", phase="call", reason_class="PYTEST_SKIP_REASON", reason_provenance="PYTEST_REPORT", normalized_reason_commitment_sha256=1, policy_match_result="NO_POLICY_ENTRY")
    with pytest.raises(topology.TopologyError, match="no-policy binding"):
        topology.parse_failure_diagnostic(document(no_policy))


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


@pytest.mark.parametrize("reason", [
    "/private/raw-path",
    r"private\\raw-path",
    "private\x01control",
    "private\x7fcontrol",
    "https://private.example.invalid/reason",
    "bearer private-value",
    "A" * 20,
    7,
    "not\tv1-normalized",
])
def test_complete_unsafe_raw_reason_publishes_only_the_fixed_nonacceptance_record_after_custody(
    monkeypatch: pytest.MonkeyPatch, reason: object,
) -> None:
    """Break caught: unsafe raw evidence either leaks, reaches policy logic, or disappears after custody."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, nodes = _seal_remainder(monkeypatch, evidence, raw, with_context=True)

        def unsafe_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1, "component": "root", "pytest_exit_status": 0,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node, "component": "root", "outcome": "skipped",
                    "reason": reason, "phase": "call",
                } for node in selected],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="^UNSAFE_RAW_REASON_NONACCEPTANCE$") as raised:
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=unsafe_exact,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )

        record = evidence / "capability-topology/portable-root-remainder.unsafe-raw-reason-nonacceptance.json"
        document = topology.read_unsafe_raw_reason_nonacceptance(
            record, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=evidence / "capability-topology/foundation-context.json",
        )
        assert document["schema_version"] == "t-g03a-unsafe-raw-reason-nonacceptance/v1"
        assert document["diagnostic_only"] is True
        assert document["custody_postcheck_status"] == "PASS"
        assert document["pytest_exit_status"] == "0"
        assert document["raw_reason_nonacceptance_state"] == "UNSAFE_RAW_REASON_OBSERVED"
        record_bytes = record.read_bytes()
        forbidden = [nodes[0].encode(), b"private", b"raw-path"]
        if isinstance(reason, str):
            forbidden.extend((reason.encode(), topology.hashlib.sha256(reason.encode()).hexdigest().encode()))
        assert all(fragment not in record_bytes and fragment not in str(raised.value).encode() for fragment in forbidden)
        assert not (evidence / "capability-topology/portable-root-remainder.failure-diagnostic.json").exists()
        assert not (evidence / "capability-topology/portable-root-remainder.governance.json").exists()


def test_missing_native_toolchain_skip_reaches_the_redacted_failure_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: hosted toolchain absence reintroduces a path-like raw skip reason."""
    fixture = runpy.run_path("tests/foundation/test_nautilus_native_entry_guard.py")
    build_guard = fixture["_build_guard"]
    assert callable(build_guard)

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        missing_root = Path(raw) / "missing"
        monkeypatch.setitem(build_guard.__globals__, "PRIVATE_RUST", missing_root / "rust")
        monkeypatch.setitem(build_guard.__globals__, "PRIVATE_LLVM", missing_root / "llvm")
        with pytest.raises(pytest.skip.Exception) as skipped:
            build_guard(Path(raw) / "build", Path(raw) / "guarded")
        reason = str(skipped.value)
        assert reason == (
            "required sealed private Rust or LLVM toolchain executable is unavailable"
        )

        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(
            monkeypatch, evidence, raw, with_context=True,
        )

        def unavailable_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1, "component": "root", "pytest_exit_status": 0,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node, "component": "root", "outcome": "skipped",
                    "reason": reason, "phase": "setup",
                } for node in selected],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="^EXACT_EXECUTION_NONPASS$"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                head_sha=head_sha, exact_runner=unavailable_exact,
                foundation_context_path=(
                    evidence / "capability-topology/foundation-context.json"
                ),
            )

        topology_root = evidence / "capability-topology"
        diagnostic = topology.parse_failure_diagnostic(
            (topology_root / "portable-root-remainder.failure-diagnostic.json").read_bytes(),
        )
        assert diagnostic["observations"][0]["policy_match_result"] == "NO_POLICY_ENTRY"
        assert not topology._unsafe_raw_reason_nonacceptance_path(topology_root).exists()


def test_structural_invalid_raw_failure_evidence_does_not_publish_any_diagnostic(
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


def test_unsafe_nonacceptance_reader_rejects_foreign_path_and_any_second_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a copy at another filename enters the diagnostic-only reader domain."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)

        def unsafe_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1, "component": "root", "pytest_exit_status": 0,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node, "component": "root", "outcome": "skipped",
                    "reason": "/private/raw-path", "phase": "call",
                } for node in selected],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="^UNSAFE_RAW_REASON_NONACCEPTANCE$"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=unsafe_exact,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        canonical = evidence / "capability-topology/portable-root-remainder.unsafe-raw-reason-nonacceptance.json"
        foreign = canonical.with_name("foreign.unsafe-raw-reason-nonacceptance.json")
        foreign.write_bytes(canonical.read_bytes())
        with pytest.raises(topology.TopologyError, match="canonical path|second unsafe"):
            topology.read_unsafe_raw_reason_nonacceptance(
                foreign, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        with pytest.raises(topology.TopologyError, match="second unsafe"):
            topology.read_unsafe_raw_reason_nonacceptance(
                canonical, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        foreign.write_bytes(b"malformed")
        with pytest.raises(topology.TopologyError, match="second unsafe"):
            topology.read_unsafe_raw_reason_nonacceptance(
                canonical, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )


@pytest.mark.parametrize("foreign_bytes", [b"malformed", "valid"], ids=["malformed", "valid"])
def test_foreign_unsafe_record_blocks_remainder_before_runner_or_terminal_publication(
    monkeypatch: pytest.MonkeyPatch, foreign_bytes: bytes | str,
) -> None:
    """Break caught: a foreign unsafe-record spelling permits a root PASS or canonical unsafe record."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        topology_root = evidence / "capability-topology"
        foreign = topology_root / "foreign.unsafe-raw-reason-nonacceptance.json"
        foreign.write_bytes(
            _valid_unsafe_nonacceptance_bytes(evidence, run_id=run_id, head_sha=head_sha)
            if foreign_bytes == "valid" else foreign_bytes
        )
        invoked = False

        def all_pass(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            nonlocal invoked
            invoked = True
            report.write_text(json.dumps({
                "schema_version": 1, "component": "root", "pytest_exit_status": 0,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node, "component": "root", "outcome": "passed",
                    "reason": "", "phase": "call",
                } for node in selected],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=all_pass,
                foundation_context_path=topology_root / "foundation-context.json",
            )
        assert not invoked
        assert not (topology_root / "portable-root-remainder.governance.json").exists()
        assert not topology._unsafe_raw_reason_nonacceptance_path(topology_root).exists()


@pytest.mark.parametrize("foreign_bytes", [b"malformed", "valid"], ids=["malformed", "valid"])
def test_foreign_unsafe_record_blocks_audit_before_baseline_loading(
    monkeypatch: pytest.MonkeyPatch, foreign_bytes: bytes | str,
) -> None:
    """Break caught: an earlier acceptance-input error masks a foreign unsafe-record presence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        (evidence / "capability-topology/foreign.unsafe-raw-reason-nonacceptance.json").write_bytes(
            _valid_unsafe_nonacceptance_bytes(evidence, run_id=run_id, head_sha=head_sha)
            if foreign_bytes == "valid" else foreign_bytes
        )
        monkeypatch.setattr(
            topology, "load_portable_root_baseline",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("foreign unsafe record must stop before baseline")),
        )
        monkeypatch.setattr(
            topology, "_installed_inventory_rows",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("foreign unsafe record must stop before inventory")),
        )
        with pytest.raises(governance.GovernanceError, match="unsafe raw reason nonacceptance is present"):
            governance.audit_topology_root_records(
                evidence_root=evidence, inventory=INVENTORY, foundation_run_id=run_id,
                foundation_head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )


@pytest.mark.parametrize("foreign_bytes", [b"malformed", "valid"], ids=["malformed", "valid"])
def test_foreign_unsafe_record_blocks_baseline_collection_before_collector(
    monkeypatch: pytest.MonkeyPatch, foreign_bytes: bytes | str,
) -> None:
    """Break caught: a reused root can invoke collection despite foreign terminal evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        topology_root = evidence / "capability-topology"
        (topology_root / "foreign.unsafe-raw-reason-nonacceptance.json").write_bytes(
            _valid_unsafe_nonacceptance_bytes(evidence, run_id=run_id, head_sha=head_sha)
            if foreign_bytes == "valid" else foreign_bytes
        )
        invoked = False

        def collector() -> tuple[str, ...]:
            nonlocal invoked
            invoked = True
            return ()

        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.collect_portable_root_baseline(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                collector=collector,
                foundation_context_path=topology_root / "foundation-context.json",
            )
        assert not invoked


@pytest.mark.parametrize("foreign_bytes", [b"malformed", "valid"], ids=["malformed", "valid"])
def test_foreign_unsafe_record_blocks_remainder_preparation_before_baseline_reuse(
    monkeypatch: pytest.MonkeyPatch, foreign_bytes: bytes | str,
) -> None:
    """Break caught: remainder preparation can reuse a baseline after terminal unsafe evidence appears."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        topology_root = evidence / "capability-topology"
        (topology_root / "foreign.unsafe-raw-reason-nonacceptance.json").write_bytes(
            _valid_unsafe_nonacceptance_bytes(evidence, run_id=run_id, head_sha=head_sha)
            if foreign_bytes == "valid" else foreign_bytes
        )
        monkeypatch.setattr(
            topology,
            "load_portable_root_baseline",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe evidence must stop before baseline reuse")),
        )
        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.prepare_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=topology_root / "foundation-context.json",
            )


def test_unsafe_presence_guard_classifies_undecodable_foreign_directory_entry() -> None:
    """Break caught: a surrogate filename escapes the topology error boundary while sorting."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        topology_root = Path(raw) / "capability-topology"
        topology_root.mkdir(mode=0o700)
        descriptor = os.open(
            os.fsencode(topology_root) + b"/foreign-\xff.unsafe-raw-reason-nonacceptance.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology._reject_unsafe_raw_reason_nonacceptance_presence(topology_root)


@pytest.mark.parametrize("field", sorted(topology.UNSAFE_RAW_REASON_NONACCEPTANCE_KEYS))
def test_unsafe_nonacceptance_parser_rejects_each_missing_or_retyped_schema_field(
    monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    """Break caught: an unsafe-record field may disappear or change type without parser rejection."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        document = topology.parse_unsafe_raw_reason_nonacceptance(
            _valid_unsafe_nonacceptance_bytes(evidence, run_id=run_id, head_sha=head_sha),
        )
        missing = dict(document)
        missing.pop(field)
        with pytest.raises(topology.TopologyError):
            topology.parse_unsafe_raw_reason_nonacceptance(topology.canonical_json_bytes(missing))
        retyped = dict(document)
        retyped[field] = 7
        with pytest.raises(topology.TopologyError):
            topology.parse_unsafe_raw_reason_nonacceptance(topology.canonical_json_bytes(retyped))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("diagnostic_only", 1),
        ("raw_reason_nonacceptance_state", "UNKNOWN_STATE"),
        ("custody_postcheck_status", "FAILED"),
        ("inventory_sha256", "0" * 63),
        ("pytest_exit_status", "01"),
    ],
)
def test_unsafe_nonacceptance_parser_rejects_hostile_literal_digest_and_status_values(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    """Break caught: a self-hashed hostile literal, digest, or status is accepted as diagnostic evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        document = topology.parse_unsafe_raw_reason_nonacceptance(
            _valid_unsafe_nonacceptance_bytes(evidence, run_id=run_id, head_sha=head_sha),
        )
        document[field] = value
        document["nonacceptance_sha256"] = topology._sha256({
            key: candidate for key, candidate in document.items()
            if key != "nonacceptance_sha256"
        })
        with pytest.raises(topology.TopologyError):
            topology.parse_unsafe_raw_reason_nonacceptance(topology.canonical_json_bytes(document))


def test_unsafe_nonacceptance_reader_rejects_self_hashed_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a canonical unsafe record can bind to another Foundation head."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)
        topology_root = evidence / "capability-topology"
        canonical = topology._unsafe_raw_reason_nonacceptance_path(topology_root)
        document = topology.parse_unsafe_raw_reason_nonacceptance(
            _valid_unsafe_nonacceptance_bytes(evidence, run_id=run_id, head_sha=head_sha),
        )
        document["foundation_head_sha"] = "0" * 40
        document["nonacceptance_sha256"] = topology._sha256({
            key: candidate for key, candidate in document.items()
            if key != "nonacceptance_sha256"
        })
        canonical.write_bytes(topology.canonical_json_bytes(document))
        with pytest.raises(topology.TopologyError, match="binding drift"):
            topology.read_unsafe_raw_reason_nonacceptance(
                canonical, inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=topology_root / "foundation-context.json",
            )


def test_unsafe_nonacceptance_hostile_integrity_and_named_acceptance_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: hostile unsafe evidence mutates, clobbers, or coexists with an accepting reader."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, nodes = _seal_remainder(
            monkeypatch, evidence, raw, with_context=True,
            extra_node_ids=("tests/ordinary/test_safe.py::test_safe",),
        )

        def mixed_exact(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1, "component": "root", "pytest_exit_status": 0,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node, "component": "root", "outcome": "skipped",
                    "reason": "approved skip" if node.endswith("test_safe") else "token /private/value",
                    "phase": "call",
                } for node in selected],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="^UNSAFE_RAW_REASON_NONACCEPTANCE$"):
            topology.execute_portable_root_remainder(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                exact_runner=mixed_exact,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        canonical = evidence / "capability-topology/portable-root-remainder.unsafe-raw-reason-nonacceptance.json"
        document = topology.parse_unsafe_raw_reason_nonacceptance(canonical.read_bytes())
        record_bytes = canonical.read_bytes()
        assert all(node.encode() not in record_bytes for node in nodes)
        assert b"approved skip" not in record_bytes and b"token /private/value" not in record_bytes
        forged = dict(document)
        forged["pytest_exit_status"] = "1"
        with pytest.raises(topology.TopologyError, match="self-hash"):
            topology.parse_unsafe_raw_reason_nonacceptance(topology.canonical_json_bytes(forged))
        forged = dict(document)
        forged["receipt"] = "forbidden"
        with pytest.raises(topology.TopologyError, match="schema keys"):
            topology.parse_unsafe_raw_reason_nonacceptance(topology.canonical_json_bytes(forged))

        with pytest.raises(FileExistsError):
            topology._publish_unsafe_raw_reason_nonacceptance(
                canonical, {key: value for key, value in document.items() if key != "nonacceptance_sha256"},
            )
        staging_root = Path(raw) / "staging-topology"
        staging_root.mkdir(mode=0o700)
        staging_target = topology._unsafe_raw_reason_nonacceptance_path(staging_root)
        staging_target.with_name(f".{staging_target.name}.staging").write_bytes(b"hostile")
        with pytest.raises(FileExistsError):
            topology._publish_unsafe_raw_reason_nonacceptance(
                staging_target, {key: value for key, value in document.items() if key != "nonacceptance_sha256"},
            )
        assert not staging_target.exists()
        with pytest.raises(topology.TopologyError, match="writer requires the canonical path"):
            topology._publish_unsafe_raw_reason_nonacceptance(
                canonical.with_name("foreign.unsafe-raw-reason-nonacceptance.json"),
                {key: value for key, value in document.items() if key != "nonacceptance_sha256"},
            )
        tamper_root = Path(raw) / "tamper-topology"
        tamper_root.mkdir(mode=0o700)
        tamper_target = topology._unsafe_raw_reason_nonacceptance_path(tamper_root)
        real_publish = topology._publish_failure_diagnostic

        def tampering_publish(path: Path, content: bytes) -> None:
            real_publish(path, content)
            path.write_bytes(b"tampered")

        monkeypatch.setattr(topology, "_publish_failure_diagnostic", tampering_publish)
        with pytest.raises(topology.TopologyError, match="post-write reread"):
            topology._publish_unsafe_raw_reason_nonacceptance(
                tamper_target, {key: value for key, value in document.items() if key != "nonacceptance_sha256"},
            )

        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.read_failure_diagnostic(
                canonical.with_name("missing.failure-diagnostic.json"), inventory=INVENTORY,
                evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.read_policy_validation_nonacceptance(
                canonical.with_name("missing.policy-validation-nonacceptance.json"), inventory=INVENTORY,
                evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.reconcile_portable_root_accounting(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.aggregate_receipts(
                [canonical.with_name(f"{code}.json") for code in sorted(topology.CODE_CLASSIFICATION)],
                rows=topology.load_inventory(INVENTORY), foundation_run_id=run_id, foundation_head_sha=head_sha,
            )
        with pytest.raises(topology.TopologyError, match="unsafe raw reason nonacceptance is present"):
            topology.run_lane(
                lane="native-capabilities", inventory=INVENTORY, evidence_root=evidence,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )


def test_nonremainder_unsafe_raw_reason_never_publishes_the_diagnostic_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a non-remainder lane can invoke the unsafe-record writer by report content."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha, _ = _seal_remainder(monkeypatch, evidence, raw, with_context=True)

        def unsafe_lane(selected: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1, "component": "root", "pytest_exit_status": 1,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node, "component": "root", "outcome": "failed",
                    "reason": "https://private.example.invalid/raw", "phase": "call",
                } for node in selected],
            }), encoding="utf-8")
            return selected

        with pytest.raises(topology.TopologyError, match="unsafe reason"):
            topology.run_lane(
                lane="portable-source", inventory=INVENTORY, evidence_root=evidence,
                run_id=run_id, head_sha=head_sha, exact_runner=unsafe_lane,
                foundation_context_path=evidence / "capability-topology/foundation-context.json",
            )
        assert not (evidence / "capability-topology/portable-root-remainder.unsafe-raw-reason-nonacceptance.json").exists()
