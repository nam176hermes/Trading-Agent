"""Pre-mount structural checks; synthetic metadata never grants custody authority."""
from datetime import UTC,datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.alpha_lifecycle.authority import AuthorityHeld,build_alpha_campaign_payload
from packages.alpha_lifecycle.contracts.authority import RunAuthorization,ReviewApproval
from packages.alpha_lifecycle.contracts.data import DatasetEvidence
from packages.alpha_lifecycle.contracts.execution import HoldoutManifest
from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore,_read
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_baseline_operation import _cli_authorization
from tests.p3.test_reference_input import reference_seed  # noqa: F401
from tests.p3.test_replica_execution import _seal


@pytest.fixture
def metadata_request(reference_seed,tmp_path):
    root,manifest_ref,spec_ref,*_=reference_seed
    output=tmp_path/'metadata';output.mkdir(mode=0o700)
    store=ReplicaArtifactStore(root,output)
    manifest=_read(store,manifest_ref,HoldoutManifest)
    registration=_read(store,manifest.research_registration_ref,RegistrationProof)
    # Deliberately invalid custody/InputSet refs: parsing alone must not admit them.
    body=dict(primary_selection_ref=manifest.primary_selection_ref,candidate_spec_ref=manifest.candidate_spec_ref,
        registration_proof_ref=manifest.research_registration_ref,custody_record_ref=manifest_ref,
        holdout_input_set_ref=manifest_ref,holdout_dataset_ref=manifest.holdout_dataset_ref,
        context_dataset_ref=manifest.context_dataset_ref,buffer_ref=manifest.buffer_ref,
        environment_ref=manifest.environment_ref,instrument_spec_ref=spec_ref,policy_digest=manifest.policy_digest)
    ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-holdout-primary-v1',
        operation='HOLDOUT',input_set_ref=registration.input_set_ref,
        allowed_alpha_ids=('a0.donchian-20-10-close-confirm',),body=body)
    intent=_read(store,ref,P3OperationInput)
    authorization=_read(store,_cli_authorization(store,ref,manifest.source),RunAuthorization)
    return store,intent,authorization,manifest


def test_holdout_fold_metadata_requires_no_row_reader(reference_seed):
    from packages.alpha_lifecycle import folds
    from packages.data_catalog.artifact_store import LocalArtifactStore
    root,manifest_ref,*_=reference_seed
    store=LocalArtifactStore(root)
    manifest=_read(store,manifest_ref,HoldoutManifest)
    context=_read(store,manifest.context_dataset_ref,DatasetEvidence)
    holdout=_read(store,manifest.holdout_dataset_ref,DatasetEvidence)
    result=folds.build_holdout_fold_manifest(context,holdout,policy_digest=manifest.policy_digest)
    assert result.mode=='HOLDOUT' and len(result.folds)==1
    assert result.dataset_evidence_ref==manifest.holdout_dataset_ref
    fold=result.folds[0]
    assert str(fold.context_start)=='2024-11-04' and str(fold.decision_start)=='2025-08-31'
    assert fold.return_count==365 and fold.fold_id=='H1'
    assert fold.decision_row_refs==(context.row_refs[-1],*holdout.row_refs[:-1])
    assert fold.return_row_refs==holdout.row_refs and fold.snapshot_ref==holdout.snapshot_ref


def test_holdout_metadata_rejects_unresolved_custody_before_mount(metadata_request):
    from packages.alpha_lifecycle import holdout
    store,intent,authorization,manifest=metadata_request
    with pytest.raises(ValueError):
        holdout.validate_holdout_operation_input(intent,authorization,expected_source=manifest.source,
            store=store,now=datetime.now(UTC))


@pytest.mark.parametrize('fault',['subject','source','verdict','same_identity','future','expired','noncanonical'])
def test_standalone_metadata_checks_outer_review_before_graph_or_evidence(metadata_request,fault):
    from datetime import timedelta
    from packages.alpha_lifecycle import holdout
    from tests.p3.test_reference_input import _changed
    store,intent,authorization,manifest=metadata_request
    review=_read(store,authorization.review_ref,ReviewApproval)
    values={
        'subject':dict(subject_digests=('9'*64,)),
        'source':dict(source=manifest.source.model_copy(update={'commit_sha':'9'*40})),
        'verdict':dict(verdict='REJECTED'),
        'same_identity':dict(reviewer_identity=review.operator_identity),
        'future':dict(issued_at=(authorization.issued_at+timedelta(seconds=1)).isoformat().replace('+00:00','Z')),
    }.get(fault,{})
    raw=canonical_json_bytes(_changed(review,**values))
    if fault=='noncanonical': raw=b' '+raw
    review_ref=store.put_bytes(raw,media_type='application/json')
    authorization=RunAuthorization.model_validate_json(canonical_json_bytes(_changed(authorization,review_ref=review_ref)))
    reads=[]
    def guarded(ref):
        assert ref==review_ref,'unapproved review reached graph or evidence read'
        reads.append(ref)
        return store.read_bytes(ref)
    with pytest.raises((ValueError,AuthorityHeld)):
        holdout.validate_holdout_operation_input(intent,authorization,expected_source=manifest.source,
            store=SimpleNamespace(read_bytes=guarded),
            now=authorization.expires_at if fault=='expired' else datetime.now(UTC))
    assert reads==[review_ref]


@pytest.mark.parametrize('bad_review',[False,True,'holdout_evidence'])
def test_dispatch_validates_holdout_metadata_before_sql_acceptance(metadata_request,monkeypatch,bad_review):
    from scripts import p3_authority as command
    from services.job_worker import p3_integration
    from tests.p3.test_reference_input import _changed
    store,intent,authorization,manifest=metadata_request
    review=_read(store,authorization.review_ref,ReviewApproval)
    if bad_review:
        values=(dict(evidence_ref=_read(store,manifest.holdout_dataset_ref,DatasetEvidence).row_refs[0])
            if bad_review=='holdout_evidence' else dict(subject_digests=('9'*64,)))
        review=ReviewApproval.model_validate(_changed(review,**values))
        review_ref=store.put_bytes(canonical_json_bytes(review),media_type='application/json')
        authorization=RunAuthorization.model_validate_json(canonical_json_bytes(_changed(authorization,review_ref=review_ref)))
        original=store.read_bytes
        def guarded(ref):
            assert ref!=review.evidence_ref,'unapproved review reached evidence read'
            return original(ref)
        monkeypatch.setattr(store,'read_bytes',guarded)
    monkeypatch.setattr(p3_integration,'_read_review',lambda *args:review)
    monkeypatch.setattr(p3_integration,'_read_authority_bytes',lambda path:canonical_json_bytes(intent))
    payload=build_alpha_campaign_payload(authorization,manifest.source,intent.workflow_operation,operation_input=intent)
    request=SimpleNamespace(authorization=authorization,operation_input=intent)
    with pytest.raises((ValueError,AuthorityHeld)):
        command._stage_request(request,payload,intent.workflow_operation,store,Path('/unused-manifest'),Path('/unused-review'))
