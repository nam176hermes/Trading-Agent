"""SQL-client fault injection uses synthetic metadata, never a release authority."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC,datetime,timedelta
import hashlib
from types import SimpleNamespace

import pytest
from psycopg import OperationalError

from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
from packages.alpha_lifecycle.contracts.authority import RunAuthorization
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.job_contracts import JobType
from services.job_store.records import ClaimedJob
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.p3_holdout_fixture import _inputs,CONSUME,READ


@pytest.fixture
def disclosure(tmp_path):
    source=SourceIdentity(commit_sha='a'*40,tree_sha='b'*40,closure_schema_version='v1',
        closure_policy_sha256='c'*64,closure_sha256='d'*64)
    authorization,graph=_inputs(source)
    root=tmp_path/'inputs';root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    for raw in (*authorization,*graph): store.put_bytes(raw.encode(),media_type='application/json')
    auth=RunAuthorization.model_validate_json(authorization[0])
    intent=P3OperationInput.model_validate_json(authorization[1])
    payload=build_alpha_campaign_payload(auth,source,intent.workflow_operation,operation_input=intent)
    claim=ClaimedJob('job_disclosure',JobType.ALPHA_CAMPAIGN,payload,'attempt_disclosure',1,
        'worker_disclosure','x'*32,datetime.now(UTC)+timedelta(minutes=5),1)
    row=dict(authorization_digest=hashlib.sha256(authorization[0].encode()).hexdigest(),
        intent_digest=intent.digest,holdout_request_sha256=hashlib.sha256(graph[0].encode()).hexdigest(),
        disclosed_at=datetime.now(UTC))
    return store,claim,graph,row


class Pool:
    def __init__(self,row,*,fault=None):
        self.row=row;self.fault=fault;self.calls=[];self.events=[];self.committed=False

    @contextmanager
    def connection(self):
        self.events.append('connect')
        yield self
        self.events.append('close')

    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try: yield self
        except BaseException:
            self.events.append('rollback')
            raise
        self.committed=True
        self.events.append('commit')
        if self.fault in {'lost_ack','absent_after_lost_ack','wrong_after_lost_ack'}:
            raise OperationalError('synthetic acknowledgement loss')

    def execute(self,sql,args):
        assert sql in {CONSUME,READ}
        self.calls.append((sql,args))
        self.events.append('consume' if sql==CONSUME else 'read')
        row=dict(self.row)
        if sql==READ:
            assert self.committed,'readback occurred before consumption commit'
            if self.fault in {'absent','absent_after_lost_ack'}: row=None
            elif self.fault=='wrong_after_lost_ack': row['holdout_request_sha256']='9'*64
            elif self.fault=='changed_time': row['disclosed_at']+=timedelta(seconds=1)
        return SimpleNamespace(fetchone=lambda:row)


@pytest.mark.parametrize('fault',[None,'lost_ack','absent','absent_after_lost_ack','wrong_after_lost_ack','changed_time'])
def test_disclosure_confirms_committed_sql_before_return_and_never_repeats_unknown_write(disclosure,fault):
    store,claim,graph,row=disclosure
    pool=Pool(row,fault=fault)
    repository=object.__new__(WorkerRepository);repository._pool=pool
    before={p.name:p.read_bytes() for p in store._root.iterdir()}
    if fault in {None,'lost_ack'}:
        assert repository.consume_p3_holdout(claim,store,trace_id='test:disclosure')==row['disclosed_at']
    else:
        with pytest.raises((ValueError,RuntimeError)):
            repository.consume_p3_holdout(claim,store,trace_id='test:disclosure')
    assert pool.calls==[
        (CONSUME,(claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token,*graph,'test:disclosure')),
        (READ,(claim.job_id,claim.attempt_id,claim.worker_id,
            row['authorization_digest'],row['intent_digest'],row['holdout_request_sha256'],'test:disclosure')),
    ]
    assert pool.events.index('commit')<pool.events.index('read') and pool.events.count('connect')==2
    assert before=={p.name:p.read_bytes() for p in store._root.iterdir()}


@pytest.mark.parametrize('fault',['authorization_digest','intent_digest','holdout_request_sha256','missing_time',
    'naive_time','expired_time','extra'])
def test_disclosure_rejects_unbound_sql_result_before_commit(disclosure,fault):
    store,claim,_,row=disclosure
    if fault.endswith('digest') or fault=='holdout_request_sha256': row[fault]='9'*64
    elif fault=='missing_time': row.pop('disclosed_at')
    elif fault=='naive_time': row['disclosed_at']=row['disclosed_at'].replace(tzinfo=None)
    elif fault=='expired_time': row['disclosed_at']+=timedelta(days=2)
    else: row['extra']='untrusted'
    pool=Pool(row)
    repository=object.__new__(WorkerRepository);repository._pool=pool
    with pytest.raises((ValueError,RuntimeError)):
        repository.consume_p3_holdout(claim,store,trace_id='test:disclosure')
    assert not pool.committed and pool.events[-1]=='rollback' and len(pool.calls)==1


@pytest.mark.parametrize('fault',['lease','operation','source','trace','noncanonical','oversized'])
def test_invalid_disclosure_transport_never_reaches_sql(disclosure,fault):
    store,claim,_,row=disclosure
    trace='bad trace' if fault=='trace' else 'test:disclosure'
    if fault=='lease': claim=replace(claim,lease_expires_at=datetime.now(UTC)-timedelta(seconds=1))
    elif fault=='operation': claim=replace(claim,payload=claim.payload.model_copy(update={'operation':'OOS'}))
    elif fault=='source':
        claim=replace(claim,payload=claim.payload.model_copy(update={
            'expected_source':claim.payload.expected_source.model_copy(update={'commit_sha':'9'*40})}))
    elif fault in {'noncanonical','oversized'}:
        raw=store.read_bytes(claim.payload.manifest_ref)
        raw=b' '+raw if fault=='noncanonical' else b' '*65537
        ref=store.put_bytes(raw,media_type='application/json')
        claim=replace(claim,payload=claim.payload.model_copy(update={'manifest_ref':ref}))
    pool=Pool(row)
    repository=object.__new__(WorkerRepository);repository._pool=pool
    with pytest.raises((ValueError,RuntimeError)):
        repository.consume_p3_holdout(claim,store,trace_id=trace)
    assert not pool.calls
