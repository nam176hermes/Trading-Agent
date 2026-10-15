"""Source checks; synthetic profiles grant no host or job authority."""
from types import SimpleNamespace
from pathlib import Path, PurePosixPath

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_worker.p3_host_profile import LocalOfficialHostProfileV2
from tests.p3.test_host_profile import _host_profile_document, _image_descriptor
from tests.p3.test_local_transport_fences import _identity


@pytest.fixture
def native_profile_inputs(tmp_path, monkeypatch):
    """Synthetic worker CAS/note; closure attestation never touches an actual P1 root."""
    from packages.alpha_lifecycle.operation_input import P3OperationInput, FAMILY_IDS
    from packages.alpha_lifecycle.native_request import NativeCommitment
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from services.job_worker.p3_session import publish_session_ref
    from services.job_worker import p1_nautilus_closure, p1_engine_spawn
    from tests.p3.test_local_research_admission import sealed
    from scripts import p3_local_host as module
    host = LocalOfficialHostProfileV2.model_validate_json(canonical_json_bytes(
        _host_profile_document('p3-local-official-host-profile-v2', identity=_identity(), image=_image_descriptor())))
    (tmp_path/'cas').mkdir(mode=0o700)
    store = LocalArtifactStore(tmp_path/'cas')
    manifest, spec = host.inventory_ref, host.integration_receipt_ref
    commitment = NativeCommitment.model_validate_json(canonical_json_bytes(sealed(dict(
        schema_version='p3-native-commitment-v1', source=host.payload.expected_source.model_dump(mode='json'),
        environment_ref=host.environment_ref.model_dump(mode='json'), manifest_ref=manifest.model_dump(mode='json'),
        instrument_spec_ref=spec.model_dump(mode='json'), primary_request_sha256='a'*64,
        baseline_request_sha256='b'*64))))
    ref = store.put_bytes(canonical_json_bytes(commitment), media_type='application/json')
    intent = P3OperationInput.model_validate_json(canonical_json_bytes(sealed(dict(
        schema_version='p3-operation-input-v1', workflow_operation='p3-native-parity-v1', operation='PARITY',
        input_set_ref=host.environment_ref.model_dump(mode='json'), allowed_alpha_ids=[FAMILY_IDS[0]],
        body=dict(holdout_manifest_ref=manifest.model_dump(mode='json'), instrument_spec_ref=spec.model_dump(mode='json'),
            primary_reference_ref=host.inventory_ref.model_dump(mode='json'),
            baseline_reference_ref=host.inventory_ref.model_dump(mode='json'), native_request_ref=ref.model_dump(mode='json'))))))
    intent_ref = store.put_bytes(canonical_json_bytes(intent), media_type='application/json')
    from packages.job_contracts import AlphaCampaignPayload
    payload = AlphaCampaignPayload.model_validate_json(canonical_json_bytes(dict(
        host.payload.model_dump(mode='json'), operation='PARITY', manifest_ref=intent_ref)))
    previous_job = 'job_'+'0'*32
    output = tmp_path/'output'
    publish_session_ref(output, previous_job, 'NATIVE_COMMITMENT', ref)
    profile = dict(schema_version='p3-native-launch-profile-v1', source=host.payload.expected_source.model_dump(mode='json'),
        session_sha256='a'*64, native_commitment_ref=ref.model_dump(mode='json'),
        job_id=host.job_id, attempt_id='attempt_'+'1'*32, runtime_root='/qualified/p1/runtime',
        artifact_directory='/qualified/p1/artifacts', sandbox_executable=host.sandbox_executable,
        sandbox_sha256=host.sandbox_sha256)
    command = dict(action='native-profile', profile=profile, payload=payload.model_dump(mode='json'),
        intent=intent.model_dump(mode='json'), environment_ref=host.environment_ref.model_dump(mode='json'),
        previous_job_id=previous_job, output_root=str(output), store_root=str(tmp_path/'cas'))
    calls = []
    closure = SimpleNamespace(sandbox=SimpleNamespace(version=host.sandbox_version,
        executable_sha256=host.sandbox_sha256))
    def attest(config):
        calls.append(config)
        return closure
    monkeypatch.setattr(p1_nautilus_closure, 'attest_p1_nautilus_closure', attest)
    monkeypatch.setattr(p1_engine_spawn, 'validate_p1_engine_closure_attestation', lambda value: value)
    return SimpleNamespace(module=module, host=host, store=store, intent=intent, payload=payload,
        commitment=commitment, ref=ref, command=command, closure=closure, calls=calls, output=output,
        store_root=tmp_path/'cas')


