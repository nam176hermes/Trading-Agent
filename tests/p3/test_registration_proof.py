"""Synthetic SQL-result artifacts exercise postcommit family proof construction."""
import hashlib
from datetime import UTC, datetime, timedelta, time

import pytest

from packages.alpha_lifecycle.contracts.lifecycle import ExpectedHead
from packages.alpha_lifecycle.contracts.policy import _CANDIDATE_DIGESTS
from packages.alpha_lifecycle.lifecycle import plan_transition
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus
from packages.alpha_lifecycle.publication import build_publication_receipt
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_publication_repository import JobCommitResult
from services.job_worker.p3_publication_producer import build_publication_proposal
from tests.p3.test_lifecycle import _record, _evidence
from tests.p3.test_publication import _changed
from tests.p3.test_baseline_selection_validation import retained_baseline


def registration_chain(baseline, *, record_fault=None, dangling_selection=False):
    from packages.alpha_lifecycle.baseline_campaign import validate_research_inputs
    store,input_ref,selection = baseline
    inputs,folds,dataset,threshold = validate_research_inputs(input_ref,store)
    selection_ref = store.put_bytes(canonical_json_bytes(selection),media_type='application/json')
    updates = dict(source_sha=inputs.source.commit_sha,dataset_snapshot_sha256=dataset.snapshot_ref.content_sha256,
        cost_model_sha256=selection.selected_result.cost_model_sha256,baseline_id=selection.selected_id,
        baseline_version=selection.selected_result.baseline_version,lineage=(inputs.epoch_id,),
        training_start_at=datetime.combine(threshold.training_range.start,time.min,UTC),
        training_end_at=datetime.combine(threshold.training_range.end,time.max,UTC),
        validation_start_at=datetime.combine(threshold.training_range.end+timedelta(days=1),time.min,UTC),
        validation_end_at=datetime.combine(folds.folds[0].decision_start-timedelta(days=1),time.max,UTC),
        oos_start_at=datetime.combine(folds.folds[0].return_end_range.start,time.min,UTC),
        oos_end_at=datetime.combine(folds.folds[-1].return_end_range.end,time.max,UTC))
    if record_fault:
        updates[record_fault] = {'source_sha':'9'*40,'dataset_snapshot_sha256':'9'*64,
            'cost_model_sha256':'9'*64,'baseline_id':'B0_CASH' if selection.selected_id != 'B0_CASH' else 'B1_BUY_AND_HOLD',
            'lineage':('another-epoch',),'oos_end_at':updates['oos_end_at']+timedelta(days=1)}[record_fault]
    records = tuple(_record(AlphaLifecycleStatus.IDEA).model_copy(update={
        **updates,'alpha_id':alpha,'parameter_set_sha256':digest}) for alpha,digest in sorted(_CANDIDATE_DIGESTS.items()))
    record_refs = tuple(store.put_bytes(canonical_json_bytes(record),media_type='application/json') for record in records)
    evidence = _changed(_evidence(),input_set_ref=input_ref.model_dump(mode='json'),
        baseline_selection_ref=(_evidence().baseline_selection_ref if dangling_selection else selection_ref).model_dump(mode='json'),
        candidate_record_refs=[r.model_dump(mode='json') for r in record_refs])
    events,heads = [],[]
    for record in records:
        idea = plan_transition(record,None,evidence)
        candidate = plan_transition(record.model_copy(update={'lifecycle_status':AlphaLifecycleStatus.CANDIDATE}),idea,evidence)
        events.extend((idea,candidate))
        heads.append(ExpectedHead(alpha_id=record.alpha_id,version=record.version,sequence=0,event_digest=None))
    now = datetime(2026,9,10,tzinfo=UTC)
    proposal = build_publication_proposal(events=tuple(events),expected_heads=tuple(heads),evidence=evidence,
        epoch_id='synthetic',job_id='job_registration',observed_at=now,expires_at=now+timedelta(hours=1),store=store)
    request_ref = store.put_bytes(canonical_json_bytes(proposal.request),media_type='application/json')
    value = dict(schema_version='p3-job-commit-result-v1',job_id=proposal.request.job_id,
        idempotency_key=proposal.request.idempotency_key,semantic_request_digest=proposal.request.semantic_request_digest,
        prepublication_ref=proposal.request.evidence_ref,ledger_event_ids=[str(e.event_id) for e in proposal.entries],
        registry_event_refs=proposal.request.proposed_event_refs,alpha_outcome='NOT_EVALUATED')
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    commit = JobCommitResult.model_validate_json(canonical_json_bytes(value))
    receipt = build_publication_receipt(request_ref,commit,committed_at=now,store=store)
    return store,evidence,proposal,receipt


def test_registration_proof_is_derived_from_complete_committed_family(retained_baseline):
    from packages.alpha_lifecycle.publication import build_registration_proof
    store,evidence,proposal,receipt = registration_chain(retained_baseline)
    proof = build_registration_proof(receipt,store=store)
    assert proof.input_set_ref == evidence.input_set_ref
    assert proof.baseline_selection_ref == evidence.baseline_selection_ref
    assert proof.candidate_head_refs == proposal.request.proposed_event_refs[1::2]
    assert store.read_bytes(proof.publication_ref) == canonical_json_bytes(receipt)
    assert build_registration_proof(receipt,store=store) == proof


def test_registration_proof_rejects_receipt_with_unrelated_head(retained_baseline):
    from packages.alpha_lifecycle.publication import build_registration_proof
    store,_,proposal,receipt = registration_chain(retained_baseline)
    refs = list(receipt.registry_event_refs)
    refs[-1] = refs[1]
    with pytest.raises(ValueError,match='publication|registration'):
        build_registration_proof(_changed(receipt,registry_event_refs=[r.model_dump(mode='json') for r in refs]),store=store)


def test_registration_proof_requires_retained_registry_bytes(retained_baseline,monkeypatch):
    from packages.alpha_lifecycle.publication import build_registration_proof
    store,_,proposal,receipt = registration_chain(retained_baseline)
    original = store.read_bytes
    def missing(ref):
        if ref == proposal.request.proposed_event_refs[0]:
            raise FileNotFoundError('synthetic missing registry artifact')
        return original(ref)
    monkeypatch.setattr(store,'read_bytes',missing)
    with pytest.raises((ValueError,OSError)):
        build_registration_proof(receipt,store=store)


@pytest.mark.parametrize('fault',['source_sha','dataset_snapshot_sha256','cost_model_sha256',
    'baseline_id','lineage','oos_end_at','dangling_selection'])
def test_registration_proof_binds_actual_research_identity(retained_baseline,fault):
    from packages.alpha_lifecycle.publication import build_registration_proof
    store,_,_,receipt = registration_chain(retained_baseline,
        record_fault=None if fault == 'dangling_selection' else fault,dangling_selection=fault=='dangling_selection')
    with pytest.raises((ValueError,OSError)):
        build_registration_proof(receipt,store=store)
