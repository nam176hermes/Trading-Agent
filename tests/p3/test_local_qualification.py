"""Local qualification identity; protected custody is injected, never installed."""
from datetime import UTC, datetime
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from uuid import UUID

import pytest

from packages.alpha_lifecycle.contracts import authority as contracts
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.runtime_release import config


NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
HOST = 'a' * 64
BOOT = UUID('00000000-0000-4000-8000-000000000001')
SAFE = dict(broker=False, live=False, network=False, production=False)


def sealed(value):
    value = {k: v for k, v in value.items() if k != 'digest'}
    return {**value, 'digest': hashlib.sha256(canonical_json_bytes(value)).hexdigest()}


@pytest.fixture
def local(tmp_path, monkeypatch):
    assert importlib.util.find_spec('packages.alpha_lifecycle.local_qualification'), 'local qualification validator is missing'
    module = importlib.import_module('packages.alpha_lifecycle.local_qualification')
    root = tmp_path / 'cas'; root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json').model_dump(mode='json')
    binding = dict(
        gate='P2_SOURCE_COMPLETE', source=dict(commit_sha='b'*40, tree_sha='c'*40,
        closure_schema_version='trading-agent-source-closure-v1', closure_policy_sha256='d'*64,
        closure_sha256='e'*64), policy_digest='f'*64,
        input_ref=retain({'input': 'frozen'}), environment_ref=retain({'environment': 'observed'}))
    identity = dict(issuer_kind='LOCAL', issuer_identity='operator-a',
        run_id='00000000-0000-4000-8000-000000000002', attempt=1, host_id=HOST, boot_id=str(BOOT))
    receipt = sealed(dict(schema_version='local-phase-qualification-receipt-v1', identity=identity,
        binding=binding, started_at='2026-09-18T11:00:00Z', completed_at='2026-09-18T11:30:00Z',
        expires_at='2026-09-18T13:00:00Z', evidence_refs=[retain({'observed': 'test output'})],
        status='PASS', authority=SAFE))
    approval = sealed(dict(schema_version='local-qualification-approval-v1', issuer_identity='operator-a',
        receipt_ref=retain(receipt), issued_at='2026-09-18T11:45:00Z', expires_at='2026-09-18T13:00:00Z',
        revoked=False, authority=SAFE))
    # Substitute filesystem custody only; canonical JSON parser and digest readback remain real.
    def protected_bytes(path):
        assert path == Path('/etc/trading-agent/local-qualification/operator-a/P2_SOURCE_COMPLETE.json')
        return canonical_json_bytes(approval) + b'\n', (1, 2)
    monkeypatch.setattr(config, '_read_protected_file', protected_bytes)
    monkeypatch.setattr(module, '_host_identity', lambda: (HOST, BOOT))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(module, 'datetime', Clock)
    for key in tuple(__import__('os').environ):
        if key.startswith('GITHUB_'):
            monkeypatch.delenv(key)
    expected_identity = contracts.LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
    expected_binding = contracts.QualificationBinding.model_validate_json(canonical_json_bytes(binding))
    def check():
        return module.validate_local_qualification(expected_identity, expected_binding, store=store)
    def approve_receipt():
        receipt.update(sealed(receipt))
        approval['receipt_ref'] = retain(receipt)
        approval.update(sealed(approval))
    return module, store, root, identity, binding, receipt, approval, check, approve_receipt


def test_current_local_evidence_authenticates_without_github_context(request):
    _, _, _, _, _, _, _, check, _ = request.getfixturevalue("local")
    result = check()
    assert result.identity.issuer_kind == 'LOCAL'
    assert result.binding.gate == 'P2_SOURCE_COMPLETE'
    assert result.identity.attempt == 1
    assert result.authority.model_dump() == SAFE


@pytest.mark.parametrize('fault', ['commit', 'tree', 'closure', 'closure_policy', 'policy', 'input',
    'environment', 'issuer', 'run', 'attempt', 'host', 'boot', 'gate'])