def test_native_profile_authenticates_worker_note_intent_and_qualified_closure(native_profile_inputs):
    x = native_profile_inputs
    assert x.module.native_profile_action(x.command) == x.command['profile']
    assert len(x.calls) == 1
    assert x.calls[0].runtime_root == Path('/qualified/p1/runtime')
    assert x.calls[0].artifact_directory == Path('/qualified/p1/artifacts')


@pytest.mark.parametrize('fault', ['note-custody', 'note-job', 'intent-ref', 'source', 'environment',
                                  'manifest', 'spec', 'sandbox-hash', 'sandbox-version', 'closure', 'extra'])
def test_native_profile_rejects_unqualified_or_unbound_inputs(native_profile_inputs, monkeypatch, fault):
    from services.job_worker import p1_nautilus_closure
    x = native_profile_inputs
    command = x.command
    if fault == 'note-custody':
        (x.output/command['previous_job_id']/'session-refs'/'native-commitment.log').chmod(0o644)
    elif fault == 'note-job':
        command['previous_job_id'] = 'job_'+'9'*32
    elif fault == 'intent-ref':
        command['profile']['native_commitment_ref'] = x.host.inventory_ref.model_dump(mode='json')
    elif fault == 'source':
        command['profile']['source']['tree_sha'] = 'f'*40
    elif fault == 'environment':
        command['environment_ref'] = x.host.inventory_ref.model_dump(mode='json')
    elif fault in {'manifest', 'spec'}:
        field = 'holdout_manifest_ref' if fault == 'manifest' else 'instrument_spec_ref'
        # A changed protected intent is not the authenticated CAS intent.
        from tests.p3.test_local_research_admission import sealed
        command['intent']['body'][field] = x.host.qualification_job_detail_ref.model_dump(mode='json')
        command['intent'] = sealed(command['intent'])
    elif fault == 'sandbox-hash':
        x.closure.sandbox.executable_sha256 = 'f'*64
    elif fault == 'sandbox-version':
        x.closure.sandbox.version = 'bubblewrap 0.0.0'
    elif fault == 'closure':
        def reject(_):
            raise ValueError('synthetic pinned closure rejected')
        monkeypatch.setattr(p1_nautilus_closure, 'attest_p1_nautilus_closure', reject)
    else:
        command['argv'] = ['untrusted']
    with pytest.raises(ValueError):
        x.module.native_profile_action(command)


