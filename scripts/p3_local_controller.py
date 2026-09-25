"""Bounded LOCAL campaign commands using existing producers, authority and launcher.

Proposal commands grant nothing. Issuer commands require root-protected inputs,
independent exact review and current source qualification. No arbitrary command
execution, automatic review, retry, database reset or holdout fallback is exposed.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.authority import (
    LocalRunAuthorization, LocalRunIdentity, LocalQualificationApproval, QualificationReceipt,
    ReviewApproval,
)
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.alpha_lifecycle.local_campaign import assemble_input_set, propose_operation, prepare_authorization
from packages.alpha_lifecycle.local_qualification import ProtectedQualificationStore, _host_identity
from packages.alpha_lifecycle.local_research import (
    LocalResearchProfile, LocalResearchProof, RESEARCH_INPUTS_GATE, protected_backup_identity,
    read_local_research_inputs, research_profile_path, validate_local_research_graph,
)
from packages.alpha_lifecycle.replica_store import _read
from packages.alpha_lifecycle.research_custody import _seal
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.local_authority import AuthorityStore, _publish
from packages.runtime_release.config import _safe_directory, read_protected_canonical_json_current
from scripts.seal_p3_dataset import _committed_source


def publish_protected(path: Path, raw: bytes, *, replace=False):
    """Publish only beneath an existing authenticated root-owned directory chain."""
    if os.geteuid() != 0:
        raise PermissionError('protected issuer requires root')
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('protected destination must be absolute')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        if not _safe_directory(os.fstat(fd)):
            raise ValueError('unprotected authority root')
        for part in path.parts[1:-1]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            if not _safe_directory(os.fstat(fd)):
                raise ValueError('unprotected authority ancestor')
        try:
            os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not replace:
                raise FileExistsError('authority already exists; no automatic replay')
            read_protected_canonical_json_current(path)
        _publish(path, raw, mode=0o444)
    finally:
        os.close(fd)


def validate_research_review(review, *, source, subject_digests, now, expires_at):
    if (review.source != source or review.verdict != 'APPROVED'
        or review.operator_identity == review.reviewer_identity
        or tuple(sorted(review.subject_digests)) != tuple(sorted(subject_digests))
        or not review.issued_at <= now < expires_at <= review.expires_at
        or expires_at > now + timedelta(hours=2)):
        raise ValueError('research requires exact current independent review')


def _protected_model(path, model):
    document, _ = read_protected_canonical_json_current(path)
    return model.model_validate_json(canonical_json_bytes(document))


def _protected_put(root, value):
    raw = canonical_json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    ref = ArtifactRefV1(content_sha256=digest, size_bytes=len(raw),
                       media_type='application/json', locator=digest+'.blob')
    path = root/ref.locator
    if not path.exists():
        publish_protected(path, raw)
    if ProtectedQualificationStore(root).read_bytes(ref) != raw:
        raise ValueError('protected evidence readback differs')
    return ref


def install_research(profile_path, review_path, *, minutes):
    """Issue against presealed custody; never recursively chown caller paths."""
    if os.geteuid() != 0 or not 1 <= minutes <= 120:
        raise ValueError('research issuer requires root and a bounded validity')
    profile = _protected_model(profile_path, LocalResearchProfile)
    source = SourceIdentity.model_validate(_committed_source())
    identity = profile.identity
    if _host_identity() != (identity.host_id, identity.boot_id):
        raise ValueError('research host identity differs')
    proof_store = ProtectedQualificationStore(Path(profile.store_root))
    inputs = _read(proof_store, profile.binding.input_ref, InputSet)
    proof = _read(proof_store, profile.proof_ref, LocalResearchProof)
    review = _protected_model(review_path, ReviewApproval)
    now = datetime.now(UTC)
    expires = now+timedelta(minutes=minutes)
    validate_research_review(review, source=source,
        subject_digests=(inputs.digest, proof.digest), now=now, expires_at=expires)
    proof_store.read_bytes(review.evidence_ref)
    if (inputs.source != source or profile.binding.source != source
        or profile.binding.environment_ref != inputs.environment_ref
        or profile.binding.policy_digest != inputs.policy_digest
        or protected_backup_identity(Path(profile.backup_store_root)) != profile.backup_root_identity):
        raise ValueError('research source/environment/backup differs')
    for ref in (profile.commitment_ref, profile.backup_receipt_ref, profile.proof_ref,
                profile.binding.input_ref, profile.binding.environment_ref, inputs.pit_proof_ref):
        proof_store.read_bytes(ref)
    # Share the existing issuer lock, without advancing the fixed Pre-P3 snapshot.
    store = AuthorityStore(identity.issuer_identity)
    # Validate the lock's directory chain before allowing its creation.
    read_protected_canonical_json_current(store.path/'runtime.json')
    with store.locked():
        review_ref = _protected_put(Path(profile.store_root), review)
        receipt = _seal(dict(schema_version='local-phase-qualification-receipt-v1', identity=identity,
            binding=profile.binding, started_at=now.isoformat().replace('+00:00','Z'),
            completed_at=now.isoformat().replace('+00:00','Z'), expires_at=expires.isoformat().replace('+00:00','Z'),
            evidence_refs=(profile.commitment_ref, profile.backup_receipt_ref, profile.proof_ref, review_ref),
            status='PASS', authority=review.authority), QualificationReceipt)
        validate_local_research_graph(profile, inputs, receipt, proof_store)
        receipt_ref = _protected_put(Path(profile.store_root), receipt)
        approval = _seal(dict(schema_version='local-qualification-approval-v1',
            issuer_identity=identity.issuer_identity, receipt_ref=receipt_ref,
            issued_at=now.isoformat().replace('+00:00','Z'), expires_at=expires.isoformat().replace('+00:00','Z'),
            revoked=False, authority=review.authority), LocalQualificationApproval)
        if SourceIdentity.model_validate(_committed_source()) != source:
            raise ValueError('source changed before research activation')
        publish_protected(store.path/f'{RESEARCH_INPUTS_GATE}.json', canonical_json_bytes(approval)+b'\n', replace=True)
        publish_protected(research_profile_path(identity), canonical_json_bytes(profile)+b'\n', replace=True)
        return read_local_research_inputs(identity, source=source, policy_digest=inputs.policy_digest,
            input_set_ref=profile.binding.input_ref)


def install_operation(request_path, review_path, proof_root):
    from packages.alpha_lifecycle.authority import (
        _LocalRequestFile, build_alpha_campaign_payload, validate_local_operation_authority, validate_operation_review,
    )
    from packages.project_status import require_p3_source_ready
    if os.geteuid() != 0:
        raise PermissionError('operation issuer requires root')
    request = _protected_model(request_path, _LocalRequestFile)
    auth, intent = request.authorization, request.operation_input
    if not isinstance(auth, LocalRunAuthorization) or intent is None:
        raise ValueError('controller accepts official LOCAL operations only')
    source = SourceIdentity.model_validate(_committed_source())
    if request.execution_source != source or auth.input_set_ref != intent.input_set_ref:
        raise ValueError('operation source or input differs')
    build_alpha_campaign_payload(auth, source, intent.workflow_operation, operation_input=intent)
    proof = ProtectedQualificationStore(proof_root)
    review = validate_operation_review(auth, source, intent, proof, now=datetime.now(UTC))
    if _protected_model(review_path, ReviewApproval) != review:
        raise ValueError('protected independent review differs')
    proof.read_bytes(review.evidence_ref)
    ready = require_p3_source_ready(ROOT, auth.identity)
    inputs = _read(proof, intent.input_set_ref, InputSet)
    research = read_local_research_inputs(auth.identity, source=source,
        policy_digest=inputs.policy_digest, input_set_ref=intent.input_set_ref)
    if auth.expires_at > min(ready.expires_at, research.expires_at):
        raise ValueError('operation exceeds qualification validity')
    store = AuthorityStore(auth.identity.issuer_identity)
    path = store.path/'operations'/f'{auth.nonce}.json'
    with store.locked():
        publish_protected(path, canonical_json_bytes(request)+b'\n')
        validate_local_operation_authority(auth, source)
    return dict(request_path=str(path), authorization_digest=auth.digest, status='INSTALLED')


def _document(path):
    with path.open('rb') as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise ValueError('controller document exceeds 1 MiB')
    return json.loads(raw)


def stage_operation(request_path, review_path):
    """Stage exact reviewed bytes; the existing enqueue creates its private nonce directory."""
    import pwd
    import stat
    from contextlib import nullcontext
    from packages.alpha_lifecycle.authority import (
        _LocalRequestFile, local_run_directory, validate_local_operation_authority,
    )
    from scripts.p3_local_host import HostInputs
    from scripts.p3_local_transport import TaskLock, _runtime_authority
    from packages.local_authority import _fsync_directory
    if os.geteuid() != 0:
        raise PermissionError('operation staging requires root')
    request = _protected_model(request_path, _LocalRequestFile)
    auth = request.authorization
    source = SourceIdentity.model_validate(_committed_source())
    if (not isinstance(auth, LocalRunAuthorization) or request.operation_input is None
        or validate_local_operation_authority(auth, source) != request):
        raise ValueError('staging requires an installed current reviewed operation')
    review = _protected_model(review_path, ReviewApproval)
    raw_review = canonical_json_bytes(review)
    if (len(raw_review) != auth.review_ref.size_bytes
        or hashlib.sha256(raw_review).hexdigest() != auth.review_ref.content_sha256):
        raise ValueError('staged review differs from admitted reference')
    directory = local_run_directory(auth.identity)
    _protected_model(directory/'host-inputs.json', HostInputs)
    runtime = _runtime_authority(dict(identity=auth.identity.model_dump(mode='json')))
    uid = runtime['uid']
    if type(uid) is not int or uid <= 0:
        raise ValueError('runtime worker UID is invalid')
    gid = pwd.getpwuid(uid).pw_gid
    if gid <= 0:
        raise ValueError('runtime worker group is invalid')
    late = auth.operation in {'PARITY', 'PHASE_EXIT'}
    with (nullcontext() if late else TaskLock(directory)), AuthorityStore(auth.identity.issuer_identity).locked():
        operations = directory/'operations'
        if not operations.exists():
            operations.mkdir(mode=0o755)
            os.chmod(operations, 0o755)
        info = operations.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
            raise ValueError('operation staging parent is unprotected')
        destination = operations/str(auth.nonce)
        destination.mkdir(mode=0o700)  # Exclusive; a partial prior attempt is retained.
        os.chown(destination, 0, gid)
        os.chmod(destination, 0o750)
        for name, value in (('manifest.json', request.operation_input), ('review.json', review)):
            fd = os.open(destination/name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o400)
            with os.fdopen(fd, 'wb') as stream:
                os.fchown(stream.fileno(), 0, gid)
                os.fchmod(stream.fileno(), 0o440)
                stream.write(canonical_json_bytes(value))
                stream.flush()
                os.fsync(stream.fileno())
        private = directory/'preflight'
        if not private.exists():
            private.mkdir(mode=0o700)
            os.chown(private, uid, gid)
            os.chmod(private, 0o700)
        info = private.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('preflight parent differs from the private worker role')
        if validate_local_operation_authority(auth, source) != request:
            raise ValueError('operation authority changed during staging')
        for folder in (destination, operations, private, directory):
            _fsync_directory(folder)
        if not late:
            path = directory/'authority-request.json'
            publish_protected(path, canonical_json_bytes(request)+b'\n', replace=path.exists())
    return dict(manifest=str(destination/'manifest.json'), review=str(destination/'review.json'),
                preflight=str(private/str(auth.nonce)), request=str(request_path))


def _session_context(request_path):
    from packages.alpha_lifecycle.authority import _LocalRequestFile, local_run_directory, validate_local_operation_authority
    from services.job_worker.p3_host_profile import OFFICIAL_PROFILE
    if os.geteuid() != 0:
        raise PermissionError('session issuer requires root')
    request = _protected_model(request_path, _LocalRequestFile)
    auth = request.authorization
    if not isinstance(auth, LocalRunAuthorization) or request.operation_input is None:
        raise ValueError('session requires an official LOCAL operation')
    source = SourceIdentity.model_validate(_committed_source())
    if validate_local_operation_authority(auth, source) != request:
        raise ValueError('current protected request differs')
    directory = local_run_directory(auth.identity)
    document, host_digest = read_protected_canonical_json_current(directory/'profile.json')
    host = OFFICIAL_PROFILE.validate_json(canonical_json_bytes(document))
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    expected = build_alpha_campaign_payload(auth, source, request.operation_input.workflow_operation,
        operation_input=request.operation_input)
    if host.payload != expected or host.identity != auth.identity:
        raise ValueError('session host profile differs from admitted operation')
    return request, directory, host, host_digest


def install_session(request_path, proof_root):
    """Bind the genuine launched worker to the already-reviewed HOLDOUT intent."""
    import stat
    from services.job_worker.p3_session import LocalSessionLaunch, LocalSessionOwnerObservation, LocalSessionProfile
    from services.job_worker.recovery import ProcProcessInspector
    from services.job_worker.results import _open_directory_chain
    from packages.alpha_lifecycle.contracts.authority import CustodyRecord
    from packages.alpha_lifecycle.operation_input import HoldoutInput
    request, directory, host, host_digest = _session_context(request_path)
    intent = request.operation_input
    if intent is None or not isinstance(intent.body, HoldoutInput):
        raise ValueError('only the initial HOLDOUT may create a session')
    launch = _protected_model(directory/'session-launch.json', LocalSessionLaunch)
    path = Path(host.output_root)/host.job_id/'session-owner'/'observation.log'
    parent_fd = _open_directory_chain(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != launch.worker_uid
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or not 1 <= info.st_size <= 65536):
                raise ValueError('worker observation custody differs')
            observation = LocalSessionOwnerObservation.model_validate_json(stream.read(65537))
    finally:
        os.close(parent_fd)
    actual = ProcProcessInspector().inspect(launch.worker_pid)
    status = Path(f'/proc/{launch.worker_pid}/status').read_text()
    fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    if (actual != observation.parent or actual is None or actual.start_ticks != launch.worker_start_ticks
        or tuple(map(int, fields['Uid'].split())) != (launch.worker_uid,)*4
        or int(fields['PPid']) != launch.controller_pid
        or launch.identity != request.authorization.identity or observation.identity != launch.identity
        or launch.source != request.execution_source or observation.source != launch.source
        or launch.job_id != host.job_id or observation.job_id != host.job_id
        or launch.host_profile_sha256 != host_digest or observation.host_profile_sha256 != host_digest
        or observation.issued_at != launch.issued_at or observation.workflow_deadline != launch.workflow_deadline
        or not launch.issued_at <= datetime.now(UTC) < launch.workflow_deadline <= request.authorization.expires_at):
        raise ValueError('worker owner is not the current transport child')
    custody = _read(ProtectedQualificationStore(proof_root), intent.body.custody_record_ref, CustodyRecord)
    profile = LocalSessionProfile.model_validate_json(canonical_json_bytes(dict(
        schema_version='p3-local-holdout-session-v1', source=request.execution_source,
        environment_ref=host.environment_ref, primary_selection_ref=intent.body.primary_selection_ref,
        custody_record_ref=intent.body.custody_record_ref, holdout_commitment=custody.holdout_commitment,
        closure_sha256=host.closure_sha256, parent=observation.model_dump(mode='json')['parent'],
        identity=launch.identity, boot_id=str(launch.identity.boot_id),
        issued_at=launch.issued_at.isoformat().replace('+00:00','Z'),
        expires_at=launch.workflow_deadline.isoformat().replace('+00:00','Z'),
        workflow_deadline=launch.workflow_deadline.isoformat().replace('+00:00','Z'))))
    with AuthorityStore(launch.identity.issuer_identity).locked():
        if _session_context(request_path) != (request, directory, host, host_digest):
            raise ValueError('session admission changed')
        if ProcProcessInspector().inspect(launch.worker_pid) != actual:
            raise ValueError('session owner changed before activation')
        publish_protected(directory/'session.json', canonical_json_bytes(profile)+b'\n')
    return profile


def next_stage(previous, current, *, session_digest, host_digest):
    from services.job_worker.p3_session import SessionStageProfile
    stages = ('HOLDOUT', 'PARITY', 'PHASE_EXIT')
    attempts = () if previous is None else previous.attempts
    if (len(attempts) >= 3 or current.operation != stages[len(attempts)]
        or tuple(a.operation for a in attempts) != stages[:len(attempts)]
        or previous is not None and previous.session_sha256 != session_digest
        or any(a.job_id == current.job_id or a.attempt_id == current.attempt_id for a in attempts)):
        raise ValueError('stage must append the next distinct actual claim in this session')
    return SessionStageProfile(schema_version='p3-holdout-session-stage-v1',
        session_sha256=session_digest, host_profile_sha256=host_digest, attempts=(*attempts, current))


def owner_database_settings(credentials, host, expires_at):
    """Compare worker metadata under its UID before reading the root observer password."""
    import time
    from scripts.p3_local_host import as_worker
    from scripts.p3_local_transport import _runtime_authority, _official_deadline
    from services.job_store.config import JobStoreSettings, read_systemd_credential
    from services.job_worker.p3_host_profile import LocalOfficialHostProfileV2
    if not isinstance(host, LocalOfficialHostProfileV2) or datetime.now(UTC) >= expires_at:
        raise ValueError('stage observation requires a current LOCAL v2 session')
    runtime = _runtime_authority(dict(identity=host.identity.model_dump(mode='json')))
    if (runtime['uid'] != host.image.worker_uid
        or runtime['identity'] != host.identity.model_dump(mode='json')
        or runtime['source'] != host.payload.expected_source.model_dump(mode='json')):
        raise ValueError('stage observer runtime identity differs')
    deadline = _official_deadline(time.monotonic()+(expires_at-datetime.now(UTC)).total_seconds(),
                                  runtime.get('expires_at'))
    from packages.runtime_release.config import _absolute
    environment = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1',
        P3_APPLICATION_ROOT=str(_absolute(runtime['application_root'])),
        P3_APPLICATION_PYTHON=str(_absolute(runtime['application_python'])))
    observed = as_worker(host.image.worker_uid, host.image.worker_gid, deadline, environment,
        dict(action='database-identity', credentials_directory=host.worker_credentials_directory))
    if time.monotonic() >= deadline:
        raise ValueError('stage observer authority expired during worker readback')
    keys = ('database-host', 'database-port', 'database-name')
    if not isinstance(observed, dict) or set(observed) != set(keys) or not all(
        isinstance(observed[key], str) for key in keys):
        raise ValueError('worker database identity fields differ')
    env = {'CREDENTIALS_DIRECTORY':str(credentials)}
    if observed != {key:read_systemd_credential(env, key) for key in keys}:
        raise ValueError('owner observer database differs from the admitted worker')
    settings = JobStoreSettings(host=observed['database-host'], port=int(observed['database-port']),
        database=observed['database-name'], user='trading_owner',
        password=read_systemd_credential(env,'database-password'))
    return settings, runtime, deadline


def install_stage(request_path, credentials):
    """Read the current claim with the owner role; never claim or expose lease tokens."""
    import time
    import psycopg
    from scripts.p3_local_transport import _runtime_authority
    from services.job_worker.p3_session import LocalSessionProfile, SessionAttempt, SessionStageProfile
    from services.job_worker.recovery import ProcProcessInspector
    request, directory, host, host_digest = _session_context(request_path)
    session_doc, session_digest = read_protected_canonical_json_current(directory/'session.json')
    session = LocalSessionProfile.model_validate_json(canonical_json_bytes(session_doc))
    if (session.identity != request.authorization.identity or session.source != request.execution_source
        or session.parent != ProcProcessInspector().inspect(session.parent.pid)
        or not session.issued_at <= datetime.now(UTC) < session.expires_at):
        raise ValueError('stage session owner or deadline differs')
    settings, runtime, deadline = owner_database_settings(credentials, host, session.expires_at)
    with psycopg.connect(settings.conninfo(), connect_timeout=10,
            options='-c default_transaction_read_only=on -c statement_timeout=5000') as connection:
        if connection.execute('SELECT session_user,current_user').fetchone() != ('trading_owner','trading_owner'):
            raise ValueError('owner observation role differs')
        row = connection.execute("""
            SELECT a.attempt_id,a.worker_id,encode(sha256(convert_to(a.lease_token,'UTF8')),'hex')
            FROM public.jobs j JOIN public.job_attempts a
              ON a.job_id=j.job_id AND a.attempt_number=j.attempt_count
            WHERE j.job_id=%s AND j.job_type='ALPHA_CAMPAIGN' AND j.payload=%s::jsonb
              AND j.state IN ('CLAIMED','RUNNING') AND a.outcome=j.state
              AND a.worker_id=j.lease_owner AND a.lease_token=j.lease_token
              AND j.lease_expires_at>clock_timestamp() AND a.lease_expires_at>clock_timestamp()
            """, (host.job_id, canonical_json_bytes(host.payload).decode())).fetchone()
    if row is None:
        raise ValueError('exact current SQL claim is unavailable; no synthetic claim allowed')
    current = SessionAttempt(operation=request.authorization.operation, job_id=host.job_id,
        attempt_id=row[0], worker_id=row[1], lease_token_sha256=row[2])
    path = directory/'stage.json'
    with AuthorityStore(session.identity.issuer_identity).locked():
        previous = _protected_model(path, SessionStageProfile) if path.exists() else None
        stage = next_stage(previous, current, session_digest=session_digest, host_digest=host_digest)
        if (_session_context(request_path) != (request, directory, host, host_digest)
            or read_protected_canonical_json_current(directory/'session.json')[1] != session_digest
            or ProcProcessInspector().inspect(session.parent.pid) != session.parent
            or datetime.now(UTC) >= session.expires_at or time.monotonic() >= deadline
            or _runtime_authority(dict(identity=host.identity.model_dump(mode='json'))) != runtime):
            raise ValueError('stage authority changed before publication')
        publish_protected(path, canonical_json_bytes(stage)+b'\n', replace=previous is not None)
    return stage


def _output(path, value):
    if path.resolve().is_relative_to(ROOT):
        raise ValueError('campaign output must remain outside source')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical_json_bytes(value)+b'\n')
        stream.flush()
        os.fsync(stream.fileno())


_BODY_REFS = {
    'p3-register-family-v1': ('baseline_selection_ref',),
    **{f'p3-oos-a{index}-v1': ('publication_receipt_ref',) for index in range(4)},
    'p3-select-primary-v1': ('oos_receipt_refs', 'review_ref'),
    'p3-holdout-primary-v1': ('primary_selection_ref', 'custody_record_ref', 'holdout_dataset_ref', 'buffer_ref'),
    'p3-native-parity-v1': ('holdout_result_ref', 'native_commitment_ref'),
    'p3-phase-exit-v1': ('primary_selection_ref', 'holdout_result_ref', 'parity_pair_ref'),
}


def derive_body(document, *, source, store):
    """Turn committed result references into an exact propose-operation request; grants nothing."""
    from packages.alpha_lifecycle import local_campaign as campaign
    from packages.alpha_lifecycle.contracts.authority import PrimarySelection
    operation = document.get('workflow_operation')
    if operation not in _BODY_REFS or set(document) != {'workflow_operation', 'input_set_ref', 'refs'}:
        raise ValueError('body derivation request differs')
    refs = document['refs']
    if set(refs) != set(_BODY_REFS[operation]):
        raise ValueError('body derivation references differ')
    def ref(name):
        return ArtifactRefV1.model_validate(refs[name])
    input_set_ref = ArtifactRefV1.model_validate(document['input_set_ref'])
    primary_alpha_id = None
    if operation == 'p3-register-family-v1':
        body = campaign.registration_body(input_set_ref, ref('baseline_selection_ref'), store=store)
    elif operation.startswith('p3-oos-a'):
        body = campaign.oos_body(operation, ref('publication_receipt_ref'), store=store)
    elif operation == 'p3-select-primary-v1':
        disclosure = campaign.family_disclosure(input_set_ref,
            tuple(ArtifactRefV1.model_validate(item) for item in refs['oos_receipt_refs']), store=store)
        body = campaign.family_review_body(disclosure, ref('review_ref'), source=source, store=store)
    elif operation == 'p3-native-parity-v1':
        body = campaign.parity_body(ref('holdout_result_ref'), ref('native_commitment_ref'), store=store)
    else:
        primary_alpha_id = _read(store, ref('primary_selection_ref'), PrimarySelection).primary_alpha_id
        body = (campaign.holdout_body(input_set_ref, ref('primary_selection_ref'), custody_record_ref=ref('custody_record_ref'),
                holdout_dataset_ref=ref('holdout_dataset_ref'), buffer_ref=ref('buffer_ref'), store=store)
            if operation == 'p3-holdout-primary-v1' else
            campaign.phase_exit_body(ref('primary_selection_ref'), ref('holdout_result_ref'), ref('parity_pair_ref'), store=store))
    request = dict(workflow_operation=operation, input_set_ref=input_set_ref, body=body)
    return request if primary_alpha_id is None else dict(request, primary_alpha_id=primary_alpha_id)


def read_session_ref(request_path, kind):
    """Root reads the worker's note for the reference the next PARITY/PHASE_EXIT intent must name."""
    from services.job_worker.p3_session import LocalSessionLaunch, read_session_ref as read_note
    request, directory, host, _ = _session_context(request_path)
    launch = _protected_model(directory/'session-launch.json', LocalSessionLaunch)
    return dict(kind=kind, job_id=host.job_id,
        ref=read_note(host.output_root, host.job_id, kind, worker_uid=launch.worker_uid,
                      store=LocalArtifactStore(Path(host.store_root)), expected_source=request.execution_source))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare-inputs', 'propose-operation', 'prepare-authorization', 'derive-body'):
        sub = commands.add_parser(name)
        for arg in ('request', 'store', 'output'):
            sub.add_argument('--'+arg, required=True, type=Path)
    research = commands.add_parser('install-research')
    research.add_argument('--profile', type=Path, required=True)
    research.add_argument('--review', type=Path, required=True)
    research.add_argument('--minutes', type=int, required=True)
    install = commands.add_parser('install-reviewed')
    for arg in ('request', 'review', 'proof-root'):
        install.add_argument('--'+arg, type=Path, required=True)
    for name in ('run-operation', 'cleanup'):
        sub = commands.add_parser(name)
        sub.add_argument('--transport-request', required=True)
    session = commands.add_parser('install-session')
    session.add_argument('--request', type=Path, required=True)
    session.add_argument('--proof-root', type=Path, required=True)
    stage = commands.add_parser('install-stage')
    stage.add_argument('--request', type=Path, required=True)
    stage.add_argument('--owner-credentials', type=Path, required=True)
    next_host = commands.add_parser('install-next-host')
    for arg in ('request', 'preflight', 'manifest', 'review'):
        next_host.add_argument('--'+arg, type=Path, required=True)
    note = commands.add_parser('read-session-ref')
    note.add_argument('--request', type=Path, required=True)
    note.add_argument('--kind', choices=('NATIVE_COMMITMENT', 'PARITY_PAIR'), required=True)
    staged = commands.add_parser('stage-operation')
    for arg in ('request', 'review'):
        staged.add_argument('--'+arg, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'install-research':
        result = install_research(args.profile, args.review, minutes=args.minutes)
    elif args.command == 'install-reviewed':
        result = install_operation(args.request, args.review, args.proof_root)
    elif args.command == 'install-session':
        result = install_session(args.request, args.proof_root)
    elif args.command == 'install-stage':
        result = install_stage(args.request, args.owner_credentials)
    elif args.command == 'install-next-host':
        from scripts.p3_local_host import install_next_host
        result = install_next_host(args.request, args.preflight, args.manifest, args.review)
    elif args.command == 'stage-operation':
        result = stage_operation(args.request, args.review)
    elif args.command == 'read-session-ref':
        result = read_session_ref(args.request, args.kind)
    elif args.command in {'run-operation', 'cleanup'}:
        from scripts import p3_local_transport
        request = p3_local_transport.read_request(args.transport_request)
        expected = 'run' if args.command == 'run-operation' else 'cleanup'
        if request['operation'] != expected:
            raise ValueError('transport request operation differs')
        return p3_local_transport.main(['p3_local_transport.py', args.transport_request])
    else:
        source = SourceIdentity.model_validate(_committed_source())
        document = _document(args.request)
        store = LocalArtifactStore(args.store)
        if args.command == 'prepare-inputs':
            fields = ('dataset_ref', 'commitment_ref', 'pit_suite_ref', 'environment_ref', 'integration_receipt_ref')
            if set(document) != set(fields):
                raise ValueError('InputSet proposal fields differ')
            result = assemble_input_set(source=source, store=store,
                **{key:ArtifactRefV1.model_validate(document[key]) for key in fields})
        elif args.command == 'derive-body':
            result = derive_body(document, source=source, store=store)
        elif args.command == 'propose-operation':
            if set(document) - {'workflow_operation', 'input_set_ref', 'body', 'primary_alpha_id'}:
                raise ValueError('operation proposal fields differ')
            result = propose_operation(document['workflow_operation'],
                ArtifactRefV1.model_validate(document['input_set_ref']), document['body'], store=store,
                primary_alpha_id=document.get('primary_alpha_id'))
        else:
            from packages.alpha_lifecycle.operation_input import P3OperationInput
            intent = P3OperationInput.model_validate_json(canonical_json_bytes(document['intent']))
            identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(document['identity']))
            expires = datetime.fromisoformat(document['expires_at'].replace('Z','+00:00'))
            result = prepare_authorization(intent, identity=identity, source=source,
                review_ref=ArtifactRefV1.model_validate(document['review_ref']), store=store,
                now=datetime.now(UTC), expires_at=expires)
        if SourceIdentity.model_validate(_committed_source()) != source:
            raise ValueError('source changed during proposal production')
        _output(args.output, result)
    print(canonical_json_bytes(result).decode())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
