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
from tests.p3.test_baseline_selection_validation import retained_baseline
from tests.p3.test_publication import _changed
from tests.p3.test_registration_proof import registration_chain
from tests.p3.test_replica_execution import _seal


@pytest.fixture(scope='module')
def synthetic_oos(tmp_path_factory):
    """Real deterministic children with explicit synthetic authority and portable transport."""
    from packages.alpha_lifecycle.publication import build_registration_proof
    from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
    from packages.alpha_lifecycle.replay import run_replays
    from packages.alpha_lifecycle.pit_suite import build_pit_suite_receipt
    from packages.alpha_lifecycle.contracts.data import PITProof
    from tests.p3.test_pit_suite import suite_inputs
    from scripts.generate_p3_specs import _candidate_specs,POLICY_SOURCE
    from tests.p3 import test_baseline_selection_validation as baseline_fixture
    baseline_inputs=baseline_fixture.baseline_inputs
    from tests.p3 import test_replica_execution as replica_fixture
    original_bar=replica_fixture._bar
    def prepared_inputs(root):
        store,ref=baseline_inputs(root,return_count=90)
        manifest=_read(store,ref,BaselineManifest)
        inputs=_read(store,manifest.input_set_ref,InputSet)
        pit=_read(store,inputs.pit_proof_ref,PITProof)
        helper=root.parent/'pit-helper'
        helper.mkdir(mode=0o700)
        pit_store,suite_ref,_,report,metadata=suite_inputs(helper)
        store.put_bytes(pit_store.read_bytes(suite_ref),media_type='application/json')
        collection={**report,'collection_only':True,'summary':{'collected':14},
            'tests':[{**row,'outcome':'collected','phase':'collection'} for row in report['tests']]}
        collection_ref=store.put_bytes(canonical_json_bytes(collection),media_type='application/json')
        report_ref=store.put_bytes((json.dumps(report,indent=2,sort_keys=True)+'\n').encode(),media_type='application/json')
        suite=build_pit_suite_receipt(inputs.source,suite_ref,collection_ref,report_ref,metadata,store=store)
        suite_ref=store.put_bytes(canonical_json_bytes(suite),media_type='application/json')
        pit=_changed(pit,no_future_suite_ref=suite_ref.model_dump(mode='json'))
        pit_ref=store.put_bytes(canonical_json_bytes(pit),media_type='application/json')
        inputs=_changed(inputs,pit_proof_ref=pit_ref.model_dump(mode='json'))
        input_ref=store.put_bytes(canonical_json_bytes(inputs),media_type='application/json')
        manifest=_changed(manifest,input_set_ref=input_ref.model_dump(mode='json'))
        return store,store.put_bytes(canonical_json_bytes(manifest),media_type='application/json')
    with pytest.MonkeyPatch.context() as patch:
        # Valid high-volume synthetic market; production participation remains uncapped.
        patch.setattr(replica_fixture,'_bar',lambda day:_changed(original_bar(day),quote_volume='100000000'))
        patch.setattr(baseline_fixture,'baseline_inputs',prepared_inputs)
        baseline=retained_baseline.__wrapped__(tmp_path_factory)
    store,evidence,_,receipt=registration_chain(baseline)
    registration=build_registration_proof(receipt,store=store)
    registration_ref=store.put_bytes(canonical_json_bytes(registration),media_type='application/json')
    spec=_candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))[0]
    spec_ref=store.put_bytes(canonical_json_bytes(spec),media_type='application/json')
    manifest_ref=_seal(store,schema_version='p3-evaluation-manifest-v1',mode='OOS',
        input_set_ref=registration.input_set_ref,candidate_spec_ref=spec_ref,
        baseline_selection_ref=registration.baseline_selection_ref,candidate_head_ref=registration.candidate_head_refs[0],
        registration_proof_ref=registration_ref,holdout_primary_ref=None)
    inputs=_read(store,registration.input_set_ref,InputSet)
    release=Path(__file__).resolve().parents[2]
    runs=tmp_path_factory.mktemp('synthetic-oos-runs')
    with pytest.MonkeyPatch.context() as patch,localcontext() as context:
        context.prec=50
        context.rounding=ROUND_HALF_EVEN
        patch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox',lambda value:Path('/usr/bin/bwrap'))
        executor=BubblewrapExecutor(store=store,store_root=store._root,release_root=release,python=Path(sys.executable),
            source=inputs.source,environment_ref=inputs.environment_ref,sandbox_policy_digest='c'*64)
        patch.setattr(executor,'_argv',lambda request,result,output,seccomp_fd:(sys.executable,'-I','-B',
            str(release/'scripts/run_p3_evaluation_child.py'),str(request),str(store._root),str(result)))
        proof=run_replays(manifest_ref,executor,logical_trial_id='p3-oos-a0-v1',output_root=runs)
    receipt=_read(store,proof.receipt_refs[0],ReplayReceipt)
    evaluation=_read(store,receipt.result_ref,EvaluationResult)
    return store,evaluation,proof


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
