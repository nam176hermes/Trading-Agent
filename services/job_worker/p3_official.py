"""One workflow-bound P3 attempt after protected startup; no import-time action."""
from pathlib import Path
import re

from packages.alpha_lifecycle.authority import WORKFLOW_OPERATIONS
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.runtime_release.config import _absolute,read_protected_canonical_json_current
from scripts.p3_authority import read_enqueued_job
from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from .command_registry import attest_worker_runtime_authority
from .p3_host_profile import read_official_profile
from .p3_spawn import P3SpawnProvider
from .p3_startup import probe_p3_startup


def run_official_once(values,*,build_worker):
    operation=values.get('P3_OPERATION')
    if WORKFLOW_OPERATIONS.get(operation) not in {'BASELINES','REGISTER_FAMILY','OOS'}:
        raise ValueError('P3 official operation is not implemented')
    run_id,attempt=(values.get(name,'') for name in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT'))
    if any(re.fullmatch('[1-9][0-9]{0,19}',value) is None for value in (run_id,attempt)):
        raise ValueError('P3 official workflow identity is invalid')
    paths={name:_absolute(values.get(name)) for name in (
        'P3_AUTHORITY_REQUEST_FILE','P3_PREFLIGHT_DIRECTORY','P3_ARTIFACT_ROOT','P3_MANIFEST_FILE','P3_REVIEW_FILE')}
    job_id,body,_=read_enqueued_job(paths['P3_AUTHORITY_REQUEST_FILE'],paths['P3_PREFLIGHT_DIRECTORY'],operation,
        artifact_root=paths['P3_ARTIFACT_ROOT'],manifest_file=paths['P3_MANIFEST_FILE'],review_file=paths['P3_REVIEW_FILE'])
    path=Path(f'/run/trading-agent-p3/{run_id}-{attempt}/profile.json')
    _,digest=read_protected_canonical_json_current(path)
    profile,closure=read_official_profile(path,digest,job_id=job_id,payload=body.payload,context=values)
    if Path(profile.store_root)!=paths['P3_ARTIFACT_ROOT']:
        raise ValueError('P3 official retained store differs from enqueue')
    if values.get('CREDENTIALS_DIRECTORY')!=profile.worker_credentials_directory:
        raise ValueError('P3 official credential directory differs from its launcher')
    probe_p3_startup(closure,private_root=Path(profile.output_root))

    def attest_closure():
        current=read_official_profile(path,digest,job_id=job_id,payload=body.payload,context=values)
        if current!=(profile,closure):
            raise ValueError('P3 official profile or closure changed after startup')
        return closure

    attest_closure()
    authority=attest_worker_runtime_authority()
    if authority.application_revision!=closure.source.commit_sha:
        raise ValueError('P3 official worker revision differs from its closure')
    settings=JobStoreSettings.from_systemd_credentials(
        {'CREDENTIALS_DIRECTORY':profile.worker_credentials_directory},expected_user='trading_job_worker')
    provider=P3SpawnProvider(attest_closure=attest_closure,store=LocalArtifactStore(Path(profile.store_root)),
        store_root=Path(profile.store_root),output_root=Path(profile.output_root))
    attest_closure()
    with WorkerRepository(settings) as repository:
        repository.assert_p3_runtime_identity()
        worker=build_worker(repository,values,authority=authority,p3_spawn_provider=provider,p3_job_id=job_id)
        # Processing an attempt is not research success; workflow wait must read
        # its canonical terminal Job API result and retained operation receipt.
        return 0 if worker.run_once() else 2
