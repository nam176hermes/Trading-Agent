"""One workflow-bound P3 attempt after protected startup; no import-time action."""
from pathlib import Path
from functools import partial

from packages.alpha_lifecycle.authority import WORKFLOW_OPERATIONS, validate_request, local_run_directory
from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity,LocalRunAuthorization,RunAuthorization
from packages.alpha_lifecycle.github_run import github_run_directory,github_run_identity
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.pre_p3_provenance import canonical_source_identity
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.runtime_release.config import _absolute,read_protected_canonical_json_current
from scripts.p3_authority import read_enqueued_job
from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from .command_registry import attest_worker_runtime_authority
from .p3_host_profile import read_official_profile, LocalOfficialHostProfileLane
from .p3_spawn import P3SpawnProvider
from .p3_startup import probe_p3_startup


def run_official_once(values,*,build_worker):
    operation=values.get('P3_OPERATION')
    if WORKFLOW_OPERATIONS.get(operation) not in {'BASELINES','REGISTER_FAMILY','OOS','HOLDOUT'}:
        raise ValueError('P3 official operation is not implemented')
    lane=values.get('P3_ISSUER_KIND','GITHUB')
    if lane not in {'LOCAL','GITHUB'}:
        raise ValueError('P3 issuer lane is invalid')
    local = lane=='LOCAL'
    paths={name:_absolute(values.get(name)) for name in (
        'P3_AUTHORITY_REQUEST_FILE','P3_PREFLIGHT_DIRECTORY','P3_ARTIFACT_ROOT','P3_MANIFEST_FILE','P3_REVIEW_FILE')}
    # Both lanes admit the protected request first; the run directory is derived from
    # its authenticated authorization, never from GITHUB_* variables.
    root=Path(__file__).resolve().parents[2]
    request=validate_request(paths['P3_AUTHORITY_REQUEST_FILE'],
        SourceIdentity.model_validate(canonical_source_identity(root)),WORKFLOW_OPERATIONS[operation])
    if local:
        identity=getattr(request.authorization,'identity',None)
        if not isinstance(identity,LocalRunIdentity):
            raise ValueError('local launcher requires protected local operation authority')
        directory=local_run_directory(identity)
    else:
        if not isinstance(request.authorization,RunAuthorization):
            raise ValueError('GitHub launcher requires protected official run authority')
        identity=github_run_identity(request.authorization)
        directory=github_run_directory(identity.run_id,identity.attempt)
    job_id,body,_=read_enqueued_job(paths['P3_AUTHORITY_REQUEST_FILE'],paths['P3_PREFLIGHT_DIRECTORY'],operation,
        artifact_root=paths['P3_ARTIFACT_ROOT'],manifest_file=paths['P3_MANIFEST_FILE'],review_file=paths['P3_REVIEW_FILE'])
    if directory is None:
        raise ValueError('P3 protected run directory is unavailable')
    path=directory/'profile.json'
    _,digest=read_protected_canonical_json_current(path)
    profile,closure=read_official_profile(path,digest,job_id=job_id,payload=body.payload,admission=True)
    if local != isinstance(profile,LocalOfficialHostProfileLane):
        raise ValueError('P3 launcher and protected profile issuer lanes differ')
    if Path(profile.store_root)!=paths['P3_ARTIFACT_ROOT']:
        raise ValueError('P3 official retained store differs from enqueue')
    if values.get('CREDENTIALS_DIRECTORY')!=profile.worker_credentials_directory:
        raise ValueError('P3 official credential directory differs from its launcher')
    probe_p3_startup(closure,private_root=Path(profile.output_root))

    def attest_closure():
        current=read_official_profile(path,digest,job_id=job_id,payload=body.payload,admission=False)
        if current!=(profile,closure):
            raise ValueError('P3 official profile or closure changed after startup')
        return closure

    attest_closure()
    authority=attest_worker_runtime_authority()
    from .p3_local_runtime import local_runtime_guard, LocalRuntimeSafetyPreflight, refresh_local_safety_evidence
    if not isinstance(request.authorization,(LocalRunAuthorization,RunAuthorization)):
        raise ValueError('P3 official authorization is unavailable')
    # Staging-scope runtime approval and the six-second safety refresh apply to both lanes.
    runtime_guard=local_runtime_guard(authority,identity,closure.source,request.authorization.expires_at)
    safety_preflight=LocalRuntimeSafetyPreflight(authority,identity,
        closure.source,request.authorization.expires_at,
        runtime_guard=runtime_guard,
        refresh_dynamic_evidence=partial(refresh_local_safety_evidence,authority,identity))
    safety_preflight()
    if authority.application_revision!=closure.source.commit_sha:
        raise ValueError('P3 official worker revision differs from its closure')
    if WORKFLOW_OPERATIONS.get(operation) == 'HOLDOUT':
        # Both lanes: the worker records its own process before SQL and waits for the root
        # controller to bind exactly this process into the protected session.
        from .p3_session import await_session_owner
        owner_preflight = safety_preflight
        def owner_guard():
            attest_closure()
            owner_preflight()
        await_session_owner(profile, closure, directory=directory, identity=identity, host_profile_digest=digest,
            expires_at=request.authorization.expires_at, guard=owner_guard)
    settings=JobStoreSettings.from_systemd_credentials(
        {'CREDENTIALS_DIRECTORY':profile.worker_credentials_directory},expected_user='trading_job_worker')
    provider=P3SpawnProvider(attest_closure=attest_closure,store=LocalArtifactStore(Path(profile.store_root)),
        store_root=Path(profile.store_root),output_root=Path(profile.output_root))
    attest_closure()
    with WorkerRepository(settings) as repository:
        if WORKFLOW_OPERATIONS.get(operation) == 'HOLDOUT':
            return run_session(values, repository=repository, profile=profile, closure=closure,
                authority=authority, build_worker=build_worker, runtime_guard=runtime_guard,
                directory=directory, identity=identity)
        # One campaign ledger at the session catalog serves every official operation.
        repository.assert_session_runtime_identity()
        worker=build_worker(repository,values,authority=authority,p3_spawn_provider=provider,p3_job_id=job_id,
            p3_safety_preflight=safety_preflight)
        # Processing an attempt is not research success; workflow wait must read
        # its canonical terminal Job API result and retained operation receipt.
        return 0 if worker.run_once() else 2