def test_root_approved_receipt_still_must_match_expected_context(local, fault):
    _, _, _, _, _, receipt, _, check, approve = local
    if fault in {'commit', 'tree', 'closure', 'closure_policy'}:
        key = {'commit': 'commit_sha', 'tree': 'tree_sha', 'closure': 'closure_sha256',
               'closure_policy': 'closure_policy_sha256'}[fault]
        receipt['binding']['source'][key] = '9' * (40 if fault in {'commit', 'tree'} else 64)
    elif fault == 'policy': receipt['binding']['policy_digest'] = '9'*64
    elif fault in {'input', 'environment'}:
        receipt['binding'][fault+'_ref'] = receipt['evidence_refs'][0]
    elif fault == 'issuer': receipt['identity']['issuer_identity'] = 'operator-b'
    elif fault == 'run': receipt['identity']['run_id'] = str(BOOT)
    elif fault == 'attempt': receipt['identity']['attempt'] = 2
    elif fault == 'host': receipt['identity']['host_id'] = '9'*64
    elif fault == 'boot': receipt['identity']['boot_id'] = receipt['identity']['run_id']
    elif fault == 'gate': receipt['binding']['gate'] = 'P2_RUNTIME_QUALIFIED'
    approve()
    with pytest.raises(ValueError, match='binding|identity'):
        check()


@pytest.mark.parametrize('fault', ['expired', 'not_yet_valid', 'revoked', 'issuer', 'bad_digest',
    'approval_outlives_receipt', 'approval_before_completion'])
def test_approval_is_current_bounded_and_authentic(local, fault):
    _, _, _, _, _, _, approval, check, _ = local
    if fault == 'expired': approval['expires_at'] = '2026-09-18T12:00:00Z'
    elif fault == 'not_yet_valid': approval['issued_at'] = '2026-09-18T12:01:00Z'
    elif fault == 'revoked': approval['revoked'] = True
    elif fault == 'issuer': approval['issuer_identity'] = 'operator-b'
    elif fault == 'approval_outlives_receipt': approval['expires_at'] = '2026-09-18T13:01:00Z'
    elif fault == 'approval_before_completion': approval['issued_at'] = '2026-09-18T11:29:00Z'
    approval.update(sealed(approval))
    if fault == 'bad_digest': approval['digest'] = '0'*64
    with pytest.raises(ValueError): check()


@pytest.mark.parametrize('fault', ['expired', 'future_completion', 'inverted', 'too_long', 'empty_evidence',
    'duplicate_evidence', 'unsafe_authority', 'unknown_gate', 'failed', 'forged_digest'])
def test_receipt_cannot_expand_time_scope_or_manufacture_success(local, fault):
    _, store, _, _, _, receipt, approval, check, approve = local
    if fault == 'expired': receipt['expires_at'] = '2026-09-18T12:00:00Z'
    elif fault == 'future_completion': receipt['completed_at'] = '2026-09-18T12:01:00Z'
    elif fault == 'inverted': receipt['completed_at'] = '2026-09-18T10:00:00Z'
    elif fault == 'too_long': receipt['expires_at'] = '2026-09-19T11:00:01Z'
    elif fault == 'empty_evidence': receipt['evidence_refs'] = []
    elif fault == 'duplicate_evidence': receipt['evidence_refs'] *= 2
    elif fault == 'unsafe_authority': receipt['authority'] = {**SAFE, 'live': True}
    elif fault == 'unknown_gate': receipt['binding']['gate'] = 'HOLDOUT'
    elif fault == 'failed': receipt['status'] = 'FAIL'
    approve()
    if fault == 'forged_digest':
        receipt['digest'] = '0'*64
        approval['receipt_ref'] = store.put_bytes(canonical_json_bytes(receipt),media_type='application/json').model_dump(mode='json')
        approval.update(sealed(approval))
    with pytest.raises(ValueError): check()


@pytest.mark.parametrize('fault', ['input', 'environment', 'evidence', 'receipt'])
def test_retained_bytes_must_exist_and_match_their_digest(local, fault):
    _, _, root, _, binding, receipt, approval, check, _ = local
    ref = {'input': binding['input_ref'], 'environment': binding['environment_ref'],
           'evidence': receipt['evidence_refs'][0], 'receipt': approval['receipt_ref']}[fault]
    (root/ref['locator']).write_bytes(b'forged')
    with pytest.raises(ValueError): check()


