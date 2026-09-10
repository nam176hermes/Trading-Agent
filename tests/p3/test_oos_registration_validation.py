"""Parent preflight closes retained registration before any candidate process."""
import json
import sys
from pathlib import Path

import pytest

from packages.alpha_lifecycle.baseline_campaign import validate_baseline_selection
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.alpha_lifecycle.publication import build_registration_proof
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor, SandboxHeld
from packages.engine_contracts.serialization import canonical_json_bytes
from scripts.generate_p3_specs import POLICY_SOURCE, _candidate_specs
from tests.p3.test_baseline_selection_validation import retained_baseline
from tests.p3.test_publication import _changed
from tests.p3.test_registration_proof import registration_chain
from tests.p3.test_replica_execution import _seal


def test_baseline_validation_never_writes_retained_evidence(retained_baseline, monkeypatch):
    store, input_ref, selection = retained_baseline
    ref = store.put_bytes(canonical_json_bytes(selection), media_type='application/json')
    def forbidden(*args, **kwargs):
        raise AssertionError('validation attempted to produce retained evidence')
    monkeypatch.setattr(store, 'put_bytes', forbidden)
    assert validate_baseline_selection(ref, input_ref, store) == selection


@pytest.mark.parametrize('fault', [None, 'missing_receipt', 'missing_event', 'reordered_heads',
    'idea_head', 'other_candidate', 'other_baseline', 'other_input'])
def test_oos_parent_requires_complete_registration_before_spawn(retained_baseline, tmp_path, monkeypatch, fault):
    store, evidence, proposal, receipt = registration_chain(retained_baseline)
    proof = build_registration_proof(receipt, store=store)
    if fault == 'reordered_heads':
        proof = _changed(proof, candidate_head_refs=[r.model_dump(mode='json') for r in reversed(proof.candidate_head_refs)])
    proof_ref = store.put_bytes(canonical_json_bytes(proof), media_type='application/json')
    specs = _candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))
    spec_ref = store.put_bytes(canonical_json_bytes(specs[0]), media_type='application/json')
    manifest_ref = _seal(store, schema_version='p3-evaluation-manifest-v1', mode='OOS',
        input_set_ref=evidence.input_set_ref,
        candidate_spec_ref=spec_ref,
        baseline_selection_ref=spec_ref if fault == 'other_baseline' else evidence.baseline_selection_ref,
        candidate_head_ref=proposal.request.proposed_event_refs[0] if fault == 'idea_head' else proof.candidate_head_refs[1 if fault == 'other_candidate' else 0],
        registration_proof_ref=proof_ref, holdout_primary_ref=None)
    if fault == 'other_input':
        proof = _changed(proof, input_set_ref=spec_ref.model_dump(mode='json'))
        # Preserve a canonical manifest but substitute the retained proof bytes via its real new ref.
        from packages.alpha_lifecycle.contracts.execution import EvaluationManifest
        manifest = EvaluationManifest.model_validate_json(store.read_bytes(manifest_ref))
        changed_ref = store.put_bytes(canonical_json_bytes(proof), media_type='application/json')
        manifest_ref = store.put_bytes(canonical_json_bytes(_changed(manifest,
            registration_proof_ref=changed_ref.model_dump(mode='json'))), media_type='application/json')
    inputs = InputSet.model_validate_json(store.read_bytes(evidence.input_set_ref))
    original_read = store.read_bytes
    missing = proof.publication_ref if fault == 'missing_receipt' else proposal.request.proposed_event_refs[0]
    def read(ref):
        if fault in {'missing_receipt', 'missing_event'} and ref == missing:
            raise FileNotFoundError('synthetic absent registration custody')
        return original_read(ref)
    def forbidden_write(*args, **kwargs):
        raise AssertionError('registration preflight attempted to produce evidence')
    monkeypatch.setattr(store, 'read_bytes', read)
    monkeypatch.setattr(store, 'put_bytes', forbidden_write)
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox', lambda value: Path('/usr/bin/bwrap'))
    executor = BubblewrapExecutor(store=store, store_root=tmp_path/'inputs', release_root=Path(__file__).resolve().parents[2],
        python=Path(sys.executable), source=inputs.source, environment_ref=inputs.environment_ref,
        sandbox_policy_digest='c'*64)
    class ReachedArgv(Exception):
        pass
    def argv(*args):
        raise ReachedArgv('validated parent reached argv')
    monkeypatch.setattr(executor, '_argv', argv)
    with pytest.raises(ReachedArgv if fault is None else SandboxHeld):
        executor.execute(manifest_ref, replicate='R1', logical_trial_id='synthetic', output_dir=tmp_path)
    assert (tmp_path/'manifest-ref.json').exists() == (fault is None)
