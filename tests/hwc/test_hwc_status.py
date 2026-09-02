from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from packages import project_status
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.hwc_status import (
    HWC_GATES,
    HwcStatusError,
    derive_hwc_source_status,
    status_sha256,
    validate_hwc_source_status,
)
from scripts import qualify_pre_p3


ROOT = Path(__file__).resolve().parents[2]


def test_current_source_derives_arch_contract_without_later_authority() -> None:
    status = derive_hwc_source_status(ROOT)

    assert validate_hwc_source_status(status) == status
    assert status["gates"]["ARCH_CONTRACT_READY"] == "PASS"
    assert all(
        status["gates"][gate] == "PASS"
        for gate in HWC_GATES[: HWC_GATES.index("ARCH_CONTRACT_READY") + 1]
    )
    assert status["gates"]["HWC_SOURCE_READY"] == "HELD"
    assert status["status_sha256"] == status_sha256(status)
    assert status["blockers"] == sorted(status["blockers"])
    assert set(status["authority"].values()) == {False}
    assert set(status["deployment"].values()) == {"HELD"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda value: value["authority"].update({"live": True}), "authority"),
        (lambda value: value["blockers"].reverse(), "blockers"),
        (
            lambda value: value["gates"].update({"ARCH_CONTRACT_READY": "HELD"}),
            "gate",
        ),
        (lambda value: value.update({"status_sha256": "0" * 64}), "digest"),
    ),
)
def test_status_validator_rejects_forged_authority_order_logic_and_digest(
    mutation, message: str
) -> None:
    payload = json.loads(json.dumps(derive_hwc_source_status(ROOT)))
    mutation(payload)
    if message != "digest":
        payload["status_sha256"] = status_sha256(payload)
    with pytest.raises(HwcStatusError, match=message):
        validate_hwc_source_status(payload)


def test_project_status_requires_arch_contract_even_with_pre_p3_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hwc = derive_hwc_source_status(ROOT)
    hwc["gates"]["ARCH_CONTRACT_READY"] = "HELD"
    for gate in ("HWC_SOURCE_COMPLETE", "HWC_SOURCE_READY"):
        hwc["gates"][gate] = "HELD"
    hwc["blockers"] = sorted(
        f"{gate}: HELD" for gate, value in hwc["gates"].items() if value == "HELD"
    )
    hwc["status_sha256"] = status_sha256(hwc)
    monkeypatch.setattr(project_status, "derive_hwc_source_status", lambda root: hwc)
    monkeypatch.setattr(
        project_status,
        "evaluate_pre_p3_provenance",
        lambda root: {
            "blockers": [],
            "gates": {},
            "latest_receipts": {},
            "legacy_receipts": {},
            "pre_p3_ready": "PASS",
            "provenance": {},
        },
    )

    status = project_status.derive_project_status(ROOT)

    assert status["gates"]["ARCH_CONTRACT_READY"] == "HELD"
    assert status["p3_alpha_development_allowed"] is False


def test_candidate_issuance_rejects_missing_or_stale_hwc_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = derive_hwc_source_status(ROOT)
    status_path = tmp_path / "docs/implementation/hwc/hwc-source-status.json"
    status_path.parent.mkdir(parents=True)
    monkeypatch.setattr(qualify_pre_p3, "ROOT", tmp_path)
    monkeypatch.setattr(
        qualify_pre_p3, "derive_hwc_source_status", lambda root: expected
    )

    with pytest.raises(qualify_pre_p3.QualificationError, match="HWC"):
        qualify_pre_p3._require_hwc_arch_contract()

    status_path.write_bytes(canonical_json_bytes(expected) + b"\n")
    qualify_pre_p3._require_hwc_arch_contract()
    status_path.write_bytes(canonical_json_bytes(expected | {"blockers": []}) + b"\n")
    with pytest.raises(qualify_pre_p3.QualificationError, match="HWC"):
        qualify_pre_p3._require_hwc_arch_contract()


def _source_ready_status() -> dict:
    status = json.loads(json.dumps(derive_hwc_source_status(ROOT)))
    status["gates"]["HWC_PORTABLE_QUALIFIED"] = "PASS"
    status["gates"]["HWC_SOURCE_READY"] = "PASS"
    status["blockers"] = []
    status["status_sha256"] = status_sha256(status)
    return validate_hwc_source_status(status)


