from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.project_status import make_pass_receipt, receipt_sha256, validate_pass_receipt
from scripts.qualify_pre_p3 import QualificationError, p1_bridge, p2_final, pre_p3_final


SHA = "a" * 40
TREE = "b" * 40


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def test_p1_native_receipts_bridge_to_common_fail_closed_gate_receipts(
    tmp_path: Path,
) -> None:
    nested = {
        "authority_limits": {
            "broker_access_authorized": False,
            "database_runtime_authorized": False,
            "live_authorized": False,
            "network_authorized": False,
            "production_authorized": False,
        },
        "execution_scope": "PAPER_LOCAL_ONLY",
        "result": "PASS",
        "schema": "trading-agent-p1-h-complete/v1",
        "source": {"clean": True, "commit": SHA, "tree": TREE},
        "status": "P1_H_COMPLETE",
    }
    nested["receipt_sha256"] = __import__("hashlib").sha256(
        canonical_json_bytes(nested)
    ).hexdigest()
    native = {
        "authority_limits": nested["authority_limits"],
        "execution_scope": "PAPER_LOCAL_ONLY",
        "p1_h_complete": nested,
        "p1_h_complete_sha256": nested["receipt_sha256"],
        "result": "PASS",
        "schema": "trading-agent-p1-lts-ready/v1",
        "source": {"clean": True, "commit": SHA, "tree": TREE},
        "status": "P1_LTS_READY",
    }
    native["receipt_sha256"] = __import__("hashlib").sha256(
        canonical_json_bytes(native)
    ).hexdigest()
    source = tmp_path / "native.json"
    _write(source, native)

    p1_bridge(source, tmp_path / "p1h.json", tmp_path / "p1lts.json")

    p1h = json.loads((tmp_path / "p1h.json").read_bytes())
    p1lts = json.loads((tmp_path / "p1lts.json").read_bytes())
    validate_pass_receipt(p1h, "P1_H_COMPLETE")
    validate_pass_receipt(p1lts, "P1_LTS_READY")
    assert p1h["receipt_sha256"] in p1lts["evidence_sha256s"]


@pytest.mark.parametrize("attack", ("authority", "nested_source"))
def test_p1_bridge_rejects_authority_or_nested_source_forgery(
    tmp_path: Path,
    attack: str,
) -> None:
    authority = {
        "broker_access_authorized": False,
        "database_runtime_authorized": False,
        "live_authorized": False,
        "network_authorized": False,
        "production_authorized": False,
    }
    nested = {
        "authority_limits": authority,
        "execution_scope": "PAPER_LOCAL_ONLY",
        "result": "PASS",
        "schema": "trading-agent-p1-h-complete/v1",
        "source": {"clean": True, "commit": SHA, "tree": TREE},
        "status": "P1_H_COMPLETE",
    }
    if attack == "nested_source":
        nested["source"] = {"clean": True, "commit": "c" * 40, "tree": TREE}
    nested["receipt_sha256"] = __import__("hashlib").sha256(
        canonical_json_bytes(nested)
    ).hexdigest()
    native = {
        "authority_limits": authority
        | ({"network_authorized": True} if attack == "authority" else {}),
        "execution_scope": "PAPER_LOCAL_ONLY",
        "p1_h_complete": nested,
        "p1_h_complete_sha256": nested["receipt_sha256"],
        "result": "PASS",
        "schema": "trading-agent-p1-lts-ready/v1",
        "source": {"clean": True, "commit": SHA, "tree": TREE},
        "status": "P1_LTS_READY",
    }
    native["receipt_sha256"] = __import__("hashlib").sha256(
        canonical_json_bytes(native)
    ).hexdigest()
    source = tmp_path / "native.json"
    _write(source, native)

    with pytest.raises(QualificationError):
        p1_bridge(source, tmp_path / "p1h.json", tmp_path / "p1lts.json")


def test_p2_final_binds_source_and_runtime_receipts(tmp_path: Path) -> None:
    source = make_pass_receipt(
        "P2_SOURCE_COMPLETE",
        source_sha=SHA,
        source_tree=TREE,
        evidence_sha256s=("1" * 64,),
    )
    runtime = make_pass_receipt(
        "P2_RUNTIME_QUALIFIED",
        source_sha=SHA,
        source_tree=TREE,
        evidence_sha256s=("2" * 64,),
    )
    _write(tmp_path / "source.json", source)
    _write(tmp_path / "runtime.json", runtime)

    p2_final(tmp_path / "source.json", tmp_path / "runtime.json", tmp_path / "final.json")

    receipt = json.loads((tmp_path / "final.json").read_bytes())
    validate_pass_receipt(receipt, "P2_QUALIFIED")
    assert set(receipt["evidence_sha256s"]) == {
        source["receipt_sha256"],
        runtime["receipt_sha256"],
    }


