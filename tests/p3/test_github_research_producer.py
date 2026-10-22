"""Guards and acquisition shape of the GITHUB research producer; the full body is a composition
of committed, separately tested functions and runs for real only inside the genuine run."""
from __future__ import annotations

from datetime import date
import json

import pytest

from tests.p3.test_github_research import _request
from tests.p3.test_github_run import AUTHORITY, NOW, SOURCE, _publish_run, _z, protected

__all__ = ['protected']


class Receipt:
    def __init__(self, day, kind):
        self.day, self.kind = day, kind

    @property
    def artifact_ref(self):
        class Ref:
            def model_dump_json(inner):
                return json.dumps(dict(day=self.day.isoformat(), kind=self.kind))
        return Ref()


def test_acquisition_uses_the_monthly_source_for_2018_02_08_only(monkeypatch):
    from packages.alpha_lifecycle import acquisition
    from packages.alpha_lifecycle.github_research import GithubResearchBatchRequest
    from packages.engine_contracts.serialization import canonical_json_bytes
    from scripts.prepare_p3_github_research import acquire
    calls = []
    monkeypatch.setattr(acquisition, 'acquire_day_receipt',
                        lambda day, transport, store: calls.append((day, transport)) or Receipt(day, 'daily'))
    monkeypatch.setattr(acquisition, 'acquire_monthly_exception',
                        lambda day, transport, store: calls.append((day, transport)) or Receipt(day, 'monthly'))
    request = GithubResearchBatchRequest.model_validate_json(canonical_json_bytes(_request()))
    references = acquire(request, store=None, transport_factory=lambda monthly_exception: monthly_exception)
    assert len(references) == 2800 and references[0]['day'] == '2018-01-01' and references[-1]['day'] == '2025-08-31'
    monthly = [ref['day'] for ref in references if ref['kind'] == 'monthly']
    assert monthly == ['2018-02-08']
    assert all(transport is (day == date(2018, 2, 8)) for day, transport in calls)


@pytest.fixture
def producer(protected, monkeypatch, tmp_path):
    import os
    import pwd
    from packages.alpha_lifecycle import github_research, github_run
    from scripts import prepare_p3_github_research as module
    monkeypatch.setattr(os, 'geteuid', lambda: 985)
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: type('User', (), dict(pw_name='ta-p3-producer'))())
    monkeypatch.setattr('scripts.seal_p3_dataset._committed_source', lambda: SOURCE)
    monkeypatch.setattr(github_run, '_current_source', lambda root: SOURCE)
    monkeypatch.setattr(github_research, 'RESEARCH_BASE', tmp_path)
    request = _request()
    protected[github_research.REQUEST_PATH] = request
    from tests.p3.test_github_research import _digest, _review
    protected[github_research.REQUEST_REVIEW_PATH] = _review([request['digest']])
    roots = github_research.research_roots(7, 1)
    for key in ('work', 'backup'):
        roots[key].mkdir(parents=True)
    produced = []
    monkeypatch.setattr(module, 'produce', lambda *args, **kwargs: produced.append(args) or dict(proposal=True))

    def run(mode='GITHUB', operation='p3-research-inputs-v1', pin=None):
        _publish_run(protected, mode=mode, operation=operation, research_inputs_sha256=pin or _digest(request))
        protected[github_run.github_run_directory(7, 1)/'source-ready.json'] = _source_ready(mode)
        monkeypatch.setattr(github_run, '_now', lambda now: NOW if now is None else now)
        return module.main(['--run-id', '7', '--attempt', '1'])
    return run, produced, roots, monkeypatch


def _source_ready(mode):
    import hashlib
    from packages.engine_contracts.serialization import canonical_json_bytes
    value = dict(schema_version='p3-github-source-ready-v1', mode=mode,
                 identity=dict(issuer_kind='GITHUB', issuer_identity='p3-authority',
                               repository='nam176hermes/Trading-Agent', workflow_ref='p3-authority.yml',
                               run_id=7, attempt=1),
                 source=SOURCE, project_status_sha256='9' * 64, observed_at=_z(NOW), authority=AUTHORITY)
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return value


def test_producer_runs_only_in_its_genuine_research_run(producer, capsys):
    run, produced, _, _ = producer
    assert run() == 0 and len(produced) == 1
    assert json.loads(capsys.readouterr().out) == dict(proposal=True)


@pytest.mark.parametrize('fault', ['fake', 'operation', 'pin', 'root', 'other_user', 'dirty_custody'])
def test_producer_refuses_anything_but_a_fresh_genuine_run_as_its_own_user(producer, fault):
    import os
    import pwd
    from packages.alpha_lifecycle.github_run import GithubRunHeld
    run, produced, roots, monkeypatch = producer
    options = {}
    if fault == 'fake':
        options['mode'] = 'FAKE'
    elif fault == 'operation':
        options['operation'] = 'p3-baselines-v1'
    elif fault == 'pin':
        options['pin'] = '0' * 64
    elif fault == 'root':
        monkeypatch.setattr(os, 'geteuid', lambda: 0)
    elif fault == 'other_user':
        monkeypatch.setattr(pwd, 'getpwuid', lambda uid: type('User', (), dict(pw_name='ta-local-worker'))())
    elif fault == 'dirty_custody':
        (roots['work']/'stale.blob').write_bytes(b'x')
    with pytest.raises((GithubRunHeld, PermissionError)):
        run(**options)
    assert not produced


def test_environment_identity_builder_matches_the_existing_environment_validator():
    from pathlib import Path
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_SHA256, SANDBOX_PROFILE_V2_SHA256
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.local_pre_p3 import build_environment_identity, validate_local_environment
    from tests.p3.test_github_run import Store
    root = Path(__file__).resolve().parents[2]
    store = Store()
    environment = build_environment_identity(root, sandbox_policy_digest=SANDBOX_PROFILE_SHA256)
    ref = store.put(json.loads(canonical_json_bytes(environment)))
    from packages.data_contracts import ArtifactRefV1
    validate_local_environment(root, ArtifactRefV1.model_validate(ref), store=store)
    assert build_environment_identity(root, sandbox_policy_digest=SANDBOX_PROFILE_V2_SHA256).sandbox_policy_digest \
        == SANDBOX_PROFILE_V2_SHA256