def test_github_identity_is_distinct_and_never_accepted_as_local(local):
    _, _, _, _, _, receipt, _, check, approve = local
    receipt['identity'] = dict(issuer_kind='GITHUB', issuer_identity='operator-a',
        repository='nam176hermes/Trading-Agent', workflow_ref='p3-authority.yml', run_id=1, attempt=1)
    approve()
    parsed = contracts.QualificationReceipt.model_validate_json(canonical_json_bytes(receipt))
    assert parsed.identity.issuer_kind == 'GITHUB'
    with pytest.raises(ValueError, match='identity'): check()
    receipt['identity']['issuer_kind'] = 'LOCAL'
    with pytest.raises(ValueError):
        contracts.QualificationReceipt.model_validate_json(canonical_json_bytes(sealed(receipt)))


@pytest.mark.parametrize('fault', ['revoked', 'replaced_attempt', 'host_changed', 'clock_backwards', 'expires_during_read'])
def test_revalidation_cannot_reuse_a_stale_approval(local, monkeypatch, fault):
    module, store, _, _, _, _, approval, check, _ = local
    read = store.read_bytes
    def changed(ref):
        raw = read(ref)
        if fault == 'revoked':
            approval['revoked'] = True
            approval.update(sealed(approval))
        elif fault == 'replaced_attempt':
            approval['receipt_ref'] = {**approval['receipt_ref'], 'content_sha256': '9'*64, 'locator': '9'*64+'.blob'}
            approval.update(sealed(approval))
        elif fault == 'host_changed': monkeypatch.setattr(module, '_host_identity', lambda: ('9'*64, BOOT))
        else:
            next_time = datetime(2026, 9, 18, 11 if fault == 'clock_backwards' else 13, tzinfo=UTC)
            class Later(datetime):
                @classmethod
                def now(cls, tz=None): return next_time
            monkeypatch.setattr(module, 'datetime', Later)
        return raw
    monkeypatch.setattr(store, 'read_bytes', changed)
    with pytest.raises(ValueError): check()


@pytest.mark.parametrize('host', [('9'*64, BOOT), (HOST, UUID('00000000-0000-4000-8000-000000000003'))])
def test_local_host_observation_is_not_taken_from_the_receipt(local, monkeypatch, host):
    module, _, _, _, _, _, _, check, _ = local
    monkeypatch.setattr(module, '_host_identity', lambda: host)
    with pytest.raises(ValueError, match='host'): check()


def test_self_issued_file_is_rejected_by_the_real_protected_reader(local, tmp_path, monkeypatch):
    module, _, _, _, _, _, approval, check, _ = local
    monkeypatch.undo()
    # Only route the fixed root to test-owned storage; use the real custody validator.
    monkeypatch.setattr(module, '_APPROVAL_ROOT', tmp_path)
    path = tmp_path/'operator-a'/'P2_SOURCE_COMPLETE.json'
    path.parent.mkdir(); path.write_bytes(canonical_json_bytes(approval)+b'\n'); path.chmod(0o444)
    with pytest.raises(ValueError, match='protected'): check()


def test_legacy_github_authorization_canonical_bytes_are_unchanged():
    from tests.p3.test_authority import _request
    value = contracts.RunAuthorization.model_validate_json(json.dumps(_request('2026-01-01T01:00:00Z')[0]['authorization']))
    assert hashlib.sha256(canonical_json_bytes(value)).hexdigest() == 'a04dd358600170b2b82edfacf5b7660a79bebedcc3a519a5e911cd7e7c9d442e'


def test_receipt_bytes_cannot_be_swapped_between_integrity_check_and_parse(local, monkeypatch):
    _, store, _, _, _, receipt, approval, check, _ = local
    fake_ref = store.put_bytes(b'fabricated output', media_type='text/plain')
    fake = sealed({**receipt, 'evidence_refs': [fake_ref.model_dump(mode='json')]})
    read = store.read_bytes
    reads = []
    def swapped(ref):
        if ref.content_sha256 == approval['receipt_ref']['content_sha256']:
            reads.append(ref)
            if len(reads) > 1:
                return canonical_json_bytes(fake)
        return read(ref)
    monkeypatch.setattr(store, 'read_bytes', swapped)
    # Accepting the original verified bytes is valid; accepting the swap is not.
    result = check()
    assert result.evidence_refs[0].content_sha256 == receipt['evidence_refs'][0]['content_sha256']


