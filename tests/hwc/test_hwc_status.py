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
