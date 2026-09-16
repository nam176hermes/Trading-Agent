from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from packages.job_contracts import EnqueueJobRequest
from services.job_store.records import EnqueueOutcome
from tests.jobs.test_repository_transition_capabilities import (
    _Connection,
    _job_row,
    _repository,
)


def test_paper_projection_does_not_import_session_authority(tmp_path):
    from packages.runtime_release.v2 import PAPER_APPLICATION_SOURCE_MAPPING
    root = Path(__file__).resolve().parents[2]
    for destination, source in PAPER_APPLICATION_SOURCE_MAPPING:
        path = tmp_path/destination
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root/source, path)
    result = subprocess.run([sys.executable, '-I', '-B', '-c',
        'import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
        'import apps.job_api.app; '
        'assert "services.job_store.p3_catalog" not in sys.modules; '
        'assert all(Path(m.__file__).is_relative_to(sys.argv[1]) for n,m in sys.modules.items() '
        'if n.split(".")[0] in ("apps","services","packages") and getattr(m,"__file__",None))', str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def _alpha_request() -> EnqueueJobRequest:
    def ref(value: str) -> dict[str, object]:
        return {
            "content_sha256": value * 64,
            "size_bytes": 1,
            "media_type": "application/json",
            "locator": f"{value * 64}.blob",
        }
    return EnqueueJobRequest.model_validate({
        "job_type": "ALPHA_CAMPAIGN",
        "payload": {
            "schema_version": "p3-alpha-campaign-payload-v1",
            "operation": "BASELINES",
            "manifest_ref": ref("a"),
            "authorization_ref": ref("b"),
            "expected_source": {
                "commit_sha": "c" * 40,
                "tree_sha": "d" * 40,
                "closure_schema_version": "source-closure-v1",
                "closure_policy_sha256": "e" * 64,
                "closure_sha256": "f" * 64,
            },
            "logical_trial_id": "p3-baselines-v1",
        },
        "idempotency_key": "p3:baselines:1",
        "actor": {"actor_type": "OPERATOR", "actor_id": "operator-p3"},
    })


def test_job_api_routes_alpha_campaign_to_the_scoped_sql_capability() -> None:
    request = _alpha_request()
    connection = _Connection(
        {"job_id": "job_fixed", "outcome": "ENQUEUED"},
        _job_row(request),
    )
    repository = _repository(connection)
    generated = iter(("job_fixed", "event_fixed"))
    repository._new_id = lambda _prefix: next(generated)

    result = repository.enqueue(request, trace_id="p3:enqueue")

    assert result.outcome is EnqueueOutcome.ENQUEUED
    assert result.job.job_id == "job_fixed"
    assert "job_plane.api_enqueue_alpha_campaign" in connection.calls[0][0]
    assert "p_payload_text" not in connection.calls[0][0]


@pytest.mark.parametrize('fault', [None, 'revision', 'catalog', 'role', 'restricted', 'paper'])
def test_session_job_api_rechecks_catalog_before_enqueue(fault):
    from apps.job_api.app import create_p3_session_app
    from apps.job_api.config import JobApiSettings
    from services.job_store.p3_catalog import SESSION_CATALOG_SHA256, SESSION_REVISION
    from tests.jobs.test_job_api import Repository, _ReadyResult, isolated_job_plane_authority, TOKEN, AUTH, PRINCIPAL
    identity = dict(current_user='trading_job_api', session_user='trading_job_api',
        version_num=SESSION_REVISION, restricted=True)
    catalog = {'catalog_sha256': SESSION_CATALOG_SHA256}
    class Connection:
        @contextmanager
        def transaction(self):
            yield
        def execute(self, query):
            if 'AS restricted' in query:
                return _ReadyResult(dict(identity))
            if 'catalog_sha256' in query:
                return _ReadyResult(dict(catalog))
            if 'version_num' in query:
                return _ReadyResult({'version_num': identity['version_num']})
            return _ReadyResult((1,))
    class Pool:
        @contextmanager
        def connection(self):
            yield Connection()
    repository = Repository()
    repository._pool = Pool()
    request = _alpha_request()
    repository.jobs[0] = replace(repository.jobs[0], job_type=request.job_type, payload=request.payload)
    settings = JobApiSettings(bearer_token=TOKEN, principal=PRINCIPAL)
    api = TestClient(create_p3_session_app(settings, repository, isolated_job_plane_authority()))
    # Drift after application construction must be checked again on the request.
    if fault == 'revision':
        identity['version_num'] = '0029_p3_session_holdout_claim'
    elif fault == 'role':
        identity['session_user'] = 'trading_owner'
    elif fault == 'restricted':
        identity['restricted'] = False
    elif fault == 'catalog':
        catalog['catalog_sha256'] = '0'*64
    body = request.model_dump(mode='json', exclude={'actor'})
    if fault == 'paper':
        body.update(job_type='SNAPSHOT', payload={'scope': 'default', 'requested_as_of': None})
    result = api.post('/v1/jobs', json=body, headers=AUTH)
    assert result.status_code == (201 if fault is None else 422 if fault == 'paper' else 503)
    assert (repository.last_enqueue is not None) == (fault is None)


def test_ordinary_p3_api_stays_on_its_own_revision():
    from apps.job_api.app import create_p3_app
    from apps.job_api.config import JobApiSettings
    from services.job_store.p3_catalog import SESSION_REVISION
    from tests.jobs.test_job_api import Repository, _ReadyResult, isolated_job_plane_authority, TOKEN, AUTH, PRINCIPAL
    class Connection:
        def execute(self, query):
            return _ReadyResult({'version_num': SESSION_REVISION} if 'version_num' in query else (1,))
    class Pool:
        @contextmanager
        def connection(self):
            yield Connection()
    repository = Repository(); repository._pool = Pool()
    api = TestClient(create_p3_app(JobApiSettings(bearer_token=TOKEN, principal=PRINCIPAL),
        repository, isolated_job_plane_authority()))
    result = api.post('/v1/jobs', json=_alpha_request().model_dump(mode='json', exclude={'actor'}), headers=AUTH)
    assert result.status_code == 503 and repository.last_enqueue is None
