"""Local lifetime checks use synthetic views, never holdout release authority."""
import os

import pytest

from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView, build_holdout_calculation_view
from packages.data_catalog.artifact_store import LocalArtifactStore
from tests.p3.test_reference_input import reference_seed  # noqa: F401
from tests.p3.test_phase_exit import exit_graph  # noqa: F401


def test_closed_view_revokes_existing_reader_aliases(reference_seed):
    root, manifest, spec, *_ = reference_seed
    view = HoldoutCalculationView(build_holdout_calculation_view(manifest, spec, LocalArtifactStore(root)), manifest, spec)
    read = view.read_bytes
    assert read(manifest)
    view.close()
    view.close()
    with pytest.raises(ValueError, match='closed'):
        read(manifest)
    with pytest.raises(ValueError, match='closed'):
        _ = view.raw


def test_view_cannot_be_used_in_a_forked_parent(reference_seed):
    root, manifest, spec, *_ = reference_seed
    view = HoldoutCalculationView(build_holdout_calculation_view(manifest, spec, LocalArtifactStore(root)), manifest, spec)
    child = os.fork()
    if child == 0:
        try:
            view.read_bytes(manifest)
        except ValueError:
            os._exit(0)
        os._exit(1)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert view.read_bytes(manifest)  # The owning parent remains usable.