def test_mounted_profile_uses_host_inventory_and_preserves_image_inventory():
    from scripts.p3_local_host import assemble_profile, host_inventory, HostInputs
    from packages.data_contracts import ArtifactRefV1
    original = _host_profile_document('p3-local-official-host-profile-v2',
        identity=_identity(), image=_image_descriptor())
    profile = LocalOfficialHostProfileV2.model_validate_json(canonical_json_bytes(original))
    fields = {key:original[key] for key in ('store_root', 'output_root',
        'worker_credentials_directory', 'qualification_job_detail_ref', 'postgres_binary_sha256')}
    settings = HostInputs.model_validate_json(canonical_json_bytes(dict(
        schema_version='p3-local-host-inputs-v1', job_api_token_file='/run/token', **fields)))
    host_ref = ArtifactRefV1.model_validate({**original['inventory_ref'], 'content_sha256':'a'*64,
                                          'locator':'a'*64+'.blob'})
    closure = SimpleNamespace(source=profile.payload.expected_source, environment_ref=profile.environment_ref,
        bound_job_id=profile.job_id, python_root=Path('/mounted/python'), image=profile.image,
        closure_sha256=profile.closure_sha256,
        sandbox=SimpleNamespace(executable=Path('/usr/bin/bwrap'), executable_sha256=profile.sandbox_sha256,
                                version=profile.sandbox_version),
        mounts=[SimpleNamespace(source=Path('/mounted/release/code.py'), target=PurePosixPath('/p3/release/code.py'),
                                size=3, mode=0o444, sha256='c'*64)])
    built = assemble_profile(settings, profile.identity, profile.payload, profile.integration_receipt_ref,
                             closure, host_ref)
    assert built.inventory_ref == host_ref and built.image.inventory_ref == profile.image.inventory_ref
    assert built.python_root == '/mounted/python'
    assert b'"source":"/mounted/release/code.py"' in canonical_json_bytes(host_inventory(closure))
    closure.source = None
    with pytest.raises(ValueError, match='source'):
        assemble_profile(settings, profile.identity, profile.payload, profile.integration_receipt_ref,
                         closure, host_ref)


def test_host_inputs_reject_relative_paths_and_unknown_fields():
    from scripts.p3_local_host import HostInputs
    document = _host_profile_document('p3-local-official-host-profile-v2')
    fields = {key:document[key] for key in ('store_root', 'output_root',
        'worker_credentials_directory', 'qualification_job_detail_ref', 'postgres_binary_sha256')}
    for extra in ({'job_api_token_file':'relative'}, {'job_api_token_file':'/run/token', 'argv':['anything']}):
        with pytest.raises(ValueError):
            HostInputs.model_validate_json(canonical_json_bytes(dict(
                schema_version='p3-local-host-inputs-v1', **fields, **extra)))


def test_worker_database_identity_reads_only_public_connection_metadata(tmp_path,monkeypatch):
    import io
    import json
    from scripts import p3_local_host as module
    credentials = tmp_path/'credentials'
    credentials.mkdir(mode=0o700)
    expected = {'database-host':'127.0.0.1','database-port':'55435','database-name':'synthetic'}
    for name,value in expected.items():
        path = credentials/name
        path.write_text(value)
        path.chmod(0o400)
    # A directory at this name makes any password read fail, even for root.
    (credentials/'database-password').mkdir()
    command = dict(action='database-identity',credentials_directory=str(credentials))
    output = io.BytesIO()
    monkeypatch.setattr(module.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(canonical_json_bytes(command))))
    monkeypatch.setattr(module.sys,'stdout',SimpleNamespace(buffer=output))
    module.main()
    assert json.loads(output.getvalue()) == expected


def test_late_host_transition_preserves_closure_and_refuses_replay_or_skips():
    from scripts.p3_local_host import next_session_host
    from packages.job_contracts import AlphaCampaignPayload
    document = _host_profile_document('p3-local-official-host-profile-v2',
        identity=_identity(), image=_image_descriptor())
    document['payload'].update(operation='PARITY', logical_trial_id='p3-native-parity-v1')
    previous = LocalOfficialHostProfileV2.model_validate_json(canonical_json_bytes(document))
    payload = AlphaCampaignPayload.model_validate_json(canonical_json_bytes(dict(
        document['payload'], operation='PHASE_EXIT', logical_trial_id='p3-phase-exit-v1')))
    current = next_session_host(previous, 'job_next', payload)
    fixed = {'job_id', 'payload', 'custodian_endpoint'}
    assert current.model_dump(exclude=fixed) == previous.model_dump(exclude=fixed)
    for job_id, proposed in ((previous.job_id,payload), ('job_next',previous.payload)):
        with pytest.raises(ValueError, match='out of order'):
            next_session_host(previous, job_id, proposed)


