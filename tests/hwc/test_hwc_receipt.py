from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.hwc_status import (
    HWC_GATES,
    HwcStatusError,
    _verify_github_attestation,
    receipt_sha256,
    status_sha256,
    validate_hwc_portable_receipt,
)
from packages.pre_p3_provenance import canonical_source_identity
from scripts.record_hwc_portable_receipt import (
    HwcPortableRecordingError,
    _run_identity,
)


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


def commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-m", message)


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test Operator")
    git(root, "config", "user.email", "operator@example.invalid")
    (root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    commit(root, "source")
    git(
        root,
        "remote",
        "add",
        "origin",
        "https://github.com/nam176hermes/Trading-Agent.git",
    )
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def source_status() -> dict[str, object]:
    gates = {gate: "HELD" for gate in HWC_GATES}
    payload: dict[str, object] = {
        "schema_version": "trading-agent-hwc-status-v1",
        "gates": gates,
        "authority": {
            "broker": False,
            "live": False,
            "network": False,
            "production": False,
        },
        "deployment": {
            "host_qualified": "HELD",
            "release_v2_integrated": "HELD",
            "runtime_active": "HELD",
        },
        "blockers": sorted(f"{gate}: HELD" for gate in HWC_GATES),
    }
    payload["status_sha256"] = status_sha256(payload)
    return payload


def portable_receipt(source: dict[str, str]) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "hwc-portable-qualified-receipt-v1",
        "status": "PASS",
        "source": source,
        "run": {
            "repository": "nam176hermes/Trading-Agent",
            "workflow": "Foundation",
            "workflow_ref": "nam176hermes/Trading-Agent/.github/workflows/foundation.yml@refs/heads/main",
            "event": "push",
            "ref": "refs/heads/main",
            "sha": source["commit_sha"],
            "workflow_sha": source["commit_sha"],
            "run_id": "12345",
            "run_attempt": "1",
        },
        "evidence": {
            "headless_receipt_sha256": "1" * 64,
            "recovery_campaign_sha256": "2" * 64,
            "hwc_boundary_report_sha256": "3" * 64,
            "generated_contract_report_sha256": "4" * 64,
        },
        "authority": {
            "broker": False,
            "live": False,
            "network": False,
            "production": False,
        },
    }
    payload["receipt_sha256"] = receipt_sha256(payload)
    return payload


def test_portable_recorder_requires_exact_protected_foundation_identity() -> None:
    source_sha = "a" * 40
    environment = {
        "GITHUB_REPOSITORY": "nam176hermes/Trading-Agent",
        "GITHUB_WORKFLOW": "Foundation",
        "GITHUB_WORKFLOW_REF": "nam176hermes/Trading-Agent/.github/workflows/foundation.yml@refs/heads/main",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": source_sha,
        "GITHUB_WORKFLOW_SHA": source_sha,
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    assert _run_identity(environment, source_sha)["sha"] == source_sha

    environment["GITHUB_WORKFLOW_REF"] = (
        "nam176hermes/Trading-Agent/.github/workflows/foundation.yml@refs/heads/feature"
    )
    with pytest.raises(HwcPortableRecordingError, match="protected Foundation"):
        _run_identity(environment, source_sha)


def test_external_attestation_verifier_binds_exact_protected_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 0, stdout=b"[{}]", stderr=b"")

    monkeypatch.setattr("packages.hwc_status.subprocess.run", run)
    receipt = tmp_path / "receipt.json"
    source_sha = "a" * 40
    _verify_github_attestation(tmp_path, receipt, source_sha)
    assert calls == [
        (
            [
                "gh",
                "attestation",
                "verify",
                str(receipt),
                "--repo",
                "nam176hermes/Trading-Agent",
                "--signer-workflow",
                "nam176hermes/Trading-Agent/.github/workflows/foundation.yml",
                "--source-ref",
                "refs/heads/main",
                "--source-digest",
                source_sha,
                "--signer-digest",
                source_sha,
                "--deny-self-hosted-runners",
                "--format",
                "json",
            ],
            {
                "cwd": tmp_path,
                "capture_output": True,
                "check": True,
                "timeout": 30,
            },
        )
    ]


