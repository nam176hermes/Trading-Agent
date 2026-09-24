"""Source checks; synthetic profiles grant no host or job authority."""
from types import SimpleNamespace
from pathlib import Path, PurePosixPath

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_worker.p3_host_profile import LocalOfficialHostProfileV2
from tests.p3.test_host_profile import _host_profile_document, _image_descriptor
from tests.p3.test_local_transport_fences import _identity


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


def test_run_prepares_host_before_image_and_passes_same_deadline(tmp_path, monkeypatch):
    from scripts import p3_local_transport as transport, p3_local_host as host
    from tests.p3.test_local_transport_fences import _run_request
    (tmp_path/'host-inputs.json').write_text('{}')
    events = []
    environment = {'P3_APPLICATION_PYTHON':'/qualified/python'}
    prepared = (None, 'digest', None, environment)
    monkeypatch.setattr(transport, '_official_run_directory', lambda _:tmp_path)
    monkeypatch.setattr(host, 'prepare_host', lambda *_:(events.append('host'),prepared)[1])
    monkeypatch.setattr(transport, '_runtime_authority', lambda _:dict(expires_at='2099-01-01T00:00:00Z'))
    monkeypatch.setattr(transport, '_prepare_image', lambda *_:(events.append('image'),dict(
        descriptor={}, inventory={}, summary={}))[1])
    def launch(*args, **kwargs):
        events.append('mount-and-worker')
        assert kwargs['host_preparation'] is prepared and kwargs['invocation_deadline'] == 1000
        assert args[2] == 1000
        return {}
    monkeypatch.setattr(transport, 'run_session', launch)
    transport._command_run(_run_request(), tmp_path, 1000)
    assert events == ['host', 'image', 'mount-and-worker']


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
