"""GITHUB requests: protected path, API-attested run context, operation binding, inputs."""
from datetime import timedelta
import json
import hashlib
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_github_run import (
    AUTHORITY, NOW, SOURCE, Store, _publish_run, _ref, _z, protected, research_inputs, sealed,
)

__all__ = ['protected']
NONCE = '00000000-0000-4000-8000-000000000001'
RUN_DIR = Path('/run/trading-agent-p3/7-1')


def _authorization(**changes):
    value = dict(schema_version='p3-run-authorization-v1', input_set_ref=_ref('2' * 64), review_ref=_ref('8' * 64),
                 operation='BASELINES', allowed_alpha_ids=[], issued_at=_z(NOW - timedelta(minutes=1)),
                 expires_at=_z(NOW + timedelta(minutes=30)), nonce=NONCE, issuer_workflow='p3-authority.yml',
                 issuer_run_id=7, issuer_attempt=1, authority=AUTHORITY)
    value.update(changes)
    return sealed(value)


def _intent(input_set_ref):
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from tests.p3.test_operation_input import intent
    return P3OperationInput.model_validate_json(intent(input_set_ref=input_set_ref)).model_dump(mode='json')


@pytest.fixture
def lane(protected, monkeypatch):
    """A fresh API-attested run 7/1 for p3-baselines-v1 with reviewed OFFICIAL inputs."""
    from packages.alpha_lifecycle import authority
    monkeypatch.setattr(authority, 'datetime', _Clock)
    store = Store()
    inputs = research_inputs(store)
    protected[Path('/etc/trading-agent-p3/github/research-inputs.json')] = inputs
    protected[Path('/etc/trading-agent-p3/github/research-inputs-review.json')] = store.review
    pin = hashlib.sha256(canonical_json_bytes(inputs) + b'\n').hexdigest()

    def publish(**changes):
        _publish_run(protected, research_inputs_sha256=pin, **changes)
    publish()
    return protected, publish


class _Clock:
    """Freeze authority.py's clock at NOW; github_run receives NOW explicitly via defaults."""
    @staticmethod
    def now(tz=None):
        return NOW


@pytest.fixture(autouse=True)
def _frozen_github_clock(monkeypatch):
    from packages.alpha_lifecycle import github_run
    monkeypatch.setattr(github_run, '_now', lambda now: NOW if now is None else now)


def _request(protected, authorization=None, *, operation_input=..., path=None):
    authorization = authorization or _authorization()
    document = dict(schema_version='p3-authority-request-file-v1', execution_source=SOURCE,
                    authorization=authorization)
    if operation_input is ...:
        operation_input = _intent(authorization.get('input_set_ref'))
    if operation_input is not None:
        document['operation_input'] = operation_input
    path = path or RUN_DIR/'operations'/f"{authorization['nonce']}.json"
    protected[path] = document
    return path


def _validate(path, operation='BASELINES'):
    from packages.alpha_lifecycle.authority import validate_request
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    return validate_request(path, SourceIdentity.model_validate(SOURCE), operation)


def test_protected_github_request_is_admitted_from_the_attested_run(lane):
    protected, _ = lane
    request = _validate(_request(protected))
    assert request.authorization.issuer_run_id == 7


@pytest.mark.parametrize('fault', ['other_run_dir', 'other_nonce_path', 'stale_run', 'other_head', 'wrong_operation',
                                   'workflow', 'revoked', 'expired', 'inputs', 'fake_official'])
