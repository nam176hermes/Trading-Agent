from packages.p3_status import derive_p3_status

import hashlib
import subprocess
import pytest

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


@pytest.mark.parametrize('outcome', ['PASS','FAIL','NONE_QUALIFIED','HELD'])
@pytest.mark.parametrize('fault',[None,'source','revoked','changed','missing_phase',
    'prerequisite_revoked','prerequisite_expired','prerequisite_changed','missing_phase_prerequisite_revoked'])
def test_local_status_keeps_economic_and_published_verdicts_separate(tmp_path,monkeypatch,outcome,fault):
    """Status routing only; custody and committed reconstruction have separate tests."""
    from types import SimpleNamespace
    from uuid import uuid4
    from packages import local_pre_p3
    from packages.alpha_lifecycle import local_qualification
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity,QualificationBinding
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.runtime_release import config
    from services.job_store.worker_repository import WorkerRepository
    from services.job_store.config import JobStoreSettings
    from services.job_worker import p3_phase_readback
    from tests.p3.test_replay import _ref
    policy=tmp_path/local_pre_p3.POLICY_PATH;policy.parent.mkdir(parents=True);policy.write_bytes(b'policy')
    source=SourceIdentity(commit_sha='a'*40,tree_sha='b'*40,closure_schema_version='source-closure-v1',
        closure_policy_sha256='c'*64,closure_sha256='d'*64)
    identity=LocalRunIdentity(issuer_kind='LOCAL',issuer_identity='synthetic',run_id=uuid4(),attempt=1,
        host_id='e'*64,boot_id=uuid4())
    ref=_ref('f'*64)
    binding=QualificationBinding(gate='P3_PHASE_EXIT',source=source,policy_digest=hashlib.sha256(b'policy').hexdigest(),
        input_ref=ref,environment_ref=ref)
    ready=SimpleNamespace(identity=identity,binding=binding)
    store=tmp_path/'cas';store.mkdir(mode=0o700)
    profile=p3_status.LocalPhaseProfile(schema_version='p3-local-phase-profile-v1',identity=identity,binding=binding,
        phase_receipt_ref=ref,store_root=str(store),worker_credentials_directory='/synthetic/credentials')
    prerequisite_reads=[]
    def prerequisite(root):
        prerequisite_reads.append(root)
        if len(prerequisite_reads)>1:
            if fault in {'prerequisite_revoked','missing_phase_prerequisite_revoked'}:
                raise local_qualification.LocalQualificationError('E_LOCAL_APPROVAL_REVOKED','revoked')
            if fault=='prerequisite_expired':
                raise local_qualification.LocalQualificationError('E_LOCAL_APPROVAL_EXPIRED','expired')
            if fault=='prerequisite_changed':
                return {'PRE_P3_READY':object()}
        return {'PRE_P3_READY':ready}
    monkeypatch.setattr(local_pre_p3,'read_local_pre_p3',prerequisite)
    count=[]
    def protected(path):
        if fault in {'missing_phase','missing_phase_prerequisite_revoked'}:
            raise FileNotFoundError('phase profile unavailable')
        count.append(path)
        return profile.model_dump(mode='json'),('changed' if fault=='changed' and len(count)>1 else 'pinned')
    monkeypatch.setattr(config,'read_protected_canonical_json_current',protected)
    def authenticate(*a,**k):
        if fault=='revoked':raise local_qualification.LocalQualificationError('E_LOCAL_APPROVAL_REVOKED','revoked')
        return SimpleNamespace(evidence_refs=(ref,))
    monkeypatch.setattr(local_qualification,'validate_local_qualification',authenticate)
    monkeypatch.setattr(JobStoreSettings,'from_systemd_credentials',lambda *a,**k:None)
    monkeypatch.setattr(WorkerRepository,'__init__',lambda *a,**k:None)
    monkeypatch.setattr(WorkerRepository,'__enter__',lambda self:self)
    monkeypatch.setattr(WorkerRepository,'__exit__',lambda *a:None)
    monkeypatch.setattr(WorkerRepository,'assert_session_runtime_identity',lambda self:None)
    monkeypatch.setattr(WorkerRepository,'alpha_publication_repository',lambda *a:None)
    monkeypatch.setattr('packages.alpha_lifecycle.replica_store._read',lambda *a,**k:SimpleNamespace(outcome=outcome))
    monkeypatch.setattr(p3_phase_readback,'validate_phase_exit_receipt',lambda *a,**k:SimpleNamespace(
        source=source.model_copy(update={'commit_sha':'0'*40}) if fault=='source' else source,identity=identity,
        policy_digest=binding.policy_digest,input_ref=ref,environment_ref=ref,outcome=outcome,digest='9'*64))
    result=p3_status.derive_local_p3_status(tmp_path)
    assert result.protected_promotion==result.published_complete=='HELD'
    assert result.source_qualification==('HELD' if fault and 'prerequisite' in fault else 'PASS')
    if fault and 'prerequisite' in fault:
        assert result.qualified_source is None
        assert result.phase_exit_receipt_digest is None
    if fault:
        assert result.local_complete=='HELD' and result.economic_outcome=='HELD' and result.blocker_codes
        if fault=='revoked':assert result.blocker_codes==('E_LOCAL_APPROVAL_REVOKED',)
    else:
        assert result.economic_outcome==outcome
        assert result.pipeline_complete==('HELD' if outcome=='HELD' else 'PASS')
        assert result.local_complete==('PASS' if outcome=='PASS' else 'HELD')