@pytest.fixture
def session_inputs(reference_seed, tmp_path, monkeypatch):
    from dataclasses import asdict
    from datetime import UTC, datetime, timedelta
    import hashlib
    import shutil
    from pathlib import Path
    from types import SimpleNamespace
    from uuid import uuid4
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload, stage_alpha_campaign_payload
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest
    from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.replica_store import _read, ReadbackStore
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.job_contracts import JobType
    from services.job_store.records import ClaimedJob
    from services.job_worker import p3_session as module
    from services.job_worker.p3_holdout_release import CustodianEndpoint
    from services.job_worker.recovery import ProcProcessInspector
    from tests.p3.test_replica_execution import _seal

    root, manifest_ref, spec_ref, *_ = reference_seed
    destination = tmp_path/'store'
    shutil.copytree(root, destination)
    store = LocalArtifactStore(destination)
    manifest = _read(store, manifest_ref, HoldoutManifest)
    registration = _read(store, manifest.research_registration_ref, RegistrationProof)
    placeholder = store.put_bytes(b'{}', media_type='application/json')
    ciphertext = store.put_bytes(b'synthetic encrypted bundle', media_type='application/octet-stream')
    custody = _seal(store, schema_version='p3-custody-record-v1', holdout_commitment='c'*64,
        ciphertext_ref=ciphertext, plaintext_bundle_digest='d'*64, access_policy_digest='e'*64,
        custodian_identity='custodian', research_identity='research', custodian_attestation_ref=placeholder,
        classification='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND')
    now = datetime.now(UTC)
    utc = lambda value: value.isoformat().replace("+00:00", "Z")
    profile = dict(schema_version='p3-holdout-session-v1', source=manifest.source,
        environment_ref=manifest.environment_ref, primary_selection_ref=manifest.primary_selection_ref,
        custody_record_ref=custody, holdout_commitment='c'*64, closure_sha256='f'*64,
        workflow_run_id=1, workflow_attempt=1, parent=asdict(ProcProcessInspector().inspect(os.getpid())),
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        issued_at=utc(now-timedelta(seconds=1)), expires_at=utc(now+timedelta(minutes=3)),
        workflow_deadline=utc(now+timedelta(minutes=4)))
    host = {}
    stage_profile = {}
    attempts = []
    events = []
    def digest(document):
        return hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    def protected(path):
        document = {'session.json': profile, 'stage.json': stage_profile, 'profile.json': host}[path.name]
        return document, digest(document)
    monkeypatch.setattr(module, 'read_protected_canonical_json_current', protected)
    endpoint = CustodianEndpoint(schema_version='p3-custodian-endpoint-v1', socket_path='/run/p3/custodian.sock',
        custodian_uid=17001, research_uid=17002, custodian_identity='custodian', research_identity='research')
    # Only protected-host custody is injected. Current stage review validation
    # uses the real immutable CAS, payload builder and review/expiry checks.
    def host_reader(path, expected_digest, *, job_id, payload, context):
        if digest(host) != expected_digest or host['job_id'] != job_id or host['payload'] != payload:
            raise ValueError('host profile changed')
        intent = _read(store, payload.manifest_ref, P3OperationInput)
        auth = _read(store, payload.authorization_ref, RunAuthorization)
        assert stage_alpha_campaign_payload(ReadbackStore(store, store), auth, manifest.source,
            intent.workflow_operation, canonical_json_bytes(intent), operation_input=intent) == payload
        return SimpleNamespace(store_root=str(destination), custodian_endpoint=endpoint,
            model_dump=lambda **kwargs: {'runtime': host.get('runtime', 'fixed')}), SimpleNamespace(
            source=manifest.source, environment_ref=manifest.environment_ref, closure_sha256='f'*64)
    monkeypatch.setattr('services.job_worker.p3_host_profile.read_official_profile', host_reader)
    session = module.P3HoldoutSession(Path('/run/trading-agent-p3/1-1/session.json'), store=store)
    def claim(operation, **changes):
        body = {
            'HOLDOUT': dict(primary_selection_ref=manifest.primary_selection_ref,
                candidate_spec_ref=manifest.candidate_spec_ref, registration_proof_ref=manifest.research_registration_ref,
                custody_record_ref=custody, holdout_input_set_ref=registration.input_set_ref,
                holdout_dataset_ref=manifest.holdout_dataset_ref, context_dataset_ref=manifest.context_dataset_ref,
                buffer_ref=manifest.buffer_ref, environment_ref=manifest.environment_ref,
                instrument_spec_ref=spec_ref, policy_digest=manifest.policy_digest),
            'PARITY': dict(holdout_manifest_ref=manifest_ref, primary_reference_ref=placeholder,
                baseline_reference_ref=placeholder, instrument_spec_ref=spec_ref,
                native_request_ref=session.native_commitment_ref or placeholder),
            'PHASE_EXIT': dict(primary_selection_ref=manifest.primary_selection_ref,
                primary_qualification_ref=placeholder, baseline_selection_ref=placeholder, holdout_request_ref=placeholder,
                holdout_evaluation_ref=placeholder, holdout_replay_ref=placeholder, executable_ref=placeholder,
                baseline_executable_ref=placeholder, parity_ref=placeholder, current_primary_head_ref=placeholder),
        }[operation]
        body.update(changes)
        workflow = {'HOLDOUT': 'p3-holdout-primary-v1', 'PARITY': 'p3-native-parity-v1', 'PHASE_EXIT': 'p3-phase-exit-v1'}[operation]
        intent_ref = _seal(store, schema_version='p3-operation-input-v1', workflow_operation=workflow,
            operation=operation, input_set_ref=registration.input_set_ref,
            allowed_alpha_ids=['a0.donchian-20-10-close-confirm'], body=body)
        intent = _read(store, intent_ref, P3OperationInput)
        authority = dict(broker=False, live=False, network=False, production=False)
        review_ref = _seal(store, schema_version='p3-review-approval-v1', source=manifest.source,
            subject_digests=[intent.digest], operator_identity='operator', reviewer_identity='reviewer',
            review_execution_id='synthetic', verdict='APPROVED', issued_at=utc(now-timedelta(minutes=2)),
            expires_at=utc(now+timedelta(minutes=5)), evidence_ref=placeholder, authority=authority)
        auth_ref = _seal(store, schema_version='p3-run-authorization-v1', input_set_ref=intent.input_set_ref,
            review_ref=review_ref, operation=operation, allowed_alpha_ids=intent.allowed_alpha_ids,
            issued_at=utc(now-timedelta(minutes=1)), expires_at=utc(now+timedelta(minutes=4)), nonce=str(uuid4()),
            issuer_workflow='p3-authority.yml', issuer_run_id=1, issuer_attempt=1, authority=authority)
        auth = _read(store, auth_ref, RunAuthorization)
        payload = build_alpha_campaign_payload(auth, manifest.source, workflow, operation_input=intent)
        result = ClaimedJob('job_'+operation, JobType.ALPHA_CAMPAIGN, payload, 'attempt_'+operation,
            1, 'worker', 's'*32, now+timedelta(minutes=1), 1)
        host.update(job_id=result.job_id, payload=payload)
        attempts.append(dict(operation=operation, job_id=result.job_id, attempt_id=result.attempt_id,
            worker_id=result.worker_id, lease_token_sha256=result.lease_token_sha256))
        stage_profile.update(schema_version='p3-holdout-session-stage-v1', session_sha256=digest(profile),
            host_profile_sha256=digest(host), attempts=list(attempts))
        return result
    def release(*args, **kwargs):
        kwargs['fence']()
        events.append('release')
        view = HoldoutCalculationView(build_holdout_calculation_view(manifest_ref, spec_ref, store), manifest_ref, spec_ref)
        return view, placeholder, manifest_ref
    monkeypatch.setattr('services.job_worker.p3_holdout_release.release_holdout_view', release)
    return SimpleNamespace(session=session, claim=claim, store=store, manifest=manifest_ref, spec=spec_ref,
        profile=profile, host=host, stage_profile=stage_profile, events=events, module=module)


