"""Portable retained research seeds; all authority in these builders is synthetic."""
from datetime import timedelta
from decimal import localcontext, ROUND_HALF_EVEN
import json
from pathlib import Path
import sys

import pytest

from packages.alpha_lifecycle.baseline_campaign import execute_baseline_manifest, _read
from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
from packages.alpha_lifecycle.contracts.results import RegimeThreshold, ReplayReceipt, EvaluationResult
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_publication import _changed
from tests.p3.test_registration_proof import registration_chain
from tests.p3.test_replica_execution import baseline_inputs, _seal


def build_retained_baseline(tmp_path_factory, inputs_factory=baseline_inputs):
    root = tmp_path_factory.mktemp('synthetic-baseline-selection')
    store,manifest_ref = inputs_factory(root/'inputs')
    manifest = BaselineManifest.model_validate_json(store.read_bytes(manifest_ref))
    inputs = InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    threshold = RegimeThreshold.model_validate_json(store.read_bytes(inputs.regime_threshold_ref))
    threshold = _changed(threshold,training_range={'start':str(threshold.training_range.start),
        'end':str(threshold.training_range.start+timedelta(days=299))},sample_count=280)
    threshold_ref = store.put_bytes(canonical_json_bytes(threshold),media_type='application/json')
    inputs = _changed(inputs,regime_threshold_ref=threshold_ref.model_dump(mode='json'))
    input_ref = store.put_bytes(canonical_json_bytes(inputs),media_type='application/json')
    manifest = _changed(manifest,input_set_ref=input_ref.model_dump(mode='json'))
    manifest_ref = store.put_bytes(canonical_json_bytes(manifest),media_type='application/json')
    release = Path(__file__).resolve().parents[2]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox',lambda value:Path('/usr/bin/bwrap'))
        executor = BubblewrapExecutor(store=store,store_root=root/'inputs',release_root=release,
            python=Path(sys.executable),source=inputs.source,environment_ref=inputs.environment_ref,sandbox_policy_digest='c'*64)
        patch.setattr(executor,'_argv',lambda request,result,output,seccomp_fd:(sys.executable,'-I','-B',
            str(release/'scripts/run_p3_evaluation_child.py'),str(request),str(root/'inputs'),str(result)))
        selection = execute_baseline_manifest(manifest_ref,executor,logical_trial_id='synthetic-baselines',output_root=root/'runs')
    return store,manifest.input_set_ref,selection



def build_synthetic_oos(tmp_path_factory):
    """Real deterministic children with explicit synthetic authority and portable transport."""
    from packages.alpha_lifecycle.publication import build_registration_proof
    from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
    from packages.alpha_lifecycle.replay import run_replays
    from packages.alpha_lifecycle.pit_suite import build_pit_suite_receipt,PIT_EVIDENCE_MEDIA
    from packages.alpha_lifecycle.contracts.data import PITProof
    from tests.p3.test_pit_suite import suite_inputs
    from scripts.generate_p3_specs import _candidate_specs,POLICY_SOURCE
    from tests.p3 import test_replica_execution as replica_fixture
    original_bar=replica_fixture._bar
    from services.job_worker.p3_output_validation import _reference
    def prepared_inputs(root):
        store,ref=baseline_inputs(root,return_count=90)
        manifest=_read(store,ref,BaselineManifest)
        inputs=_read(store,manifest.input_set_ref,InputSet)
        pit=_read(store,inputs.pit_proof_ref,PITProof)
        helper=root.parent/'pit-helper'
        helper.mkdir(mode=0o700)
        pit_store,suite_ref,_,report,metadata=suite_inputs(helper)
        store.put_bytes(pit_store.read_bytes(suite_ref),media_type=PIT_EVIDENCE_MEDIA)
        collection={**report,'collection_only':True,'summary':{'collected':14},
            'tests':[{**row,'outcome':'collected','phase':'collection'} for row in report['tests']]}
        collection_ref=store.put_bytes(canonical_json_bytes(collection),media_type='application/json')
        report_ref=store.put_bytes((json.dumps(report,indent=2,sort_keys=True)+'\n').encode(),media_type=PIT_EVIDENCE_MEDIA)
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
        patch.setattr(replica_fixture,'_bar',lambda day:_changed(original_bar(day),quote_volume='100000000',partition_ref=_reference(b'{}').model_dump(mode='json')))
        baseline=build_retained_baseline(tmp_path_factory, inputs_factory=prepared_inputs)
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
