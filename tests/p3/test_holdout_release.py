"""Private synthetic custody transport; never cross-UID/runtime qualification."""
import hashlib
import os
import shutil
import socket
import struct

import pytest

from packages.alpha_lifecycle.contracts.authority import CustodyRecord
from packages.alpha_lifecycle.contracts.execution import HoldoutManifest
from packages.alpha_lifecycle.replica_store import _read
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_worker.engine_spawn import _sealed_memfd, _memfd_create
from tests.p3.test_reference_input import reference_seed  # noqa: F401
from tests.p3.test_holdout_disclosure_client import disclosure  # noqa: F401


@pytest.fixture
def released(reference_seed):
    from packages.alpha_lifecycle.custody import build_attestation
    root, manifest_ref, spec_ref, _, rows, buffer = reference_seed
    store = LocalArtifactStore(root)
    refs = (*rows, buffer)
    raw = canonical_json_bytes({ref.locator: store.read_bytes(ref).decode() for ref in refs})
    ciphertext = store.put_bytes(b'synthetic encrypted bytes', media_type='application/octet-stream')
    attestation = build_attestation(ciphertext_ref=ciphertext,
        plaintext_bundle_digest=hashlib.sha256(raw).hexdigest(), access_policy_digest='e'*64,
        custodian_identity='custodian', research_identity='research',
        custodian_uid=17001, research_uid=17002, row_refs=refs)
    attestation_ref = store.put_bytes(canonical_json_bytes(attestation), media_type='application/json')
    payload = dict(schema_version='p3-custody-record-v1',
        holdout_commitment=attestation.holdout_commitment, ciphertext_ref=ciphertext,
        plaintext_bundle_digest=attestation.plaintext_bundle_digest,
        access_policy_digest=attestation.access_policy_digest, custodian_identity='custodian',
        research_identity='research', custodian_attestation_ref=attestation_ref,
        classification='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND')
    custody = CustodyRecord.model_validate({**payload,'digest':hashlib.sha256(canonical_json_bytes(payload)).hexdigest()})
    return store, manifest_ref, spec_ref, refs, raw, attestation, custody


def test_authenticated_bundle_builds_exact_view_without_retaining_plaintext(released,tmp_path):
    from packages.alpha_lifecycle.custody import released_view
    store, manifest_ref, spec_ref, refs, raw, attestation, custody = released
    # The research store really lacks every protected row, not a mock of a read.
    metadata=tmp_path/'metadata';metadata.mkdir(mode=0o700)
    protected={ref.locator for ref in refs}
    for path in store._root.iterdir():
        if path.name not in protected:shutil.copy2(path,metadata/path.name)
    store=LocalArtifactStore(metadata)
    before = set(store._root.iterdir())
    view = released_view(raw, attestation, custody, manifest_ref, spec_ref, store,
        custodian_uid=17001, research_uid=17002)
    assert len(__import__('json').loads(view.raw)) == 681
    assert set(store._root.iterdir()) == before
    assert all(view.read_bytes(ref) for ref in refs)
    assert _read(view, manifest_ref, HoldoutManifest).schema_version == 'p3-holdout-manifest-v1'


@pytest.mark.parametrize('fault',['extra','missing','noncanonical','digest','uid','same_uid','commitment'])
def test_bundle_substitution_rejects_before_view(released,fault):
    from packages.alpha_lifecycle.custody import released_view
    store, manifest_ref, spec_ref, refs, raw, attestation, custody = released
    import json
    records=json.loads(raw)
    if fault=='extra': records['unapproved']='{}'
    if fault=='missing': records.pop(refs[0].locator)
    if fault=='digest': records[refs[0].locator]+=' '
    raw=canonical_json_bytes(records)
    if fault=='noncanonical':raw+=b'\n'
    if fault=='commitment':custody=custody.model_copy(update={'holdout_commitment':'0'*64})
    with pytest.raises(ValueError):
        released_view(raw,attestation,custody,manifest_ref,spec_ref,store,
            custodian_uid=17002 if fault=='same_uid' else 17001,
            research_uid=17003 if fault=='uid' else 17002)


