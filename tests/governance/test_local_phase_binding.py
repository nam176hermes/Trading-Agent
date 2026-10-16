"""Real LOCAL prerequisite/campaign domains; no runtime or installed authority."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity, QualificationBinding
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity, InputSet
from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_SHA256, SANDBOX_PROFILE_V2_SHA256
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.local_pre_p3 import POLICY_PATH
from tests.p3.test_local_qualification import sealed


def phase_inputs(root, *, local_v2=True):
    policy_path = root / POLICY_PATH
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy = {'policy': 'frozen'}
    policy_path.write_text(json.dumps(policy, indent=2) + '\n')
    source = SourceIdentity(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='source-closure-v1',
        closure_policy_sha256='c'*64, closure_sha256='d'*64)
    identity = LocalRunIdentity(issuer_kind='LOCAL', issuer_identity='synthetic', run_id=uuid4(), attempt=1,
        host_id='e'*64, boot_id=uuid4())
    store_root = root / 'cas'; store_root.mkdir(mode=0o700, exist_ok=True)
    store = LocalArtifactStore(store_root)
    def put(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json')
    environment = EnvironmentIdentity.model_validate(sealed(dict(schema_version='p3-environment-identity-v1',
        python_version='3.11', root_lock_digest='1'*64, native_manifest_digest='2'*64,
        sandbox_policy_digest=SANDBOX_PROFILE_SHA256, platform='linux-x86_64', decimal_precision=50)))
    original_ref = put(environment)
    fields = environment.model_dump(mode='json')
    if local_v2:
        fields['sandbox_policy_digest'] = SANDBOX_PROFILE_V2_SHA256
    environment_ref = put(EnvironmentIdentity.model_validate(sealed(fields)))
    ref = put({'retained': 'fixture'})
    canonical_policy = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    inputs = InputSet.model_validate(sealed(dict(schema_version='p3-input-set-v1', source=source,
        epoch_id='test-epoch', policy_digest=canonical_policy, family_digest='3'*64,
        dataset_evidence_ref=ref, fold_manifest_ref=ref, pit_proof_ref=ref, environment_ref=environment_ref,
        cost_model=dict(fee_bps=1, spread_bps=1, slippage_bps=1, funding_bps=0, borrow_bps=0),
        regime_threshold_ref=ref, integration_receipt_ref=ref)))
    binding = QualificationBinding(gate='P3_PHASE_EXIT', source=source, policy_digest=canonical_policy,
        input_ref=put(inputs), environment_ref=environment_ref)
    ready = SimpleNamespace(identity=identity, binding=binding.model_copy(update=dict(gate='PRE_P3_READY',
        policy_digest=hashlib.sha256(policy_path.read_bytes()).hexdigest(), environment_ref=original_ref)))
    assert ready.binding.policy_digest != binding.policy_digest
    assert (ready.binding.environment_ref != binding.environment_ref) == local_v2
    return identity, ready, binding, store, store_root


import pytest


@pytest.mark.parametrize('local_v2', [False, True])
@pytest.mark.parametrize('fault', [None, 'raw_campaign_policy', 'canonical_prerequisite_policy',
    'identity', 'source', 'input_source', 'input_policy', 'input_environment',
    'environment_lock', 'environment_native', 'unknown_sandbox', 'bad_environment_digest'])
def test_phase_binding_authenticates_the_two_domains(tmp_path, local_v2, fault):
    from packages.local_pre_p3 import validate_local_phase_binding
    from packages.alpha_lifecycle.replica_store import _read
    identity, ready, binding, store, _ = phase_inputs(tmp_path, local_v2=local_v2)
    def put(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json')
    if fault == 'raw_campaign_policy':
        binding = binding.model_copy(update={'policy_digest': ready.binding.policy_digest})
    elif fault == 'canonical_prerequisite_policy':
        ready.binding = ready.binding.model_copy(update={'policy_digest': binding.policy_digest})
    elif fault == 'identity':
        identity = identity.model_copy(update={'attempt': 2})
    elif fault == 'source':
        binding = binding.model_copy(update={'source': binding.source.model_copy(update={'commit_sha': '9'*40})})
    elif fault and fault.startswith('input_'):
        fields = _read(store, binding.input_ref, InputSet).model_dump(mode='json')
        if fault == 'input_source': fields['source']['commit_sha'] = '9'*40
        elif fault == 'input_policy': fields['policy_digest'] = '9'*64
        else: fields['environment_ref'] = ready.binding.input_ref.model_dump(mode='json')
        binding = binding.model_copy(update={'input_ref': put(sealed(fields))})
    elif fault:
        fields = _read(store, binding.environment_ref, EnvironmentIdentity).model_dump(mode='json')
        if fault == 'environment_lock': fields['root_lock_digest'] = '9'*64
        elif fault == 'environment_native': fields['native_manifest_digest'] = '9'*64
        elif fault == 'unknown_sandbox': fields['sandbox_policy_digest'] = '9'*64
        fields = sealed(fields)
        if fault == 'bad_environment_digest': fields['digest'] = '9'*64
        environment_ref = put(fields)
        inputs = _read(store, binding.input_ref, InputSet).model_dump(mode='json')
        inputs['environment_ref'] = environment_ref.model_dump(mode='json')
        binding = binding.model_copy(update={'input_ref': put(sealed(inputs)), 'environment_ref': environment_ref})
    if fault:
        with pytest.raises(ValueError):
            validate_local_phase_binding(tmp_path, identity, binding, ready, store=store)
    else:
        validate_local_phase_binding(tmp_path, identity, binding, ready, store=store)
