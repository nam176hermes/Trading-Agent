from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts import t_g03_capability_topology as topology
from scripts import check_test_governance as governance


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