def test_worker_helper_executes_qualified_stage_and_reaps_failures(monkeypatch, tmp_path):
    """Real fork/reap with exec/drop substituted; no root execution."""
    import os
    import time
    import json
    from scripts import p3_local_host as module
    from scripts import p3_local_transport as transport
    monkeypatch.setattr(module.os, 'geteuid', lambda:0)
    monkeypatch.setattr(transport, 'drop_to_worker', lambda *_:None)
    environment = dict(P3_APPLICATION_ROOT=str(tmp_path), P3_APPLICATION_PYTHON='/qualified/python')
    def execute(path, argv, env):
        assert path == '/qualified/python' and argv == [path, '-I', '-B', '-m', 'scripts.p3_local_host']
        assert Path.cwd() == tmp_path and env == environment
        command = json.loads(os.read(0,4096))
        if command['action'] == 'fail':
            raise ValueError('private details must not escape')
        if command['action'] == 'timeout':
            time.sleep(10)
        os.write(1, b'{"ok":true}')
        os._exit(0)
    monkeypatch.setattr(module.os, 'execve', execute)
    assert module.as_worker(os.getuid(), os.getgid(), time.monotonic()+2,
                            environment, dict(action='ok')) == {'ok':True}
    for action in ('fail', 'timeout'):
        with pytest.raises(ValueError, match='worker-owned host preparation failed'):
            module.as_worker(os.getuid(), os.getgid(), time.monotonic()+0.05,
                             environment, dict(action=action))


@pytest.mark.parametrize('minutes', [50, 59, 70])
def test_run_prepares_host_before_image_and_passes_same_deadline(tmp_path, monkeypatch, minutes):
    from datetime import UTC, datetime, timedelta
    import time
    from types import SimpleNamespace
    from packages.alpha_lifecycle import authority
    from scripts import p3_local_transport as transport, p3_local_host as host
    from tests.p3.test_local_transport_fences import _run_request
    (tmp_path/'host-inputs.json').write_text('{}')
    events = []
    invoked = datetime.now(UTC)
    original_expiry = invoked+timedelta(minutes=59)
    deadline = time.monotonic()+59*60
    operation_expiry = invoked+timedelta(minutes=minutes)
    source = dict(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='v1',
                  closure_policy_sha256='c'*64, closure_sha256='d'*64)
    request = _run_request(source=source)
    environment = {'P3_APPLICATION_PYTHON':'/qualified/python', 'P3_AUTHORITY_REQUEST_FILE':'/protected/op'}
    prepared = (None, 'digest', None, environment)
    monkeypatch.setattr(transport, '_official_run_directory', lambda _:tmp_path)
    monkeypatch.setattr(host, 'prepare_host', lambda *_:(events.append('host'),prepared)[1])
    monkeypatch.setattr(transport, '_runtime_authority', lambda _:dict(
        expires_at=(invoked+timedelta(minutes=70)).isoformat().replace('+00:00', 'Z')))
    def authorize(path, expected_source, operation):
        assert str(path) == '/protected/op' and expected_source.model_dump() == source and operation == 'BASELINES'
        events.append('authorized-operation')
        return SimpleNamespace(authorization=SimpleNamespace(
            expires_at=operation_expiry, identity=SimpleNamespace(model_dump=lambda **_:request['identity'])))
    monkeypatch.setattr(authority, 'validate_request', authorize)
    monkeypatch.setattr(transport, '_prepare_image', lambda *_:(events.append('image'),dict(
        descriptor={}, inventory={}, summary={}))[1])
    def launch(*args, **kwargs):
        events.append('mount-and-worker')
        assert kwargs['host_preparation'] is prepared and kwargs['invocation_deadline'] == deadline
        assert kwargs['invocation_started'] == invoked
        assert kwargs['invocation_expires_at'] == min(original_expiry, operation_expiry)
        assert args[2] <= deadline
        assert 0 <= args[2]-time.monotonic() <= min(59, minutes)*60
        return {}
    monkeypatch.setattr(transport, 'run_session', launch)
    transport._command_run(request, tmp_path, deadline, invoked, original_expiry)
    assert events == ['host', 'authorized-operation', 'image', 'mount-and-worker']


