"""Synthetic producer/controller checks; these issue no runtime authority."""
from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest

from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
from packages.alpha_lifecycle.contracts.data import PITProof
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_replica_execution import baseline_inputs, _seal


def test_prepare_inputs_uses_frozen_training_and_rejects_wrong_source(tmp_path):
    from packages.alpha_lifecycle.local_campaign import assemble_input_set
    from packages.alpha_lifecycle.pit_suite import build_pit_suite_receipt, PIT_EVIDENCE_MEDIA
    from scripts.generate_p3_specs import POLICY_SOURCE
    from tests.p3.test_pit_suite import suite_inputs
    from packages.alpha_lifecycle.contracts.results import RegimeThreshold
    from packages.alpha_lifecycle.contracts.data import FoldManifest

    store, manifest_ref = baseline_inputs(tmp_path/'inputs')
    old = InputSet.model_validate_json(store.read_bytes(
        BaselineManifest.model_validate_json(store.read_bytes(manifest_ref)).input_set_ref))
    policy = json.loads(POLICY_SOURCE.read_bytes())
    digest = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    pit = PITProof.model_validate_json(store.read_bytes(old.pit_proof_ref))
    commitment = json.loads(store.read_bytes(pit.revision_proof_ref))
    commitment.pop('digest')
    commitment['policy_digest'] = digest
    commitment_ref = _seal(store, **commitment)
    (tmp_path/'suite').mkdir()
    helper, suite_ref, _, report, qualification = suite_inputs(tmp_path/'suite')
    store.put_bytes(helper.read_bytes(suite_ref), media_type=PIT_EVIDENCE_MEDIA)
    collection = {**report, 'collection_only':True, 'summary':{'collected':14},
                  'tests':[{**row, 'outcome':'collected', 'phase':'collection'} for row in report['tests']]}
    collected = store.put_bytes(canonical_json_bytes(collection), media_type='application/json')
    executed = store.put_bytes((json.dumps(report, indent=2, sort_keys=True)+'\n').encode(), media_type=PIT_EVIDENCE_MEDIA)
    suite = build_pit_suite_receipt(old.source, suite_ref, collected, executed, qualification, store=store)
    suite_ref = store.put_bytes(canonical_json_bytes(suite), media_type='application/json')
    integration = _seal(store, schema_version='p3-integration-qualified-v1', source=old.source,
        sql_proof_ref=collected, native_fixture_proof_ref=executed, cleanup_proof_ref=collected,
        workflow_run_id=1, workflow_attempt=1, status='PASS',
        authority=dict(broker=False, live=False, network=False, production=False))
    kwargs = dict(source=old.source, dataset_ref=old.dataset_evidence_ref,
        commitment_ref=commitment_ref, pit_suite_ref=suite_ref,
        environment_ref=old.environment_ref, integration_receipt_ref=integration, store=store)
    result_ref = assemble_input_set(**kwargs)
    result = InputSet.model_validate_json(store.read_bytes(result_ref))
    threshold = RegimeThreshold.model_validate_json(store.read_bytes(result.regime_threshold_ref))
    folds = FoldManifest.model_validate_json(store.read_bytes(result.fold_manifest_ref))
    assert str(threshold.training_range.end) == '2021-08-31'
    assert str(folds.folds[0].return_end_range.start) == '2022-09-01'
    assert threshold.sample_count == 1276
    assert result.policy_digest == digest
    assert result.cost_model.fee_bps == 10
    with pytest.raises(ValueError):
        assemble_input_set(**{**kwargs, 'source':old.source.model_copy(update={'commit_sha':'9'*40})})
    dataset = json.loads(store.read_bytes(old.dataset_evidence_ref))
    dataset.pop('digest')
    dataset['row_refs'][100:102] = reversed(dataset['row_refs'][100:102])
    shuffled = _seal(store, **dataset)
    with pytest.raises(ValueError, match='chronological'):
        assemble_input_set(**{**kwargs, 'dataset_ref':shuffled})


def test_operation_proposal_requires_existing_body_and_independent_review(tmp_path):
    from packages.alpha_lifecycle.local_campaign import propose_operation, prepare_authorization
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    store, manifest_ref = baseline_inputs(tmp_path/'inputs')
    manifest = BaselineManifest.model_validate_json(store.read_bytes(manifest_ref))
    inputs = InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    intent = propose_operation('p3-baselines-v1', manifest.input_set_ref,
        {'baseline_manifest_ref':manifest_ref}, store=store)
    assert intent.operation == 'BASELINES' and intent.allowed_alpha_ids == ()
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='LOCAL',
        issuer_identity='test-agent', run_id='00000000-0000-4000-8000-000000000002', attempt=1,
        host_id='a'*64, boot_id='00000000-0000-4000-8000-000000000001')))
    now = datetime.now(UTC).replace(microsecond=0)
    evidence = store.put_bytes(b'Independent synthetic review only.', media_type='text/plain')
    review = dict(schema_version='p3-review-approval-v1', source=inputs.source,
        subject_digests=[intent.digest], operator_identity='operator', reviewer_identity='agent-reviewer',
        review_execution_id='review-1', verdict='APPROVED', issued_at=(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),
        expires_at=(now+timedelta(minutes=30)).isoformat().replace('+00:00','Z'), evidence_ref=evidence,
        authority=dict(broker=False, live=False, network=False, production=False))
    review_ref = _seal(store, **review)
    result = prepare_authorization(intent, identity=identity, review_ref=review_ref, store=store,
        source=inputs.source, now=now, expires_at=now+timedelta(minutes=20))
    assert result.review_ref == review_ref and result.identity == identity
    for change in ({'reviewer_identity':'operator'}, {'subject_digests':['0'*64]},
                   {'verdict':'REJECTED'}, {'expires_at':(now-timedelta(seconds=1)).isoformat().replace('+00:00','Z')}):
        with pytest.raises(ValueError):
            prepare_authorization(intent, identity=identity, review_ref=_seal(store, **{**review, **change}),
                store=store, source=inputs.source, now=now, expires_at=now+timedelta(minutes=20))
    from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity, RunAuthorization
    github = GithubRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='GITHUB',
        issuer_identity='p3-authority', repository='nam176hermes/Trading-Agent', workflow_ref='p3-authority.yml',
        run_id=7, attempt=1)))
    hosted = prepare_authorization(intent, identity=github, review_ref=review_ref, store=store,
        source=inputs.source, now=now, expires_at=now+timedelta(minutes=20))
    assert type(hosted) is RunAuthorization
    assert (hosted.issuer_workflow, hosted.issuer_run_id, hosted.issuer_attempt) == ('p3-authority.yml', 7, 1)
    with pytest.raises(ValueError):
        prepare_authorization(intent, identity=github.model_copy(update={'workflow_ref':'foundation.yml'}),
            review_ref=review_ref, store=store, source=inputs.source, now=now, expires_at=now+timedelta(minutes=20))
    with pytest.raises(ValueError):
        prepare_authorization(intent, identity=github, review_ref=review_ref, store=store,
            source=inputs.source, now=now, expires_at=now+timedelta(minutes=40))
    missing = manifest_ref.model_copy(update={'content_sha256':'0'*64, 'locator':'0'*64+'.blob'})
    with pytest.raises((OSError, ValueError)):
        propose_operation('p3-baselines-v1', manifest.input_set_ref, {'baseline_manifest_ref':missing}, store=store)


