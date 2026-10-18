"""v2 host profiles (LOCAL or GITHUB lane) from the mounted image and an authenticated queued job.

Private CAS/preflight access stays under the worker UID. Only root publishes the
profile; the existing worker consumer still validates it before SQL admission.
"""
import json
import os
import sys
from pathlib import Path
import time
from typing import Literal
from datetime import UTC, datetime

from pydantic import model_validator

from packages.alpha_lifecycle.contracts.base import StrictModel, Sha256
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.alpha_lifecycle.replica_store import _read
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import EnqueueJobBody
from packages.runtime_release.config import _absolute, read_protected_canonical_json_current
from services.job_store.records import validate_p3_job_id
from services.job_worker.p3_holdout_release import CustodianEndpoint
from services.job_worker.p3_host_profile import (
    OFFICIAL_PROFILE, LocalOfficialHostProfileV2, OfficialHostProfileV2, _HostFiles,
)


class HostInputs(StrictModel):
    schema_version: Literal['p3-local-host-inputs-v1']
    store_root: str
    output_root: str
    worker_credentials_directory: str
    job_api_token_file: str
    qualification_job_detail_ref: ArtifactRefV1
    postgres_binary_sha256: Sha256
    custodian_endpoint: CustodianEndpoint | None = None

    @model_validator(mode='after')
    def _paths(self):
        for value in (self.store_root, self.output_root,
                      self.worker_credentials_directory, self.job_api_token_file):
            _absolute(value)
        return self


def as_worker(uid, gid, deadline, environment, command):
    """Bounded private-store access; never change the root controller's credentials."""
    from scripts.p3_local_transport import (
        _process_start_time, drop_to_worker, _read_bounded, reap_with_escalation,
    )
    if os.geteuid() != 0 or uid <= 0 or gid <= 0 or time.monotonic() >= deadline:
        raise ValueError('host producer requires root, a worker UID and a current deadline')
    from services.job_worker.engine_spawn import _sealed_memfd
    raw = canonical_json_bytes(command)
    if len(raw) > 4194304:
        raise ValueError('host producer input exceeds bound')
    parent = (os.getpid(), _process_start_time(os.getpid()))
    input_fd = _sealed_memfd('p3-host-input', raw, mode=0o400)
    read, write = os.pipe()
    try:
        child = os.fork()
    except OSError:
        for fd in (input_fd, read, write):
            os.close(fd)
        raise
    if child == 0:
        status = 1
        try:
            os.dup2(input_fd, 0)
            os.dup2(write, 1)
            null_fd = os.open('/dev/null', os.O_WRONLY)
            os.dup2(null_fd, 2)
            for name in os.listdir('/proc/self/fd'):
                fd = int(name)
                if fd > 2:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            drop_to_worker(uid, gid, parent)
            os.chdir(environment['P3_APPLICATION_ROOT'])
            os.execve(environment['P3_APPLICATION_PYTHON'],
                [environment['P3_APPLICATION_PYTHON'], '-I', '-B', '-m', 'scripts.p3_local_host'],
                environment)
        except Exception:
            # Never put private artifacts, credential or SQL errors in the pipe/log.
            pass
        finally:
            os._exit(status)
    os.close(write)
    os.close(input_fd)
    try:
        raw, truncated = _read_bounded(read, deadline)
    finally:
        os.close(read)
        reaped = reap_with_escalation(child, deadline)
    if truncated or reaped['status'] != 0 or reaped.get('escalation'):
        raise ValueError('worker-owned host preparation failed; no launch admitted')
    return json.loads(raw)


def checked_enqueue(settings, environment):
    """Run under the worker UID using the same inputs as the official entrypoint."""
    from scripts.p3_authority import read_enqueued_job
    job_id, body, operator = read_enqueued_job(
        Path(environment['P3_AUTHORITY_REQUEST_FILE']), Path(environment['P3_PREFLIGHT_DIRECTORY']),
        environment['P3_OPERATION'], artifact_root=Path(settings.store_root),
        manifest_file=Path(environment['P3_MANIFEST_FILE']), review_file=Path(environment['P3_REVIEW_FILE']))
    validate_p3_job_id(job_id)
    store = LocalArtifactStore(Path(settings.store_root))
    intent = _read(store, body.payload.manifest_ref, P3OperationInput)
    inputs = _read(store, intent.input_set_ref, InputSet)
    return dict(job_id=job_id, body=body, operator=operator,
                environment_ref=inputs.environment_ref, integration_receipt_ref=inputs.integration_receipt_ref)


