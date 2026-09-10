"""Retained baseline evidence must be closed before registration or qualification."""
from pathlib import Path
import sys
from datetime import timedelta

import pytest

from packages.alpha_lifecycle.baseline_campaign import execute_baseline_manifest
from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
from packages.alpha_lifecycle.contracts.results import ReplayProof, ReplayReceipt
from packages.alpha_lifecycle.contracts.results import RegimeThreshold
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_publication import _changed
from tests.p3.test_replica_execution import baseline_inputs


@pytest.fixture(scope='module')
def retained_baseline(tmp_path_factory):
    root = tmp_path_factory.mktemp('synthetic-baseline-selection')
    store,manifest_ref = baseline_inputs(root/'inputs')
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
        patch.setattr(executor,'_argv',lambda request,result,output:(sys.executable,'-I','-B',
            str(release/'scripts/run_p3_evaluation_child.py'),str(request),str(root/'inputs'),str(result)))
        selection = execute_baseline_manifest(manifest_ref,executor,logical_trial_id='synthetic-baselines',output_root=root/'runs')
    return store,manifest.input_set_ref,selection


@pytest.mark.parametrize('fault',[None,'duplicate_replica','wrong_manifest','wrong_source','wrong_environment',
    'wrong_policy','wrong_trial','wrong_inventory','wrong_result_size','wrong_winner','wrong_selection_policy'])
def test_retained_selection_requires_its_complete_baseline_replay(retained_baseline,fault):
    from packages.alpha_lifecycle.baseline_campaign import validate_baseline_selection
    store,input_ref,selection = retained_baseline
    def seal(value):
        return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    proof = ReplayProof.model_validate_json(store.read_bytes(selection.baseline_replay_proof_ref))
    if fault == 'duplicate_replica':
        proof = _changed(proof,receipt_refs=[r.model_dump(mode='json') for r in (proof.receipt_refs[0],)*3])
    elif fault == 'wrong_manifest':
        proof = _changed(proof,manifest_digest='9'*64)
    elif fault not in {None,'wrong_winner','wrong_selection_policy'}:
        receipt = ReplayReceipt.model_validate_json(store.read_bytes(proof.receipt_refs[1]))
        updates = {
            'wrong_source':{'source':{**receipt.source.model_dump(mode='json'),'commit_sha':'9'*40}},
            'wrong_environment':{'environment_ref':input_ref.model_dump(mode='json')},
            'wrong_policy':{'sandbox_policy_digest':'9'*64},
            'wrong_trial':{'logical_trial_id':'another-trial'},
            'wrong_inventory':{'output_inventory_digest':'9'*64},
            'wrong_result_size':{'result_ref':{**receipt.result_ref.model_dump(mode='json'),'size_bytes':1}},
        }[fault]
        refs = list(proof.receipt_refs)
        refs[1] = seal(_changed(receipt,**updates))
        proof = _changed(proof,receipt_refs=[r.model_dump(mode='json') for r in refs])
    selection = _changed(selection,baseline_replay_proof_ref=seal(proof).model_dump(mode='json'))
    if fault == 'wrong_winner':
        selection = _changed(selection,selected_id='B0_CASH' if selection.selected_id != 'B0_CASH' else 'B1_BUY_AND_HOLD')
    elif fault == 'wrong_selection_policy':
        selection = _changed(selection,selection_policy_digest='9'*64)
    if fault is None:
        assert validate_baseline_selection(seal(selection),input_ref,store) == selection
    else:
        with pytest.raises(ValueError,match='baseline|replay'):
            validate_baseline_selection(seal(selection),input_ref,store)
