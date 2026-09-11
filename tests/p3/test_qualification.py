from packages.alpha_lifecycle import protocol
from packages.alpha_lifecycle import qualification


def test_adapter_uses_the_unchanged_frozen_qualification_function() -> None:
    assert qualification.evaluate_alpha_qualification is protocol.evaluate_alpha_qualification


import hashlib
import json
import sys
from pathlib import Path
from decimal import localcontext,ROUND_HALF_EVEN

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.alpha_lifecycle.baseline_campaign import _read
from packages.alpha_lifecycle.contracts.execution import BaselineManifest,InputSet
from packages.alpha_lifecycle.contracts.results import EvaluationResult,ReplayProof,ReplayReceipt
from tests.p3.test_publication import _changed
from tests.p3.test_registration_proof import registration_chain
from tests.p3.test_replica_execution import _seal


@pytest.mark.parametrize('fault',['duplicate_replica','manifest','source','environment','sandbox','result_size','unretained'])
def test_qualification_rejects_unbound_replay_before_writing(synthetic_oos,monkeypatch,fault):
    store,evaluation,proof=synthetic_oos
    if fault == 'duplicate_replica':
        proof=_changed(proof,receipt_refs=[ref.model_dump(mode='json') for ref in (proof.receipt_refs[0],)*3])
    elif fault == 'manifest':
        proof=_changed(proof,manifest_digest='9'*64)
    else:
        receipt=_read(store,proof.receipt_refs[1],ReplayReceipt)
        updates={'source':{'source':{**receipt.source.model_dump(mode='json'),'commit_sha':'9'*40}},
            'environment':{'environment_ref':evaluation.manifest_ref.model_dump(mode='json')},
            'sandbox':{'sandbox_policy_digest':'9'*64},
            'result_size':{'result_ref':{**receipt.result_ref.model_dump(mode='json'),'size_bytes':1}},
            'unretained':{}}[fault]
        changed=_changed(receipt,**updates)
        ref=store.put_bytes(canonical_json_bytes(changed),media_type='application/json')
        refs=list(proof.receipt_refs);refs[1]=ref
        proof=_changed(proof,receipt_refs=[ref.model_dump(mode='json') for ref in refs])
    original=store.read_bytes
    result_digest=proof.result_digest
    def read(ref):
        if fault == 'unretained' and ref.content_sha256 == result_digest:
            raise FileNotFoundError('synthetic unretained evaluation')
        return original(ref)
    writes=[]
    monkeypatch.setattr(store,'read_bytes',read)
    monkeypatch.setattr(store,'put_bytes',lambda *args,**kwargs:writes.append(args) or (_ for _ in ()).throw(AssertionError('qualification wrote before validating replay')))
    with pytest.raises((ValueError,OSError)):
        qualification.qualify(evaluation,proof,store)
    assert writes == []


def test_qualification_of_retained_replay_keeps_pipeline_and_alpha_distinct(synthetic_oos):
    store,evaluation,proof=synthetic_oos
    with localcontext() as context:
        context.prec=50
        context.rounding=ROUND_HALF_EVEN
        bundle=qualification.qualify(evaluation,proof,store)
    assert bundle.pipeline_verdict == 'PASS'
    assert bundle.alpha_verdict == 'FAIL'
    assert len(bundle.criteria) == 16
    assert not all(criterion.passed for criterion in bundle.criteria)


def test_qualification_pins_decimal_context_without_changing_caller(synthetic_oos):
    store,evaluation,proof=synthetic_oos
    with localcontext() as context:
        context.prec=50
        context.rounding=ROUND_HALF_EVEN
        expected=qualification.qualify(evaluation,proof,store)
    with localcontext() as context:
        context.prec=6
        result=qualification.qualify(evaluation,proof,store)
        assert context.prec == 6
    assert result == expected


@pytest.mark.parametrize('fault',[None,'intent_input','intent_candidate','wrong_manifest'])
def test_oos_proposal_contains_complete_bound_failure(synthetic_oos,fault):
    from datetime import UTC,datetime,timedelta
    from packages.alpha_lifecycle.operation_input import P3OperationInput,FAMILY_IDS
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest
    from packages.alpha_lifecycle.lifecycle import read_registry_event
    from packages.alpha_lifecycle.registry import _IDENTITY_FIELDS
    from services.job_worker import p3_publication_producer as producer
    store,evaluation,proof=synthetic_oos
    manifest=_read(store,evaluation.manifest_ref,EvaluationManifest)
    candidate=1 if fault=='intent_candidate' else 0
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation=f'p3-oos-a{candidate}-v1',
        operation='OOS',input_set_ref=evaluation.manifest_ref if fault=='intent_input' else manifest.input_set_ref,
        allowed_alpha_ids=(FAMILY_IDS[candidate],),body=dict(evaluation_manifest_ref=manifest.input_set_ref if fault=='wrong_manifest' else evaluation.manifest_ref))
    intent=_read(store,intent_ref,P3OperationInput)
    now=datetime(2026,9,10,tzinfo=UTC)
    args=dict(job_id='job_oos',observed_at=now,expires_at=now+timedelta(hours=1),store=store)
    if fault:
        with pytest.raises(ValueError):
            producer.prepare_candidate_oos(intent,evaluation,proof,**args)
    else:
        proposal=producer.prepare_candidate_oos(intent,evaluation,proof,**args)
        assert proposal.request.stage=='RESEARCH_DECISION'
        assert len(proposal.entries)==2
        events=[read_registry_event(store,ref) for ref in proposal.request.proposed_event_refs]
        assert [event.record.lifecycle_status.value for event in events]==['RESEARCHED','REJECTED']
        assert all(event.record.qualification_decision.value=='FAIL' for event in events)
        head=read_registry_event(store,manifest.candidate_head_ref)
        for event in events:
            assert all(getattr(event.record,name)==getattr(head.record,name) for name in _IDENTITY_FIELDS)
        assert proposal.request.expected_heads[0].event_digest==head.event_sha256
        assert producer.prepare_candidate_oos(intent,evaluation,proof,**args)==proposal