def authenticate_job(settings, observation, *, state='QUEUED'):
    from apps.job_api.contracts import JobDetailEnvelope
    from scripts.p3_authority import _request_json, _read_token, _bind_job_response
    job_id = observation['job_id']
    validate_p3_job_id(job_id)
    body = EnqueueJobBody.model_validate_json(canonical_json_bytes(observation['body']))
    detail = JobDetailEnvelope.model_validate(_request_json(
        'GET', f'/v1/jobs/{job_id}', _read_token(Path(settings.job_api_token_file))))
    _bind_job_response(detail.data.job, body, observation['operator'], job_id=job_id)
    if detail.data.job.state.value != state:
        raise ValueError('authenticated job is not in the required state')
    return body.payload


def previous_succeeded(settings, profile):
    from apps.job_api.contracts import JobDetailEnvelope
    from scripts.p3_authority import _request_json, _read_token
    from packages.job_contracts import payload_fingerprint
    validate_p3_job_id(profile.job_id)
    job = JobDetailEnvelope.model_validate(_request_json('GET', f'/v1/jobs/{profile.job_id}',
        _read_token(Path(settings.job_api_token_file)))).data.job
    if (job.job_id != profile.job_id or job.payload != profile.payload
        or job.payload_fingerprint != payload_fingerprint(profile.payload) or job.state.value != 'SUCCEEDED'):
        raise ValueError('previous exact job has no authenticated success')


def validate_transition(previous, profile):
    operations = ('p3-baselines-v1', 'p3-register-family-v1', 'p3-oos-a0-v1', 'p3-oos-a1-v1',
        'p3-oos-a2-v1', 'p3-oos-a3-v1', 'p3-select-primary-v1',
        'p3-holdout-primary-v1', 'p3-native-parity-v1', 'p3-phase-exit-v1')
    before, after = previous.payload.logical_trial_id, profile.payload.logical_trial_id
    if (before not in operations[:-1] or after != operations[operations.index(before)+1]
        or previous.job_id == profile.job_id or type(previous) is not type(profile)
        or getattr(previous, 'identity', None) != getattr(profile, 'identity', None)
        or previous.payload.expected_source != profile.payload.expected_source
        or previous.environment_ref != profile.environment_ref):
        raise ValueError('host transition is out of order or changes campaign identity')


def next_session_host(previous, job_id, payload):
    profile = type(previous).model_validate_json(canonical_json_bytes(dict(
        previous.model_dump(mode='json'), job_id=job_id, payload=payload, custodian_endpoint=None)))
    validate_transition(previous, profile)
    if payload.operation not in {'PARITY', 'PHASE_EXIT'}:
        raise ValueError('late host transition requires the existing holdout session')
    return profile


def assemble_profile(settings, identity, payload, integration_ref, closure, inventory_ref):
    if closure.source != payload.expected_source:
        raise ValueError('mounted closure source differs from admitted payload')
    # The GITHUB profile carries no identity: its run is the protected directory it lives in.
    kind = identity.get('issuer_kind') if isinstance(identity, dict) else identity.issuer_kind
    lane = (dict(schema_version='p3-official-host-profile-v2') if kind == 'GITHUB'
            else dict(schema_version='p3-local-official-host-profile-v2', identity=identity))
    model = OfficialHostProfileV2 if kind == 'GITHUB' else LocalOfficialHostProfileV2
    return model.model_validate_json(canonical_json_bytes(dict(
        **lane,
        job_id=closure.bound_job_id, payload=payload, environment_ref=closure.environment_ref,
        store_root=settings.store_root, output_root=settings.output_root,
        worker_credentials_directory=settings.worker_credentials_directory,
        python_root=str(closure.python_root), sandbox_executable=str(closure.sandbox.executable),
        sandbox_sha256=closure.sandbox.executable_sha256, sandbox_version=closure.sandbox.version,
        inventory_ref=inventory_ref, closure_sha256=closure.closure_sha256, image=closure.image,
        integration_receipt_ref=integration_ref, qualification_job_detail_ref=settings.qualification_job_detail_ref,
        postgres_binary_sha256=settings.postgres_binary_sha256,
        custodian_endpoint=settings.custodian_endpoint if payload.operation == 'HOLDOUT' else None)))


