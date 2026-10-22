"""Root side of the GITHUB research producer, on real custody directories in a private temp root.

Custody is built from the same synthetic GITHUB v2 graph the admission tests use, written as
content-addressed blobs. Ownership changes (root only) and the protected publication are replaced
at their named seams; every byte, listing and binding check runs for real.
"""
from __future__ import annotations

from datetime import timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_github_research import ATTEMPT, IDENTITY, RUN, Graph, _completed, _digest, _request, _review
from tests.p3.test_github_run import NOW, SOURCE, _z, protected

__all__ = ['protected']
OBJECTS = [dict(fixture='dataset'), dict(fixture='raw-zip'), dict(fixture='normalized')]


def _blob(value):
    raw = canonical_json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    return raw, dict(content_sha256=digest, size_bytes=len(raw), media_type='application/json',
                     locator=f'{digest}.blob')


class Custody:
    """Run-derived roots under a private base, holding a complete, consistent producer output."""

    def __init__(self, base: Path, monkeypatch):
        from packages.alpha_lifecycle import github_research
        from scripts import p3_github_research
        monkeypatch.setattr(github_research, 'RESEARCH_BASE', base)
        monkeypatch.setattr(p3_github_research, 'RESEARCH_BASE', base)
        self.roots = github_research.research_roots(RUN, ATTEMPT)
        for key in ('work', 'backup'):
            self.roots[key].mkdir(parents=True, mode=0o700)
        objects = [_blob(value) for value in OBJECTS]
        inventory_raw, self.inventory_ref = _blob([ref for _, ref in objects])
        self.integration_raw, integration = _blob(dict(schema_version='p3-integration-qualified-v1', run=RUN))
        self.graph = Graph(backup_changes=dict(object_inventory_content_sha256=self.inventory_ref['content_sha256'],
                                               object_version=self.inventory_ref['content_sha256'],
                                               object_inventory_size_bytes=self.inventory_ref['size_bytes']),
                           integration_ref=integration)
        self.graph.store.blobs[integration['content_sha256']] = self.integration_raw
        for raw, ref in objects:
            self.write('work', raw)
            self.write('backup', raw)
        self.write('backup', inventory_raw)
        for raw in self.graph.store.blobs.values():
            self.write('work', raw)

    def write(self, key, raw):
        (self.roots[key]/f'{hashlib.sha256(raw).hexdigest()}.blob').write_bytes(raw)

    def proposal(self, **changes):
        from scripts.p3_github_research import GithubResearchProposal
        graph = self.graph
        value = dict(schema_version='p3-github-research-proposal-v1', identity=IDENTITY, head_sha=SOURCE['commit_sha'],
                     input_set_ref=graph.input_ref, environment_ref=graph.environment_ref, pit_proof_ref=graph.pit_ref,
                     commitment_ref=graph.commitment_ref, backup_receipt_ref=graph.backup_ref,
                     backup_inventory_ref=self.inventory_ref, completed_at=_z(NOW - timedelta(minutes=31)),
                     authority=dict(broker=False, live=False, network=False, production=False))
        value.update(changes)
        value = {k: v for k, v in value.items() if k != 'digest'}
        value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        return GithubResearchProposal.model_validate_json(canonical_json_bytes(value))


@pytest.fixture
def custody(tmp_path, monkeypatch):
    base = tmp_path/'research'
    base.mkdir(mode=0o700)
    return Custody(base, monkeypatch)


def test_observed_custody_is_exactly_the_typed_closure_plus_backup_objects(custody):
    from packages.data_contracts import ArtifactRefV1
    from scripts.p3_github_research import observe_custody
    closure, inventory = observe_custody(custody.roots, ArtifactRefV1.model_validate(custody.graph.input_ref))
    assert inventory == ArtifactRefV1.model_validate(custody.inventory_ref)
    assert custody.graph.commitment_ref['locator'] in closure and custody.graph.suite_ref['locator'] in closure