def test_valid_generated_hwc_status_is_excluded_but_forgery_is_source(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    before = canonical_source_identity(root)
    path = root / "docs/implementation/hwc/hwc-source-status.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(source_status()) + b"\n")
    commit(root, "valid status")
    assert canonical_source_identity(root)["closure_sha256"] == before["closure_sha256"]

    forged = source_status()
    forged["authority"]["live"] = True  # type: ignore[index]
    forged["status_sha256"] = status_sha256(forged)
    path.write_bytes(canonical_json_bytes(forged) + b"\n")
    commit(root, "forged status")
    assert canonical_source_identity(root)["closure_sha256"] != before["closure_sha256"]


def test_portable_receipt_exclusion_requires_protected_run_and_current_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    source = canonical_source_identity(root)
    receipt = portable_receipt(source)
    path = root / "docs/implementation/hwc/receipts/hwc-portable-qualified-v1.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    commit(root, "portable receipt")
    assert canonical_source_identity(root)["closure_sha256"] == source["closure_sha256"]
    monkeypatch.setattr(
        "packages.hwc_status._verify_github_attestation", lambda *_: None
    )
    validate_hwc_portable_receipt(receipt, root=root)

    stale = json.loads(json.dumps(receipt))
    stale["source"]["closure_sha256"] = "0" * 64
    stale["receipt_sha256"] = receipt_sha256(stale)
    path.write_bytes(canonical_json_bytes(stale) + b"\n")
    commit(root, "stale receipt")
    assert canonical_source_identity(root)["closure_sha256"] != source["closure_sha256"]

    path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    commit(root, "restore portable receipt")
    unknown_source = json.loads(json.dumps(receipt))
    unknown_source["source"]["commit_sha"] = "0" * 40
    unknown_source["run"]["sha"] = "0" * 40
    unknown_source["run"]["workflow_sha"] = "0" * 40
    unknown_source["receipt_sha256"] = receipt_sha256(unknown_source)
    path.write_bytes(canonical_json_bytes(unknown_source) + b"\n")
    commit(root, "unknown source receipt")
    assert canonical_source_identity(root)["closure_sha256"] != source["closure_sha256"]

    path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    commit(root, "restore portable receipt again")
    forged = json.loads(json.dumps(receipt))
    forged["run"]["event"] = "pull_request"
    forged["receipt_sha256"] = receipt_sha256(forged)
    path.write_bytes(canonical_json_bytes(forged) + b"\n")
    commit(root, "forged receipt")
    assert canonical_source_identity(root)["closure_sha256"] != source["closure_sha256"]


def test_feature_branch_cannot_self_declare_a_protected_main_receipt(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    (root / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
    commit(root, "feature source")
    receipt = portable_receipt(canonical_source_identity(root))
    path = root / "docs/implementation/hwc/receipts/hwc-portable-qualified-v1.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    commit(root, "feature receipt")

    with pytest.raises(HwcStatusError, match="protected main|stale"):
        validate_hwc_portable_receipt(receipt, root=root)


def test_portable_receipt_requires_external_github_attestation(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    receipt = portable_receipt(canonical_source_identity(root))
    path = root / "docs/implementation/hwc/receipts/hwc-portable-qualified-v1.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    commit(root, "unattested portable receipt")

    with pytest.raises(HwcStatusError, match="attestation"):
        validate_hwc_portable_receipt(receipt, root=root)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value["authority"].update({"production": True}),
        lambda value: value["evidence"].update({"headless_receipt_sha256": "x" * 64}),
        lambda value: value["run"].update({"ref": "refs/heads/feature"}),
        lambda value: value["run"].update({"workflow_ref": "attacker/workflow"}),
        lambda value: value.update({"receipt_sha256": "0" * 64}),
    ),
)
def test_portable_receipt_rejects_forged_fields(tmp_path: Path, mutation) -> None:
    root = repository(tmp_path)
    receipt = portable_receipt(canonical_source_identity(root))
    mutation(receipt)
    if receipt["receipt_sha256"] != "0" * 64:
        receipt["receipt_sha256"] = receipt_sha256(receipt)
    with pytest.raises(HwcStatusError):
        validate_hwc_portable_receipt(receipt, root=root)
