"""Task-owned root controller for the hosted (GITHUB) P3 lane; orchestration only.

The GitHub job holds no authority. This controller observes a dispatched run through the
GitHub REST API, binds a just-in-time runner carrying a per-run label, publishes the
protected run attestation that worker-side consumers trust, admits exactly one reviewed
operation through the existing primitives, and publishes only public-safe results to the
run's outbox. It never retries a research job, extends a deadline or replays a holdout.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Annotated, Any, Literal
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import Field  # noqa: E402

from packages.alpha_lifecycle.contracts.base import StrictModel, Sha256  # noqa: E402
from packages.alpha_lifecycle.github_run import (  # noqa: E402
    REPOSITORY, SOURCE_READY_NAME, WORKFLOW_PATH, ExpectedRun, GithubRunAttestation, GithubRunHeld,
    GithubSourceReady, authenticate_run, github_run_directory,
)
from packages.engine_contracts.serialization import canonical_json_bytes  # noqa: E402

API = 'https://api.github.com'
OUTBOX_NAMES = frozenset({'status.json', 'p3-phase-exit-v2.json', 'p3-phase-exit-v1.json'})
REFRESH_SECONDS = 60
_HEX40 = r'^[0-9a-f]{40}$'


class ControllerHeld(RuntimeError):
    """A precondition, binding or authority check failed; nothing further is admitted."""


class ControllerPlan(StrictModel):
    """Root-protected campaign plan; the controller reads nothing authority-bearing elsewhere."""
    schema_version: Literal['p3-github-controller-plan-v1']
    mode: Literal['GITHUB', 'FAKE']
    head_sha: Annotated[str, Field(pattern=_HEX40)]
    actor: Annotated[str, Field(pattern=r'^[A-Za-z0-9-]{1,39}$')]
    research_inputs_sha256: Sha256
    runner_root: str
    runner_user: Annotated[str, Field(pattern=r'^[a-z_][a-z0-9_-]{0,31}$')]
    outbox_root: str
    token_file: str


class GitHubApi:
    """Bounded REST client; the token file is root-only and never leaves this process."""

    def __init__(self, token_file: Path, *, base: str = API, timeout: float = 30):
        self._token_file, self._base, self._timeout = token_file, base, timeout

    def _token(self) -> str:
        info = os.stat(self._token_file, follow_symlinks=False)
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise ControllerHeld('GitHub token file must be root-only')
        return self._token_file.read_text().strip()

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> bytes:
        request = Request(self._base + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self._token(), 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        with urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - fixed https base
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ControllerHeld('GitHub API response exceeds 1 MiB')
        return raw

    def get(self, path: str) -> bytes:
        return self._call('GET', path)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        value = json.loads(self._call('POST', path, body))
        if not isinstance(value, dict):
            raise ControllerHeld('GitHub API POST response is not an object')
        return value

    def delete(self, path: str) -> None:
        self._call('DELETE', path)


def _json(api, path: str) -> dict[str, Any]:
    value = json.loads(api.get(path))
    if not isinstance(value, dict):
        raise ControllerHeld('GitHub API response is not an object')
    return value


def precheck_queued(api, plan: ControllerPlan, operation: str) -> tuple[int, int]:
    """The single queued run of exactly this dispatch; anything ambiguous is HELD."""
    runs = _json(api, f'/repos/{REPOSITORY}/actions/workflows/p3-authority.yml/runs'
                      '?status=queued&event=workflow_dispatch&branch=main&per_page=20').get('workflow_runs')
    if not isinstance(runs, list):
        raise ControllerHeld('queued run listing is invalid')
    matching = [run for run in runs if isinstance(run, dict) and run.get('display_title') == f'p3 {operation}']
    if len(matching) != 1 or len(runs) != 1:
        raise ControllerHeld('exactly one queued p3-authority run is required')
    run = matching[0]
    actor = (run.get('actor') or {}).get('login')
    if (run.get('path') != WORKFLOW_PATH or run.get('head_branch') != 'main' or run.get('event') != 'workflow_dispatch'
            or run.get('head_sha') != plan.head_sha or actor != plan.actor
            or (run.get('triggering_actor') or {}).get('login') != plan.actor
            or type(run.get('id')) is not int or type(run.get('run_attempt')) is not int):
        raise ControllerHeld('queued run differs from the frozen protected-main dispatch')
    github_run_directory(run['id'], run['run_attempt'])
    return run['id'], run['run_attempt']


def check_repository_guards(api, plan: ControllerPlan) -> None:
    """F-02/F-05: public-repo runner isolation settings and the main freeze, before any runner."""
    approval = _json(api, f'/repos/{REPOSITORY}/actions/permissions/fork-pr-contributor-approval')
    if approval.get('approval_policy') != 'all_external_contributors':
        raise ControllerHeld('fork pull-request workflows must require approval for all external contributors')
    branch = _json(api, f'/repos/{REPOSITORY}/branches/main')
    if (branch.get('commit') or {}).get('sha') != plan.head_sha or branch.get('protected') is not True:
        raise ControllerHeld('main moved or is unprotected; the campaign source is frozen')


def runner_label(run_id: int, attempt: int) -> str:
    github_run_directory(run_id, attempt)
    return f'p3-run-{run_id}-{attempt}'


def create_jit_runner(api, run_id: int, attempt: int) -> tuple[str, int, str]:
    """(runner name, runner id, encoded JIT config); the config is a one-job credential."""
    name = f'p3-jit-{run_id}-{attempt}'
    value = api.post(f'/repos/{REPOSITORY}/actions/runners/generate-jitconfig', dict(
        name=name, runner_group_id=1, labels=['self-hosted', 'p3-authority', runner_label(run_id, attempt)],
        work_folder='_work'))
    runner, config = value.get('runner') or {}, value.get('encoded_jit_config')
    if runner.get('name') != name or type(runner.get('id')) is not int or not isinstance(config, str):
        raise ControllerHeld('just-in-time runner registration differs')
    return name, runner['id'], config


@dataclass
class Primitives:
    """Host steps executed through the existing lane-generic commands; injected in tests."""
    publish: Callable[[Path, bytes, bool], None]
    revoke: Callable[[Path], None]
    host_identity: Callable[[], tuple[str, Any]]
    start_runner: Callable[[ControllerPlan, str], Any]
    stop_runner: Callable[[Any], None]
    admit: Callable[[GithubRunAttestation, str], dict[str, Any]]
    execute: Callable[[GithubRunAttestation, str, dict[str, Any]], dict[str, Any]]
    # {source, project_status_sha256} for the protected-main checkout; see observe_source_ready.
    source_ready: Callable[[ControllerPlan], dict[str, Any]]
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def observe_source_ready(root: Path, head_sha: str) -> dict[str, Any]:
    """Host primitive: derive the attested status of the protected-main checkout `root`.

    Runs only in the root controller, which has the GitHub origin, `gh` and token that a staged
    role process lacks (D-S7-02); consumers then check the published record offline.
    """
    from packages.pre_p3_provenance import canonical_source_identity
    from packages.project_status import derive_project_status
    status = derive_project_status(root)
    source = canonical_source_identity(root)
    if (source['commit_sha'] != head_sha or status['gates']['HWC_SOURCE_READY'] != 'PASS'
            or status['gates']['PRE_P3_READY'] != 'PASS' or status['p3_alpha_development_allowed'] is not True):
        raise ControllerHeld('protected-main source is not P3-ready')
    return dict(source=source, project_status_sha256=hashlib.sha256(canonical_json_bytes(status)).hexdigest())


def publish_source_ready(plan: ControllerPlan, primitives: Primitives, run: GithubRunAttestation) -> Path:
    observed = primitives.source_ready(plan)
    value = dict(schema_version='p3-github-source-ready-v1', mode=plan.mode,
                 identity=run.identity.model_dump(mode='json'), source=observed.get('source'),
                 project_status_sha256=observed.get('project_status_sha256'),
                 observed_at=primitives.now().isoformat().replace('+00:00', 'Z'),
                 authority=dict(broker=False, live=False, network=False, production=False))
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    try:
        ready = GithubSourceReady.model_validate_json(canonical_json_bytes(value))
    except ValueError as error:
        raise ControllerHeld('source-ready observation is invalid') from error
    if ready.source.commit_sha != plan.head_sha or ready.source.commit_sha != run.head_sha:
        raise ControllerHeld('source-ready observation is for another commit')
    path = github_run_directory(run.identity.run_id, run.identity.attempt)/SOURCE_READY_NAME
    primitives.publish(path, canonical_json_bytes(ready) + b'\n', True)
    return path


def attest(api, plan: ControllerPlan, primitives: Primitives, run_id: int, attempt: int, runner_name: str,
           operation: str) -> GithubRunAttestation:
    """Authenticate the in-progress run through the API and publish the protected attestation."""
    expected = ExpectedRun(head_sha=plan.head_sha, actor=plan.actor, operation=operation,
                           runner_name=runner_name, research_inputs_sha256=plan.research_inputs_sha256)
    run = authenticate_run(lambda path: api.get(path), run_id=run_id, attempt=attempt, expected=expected,
                           mode=plan.mode, host=primitives.host_identity(), now=primitives.now())
    primitives.publish(github_run_directory(run_id, attempt)/'github-run.json',
                       canonical_json_bytes(run) + b'\n', True)
    return run


def await_assignment(api, plan, primitives, run_id, attempt, runner_name, operation, *, deadline: float):
    """Wait, bounded, until the run executes on the bound runner; then attest it."""
    while time.monotonic() < deadline:
        try:
            return attest(api, plan, primitives, run_id, attempt, runner_name, operation)
        except GithubRunHeld:
            run = _json(api, f'/repos/{REPOSITORY}/actions/runs/{run_id}')
            if run.get('status') == 'completed' or run.get('run_attempt') != attempt:
                raise ControllerHeld('run ended or was re-attempted before assignment') from None
            primitives.sleep(5)
    raise ControllerHeld('run was not assigned to the bound runner in time')


class Refresher:
    """Re-authenticate and re-observe source readiness every REFRESH_SECONDS; on any failure revoke and stop."""

    def __init__(self, api, plan, primitives, run_id, attempt, runner_name, operation):
        self._arguments = (api, plan, primitives, run_id, attempt, runner_name, operation)
        self._path = github_run_directory(run_id, attempt)/'github-run.json'
        self._stop = threading.Event()
        self.failed: BaseException | None = None
        self._thread = threading.Thread(target=self._loop, name='p3-github-refresh', daemon=True)

    def _loop(self):
        while not self._stop.wait(REFRESH_SECONDS):
            try:
                api, plan, primitives, *_ = self._arguments
                publish_source_ready(plan, primitives, attest(*self._arguments))
            except BaseException as error:  # noqa: BLE001 - any failure revokes; nothing is retried
                self.failed = error
                self._arguments[2].revoke(self._path)
                return

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        # Only after the refresh thread stopped, so no refresh can republish source-ready afterwards.
        self._arguments[2].revoke(self._path.with_name(SOURCE_READY_NAME))
        self._arguments[2].revoke(self._path)


def write_outbox(plan: ControllerPlan, primitives: Primitives, run_id: int, attempt: int,
                 files: dict[str, bytes]) -> Path:
    """Only the allowlisted public-safe names; status is written last so the job waits for all."""
    if set(files) - OUTBOX_NAMES or 'status.json' not in files:
        raise ControllerHeld('outbox content is outside the public-safe allowlist')
    directory = Path(plan.outbox_root)/f'{run_id}-{attempt}'
    for name in sorted(set(files) - {'status.json'}):
        primitives.publish(directory/name, files[name], False)
    primitives.publish(directory/'status.json', files['status.json'], False)
    return directory


def status_document(operation: str, run: GithubRunAttestation, result: dict[str, Any]) -> bytes:
    outcome = result.get('outcome')
    phase_exit = result.get('phase_exit', 'NONE')
    if outcome not in {'SUCCEEDED', 'FAILED', 'BLOCKED', 'CANCELLED', 'HELD'} or phase_exit not in {'PASS', 'NONE'}:
        raise ControllerHeld('operation result is not a closed terminal outcome')
    value = dict(schema_version='p3-github-run-status-v1', operation=operation, run_id=run.identity.run_id,
        attempt=run.identity.attempt, source_commit=run.head_sha, outcome=outcome, phase_exit=phase_exit,
        job_ids=list(result.get('job_ids', ())), digests=dict(result.get('digests', {})),
        authority=dict(broker=False, live=False, network=False, production=False))
    return canonical_json_bytes(value) + b'\n'


def serve_operation(api, plan: ControllerPlan, primitives: Primitives, operation: str, *,
                    assignment_seconds: float = 600) -> dict[str, Any]:
    """One dispatched operation, end to end; every failure leaves the run HELD, never retried."""
    check_repository_guards(api, plan)
    run_id, attempt = precheck_queued(api, plan, operation)
    if operation == 'p3-holdout-primary-v1' and attempt != 1:
        raise ControllerHeld('a HOLDOUT is admitted only on the first attempt of its run')
    runner_name, runner_id, config = create_jit_runner(api, run_id, attempt)
    process = primitives.start_runner(plan, config)
    del config
    try:
        run = await_assignment(api, plan, primitives, run_id, attempt, runner_name, operation,
                               deadline=time.monotonic() + assignment_seconds)
        with Refresher(api, plan, primitives, run_id, attempt, runner_name, operation) as refresher:
            publish_source_ready(plan, primitives, run)
            return _admit_and_execute(api, plan, primitives, run, operation, refresher)
    finally:
        primitives.stop_runner(process)
        try:
            api.delete(f'/repos/{REPOSITORY}/actions/runners/{runner_id}')
        except Exception:  # noqa: BLE001 - an ephemeral runner deregisters itself after its job
            pass


def _admit_and_execute(api, plan, primitives, run, operation, refresher) -> dict[str, Any]:
    run_id, attempt = run.identity.run_id, run.identity.attempt
    admitted = primitives.admit(run, operation)
    if refresher.failed is not None:
        raise ControllerHeld('run attestation lapsed during admission')
    result = primitives.execute(run, operation, admitted)
    if refresher.failed is not None:
        result = dict(result, outcome='HELD')
    # Phase receipts leave the host only with a SUCCEEDED, still-attested operation.
    if result.get('outcome') != 'SUCCEEDED':
        result = dict(result, files={}, phase_exit='NONE')
    files = {name: value for name, value in result.get('files', {}).items()}
    files['status.json'] = status_document(operation, run, result)
    write_outbox(plan, primitives, run_id, attempt, files)
    return dict(run_id=run_id, attempt=attempt, outcome=result['outcome'],
                status_sha256=hashlib.sha256(files['status.json']).hexdigest())


__all__ = ['ControllerHeld', 'ControllerPlan', 'GitHubApi', 'Primitives', 'Refresher', 'attest',
           'observe_source_ready', 'publish_source_ready',
           'check_repository_guards', 'create_jit_runner', 'precheck_queued', 'serve_operation',
           'status_document', 'write_outbox']
