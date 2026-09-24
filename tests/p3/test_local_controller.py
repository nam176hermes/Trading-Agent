"""Controller checks use synthetic documents and never install host authority."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.alpha_lifecycle.contracts.authority import ReviewApproval
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_local_research_admission import sealed


def test_research_review_requires_exact_subjects_and_full_validity():
    from scripts.p3_local_controller import validate_research_review
    now = datetime.now(UTC).replace(microsecond=0)
    source = dict(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='v1',
                  closure_policy_sha256='c'*64, closure_sha256='d'*64)
    ref = dict(content_sha256='e'*64, locator='e'*64+'.blob', size_bytes=2, media_type='application/json')
    value = dict(schema_version='p3-review-approval-v1', source=source,
        subject_digests=['1'*64,'2'*64], operator_identity='operator', reviewer_identity='agent-reviewer',
        review_execution_id='independent-1', verdict='APPROVED',
        issued_at=(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),
        expires_at=(now+timedelta(minutes=40)).isoformat().replace('+00:00','Z'),
        evidence_ref=ref, authority=dict(broker=False, live=False, network=False, production=False))
    def check(change):
        review = ReviewApproval.model_validate_json(canonical_json_bytes(sealed({**value, **change})))
        validate_research_review(review, source=review.source, subject_digests=('1'*64,'2'*64),
            now=now, expires_at=now+timedelta(minutes=30))
    check({})
    for change in ({'reviewer_identity':'operator'}, {'verdict':'REJECTED'},
                   {'subject_digests':['1'*64]}, {'subject_digests':['1'*64,'2'*64,'3'*64]},
                   {'expires_at':(now+timedelta(minutes=2)).isoformat().replace('+00:00','Z')}):
        with pytest.raises(ValueError): check(change)


def test_protected_publication_refuses_untrusted_parent_and_symlink(tmp_path):
    from scripts.p3_local_controller import publish_protected
    target = tmp_path/'authority.json'
    with pytest.raises((ValueError, PermissionError)):
        publish_protected(target, b'{}')
    assert not target.exists()
    link = tmp_path/'link'
    link.symlink_to('/etc')
    with pytest.raises((OSError, ValueError, PermissionError)):
        publish_protected(link/'must-not-exist.json', b'{}')
    assert not Path('/etc/must-not-exist.json').exists()


def test_stage_history_rejects_reordered_duplicate_or_different_session():
    from scripts.p3_local_controller import next_stage
    from services.job_worker.p3_session import SessionAttempt
    def attempt(operation, label):
        return SessionAttempt(operation=operation, job_id='job_'+label,
            attempt_id='attempt_'+label, worker_id='worker', lease_token_sha256='f'*64)
    first = next_stage(None, attempt('HOLDOUT', 'one'), session_digest='a'*64, host_digest='b'*64)
    second = next_stage(first, attempt('PARITY', 'two'), session_digest='a'*64, host_digest='c'*64)
    assert len(second.attempts) == 2 and second.attempts[0] == first.attempts[0]
    for current, session_digest in ((attempt('PHASE_EXIT','two'),'a'*64),
                                    (attempt('PARITY','one'),'a'*64),
                                    (attempt('PARITY','two'),'d'*64)):
        with pytest.raises(ValueError):
            next_stage(first, current, session_digest=session_digest, host_digest='c'*64)


def test_operation_issuer_rejects_auth_intent_mismatch_before_any_authority_io(monkeypatch, tmp_path):
    from scripts import p3_local_controller as module
    from packages.alpha_lifecycle.authority import _LocalRequestFile, AuthorityHeld
    from tests.p3.test_authority import _request
    from tests.p3.test_operation_input import intent
    import json
    now = datetime.now(UTC)
    request, source = _request((now+timedelta(minutes=5)).isoformat().replace('+00:00','Z'))
    auth = request['authorization']
    for key in ('issuer_workflow', 'issuer_run_id', 'issuer_attempt'):
        auth.pop(key)
    auth.update(schema_version='p3-local-run-authorization-v1', operation='OOS',
        allowed_alpha_ids=['a0.donchian-20-10-close-confirm'],
        issued_at=(now-timedelta(seconds=1)).isoformat().replace('+00:00','Z'),
        identity=dict(issuer_kind='LOCAL', issuer_identity='operator-a',
            run_id='00000000-0000-4000-8000-000000000002', attempt=1,
            host_id='a'*64, boot_id='00000000-0000-4000-8000-000000000001'))
    request.update(schema_version='p3-local-authority-request-v1', authorization=sealed(auth),
        operation_input=json.loads(intent(input_set_ref=auth['input_set_ref'])))
    request = _LocalRequestFile.model_validate_json(canonical_json_bytes(request))
    monkeypatch.setattr(module.os, 'geteuid', lambda:0)
    monkeypatch.setattr(module, '_protected_model', lambda *_:request)
    monkeypatch.setattr(module, '_committed_source', lambda:source)
    def unexpected(*_):
        pytest.fail('mismatched operation reached authority/proof access')
    monkeypatch.setattr(module, 'ProtectedQualificationStore', unexpected)
    monkeypatch.setattr(module, 'publish_protected', unexpected)
    with pytest.raises(AuthorityHeld, match='operation differs'):
        module.install_operation(tmp_path/'request', tmp_path/'review', tmp_path/'proof')


@pytest.mark.parametrize('wrong_owner', [False, True])
def test_session_issuer_observes_actual_process_before_publication(tmp_path, monkeypatch, wrong_owner):
    """Real /proc observations; protected input/issuer IO is explicitly substituted."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    import os
    from scripts import p3_local_controller as module
    from services.job_worker.p3_session import LocalSessionLaunch
    from services.job_worker.recovery import ProcProcessInspector
    from packages.alpha_lifecycle.operation_input import HoldoutInput
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.alpha_lifecycle.local_qualification import _host_identity
    from tests.p3.test_replay import _ref
    host_id, boot_id = _host_identity()
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='LOCAL',
        issuer_identity='test-owner', run_id='00000000-0000-4000-8000-000000000002', attempt=1,
        host_id=host_id, boot_id=str(boot_id))))
    source = SourceIdentity(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='v1',
        closure_policy_sha256='c'*64, closure_sha256='d'*64)
    now = datetime.now(UTC)
    process = ProcProcessInspector().inspect(os.getpid())
    parent = dict(pid=process.pid, process_group=process.process_group,
        start_ticks=process.start_ticks, command_fingerprint=process.command_fingerprint)
    deadline = now+timedelta(minutes=30)
    launch = LocalSessionLaunch.model_validate_json(canonical_json_bytes(dict(
        schema_version='p3-local-session-launch-v1', identity=identity, source=source, job_id='job_owner',
        host_profile_sha256='a'*64, worker_pid=os.getpid(), worker_start_ticks=process.start_ticks,
        worker_uid=os.geteuid(), controller_pid=os.getppid(),
        issued_at=now.isoformat().replace('+00:00','Z'), workflow_deadline=deadline.isoformat().replace('+00:00','Z'))))
    observation = dict(schema_version='p3-local-session-owner-observation-v1',
        identity=identity, source=source, job_id='job_owner', host_profile_sha256='a'*64,
        parent={**parent, 'pid':os.getpid()+1} if wrong_owner else parent,
        issued_at=now.isoformat().replace('+00:00','Z'), workflow_deadline=deadline.isoformat().replace('+00:00','Z'))
    path = tmp_path/'job_owner/session-owner/observation.log'
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(observation)); path.chmod(0o600)
    body = HoldoutInput.model_validate_json(canonical_json_bytes({key:
        'f'*64 if key=='policy_digest' else _ref('e'*64) for key in HoldoutInput.model_fields}))
    host = SimpleNamespace(job_id='job_owner', output_root=str(tmp_path),
        environment_ref=body.environment_ref, closure_sha256=source.closure_sha256)
    request = SimpleNamespace(operation_input=SimpleNamespace(body=body), execution_source=source,
        authorization=SimpleNamespace(identity=identity, expires_at=deadline))
    monkeypatch.setattr(module, '_session_context', lambda _:(request, tmp_path, host, 'a'*64))
    monkeypatch.setattr(module, '_protected_model', lambda *_:launch)
    monkeypatch.setattr(module, 'ProtectedQualificationStore', lambda _:object())
    monkeypatch.setattr(module, '_read', lambda *_:SimpleNamespace(holdout_commitment='f'*64))
    monkeypatch.setattr(module, 'AuthorityStore', lambda _:SimpleNamespace(locked=nullcontext))
    published = []
    monkeypatch.setattr(module, 'publish_protected', lambda path, raw:published.append((path,raw)))
    if wrong_owner:
        with pytest.raises(ValueError, match='current transport child'):
            module.install_session(tmp_path/'request', tmp_path/'proof')
        assert published == []
    else:
        session = module.install_session(tmp_path/'request', tmp_path/'proof')
        assert session.parent == process and session.workflow_deadline == deadline
        assert session.issued_at == now and len(published) == 1
