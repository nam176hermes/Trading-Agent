"""Synthetic InputSet bindings; no protected producer or reviewer authority."""
import pytest

from tests.p3.test_research_commitment import inputs, _sealed
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.engine_contracts.serialization import canonical_json_bytes


@pytest.fixture
def graph(inputs):
    store,retain,source,_,dataset,dataset_ref,_,commitment=inputs
    placeholder=retain({})
    pit=_sealed(schema_version='p3-p-i-t-proof-v1',dataset_ref=dataset_ref,
        fold_manifest_ref=placeholder,vintage_class=dataset.vintage_class,
        historical_vintage_verified=False,revision_proof_ref=retain(commitment),
        no_future_suite_ref=placeholder,limitations=dataset.limitations)
    value=_sealed(schema_version='p3-input-set-v1',source=source,epoch_id='synthetic',
        policy_digest='5'*64,family_digest='6'*64,dataset_evidence_ref=dataset_ref,
        fold_manifest_ref=placeholder,pit_proof_ref=retain(pit),environment_ref=placeholder,
        regime_threshold_ref=placeholder,integration_receipt_ref=placeholder,
        cost_model=dict(fee_bps=0,spread_bps=0,slippage_bps=0,funding_bps=0,borrow_bps=0))
    return store,retain,pit,InputSet.model_validate_json(canonical_json_bytes(value))


def test_inputset_resolves_exact_revision_commitment_without_writes(graph,monkeypatch):
    from packages.alpha_lifecycle import research_custody
    store,retain,_,inputs=graph
    ref=retain(inputs)
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('resolver wrote artifacts')))
    result=research_custody.validate_input_commitment(ref,store=store)
    assert result.source==inputs.source and result.policy_digest==inputs.policy_digest


@pytest.mark.parametrize('fault',['revision','dataset','fold','limitations','vintage','historical','source','policy','media'])
def test_input_commitment_rejects_scope_drift_before_writes(graph,monkeypatch,fault):
    from packages.alpha_lifecycle.research_custody import validate_input_commitment
    from tests.p3.test_publication import _changed
    store,retain,pit,inputs=graph
    placeholder=retain({})
    if fault in ('source','policy'):
        inputs=_changed(inputs,**({'source':{**inputs.source.model_dump(mode='json'),'commit_sha':'9'*40}}
            if fault=='source' else {'policy_digest':'9'*64}))
    else:
        pit=dict(pit)
        pit.pop('digest')
        changes={'revision':{'revision_proof_ref':placeholder},
            'dataset':{'dataset_ref':placeholder},'fold':{'fold_manifest_ref':retain({'wrong':'fold'})},
            'limitations':{'limitations':[]},'vintage':{'vintage_class':'HISTORICAL_VINTAGE_VERIFIED'},
            'historical':{'historical_vintage_verified':True},'media':{}}
        pit.update(changes[fault])
        inputs=_changed(inputs,pit_proof_ref=retain(_sealed(**pit)).model_dump(mode='json'))
    ref=retain(inputs)
    if fault=='media':
        ref=ref.model_copy(update={'media_type':'text/plain'})
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('resolver wrote artifacts')))
    with pytest.raises(ValueError):
        validate_input_commitment(ref,store=store)
