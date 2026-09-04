from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from packages import project_status
from packages.project_status import (
    ProjectStatusError,
    derive_project_status,
    make_pass_receipt,
    receipt_sha256,
    validate_pass_receipt,
)


ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("pre_p3_ready", "arch_ready", "hwc_ready", "allowed"),
    (
        pytest.param("PASS", "PASS", "HELD", False, id="GOV-HWC-001"),
        pytest.param("PASS", "PASS", "PASS", True, id="GOV-HWC-002"),
        pytest.param("HELD", "PASS", "PASS", False, id="GOV-HWC-003"),
        pytest.param("PASS", "HELD", "PASS", False, id="GOV-HWC-004"),
    ),
)
def test_p3_authority_requires_pre_p3_arch_and_hwc_source_readiness(
    monkeypatch: pytest.MonkeyPatch,
    pre_p3_ready: str,
    arch_ready: str,
    hwc_ready: str,
    allowed: bool,
) -> None:
    hwc = project_status.derive_hwc_source_status(ROOT)
    hwc["gates"]["ARCH_CONTRACT_READY"] = arch_ready
    hwc["gates"]["HWC_SOURCE_READY"] = hwc_ready
    monkeypatch.setattr(project_status, "derive_hwc_source_status", lambda root: hwc)
    monkeypatch.setattr(
        project_status,
        "evaluate_pre_p3_provenance",
        lambda root: {
            "blockers": [],
            "gates": {},
            "latest_receipts": {},
            "legacy_receipts": {},
            "pre_p3_ready": pre_p3_ready,
            "provenance": {},
        },
    )

    status = derive_project_status(ROOT)

    assert status["p3_alpha_development_allowed"] is allowed
    assert status["current_phase"] == (
        "P3_ALPHA_DEVELOPMENT" if allowed else "PRE_P3_CLOSURE"
    )


def test_p0_receipt_binds_the_published_promotion_dossier() -> None:
    receipt = json.loads(
        (
            ROOT
            / "docs/implementation/pre-p3/receipts/p0-source-complete-v1.json"
        ).read_bytes()
    )

    validate_pass_receipt(receipt, "P0_SOURCE_COMPLETE")
    assert receipt["source_sha"] == "e0baa410cdcf0de4344d58ad82fd8a56788f84df"
    assert receipt["source_tree"] == "c066f28b97dc1e1e09fced9527a2f5c50322be12"
    assert "3a130e5ff0b52cc948e3c1b56dceabc5aba739e0b83f06de9884ce91d7bfdbe6" in receipt[
        "evidence_sha256s"
    ]


def test_gate_receipts_are_self_hashing_and_fail_closed() -> None:
    receipt = make_pass_receipt(
        "P2_SOURCE_COMPLETE",
        source_sha="a" * 40,
        source_tree="b" * 40,
        evidence_sha256s=("d" * 64, "c" * 64),
    )

    assert receipt["evidence_sha256s"] == ["c" * 64, "d" * 64]
    assert validate_pass_receipt(receipt, "P2_SOURCE_COMPLETE") == receipt
    forged = receipt | {"status": "HELD"}
    with pytest.raises(ProjectStatusError):
        validate_pass_receipt(forged, "P2_SOURCE_COMPLETE")
    forged = receipt | {"authority": receipt["authority"] | {"network": True}}
    with pytest.raises(ProjectStatusError):
        validate_pass_receipt(forged, "P2_SOURCE_COMPLETE")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_sha", "z" * 40),
        ("source_tree", "z" * 40),
        ("evidence_sha256s", ["z" * 64]),
    ),
)
def test_gate_receipts_reject_non_hex_content_identities(
    field: str, value: object
) -> None:
    receipt = make_pass_receipt(
        "P2_SOURCE_COMPLETE",
        source_sha="a" * 40,
        source_tree="b" * 40,
        evidence_sha256s=("c" * 64,),
    )
    forged = receipt | {field: value}
    forged["receipt_sha256"] = receipt_sha256(forged)

    with pytest.raises(ProjectStatusError):
        validate_pass_receipt(forged, "P2_SOURCE_COMPLETE")


