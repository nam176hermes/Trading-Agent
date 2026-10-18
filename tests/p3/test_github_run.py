"""GITHUB-lane trust: API-authenticated run attestation and reviewed research inputs."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
HEAD = 'a' * 40
HOST = ('b' * 64, UUID('00000000-0000-4000-8000-000000000009'))
SOURCE = dict(commit_sha=HEAD, tree_sha='c' * 40, closure_schema_version='pre-p3-source-closure-v1',
              closure_policy_sha256='d' * 64, closure_sha256='e' * 64)
AUTHORITY = dict(broker=False, live=False, network=False, production=False)


def _ref(digest):
    return dict(content_sha256=digest, size_bytes=1, media_type='application/json', locator=f'{digest}.blob')


def _z(value):
    return value.isoformat().replace('+00:00', 'Z')


def sealed(value):
    value = {k: v for k, v in value.items() if k != 'digest'}
    return {**value, 'digest': hashlib.sha256(canonical_json_bytes(value)).hexdigest()}


def api_documents(**changes):
    run = dict(id=7, run_attempt=1, path='.github/workflows/p3-authority.yml', event='workflow_dispatch',
               head_branch='main', head_sha=HEAD, status='in_progress', actor=dict(login='nam176hermes'),
               triggering_actor=dict(login='nam176hermes'), display_title='p3 p3-baselines-v1',
               repository=dict(full_name='nam176hermes/Trading-Agent'))
    job = dict(name='p3', status='in_progress', run_id=7, run_attempt=1, runner_name='p3-jit-7-1',
               labels=['self-hosted', 'p3-authority', 'p3-run-7-1'])
    jobs = dict(total_count=1, jobs=[job])
    branch = dict(name='main', protected=True)
    for key, value in changes.items():
        kind, _, field = key.partition('__')
        target = dict(run=run, job=job, jobs=jobs, branch=branch)[kind]
        if value is ...:
            target.pop(field)
        else:
            target[field] = value
    return {'/repos/nam176hermes/Trading-Agent/actions/runs/7': run,
            '/repos/nam176hermes/Trading-Agent/actions/runs/7/attempts/1/jobs': jobs,
            '/repos/nam176hermes/Trading-Agent/branches/main': branch}


def expected(**changes):
    from packages.alpha_lifecycle.github_run import ExpectedRun
    return ExpectedRun.model_validate(dict(dict(head_sha=HEAD, actor='nam176hermes', operation='p3-baselines-v1',
        runner_name='p3-jit-7-1', research_inputs_sha256='f' * 64), **changes))


def authenticate(documents=None, **options):
    from packages.alpha_lifecycle.github_run import authenticate_run
    documents = api_documents() if documents is None else documents
    return authenticate_run(lambda path: json.dumps(documents[path]).encode(), run_id=7, attempt=1,
        expected=options.pop('expected', expected()), mode=options.pop('mode', 'GITHUB'), host=HOST, now=NOW)


def test_run_directory_is_the_single_validated_derivation():
    from packages.alpha_lifecycle.github_run import GithubRunHeld, github_run_directory
    assert github_run_directory(7, 1) == Path('/run/trading-agent-p3/7-1')
    for bad in ((0, 1), (7, 0), ('7', 1), (True, 1), (7, -1)):
        with pytest.raises(GithubRunHeld):
            github_run_directory(*bad)


def test_authenticated_run_binds_identity_runner_and_input_pin():
    run = authenticate()
    assert (run.identity.run_id, run.identity.attempt, run.mode) == (7, 1, 'GITHUB')
    assert run.runner_name == 'p3-jit-7-1' and run.research_inputs_sha256 == 'f' * 64
    assert run.host_id == HOST[0] and run.observed_at == NOW


@pytest.mark.parametrize('change', [
    dict(run__path='.github/workflows/foundation.yml'), dict(run__event='push'), dict(run__head_branch='feature'),
    dict(run__head_sha='0' * 40), dict(run__status='queued'), dict(run__status='completed'),
    dict(run__run_attempt=2), dict(run__actor=dict(login='someone')), dict(run__triggering_actor=dict(login='x')),
    dict(run__display_title='p3 p3-holdout-primary-v1'), dict(run__repository=dict(full_name='fork/Trading-Agent')),
    dict(run__id=8), dict(job__runner_name='other'), dict(job__labels=['self-hosted', 'p3-authority']),
    dict(job__status='queued'), dict(job__run_attempt=2), dict(jobs__total_count=2),
    dict(branch__protected=False), dict(run__actor=...),
])
def test_any_unexpected_api_value_is_held(change):
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    with pytest.raises(GithubRunHeld):
        authenticate(api_documents(**change))


def test_bound_runner_taking_another_job_is_held():
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    documents = api_documents()
    jobs = documents['/repos/nam176hermes/Trading-Agent/actions/runs/7/attempts/1/jobs']
    jobs['jobs'].append(dict(name='intruder', status='completed', runner_name='p3-jit-7-1'))
    jobs['total_count'] = 2
    with pytest.raises(GithubRunHeld):
        authenticate(documents)


def test_oversized_or_non_object_response_is_held():
    from packages.alpha_lifecycle.github_run import GithubRunHeld, authenticate_run
    for body in (b'x' * 1_048_577, b'[]', b'not json'):
        with pytest.raises(GithubRunHeld):
            authenticate_run(lambda path: body, run_id=7, attempt=1, expected=expected(), mode='GITHUB',
                             host=HOST, now=NOW)


@pytest.fixture
def protected(monkeypatch):
    """Serve canonical documents as if root-protected; anything else is unavailable."""
    from packages.alpha_lifecycle import local_qualification
    from packages.runtime_release import config
    documents = {}

    def read(path):
        if path not in documents:
            raise FileNotFoundError(path)
        return canonical_json_bytes(documents[path]) + b'\n', (1, 2)
    monkeypatch.setattr(config, '_read_protected_file', read)
    monkeypatch.setattr(local_qualification, '_host_identity', lambda: HOST)
    return documents


def _publish_run(documents, **changes):
    run = authenticate().model_dump(mode='json')
    run.update(changes)
    documents[Path('/run/trading-agent-p3/7-1/github-run.json')] = sealed(run)


def test_read_run_requires_fresh_observation_only_for_admission(protected):
    from packages.alpha_lifecycle.github_run import GithubRunHeld, read_github_run
    _publish_run(protected)
    run, digest = read_github_run(7, 1, admission=True, now=NOW + timedelta(seconds=60))
    assert run.identity.run_id == 7 and len(digest) == 64
    later = NOW + timedelta(minutes=10)
    assert read_github_run(7, 1, admission=False, now=later)[0] == run
    with pytest.raises(GithubRunHeld, match='stale'):
        read_github_run(7, 1, admission=True, now=later)


def test_read_run_rejects_other_run_host_future_and_revocation(protected, monkeypatch):
    from packages.alpha_lifecycle import local_qualification
    from packages.alpha_lifecycle.github_run import GithubRunHeld, read_github_run
    _publish_run(protected)
    protected[Path('/run/trading-agent-p3/8-1/github-run.json')] = protected[Path('/run/trading-agent-p3/7-1/github-run.json')]
    with pytest.raises(GithubRunHeld):
        read_github_run(8, 1, admission=False, now=NOW)
    with pytest.raises(GithubRunHeld, match='future'):
        read_github_run(7, 1, admission=False, now=NOW - timedelta(minutes=1))
    monkeypatch.setattr(local_qualification, '_host_identity', lambda: ('0' * 64, HOST[1]))
    with pytest.raises(GithubRunHeld, match='host'):
        read_github_run(7, 1, admission=False, now=NOW)
    monkeypatch.setattr(local_qualification, '_host_identity', lambda: HOST)
    protected.clear()
    with pytest.raises(GithubRunHeld):
        read_github_run(7, 1, admission=False, now=NOW)


def test_read_run_ignores_forged_environment(protected, monkeypatch):
    from packages.alpha_lifecycle.github_run import GithubRunHeld, read_github_run
    for key, value in dict(GITHUB_ACTIONS='true', GITHUB_RUN_ID='7', GITHUB_RUN_ATTEMPT='1',
                           GITHUB_REPOSITORY='nam176hermes/Trading-Agent').items():
        monkeypatch.setenv(key, value)
    with pytest.raises(GithubRunHeld):
        read_github_run(7, 1, admission=False, now=NOW)


class Store:
    def __init__(self):
        self.blobs = {}

    def put(self, value):
        raw = canonical_json_bytes(value)
        digest = hashlib.sha256(raw).hexdigest()
        self.blobs[digest] = raw
        return dict(content_sha256=digest, size_bytes=len(raw), media_type='application/json',
                    locator=f'{digest}.blob')

    def read_bytes(self, ref):
        return self.blobs[ref.content_sha256]


def research_inputs(store, *, campaign='OFFICIAL', review_changes=None, subjects=None, **changes):
    from packages.alpha_lifecycle.github_run import GithubResearchInputs
    body = dict(schema_version='p3-github-research-inputs-v1', campaign=campaign, source=SOURCE,
                policy_digest='1' * 64, input_ref=_ref('2' * 64), environment_ref=_ref('3' * 64),
                integration_receipt_ref=_ref('4' * 64), review_ref=_ref('5' * 64),
                issued_at=_z(NOW - timedelta(minutes=5)), expires_at=_z(NOW + timedelta(hours=6)),
                authority=AUTHORITY)
    body.update(changes)
    binding = GithubResearchInputs.model_validate(sealed(body)).binding_digest
    review = dict(schema_version='p3-review-approval-v1', source=SOURCE,
                  subject_digests=subjects if subjects is not None else ['2' * 64, binding],
                  operator_identity='nam176hermes', reviewer_identity='claude-agent-reviewer',
                  review_execution_id='review-1', verdict='APPROVED',
                  issued_at=_z(NOW - timedelta(minutes=10)), expires_at=_z(NOW + timedelta(hours=8)),
                  evidence_ref=_ref('6' * 64), authority=AUTHORITY)
    review.update(review_changes or {})
    store.review = sealed(review)
    body['review_ref'] = store.put(store.review)
    return sealed(body)


def _admit(protected, store, inputs, *, mode='GITHUB', pin=None, input_ref=None, now=NOW):
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.alpha_lifecycle.github_run import read_github_research_inputs, read_github_run
    from packages.data_contracts import ArtifactRefV1
    protected[Path('/etc/trading-agent-p3/github/research-inputs.json')] = inputs
    protected[Path('/etc/trading-agent-p3/github/research-inputs-review.json')] = store.review
    digest = hashlib.sha256(canonical_json_bytes(inputs) + b'\n').hexdigest()
    _publish_run(protected, mode=mode, research_inputs_sha256=pin or digest)
    run, _ = read_github_run(7, 1, admission=True, now=now)
    return read_github_research_inputs(run, source=SourceIdentity.model_validate(SOURCE),
        input_set_ref=ArtifactRefV1.model_validate(input_ref or _ref('2' * 64)), now=now)


def test_reviewed_official_inputs_are_admitted(protected):
    store = Store()
    admitted = _admit(protected, store, research_inputs(store))
    assert admitted.campaign == 'OFFICIAL'


def test_fake_run_admits_only_rehearsal_inputs(protected):
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    store = Store()
    assert _admit(protected, store, research_inputs(store, campaign='REHEARSAL'), mode='FAKE').campaign == 'REHEARSAL'
    with pytest.raises(GithubRunHeld, match='REHEARSAL'):
        _admit(protected, store, research_inputs(store), mode='FAKE')


@pytest.mark.parametrize('fault', ['pin', 'input', 'expired', 'same_identity', 'rejected', 'missing_binding',
                                   'missing_input', 'review_short', 'review_late', 'other_source', 'swapped_review'])
def test_unreviewed_or_mismatched_inputs_are_held(protected, fault):
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    store = Store()
    options, admit = {}, {}
    if fault == 'pin':
        admit['pin'] = '0' * 64
    elif fault == 'input':
        admit['input_ref'] = _ref('9' * 64)
    elif fault == 'expired':
        options['expires_at'] = _z(NOW - timedelta(seconds=1))
    elif fault == 'same_identity':
        options['review_changes'] = dict(reviewer_identity='nam176hermes')
    elif fault == 'rejected':
        options['review_changes'] = dict(verdict='REJECTED')
    elif fault == 'missing_binding':
        options['subjects'] = ['2' * 64]
    elif fault == 'missing_input':
        options['subjects'] = ['7' * 64]
    elif fault == 'review_short':
        options['review_changes'] = dict(expires_at=_z(NOW + timedelta(hours=1)))
    elif fault == 'review_late':
        options['review_changes'] = dict(issued_at=_z(NOW))
    elif fault == 'other_source':
        options['review_changes'] = dict(source={**SOURCE, 'commit_sha': '0' * 40})
    inputs = research_inputs(store, **options)
    if fault == 'swapped_review':
        store.review = sealed({**store.review, 'review_execution_id': 'review-2'})
    with pytest.raises(GithubRunHeld):
        _admit(protected, store, inputs, **admit)
