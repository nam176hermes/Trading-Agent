import hashlib
from datetime import UTC,datetime

import pytest

from packages.alpha_lifecycle.contracts.authority import CustodyRecord
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


@pytest.mark.parametrize('fault',[None,'missing_replay','wrong_digest','extra_authority','bare_evaluation'])
def test_worker_holdout_result_requires_all_parent_outputs(fault):
    from packages.alpha_lifecycle import holdout
    from services.job_worker.results import validate_p3_result_bytes,ResultValidationError
    from tests.p3.test_replay import _ref
    fields=('holdout_request_ref','holdout_manifest_ref','holdout_evaluation_ref',
        'holdout_replay_ref','executable_ref','baseline_executable_ref')
    value={'schema_version':'p3-holdout-operation-result-v1',
        **{name:_ref(str(i)*64) for i,name in enumerate(fields)}}
    if fault=='missing_replay':
        value.pop('holdout_replay_ref')
    if fault=='extra_authority':
        value['approved']=True
    if fault=='bare_evaluation':
        value['schema_version']='p3-holdout-evaluation-result-v1'
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    if fault=='wrong_digest':
        value['digest']='f'*64
    raw=canonical_json_bytes(value)
    if fault:
        with pytest.raises(ResultValidationError):
            validate_p3_result_bytes('p3-holdout-operation-result-v1',raw)
    else:
        result=validate_p3_result_bytes('p3-holdout-operation-result-v1',raw)
        assert isinstance(result,holdout.HoldoutOperationResult)
        assert canonical_json_bytes(result)==raw
        assert set(type(result).model_fields)=={'schema_version','digest',*fields}