def test_session_rejects_an_attempt_missing_from_protected_chain(session_inputs):
    x = session_inputs
    claim = x.claim('HOLDOUT')
    x.stage_profile['attempts'][0]['attempt_id'] = 'attempt_other'
    with pytest.raises(ValueError, match='attempt'):
        with x.session.stage(claim, fence=lambda: None):
            pass


def test_session_reuses_one_view_across_independently_admitted_stages(session_inputs):
    from services.job_store.worker_repository import WorkerRepository
    x = session_inputs
    with x.session.stage(x.claim('HOLDOUT'), fence=lambda: x.events.append('holdout-fence')):
        x.session.release(object.__new__(WorkerRepository), trace_id='test:session')
        view = x.session.view
        assert view.read_bytes(x.manifest)
    for stage in ('PARITY', 'PHASE_EXIT'):
        with x.session.stage(x.claim(stage), fence=lambda: x.events.append(stage)):
            assert x.session.view is view
            assert view.read_bytes(x.manifest)
            if stage == 'PARITY':
                assert tuple(request.role for request in x.session.native_requests()) == ('PRIMARY', 'SELECTED_BASELINE')
    assert x.events.count('release') == 1
    with pytest.raises(ValueError, match='closed'):
        view.read_bytes(x.manifest)


@pytest.mark.parametrize('fault', ['cancel', 'lease', 'profile', 'runtime', 'deadline', 'repeated_release',
    'stage_order', 'alias_between_jobs', 'attempt_chain', 'review', 'pid', 'boot'])
def test_failed_session_revokes_alias_and_cannot_resume(session_inputs, monkeypatch, fault):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    from services.job_store.worker_repository import WorkerRepository
    x = session_inputs
    control = {'fault': False}
    def fence():
        if control['fault']:
            raise ValueError('synthetic cancel or lease loss')
    with pytest.raises((ValueError, AuthorityHeld)):
        with x.session.stage(x.claim('HOLDOUT'), fence=fence):
            x.session.release(object.__new__(WorkerRepository), trace_id='test:session')
            view = x.session.view
            if fault in {'cancel', 'lease'}: control['fault'] = True
            elif fault == 'profile': x.profile['holdout_commitment'] = '0'*64
            elif fault == 'runtime': x.host['runtime'] = 'substituted'
            elif fault == 'attempt_chain': x.stage_profile['attempts'][0]['lease_token_sha256'] = '0'*64
            elif fault == 'review':
                from packages.alpha_lifecycle.contracts.authority import RunAuthorization
                auth = RunAuthorization.model_validate_json(x.store.read_bytes(x.host['payload'].authorization_ref))
                (x.store._root/auth.review_ref.locator).write_bytes(b'{}')
            elif fault == 'pid': monkeypatch.setattr(x.module.os, 'getpid', lambda: 999999999)
            elif fault == 'boot':
                from pathlib import Path
                original = Path.read_text
                monkeypatch.setattr(Path, 'read_text', lambda path, **kwargs:
                    '00000000-0000-0000-0000-000000000000' if str(path) == '/proc/sys/kernel/random/boot_id'
                    else original(path, **kwargs))
            elif fault == 'deadline': monkeypatch.setattr(x.module.time, 'monotonic', lambda: 1e30)
            elif fault == 'repeated_release': x.session.release(object.__new__(WorkerRepository), trace_id='test:repeat')
            if fault not in {'stage_order', 'alias_between_jobs'}: x.session.fence()
        if fault == 'stage_order':
            with x.session.stage(x.claim('PHASE_EXIT'), fence=fence): pass
        if fault == 'alias_between_jobs': view.read_bytes(x.manifest)
    with pytest.raises(ValueError, match='closed'): view.read_bytes(x.manifest)
    with pytest.raises(ValueError):
        with x.session.stage(x.claim('PARITY'), fence=lambda: None): pass
    assert x.events.count('release') == 1