@pytest.mark.parametrize('fault', ['extra_work', 'missing_work', 'extra_backup', 'missing_backup', 'tmp_file',
                                   'changed_copy', 'symlink'])
def test_custody_observation_holds_on_any_unexplained_or_missing_object(custody, fault):
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    from packages.data_contracts import ArtifactRefV1
    from scripts.p3_github_research import observe_custody
    work, backup = custody.roots['work'], custody.roots['backup']
    target = sorted(backup.iterdir())[0]
    if fault == 'extra_work':
        custody.write('work', b'{"stray":1}')
    elif fault == 'missing_work':
        (work/custody.graph.suite_ref['locator']).unlink()
    elif fault == 'extra_backup':
        custody.write('backup', b'{"stray":1}')
    elif fault == 'missing_backup':
        next(path for path in backup.iterdir() if path.name != custody.inventory_ref['locator']).unlink()
    elif fault == 'tmp_file':
        (work/'partial.tmp').write_bytes(b'x')
    elif fault == 'changed_copy':
        name = next(path.name for path in backup.iterdir() if path.name != custody.inventory_ref['locator'])
        (backup/name).write_bytes(b'tampered')
    elif fault == 'symlink':
        name = custody.graph.environment_ref['locator']
        (work/name).unlink()
        (work/name).symlink_to(target)
    with pytest.raises((GithubRunHeld, ValueError, OSError)):
        observe_custody(custody.roots, ArtifactRefV1.model_validate(custody.graph.input_ref))


def test_create_custody_is_exclusive_and_owned_by_the_producer(tmp_path, monkeypatch):
    import os
    from packages.alpha_lifecycle import github_research
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    from scripts import p3_github_research
    base = tmp_path/'research'
    base.mkdir(mode=0o755)
    monkeypatch.setattr(github_research, 'RESEARCH_BASE', base)
    monkeypatch.setattr(p3_github_research, 'RESEARCH_BASE', base)
    owners = []
    monkeypatch.setattr(os, 'chown', lambda path, uid, gid, **_: owners.append((Path(path).name, uid, gid)))
    monkeypatch.setattr(os, 'lstat', lambda path, _real=os.lstat: _as_root(_real(path)))
    roots = p3_github_research.create_research_custody(RUN, ATTEMPT, producer_uid=985, producer_gid=985)
    assert owners == [('work', 985, 985), ('backup', 985, 985)]
    assert {key: path.exists() for key, path in roots.items()} == dict(work=True, backup=True, evidence=False)
    with pytest.raises(GithubRunHeld, match='never reused'):
        p3_github_research.create_research_custody(RUN, ATTEMPT, producer_uid=985, producer_gid=985)


def _as_root(info):
    import os
    values = list(info)
    values[4] = 0  # st_uid
    return os.stat_result(values)


def test_outbox_commitment_comes_from_the_store_and_binds_the_run(custody, protected):
    from packages.alpha_lifecycle.github_run import GithubRunHeld, read_github_run
    from scripts.p3_github_research import COMMITMENT_NAME, research_files
    from tests.p3.test_github_run import _publish_run
    _publish_run(protected, operation='p3-research-inputs-v1')
    run, _ = read_github_run(7, 1, admission=False, now=NOW)
    run = run.model_copy(update=dict(identity=run.identity.model_copy(update=dict(run_id=RUN, attempt=ATTEMPT))))
    files = research_files(run, canonical_json_bytes(custody.proposal()))
    assert hashlib.sha256(files[COMMITMENT_NAME]).hexdigest() == custody.graph.commitment_ref['content_sha256']
    assert (custody.roots['work'].parent/'proposal.json').exists()
    other = run.model_copy(update=dict(operation='p3-baselines-v1'))
    with pytest.raises(GithubRunHeld):
        research_files(other, canonical_json_bytes(custody.proposal()))