def test_status_is_derived_and_never_promotes_live_authority() -> None:
    status = derive_project_status(ROOT)

    assert status["gates"]["P1_COMPLETE"] == "PASS"
    assert status["gates"]["P0"] == "P0_SOURCE_COMPLETE"
    readiness_gates = (
        "P1_H_COMPLETE",
        "P1_LTS_READY",
        "P2_SOURCE_COMPLETE",
        "P2_RUNTIME_QUALIFIED",
        "P2_QUALIFIED",
        "P3_BASELINES_FROZEN",
        "P3_EVALUATION_PROTOCOL_FROZEN",
        "ALPHA_REGISTRY_FOUNDATION",
    )
    assert {status["gates"][gate] for gate in readiness_gates} <= {"HELD", "PASS"}
    assert status["gates"]["PRE_P3_READY"] in {"HELD", "PASS"}
    assert status["gates"]["PROJECT_STATUS_AUTHORITY"] == "PASS"
    assert status["execution_scope"] == "PAPER_LOCAL_ONLY"
    assert status["p3_alpha_development_allowed"] is (
        status["gates"]["PRE_P3_READY"] == "PASS"
        and status["gates"]["ARCH_CONTRACT_READY"] == "PASS"
        and status["gates"]["HWC_SOURCE_READY"] == "PASS"
    )
    assert status["live_eligible"] is False
    assert status["live_enabled"] is False
    assert set(status["authority"].values()) == {False}
    assert "P0: P0_SOURCE_COMPLETE absent" not in status["blockers"]
    assert status["latest_receipts"]["P0"]["path"].endswith(
        "p0-source-complete-v1.json"
    )
    assert status["schema_version"] == "trading-agent-project-status-v2"


def test_committed_project_status_is_the_canonical_derivation() -> None:
    """Break caught: a manually asserted PASS survives canonical CI."""
    committed = (ROOT / "docs/implementation/project-status.json").read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/derive_project_status.py",
            "--check",
            "docs/implementation/project-status.json",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert committed == json.dumps(
        derive_project_status(ROOT),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode() + b"\n"


def test_status_cli_is_canonical_and_check_mode_detects_drift(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/derive_project_status.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.encode() == json.dumps(
        json.loads(result.stdout),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode() + b"\n"

    path = tmp_path / "status.json"
    path.write_text(result.stdout)
    checked = subprocess.run(
        [sys.executable, "scripts/derive_project_status.py", "--check", str(path)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0
    path.write_bytes(path.read_bytes() + hashlib.sha256(b"drift").digest())
    checked = subprocess.run(
        [sys.executable, "scripts/derive_project_status.py", "--check", str(path)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 1


def test_status_cli_writes_the_canonical_projection_atomically(tmp_path: Path) -> None:
    """Break caught: operators hand-edit generated project authority."""
    path = tmp_path / "status.json"
    result = subprocess.run(
        [sys.executable, "scripts/derive_project_status.py", "--write", str(path)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert path.read_bytes() == json.dumps(
        derive_project_status(ROOT),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode() + b"\n"
    assert not list(tmp_path.glob(".*.tmp"))


def test_status_cli_rejects_symlinked_projection_paths(tmp_path: Path) -> None:
    """Break caught: status generation follows a leaf symlink and overwrites its target."""
    target = tmp_path / "target.json"
    target.write_bytes(b"preserve\n")
    link = tmp_path / "status.json"
    link.symlink_to(target)

    written = subprocess.run(
        [sys.executable, "scripts/derive_project_status.py", "--write", str(link)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    checked = subprocess.run(
        [sys.executable, "scripts/derive_project_status.py", "--check", str(link)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert written.returncode == 2
    assert checked.returncode == 1
    assert target.read_bytes() == b"preserve\n"


def test_project_status_check_rejects_manually_changed_p3_authority(
    tmp_path: Path,
) -> None:
    """GOV-HWC-009: generated authority cannot be changed by hand."""
    forged = derive_project_status(ROOT)
    forged["p3_alpha_development_allowed"] = not forged[
        "p3_alpha_development_allowed"
    ]
    path = tmp_path / "project-status.json"
    path.write_bytes(
        json.dumps(forged, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    )

    checked = subprocess.run(
        [sys.executable, "scripts/derive_project_status.py", "--check", str(path)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert checked.returncode == 1


def test_legacy_receipts_cannot_authorize_p3_without_hwc_source_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GOV-HWC-010: historical v1 receipts never bypass current HWC readiness."""
    hwc = project_status.derive_hwc_source_status(ROOT)
    hwc["gates"]["HWC_PORTABLE_QUALIFIED"] = "HELD"
    hwc["gates"]["HWC_SOURCE_READY"] = "HELD"
    monkeypatch.setattr(project_status, "derive_hwc_source_status", lambda root: hwc)

    status = derive_project_status(ROOT)

    assert status["legacy_receipts"]
    assert status["gates"]["HWC_SOURCE_READY"] == "HELD"
    assert status["p3_alpha_development_allowed"] is False
