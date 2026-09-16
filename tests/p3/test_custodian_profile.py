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
    monkeypatch.setattr(module,'derive_project_status',lambda root:dict(gates={
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
    elif fault=='gates':monkeypatch.setattr(module,'derive_project_status',lambda root:dict(gates={
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