def test_pre_p3_final_requires_all_eight_same_source_gates(tmp_path: Path) -> None:
    gates = {
        "P1_H_COMPLETE": "p1-h-complete-v1.json",
        "P1_LTS_READY": "p1-lts-ready-v1.json",
        "P2_SOURCE_COMPLETE": "p2-source-complete-v1.json",
        "P2_RUNTIME_QUALIFIED": "p2-runtime-qualified-v1.json",
        "P2_QUALIFIED": "p2-qualified-v1.json",
        "P3_BASELINES_FROZEN": "p3-baselines-frozen-v1.json",
        "P3_EVALUATION_PROTOCOL_FROZEN": "p3-evaluation-protocol-frozen-v1.json",
        "ALPHA_REGISTRY_FOUNDATION": "alpha-registry-foundation-v1.json",
    }
    for index, (gate, name) in enumerate(gates.items(), start=1):
        _write(
            tmp_path / name,
            make_pass_receipt(
                gate,
                source_sha=SHA,
                source_tree=TREE,
                evidence_sha256s=(f"{index:x}" * 64,),
            ),
        )

    final_gates = {
        "P1_COMPLETE",
        "P1_H_COMPLETE",
        "P1_LTS_READY",
        "P2_QUALIFIED",
        "PROJECT_STATUS_AUTHORITY",
        "P3_BASELINES_FROZEN",
        "P3_EVALUATION_PROTOCOL_FROZEN",
        "ALPHA_REGISTRY_FOUNDATION",
    }
    status = {
        "authority": {"broker": False, "live": False, "network": False, "production": False},
        "gates": {**{gate: "PASS" for gate in final_gates}, "P0": "P0_SOURCE_COMPLETE"},
        "live_eligible": False,
        "live_enabled": False,
    }
    pre_p3_final(
        tmp_path,
        tmp_path / "pre-p3.json",
        status_payload=status,
    )

    receipt = json.loads((tmp_path / "pre-p3.json").read_bytes())
    assert receipt["schema_version"] == "pre-p3-certification-v1"
    assert receipt["status"] == "PRE_P3_READY"
    assert set(receipt["bindings"]) == final_gates
    assert receipt["receipt_sha256"] == receipt_sha256(receipt)
    assert receipt["p3_alpha_development_allowed"] is True
    assert receipt["live_eligible"] is False
    assert receipt["live_enabled"] is False


def test_pre_p3_final_rejects_pending_p0_even_when_other_gates_pass(
    tmp_path: Path,
) -> None:
    gates = {
        "P1_H_COMPLETE": "p1-h-complete-v1.json",
        "P1_LTS_READY": "p1-lts-ready-v1.json",
        "P2_SOURCE_COMPLETE": "p2-source-complete-v1.json",
        "P2_RUNTIME_QUALIFIED": "p2-runtime-qualified-v1.json",
        "P2_QUALIFIED": "p2-qualified-v1.json",
        "P3_BASELINES_FROZEN": "p3-baselines-frozen-v1.json",
        "P3_EVALUATION_PROTOCOL_FROZEN": "p3-evaluation-protocol-frozen-v1.json",
        "ALPHA_REGISTRY_FOUNDATION": "alpha-registry-foundation-v1.json",
    }
    for index, (gate, name) in enumerate(gates.items(), start=1):
        _write(
            tmp_path / name,
            make_pass_receipt(
                gate,
                source_sha=SHA,
                source_tree=TREE,
                evidence_sha256s=(f"{index:x}" * 64,),
            ),
        )
    status = {
        "authority": {"broker": False, "live": False, "network": False, "production": False},
        "gates": {
            **{gate: "PASS" for gate in {
                "P1_COMPLETE",
                "P1_H_COMPLETE",
                "P1_LTS_READY",
                "P2_QUALIFIED",
                "PROJECT_STATUS_AUTHORITY",
                "P3_BASELINES_FROZEN",
                "P3_EVALUATION_PROTOCOL_FROZEN",
                "ALPHA_REGISTRY_FOUNDATION",
            }},
            "P0": "QUALIFICATION_PENDING",
        },
        "live_eligible": False,
        "live_enabled": False,
    }

    with pytest.raises(QualificationError, match="P0"):
        pre_p3_final(tmp_path, tmp_path / "pre-p3.json", status_payload=status)
