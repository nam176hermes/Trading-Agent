from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.pre_p3_provenance import (
    SOURCE_CLOSURE_POLICY_SHA256,
    make_v2_gate_receipt,
    validate_v2_gate_receipt,
)
from packages.project_status import make_pass_receipt, receipt_sha256, validate_pass_receipt
from scripts.validate_disposable_postgres_approval import (
    load_protected_approval_record,
    validate_disposable_postgres_approval_record,
    validate_source_binding_files,
)
from scripts.validate_disposable_postgres_fixture_plan import (
    load_protected_fixture_plan,
    validate_disposable_postgres_fixture_plan,
)
from scripts import qualify_pre_p3
from scripts.qualify_pre_p3 import (
    QualificationError,
    p1_bridge,
    p2_final,
    pre_p3_final,
)


SHA = "a" * 40
TREE = "b" * 40


def _p1_topology_receipt() -> dict[str, object]:
    return {
        "external_outcomes": {
            "EXT-DISPOSABLE-PG-GREEN": "DEFERRED",
            "EXT-DISPOSABLE-PG-RED": "DEFERRED",
            "EXT-DISPOSABLE-PG-RED-EVIDENCE": "DEFERRED",
            "EXT-LEGACY-UV-AUTHORITY": "PASS",
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": "PASS",
            "EXT-PHASE3B-CORPUS": "PASS",
        },
        "foundation_head_sha": SHA,
        "foundation_run_id": "12345",
        "lane": "P1_U04_HOST_TOPOLOGY",
        "native_status": "PASS",
        "outcome": "PASS",
        "portable_closure_status": "PASS",
        "schema": "p1-u04-host-topology-receipt-v1",
    }


