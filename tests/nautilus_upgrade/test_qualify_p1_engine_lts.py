"""Read-only P1 engine LTS qualification receipt tests."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import qualify_p1_engine_lts as qualifier


COMMIT = "1" * 40
TREE = "2" * 40
LOCAL_GATES = {
    "P1S_SOURCE": True,
    "P1S_RECOVERY": True,
    "P1S_GOLDEN": True,
}


def test_local_gate_harness_covers_every_p1_h_source_owner() -> None:
    covered = {
        nodeid
        for nodeids in qualifier._LOCAL_GATES.values()
        for nodeid in nodeids
    }

    assert {
        "tests/engine_contracts/test_session.py",
        "tests/nautilus_upgrade/test_lts_policy.py",
        "tests/nautilus_upgrade/test_p1_h_impact_cli.py",
        "tests/nautilus_upgrade/test_p1_h_lifecycle.py",
        "tests/paper_runtime/test_engine_session_port.py",
    } <= covered


def _external_receipt(schema: str, verdict: str = "PASS") -> bytes:
    return qualifier.canonical_receipt_bytes(
        {
            "authority_limits": qualifier.SAFE_AUTHORITY_LIMITS,
            "evidence_sha256s": ["a" * 64],
            "execution_scope": "PAPER_LOCAL_ONLY",
            "schema": schema,
            "source_commit": COMMIT,
            "source_tree": TREE,
            "verdict": verdict,
        }
    )


def test_local_receipt_is_source_only_and_never_grants_runtime_authority() -> None:
    receipt, exit_code = qualifier.qualify(
        mode="local",
        source_identity=(COMMIT, TREE),
        source_clean=True,
        local_gate_results=LOCAL_GATES,
    )

    assert exit_code == 0
    assert receipt["status"] == "P1_H_LOCAL_SOURCE_QUALIFIED"
    assert receipt["local_gates"] == LOCAL_GATES
    assert [entry["lifecycle"] for entry in receipt["engine_registry"]] == ["ACTIVE", "ROLLBACK"]
    assert [entry["engine_version"] for entry in receipt["engine_registry"]] == ["1.231.0", "1.227.0"]
    assert receipt["event_api_epoch"]["request_protocol"] == "1.0.0"
    assert len(receipt["golden_registry_sha256"]) == 64
    assert receipt["checkpoint_policy"]["active_schema"] == "sandbox-recovery-checkpoint-v2"
    assert receipt["source"] == {"clean": True, "commit": COMMIT, "tree": TREE}
    assert receipt["execution_scope"] == "PAPER_LOCAL_ONLY"
    assert receipt["authority_limits"] == qualifier.SAFE_AUTHORITY_LIMITS
    assert all(value is False for value in receipt["authority_limits"].values())
    assert receipt["external_evidence"] == "NOT_ASSESSED"
    assert [node["node_id"] for node in receipt["node_results"]] == list(LOCAL_GATES)
    assert all(node["status"] == "PASS" for node in receipt["node_results"])
    assert all(node["failure_codes"] == [] for node in receipt["node_results"])


def test_local_receipt_holds_a_dirty_source() -> None:
    receipt, exit_code = qualifier.qualify(
        mode="local",
        source_identity=(COMMIT, TREE),
        source_clean=False,
        local_gate_results=LOCAL_GATES,
    )

    assert exit_code == 2
    assert receipt["status"] == "HELD_DIRTY_SOURCE"


def test_local_receipt_does_not_misclassify_initial_dirty_source_as_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(qualifier, "_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(qualifier, "_source_clean", lambda: False)

    receipt, exit_code = qualifier.qualify(
        mode="local",
        local_gate_results=LOCAL_GATES,
    )

    assert exit_code == 2
    assert receipt["status"] == "HELD_DIRTY_SOURCE"


def test_report_without_external_inputs_is_explicitly_deferred() -> None:
    receipt, exit_code = qualifier.qualify(
        mode="report",
        source_identity=(COMMIT, TREE),
        source_clean=True,
    )

    assert exit_code == 0
    assert receipt["status"] == "DEFERRED_EXTERNAL"
    assert receipt["missing_external_evidence"] == [
        "foundation_receipt",
        "native_receipt",
        "operator_receipt",
    ]
    assert receipt["authority_limits"] == qualifier.SAFE_AUTHORITY_LIMITS


@pytest.mark.parametrize("mode", ("source-ready", "final"))
def test_qualification_modes_fail_closed_without_external_inputs(mode: str) -> None:
    receipt, exit_code = qualifier.qualify(
        mode=mode,
        source_identity=(COMMIT, TREE),
        source_clean=True,
    )

    assert exit_code == 2
    assert receipt["status"] == "HELD_MISSING_EXTERNAL_EVIDENCE"


def test_partial_external_inputs_are_held(tmp_path: Path) -> None:
    native = tmp_path / "native.json"
    native.write_bytes(_external_receipt("trading-agent-p1-lts-native-proof/v1"))

    receipt, exit_code = qualifier.qualify(
        mode="source-ready",
        native_receipt=native,
        source_identity=(COMMIT, TREE),
        source_clean=True,
    )

    assert exit_code == 2
    assert receipt["status"] == "HELD_PARTIAL_EXTERNAL_EVIDENCE"


def test_complete_exact_external_chain_reaches_p1_h_complete(tmp_path: Path) -> None:
    native = tmp_path / "native.json"
    foundation = tmp_path / "foundation.json"
    operator = tmp_path / "operator.json"
    native.write_bytes(_external_receipt("trading-agent-p1-lts-native-proof/v1"))
    foundation.write_bytes(_external_receipt("trading-agent-p1-lts-foundation-proof/v1"))
    operator.write_bytes(
        _external_receipt("trading-agent-p1-lts-operator-acceptance/v1", "ACCEPT")
    )

    receipt, exit_code = qualifier.qualify(
        mode="source-ready",
        native_receipt=native,
        foundation_receipt=foundation,
        operator_receipt=operator,
        source_identity=(COMMIT, TREE),
        source_clean=True,
    )

    assert exit_code == 0
    assert receipt["status"] == "P1_H_COMPLETE"
    assert receipt["result"] == "PASS"
    assert len(receipt["receipt_sha256"]) == 64
    assert set(receipt["external_receipt_sha256s"]) == {
        "foundation_receipt",
        "native_receipt",
        "operator_receipt",
    }
    assert receipt["authority_limits"] == qualifier.SAFE_AUTHORITY_LIMITS


def test_final_mode_binds_p1_h_and_p1_complete_into_lts_ready(tmp_path: Path) -> None:
    native = tmp_path / "native.json"
    foundation = tmp_path / "foundation.json"
    operator = tmp_path / "operator.json"
    native.write_bytes(_external_receipt("trading-agent-p1-lts-native-proof/v1"))
    foundation.write_bytes(_external_receipt("trading-agent-p1-lts-foundation-proof/v1"))
    operator.write_bytes(
        _external_receipt("trading-agent-p1-lts-operator-acceptance/v1", "ACCEPT")
    )

    receipt, exit_code = qualifier.qualify(
        mode="final",
        native_receipt=native,
        foundation_receipt=foundation,
        operator_receipt=operator,
        source_identity=(COMMIT, TREE),
        source_clean=True,
    )

    assert exit_code == 0
    assert receipt["status"] == "P1_LTS_READY"
    assert receipt["result"] == "PASS"
    assert receipt["p1_h_complete"]["status"] == "P1_H_COMPLETE"
    assert receipt["p1_h_complete_sha256"] == receipt["bindings"]["p1_h_complete"]
    assert len(receipt["bindings"]["p1_complete"]) == 64
    assert all(value is False for value in receipt["authority_limits"].values())


def test_external_source_mismatch_authority_grant_and_dirty_source_fail_closed(
    tmp_path: Path,
) -> None:
    native = tmp_path / "native.json"
    foundation = tmp_path / "foundation.json"
    operator = tmp_path / "operator.json"
    native.write_bytes(_external_receipt("trading-agent-p1-lts-native-proof/v1"))
    foundation.write_bytes(_external_receipt("trading-agent-p1-lts-foundation-proof/v1"))
    unsafe = json.loads(
        _external_receipt("trading-agent-p1-lts-operator-acceptance/v1", "ACCEPT")
    )
    unsafe["authority_limits"]["live_authorized"] = True
    operator.write_bytes(qualifier.canonical_receipt_bytes(unsafe))

    with pytest.raises(qualifier.P1EngineLtsQualificationError, match="authority"):
        qualifier.qualify(
            mode="source-ready",
            native_receipt=native,
            foundation_receipt=foundation,
            operator_receipt=operator,
            source_identity=(COMMIT, TREE),
            source_clean=True,
        )

    operator.write_bytes(
        _external_receipt("trading-agent-p1-lts-operator-acceptance/v1", "ACCEPT")
    )
    receipt, exit_code = qualifier.qualify(
        mode="source-ready",
        native_receipt=native,
        foundation_receipt=foundation,
        operator_receipt=operator,
        source_identity=(COMMIT, TREE),
        source_clean=False,
    )
    assert exit_code == 2
    assert receipt["status"] == "HELD_DIRTY_SOURCE"


def test_external_receipt_must_be_canonical_and_exact(tmp_path: Path) -> None:
    receipt = tmp_path / "native.json"
    receipt.write_bytes(
        _external_receipt("trading-agent-p1-lts-native-proof/v1") + b"\n"
    )

    with pytest.raises(qualifier.P1EngineLtsQualificationError, match="canonical"):
        qualifier.load_external_receipt(
            receipt,
            schema="trading-agent-p1-lts-native-proof/v1",
            verdict="PASS",
            source_identity=(COMMIT, TREE),
        )


def test_external_receipt_requires_canonical_evidence_digests(tmp_path: Path) -> None:
    receipt = tmp_path / "native.json"
    payload = json.loads(
        _external_receipt("trading-agent-p1-lts-native-proof/v1")
    )
    payload["evidence_sha256s"] = []
    receipt.write_bytes(qualifier.canonical_receipt_bytes(payload))

    with pytest.raises(qualifier.P1EngineLtsQualificationError, match="evidence"):
        qualifier.load_external_receipt(
            receipt,
            schema="trading-agent-p1-lts-native-proof/v1",
            verdict="PASS",
            source_identity=(COMMIT, TREE),
        )


def test_invalid_candidate_generation_becomes_a_typed_held_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_candidate(_path: Path) -> object:
        raise qualifier.CandidateGenerationError("invalid G1")

    monkeypatch.setattr(qualifier, "load_candidate_generation", invalid_candidate)

    with pytest.raises(qualifier.P1EngineLtsQualificationError, match="invalid G1"):
        qualifier.qualify(
            mode="local",
            source_identity=(COMMIT, TREE),
            source_clean=True,
            local_gate_results=LOCAL_GATES,
        )


def test_local_receipt_is_held_when_an_executable_gate_fails() -> None:
    receipt, exit_code = qualifier.qualify(
        mode="local",
        source_identity=(COMMIT, TREE),
        source_clean=True,
        local_gate_results={**LOCAL_GATES, "P1S_GOLDEN": False},
    )

    assert exit_code == 2
    assert receipt["status"] == "HELD_LOCAL_GATE_FAILURE"


def test_local_receipt_rechecks_source_after_executable_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = iter(((COMMIT, TREE), ("3" * 40, "4" * 40)))
    monkeypatch.setattr(qualifier, "_source_identity", lambda: next(identities))
    monkeypatch.setattr(qualifier, "_source_clean", lambda: True)

    receipt, exit_code = qualifier.qualify(
        mode="local",
        local_gate_results=LOCAL_GATES,
    )

    assert exit_code == 2
    assert receipt["status"] == "HELD_SOURCE_CHANGED_DURING_QUALIFICATION"


def test_make_report_target_is_source_safe_and_explicitly_deferred() -> None:
    completed = subprocess.run(
        ("make", "--no-print-directory", "qualify-p1-engine-lts-report"),
        cwd=qualifier.ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(completed.stdout.splitlines()[-1])
    assert receipt["status"] == "DEFERRED_EXTERNAL"
    assert all(value is False for value in receipt["authority_limits"].values())


def test_report_cli_emits_exactly_one_canonical_json_document() -> None:
    completed = subprocess.run(
        (sys.executable, "scripts/qualify_p1_engine_lts.py", "--mode", "report"),
        cwd=qualifier.ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert len(completed.stdout.splitlines()) == 1
    assert json.loads(completed.stdout)["status"] == "DEFERRED_EXTERNAL"