def run_session(values, *, repository: WorkerRepository, profile, closure, authority, build_worker, runtime_guard=None,
                directory: Path, identity) -> int:
    """Consume three independently approved jobs in one bounded process lifetime.

    Protected stage profiles are supplied by the existing host authority. Waiting
    never creates a job, review, profile or second disclosure request.
    """
    import time
    from .p3_session import P3HoldoutSession, SessionStageProfile, _STAGES, session_identity
    from packages.runtime_release.config import ProtectedAuthorityError
    from .recovery import ProcProcessInspector
    from services.job_store.records import ClaimedJob
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.job_contracts import JobState
    from .p3_host_profile import OFFICIAL_PROFILE, LocalOfficialHostProfileLane, read_official_profile
    from .p3_spawn import P3SpawnProvider
    from .worker import WORKER_LEASE_SECONDS
    path = directory/'session.json'
    store = LocalArtifactStore(Path(profile.store_root))
    with P3HoldoutSession(path, store=store) as session:
        safety_preflight=None
        if runtime_guard is not None:
            # Preserve the startup digest for the whole session; stage-specific
            # approvals keep their own expiry checks in the existing fences.
            runtime_guard=partial(runtime_guard,deadline=session.profile.workflow_deadline)
            runtime_guard()
            from .p3_local_runtime import LocalRuntimeSafetyPreflight, refresh_local_safety_evidence
            safety_preflight=LocalRuntimeSafetyPreflight(authority,identity,closure.source,
                session.profile.workflow_deadline,
                runtime_guard=runtime_guard,
                refresh_dynamic_evidence=partial(refresh_local_safety_evidence,authority,identity))
        if (session.profile.source != closure.source or session.profile.environment_ref != profile.environment_ref
            or session.profile.closure_sha256 != closure.closure_sha256):
            raise ValueError('session differs from admitted startup closure')
        worker = None
        for operation in _STAGES:
            # Keep the released bytes in this owner while the host independently
            # admits the next exact job, using prior result/commitment references.
            if operation != 'HOLDOUT':
                if worker is None:
                    raise ValueError('session lost its preceding worker')
                while True:
                    session._check_owner()
                    worker._safety_preflight()
                    if read_protected_canonical_json_current(path)[1] != session._digest:
                        raise ValueError('session changed while waiting for next job')
                    document, digest = read_protected_canonical_json_current(path.with_name('profile.json'))
                    # Dispatch by enumeration, exactly as read_official_profile does. The
                    # previous ternary selected LOCAL only on an exact string match and sent
                    # everything else to the GITHUB model. Both models pin schema_version as
                    # a Literal, so an unknown schema still failed closed; it failed as a
                    # GITHUB field error after the wrong model had been chosen, rather than
                    # as an unrecognised profile version.
                    candidate = OFFICIAL_PROFILE.validate_json(canonical_json_bytes(document))
                    if candidate.job_id != profile.job_id:
                        if candidate.payload.operation != operation:
                            raise ValueError('protected host selected an out-of-order job')
                        profile, closure = read_official_profile(path.with_name('profile.json'), digest,
                            job_id=candidate.job_id, payload=candidate.payload, admission=True)
                        break
                    time.sleep(0.25)
            _, digest = read_protected_canonical_json_current(path.with_name('profile.json'))
            def attest():
                current = read_official_profile(path.with_name('profile.json'), digest,
                    job_id=profile.job_id, payload=profile.payload, admission=False)
                if current != (profile, closure):
                    raise ValueError('session stage closure changed')
                return closure
            provider = P3SpawnProvider(attest_closure=attest, store=store, store_root=Path(profile.store_root),
                output_root=Path(profile.output_root), holdout_inputs=session.spawn_inputs)
            safety_kwargs=({'p3_safety_preflight':safety_preflight} if safety_preflight is not None
                else {'p3_runtime_guard':runtime_guard})
            worker = build_worker(repository, values, authority=authority, p3_spawn_provider=provider,
                p3_job_id=profile.job_id, p3_session=session,**safety_kwargs)
            workflow = session_identity(session.profile)
            repository.assert_session_runtime_identity()
            worker._safety_preflight()
            repository.recover_expired_leases(ProcProcessInspector(), alpha_campaign=True, fixture_only=False,
                job_id=profile.job_id, session_workflow=workflow if operation == 'HOLDOUT' else None)
            if operation=='HOLDOUT':
                if isinstance(workflow,LocalRunIdentity):
                    claim=repository.claim_local_holdout(worker._worker_id,WORKER_LEASE_SECONDS,'session:claim',
                        job_id=profile.job_id,identity=workflow)
                else:
                    claim=repository.claim_session_holdout(worker._worker_id,WORKER_LEASE_SECONDS,'session:claim',
                        job_id=profile.job_id,workflow_run_id=workflow[0],workflow_attempt=workflow[1])
            else:
                claim=repository.claim_next_alpha_campaign(worker._worker_id,WORKER_LEASE_SECONDS,
                    'session:claim',fixture_only=False,job_id=profile.job_id)
            if claim is None:
                raise ValueError('session exact job is unavailable; no experiment retry')
            def fence(claim: ClaimedJob = claim, current_worker=worker):
                session._check_owner()
                repository.assert_session_runtime_identity()
                if current_worker is None:
                    raise ValueError('session lost its worker')
                current_worker._safety_preflight()
                state = repository.session_attempt_state(claim)
                if state is None or state[1] not in ('CLAIMED', 'RUNNING'):
                    raise ValueError('session attempt lost its active state')
                control = repository.pre_spawn_control if state[1] == 'CLAIMED' else repository.heartbeat_control
                if control(claim.job_id, claim.attempt_id, claim.worker_id, claim.lease_token,
                    WORKER_LEASE_SECONDS, alpha_campaign=True,
                    session_workflow=workflow if operation == 'HOLDOUT' else None) != 'CONTINUE':
                    raise ValueError('session cancelled, expired or lost its lease')
            try:
                # The protected owner binds the freshly returned attempt/token
                # hash. A preceding stage profile is never accepted for this job.
                while True:
                    fence()
                    try:
                        document, _ = read_protected_canonical_json_current(path.with_name('stage.json'))
                    except ProtectedAuthorityError:
                        if path.with_name('stage.json').exists():
                            raise
                        time.sleep(0.25)
                        continue
                    stage = SessionStageProfile.model_validate_json(canonical_json_bytes(document))
                    if len(stage.attempts) == len(session._attempts)+1:
                        break
                    if tuple(stage.attempts) != tuple(session._attempts):
                        raise ValueError('protected stage history differs')
                    time.sleep(0.25)
                with session.stage(claim, fence=fence):
                    worker.run_once(session_claim=claim)
                    if not session.completed_jobs or session.completed_jobs[-1] != claim.job_id:
                        raise ValueError('session stage did not confirm its durable result')
            except BaseException:
                session.close()
                # Never guess success or rerun a computation after an uncertain
                # write. Fresh exact SQL state permits terminal cleanup only.
                state = repository.session_attempt_state(claim)
                if state is not None and state[1] in ('CLAIMED', 'RUNNING'):
                    cancelled = state[0] is JobState.CANCEL_REQUESTED
                    repository.finalize_execution(claim, expected_state=state[0], expected_attempt_outcome=state[1],
                        final_state=JobState.CANCELLED if cancelled else JobState.BLOCKED,
                        reason_code='CANCELLED' if cancelled else 'P3_AUTHORITY_HELD', trace_id='session:held',
                        outcome=None, result=None, stream_artifacts=(), alpha_campaign=True,
                        session_workflow=workflow if operation == 'HOLDOUT' else None)
                raise
        return 0
