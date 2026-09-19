"""P3_RESEARCH_INPUTS admission; protected custody is injected, never installed.

These tests grant no protected admission. They pin the semantics the official
LOCAL lane must enforce before an InputSet may be enqueued or executed.
"""
from datetime import UTC, datetime
import hashlib
import importlib
import importlib.util
from pathlib import Path
from uuid import UUID

import pytest

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.runtime_release import config
from tests.p3.test_research_commitment import inputs  # shared synthetic 2,800-row subject


NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
HOST = 'a' * 64
BOOT = UUID('00000000-0000-4000-8000-000000000001')
SAFE = dict(broker=False, live=False, network=False, production=False)
ISSUER = 'operator-a'
GATE = 'P3_RESEARCH_INPUTS'


def sealed(value):
    value = {k: v for k, v in value.items() if k != 'digest'}
    return {**value, 'digest': hashlib.sha256(canonical_json_bytes(value)).hexdigest()}


@pytest.fixture
def research(tmp_path, monkeypatch):
    assert importlib.util.find_spec('packages.alpha_lifecycle.local_research'), \
        'local research admission validator is missing'
    module = importlib.import_module('packages.alpha_lifecycle.local_research')
    root = tmp_path / 'cas'; root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)

    def retain(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json').model_dump(mode='json')

    source = dict(commit_sha='b'*40, tree_sha='c'*40,
        closure_schema_version='trading-agent-source-closure-v1',
        closure_policy_sha256='d'*64, closure_sha256='e'*64)
    identity = dict(issuer_kind='LOCAL', issuer_identity=ISSUER,
        run_id='00000000-0000-4000-8000-000000000002', attempt=1,
        host_id=HOST, boot_id=str(BOOT))
    input_set_ref = retain({'input_set': 'official-research'})
    other_input_ref = retain({'input_set': 'a-different-campaign'})
    environment_ref = retain({'environment': 'observed'})
    binding = dict(gate=GATE, source=source, policy_digest='f'*64,
        input_ref=input_set_ref, environment_ref=environment_ref)
    receipt = sealed(dict(schema_version='local-phase-qualification-receipt-v1', identity=identity,
        binding=binding, started_at='2026-09-18T11:00:00Z', completed_at='2026-09-18T11:30:00Z',
        expires_at='2026-09-18T13:00:00Z', evidence_refs=[retain({'observed': 'research proof'})],
        status='PASS', authority=SAFE))
    approval = sealed(dict(schema_version='local-qualification-approval-v1', issuer_identity=ISSUER,
        receipt_ref=retain(receipt), issued_at='2026-09-18T11:45:00Z',
        expires_at='2026-09-18T13:00:00Z', revoked=False, authority=SAFE))
    state = {'approval': approval, 'reads': 0}

    expected_path = Path(f'/etc/trading-agent/local-qualification/{ISSUER}/{GATE}.json')

    def protected_bytes(path):
        # Path is derived from the authenticated issuer; another issuer finds nothing.
        if path != expected_path:
            raise config.ProtectedAuthorityError(f'no protected approval at {path}')
        state['reads'] += 1
        return canonical_json_bytes(state['approval']) + b'\n', (1, 2)

    qualification = importlib.import_module('packages.alpha_lifecycle.local_qualification')

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(config, '_read_protected_file', protected_bytes)
    monkeypatch.setattr(qualification, '_host_identity', lambda: (HOST, BOOT))
    monkeypatch.setattr(qualification, 'datetime', Clock)
    monkeypatch.setattr(module, '_host_identity', lambda: (HOST, BOOT))
    monkeypatch.setattr(module, '_now', lambda: NOW)
    return module, store, identity, source, binding, input_set_ref, other_input_ref, environment_ref, state


def test_gate_literal_accepts_the_research_inputs_gate():
    """The admission gate must exist without joining the fixed Pre-P3 prerequisite set."""
    from packages.alpha_lifecycle.contracts.authority import QualificationBinding
    assert GATE in QualificationBinding.model_fields['gate'].annotation.__args__


def test_backup_custody_metadata_rejects_a_researcher_owned_root(tmp_path):
    from packages.alpha_lifecycle.local_research import protected_backup_identity
    root=tmp_path/'backup';root.mkdir(mode=0o500)
    with pytest.raises(ValueError,match='unprotected'):
        protected_backup_identity(root)


def test_valid_research_qualification_admits_the_declared_input_set(research):
    module, store, identity, source, binding, input_set_ref, _other, environment_ref, _state = research
    receipt = module.require_research_inputs_qualified(identity, source=source,
        policy_digest='f'*64, input_set_ref=input_set_ref, environment_ref=environment_ref,
        store=store)
    assert receipt.binding.gate == GATE
    assert receipt.binding.input_ref.model_dump(mode='json') == input_set_ref
    assert receipt.identity.issuer_identity == ISSUER


def test_receipt_bound_to_a_different_input_set_is_rejected(research):
    """A valid receipt must not certify an InputSet it was not issued for."""
    module, store, identity, source, _binding, _input_set_ref, other_input_ref, environment_ref, _state = research
    with pytest.raises(ValueError):
        module.require_research_inputs_qualified(identity, source=source,
            policy_digest='f'*64, input_set_ref=other_input_ref,
            environment_ref=environment_ref, store=store)


@pytest.mark.parametrize('mutation', ['source', 'policy', 'environment', 'issuer', 'host'])
def test_admission_rejects_a_mismatched_expectation(research, mutation):
    """Source, policy, environment, issuer and host each bind the admission."""
    module, store, identity, source, _binding, input_set_ref, _other, environment_ref, _state = research
    kwargs = dict(source=source, policy_digest='f'*64, input_set_ref=input_set_ref,
        environment_ref=environment_ref, store=store)
    if mutation == 'source':
        kwargs['source'] = dict(source, commit_sha='9'*40)
    elif mutation == 'policy':
        kwargs['policy_digest'] = '0'*64
    elif mutation == 'environment':
        kwargs['environment_ref'] = dict(environment_ref, content_sha256='1'*64)
    elif mutation == 'issuer':
        identity = dict(identity, issuer_identity='operator-b')
    else:
        identity = dict(identity, host_id='9'*64)
    with pytest.raises(ValueError):
        module.require_research_inputs_qualified(identity, **kwargs)


def test_revoked_approval_is_rejected(research):
    """Revocation between issue and admission must fail closed."""
    module, store, identity, source, _binding, input_set_ref, _other, environment_ref, state = research
    state['approval'] = sealed(dict(
        {k: v for k, v in state['approval'].items() if k != 'digest'}, revoked=True))
    with pytest.raises(ValueError):
        module.require_research_inputs_qualified(identity, source=source,
            policy_digest='f'*64, input_set_ref=input_set_ref,
            environment_ref=environment_ref, store=store)


def test_admission_rereads_the_protected_approval(research):
    """The approval is read again after the proof, so a mid-read withdrawal is caught."""
    module, store, identity, source, _binding, input_set_ref, _other, environment_ref, state = research
    module.require_research_inputs_qualified(identity, source=source,
        policy_digest='f'*64, input_set_ref=input_set_ref,
        environment_ref=environment_ref, store=store)
    assert state['reads'] >= 2


def test_fixture_authorization_cannot_certify_an_official_input_set(research):
    """The fixture lane stays separate; it may never admit official research."""
    module, _store, identity, _source, _binding, _input_set_ref, _other, _environment_ref, _state = research
    fixture_identity = dict(identity, issuer_kind='GITHUB')
    with pytest.raises(ValueError):
        module.require_research_inputs_qualified(fixture_identity, source=dict(
            commit_sha='b'*40, tree_sha='c'*40,
            closure_schema_version='trading-agent-source-closure-v1',
            closure_policy_sha256='d'*64, closure_sha256='e'*64),
            policy_digest='f'*64, input_set_ref={'content_sha256': '2'*64, 'size_bytes': 2,
            'media_type': 'application/json', 'locator': '2'*64 + '.blob'},
            environment_ref={'content_sha256': '3'*64, 'size_bytes': 2,
            'media_type': 'application/json', 'locator': '3'*64 + '.blob'}, store=_store)


def test_protected_profile_cannot_qualify_arbitrary_json_as_research(research, monkeypatch):
    from packages.alpha_lifecycle import local_qualification
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    module, store, identity, source, binding, input_ref, _, _, state = research
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
    profile = dict(schema_version='local-research-profile-v1', identity=identity,
                   store_root=str(store._root), binding=binding,
                   commitment_ref=input_ref, backup_receipt_ref=input_ref)
    original = config.read_protected_canonical_json_current
    monkeypatch.setattr(config, 'read_protected_canonical_json_current',
        lambda path: (profile, 'a'*64) if path == module.research_profile_path(identity) else original(path))
    monkeypatch.setattr(local_qualification, 'ProtectedQualificationStore', lambda root: store)
    with pytest.raises(ValueError):
        module.read_local_research_inputs(identity, source=source,
            policy_digest='f'*64, input_set_ref=input_ref)


@pytest.mark.parametrize('fault', [None, 'environment', 'source', 'policy', 'backup_subject',
                                  'backup_identity', 'missing_backup', 'revoke_at_last_proof_read',
                                  'wrong_backup_root', 'overlapping_roots', 'missing_backup_root',
                                  'replaced_backup_root'])
def test_official_compact_admission_binds_proof_without_private_dataset_access(
        research, inputs, monkeypatch, fault):
    import json
    from packages.alpha_lifecycle import local_qualification
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    module, _, identity, _, _, _, _, _, state = research
    store, retain, source, _, _, dataset_ref, backup, commitment = inputs
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
    for value in (backup, commitment):
        assert value['source'] == source

    def local(value, schema):
        result = json.loads(canonical_json_bytes(value))
        for key in ('digest', 'producer_repository', 'producer_workflow_ref', 'producer_run_id', 'producer_attempt'):
            result.pop(key)
        return dict(result, schema_version=schema, identity=identity.model_dump(mode='json'))

    backup = local(backup, 'p3-local-research-backup-receipt-v1')
    if fault == 'backup_subject':
        backup['dataset_content_sha256'] = '0'*64
    if fault == 'backup_identity':
        backup['identity']['attempt'] += 1
    backup_ref = retain(sealed(backup))
    commitment = local(commitment, 'p3-local-research-batch-commitment-v1')
    commitment['backup_receipt_ref'] = backup_ref
    commitment_ref = retain(sealed(commitment))
    environment_ref = retain(sealed(dict(schema_version='p3-environment-identity-v1',
        python_version='3.11.14', root_lock_digest='a'*64, native_manifest_digest='b'*64,
        sandbox_policy_digest='c'*64, platform='linux-x86_64', decimal_precision=50)))
    placeholder = retain({})
    pit_ref = retain(sealed(dict(schema_version='p3-p-i-t-proof-v1', dataset_ref=dataset_ref,
        fold_manifest_ref=placeholder, vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE',
        historical_vintage_verified=False, revision_proof_ref=commitment_ref,
        no_future_suite_ref=placeholder, limitations=['retrospective.current_archive'])))
    input_ref = retain(sealed(dict(schema_version='p3-input-set-v1', source=source,
        epoch_id='synthetic', policy_digest='5'*64, family_digest='6'*64,
        dataset_evidence_ref=dataset_ref, fold_manifest_ref=placeholder, pit_proof_ref=pit_ref,
        environment_ref=environment_ref, regime_threshold_ref=placeholder,
        integration_receipt_ref=placeholder,
        cost_model=dict(fee_bps=0, spread_bps=0, slippage_bps=0, funding_bps=0, borrow_bps=0))))
    binding = dict(gate=GATE, source=source, policy_digest='5'*64, input_ref=input_ref,
                   environment_ref=placeholder if fault == 'environment' else environment_ref)
    source_root = str(store._root)
    backup_root = str(store._root.parent/'private-backup')
    proof = sealed(dict(schema_version='p3-local-research-proof-v1', identity=identity,
        source_store_root=source_root,
        backup_store_root=source_root if fault=='overlapping_roots' else backup_root,
        commitment_ref=commitment_ref, backup_receipt_ref=backup_ref,
        backup_inventory_ref=dict(content_sha256='8'*64, size_bytes=1000000,
                                  media_type='application/json',locator='8'*64+'.blob'),
        verified_at='2026-09-11T12:02:00Z', status='READBACK_VERIFIED', authority=SAFE))
    proof_ref=retain(proof)
    receipt = sealed(dict(schema_version='local-phase-qualification-receipt-v1', identity=identity,
        binding=binding, started_at='2026-09-18T11:00:00Z', completed_at='2026-09-18T11:30:00Z',
        expires_at='2026-09-18T13:00:00Z', evidence_refs=[commitment_ref, backup_ref, proof_ref],
        status='PASS', authority=SAFE))
    state['approval'] = sealed(dict(state['approval'], receipt_ref=retain(receipt)))
    profile = dict(schema_version='local-research-profile-v1', identity=identity,
        store_root=str(store._root), binding=binding, commitment_ref=commitment_ref, backup_receipt_ref=backup_ref,
        proof_ref=proof_ref,source_store_root=source_root,
        backup_store_root=backup_root+'-other' if fault=='wrong_backup_root' else backup_root,
        backup_root_identity=[1,2,3,4])
    root_reads=[]
    def protected_backup(path):
        root_reads.append(path)
        if fault=='missing_backup_root':raise ValueError('backup root missing')
        return (1,9,3,4) if fault=='replaced_backup_root' and len(root_reads)>1 else (1,2,3,4)
    monkeypatch.setattr(module,'protected_backup_identity',protected_backup,raising=False)
    original = config.read_protected_canonical_json_current
    monkeypatch.setattr(config, 'read_protected_canonical_json_current',
        lambda path: (profile, 'a'*64) if path == module.research_profile_path(identity) else original(path))
    reads = []

    class CompactStore:
        def read_bytes(self, ref):
            assert ref != dataset_ref, 'API must not read private research dataset'
            assert ref.size_bytes <= 131072
            reads.append(ref)
            if fault == 'missing_backup' and ref == backup_ref:
                raise ValueError('backup receipt unavailable')
            raw = store.read_bytes(ref)
            if fault == 'revoke_at_last_proof_read' and ref == backup_ref and reads.count(ref) == 2:
                state['approval'] = sealed(dict(state['approval'], revoked=True))
            return raw

    monkeypatch.setattr(local_qualification, 'ProtectedQualificationStore', lambda root: CompactStore())
    kwargs = dict(source=source.model_copy(update={'commit_sha': '9'*40}) if fault == 'source' else source,
                  policy_digest='9'*64 if fault == 'policy' else '5'*64, input_set_ref=input_ref)
    if fault:
        with pytest.raises(ValueError):
            module.read_local_research_inputs(identity, **kwargs)
    else:
        result = module.read_local_research_inputs(identity, **kwargs)
        assert result.binding.input_ref == input_ref
        assert reads.count(backup_ref) >= 3