def test_github_request_faults_are_held(lane, fault):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    protected, publish = lane
    authorization, path = _authorization(), None
    if fault == 'other_run_dir':
        path = Path('/run/trading-agent-p3/8-1/operations')/f'{NONCE}.json'
    elif fault == 'other_nonce_path':
        path = RUN_DIR/'operations'/'00000000-0000-4000-8000-000000000002.json'
    elif fault == 'stale_run':
        publish(observed_at=_z(NOW - timedelta(minutes=5)))
    elif fault == 'other_head':
        publish(head_sha='0' * 40)
    elif fault == 'wrong_operation':
        publish(operation='p3-oos-a0-v1')
    elif fault == 'workflow':
        authorization = _authorization(issuer_workflow='foundation.yml')
    elif fault == 'revoked':
        protected.pop(RUN_DIR/'github-run.json')
    elif fault == 'expired':
        authorization = _authorization(expires_at=_z(NOW - timedelta(seconds=1)))
    elif fault == 'inputs':
        authorization = _authorization(input_set_ref=_ref('9' * 64))
    elif fault == 'fake_official':
        publish(mode='FAKE')
    with pytest.raises(AuthorityHeld):
        _validate(_request(protected, authorization, path=path))


def test_forged_environment_without_attestation_is_held(lane, monkeypatch, tmp_path):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    protected, _ = lane
    protected.pop(RUN_DIR/'github-run.json')
    for key, value in dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY='nam176hermes/Trading-Agent',
            GITHUB_REF='refs/heads/main', GITHUB_SHA=SOURCE['commit_sha'], GITHUB_REF_PROTECTED='true',
            GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_RUN_ID='7', GITHUB_RUN_ATTEMPT='1',
            GITHUB_WORKFLOW_REF='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main').items():
        monkeypatch.setenv(key, value)
    with pytest.raises(AuthorityHeld):
        _validate(_request(protected))
    # The legacy caller-owned 0600 request path is held for the same reason.
    legacy = tmp_path/'request.json'
    document = dict(schema_version='p3-authority-request-file-v1', execution_source=SOURCE,
                    authorization=_authorization(), operation_input=_intent(_ref('2' * 64)))
    legacy.write_bytes(canonical_json_bytes(document))
    legacy.chmod(0o600)
    with pytest.raises(AuthorityHeld):
        _validate(legacy.resolve())


def test_fixture_request_needs_no_research_inputs(lane):
    protected, publish = lane
    publish(operation='p3-integration-fixture-v1')
    fixture = {k: v for k, v in _authorization(operation='PARITY').items() if k not in ('input_set_ref', 'allowed_alpha_ids', 'digest')}
    fixture.update(schema_version='p3-fixture-authorization-v1', fixture_plan_ref=_ref('a' * 64))
    request = _validate(_request(protected, sealed(fixture), operation_input=None), 'PARITY')
    assert request.operation_input is None


def _issuer(operation, protected, session=False):
    from packages.alpha_lifecycle.authority import validate_operation_issuer
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    if session:
        protected[RUN_DIR/'session.json'] = dict(schema_version='p3-holdout-session-v1', source=SOURCE,
                                                 workflow_run_id=7, workflow_attempt=1)
    authorization = RunAuthorization.model_validate_json(canonical_json_bytes(_authorization(operation=operation)))
    workflow = {'PARITY': 'p3-native-parity-v1', 'PHASE_EXIT': 'p3-phase-exit-v1'}[operation]
    return validate_operation_issuer(authorization, SourceIdentity.model_validate(SOURCE), workflow_operation=workflow,
                                     admission=False)


@pytest.mark.parametrize('operation', ['PARITY', 'PHASE_EXIT'])
def test_session_stages_are_admitted_only_inside_the_holdout_session(lane, operation):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    protected, publish = lane
    with pytest.raises(AuthorityHeld):
        _issuer(operation, protected)  # baselines run
    publish(operation='p3-holdout-primary-v1')
    with pytest.raises(AuthorityHeld):
        _issuer(operation, protected)  # no protected session yet
    _issuer(operation, protected, session=True)
    protected[RUN_DIR/'session.json'] = dict(protected[RUN_DIR/'session.json'], workflow_run_id=8)
    with pytest.raises(AuthorityHeld):
        _issuer(operation, protected)