def test_registration_body_is_derived_and_accepted_by_the_job_validator(retained_baseline):
    from packages.alpha_lifecycle.local_campaign import propose_operation, registration_body
    from packages.alpha_lifecycle.registry import AlphaRecordV1
    from services.job_worker.p3_publication_producer import prepare_family_registration

    store, input_ref, selection = retained_baseline
    selection_ref = store.put_bytes(canonical_json_bytes(selection), media_type='application/json')
    body = registration_body(input_ref, selection_ref, store=store)
    intent = propose_operation('p3-register-family-v1', input_ref, body, store=store)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    proposal = prepare_family_registration(intent, job_id='job_registration', observed_at=now,
                                           expires_at=now + timedelta(hours=1), store=store)
    assert len(proposal.request.proposed_event_refs) == 8
    records = [AlphaRecordV1.model_validate_json(store.read_bytes(ref)) for ref in body['candidate_record_refs']]
    specs = body['candidate_spec_refs']
    assert [r.artifact_digests for r in records] == [(spec.content_sha256,) for spec in specs]
    assert records[0].feature_set == ('close', 'high', 'low') and records[1].feature_set == ('close',)
    assert all(r.validation_start_at == r.training_end_at + timedelta(microseconds=1) for r in records)
    # Deterministic: the same inputs give byte-identical records.
    assert registration_body(input_ref, selection_ref, store=store) == body


def test_registration_body_refuses_a_selection_for_another_input_set(retained_baseline, tmp_path):
    from packages.alpha_lifecycle.local_campaign import registration_body

    store, input_ref, selection = retained_baseline
    selection_ref = store.put_bytes(canonical_json_bytes(selection), media_type='application/json')
    other = store.put_bytes(b'{}', media_type='application/json')
    with pytest.raises((ValueError, OSError)):
        registration_body(other, selection_ref, store=store)


@pytest.mark.parametrize('index', range(4))
def test_oos_body_binds_each_candidate_to_its_committed_registration(retained_baseline, index):
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest
    from packages.alpha_lifecycle.local_campaign import oos_body, propose_operation
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS
    from packages.alpha_lifecycle.publication import validate_evaluation_registration
    from tests.p3.test_registration_proof import registration_chain

    store, _, _, receipt = registration_chain(retained_baseline)
    receipt_ref = store.put_bytes(canonical_json_bytes(receipt), media_type='application/json')
    operation = f'p3-oos-a{index}-v1'
    body = oos_body(operation, receipt_ref, store=store)
    manifest = EvaluationManifest.model_validate_json(store.read_bytes(body['evaluation_manifest_ref']))
    proof = validate_evaluation_registration(manifest, store=store)
    intent = propose_operation(operation, proof.input_set_ref, body, store=store)
    assert intent.allowed_alpha_ids == (FAMILY_IDS[index],) and manifest.holdout_primary_ref is None
    assert oos_body(operation, receipt_ref, store=store) == body


def test_derive_body_request_feeds_propose_operation_and_rejects_foreign_refs(retained_baseline):
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.local_campaign import propose_operation
    from scripts.p3_local_controller import derive_body

    store, input_ref, selection = retained_baseline
    selection_ref = store.put_bytes(canonical_json_bytes(selection), media_type='application/json')
    source = InputSet.model_validate_json(store.read_bytes(input_ref)).source
    document = dict(workflow_operation='p3-register-family-v1', input_set_ref=input_ref.model_dump(mode='json'),
                    refs=dict(baseline_selection_ref=selection_ref.model_dump(mode='json')))
    request = derive_body(document, source=source, store=store)
    intent = propose_operation(request['workflow_operation'], request['input_set_ref'], request['body'], store=store)
    assert intent.operation == 'REGISTER_FAMILY' and 'primary_alpha_id' not in request
    for bad in (dict(document, refs={}), dict(document, workflow_operation='p3-integration-fixture-v1'),
                dict(document, extra=True)):
        with pytest.raises(ValueError):
            derive_body(bad, source=source, store=store)