def test_final_candidate_requirement_rejects_held_hwc_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOV-HWC-005: final candidate issuance stops before creating output."""
    held = derive_hwc_source_status(ROOT)
    status_path = tmp_path / "docs/implementation/hwc/hwc-source-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_bytes(canonical_json_bytes(held) + b"\n")
    output = tmp_path / "candidate.json"
    monkeypatch.setattr(qualify_pre_p3, "ROOT", tmp_path)
    monkeypatch.setattr(qualify_pre_p3, "derive_hwc_source_status", lambda root: held)

    with pytest.raises(qualify_pre_p3.QualificationError, match="HWC_SOURCE_READY"):
        qualify_pre_p3.candidate_v2(
            tmp_path / "receipts",
            tmp_path / "legacy",
            output,
            base_sha="0" * 40,
            promotion_type="SQUASH",
            qualification={
                "completed_at_utc": "2026-09-02T00:00:00Z",
                "producer": "scripts/qualify_pre_p3.py",
                "run_attempt": "1",
                "run_id": "1",
            },
        )

    assert not output.exists()


def test_final_candidate_requirement_accepts_matching_ready_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOV-HWC-006: matching canonical source-ready status passes the guard."""
    ready = _source_ready_status()
    status_path = tmp_path / "docs/implementation/hwc/hwc-source-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_bytes(canonical_json_bytes(ready) + b"\n")
    monkeypatch.setattr(qualify_pre_p3, "ROOT", tmp_path)
    monkeypatch.setattr(qualify_pre_p3, "derive_hwc_source_status", lambda root: ready)

    qualify_pre_p3._require_hwc_source_ready()


def test_final_candidate_requirement_rejects_forged_ready_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOV-HWC-007: tracked PASS cannot override independently derived HELD."""
    held = derive_hwc_source_status(ROOT)
    status_path = tmp_path / "docs/implementation/hwc/hwc-source-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_bytes(canonical_json_bytes(_source_ready_status()) + b"\n")
    monkeypatch.setattr(qualify_pre_p3, "ROOT", tmp_path)
    monkeypatch.setattr(qualify_pre_p3, "derive_hwc_source_status", lambda root: held)

    with pytest.raises(qualify_pre_p3.QualificationError, match="HWC"):
        qualify_pre_p3._require_hwc_source_ready()


@pytest.mark.parametrize(
    "mutation",
    ("malformed", "stale", "digest", "extra", "missing"),
)
def test_final_candidate_requirement_rejects_invalid_status_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """GOV-HWC-008: malformed or non-canonical projections fail closed."""
    ready = _source_ready_status()
    status_path = tmp_path / "docs/implementation/hwc/hwc-source-status.json"
    status_path.parent.mkdir(parents=True)
    if mutation == "malformed":
        status_path.write_bytes(b"{not-json\n")
    else:
        payload = json.loads(json.dumps(ready))
        if mutation == "stale":
            payload["gates"]["HWC_SOURCE_READY"] = "HELD"
            payload["blockers"] = ["HWC_SOURCE_READY: HELD"]
            payload["status_sha256"] = status_sha256(payload)
        elif mutation == "digest":
            payload["status_sha256"] = "0" * 64
        elif mutation == "extra":
            payload["unexpected"] = True
        else:
            del payload["deployment"]
        status_path.write_bytes(canonical_json_bytes(payload) + b"\n")
    monkeypatch.setattr(qualify_pre_p3, "ROOT", tmp_path)
    monkeypatch.setattr(qualify_pre_p3, "derive_hwc_source_status", lambda root: ready)

    with pytest.raises(qualify_pre_p3.QualificationError, match="HWC"):
        qualify_pre_p3._require_hwc_source_ready()


def test_final_candidate_requirement_rejects_symlinked_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOV-HWC-008: a symlink cannot supply canonical HWC authority."""
    ready = _source_ready_status()
    target = tmp_path / "status-target.json"
    target.write_bytes(canonical_json_bytes(ready) + b"\n")
    status_path = tmp_path / "docs/implementation/hwc/hwc-source-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.symlink_to(target)
    monkeypatch.setattr(qualify_pre_p3, "ROOT", tmp_path)
    monkeypatch.setattr(qualify_pre_p3, "derive_hwc_source_status", lambda root: ready)

    with pytest.raises(qualify_pre_p3.QualificationError, match="HWC"):
        qualify_pre_p3._require_hwc_source_ready()


def test_hwc_status_cli_writes_checks_and_rejects_leaf_symlink(tmp_path: Path) -> None:
    output = tmp_path / "status.json"
    written = subprocess.run(
        [sys.executable, "scripts/derive_hwc_status.py", "--write", str(output)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr.decode()
    checked = subprocess.run(
        [sys.executable, "scripts/derive_hwc_status.py", "--check", str(output)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0
    output.write_bytes(output.read_bytes() + b"drift")
    assert subprocess.run(
        [sys.executable, "scripts/derive_hwc_status.py", "--check", str(output)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    ).returncode == 1

    target = tmp_path / "target.json"
    target.write_bytes(b"preserve\n")
    output.unlink()
    output.symlink_to(target)
    assert subprocess.run(
        [sys.executable, "scripts/derive_hwc_status.py", "--write", str(output)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    ).returncode == 2
    assert target.read_bytes() == b"preserve\n"