def test_no_production_caller_passes_environment_context():
    """R-03: GITHUB authenticity never comes from a caller-supplied environment."""
    import ast
    root = Path(__file__).resolve().parents[2]
    names = {'read_official_profile', 'validate_operation_issuer', '_validate_workflow_context'}
    offenders = []
    for top in ('packages', 'services', 'scripts', 'apps'):
        for path in (root/top).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Call) and getattr(node.func, 'attr', getattr(node.func, 'id', None)) in names
                        and any(keyword.arg == 'context' for keyword in node.keywords)):
                    offenders.append(f'{path.relative_to(root)}:{node.lineno}')
    assert offenders == []


def _fixture_request(protected):
    fixture = {k: v for k, v in _authorization(operation='PARITY').items() if k not in ('input_set_ref', 'allowed_alpha_ids', 'digest')}
    fixture.update(schema_version='p3-fixture-authorization-v1', fixture_plan_ref=_ref('a' * 64))
    return _request(protected, sealed(fixture), operation_input=None)


def test_fake_run_is_held_without_a_pinned_rehearsal_receipt_even_without_inputs(lane):
    """PR77-01: a recorded-API run must never admit anything an OFFICIAL campaign could cite."""
    from packages.alpha_lifecycle.authority import AuthorityHeld
    protected, publish = lane
    publish(operation='p3-integration-fixture-v1', mode='FAKE')
    with pytest.raises(AuthorityHeld):
        _validate(_fixture_request(protected), 'PARITY')
    publish(operation='p3-holdout-primary-v1', mode='FAKE')
    with pytest.raises(AuthorityHeld):
        _issuer('PARITY', protected, session=True)


def test_fake_run_with_a_pinned_rehearsal_receipt_admits_the_fixture(lane):
    protected, publish = lane
    store = Store()
    inputs = research_inputs(store, campaign='REHEARSAL')
    protected[Path('/etc/trading-agent-p3/github/research-inputs.json')] = inputs
    protected[Path('/etc/trading-agent-p3/github/research-inputs-review.json')] = store.review
    pin = hashlib.sha256(canonical_json_bytes(inputs) + b'\n').hexdigest()
    _publish_run(protected, operation='p3-integration-fixture-v1', mode='FAKE', research_inputs_sha256=pin)
    assert _validate(_fixture_request(protected), 'PARITY').operation_input is None


@pytest.mark.parametrize('attested,requested', [
    ('p3-oos-a0-v1', 'p3-oos-a1-v1'), ('p3-oos-a1-v1', 'p3-select-primary-v1'), ('p3-oos-a3-v1', 'p3-oos-a3-v1'),
])
def test_run_admits_only_its_exact_workflow_operation(lane, attested, requested):
    """PR77-02: one OOS dispatch can never admit another arm or the selection."""
    from packages.alpha_lifecycle.authority import AuthorityHeld, _validate_workflow_context
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    protected, publish = lane
    publish(operation=attested)
    authorization = RunAuthorization.model_validate_json(canonical_json_bytes(_authorization(operation='OOS')))
    source = SourceIdentity.model_validate(SOURCE)
    if attested == requested:
        assert _validate_workflow_context(authorization, source, workflow_operation=requested).operation == attested
        return
    with pytest.raises(AuthorityHeld):
        _validate_workflow_context(authorization, source, workflow_operation=requested)


@pytest.mark.parametrize('attested', ['p3-integration-fixture-v1', 'p3-native-parity-v1', 'p3-phase-exit-v1'])
def test_parity_and_exit_are_never_admitted_outside_the_holdout_session(lane, attested):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    protected, publish = lane
    publish(operation=attested)
    with pytest.raises(AuthorityHeld):
        _issuer('PARITY', protected, session=True)


def test_fixture_request_requires_a_fixture_dispatch(lane):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    protected, publish = lane
    publish(operation='p3-native-parity-v1')
    with pytest.raises(AuthorityHeld):
        _validate(_fixture_request(protected), 'PARITY')