def test_host_inputs_select_distinct_operation_staging_and_preflight(tmp_path, monkeypatch):
    from scripts import p3_local_transport as transport
    from tests.p3.test_local_transport_fences import _protected, _run_request, _runtime_profile, _host_profile
    (tmp_path/'host-inputs.json').write_text('{}')
    monkeypatch.setattr(transport, '_official_run_directory', lambda _:tmp_path)
    monkeypatch.setattr('packages.runtime_release.config.read_protected_canonical_json_current',
        _protected(**{'runtime.json':_runtime_profile()}))
    environment = transport._worker_environment(_run_request(), tmp_path/'mnt', 'release',
        tmp_path/'worker', host=_host_profile())
    nonce = '00000000-0000-4000-8000-000000000004'
    assert environment['P3_PREFLIGHT_DIRECTORY'] == str(tmp_path/'preflight'/nonce)
    assert environment['P3_MANIFEST_FILE'] == str(tmp_path/'operations'/nonce/'manifest.json')
    assert environment['P3_REVIEW_FILE'] == str(tmp_path/'operations'/nonce/'review.json')


def test_slow_job_readback_cannot_publish_after_original_deadline(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from scripts import p3_local_host as host, p3_local_transport as transport, p3_local_controller as controller
    from packages import local_authority
    document = _host_profile_document('p3-local-official-host-profile-v2',
        identity=_identity(), image=_image_descriptor())
    profile = LocalOfficialHostProfileV2.model_validate_json(canonical_json_bytes(document))
    settings = host.HostInputs.model_validate_json(canonical_json_bytes(dict(
        schema_version='p3-local-host-inputs-v1', job_api_token_file='/run/token',
        **{key:document[key] for key in ('store_root','output_root','worker_credentials_directory',
                                        'qualification_job_detail_ref','postgres_binary_sha256')})))
    closure = SimpleNamespace(source=profile.payload.expected_source, environment_ref=profile.environment_ref,
        bound_job_id=profile.job_id, python_root=Path(profile.python_root), image=profile.image,
        closure_sha256=profile.closure_sha256, mounts=[], sandbox=SimpleNamespace(
            executable=Path(profile.sandbox_executable), executable_sha256=profile.sandbox_sha256,
            version=profile.sandbox_version))
    now, calls, published = [0], [], []
    def authenticate(*_):
        calls.append(True)
        if len(calls) == 2:
            now[0] = 11
        return profile.payload
    monkeypatch.setattr(host.time, 'monotonic', lambda:now[0])
    monkeypatch.setattr(host, 'authenticate_job', authenticate)
    monkeypatch.setattr(host, 'host_inventory', lambda _: {})
    monkeypatch.setattr(host, 'as_worker', lambda *_:profile.inventory_ref.model_dump(mode='json'))
    monkeypatch.setattr(host, 'read_protected_canonical_json_current', lambda _:(None,'digest'))
    monkeypatch.setattr(transport, '_official_run_directory', lambda _:tmp_path)
    monkeypatch.setattr(transport, '_worker_environment', lambda *_, **__: {})
    monkeypatch.setattr(transport, 'build_v2_closure', lambda *_:closure)
    monkeypatch.setattr(local_authority, 'AuthorityStore', lambda _:SimpleNamespace(locked=nullcontext))
    monkeypatch.setattr(controller, 'publish_protected', lambda *_, **__:published.append(True))
    request = dict(identity=_identity(), worker_uid=980, worker_gid=980, release_root='release',
        source=document['payload']['expected_source'], environment_ref=document['environment_ref'],
        job_id=profile.job_id, bwrap_sha256='2'*64)
    with pytest.raises(ValueError, match='deadline expired'):
        host.activate_mounted_host(request, tmp_path, dict(external_libraries=[]), {}, 10,
            (settings, 'digest', dict(integration_receipt_ref=document['integration_receipt_ref']), {}))
    assert not published


def test_staging_keeps_exact_role_readable_bytes_and_refuses_nonce_replay(tmp_path, monkeypatch):
    """Real files/fsync; root custody and approved authority explicitly substituted."""
    import os
    import hashlib
    from contextlib import nullcontext
    from scripts import p3_local_controller as module, p3_local_transport as transport
    from packages.alpha_lifecycle import authority
    from packages.alpha_lifecycle.contracts.authority import LocalRunAuthorization
    from tests.p3.test_authority import _request
    from tests.p3.test_operation_input import intent
    from tests.p3.test_local_research_admission import sealed
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    request, source = _request('2026-01-01T01:00:00Z')
    auth = request['authorization']
    for key in ('issuer_workflow', 'issuer_run_id', 'issuer_attempt', 'digest'):
        auth.pop(key)
    review = {'synthetic_review':'no authority'}
    raw_review = canonical_json_bytes(review)
    digest = hashlib.sha256(raw_review).hexdigest()
    auth.update(schema_version='p3-local-run-authorization-v1', identity=_identity(),
        review_ref=dict(content_sha256=digest, size_bytes=len(raw_review),
                        locator=digest+'.blob', media_type='application/json'))
    auth = LocalRunAuthorization.model_validate_json(canonical_json_bytes(sealed(auth)))
    staged = SimpleNamespace(authorization=auth,
        operation_input=P3OperationInput.model_validate_json(intent(input_set_ref=auth.input_set_ref.model_dump(mode='json'))))
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(module.os, 'geteuid', lambda:0)
    monkeypatch.setattr(module.os, 'chown', lambda *_:None)
    monkeypatch.setattr(module.os, 'fchown', lambda *_:None)
    monkeypatch.setattr(module, '_committed_source', lambda:source)
    monkeypatch.setattr(module, '_protected_model', lambda path, _:
        staged if path.name=='request' else review if path.name=='review' else {})
    monkeypatch.setattr(authority, 'validate_local_operation_authority', lambda *_:staged)
    monkeypatch.setattr(authority, 'local_run_directory', lambda _:tmp_path)
    monkeypatch.setattr(transport, '_runtime_authority', lambda _:dict(uid=uid))
    monkeypatch.setattr(transport, 'TaskLock', lambda _:nullcontext())
    monkeypatch.setattr(module, 'AuthorityStore', lambda _:SimpleNamespace(locked=nullcontext))
    monkeypatch.setattr(module, 'publish_protected', lambda *_args, **_kwargs:None)
    original_lstat = Path.lstat
    def metadata(path):
        result = original_lstat(path)
        if path == tmp_path/'operations':
            values = list(result); values[4] = 0
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, 'lstat', metadata)
    # Staged request serialization follows the real typed request's JSON shape.
    monkeypatch.setattr(module, 'canonical_json_bytes', lambda value:
        canonical_json_bytes(dict(authorization=auth, operation_input=staged.operation_input))
        if value is staged else canonical_json_bytes(value))
    result = module.stage_operation(tmp_path/'request', tmp_path/'review')
    assert Path(result['review']).read_bytes() == raw_review
    assert Path(result['manifest']).read_bytes() == canonical_json_bytes(staged.operation_input)
    assert Path(result['review']).stat().st_mode & 0o777 == 0o440
    assert Path(result['preflight']).parent.stat().st_mode & 0o777 == 0o700
    assert not Path(result['preflight']).exists()  # Only enqueue creates it, under the worker UID.
    with pytest.raises(FileExistsError):
        module.stage_operation(tmp_path/'request', tmp_path/'review')