def prepare_host(request, root, deadline):
    import pwd
    from scripts.p3_local_transport import _official_run_directory, _worker_environment, _runtime_authority
    directory = _official_run_directory(request)
    runtime = _runtime_authority(request)
    if (request['worker_uid'] != runtime['uid']
        or request['worker_gid'] != pwd.getpwuid(runtime['uid']).pw_gid):
        raise ValueError('transport worker differs from qualified runtime role')
    document, digest = read_protected_canonical_json_current(directory/'host-inputs.json')
    settings = HostInputs.model_validate_json(canonical_json_bytes(document))
    environment = _worker_environment(request, root/'mnt', request['release_root'], root/'worker',
                                      host=document)
    observed = as_worker(request['worker_uid'], request['worker_gid'], deadline, environment,
                         dict(action='check-enqueue', settings=settings))
    payload = authenticate_job(settings, observed)
    if (observed['job_id'] != request['job_id']
        or payload.expected_source.model_dump(mode='json') != request['source']
        or observed['environment_ref'] != request['environment_ref']):
        raise ValueError('transport differs from authenticated enqueue source/job/environment')
    return settings, digest, observed, environment


def activate_mounted_host(request, root, descriptor, inventory, deadline, prepared):
    from scripts.p3_lane import identity_from_transport, issuer_lock
    from scripts.p3_local_controller import publish_protected
    from scripts.p3_local_transport import build_v2_closure, _official_run_directory, _worker_environment
    settings, settings_digest, observed, environment = prepared
    directory = _official_run_directory(request)
    uid, gid = request['worker_uid'], request['worker_gid']
    payload = authenticate_job(settings, observed)
    closure = build_v2_closure(root/'mnt', descriptor, descriptor['external_libraries'], inventory,
        request['source'], request['environment_ref'], request['job_id'], request['bwrap_sha256'])
    inventory_ref = as_worker(uid, gid, deadline, environment,
        dict(action='retain-inventory', store_root=settings.store_root, inventory=host_inventory(closure)))
    profile = assemble_profile(settings, request['identity'], payload,
        observed['integration_receipt_ref'], closure, inventory_ref)
    path = directory/'profile.json'
    with issuer_lock(identity_from_transport(request['identity'])):
        if (read_protected_canonical_json_current(directory/'host-inputs.json')[1] != settings_digest
            or _worker_environment(request, root/'mnt', request['release_root'], root/'worker',
                host=settings.model_dump(mode='json')) != environment
            or time.monotonic() >= deadline):
            raise ValueError('host inputs or authority changed before publication')
        authenticate_job(settings, observed)
        previous = None
        if path.exists():
            previous = OFFICIAL_PROFILE.validate_json(canonical_json_bytes(
                read_protected_canonical_json_current(path)[0]))
            validate_transition(previous, profile)
            previous_succeeded(settings, previous)
        if (directory/'session.json').exists():
            raise ValueError('an existing session cannot admit another transport')
        if time.monotonic() >= deadline:
            raise ValueError('host deadline expired during authenticated readback')
        publish_protected(path, canonical_json_bytes(profile)+b'\n', replace=previous is not None)
        digest = read_protected_canonical_json_current(path)[1]
    admitted = as_worker(uid, gid, deadline, environment,
        dict(action='read-profile', path=str(path), digest=digest, profile=profile))
    if admitted != profile.model_dump(mode='json'):
        raise ValueError('mounted host consumer readback differs')
    return environment


