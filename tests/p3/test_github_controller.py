"""Hosted controller orchestration against a recorded GitHub API; host steps are fakes."""
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import UUID

import pytest

HEAD = 'a' * 40
HOST = ('b' * 64, UUID('00000000-0000-4000-8000-000000000009'))
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class Api:
    def __init__(self, *, operation='p3-baselines-v1', attempt=1, queued=1, approval='all_external_contributors',
                 main=HEAD, assign_after=0):
        self.calls, self.operation, self.attempt = [], operation, attempt
        self.queued, self.approval, self.main, self.assign_after = queued, approval, main, assign_after
        self.polls = 0

    def _run(self, status):
        return dict(id=7, run_attempt=self.attempt, path='.github/workflows/p3-authority.yml',
            event='workflow_dispatch', head_branch='main', head_sha=HEAD, status=status,
            actor=dict(login='nam176hermes'), triggering_actor=dict(login='nam176hermes'),
            display_title=f'p3 {self.operation}', repository=dict(full_name='nam176hermes/Trading-Agent'))

    def get(self, path):
        self.calls.append(('GET', path.split('?')[0]))
        base = '/repos/nam176hermes/Trading-Agent'
        if path.startswith(base + '/actions/workflows/p3-authority.yml/runs'):
            value = dict(workflow_runs=[self._run('queued')] * self.queued)
        elif path == base + '/actions/permissions/fork-pr-contributor-approval':
            value = dict(approval_policy=self.approval)
        elif path == base + '/branches/main':
            value = dict(name='main', protected=True, commit=dict(sha=self.main))
        elif path == base + '/actions/runs/7':
            self.polls += 1
            value = self._run('in_progress' if self.polls > self.assign_after else 'queued')
        elif path == base + f'/actions/runs/7/attempts/{self.attempt}/jobs':
            job = dict(name='p3', status='in_progress', run_id=7, run_attempt=self.attempt,
                       runner_name=f'p3-jit-7-{self.attempt}',
                       labels=['self-hosted', 'p3-authority', f'p3-run-7-{self.attempt}'])
            value = dict(total_count=1, jobs=[job])
        else:
            raise AssertionError(path)
        return json.dumps(value).encode()

    def post(self, path, body):
        self.calls.append(('POST', path))
        assert body['labels'] == ['p3-authority', f'p3-run-7-{self.attempt}']
        return dict(runner=dict(name=body['name'], id=33), encoded_jit_config='one-job-credential')

    def delete(self, path):
        self.calls.append(('DELETE', path))


def plan(**changes):
    from scripts.p3_github_controller import ControllerPlan
    return ControllerPlan.model_validate(dict(dict(schema_version='p3-github-controller-plan-v1', mode='GITHUB',
        head_sha=HEAD, actor='nam176hermes', research_inputs_sha256='f' * 64, runner_root='/srv/p3-runner/runner',
        runner_user='ta-gh-runner', outbox_root='/srv/trading-agent-p3/outbox', token_file='/etc/token'), **changes))


def primitives(events, *, admit=None, execute=None):
    from scripts.p3_github_controller import Primitives
    published = {}

    def publish(path, raw, replace):
        events.append(('publish', str(path)))
        published[str(path)] = raw

    def default_execute(run, operation, admitted):
        events.append(('execute', operation))
        return dict(outcome='SUCCEEDED', job_ids=['job_1'], files={})
    value = Primitives(publish=publish, revoke=lambda path: events.append(('revoke', str(path))),
        host_identity=lambda: HOST, start_runner=lambda plan, config: events.append(('runner', config)) or 'proc',
        stop_runner=lambda process: events.append(('stop', process)),
        admit=admit or (lambda run, operation: events.append(('admit', operation)) or {'nonce': 'n'}),
        execute=execute or default_execute, sleep=lambda seconds: None, now=lambda: NOW)
    return value, published


def test_operation_is_attested_admitted_executed_and_published_in_order():
    from scripts.p3_github_controller import serve_operation
    events, api = [], Api()
    value, published = primitives(events)
    result = serve_operation(api, plan(), value, 'p3-baselines-v1')
    assert result['outcome'] == 'SUCCEEDED' and (result['run_id'], result['attempt']) == (7, 1)
    names = [event[0] for event in events]
    assert names == ['runner', 'publish', 'admit', 'execute', 'publish', 'revoke', 'stop']
    assert events[1] == ('publish', '/run/trading-agent-p3/7-1/github-run.json')
    assert events[4] == ('publish', '/srv/trading-agent-p3/outbox/7-1/status.json')
    status = json.loads(published['/srv/trading-agent-p3/outbox/7-1/status.json'])
    assert status['outcome'] == 'SUCCEEDED' and status['phase_exit'] == 'NONE' and status['run_id'] == 7
    attestation = json.loads(published['/run/trading-agent-p3/7-1/github-run.json'])
    assert attestation['runner_name'] == 'p3-jit-7-1' and attestation['research_inputs_sha256'] == 'f' * 64
    # Guards run before any runner exists; the runner is deregistered afterwards.
    assert api.calls.index(('GET', '/repos/nam176hermes/Trading-Agent/actions/permissions/fork-pr-contributor-approval')) \
        < api.calls.index(('POST', '/repos/nam176hermes/Trading-Agent/actions/runners/generate-jitconfig'))
    assert api.calls[-1] == ('DELETE', '/repos/nam176hermes/Trading-Agent/actions/runners/33')