@pytest.mark.parametrize('machine,boot', [
    (b'0'*32, str(BOOT)), (b'g'*32, str(BOOT)), (b'a'*130, str(BOOT)),
    (b'a'*32, 'not-a-uuid'), (b'a'*32, '00000000-0000-0000-0000-000000000000'),
])
def test_invalid_os_identity_cannot_authenticate_a_host(local, monkeypatch, machine, boot):
    module, _, _, _, _, _, _, _, _ = local
    monkeypatch.undo()
    from io import BytesIO
    original = Path.open
    def opened(path, *args, **kwargs):
        if path == Path('/etc/machine-id'): return BytesIO(machine+b'\n')
        if path == Path('/proc/sys/kernel/random/boot_id'): return BytesIO(boot.encode()+b'\n')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', opened)
    with pytest.raises(ValueError): module._host_identity()


def test_local_run_requires_a_fresh_uuid4_not_a_nil_sentinel(local):
    _, _, _, identity, _, _, _, _, _ = local
    with pytest.raises(ValueError):
        contracts.LocalRunIdentity.model_validate_json(canonical_json_bytes({
            **identity, 'run_id': '00000000-0000-0000-0000-000000000000'}))


def test_host_identity_uses_os_files_not_environment(local, monkeypatch):
    module, _, _, _, _, _, _, _, _ = local
    monkeypatch.undo()
    from io import BytesIO
    original = Path.open
    def opened(path, *args, **kwargs):
        if path == Path('/etc/machine-id'): return BytesIO(b'a'*32+b'\n')
        if path == Path('/proc/sys/kernel/random/boot_id'): return BytesIO(str(BOOT).encode()+b'\n')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setenv('HOSTNAME', 'untrusted-host')
    monkeypatch.setenv('GITHUB_RUN_ID', '1234')
    assert module._host_identity() == ('3ba3f5f43b92602683c19aee62a20342b084dd5971ddd33808d81a328879a547', BOOT)


def test_local_operation_and_integration_contracts_keep_uuid_identity_and_legacy_bytes():
    from tests.p3.test_authority import _request
    assert hasattr(contracts,'LocalRunAuthorization'), 'typed local operation authority is missing'
    request,_=_request('2026-01-01T01:00:00Z')
    legacy=contracts.RunAuthorization.model_validate_json(canonical_json_bytes(request['authorization']))
    raw=canonical_json_bytes(legacy)
    value=legacy.model_dump(mode='json')
    for key in ('issuer_workflow','issuer_run_id','issuer_attempt'): value.pop(key)
    identity=dict(issuer_kind='LOCAL',issuer_identity='operator-a',
        run_id='00000000-0000-4000-8000-000000000002',attempt=1,host_id=HOST,boot_id=str(BOOT))
    value.update(schema_version='p3-local-run-authorization-v1',identity=identity)
    local=contracts.RUN_AUTHORIZATION.validate_json(canonical_json_bytes(sealed(value)))
    assert isinstance(local,contracts.LocalRunAuthorization)
    assert local.identity.run_id.version==4 and not hasattr(local,'issuer_run_id')
    assert canonical_json_bytes(contracts.RUN_AUTHORIZATION.validate_json(raw))==raw
    with pytest.raises(ValueError):
        contracts.RUN_AUTHORIZATION.validate_json(canonical_json_bytes(sealed({**value,'issuer_run_id':1})))
    with pytest.raises(ValueError):
        contracts.RunAuthorization.model_validate(local)
    integration=sealed(dict(schema_version='p3-local-integration-qualified-v1',source=request['execution_source'],
        identity=identity,sql_proof_ref=value['review_ref'],native_fixture_proof_ref=value['review_ref'],
        cleanup_proof_ref=value['review_ref'],status='PASS',authority=SAFE))
    assert contracts.INTEGRATION_RECEIPT.validate_json(canonical_json_bytes(integration)).identity==local.identity
    with pytest.raises(ValueError):
        contracts.IntegrationReceipt.model_validate(integration)