@pytest.mark.parametrize('count',[0,1,2,40])
def test_received_descriptors_are_closed_on_all_paths(count,monkeypatch):
    from services.job_worker.p3_holdout_release import receive_bundle
    reader,writer=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    descriptor=_sealed_memfd('synthetic-holdout',b'{}',mode=0o400)
    closed=[]; real_close=os.close
    def close(fd):
        closed.append(fd);real_close(fd)
    try:
        ancillary=[] if not count else [(socket.SOL_SOCKET,socket.SCM_RIGHTS,struct.pack(f'{count}i',*([descriptor]*count)))]
        writer.sendmsg([b'{"request_sha256":"'+b'a'*64+b'"}'],ancillary)
        monkeypatch.setattr('services.job_worker.p3_holdout_release.os.close',close)
        if count==1:
            assert receive_bundle(reader,'a'*64)==b'{}'
        else:
            with pytest.raises(ValueError):receive_bundle(reader,'a'*64)
        assert closed or count==0
        for fd in closed:
            with pytest.raises(OSError):os.fstat(fd)
    finally:
        real_close(descriptor);reader.close();writer.close()


@pytest.fixture
def release_request(released):
    from packages.alpha_lifecycle.custody import ReleaseRequest
    store,manifest_ref,_,_,_,attestation,custody=released
    custody_ref=store.put_bytes(canonical_json_bytes(custody),media_type='application/json')
    return ReleaseRequest(schema_version='p3-holdout-release-request-v1',
        source=_read(store,manifest_ref,HoldoutManifest).source,job_id='job_release',
        attempt_id='attempt_release',worker_id='worker_release',authorization_digest='a'*64,
        intent_digest='b'*64,holdout_request_sha256='c'*64,custody_record_ref=custody_ref,
        holdout_commitment=custody.holdout_commitment,plaintext_bundle_digest=custody.plaintext_bundle_digest,
        row_inventory_digest=hashlib.sha256(canonical_json_bytes(attestation.row_refs)).hexdigest(),
        custodian_identity='custodian',research_identity='research',custodian_uid=17001,research_uid=17002)


@pytest.mark.parametrize('fault',[None,'commit_ack_lost','absent_readback','changed_readback','wrong_role','role_drift','catalog_drift'])
def test_sql_owner_requires_commit_then_exact_readback_and_never_retries(release_request,monkeypatch,fault):
    from datetime import UTC,datetime,timedelta
    from types import SimpleNamespace
    from psycopg import OperationalError
    from services.job_store.config import JobStoreSettings
    from services.job_store import p3_custodian_release as sql
    events=[];stamp=datetime.now(UTC)
    class Connection:
        def __init__(self):self.wrote=False
        def __enter__(self):events.append('connect');return self
        def __exit__(self,kind,*args):
            events.append('rollback' if kind else 'commit')
            if self.wrote and fault=='commit_ack_lost':raise OperationalError('synthetic lost ack')
        def execute(self,query,args=None):
            if query==sql.IDENTITY:
                row=dict(current_user='trading_job_worker' if fault=='wrong_role' else 'trading_p3_custodian',
                    session_user='trading_p3_custodian',version_num=sql.REVISION,restricted=fault!='role_drift')
            elif query==getattr(sql,'CATALOG_SQL',None):
                row={'catalog_sha256':'0'*64 if fault=='catalog_drift' else sql.CATALOG_SHA256}
            else:
                assert args==(canonical_json_bytes(release_request).decode(),)
                self.wrote=query==sql.CLAIM
                events.append('claim' if self.wrote else 'read')
                if not self.wrote:assert 'commit' in events
                row=dict(request_sha256=hashlib.sha256(canonical_json_bytes(release_request)).hexdigest(),released_at=stamp)
                if not self.wrote and fault=='absent_readback':row=None
                if not self.wrote and fault=='changed_readback':row['released_at']+=timedelta(seconds=1)
            return SimpleNamespace(fetchone=lambda:row)
    monkeypatch.setattr(sql.psycopg.Connection,'connect',lambda *args,**kwargs:Connection())
    repository=sql.CustodianReleaseRepository(JobStoreSettings('localhost',5432,'synthetic','trading_p3_custodian','synthetic'))
    if fault:
        with pytest.raises((ValueError,OperationalError)):repository.claim(release_request)
    else:repository.claim(release_request)
    assert events.count('claim')==(0 if fault in {'wrong_role','role_drift','catalog_drift'} else 1)
    assert events.count('read')==(0 if fault in {'wrong_role','role_drift','catalog_drift','commit_ack_lost'} else 1)


def test_peer_check_uses_kernel_identity_without_accepting_declared_uid():
    from services.job_worker.p3_holdout_release import _peer
    reader,writer=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    try:
        _peer(reader,os.getuid())
        with pytest.raises(ValueError,match='peer'):_peer(reader,os.getuid()+1)
    finally:reader.close();writer.close()


