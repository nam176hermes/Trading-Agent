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
