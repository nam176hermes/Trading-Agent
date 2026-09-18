"""Synthetic profile reader injection; no protected host provisioning."""
import hashlib
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_holdout_release import released,release_request  # noqa: F401
from tests.p3.test_reference_input import reference_seed  # noqa: F401


@pytest.fixture
def custodian_profile(released,release_request,monkeypatch):
    from services.job_worker import p3_custodian_profile as module
    store,*_=released
    document=dict(schema_version='p3-custodian-host-profile-v1',request=release_request.model_dump(mode='json'),
        endpoint=dict(schema_version='p3-custodian-endpoint-v1',socket_path='/run/p3/custodian.sock',
            custodian_uid=17001,research_uid=17002,custodian_identity='custodian',research_identity='research'),
        metadata_root=str(store._root),credentials_directory='/run/credentials/p3-custodian',
        catalog_sha256=module.CUSTODIAN_CATALOG_SHA256)
    def digest():return hashlib.sha256(canonical_json_bytes(document)+b'\n').hexdigest()
    monkeypatch.setattr(module,'read_protected_canonical_json_current',lambda path:(document,digest()))
    monkeypatch.setattr(module,'canonical_source_identity',lambda root:release_request.source.model_dump(mode='json'))
    monkeypatch.setattr(__import__('packages.project_status',fromlist=['derive_project_status']),'derive_project_status',lambda root:dict(gates={
        'HWC_SOURCE_READY':'PASS','PRE_P3_READY':'PASS'},p3_alpha_development_allowed=True))
    return module,document,digest