def _set_operator_context(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "GITHUB_ACTOR": "nam176hermes",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "nam176hermes/Trading-Agent",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_SHA": SHA,
        "GITHUB_WORKFLOW": "Host Authority",
        "GITHUB_WORKFLOW_REF": "nam176hermes/Trading-Agent/.github/workflows/host-authority.yml@refs/heads/main",
        "GITHUB_WORKFLOW_SHA": SHA,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


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


def test_receipt_writer_never_replaces_existing_evidence(tmp_path: Path) -> None:
    """Break caught: a rerun silently rewrites historical qualification."""
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
    output = tmp_path / "final.json"
    output.write_bytes(b"historical evidence\n")

    with pytest.raises(QualificationError, match="already exists"):
        p2_final(tmp_path / "source.json", tmp_path / "runtime.json", output)

    assert output.read_bytes() == b"historical evidence\n"


def test_qualification_rejects_symlinked_receipt_inputs(tmp_path: Path) -> None:
    """Break caught: effective evidence bytes escape the reviewed receipt path."""
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
    _write(tmp_path / "real-source.json", source)
    (tmp_path / "source.json").symlink_to("real-source.json")
    _write(tmp_path / "runtime.json", runtime)

    with pytest.raises(QualificationError, match="regular file"):
        p2_final(
            tmp_path / "source.json",
            tmp_path / "runtime.json",
            tmp_path / "final.json",
        )


def test_p2_final_v2_binds_both_upstream_receipts() -> None:
    source_identity = {
        "closure_policy_sha256": SOURCE_CLOSURE_POLICY_SHA256,
        "closure_schema_version": "trading-agent-source-closure-v1",
        "closure_sha256": "2" * 64,
        "commit_sha": SHA,
        "tree_sha": TREE,
    }
    qualification = {
        "completed_at_utc": "2026-09-01T12:00:00Z",
        "producer": "scripts/qualify_pre_p3.py",
        "run_attempt": "1",
        "run_id": "12345",
    }
    evidence = ({
        "kind": "EXTERNAL_RECEIPT",
        "locator": "test-proof",
        "name": "test-proof",
        "sha256": "3" * 64,
    },)
    source = make_v2_gate_receipt(
        "P2_SOURCE_COMPLETE",
        source=source_identity,
        evidence=evidence,
        qualification=qualification,
    )
    runtime = make_v2_gate_receipt(
        "P2_RUNTIME_QUALIFIED",
        source=source_identity,
        evidence=evidence,
        qualification=qualification,
    )

    receipt = qualify_pre_p3.p2_final_v2(source, runtime, qualification=qualification)

    validate_v2_gate_receipt(receipt, "P2_QUALIFIED")
    assert {
        item["locator"]: item["sha256"]
        for item in receipt["evidence"]
    } == {
        "P2_RUNTIME_QUALIFIED": runtime["receipt_sha256"],
        "P2_SOURCE_COMPLETE": source["receipt_sha256"],
    }


def test_v2_qualification_metadata_requires_real_ci_run_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: synthetic or unavailable run metadata produces PASS evidence."""
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)

    with pytest.raises(QualificationError, match="run identity"):
        qualify_pre_p3.qualification_metadata()


def test_p2_fixture_issuer_creates_private_source_bound_green_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "closure_policy_sha256": SOURCE_CLOSURE_POLICY_SHA256,
        "closure_schema_version": "trading-agent-source-closure-v1",
        "closure_sha256": "c" * 64,
        "commit_sha": SHA,
        "tree_sha": TREE,
    }
    approved_at = datetime(2026, 9, 1, 23, 30, tzinfo=UTC)
    monkeypatch.setattr(qualify_pre_p3, "_source_v2", lambda: source)

    approval_path, plan_path = qualify_pre_p3.issue_p2_fixture(
        tmp_path / "authority",
        operator="nam176hermes",
        reviewer="codex-governance-auditor",
        approved_at=approved_at,
    )

    assert (tmp_path / "authority").stat().st_mode & 0o777 == 0o700
    assert approval_path.stat().st_mode & 0o777 == 0o600
    assert plan_path.stat().st_mode & 0o777 == 0o600
    approval = load_protected_approval_record(approval_path)
    plan = load_protected_fixture_plan(plan_path)
    validate_disposable_postgres_approval_record(
        approval,
        expected_scope="DISPOSABLE_PG_GREEN",
        expected_commit=SHA,
        expected_tree=TREE,
        expected_sql_sha256=None,
        runtime_setting_names=frozenset(),
        now=approved_at,
    )
    validate_source_binding_files(approval, qualify_pre_p3.ROOT)
    slots = validate_disposable_postgres_fixture_plan(
        plan,
        approval,
        source_commit=SHA,
        source_tree=TREE,
        now=approved_at,
    )
    assert [(slot.test_path, slot.operation_id, slot.ordinal) for slot in slots] == [
        (
            "tests/security_master/test_postgres_runtime.py",
            "p2-security-master-runtime-green-v1",
            1,
        )
    ]


def test_p2_runtime_environment_requires_external_fixture_and_rejects_runtime_dsn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "closure_policy_sha256": SOURCE_CLOSURE_POLICY_SHA256,
        "closure_schema_version": "trading-agent-source-closure-v1",
        "closure_sha256": "c" * 64,
        "commit_sha": SHA,
        "tree_sha": TREE,
    }
    approved_at = datetime(2026, 9, 1, 23, 30, tzinfo=UTC)
    monkeypatch.setattr(qualify_pre_p3, "_source_v2", lambda: source)
    approval_path, plan_path = qualify_pre_p3.issue_p2_fixture(
        tmp_path / "authority",
        operator="nam176hermes",
        reviewer="codex-governance-auditor",
        approved_at=approved_at,
    )
    monkeypatch.setenv("DISPOSABLE_PG_GREEN_APPROVAL_RECORD", str(approval_path))
    monkeypatch.setenv("DISPOSABLE_PG_GREEN_FIXTURE_PLAN", str(plan_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://forbidden.invalid/runtime")

    with pytest.raises(QualificationError, match="runtime database settings"):
        qualify_pre_p3.p2_runtime_environment(now=approved_at)

    monkeypatch.delenv("DATABASE_URL")
    environment = qualify_pre_p3.p2_runtime_environment(now=approved_at)
    assert environment["TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES"] == "YES"
    assert environment["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"] == "DISPOSABLE_PG_GREEN"
    assert environment["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"] == str(approval_path)
    assert environment["TRADING_TEST_DISPOSABLE_FIXTURE_PLAN"] == str(plan_path)
    assert environment["TRADING_TEST_REQUESTED_MODE"] == "paper"
    assert environment["TRADING_TEST_EFFECTIVE_MODE"] == "paper"
    assert environment["LIVE_EXECUTION_ENABLED"] == "false"
    assert environment["LIVE_TRADING_APPROVED"] == "false"
    assert environment["LIVE_TRADING_ENABLED"] == "false"
    assert environment["TRADING_TEST_KILL_SWITCH"] == "INACTIVE"
    assert all(
        name not in environment
        for name in ("DATABASE_URL", "POSTGRES_URL", "TRADING_DATABASE_URL")
    )


def test_p2_fixture_issuer_rejects_source_checkout_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        qualify_pre_p3,
        "_source_v2",
        lambda: {
            "commit_sha": SHA,
            "tree_sha": TREE,
        },
    )

    with pytest.raises(QualificationError, match="outside source checkout"):
        qualify_pre_p3.issue_p2_fixture(
            qualify_pre_p3.ROOT / "scripts",
            operator="nam176hermes",
            reviewer="codex-governance-auditor",
            approved_at=datetime(2026, 9, 1, 23, 30, tzinfo=UTC),
        )


def test_p2_fixture_issuer_rejects_git_metadata_destination() -> None:
    git_common_dir = Path(
        subprocess.run(
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
            cwd=qualify_pre_p3.ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    )

    with pytest.raises(QualificationError, match="outside (source checkout|Git metadata)"):
        qualify_pre_p3.issue_p2_fixture(
            git_common_dir / "pre-p3-authority",
            operator="nam176hermes",
            reviewer="codex-governance-auditor",
            approved_at=datetime(2026, 9, 1, 23, 30, tzinfo=UTC),
        )


def test_p2_runtime_rejects_source_change_during_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "closure_policy_sha256": SOURCE_CLOSURE_POLICY_SHA256,
        "closure_schema_version": "trading-agent-source-closure-v1",
        "closure_sha256": "c" * 64,
        "commit_sha": SHA,
        "tree_sha": TREE,
    }
    changed_source = source | {
        "closure_sha256": "d" * 64,
        "commit_sha": "e" * 40,
        "tree_sha": "f" * 40,
    }
    qualification = {
        "completed_at_utc": "2026-09-01T23:30:00Z",
        "producer": "scripts/qualify_pre_p3.py",
        "run_attempt": "1",
        "run_id": "12345",
    }
    approved_at = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(qualify_pre_p3, "_source_v2", lambda: source)
    approval_path, plan_path = qualify_pre_p3.issue_p2_fixture(
        tmp_path / "authority",
        operator="nam176hermes",
        reviewer="codex-governance-auditor",
        approved_at=approved_at,
    )
    monkeypatch.setenv("DISPOSABLE_PG_GREEN_APPROVAL_RECORD", str(approval_path))
    monkeypatch.setenv("DISPOSABLE_PG_GREEN_FIXTURE_PLAN", str(plan_path))
    snapshots = iter((source, changed_source))
    monkeypatch.setattr(qualify_pre_p3, "_source_v2", lambda: next(snapshots))
    monkeypatch.setattr(
        qualify_pre_p3,
        "_run",
        lambda *command, environment=None: (
            "postgres (PostgreSQL) 16.10" if "--version" in command else ""
        ),
    )

    with pytest.raises(QualificationError, match="source changed"):
        qualify_pre_p3.p2_runtime_v2(
            tmp_path / "runtime.json",
            qualification=qualification,
        )

    assert not (tmp_path / "runtime.json").exists()


def test_p1_external_receipts_bind_exact_protected_run_and_safe_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    topology = tmp_path / "topology.json"
    _write(topology, _p1_topology_receipt())
    _set_operator_context(monkeypatch)
    monkeypatch.setattr(
        qualify_pre_p3,
        "_source_v2",
        lambda: {
            "closure_policy_sha256": SOURCE_CLOSURE_POLICY_SHA256,
            "closure_schema_version": "trading-agent-source-closure-v1",
            "closure_sha256": "c" * 64,
            "commit_sha": SHA,
            "tree_sha": TREE,
        },
    )

    qualify_pre_p3.p1_external_v1(
        topology,
        tmp_path / "receipts",
        operation="p2-security-master-runtime-green-v1",
    )

    names = {
        "p1-foundation-proof-v1.json": ("trading-agent-p1-lts-foundation-proof/v1", "PASS"),
        "p1-native-proof-v1.json": ("trading-agent-p1-lts-native-proof/v1", "PASS"),
        "p1-operator-acceptance-v1.json": ("trading-agent-p1-lts-operator-acceptance/v1", "ACCEPT"),
    }
    for name, (schema, verdict) in names.items():
        path = tmp_path / "receipts" / name
        assert not path.read_bytes().endswith(b"\n")
        receipt = json.loads(path.read_bytes())
        assert receipt["schema"] == schema
        assert receipt["verdict"] == verdict
        assert receipt["source_commit"] == SHA
        assert receipt["source_tree"] == TREE
        assert all(value is False for value in receipt["authority_limits"].values())
        assert len(receipt["evidence_sha256s"]) == 1

    decision = json.loads(
        (tmp_path / "receipts" / "p1-operator-decision-v1.json").read_bytes()
    )
    assert decision["operation"] == "p2-security-master-runtime-green-v1"
    assert decision["actor"] == "nam176hermes"
    assert decision["sha"] == SHA
    assert all(value is False for value in decision["authority"].values())


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("GITHUB_ACTOR", "contributor", "operator"),
        ("GITHUB_EVENT_NAME", "push", "operator"),
        ("GITHUB_REF", "refs/heads/feature", "operator"),
        ("GITHUB_SHA", "d" * 40, "operator"),
        ("GITHUB_WORKFLOW_SHA", "d" * 40, "operator"),
    ],
)
def test_p1_external_receipts_reject_wrong_operator_run_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    topology = tmp_path / "topology.json"
    _write(topology, _p1_topology_receipt())
    _set_operator_context(monkeypatch)
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        qualify_pre_p3,
        "_source_v2",
        lambda: {"commit_sha": SHA, "tree_sha": TREE},
    )

    with pytest.raises(QualificationError, match=message):
        qualify_pre_p3.p1_external_v1(
            topology,
            tmp_path / "receipts",
            operation="p2-security-master-runtime-green-v1",
        )


def test_p1_external_receipts_reject_mixed_or_incomplete_host_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _p1_topology_receipt()
    payload["native_status"] = "DEFERRED"
    topology = tmp_path / "topology.json"
    _write(topology, payload)
    _set_operator_context(monkeypatch)
    monkeypatch.setattr(
        qualify_pre_p3,
        "_source_v2",
        lambda: {"commit_sha": SHA, "tree_sha": TREE},
    )

    with pytest.raises(QualificationError, match="topology"):
        qualify_pre_p3.p1_external_v1(
            topology,
            tmp_path / "receipts",
            operation="p2-security-master-runtime-green-v1",
        )


def test_p1_external_receipts_reject_unapproved_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    topology = tmp_path / "topology.json"
    _write(topology, _p1_topology_receipt())
    _set_operator_context(monkeypatch)
    monkeypatch.setattr(
        qualify_pre_p3,
        "_source_v2",
        lambda: {"commit_sha": SHA, "tree_sha": TREE},
    )

    with pytest.raises(QualificationError, match="operator"):
        qualify_pre_p3.p1_external_v1(
            topology,
            tmp_path / "receipts",
            operation="unapproved-operation",
        )


def test_v2_qualification_rejects_untracked_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: qualification reports HEAD while testing uncommitted bytes."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(("git", "init", "-b", "main"), cwd=root, check=True, capture_output=True)
    subprocess.run(("git", "config", "user.name", "Test Operator"), cwd=root, check=True)
    subprocess.run(
        ("git", "config", "user.email", "operator@example.invalid"), cwd=root, check=True
    )
    (root / "source.py").write_text("VALUE = 1\n")
    subprocess.run(("git", "add", "source.py"), cwd=root, check=True)
    subprocess.run(("git", "commit", "-m", "source"), cwd=root, check=True, capture_output=True)
    (root / "untracked.py").write_text("VALUE = 2\n")
    monkeypatch.setattr(qualify_pre_p3, "ROOT", root)

    with pytest.raises(QualificationError, match="clean source tree"):
        qualify_pre_p3._source_v2()


def test_promotion_issuance_rejects_dirty_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: post-main proof is issued from an uncommitted worktree."""
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(qualify_pre_p3, "_git", lambda *args: "?? untracked.py")

    with pytest.raises(QualificationError, match="clean source tree"):
        qualify_pre_p3.promotion_v1(
            tmp_path / "candidate.json",
            tmp_path / "promotion.json",
            promoted_revision="HEAD",
        )


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
