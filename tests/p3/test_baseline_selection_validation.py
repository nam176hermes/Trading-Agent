"""Retained baseline evidence must be closed before registration or qualification."""

import pytest

from packages.alpha_lifecycle.contracts.results import ReplayProof, ReplayReceipt
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_publication import _changed


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