def test_unprotected_custodian_profile_rejected(tmp_path):
    from services.job_worker.p3_custodian_profile import read_custodian_profile
    path=tmp_path/'profile.json';path.write_bytes(b'{}\n');path.chmod(0o444)
    with pytest.raises(ValueError):read_custodian_profile(path,hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize('fault',[None,'digest','source','gates','endpoint','catalog','credentials','request','changed'])
def test_custodian_profile_binds_current_source_request_endpoint_and_catalog(custodian_profile,monkeypatch,fault):
    module,document,digest=custodian_profile
    expected=digest()
    if fault=='digest':expected='0'*64
    elif fault=='source':document['request']['source']['commit_sha']='9'*40
    elif fault=='gates':monkeypatch.setattr(__import__('packages.project_status',fromlist=['derive_project_status']),'derive_project_status',lambda root:dict(gates={
        'HWC_SOURCE_READY':'PASS','PRE_P3_READY':'HELD'},p3_alpha_development_allowed=False))
    elif fault=='endpoint':document['endpoint']['research_uid']=17003
    elif fault=='catalog':document['catalog_sha256']='0'*64
    elif fault=='credentials':document['credentials_directory']='/run/../credentials'
    elif fault=='request':document['request']['unknown']=True
    elif fault=='changed':
        original=module.read_protected_canonical_json_current;calls=[]
        def read(path):
            calls.append(path)
            return original(path) if len(calls)==1 else (document,'0'*64)
        monkeypatch.setattr(module,'read_protected_canonical_json_current',read)
    if fault!='digest':expected=digest()
    if fault:
        with pytest.raises(ValueError):module.read_custodian_profile(Path('/synthetic/profile'),expected)
    else:
        profile=module.read_custodian_profile(Path('/synthetic/profile'),expected)
        assert profile.request.job_id==document['request']['job_id']
        assert profile.endpoint.research_uid==17002


@pytest.mark.parametrize('fault',[None,'request','profile'])
def test_profiled_connection_rechecks_before_loading_plaintext(custodian_profile,released,release_request,monkeypatch,fault):
    import socket
    from services.job_worker import p3_holdout_release as wire
    from services.job_store.p3_custodian_release import CustodianReleaseRepository
    module,document,digest=custodian_profile
    _,_,_,_,raw,_,_=released
    calls=[]
    def credentials(cls,values, *, session=False):
        assert session is False
        assert values=={'CREDENTIALS_DIRECTORY':document['credentials_directory']}
        return object.__new__(CustodianReleaseRepository)
    monkeypatch.setattr(CustodianReleaseRepository,'from_systemd_credentials',classmethod(credentials))
    def claim(self,request):
        calls.append('claim')
        if fault=='profile':document['endpoint']['research_uid']=17003
    monkeypatch.setattr(CustodianReleaseRepository,'claim',claim)
    monkeypatch.setattr(CustodianReleaseRepository,'fence',lambda *args:calls.append('fence'))
    # Simulate the independent UID; the separate profile-reader tests retain real directory checks.
    monkeypatch.setattr(module,'_directory_identity',lambda path:(path.stat().st_dev,path.stat().st_ino))
    monkeypatch.setattr(wire.os,'geteuid',lambda:17001)
    monkeypatch.setattr(wire,'_peer',lambda *args:None)
    monkeypatch.setattr(wire,'send_bundle',lambda *args:calls.append('send'))
    reader,writer=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    def plaintext(custody):calls.append('plaintext');return raw
    try:
        request=release_request if fault!='request' else release_request.model_copy(update={'job_id':'job_other'})
        writer.send(canonical_json_bytes(request))
        def run():module.serve_profiled_release(reader,profile_path=Path('/synthetic/profile'),
            profile_digest=digest(),read_plaintext=plaintext)
        if fault:
            with pytest.raises(ValueError):run()
            assert 'send' not in calls and 'plaintext' not in calls
            if fault=='request':assert calls==[]
        else:
            run();assert calls==['claim','fence','plaintext','fence','send']
    finally:reader.close();writer.close()


def test_wrong_process_uid_cannot_read_custodian_credentials(custodian_profile,monkeypatch):
    import socket
    from services.job_store.p3_custodian_release import CustodianReleaseRepository
    module,_,digest=custodian_profile
    calls=[]
    def credentials(*args):calls.append('credentials');raise AssertionError('credentials read by wrong UID')
    monkeypatch.setattr(CustodianReleaseRepository,'from_systemd_credentials',credentials)
    reader,writer=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    try:
        with pytest.raises(ValueError):module.serve_profiled_release(reader,profile_path=Path('/synthetic/profile'),
            profile_digest=digest(),read_plaintext=lambda _:b'')
        assert not calls
    finally:reader.close();writer.close()


@pytest.mark.parametrize('session', [False, True])
def test_custodian_catalog_is_selected_by_explicit_profile_version(custodian_profile, session):
    module, document, digest = custodian_profile
    if session:
        document['schema_version'] = 'p3-custodian-session-profile-v1'
        document['catalog_sha256'] = module.SESSION_CATALOG_SHA256
    assert module.read_custodian_profile(Path('/synthetic/profile'), digest()).schema_version == document['schema_version']
    document['catalog_sha256'] = module.CUSTODIAN_CATALOG_SHA256 if session else module.SESSION_CATALOG_SHA256
    with pytest.raises(ValueError, match='catalog'):
        module.read_custodian_profile(Path('/synthetic/profile'), digest())


@pytest.mark.parametrize('fault',[None,'issuer','authorization','intent','holdout','revoked'])
def test_local_custodian_binds_protected_operation_before_plaintext(custodian_profile,released,monkeypatch,fault):
    from datetime import UTC,datetime,timedelta
    from types import SimpleNamespace
    from packages.alpha_lifecycle.contracts.authority import LocalRunAuthorization
    from tests.p3.test_local_qualification import sealed
    from packages.alpha_lifecycle import holdout
    module,document,digest=custodian_profile;store,*_=released
    now=datetime.now(UTC)
    identity=dict(issuer_kind='LOCAL',issuer_identity='synthetic',run_id='00000000-0000-4000-8000-000000000002',
        attempt=1,host_id='a'*64,boot_id='00000000-0000-4000-8000-000000000001')
    ref=document['request']['custody_record_ref']
    auth=LocalRunAuthorization.model_validate_json(canonical_json_bytes(sealed(dict(
        schema_version='p3-local-run-authorization-v1',identity=identity,input_set_ref=ref,review_ref=ref,
        operation='HOLDOUT',allowed_alpha_ids=['a0.donchian-20-10-close-confirm'],
        issued_at=now.isoformat().replace('+00:00','Z'),expires_at=(now+timedelta(hours=1)).isoformat().replace('+00:00','Z'),
        nonce='00000000-0000-4000-8000-000000000003',authority=dict(broker=False,live=False,network=False,production=False)))))
    auth_ref=store.put_bytes(canonical_json_bytes(auth),media_type='application/json')
    document.update(schema_version='p3-local-custodian-profile-v1',identity=identity,
        authorization_ref=auth_ref.model_dump(mode='json'),catalog_sha256=module.SESSION_CATALOG_SHA256)
    document['request'].update(authorization_digest=auth_ref.content_sha256,
        holdout_request_sha256=hashlib.sha256(canonical_json_bytes({'synthetic':'request'})).hexdigest())
    calls=[]
    monkeypatch.setattr(module,'require_p3_source_ready',lambda root,who:calls.append('gates'))
    def protected(actual,source,**kwargs):
        assert actual==auth and source==module.SourceIdentity.model_validate(document['request']['source'])
        calls.append('protected-operation')
        if fault=='revoked':raise ValueError('revoked')
        return SimpleNamespace(operation_input=SimpleNamespace(digest='0'*64 if fault=='intent' else document['request']['intent_digest']))
    monkeypatch.setattr(module,'validate_local_operation_authority',protected)
    monkeypatch.setattr(holdout,'derive_holdout_request',lambda *a,**k:{'synthetic':'request'})
    if fault=='issuer':document['identity']={**identity,'attempt':2}
    elif fault=='authorization':document['request']['authorization_digest']='0'*64
    elif fault=='holdout':document['request']['holdout_request_sha256']='0'*64
    if fault:
        with pytest.raises(ValueError):module.read_custodian_profile(Path('/synthetic/profile'),digest())
    else:
        assert module.read_custodian_profile(Path('/synthetic/profile'),digest()).identity==auth.identity
        assert calls==['gates','protected-operation']