def test_attestation_result_needs_one_subject_and_a_tlog_time():
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    from scripts.p3_github_research import parse_attestation
    good = [dict(verificationResult=dict(statement=dict(subject=[dict(digest=dict(sha256='a' * 64))]),
                                         verifiedTimestamps=[dict(type='Tlog', timestamp='2026-10-18T11:45:00-04:00')]))]
    digest, moment = parse_attestation(good)
    assert digest == 'a' * 64 and moment.utcoffset() is not None
    for bad in ([], [good[0], good[0]], [dict(verificationResult=dict(statement=dict(subject=[]),
                                                                      verifiedTimestamps=[]))],
                [dict(verificationResult=dict(statement=good[0]['verificationResult']['statement'],
                                              verifiedTimestamps=[dict(type='TSA', timestamp='2026-10-18T11:45:00Z')]))]):
        with pytest.raises(GithubRunHeld):
            parse_attestation(bad)


# ------------------------------------------------------------------- registration
class Api:
    def __init__(self, documents):
        self.documents = documents

    def get(self, path):
        return json.dumps(self.documents[path]).encode()


@pytest.fixture
def registration(custody, protected, tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.p3_github_research.RECORD_PATH', tmp_path/'research-batches'/'0001.json')
    (tmp_path/'research-batches').mkdir()
    request = _request(issued_at=_z(NOW - timedelta(hours=2)), expires_at=_z(NOW + timedelta(hours=3)),
                       integration_receipt_ref=custody.graph.integration_ref)
    protected[Path('/etc/trading-agent-p3/github/research-batch-request.json')] = request
    protected[Path('/etc/trading-agent-p3/github/research-batch-request-review.json')] = _review(
        [request['digest']], issued_at=_z(NOW - timedelta(hours=3)))
    outbox = tmp_path/'outbox'
    (outbox/f'{RUN}-{ATTEMPT}').mkdir(parents=True)
    commitment = custody.graph.store.blobs[custody.graph.commitment_ref['content_sha256']]
    (outbox/f'{RUN}-{ATTEMPT}'/'p3-research-commitment-v1.json').write_bytes(commitment)
    (custody.roots['work'].parent/'proposal.json').write_bytes(canonical_json_bytes(custody.proposal()) + b'\n')
    published = {}
    state = dict(api=_completed(run__run_started_at=_z(NOW - timedelta(minutes=110)),
                                p3__completed_at=_z(NOW - timedelta(minutes=30))),
                 subject=custody.graph.commitment_ref['content_sha256'],
                 attested=NOW - timedelta(minutes=25), pin=_digest(request), sealed=[], idle=lambda: None)

    def register(now=NOW - timedelta(minutes=20)):
        from scripts.p3_github_research import register_research_batch
        return register_research_batch(
            Api(state['api']), head_sha=SOURCE['commit_sha'], actor='nam176hermes', run_id=RUN, attempt=ATTEMPT,
            request_pin=state['pin'], outbox_root=outbox, now=now,
            verify=lambda path, head: (state['subject'], state['attested']),
            seal=state['sealed'].append, backup_identity=lambda path: (1, 2, 3, 4),
            publish=lambda path, raw: published.setdefault(path, raw), idle=lambda: state['idle']())
    return register, state, published, custody, outbox


def test_registration_writes_one_record_binding_run_custody_attestation_and_request(registration):
    from packages.alpha_lifecycle.github_research import GithubResearchBatchRecord
    from scripts.p3_github_research import RECORD_PATH
    register, state, published, custody, _ = registration
    state['attested'] = (NOW - timedelta(minutes=25)).astimezone(timezone(timedelta(hours=-4)))  # gh local time
    record = register()
    assert record.attestation_timestamp == NOW - timedelta(minutes=25) and state['sealed']
    assert isinstance(record, GithubResearchBatchRecord) and list(published) == [RECORD_PATH]
    assert record.attestation_subject_sha256 == custody.graph.commitment_ref['content_sha256']
    assert record.request_sha256 == state['pin'] and record.backup_root_identity == (1, 2, 3, 4)
    evidence = sorted(path.name for path in custody.roots['evidence'].iterdir())
    assert custody.graph.suite_ref['locator'] in evidence and len(evidence) == 6


@pytest.mark.parametrize('fault', ['attest_job', 'run_failed', 'subject', 'attested_early', 'request_pin',
                                   'request_expired_at_start', 'outbox_changed', 'extra_blob', 'started_late',
                                   'hard_link', 'producer_running', 'registry_present', 'request_policy'])
def test_registration_holds_on_any_unverified_precondition(registration, protected, fault):
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    register, state, published, custody, outbox = registration
    api = state['api']
    attempt = f'/repos/nam176hermes/Trading-Agent/actions/runs/{RUN}/attempts/{ATTEMPT}'
    if fault == 'attest_job':
        api[f'{attempt}/jobs']['jobs'][1]['conclusion'] = 'skipped'
    elif fault == 'run_failed':
        api[attempt]['conclusion'] = 'failure'
    elif fault == 'subject':
        state['subject'] = '0' * 64
    elif fault == 'attested_early':
        state['attested'] = NOW - timedelta(minutes=31)
    elif fault == 'request_pin':
        state['pin'] = '0' * 64
    elif fault == 'request_expired_at_start':
        api[attempt]['run_started_at'] = _z(NOW - timedelta(hours=2, minutes=1))
    elif fault == 'outbox_changed':
        (outbox/f'{RUN}-{ATTEMPT}'/'p3-research-commitment-v1.json').write_bytes(b'{}')
    elif fault == 'extra_blob':
        custody.write('work', b'{"stray":1}')
    elif fault == 'started_late':
        api[attempt]['run_started_at'] = _z(NOW - timedelta(minutes=49))
    elif fault == 'hard_link':
        blob = next(custody.roots['backup'].iterdir())
        os.link(blob, custody.roots['work'].parent/'outside')
    elif fault == 'producer_running':
        def running():
            raise GithubRunHeld('producer still running')
        state['idle'] = running
    elif fault == 'request_policy':  # a reviewed request field the batch does not follow
        request = _request(issued_at=_z(NOW - timedelta(hours=2)), expires_at=_z(NOW + timedelta(hours=3)),
                       integration_receipt_ref=custody.graph.integration_ref,
                           policy_digest='0' * 64)
        protected[Path('/etc/trading-agent-p3/github/research-batch-request.json')] = request
        protected[Path('/etc/trading-agent-p3/github/research-batch-request-review.json')] = _review(
            [request['digest']], issued_at=_z(NOW - timedelta(hours=3)))
        state['pin'] = _digest(request)
    elif fault == 'registry_present':
        from scripts.p3_github_research import RECORD_PATH
        RECORD_PATH.write_text('{}')
    with pytest.raises((GithubRunHeld, ValueError)):
        register()
    assert not published and not state['sealed']  # nothing irreversible happens before every check holds


def test_sealing_makes_custody_root_owned_and_read_only(custody, monkeypatch):
    from scripts.p3_github_research import seal_research_custody
    owners = {}
    monkeypatch.setattr(os, 'chown', lambda path, uid, gid, **_: owners.__setitem__(Path(path), (uid, gid)))
    custody.write('work', b'{"w":1}')
    custody.write('backup', b'{"b":1}')
    seal_research_custody(custody.roots)
    for key, directory_mode, file_mode in (('work', 0o555, 0o444), ('backup', 0o500, 0o400)):
        root = custody.roots[key]
        assert owners[root] == (0, 0) and root.stat().st_mode & 0o7777 == directory_mode
        for path in root.iterdir():
            assert owners[path] == (0, 0) and path.stat().st_mode & 0o7777 == file_mode
    for key in ('work', 'backup'):
        os.chmod(custody.roots[key], 0o755)  # let tmp_path cleanup proceed