def install_next_host(request_path, preflight, manifest, review):
    """Admit only the next queued PARITY/PHASE_EXIT in the original live owner."""
    from packages.alpha_lifecycle.authority import (
        _LocalRequestFile, local_run_directory, validate_local_operation_authority,
    )
    from packages.local_authority import AuthorityStore
    from scripts.p3_local_controller import _protected_model, publish_protected, _committed_source
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.alpha_lifecycle.contracts.authority import LocalRunAuthorization
    from services.job_worker.p3_session import LocalSessionProfile, SessionStageProfile
    from services.job_worker.recovery import ProcProcessInspector
    if os.geteuid() != 0:
        raise PermissionError('host transition requires root')
    request = _protected_model(request_path, _LocalRequestFile)
    auth = request.authorization
    source = SourceIdentity.model_validate(_committed_source())
    if (not isinstance(auth, LocalRunAuthorization)
        or request.operation_input is None or auth.operation not in {'PARITY', 'PHASE_EXIT'}
        or validate_local_operation_authority(auth, source) != request):
        raise ValueError('host transition requires a current reviewed LOCAL stage')
    directory = local_run_directory(auth.identity)
    settings = _protected_model(directory/'host-inputs.json', HostInputs)
    session = _protected_model(directory/'session.json', LocalSessionProfile)
    previous = _protected_model(directory/'profile.json', LocalOfficialHostProfileV2)
    now = datetime.now(UTC)
    if (session.identity != auth.identity or session.source != source
        or now >= session.expires_at or ProcProcessInspector().inspect(session.parent.pid) != session.parent):
        raise ValueError('original session owner or authority is unavailable')
    deadline = time.monotonic() + (session.expires_at-now).total_seconds()
    from scripts.p3_local_transport import _runtime_authority, _official_deadline
    runtime = _runtime_authority(dict(identity=auth.identity.model_dump(mode='json')))
    if runtime['uid'] != previous.image.worker_uid:
        raise ValueError('qualified runtime role changed during the session')
    deadline = _official_deadline(deadline, runtime.get('expires_at'))
    environment = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1',
        P3_APPLICATION_ROOT=str(_absolute(runtime['application_root'])),
        P3_APPLICATION_PYTHON=str(_absolute(runtime['application_python'])),
        P3_ISSUER_KIND='LOCAL', P3_OPERATION=request.operation_input.workflow_operation,
        P3_AUTHORITY_REQUEST_FILE=str(request_path), P3_PREFLIGHT_DIRECTORY=str(preflight),
        P3_MANIFEST_FILE=str(manifest), P3_REVIEW_FILE=str(review))
    observed = as_worker(previous.image.worker_uid, previous.image.worker_gid, deadline, environment,
                         dict(action='check-enqueue', settings=settings))
    payload = authenticate_job(settings, observed)
    profile = next_session_host(previous, observed['job_id'], payload)
    if (observed['environment_ref'] != profile.environment_ref.model_dump(mode='json')
        or observed['integration_receipt_ref'] != profile.integration_receipt_ref.model_dump(mode='json')):
        raise ValueError('late stage changes admitted environment or integration')
    with AuthorityStore(auth.identity.issuer_identity).locked():
        stage = _protected_model(directory/'stage.json', SessionStageProfile)
        session_digest = read_protected_canonical_json_current(directory/'session.json')[1]
        if (stage.session_sha256 != session_digest or stage.attempts[-1].job_id != previous.job_id
            or _protected_model(directory/'session.json', LocalSessionProfile) != session
            or _protected_model(directory/'profile.json', LocalOfficialHostProfileV2) != previous
            or _protected_model(directory/'host-inputs.json', HostInputs) != settings
            or datetime.now(UTC) >= session.expires_at
            or ProcProcessInspector().inspect(session.parent.pid) != session.parent
            or validate_local_operation_authority(auth, source) != request):
            raise ValueError('stage, owner or authority changed before host transition')
        previous_succeeded(settings, previous)
        authenticate_job(settings, observed)
        if datetime.now(UTC) >= session.expires_at or time.monotonic() >= deadline:
            raise ValueError('session expired during authenticated readback')
        publish_protected(directory/'profile.json', canonical_json_bytes(profile)+b'\n', replace=True)
    # The continuing worker performs full mounted consumer readback in its namespace.
    return dict(status='INSTALLED_PENDING_WORKER_READBACK', job_id=profile.job_id)


def host_inventory(closure):
    return _HostFiles.model_validate_json(canonical_json_bytes(dict(
        schema_version='p3-host-files-v1', files=[dict(source=str(m.source), target=str(m.target),
            size=m.size, mode=m.mode, sha256=m.sha256) for m in closure.mounts])))


def session_ref_action(command):
    """Worker-UID read of a session note; the worker owns both its output root and its CAS."""
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from services.job_worker.p3_session import read_session_ref
    if set(command) != {'action', 'output_root', 'job_id', 'kind', 'store_root', 'expected_source'}:
        raise ValueError('session reference read accepts only its fixed fields')
    return read_session_ref(_absolute(command['output_root']), command['job_id'], command['kind'],
        worker_uid=os.geteuid(), store=LocalArtifactStore(_absolute(command['store_root'])),
        expected_source=SourceIdentity.model_validate(command['expected_source'])).model_dump(mode='json')


