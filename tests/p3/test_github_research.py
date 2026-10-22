"""GITHUB research batch: reviewed pre-run request, root registry record and OFFICIAL v2 admission.

Synthetic bindings only. Protected files are served by the same `_read_protected_file` seam the
other GITHUB lane tests use; custody identity and the root evidence store are replaced at their
two named seams. Nothing here grants authority.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_github_run import (
    AUTHORITY, NOW, SOURCE, Store, _publish_run, _ref, _z, protected, research_inputs, sealed,
)

__all__ = ['protected']
RUN, ATTEMPT = 41, 1
ROOT = Path(f'/srv/trading-agent-p3/research/{RUN}-{ATTEMPT}')
RECORD = Path('/etc/trading-agent-p3/github/research-batches/0001.json')
REQUEST = Path('/etc/trading-agent-p3/github/research-batch-request.json')
REQUEST_REVIEW = Path('/etc/trading-agent-p3/github/research-batch-request-review.json')
IDENTITY = dict(issuer_kind='GITHUB', issuer_identity='p3-authority', repository='nam176hermes/Trading-Agent',
                workflow_ref='p3-authority.yml', run_id=RUN, attempt=ATTEMPT)
WORKFLOW_REF = 'nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main'
PRODUCER = dict(producer_repository='nam176hermes/Trading-Agent', producer_workflow_ref=WORKFLOW_REF,
                producer_run_id=RUN, producer_attempt=ATTEMPT)


def _request(**changes):
    value = dict(schema_version='p3-github-research-batch-request-v1', source=SOURCE, policy_digest='1' * 64,
                 segment='RESEARCH', start='2018-01-01', end='2025-08-31', monthly_exceptions=['2018-02-08'],
                 batch_ordinal=1, predecessor_commitment_sha256=None, integration_receipt_ref=_ref('4' * 64),
                 issued_at=_z(NOW - timedelta(minutes=30)), expires_at=_z(NOW + timedelta(hours=5)),
                 authority=AUTHORITY)
    value.update(changes)
    return sealed(value)


def _review(subjects, **changes):
    value = dict(schema_version='p3-review-approval-v1', source=SOURCE, subject_digests=list(subjects),
                 operator_identity='nam176hermes', reviewer_identity='claude-independent-reviewer',
                 review_execution_id='claude-session-1', verdict='APPROVED',
                 issued_at=_z(NOW - timedelta(hours=1)), expires_at=_z(NOW + timedelta(hours=6)),
                 evidence_ref=_ref('6' * 64), authority=AUTHORITY)
    value.update(changes)
    return sealed(value)


def _digest(document):
    return hashlib.sha256(canonical_json_bytes(document) + b'\n').hexdigest()


# ------------------------------------------------------------------- pre-run request
@pytest.mark.parametrize('change', [
    dict(batch_ordinal=2), dict(predecessor_commitment_sha256='1' * 64), dict(monthly_exceptions=[]),
    dict(monthly_exceptions=['2018-02-08', '2018-02-09']), dict(start='2017-12-31'), dict(segment='HOLDOUT'),
    dict(expires_at=_z(NOW + timedelta(hours=7))), dict(expires_at=_z(NOW - timedelta(hours=1))),
])
def test_request_model_admits_only_the_reviewed_first_research_batch(change):
    from packages.alpha_lifecycle.github_research import GithubResearchBatchRequest
    GithubResearchBatchRequest.model_validate_json(canonical_json_bytes(_request()))
    with pytest.raises(ValueError):
        GithubResearchBatchRequest.model_validate_json(canonical_json_bytes(_request(**change)))


@pytest.mark.parametrize('fault', [None, 'pin', 'source', 'stale', 'not_yet', 'subject', 'same_identity',
                                   'rejected', 'review_late', 'review_short'])
def test_request_validation_binds_pin_source_time_and_independent_review(fault):
    from packages.alpha_lifecycle.github_research import validate_research_batch_request
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    request = _request()
    review_changes, at, pin, source = {}, NOW, _digest(request), SOURCE
    subjects = [request['digest']]
    if fault == 'pin':
        pin = '0' * 64
    elif fault == 'source':
        source = dict(SOURCE, commit_sha='0' * 40)
    elif fault == 'stale':
        at = NOW + timedelta(hours=6)
    elif fault == 'not_yet':
        at = NOW - timedelta(hours=1)
    elif fault == 'subject':
        subjects = ['0' * 64]
    elif fault == 'same_identity':
        review_changes = dict(reviewer_identity='nam176hermes')
    elif fault == 'rejected':
        review_changes = dict(verdict='REJECTED')
    elif fault == 'review_late':
        review_changes = dict(issued_at=_z(NOW - timedelta(minutes=20)))
    elif fault == 'review_short':
        review_changes = dict(expires_at=_z(NOW + timedelta(hours=1)))
    review = _review(subjects, **review_changes)

    def check():
        return validate_research_batch_request(request, _digest(request), review, source=source,
                                               pin_sha256=pin, at=at)
    if fault is None:
        assert check().batch_ordinal == 1
    else:
        with pytest.raises(GithubRunHeld):
            check()


@pytest.mark.parametrize('fault', [None, 'fake', 'operation'])
def test_producer_reads_the_request_only_under_a_genuine_research_run(protected, fault):
    from packages.alpha_lifecycle.github_research import read_github_research_batch_request
    from packages.alpha_lifecycle.github_run import GithubRunHeld, read_github_run
    request = _request()
    protected[REQUEST] = request
    protected[REQUEST_REVIEW] = _review([request['digest']])
    _publish_run(protected, mode='FAKE' if fault == 'fake' else 'GITHUB',
                 operation='p3-baselines-v1' if fault == 'operation' else 'p3-research-inputs-v1',
                 research_inputs_sha256=_digest(request))
    run, _ = read_github_run(7, 1, admission=True, now=NOW)
    if fault is None:
        assert read_github_research_batch_request(run, source=SOURCE, now=NOW).digest == request['digest']
    else:
        with pytest.raises(GithubRunHeld):
            read_github_research_batch_request(run, source=SOURCE, now=NOW)


# ------------------------------------------------------------------- registry admission graph
class Graph:
    """A synthetic GITHUB v2 research graph in an in-memory evidence store."""

    def __init__(self, *, commitment_changes=None, backup_changes=None, local=False, suite_changes=None,
                 integration_ref=None):
        self.store = Store()
        put = self.store.put
        placeholder = put({})
        dataset = put(dict(fixture='dataset'))
        backup = dict(schema_version='p3-research-backup-receipt-v2', source=SOURCE, inventory_content_sha256='7' * 64,
                      dataset_content_sha256=dataset['content_sha256'], snapshot_content_sha256='a' * 64,
                      object_inventory_content_sha256='8' * 64, object_inventory_size_bytes=1000000,
                      destination_namespace='p3.research.backup', object_version='8' * 64, object_count=20000,
                      total_bytes=10000000, verified_at=_z(NOW - timedelta(minutes=40)), status='READBACK_VERIFIED',
                      authority=AUTHORITY, **PRODUCER)
        backup.update(backup_changes or {})
        if local:
            for key in PRODUCER:
                backup.pop(key)
            backup.update(schema_version='p3-local-research-backup-receipt-v1', identity=dict(
                issuer_kind='LOCAL', issuer_identity='p3-official-research-producer',
                run_id='00000000-0000-4000-8000-000000000001', attempt=1, host_id='b' * 64,
                boot_id='00000000-0000-4000-8000-000000000009'))
        self.backup_ref = put(sealed(backup))
        completed = NOW - timedelta(minutes=45)
        commitment = dict(schema_version='p3-research-batch-commitment-v2', source=SOURCE, policy_digest='1' * 64,
                          segment='RESEARCH', query=dict(mode='SYSTEM_OBSERVED', valid_at='2025-09-01T00:00:00Z',
                                                         cutoff=_z(completed)),
                          inventory_content_sha256='7' * 64, inventory_size_bytes=1, inventory_entry_count=2800,
                          dataset_content_sha256=dataset['content_sha256'], dataset_digest='b' * 64,
                          snapshot_content_sha256='a' * 64, batch_ordinal=1, predecessor_commitment_content_sha256=None,
                          frozen_at=_z(completed - timedelta(minutes=5)), completed_at=_z(completed),
                          issued_at=_z(NOW - timedelta(minutes=35)), backup_receipt_ref=self.backup_ref,
                          status='PASS', authority=AUTHORITY, **PRODUCER)
        commitment.update(commitment_changes or {})
        if local:
            for key in PRODUCER:
                commitment.pop(key)
            commitment.update(schema_version='p3-local-research-batch-commitment-v1', identity=backup['identity'])
        self.commitment_ref = put(sealed(commitment))
        self.environment_ref = put(sealed(dict(schema_version='p3-environment-identity-v1', python_version='3.11',
            root_lock_digest='a' * 64, native_manifest_digest='b' * 64, sandbox_policy_digest='c' * 64,
            platform='linux-x86_64', decimal_precision=50)))
        suite = dict(schema_version='p3-pit-adversarial-suite-receipt-v1', source=SOURCE,
            suite_manifest_ref=placeholder, collection_ref=placeholder, report_ref=placeholder,
            cases=[dict(case_id=f'case-{i}', node_id=f'tests/p3/test_pit.py::case_{i}') for i in range(7)],
            executed_node_ids=[f'tests/p3/test_pit.py::case_{i % 7}[{i // 7}]' for i in range(14)],
            logical_selector_count=7, collected_node_count=14, passed_node_count=14, nonpass_count=0,
            qualification=dict(completed_at_utc=_z(NOW - timedelta(minutes=36)), producer='scripts/qualify_pre_p3.py',
                               run_id=str(RUN), run_attempt=str(ATTEMPT)), authority=AUTHORITY)
        suite.update(suite_changes or {})
        self.suite_ref = put(sealed(suite))
        self.pit_ref = put(sealed(dict(schema_version='p3-p-i-t-proof-v1', dataset_ref=dataset,
            fold_manifest_ref=placeholder, vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE',
            historical_vintage_verified=False, revision_proof_ref=self.commitment_ref,
            no_future_suite_ref=self.suite_ref, limitations=['retrospective.current_archive'])))
        self.integration_ref = integration_ref or _ref('4' * 64)
        self.input_ref = put(sealed(dict(schema_version='p3-input-set-v1', source=SOURCE, epoch_id='synthetic',
            policy_digest='1' * 64, family_digest='6' * 64, dataset_evidence_ref=dataset,
            fold_manifest_ref=placeholder, pit_proof_ref=self.pit_ref, environment_ref=self.environment_ref,
            regime_threshold_ref=placeholder, integration_receipt_ref=self.integration_ref,
            cost_model=dict(fee_bps=0, spread_bps=0, slippage_bps=0, funding_bps=0, borrow_bps=0))))

    def record(self, **changes):
        value = dict(schema_version='p3-github-research-batch-record-v1', identity=IDENTITY,
                     head_sha=SOURCE['commit_sha'], request_sha256='c' * 64, request_review_sha256='d' * 64,
                     commitment_ref=self.commitment_ref, backup_receipt_ref=self.backup_ref,
                     backup_inventory_sha256='8' * 64, backup_inventory_size_bytes=1000000,
                     input_set_ref=self.input_ref, environment_ref=self.environment_ref, pit_proof_ref=self.pit_ref,
                     roots=dict(work=str(ROOT/'work'), backup=str(ROOT/'backup'), evidence=str(ROOT/'evidence')),
                     backup_root_identity=[1, 2, 3, 4], attestation_subject_sha256=self.commitment_ref['content_sha256'],
                     attestation_timestamp=_z(NOW - timedelta(minutes=25)),
                     run_started_at=_z(NOW - timedelta(minutes=110)),
                     job_completed_at=_z(NOW - timedelta(minutes=30)), registered_at=_z(NOW - timedelta(minutes=20)),
                     authority=AUTHORITY)
        value.update(changes)
        return sealed(value)


@pytest.fixture
def official(protected, monkeypatch):
    """Serve an OFFICIAL v2 receipt, its registry record and sealed custody; return a re-publisher."""
    from packages.alpha_lifecycle import github_research
    identity = {'value': (1, 2, 3, 4)}
    monkeypatch.setattr(github_research, '_backup_identity', lambda path: identity['value'])
    state = {}

    def publish(graph=None, *, record_changes=None, subjects=None, record_pin=None, **receipt_changes):
        graph = graph or Graph()
        monkeypatch.setattr(github_research, '_evidence_store', lambda root: graph.store)
        record = graph.record(**(record_changes or {}))
        protected[RECORD] = record
        record_sha256 = record_pin or _digest(record)
        store = Store()
        changes = dict(input_ref=graph.input_ref, environment_ref=graph.environment_ref,
                       integration_receipt_ref=graph.integration_ref, issued_at=_z(NOW - timedelta(minutes=10)))
        changes.update(receipt_changes)
        inputs = research_inputs(store, campaign='OFFICIAL', batch_record_sha256=record_sha256, **changes)
        subjects = subjects or (lambda inputs, record: [inputs['input_ref']['content_sha256'], _binding(inputs), record])
        store.review = sealed(dict({k: v for k, v in store.review.items() if k != 'digest'},
                                   subject_digests=subjects(inputs, record_sha256)))
        inputs = sealed(dict({k: v for k, v in inputs.items() if k != 'digest'}, review_ref=store.put(store.review)))
        protected[Path('/etc/trading-agent-p3/github/research-inputs.json')] = inputs
        protected[Path('/etc/trading-agent-p3/github/research-inputs-review.json')] = store.review
        _publish_run(protected, research_inputs_sha256=_digest(inputs))
        state.update(graph=graph, record=record, inputs=inputs)
        return graph

    def admit():
        from packages.alpha_lifecycle.contracts.base import SourceIdentity
        from packages.alpha_lifecycle.github_research import read_github_research_inputs
        from packages.alpha_lifecycle.github_run import read_github_run
        from packages.data_contracts import ArtifactRefV1
        run, _ = read_github_run(7, 1, admission=True, now=NOW)
        return read_github_research_inputs(run, source=SourceIdentity.model_validate(SOURCE),
            input_set_ref=ArtifactRefV1.model_validate(state['graph'].input_ref), now=NOW)
    return publish, admit, identity


def test_official_v2_inputs_admit_through_the_registry_record_and_sealed_custody(official):
    publish, admit, _ = official
    publish()
    admitted = admit()
    assert admitted.campaign == 'OFFICIAL' and admitted.schema_version == 'p3-github-research-inputs-v2'


@pytest.mark.parametrize('fault', [
    'local_commitment', 'other_run', 'run_attempt', 'record_pin', 'backup_identity', 'backup_identity_late',
    'record_missing', 'evidence_missing', 'registered_after_inputs', 'frozen_before_run',
    'review_without_record', 'other_head', 'inventory_size', 'second_batch', 'suite_other_run', 'suite_local',
])
def test_official_v2_admission_holds_on_any_provenance_or_custody_gap(official, monkeypatch, protected, fault):
    from packages.alpha_lifecycle import github_research
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    publish, admit, identity = official
    options = {}
    graph = None
    if fault == 'local_commitment':
        graph = Graph(local=True)
    elif fault == 'other_run':
        graph = Graph(commitment_changes=dict(producer_run_id=99), backup_changes=dict(producer_run_id=99))
    elif fault == 'run_attempt':
        options['record_changes'] = dict(identity=dict(IDENTITY, attempt=2), roots=dict(
            work=f'/srv/trading-agent-p3/research/{RUN}-2/work', backup=f'/srv/trading-agent-p3/research/{RUN}-2/backup',
            evidence=f'/srv/trading-agent-p3/research/{RUN}-2/evidence'))
    elif fault == 'record_pin':
        options['record_pin'] = 'e' * 64
    elif fault == 'registered_after_inputs':
        options['record_changes'] = dict(registered_at=_z(NOW - timedelta(minutes=5)))
    elif fault == 'frozen_before_run':
        options['record_changes'] = dict(run_started_at=_z(NOW - timedelta(minutes=49)))
    elif fault == 'review_without_record':
        options['subjects'] = lambda inputs, record: [inputs['input_ref']['content_sha256'],
                                                      _binding(inputs)]
    elif fault == 'other_head':
        options['record_changes'] = dict(head_sha='0' * 40)
    elif fault == 'inventory_size':
        options['record_changes'] = dict(backup_inventory_size_bytes=999999)
    elif fault == 'suite_other_run':
        graph = Graph(suite_changes=dict(qualification=dict(completed_at_utc=_z(NOW - timedelta(minutes=36)),
            producer='scripts/qualify_pre_p3.py', run_id='99', run_attempt='1')))
    elif fault == 'suite_local':
        graph = Graph(suite_changes=dict(schema_version='p3-pit-adversarial-suite-receipt-v2', qualification=dict(
            completed_at_utc=_z(NOW - timedelta(minutes=36)), producer='scripts/qualify_pre_p3.py', identity=dict(
                issuer_kind='LOCAL', issuer_identity='p3-official-research-producer',
                run_id='00000000-0000-4000-8000-000000000001', attempt=1, host_id='b' * 64,
                boot_id='00000000-0000-4000-8000-000000000009'))))
    elif fault == 'second_batch':
        graph = Graph(commitment_changes=dict(batch_ordinal=2, predecessor_commitment_content_sha256='9' * 64))
    publish(graph, **options)
    if fault == 'backup_identity':
        identity['value'] = (1, 2, 3, 5)
    elif fault == 'backup_identity_late':
        values = iter([(1, 2, 3, 4), (9, 9, 9, 9)])
        monkeypatch.setattr(github_research, '_backup_identity', lambda path: next(values))
    elif fault == 'record_missing':
        protected.pop(RECORD)
    elif fault == 'evidence_missing':
        class Missing:  # the protected store's own failure for an absent or replaced object
            def read_bytes(self, ref):
                raise FileNotFoundError(ref.locator)
        monkeypatch.setattr(github_research, '_evidence_store', lambda root: Missing())
    with pytest.raises(GithubRunHeld):
        admit()


def _binding(inputs):
    from packages.alpha_lifecycle.github_run import parse_github_research_inputs
    return parse_github_research_inputs(inputs).binding_digest


@pytest.mark.parametrize('change', [
    dict(roots=dict(work='/tmp/w', backup='/tmp/b', evidence='/tmp/e')),
    dict(attestation_subject_sha256='0' * 64),
    dict(attestation_timestamp=_z(NOW - timedelta(minutes=31))),
    dict(identity=dict(IDENTITY, workflow_ref='foundation.yml')),
])
def test_record_model_binds_run_derived_roots_subject_and_timing(change):
    from packages.alpha_lifecycle.github_research import GithubResearchBatchRecord
    graph = Graph()
    GithubResearchBatchRecord.model_validate_json(canonical_json_bytes(graph.record()))
    with pytest.raises(ValueError):
        GithubResearchBatchRecord.model_validate_json(canonical_json_bytes(graph.record(**change)))


# ------------------------------------------------------------------- completed-run authentication
def _completed(**changes):
    base = f'/repos/nam176hermes/Trading-Agent/actions/runs/{RUN}'
    run = dict(id=RUN, run_attempt=ATTEMPT, path='.github/workflows/p3-authority.yml', event='workflow_dispatch',
               head_branch='main', head_sha=SOURCE['commit_sha'], status='completed', conclusion='success',
               actor=dict(login='nam176hermes'), triggering_actor=dict(login='nam176hermes'),
               display_title='p3 p3-research-inputs-v1', repository=dict(full_name='nam176hermes/Trading-Agent'),
               run_started_at='2026-10-21T10:00:00Z')
    p3 = dict(name='p3', conclusion='success', runner_name=f'p3-jit-{RUN}-{ATTEMPT}',
              labels=['self-hosted', 'p3-authority', f'p3-run-{RUN}-{ATTEMPT}'], completed_at='2026-10-21T10:55:00Z')
    attest = dict(name='attest-research-inputs', conclusion='success', runner_name='GitHub Actions 1')
    for key, value in changes.items():
        target, field = key.split('__')
        dict(run=run, p3=p3, attest=attest)[target][field] = value
    jobs = [p3, attest]
    return {f'{base}/attempts/{ATTEMPT}': run, f'{base}/attempts/{ATTEMPT}/jobs': dict(total_count=2, jobs=jobs)}


def _authenticate(documents, operation='p3-research-inputs-v1'):
    import json
    from packages.alpha_lifecycle.github_research import authenticate_completed_run
    from packages.alpha_lifecycle.github_run import ExpectedRun
    expected = ExpectedRun(head_sha=SOURCE['commit_sha'], actor='nam176hermes', operation=operation,
                           runner_name=f'p3-jit-{RUN}-{ATTEMPT}', research_inputs_sha256='c' * 64)
    return authenticate_completed_run(lambda path: json.dumps(documents[path]).encode(), run_id=RUN,
                                      attempt=ATTEMPT, expected=expected)


def test_completed_producer_run_is_authenticated_with_its_api_times():
    assert _authenticate(_completed()) == dict(run_started_at='2026-10-21T10:00:00Z',
                                               job_completed_at='2026-10-21T10:55:00Z')


@pytest.mark.parametrize('change', [
    dict(run__status='in_progress'), dict(run__conclusion='failure'), dict(run__path='.github/workflows/foundation.yml'),
    dict(run__head_sha='0' * 40), dict(run__display_title='p3 p3-baselines-v1'), dict(run__event='push'),
    dict(run__actor=dict(login='other')), dict(p3__conclusion='failure'), dict(attest__conclusion='skipped'),
    dict(p3__runner_name='other'), dict(p3__labels=['self-hosted']), dict(attest__runner_name=f'p3-jit-{RUN}-{ATTEMPT}'),
    dict(attest__name='intruder'), dict(run__run_started_at=None),
])
def test_completed_run_authentication_holds_on_any_unexpected_value(change):
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    with pytest.raises(GithubRunHeld):
        _authenticate(_completed(**change))


def test_completed_run_authentication_is_only_for_the_research_producer():
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    with pytest.raises(GithubRunHeld):
        _authenticate(_completed(), operation='p3-baselines-v1')