@pytest.mark.parametrize('fault',[None,'claim','fence','profile','request'])
def test_custodian_does_not_load_plaintext_before_durable_admission(released,release_request,monkeypatch,fault):
    from services.job_store.p3_custodian_release import CustodianReleaseRepository
    from services.job_worker import p3_holdout_release as wire
    store,_,_,_,raw,_,_=released
    endpoint=wire.CustodianEndpoint(schema_version='p3-custodian-endpoint-v1',socket_path='/run/p3/custodian.sock',
        custodian_uid=17001,research_uid=17002,custodian_identity='custodian',research_identity='research')
    # These two patches explicitly model the OS split; the separate peer test
    # exercises real SO_PEERCRED. This test cannot qualify cross-UID custody.
    monkeypatch.setattr(wire.os,'geteuid',lambda:17001)
    monkeypatch.setattr(wire,'_peer',lambda *args:None)
    events=[]
    def claim(self,request):
        events.append('claim')
        if fault=='claim':raise RuntimeError('synthetic SQL reject')
    def fence(self,request):
        events.append('fence')
        if fault=='fence':raise RuntimeError('synthetic revocation')
    monkeypatch.setattr(CustodianReleaseRepository,'claim',claim)
    monkeypatch.setattr(CustodianReleaseRepository,'fence',fence)
    monkeypatch.setattr(wire,'send_bundle',lambda channel,digest,value:events.append('send'))
    def plaintext(custody):events.append('plaintext');return raw
    reader,writer=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    try:
        writer.send(canonical_json_bytes(release_request))
        def run():
            wire.serve_release(reader,endpoint,store,repository=object.__new__(CustodianReleaseRepository),
                request_sha256='0'*64 if fault=='request' else hashlib.sha256(canonical_json_bytes(release_request)).hexdigest(),
                read_plaintext=plaintext,recheck_profile=lambda:None if fault=='profile' else endpoint)
        if fault:
            with pytest.raises((ValueError,RuntimeError)):run()
            assert 'plaintext' not in events and 'send' not in events
        else:
            run();assert events==['claim','fence','plaintext','fence','send']
    finally:reader.close();writer.close()


@pytest.mark.parametrize('fault',['unsealed','writable','wrong_mode','wrong_binding','empty'])
def test_receiver_rejects_unsafe_memory_files(fault):
    from services.job_worker.p3_holdout_release import receive_bundle
    reader,writer=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    fd=_sealed_memfd('synthetic',b'{}' if fault!='empty' else b'',mode=0o600 if fault in {'wrong_mode','writable'} else 0o400)
    if fault=='unsealed':
        os.close(fd);fd=_memfd_create('synthetic');os.write(fd,b'{}');os.fchmod(fd,0o400)
    if fault=='writable':
        replacement=os.open(f'/proc/self/fd/{fd}',os.O_RDWR);os.close(fd);fd=replacement;os.fchmod(fd,0o400)
    try:
        packet=canonical_json_bytes({'request_sha256':('b' if fault=='wrong_binding' else 'a')*64})
        writer.sendmsg([packet],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,struct.pack('i',fd))])
        with pytest.raises(ValueError):receive_bundle(reader,'a'*64)
    finally:os.close(fd);reader.close();writer.close()


def test_parent_rejects_unapproved_metadata_before_sql_or_custodian(disclosure,monkeypatch):
    from services.job_store.worker_repository import WorkerRepository
    from services.job_worker import p3_holdout_release as wire
    store,claim,_,_=disclosure
    calls=[]
    monkeypatch.setattr(WorkerRepository,'consume_p3_holdout',lambda *args,**kwargs:calls.append('SQL'))
    monkeypatch.setattr(wire,'request_bundle',lambda *args,**kwargs:calls.append('socket'))
    endpoint=wire.CustodianEndpoint(schema_version='p3-custodian-endpoint-v1',socket_path='/run/p3/custodian.sock',
        custodian_uid=17001,research_uid=17002,custodian_identity='custodian',research_identity='research')
    with pytest.raises(ValueError):
        wire.release_holdout_view(claim,object.__new__(WorkerRepository),store,
            endpoint=endpoint,fence=lambda:None,trace_id='test:metadata')
    assert not calls