def native_profile_action(command):
    """Worker-UID custody and pinned closure checks; return only the finite launch profile."""
    from packages.alpha_lifecycle.operation_input import NativeParityInput
    from packages.alpha_lifecycle.native_request import NativeCommitment
    from packages.job_contracts import AlphaCampaignPayload
    from services.job_worker.p3_session import read_session_ref
    from services.job_worker.p3_native_spawn import NativeLaunchProfile
    from services.job_worker.nautilus_closure import NautilusClosureConfig
    from services.job_worker.p1_nautilus_closure import attest_p1_nautilus_closure
    from services.job_worker.p1_engine_spawn import validate_p1_engine_closure_attestation
    from packages.alpha_lifecycle.sandbox_policy import BWRAP_VERSION
    fields = {'action', 'profile', 'payload', 'intent', 'environment_ref',
              'previous_job_id', 'output_root', 'store_root'}
    if set(command) != fields or command['action'] != 'native-profile':
        raise ValueError('native profile read accepts only its fixed fields')
    profile = NativeLaunchProfile.model_validate_json(canonical_json_bytes(command['profile']))
    payload = AlphaCampaignPayload.model_validate_json(canonical_json_bytes(command['payload']))
    intent = P3OperationInput.model_validate_json(canonical_json_bytes(command['intent']))
    store = LocalArtifactStore(_absolute(command['store_root']))
    if (_read(store, payload.manifest_ref, P3OperationInput) != intent
        or payload.operation != 'PARITY' or payload.expected_source != profile.source
        or intent.operation != 'PARITY' or not isinstance(intent.body, NativeParityInput)):
        raise ValueError('native profile differs from the admitted PARITY intent')
    ref = read_session_ref(_absolute(command['output_root']), command['previous_job_id'], 'NATIVE_COMMITMENT',
        worker_uid=os.geteuid(), store=store, expected_source=profile.source)
    commitment = _read(store, ref, NativeCommitment)
    environment_ref = ArtifactRefV1.model_validate_json(canonical_json_bytes(command['environment_ref']))
    if (ref != profile.native_commitment_ref or ref != intent.body.native_request_ref
        or commitment.source != profile.source or commitment.environment_ref != environment_ref
        or commitment.manifest_ref != intent.body.holdout_manifest_ref
        or commitment.instrument_spec_ref != intent.body.instrument_spec_ref):
        raise ValueError('native commitment differs from the held session and PARITY intent')
    closure = validate_p1_engine_closure_attestation(attest_p1_nautilus_closure(NautilusClosureConfig(
        _absolute(profile.runtime_root), _absolute(profile.artifact_directory), _absolute(profile.sandbox_executable))))
    if closure.sandbox.version != BWRAP_VERSION or closure.sandbox.executable_sha256 != profile.sandbox_sha256:
        raise ValueError('native qualified sandbox differs from the admitted host')
    return profile.model_dump(mode='json')


def main():
    """Fixed worker-UID helper in the qualified application, never an alternate launcher."""
    raw = sys.stdin.buffer.read(4194305)
    if len(raw) > 4194304:
        raise ValueError('host helper input exceeds bound')
    command = json.loads(raw)
    if command['action'] == 'check-enqueue':
        settings = HostInputs.model_validate_json(canonical_json_bytes(command['settings']))
        result = checked_enqueue(settings, os.environ)
    elif command['action'] == 'retain-inventory':
        inventory = _HostFiles.model_validate_json(canonical_json_bytes(command['inventory']))
        result = LocalArtifactStore(_absolute(command['store_root'])).put_bytes(
            canonical_json_bytes(inventory), media_type='application/json')
    elif command['action'] == 'read-profile':
        from services.job_worker.p3_host_profile import read_official_profile
        profile = OFFICIAL_PROFILE.validate_json(canonical_json_bytes(command['profile']))
        if type(profile) not in (LocalOfficialHostProfileV2, OfficialHostProfileV2):
            raise ValueError('host helper reads only image-transport profiles')
        result, _ = read_official_profile(_absolute(command['path']), command['digest'],
            job_id=profile.job_id, payload=profile.payload, admission=True)
    elif command['action'] == 'session-ref':
        result = session_ref_action(command)
    elif command['action'] == 'native-profile':
        result = native_profile_action(command)
    elif command['action'] == 'database-identity':
        from services.job_store.config import read_systemd_credential
        if set(command) != {'action', 'credentials_directory'}:
            raise ValueError('database identity accepts no arbitrary credential names')
        credentials = {'CREDENTIALS_DIRECTORY':str(_absolute(command['credentials_directory']))}
        result = {key:read_systemd_credential(credentials, key)
                  for key in ('database-host', 'database-port', 'database-name')}
    else:
        raise ValueError('unknown host helper action')
    sys.stdout.buffer.write(canonical_json_bytes(result))


if __name__ == '__main__':
    main()
