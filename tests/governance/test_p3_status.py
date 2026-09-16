from packages.p3_status import derive_p3_status

import hashlib
import subprocess

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.pre_p3_provenance import canonical_source_identity
from packages import p3_status


def _commit(root):
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
    return canonical_source_identity(root)


def _write(root, relative, payload):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    path.write_bytes(canonical_json_bytes(payload))
    return payload


def _promoted_repository(root, monkeypatch):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "source.py").write_text("value = 1\n")
    qualified = _commit(root)
    ref = dict(content_sha256="a" * 64, locator="a" * 64 + ".blob", size_bytes=1, media_type="application/json")
    authority = dict(broker=False, live=False, network=False, production=False)
    phase = _write(root, p3_status.PHASE_EXIT_PATH, dict(
        schema_version="p3-phase-exit-v1", source=qualified, epoch_id="test-epoch",
        primary_selection_ref=ref, exit_result_ref=ref, qualified_registry_head_ref=ref,
        closure_report_ref=ref, bundle_inventory_ref=ref, qualification_workflow="p3-authority.yml",
        qualification_run_id=1, qualification_attempt=1, status="PASS", authority=authority,
    ))
    promoted = _commit(root)
    promotion = _write(root, p3_status.PROMOTION_ROOT / (promoted["commit_sha"] + "-v1.json"), dict(
        schema_version="p3-promotion-v1", qualified_source=qualified, promoted_source=promoted,
        phase_exit_receipt_digest=phase["digest"], foundation_run_id=1, foundation_attempt=1,
        workflow_ref="foundation.yml", status="PASS", authority=authority,
    ))
    _commit(root)
    monkeypatch.setattr(p3_status, "_attested", lambda *args, **kwargs: True)
    return phase, promotion


def test_promotion_survives_receipt_only_commits_and_ignores_unrelated_history(tmp_path, monkeypatch):
    _, promotion = _promoted_repository(tmp_path, monkeypatch)
    assert derive_p3_status(tmp_path).gates.phase_complete == "PASS"
    historical = dict(promotion)
    historical.pop("digest")
    historical["promoted_source"] = {**promotion["promoted_source"], "commit_sha": "b" * 40}
    _write(tmp_path, p3_status.PROMOTION_ROOT / ("b" * 40 + "-v1.json"), historical)
    _commit(tmp_path)
    assert derive_p3_status(tmp_path).gates.phase_complete == "PASS"
    (tmp_path / "source.py").write_text("value = 2\n")
    _commit(tmp_path)
    assert derive_p3_status(tmp_path).gates.phase_complete == "HELD"


def test_newer_invalid_promotion_never_falls_back(tmp_path, monkeypatch):
    _, promotion = _promoted_repository(tmp_path, monkeypatch)
    current = canonical_source_identity(tmp_path)
    broken = dict(promotion)
    broken.pop("digest")
    broken["promoted_source"] = current
    broken["phase_exit_receipt_digest"] = "f" * 64
    _write(tmp_path, p3_status.PROMOTION_ROOT / (current["commit_sha"] + "-v1.json"), broken)
    _commit(tmp_path)
    assert derive_p3_status(tmp_path).gates.phase_complete == "HELD"


def test_changed_closure_policy_never_qualifies(tmp_path, monkeypatch):
    phase, _ = _promoted_repository(tmp_path, monkeypatch)
    phase.pop("digest")
    phase["source"] = {**phase["source"], "closure_policy_sha256": "f" * 64}
    _write(tmp_path, p3_status.PHASE_EXIT_PATH, phase)
    _commit(tmp_path)
    assert derive_p3_status(tmp_path).gates.pipeline_complete == "HELD"


def test_p3_status_is_held_without_external_phase_receipts(tmp_path) -> None:
    # Current repository has source authority but intentionally no official P3 outcome.
    status = derive_p3_status(__import__("pathlib").Path(__file__).parents[2])
    assert status.gates.phase_complete == "HELD"
    assert status.authority.live is False
    assert status.authority.broker is False


def test_structural_receipt_cannot_bypass_external_attestation(monkeypatch) -> None:
    monkeypatch.setattr("packages.p3_status._attested", lambda *args, **kwargs: False)
    status = derive_p3_status(__import__("pathlib").Path(__file__).parents[2])
    assert status.gates.pipeline_complete == "HELD"
    assert status.gates.phase_complete == "HELD"