@pytest.mark.parametrize('fault', [None, 'after_bundle', 'between_metadata', 'after_view'])
def test_release_retention_rechecks_authority_and_revokes_failed_view(released, disclosure, tmp_path, monkeypatch, fault):
    """Real custody/view validation; metadata approval, socket and SQL are synthetic."""
    from dataclasses import replace
    from packages.alpha_lifecycle import custody as custody_module, holdout
    from packages.alpha_lifecycle.contracts.authority import HoldoutRequest
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from services.job_store.worker_repository import WorkerRepository
    from services.job_worker import p3_holdout_release as wire
    source, manifest_ref, spec_ref, protected, raw, _, custody = released
    metadata, claim, graph, _ = disclosure
    root = tmp_path/'release-metadata'; root.mkdir(mode=0o700)
    for store in (source, metadata):
        for path in store._root.iterdir():
            if path.name not in {ref.locator for ref in protected}:
                shutil.copy2(path, root/path.name)
    store = LocalArtifactStore(root)
    custody_ref = store.put_bytes(canonical_json_bytes(custody), media_type='application/json')
    intent = _read(store, claim.payload.manifest_ref, P3OperationInput)
    document = intent.model_dump(mode='json', exclude={'digest'})
    document['body'].update(custody_record_ref=custody_ref.model_dump(mode='json'),
        instrument_spec_ref=spec_ref.model_dump(mode='json'))
    document['digest'] = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    intent_ref = store.put_bytes(canonical_json_bytes(document), media_type='application/json')
    claim = replace(claim, payload=claim.payload.model_copy(update={'manifest_ref': intent_ref}))
    request, manifest = HoldoutRequest.model_validate_json(graph[0]), _read(store, manifest_ref, HoldoutManifest)
    monkeypatch.setattr(holdout, 'validate_holdout_operation_input', lambda *args, **kwargs: (request, manifest))
    endpoint = wire.CustodianEndpoint(schema_version='p3-custodian-endpoint-v1', socket_path='/run/p3/custodian.sock',
        custodian_uid=17001, research_uid=17002, custodian_identity='custodian', research_identity='research')
    current = True
    events, writes, views = [], [], []
    def fence():
        if not current: raise ValueError('synthetic authority revoked')
    monkeypatch.setattr(WorkerRepository, 'consume_p3_holdout', lambda *args, **kwargs: events.append('consume'))
    def bundle(*args, **kwargs):
        nonlocal current
        kwargs['fence'](); kwargs['consume']()
        events.append('bundle')
        if fault == 'after_bundle': current = False
        return raw
    monkeypatch.setattr(wire, 'request_bundle', bundle)
    put = store.put_bytes
    def retain(value, *, media_type):
        nonlocal current
        ref = put(value, media_type=media_type); writes.append(ref)
        if fault == 'between_metadata': current = False
        return ref
    monkeypatch.setattr(store, 'put_bytes', retain)
    build = custody_module.released_view
    def view(*args, **kwargs):
        nonlocal current
        result = build(*args, **kwargs); views.append(result)
        if fault == 'after_view': current = False
        return result
    monkeypatch.setattr(custody_module, 'released_view', view)
    def release():
        return wire.release_holdout_view(claim, object.__new__(WorkerRepository), store,
            endpoint=endpoint, fence=fence, trace_id='test:retention')
    if fault:
        with pytest.raises(ValueError, match='revoked'): release()
        assert len(writes) == {'after_bundle': 0, 'between_metadata': 1, 'after_view': 2}[fault]
        for value in views:
            with pytest.raises(ValueError, match='closed'): _ = value.raw
    else:
        value, _, _ = release()
        assert len(writes) == 2 and value.read_bytes(protected[0])
        value.close()
    assert events == ['consume', 'bundle']
    assert all(not (root/ref.locator).exists() for ref in protected)


def test_custodian_credentials_use_fixed_separate_role(monkeypatch):
    from services.job_store import p3_custodian_release as sql
    from services.job_store.config import JOB_PLANE_DATABASE_USERS
    values={'CREDENTIALS_DIRECTORY':'/run/credentials/p3-custodian'}
    credentials={'database-host':'localhost','database-port':'5432',
        'database-name':'synthetic','database-password':'private-test-value'}
    calls=[]
    def read(context,name):
        assert context==values
        calls.append(name)
        return credentials[name]
    monkeypatch.setattr(sql,'read_systemd_credential',read,raising=False)
    repository=sql.CustodianReleaseRepository.from_systemd_credentials(values)
    assert repository._settings.user=='trading_p3_custodian'
    assert set(calls)==set(credentials)
    assert 'private-test-value' not in repr(repository._settings)
    assert 'trading_p3_custodian' not in JOB_PLANE_DATABASE_USERS