def test_holdout_custodian_must_be_distinct_from_researcher() -> None:
    digest = "a"*64
    ref = ArtifactRefV1(content_sha256=digest,size_bytes=1,media_type="application/octet-stream",locator=f"{digest}.blob")
    payload = {
        "schema_version":"p3-custody-record-v1","holdout_commitment":"b"*64,
        "ciphertext_ref":ref,"plaintext_bundle_digest":"c"*64,
        "custodian_identity":"same","research_identity":"same",
        "custodian_attestation_ref":ref,"access_policy_digest":"d"*64,
        "classification":"HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND",
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    with pytest.raises(ValueError,match="distinct"):
        CustodyRecord.model_validate(payload)


@pytest.mark.parametrize('substitution',[None,'primary','custody','digest','media'])
def test_holdout_request_binds_exact_selected_primary_and_custody(tmp_path,substitution):
    from packages.alpha_lifecycle.contracts.authority import HoldoutRequest,PrimarySelection
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.alpha_lifecycle.holdout import validate_holdout_access
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from tests.p3.test_replica_execution import _seal
    tmp_path.chmod(0o700)
    store=LocalArtifactStore(tmp_path)
    placeholder=store.put_bytes(b'{}',media_type='application/json')
    source=SourceIdentity(commit_sha='a'*40,tree_sha='b'*40,closure_schema_version='synthetic',
        closure_policy_sha256='c'*64,closure_sha256='d'*64)
    primary_ref=_seal(store,schema_version='p3-primary-selection-v1',family_review_ref=placeholder,
        selection_policy_digest='e'*64,primary_alpha_id='synthetic.primary',primary_version='1.0.0',
        primary_candidate_head_ref=placeholder,outcome='SELECTED')
    custody_ref=_seal(store,schema_version='p3-custody-record-v1',holdout_commitment='b'*64,
        ciphertext_ref=placeholder,plaintext_bundle_digest='c'*64,custodian_identity='synthetic.custodian',
        research_identity='synthetic.researcher',custodian_attestation_ref=placeholder,access_policy_digest='d'*64,
        classification='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND')
    primary=PrimarySelection.model_validate_json(store.read_bytes(primary_ref))
    custody=CustodyRecord.model_validate_json(store.read_bytes(custody_ref))
    if substitution=='digest':
        primary_ref=primary_ref.model_copy(update={'content_sha256':'0'*64,'locator':'0'*64+'.blob'})
    elif substitution=='media':
        custody_ref=custody_ref.model_copy(update={'media_type':'application/octet-stream'})
    now=datetime(2026,9,12,tzinfo=UTC)
    request_ref=_seal(store,schema_version='p3-holdout-request-v1',source=source,
        primary_selection_ref=placeholder if substitution=='primary' else primary_ref,
        custody_record_ref=placeholder if substitution=='custody' else custody_ref,
        policy_digest=primary.selection_policy_digest,holdout_input_set_ref=placeholder,review_ref=placeholder,
        issued_at='2026-09-12T00:00:00Z',expires_at='2026-09-12T01:00:00Z',logical_trial_id='synthetic.holdout',
        authority=dict(broker=False,live=False,network=False,production=False))
    request=HoldoutRequest.model_validate_json(store.read_bytes(request_ref))
    if substitution:
        with pytest.raises(ValueError,match='bind'):
            validate_holdout_access(request,primary,custody,expected_source=source,now=now)
    else:
        validate_holdout_access(request,primary,custody,expected_source=source,now=now)


@pytest.mark.parametrize('fault',[None,'operation','input_set','alpha','body'])
def test_holdout_request_is_derived_downstream_of_the_reviewed_intent(fault):
    from packages.alpha_lifecycle import holdout
    from packages.alpha_lifecycle.authority import AuthorityHeld
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from tests.p3.test_authority import _request
    from tests.p3.test_operation_input import intent
    from tests.p3.test_replay import _ref
    original,source=_request('2026-01-01T01:00:00Z')
    fields=('primary_selection_ref','candidate_spec_ref','registration_proof_ref','custody_record_ref',
        'holdout_input_set_ref','holdout_dataset_ref','context_dataset_ref','buffer_ref','environment_ref','instrument_spec_ref')
    body={name:_ref(str(i)*64).model_dump(mode='json') for i,name in enumerate(fields)}
    body['policy_digest']='c'*64
    alpha='a0.donchian-20-10-close-confirm'
    operation_input=P3OperationInput.model_validate_json(intent(workflow_operation='p3-holdout-primary-v1',
        operation='HOLDOUT',allowed_alpha_ids=[alpha],body=body,
        input_set_ref=original['authorization']['input_set_ref']))
    payload={**original['authorization'],'operation':'HOLDOUT','allowed_alpha_ids':[alpha]}
    payload.pop('digest')
    if fault=='operation': payload['operation']='OOS'
    elif fault=='input_set': payload['input_set_ref']=_ref('f'*64).model_dump(mode='json')
    elif fault=='alpha': payload['allowed_alpha_ids']=['a1.dual-sma-50-200']
    elif fault=='body': operation_input=P3OperationInput.model_validate_json(intent())
    payload['digest']=hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    authorization=RunAuthorization.model_validate_json(canonical_json_bytes(payload))
    if fault:
        with pytest.raises((ValueError,AuthorityHeld)):
            holdout.derive_holdout_request(operation_input,authorization,expected_source=source)
        return
    before=canonical_json_bytes(operation_input)
    result=holdout.derive_holdout_request(operation_input,authorization,expected_source=source)
    assert result==holdout.derive_holdout_request(operation_input,authorization,expected_source=source)
    assert result.source==source and result.primary_selection_ref==operation_input.body.primary_selection_ref
    assert result.custody_record_ref==operation_input.body.custody_record_ref
    assert result.holdout_input_set_ref==operation_input.body.holdout_input_set_ref
    assert result.policy_digest==operation_input.body.policy_digest
    assert (result.review_ref,result.issued_at,result.expires_at,result.authority)==(
        authorization.review_ref,authorization.issued_at,authorization.expires_at,authorization.authority)
    assert result.logical_trial_id=='p3-holdout-primary-v1'
    assert canonical_json_bytes(operation_input)==before
    assert b'holdout_request_ref' not in before and result.digest.encode() not in before
