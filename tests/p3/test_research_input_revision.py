"""Every research calculation rejects an opaque revision object."""
import pytest
from packages.alpha_lifecycle.baseline_campaign import _read,validate_research_inputs
from packages.alpha_lifecycle.contracts.execution import BaselineManifest,InputSet
from packages.alpha_lifecycle.contracts.data import PITProof
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_publication import _changed
from tests.p3.test_replica_execution import baseline_inputs


def test_research_input_cannot_replace_revision_commitment_with_opaque_json(tmp_path):
    store,manifest_ref=baseline_inputs(tmp_path/'inputs')
    manifest=_read(store,manifest_ref,BaselineManifest)
    inputs=_read(store,manifest.input_set_ref,InputSet)
    pit=_read(store,inputs.pit_proof_ref,PITProof)
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    pit=_changed(pit,revision_proof_ref=retain({'unverified':True}).model_dump(mode='json'))
    inputs=_changed(inputs,pit_proof_ref=retain(pit).model_dump(mode='json'))
    with pytest.raises(ValueError):
        validate_research_inputs(retain(inputs),store)