@pytest.mark.parametrize('fault', ['approval', 'main_moved', 'two_queued', 'holdout_reattempt'])
def test_guards_hold_before_any_runner_or_authority(fault):
    from scripts.p3_github_controller import ControllerHeld, serve_operation
    operation = 'p3-holdout-primary-v1' if fault == 'holdout_reattempt' else 'p3-baselines-v1'
    api = Api(operation=operation, approval='first_time_contributors' if fault == 'approval' else 'all_external_contributors',
              main='0' * 40 if fault == 'main_moved' else HEAD, queued=2 if fault == 'two_queued' else 1,
              attempt=2 if fault == 'holdout_reattempt' else 1)
    events = []
    value, _ = primitives(events)
    with pytest.raises(ControllerHeld):
        serve_operation(api, plan(), value, operation)
    assert events == [] and not any(call[0] == 'POST' for call in api.calls)


def test_queued_run_for_another_source_or_actor_is_held():
    from scripts.p3_github_controller import ControllerHeld, precheck_queued
    with pytest.raises(ControllerHeld):
        precheck_queued(Api(), plan(head_sha='c' * 40), 'p3-baselines-v1')
    with pytest.raises(ControllerHeld):
        precheck_queued(Api(), plan(actor='someone-else'), 'p3-baselines-v1')
    with pytest.raises(ControllerHeld):
        precheck_queued(Api(operation='p3-oos-a0-v1'), plan(), 'p3-baselines-v1')


def test_failed_admission_stops_the_runner_revokes_and_publishes_nothing():
    from scripts.p3_github_controller import serve_operation
    events, api = [], Api()
    def refuse(run, operation):
        raise ValueError('review does not cover this intent')
    value, published = primitives(events, admit=refuse)
    with pytest.raises(ValueError):
        serve_operation(api, plan(), value, 'p3-baselines-v1')
    assert [event[0] for event in events] == ['runner', 'publish', 'revoke', 'stop']
    assert not any(path.startswith('/srv/trading-agent-p3/outbox') for path in published)
    assert api.calls[-1][0] == 'DELETE'


def test_late_assignment_is_polled_and_bounded():
    from scripts.p3_github_controller import ControllerHeld, serve_operation
    events = []
    value, _ = primitives(events)
    assert serve_operation(Api(assign_after=2), plan(), value, 'p3-baselines-v1')['outcome'] == 'SUCCEEDED'
    with pytest.raises(ControllerHeld):
        serve_operation(Api(assign_after=10**6), plan(), primitives([])[0], 'p3-baselines-v1', assignment_seconds=0)


def test_outbox_accepts_only_public_safe_names_and_writes_status_last():
    from scripts.p3_github_controller import ControllerHeld, write_outbox
    events = []
    value, _ = primitives(events)
    write_outbox(plan(), value, 7, 1, {'p3-phase-exit-v1.json': b'{}', 'status.json': b'{}'})
    assert [Path(event[1]).name for event in events] == ['p3-phase-exit-v1.json', 'status.json']
    for files in ({'status.json': b'{}', 'host-profile.json': b'{}'}, {'p3-phase-exit-v1.json': b'{}'}):
        with pytest.raises(ControllerHeld):
            write_outbox(plan(), value, 7, 1, files)


def test_status_document_accepts_only_closed_terminal_outcomes():
    from scripts.p3_github_controller import ControllerHeld, status_document
    from tests.p3.test_github_run import authenticate
    run = authenticate()
    assert json.loads(status_document('p3-holdout-primary-v1', run,
        dict(outcome='SUCCEEDED', phase_exit='PASS')))['phase_exit'] == 'PASS'
    for result in (dict(outcome='MAYBE'), dict(outcome='SUCCEEDED', phase_exit='FAIL')):
        with pytest.raises(ControllerHeld):
            status_document('p3-baselines-v1', run, result)


def test_refresh_failure_revokes_the_attestation_and_holds_the_result(monkeypatch):
    from scripts import p3_github_controller as module
    monkeypatch.setattr(module, 'REFRESH_SECONDS', 0.01)
    events, api = [], Api()
    def slow(run, operation, admitted):
        api.attempt_saved, api.attempt = api.attempt, 99   # the API now reports another attempt
        time_limit = __import__('time').monotonic() + 2
        while ('revoke', '/run/trading-agent-p3/7-1/github-run.json') not in events:
            assert __import__('time').monotonic() < time_limit
            __import__('time').sleep(0.01)
        return dict(outcome='SUCCEEDED', phase_exit='PASS', files={'p3-phase-exit-v1.json': b'{}'})
    value, published = primitives(events, execute=slow)
    module.serve_operation(api, plan(), value, 'p3-baselines-v1')
    status = json.loads(published['/srv/trading-agent-p3/outbox/7-1/status.json'])
    assert status['outcome'] == 'HELD' and status['phase_exit'] == 'NONE'
    assert '/srv/trading-agent-p3/outbox/7-1/p3-phase-exit-v1.json' not in published  # PR77-03