def test_session_never_retries_an_uncertain_disclosure(session_inputs, monkeypatch):
    from services.job_store.worker_repository import WorkerRepository
    x = session_inputs
    calls = []
    def uncertain(*args, **kwargs):
        calls.append('release')
        raise OSError('acknowledgement lost')
    monkeypatch.setattr('services.job_worker.p3_holdout_release.release_holdout_view', uncertain)
    with pytest.raises(OSError):
        with x.session.stage(x.claim('HOLDOUT'), fence=lambda: None):
            x.session.release(object.__new__(WorkerRepository), trace_id='test:lost-ack')
    with pytest.raises(ValueError):
        x.session.release(object.__new__(WorkerRepository), trace_id='test:retry')
    assert calls == ['release']


def test_unprotected_session_profile_never_admits_a_view(tmp_path):
    from services.job_worker.p3_session import P3HoldoutSession
    from packages.runtime_release.config import ProtectedAuthorityError
    path = tmp_path/'session.json'
    path.write_text('{}')
    with pytest.raises(ProtectedAuthorityError):
        P3HoldoutSession(path, store=LocalArtifactStore(tmp_path))


def test_bound_view_lifetime_cannot_be_replaced(reference_seed):
    root, manifest, spec, *_ = reference_seed
    view = HoldoutCalculationView(build_holdout_calculation_view(manifest, spec, LocalArtifactStore(root)), manifest, spec)
    view.bind_lifetime(lambda: None)
    with pytest.raises(ValueError, match='cannot be replaced'):
        view.bind_lifetime(lambda: None)


def test_expiry_cannot_be_extended_by_wall_clock_rollback(session_inputs, monkeypatch):
    from datetime import datetime, timedelta
    x = session_inputs
    class ClockRollback(datetime):
        @classmethod
        def now(cls, tz=None):
            return x.session.profile.issued_at + timedelta(milliseconds=1)
    monkeypatch.setattr(x.module, 'datetime', ClockRollback)
    monkeypatch.setattr(x.module.time, 'monotonic', lambda: 1e30)
    with pytest.raises(ValueError, match='expired'):
        with x.session.stage(x.claim('HOLDOUT'), fence=lambda: None):
            pass


def test_session_exit_recomputes_with_same_view_without_raw_cas(session_inputs, exit_graph, reference_seed, monkeypatch):
    from pathlib import Path
    from packages.alpha_lifecycle.contracts.results import HoldoutEvaluationResult
    from packages.alpha_lifecycle.replica_store import _read
    from services.job_store.worker_repository import WorkerRepository
    from services.job_worker.p3_session import P3HoldoutSession
    x = session_inputs
    retained, intent, _, released = exit_graph
    for path in retained._root.iterdir():
        x.store.put_bytes(path.read_bytes(), media_type='application/json')
    for ref in (*reference_seed[-2], reference_seed[-1]):
        (x.store._root/ref.locator).unlink()
    manifest = _read(x.store, intent.body.holdout_evaluation_ref, HoldoutEvaluationResult).manifest_ref
    x.profile['primary_selection_ref'] = intent.body.primary_selection_ref
    view = HoldoutCalculationView(released.raw, manifest, x.spec)
    monkeypatch.setattr('services.job_worker.p3_holdout_release.release_holdout_view',
        lambda *args, **kwargs: (view, intent.body.holdout_request_ref, manifest))
    with P3HoldoutSession(Path('/run/trading-agent-p3/1-1/session.json'), store=x.store) as session:
        with session.stage(x.claim('HOLDOUT', primary_selection_ref=intent.body.primary_selection_ref), fence=lambda: None):
            session.release(object.__new__(WorkerRepository), trace_id='test:session-exit')
        with session.stage(x.claim('PARITY', holdout_manifest_ref=manifest,
            native_request_ref=session.native_commitment_ref), fence=lambda: None):
            assert len(session.native_requests()) == 2
        with session.stage(x.claim('PHASE_EXIT', **{name: getattr(intent.body, name)
            for name in type(intent.body).model_fields}), fence=lambda: None):
            proposal = session.prepare_exit()
            assert proposal.request.stage == 'EXIT_DECISION'
            assert len(proposal.entries) == 1
            assert 'REJECTED' in proposal.entries[0].canonical_event_text
    assert all(not (x.store._root/ref.locator).exists() for ref in (*reference_seed[-2], reference_seed[-1]))
    with pytest.raises(ValueError, match='closed'):
        _ = view.raw
