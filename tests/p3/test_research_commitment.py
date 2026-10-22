"""Synthetic compact bindings only; these tests grant no protected admission."""
import hashlib
import json
from datetime import UTC, datetime

import pytest

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.contracts.data import DatasetEvidence
from packages.data_contracts import ArtifactRefV1, PITQueryMode, PITQueryV1
from packages.engine_contracts.serialization import canonical_json_bytes


def _sealed(**value):
    return dict(value,digest=hashlib.sha256(canonical_json_bytes(value)).hexdigest())


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    from packages.data_catalog.artifact_store import LocalArtifactStore
    root=tmp_path_factory.mktemp('compact-structural-store')
    root.chmod(0o700)
    store=LocalArtifactStore(root)
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    source=SourceIdentity(commit_sha='1'*40,tree_sha='2'*40,closure_schema_version='fixture',
        closure_policy_sha256='3'*64,closure_sha256='4'*64)
    query=PITQueryV1(mode=PITQueryMode.SYSTEM_OBSERVED,valid_at=datetime(2025,9,1,tzinfo=UTC),
        cutoff=datetime(2026,9,11,12,tzinfo=UTC))
    placeholder=retain({})
    rows=tuple(retain({'fixture_row':i}) for i in range(2800))
    dataset=DatasetEvidence.model_validate_json(canonical_json_bytes(_sealed(
        schema_version='p3-dataset-evidence-v1',segment='RESEARCH',snapshot_ref=placeholder,
        query_digest=query.canonical_digest,date_range=dict(start='2018-01-01',end='2025-08-31'),
        usable_rows=2800,row_refs=rows,
        ordered_rows_digest=hashlib.sha256(canonical_json_bytes(tuple(row.content_sha256 for row in rows))).hexdigest(),
        vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE',observed_cutoff='2026-09-11T12:00:00Z',
        limitations=['retrospective.current_archive'])))
    dataset_ref=retain(dataset)
    producer=dict(producer_repository='nam176hermes/Trading-Agent',
        producer_workflow_ref='nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main',
        producer_run_id=1,producer_attempt=1)
    authority=dict(broker=False,live=False,network=False,production=False)
    backup=_sealed(schema_version='p3-research-backup-receipt-v1',source=source,
        inventory_content_sha256='7'*64,dataset_content_sha256=dataset_ref.content_sha256,
        snapshot_content_sha256=placeholder.content_sha256,object_inventory_content_sha256='8'*64,
        object_inventory_size_bytes=1000000,
        destination_namespace='p3.research.backup',object_version='8'*64,
        object_count=20000,total_bytes=10000000,verified_at='2026-09-11T12:01:00Z',
        status='READBACK_VERIFIED',authority=authority,**producer)
    value=_sealed(schema_version='p3-research-batch-commitment-v1',source=source,policy_digest='5'*64,
        segment='RESEARCH',query=query,inventory_content_sha256='7'*64,
        inventory_size_bytes=1,inventory_entry_count=2800,dataset_content_sha256=dataset_ref.content_sha256,
        dataset_digest=dataset.digest,snapshot_content_sha256=placeholder.content_sha256,
        batch_ordinal=1,predecessor_commitment_content_sha256=None,
        frozen_at='2026-09-11T11:59:59Z',completed_at='2026-09-11T12:00:00Z',issued_at='2026-09-11T12:02:00Z',
        backup_receipt_ref=retain(backup),status='PASS',authority=authority,**producer)
    return store,retain,source,query,dataset,dataset_ref,backup,value


def test_compact_commitment_binds_dataset_and_small_backup_without_inventory_edge(inputs,monkeypatch):
    from packages.alpha_lifecycle.research_custody import validate_research_batch_commitment
    store,retain,source,query,dataset,dataset_ref,backup,value=inputs
    ref=retain(value)
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('validator wrote an artifact')))
    result=validate_research_batch_commitment(ref,source=source,policy_digest='5'*64,
        dataset_ref=dataset_ref,dataset=dataset,store=store)
    assert result.inventory_entry_count==2800
    assert result.query==query
    assert result.backup_receipt_ref==ArtifactRefV1.model_validate(value['backup_receipt_ref'])
    assert 'inventory_ref' not in result.model_dump()


@pytest.mark.parametrize('path,replacement',[
    ('source.commit_sha','6'*40),('policy_digest','6'*64),
    ('dataset_content_sha256','6'*64),('dataset_digest','6'*64),
    ('snapshot_content_sha256','6'*64),('inventory_size_bytes',67108865),
    ('inventory_entry_count',2799),('batch_ordinal',2),
    ('predecessor_commitment_content_sha256','6'*64),
    ('frozen_at','2026-09-11T12:00:01Z'),('issued_at','2026-09-11T12:00:30Z'),
    ('query.mode','MARKET_AVAILABLE'),('query.cutoff','2026-09-11T12:00:01Z'),
    ('producer_workflow_ref','untrusted/workflow'),
    ('backup.source.commit_sha','6'*40),('backup.inventory_content_sha256','6'*64),
    ('backup.dataset_content_sha256','6'*64),('backup.snapshot_content_sha256','6'*64),
    ('backup.producer_run_id',2),('backup.producer_attempt',2),
    ('backup.object_version','6'*64),('backup.object_count',2),
    ('backup.total_bytes',20000),
    ('backup.verified_at','2026-09-11T12:02:01Z'),
    ('backup_receipt_ref.media_type','text/plain'),
])
def test_compact_commitment_rejects_mismatched_bindings_before_any_write(inputs,monkeypatch,path,replacement):
    from packages.alpha_lifecycle.research_custody import validate_research_batch_commitment
    store,retain,source,_,dataset,dataset_ref,backup,value=inputs
    value=json.loads(canonical_json_bytes(value))
    backup=json.loads(canonical_json_bytes(backup))
    parts=path.split('.')
    target=value
    if parts[0]=='backup':
        target=backup
        parts=parts[1:]
    for name in parts[:-1]:
        target=target[name]
    target[parts[-1]]=replacement
    if path.startswith('backup.'):
        backup.pop('digest')
        value['backup_receipt_ref']=retain(_sealed(**backup)).model_dump(mode='json')
    value.pop('digest')
    ref=retain(_sealed(**value))
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('validator wrote an artifact')))
    with pytest.raises(ValueError):
        validate_research_batch_commitment(ref,source=source,policy_digest='5'*64,
            dataset_ref=dataset_ref,dataset=dataset,store=store)


@pytest.mark.parametrize('fault',[
    'snapshot_zero','snapshot_media','snapshot_oversized',
    'row_zero','row_media','row_oversized','missing_disclosure','duplicate_row',
    'conflicting_size','known_object_count','known_byte_total','ordered_digest',
])
def test_compact_consumer_rejects_invalid_known_object_closure(inputs,monkeypatch,fault):
    from packages.alpha_lifecycle.research_custody import validate_research_batch_commitment
    store,retain,source,_,dataset,_,backup,value=inputs
    data=dataset.model_dump(mode='json',exclude={'digest'})
    backup=json.loads(canonical_json_bytes(backup))
    value=json.loads(canonical_json_bytes(value))
    backup.update(object_count=1000000,total_bytes=1073741824)
    if fault.startswith(('snapshot_','row_')):
        target=data['snapshot_ref'] if fault.startswith('snapshot_') else data['row_refs'][0]
        suffix=fault.split('_')[1]
        if suffix=='zero':
            target['size_bytes']=0
        elif suffix=='media':
            target['media_type']='text/plain'
        else:
            target['size_bytes']=67108865 if fault.startswith('snapshot_') else 65537
    elif fault=='missing_disclosure':
        data['limitations']=[]
    elif fault=='duplicate_row':
        data['row_refs'][1]=data['row_refs'][0].copy()
    elif fault=='conflicting_size':
        data['row_refs'][0]=dict(data['snapshot_ref'],size_bytes=data['snapshot_ref']['size_bytes']+1)
    elif fault=='ordered_digest':
        data['ordered_rows_digest']='6'*64
    dataset=DatasetEvidence.model_validate_json(canonical_json_bytes(_sealed(**data)))
    dataset_ref=retain(dataset)
    backup.update(dataset_content_sha256=dataset_ref.content_sha256,
        snapshot_content_sha256=dataset.snapshot_ref.content_sha256)
    value.update(dataset_content_sha256=dataset_ref.content_sha256,dataset_digest=dataset.digest,
        snapshot_content_sha256=dataset.snapshot_ref.content_sha256)
    if fault=='known_object_count':
        backup['object_count']=value['inventory_entry_count']
    elif fault=='known_byte_total':
        known={value['inventory_content_sha256']:value['inventory_size_bytes']}
        known.update({r.content_sha256:r.size_bytes for r in (dataset_ref,dataset.snapshot_ref,*dataset.row_refs)})
        backup.update(object_count=len(known),total_bytes=sum(known.values())-1)
    backup.pop('digest')
    value['backup_receipt_ref']=retain(_sealed(**backup))
    value.pop('digest')
    ref=retain(_sealed(**value))
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('validator wrote an artifact')))
    with pytest.raises(ValueError):
        validate_research_batch_commitment(ref,source=source,policy_digest='5'*64,
            dataset_ref=dataset_ref,dataset=dataset,store=store)


def test_later_commitment_has_a_predecessor_content_hash(inputs):
    from packages.alpha_lifecycle.research_custody import validate_research_batch_commitment
    store,retain,source,_,dataset,dataset_ref,_,value=inputs
    value=dict(value,batch_ordinal=2,predecessor_commitment_content_sha256='9'*64)
    value.pop('digest')
    result=validate_research_batch_commitment(retain(_sealed(**value)),source=source,
        policy_digest='5'*64,dataset_ref=dataset_ref,dataset=dataset,store=store)
    assert result.batch_ordinal==2
    # Matching that hash to a real protected predecessor is a separate admission check.
    assert result.predecessor_commitment_content_sha256=='9'*64


def test_backup_receipt_requires_bounded_out_of_band_inventory_size(inputs):
    from packages.alpha_lifecycle.research_custody import P3ResearchBackupReceipt
    backup=dict(inputs[-2])
    backup.pop('digest')
    backup.pop('object_inventory_size_bytes')
    with pytest.raises(ValueError):
        P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(_sealed(**backup)))
    for size in (0,67108865):
        with pytest.raises(ValueError):
            P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(
                _sealed(**backup,object_inventory_size_bytes=size)))
    result=P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(inputs[-2]))
    assert result.object_inventory_size_bytes==1000000
    assert 'object_inventory_ref' not in result.model_dump()


# --- T01: LOCAL producer lane, backward compatible with GITHUB v1 ---

_LOCAL_IDENTITY=dict(issuer_kind='LOCAL',issuer_identity='thenam176-local',
    run_id='eb0103be-3ab4-4f3e-86e0-31d1c9547ed2',attempt=1,host_id='b'*64,
    boot_id='39c646a1-867a-4597-9213-3a54eb34666a')

# Pinned from ee1aa3f before the LOCAL lane existed; GITHUB v1 must never move.
_GITHUB_BACKUP_DIGEST='975cbe1903cef4d49c42fe03e90e37556a30376901d72ca57b6ff61c95ab101c'
_GITHUB_BACKUP_BYTES='60f848bc5ce3d7c0adfc0dd41d0c9929654f10eba06daaf4c94c54d3e1dea85e'


def _github_backup_payload():
    source=dict(commit_sha='1'*40,tree_sha='2'*40,closure_schema_version='fixture',
        closure_policy_sha256='3'*64,closure_sha256='4'*64)
    return _sealed(schema_version='p3-research-backup-receipt-v1',source=source,
        inventory_content_sha256='7'*64,dataset_content_sha256='9'*64,snapshot_content_sha256='a'*64,
        object_inventory_content_sha256='8'*64,object_inventory_size_bytes=1000000,
        destination_namespace='p3.research.backup',object_version='8'*64,object_count=20000,
        total_bytes=10000000,verified_at='2026-09-11T12:01:00Z',status='READBACK_VERIFIED',
        authority=dict(broker=False,live=False,network=False,production=False),
        producer_repository='nam176hermes/Trading-Agent',
        producer_workflow_ref='nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main',
        producer_run_id=1,producer_attempt=1)


def test_github_v1_canonical_bytes_and_digest_are_unchanged():
    """Adding a LOCAL lane must not move GITHUB v1 canonical bytes or semantic digest."""
    from packages.alpha_lifecycle.research_custody import P3ResearchBackupReceipt
    payload=_github_backup_payload()
    assert hashlib.sha256(canonical_json_bytes(payload)).hexdigest()==_GITHUB_BACKUP_BYTES
    receipt=P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(payload))
    assert receipt.digest==_GITHUB_BACKUP_DIGEST


def test_github_model_cannot_represent_a_valid_local_producer():
    """The GITHUB-shaped receipt has no field able to carry a LOCAL run identity."""
    from packages.alpha_lifecycle.research_custody import P3ResearchBackupReceipt
    payload=_github_backup_payload()
    for key in ('producer_repository','producer_workflow_ref','producer_run_id','producer_attempt'):
        payload.pop(key)
    payload.pop('digest')
    with pytest.raises(ValueError):
        P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(
            _sealed(**payload,identity=_LOCAL_IDENTITY)))


def test_local_run_uuid_is_never_coerced_into_a_numeric_run_id():
    """A LOCAL UUID must not be smuggled through the numeric GitHub run-id field.

    Origin is proven by lane and authority, never by integer magnitude. A UUID has no
    integer form the GITHUB lane will read as a run id, and the integer that happens to
    share its bits is an ordinary in-domain run id that still yields a GITHUB receipt
    carrying no LOCAL identity. Rejecting it by size would narrow the accepted v1 domain
    (see test_github_v1_accepts_run_identity_beyond_the_removed_upper_bounds).
    """
    from packages.alpha_lifecycle.research_custody import (
        P3LocalResearchBackupReceipt, P3ResearchBackupReceipt, parse_research_backup_receipt)
    payload=_github_backup_payload()
    payload.pop('digest')
    for value in (_LOCAL_IDENTITY['run_id'],_LOCAL_IDENTITY['run_id'].replace('-','')):
        candidate=dict(payload,producer_run_id=value)
        with pytest.raises(ValueError):
            P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(_sealed(**candidate)))
    numeric=dict(payload,producer_run_id=int(_LOCAL_IDENTITY['run_id'].replace('-',''),16))
    parsed=parse_research_backup_receipt(canonical_json_bytes(_sealed(**numeric)))
    assert isinstance(parsed,P3ResearchBackupReceipt)
    assert not hasattr(parsed,'identity')
    with pytest.raises(ValueError):
        P3LocalResearchBackupReceipt.model_validate_json(canonical_json_bytes(_sealed(**numeric)))


# Measured on the v1 baseline commit 8460e12, before the LOCAL lane existed, for the exact
# payloads below. Reproducing them here proves the accepted GITHUB numeric domain and its
# canonical bytes were restored rather than merely re-pinned against the current code.
_GITHUB_COMMITMENT_BYTES='784d3c55b6a0e0e4b045d4bf70c66e3dc81d21c5d86b0cc04199ff7bbdc28a46'
_GITHUB_COMMITMENT_DIGEST='9ed47cbe29c6a00eb277344a2a5da03f7654b4b50e06d67d842dd4b295c463aa'
_V1_RUN_IDENTITY_VECTORS=(
    (9999999999999+1,1001,
     '9c5748096be00d3638ae5826a8a05a50bfbbf9ac7d1eeb9fab9781c54bdb1cd8',
     '42756b61db11c8f0c6c9666c8c58447f3fa44b28e8965dddafda08c42b304eeb',
     'e36bd212a731f337b19552205e3617f66ffd7edd2aca9fb1c77071c171d78b7e',
     'a1d9827fcd12ec613bc3a0423d34ea1bb8f64c2bb15e66653e2d1ddfab48bf9f'),
    (2**63-1,10**6,
     '66382a438d5bf4bd3655c476fea08b3840f3b938f9c084c5a3e0471f8e6d600d',
     '94507479e004977dd88ee341a9a34855af82f7c9215ce9ba98fca7b107715ac9',
     '70f3dbe7f08e369900e724b88d0ea3e9b84d42d878eaa56c0fa5398c255a532d',
     'e6a0dcb753bd3dcb98117dbed83d25b506121b2e415a7fc7fc9d216cb85492a1'),
    (int('eb0103be3ab44f3e86e031d1c9547ed2',16),1,
     '614a97625f4d55cf662e56e16a44f0634b1afef8f7e7f84544fa53cfb8b66c15',
     '0bfa8eca56a20e5e1377a1f9c84edaa9f83806ab8a03d2140cc43a782171351f',
     '83641f00b9696ae794bc5f298d7d882bf42fb31b6a5a59b773b7a2671d25e0da',
     '80c97f6c911a3a70afbaffcb3e085eaea9bed847d762c61cd184c7c412ecbc8d'),
)


def _github_commitment_payload(producer_run_id=1,producer_attempt=1):
    """Self-contained GITHUB v1 commitment; the backup ref is the store-independent
    ArtifactRefV1 that LocalArtifactStore.put_bytes derives from _github_backup_payload."""
    source=dict(commit_sha='1'*40,tree_sha='2'*40,closure_schema_version='fixture',
        closure_policy_sha256='3'*64,closure_sha256='4'*64)
    backup=canonical_json_bytes(_github_backup_payload())
    content=hashlib.sha256(backup).hexdigest()
    return _sealed(schema_version='p3-research-batch-commitment-v1',source=source,
        policy_digest='5'*64,segment='RESEARCH',
        query=dict(mode='SYSTEM_OBSERVED',valid_at='2025-09-01T00:00:00Z',
            cutoff='2026-09-11T12:00:00Z'),
        inventory_content_sha256='7'*64,inventory_size_bytes=1,inventory_entry_count=2800,
        dataset_content_sha256='9'*64,dataset_digest='b'*64,snapshot_content_sha256='a'*64,
        batch_ordinal=1,predecessor_commitment_content_sha256=None,
        frozen_at='2026-09-11T11:59:59Z',completed_at='2026-09-11T12:00:00Z',
        issued_at='2026-09-11T12:02:00Z',
        backup_receipt_ref=dict(content_sha256=content,size_bytes=len(backup),
            media_type='application/json',locator=content+'.blob'),
        status='PASS',authority=dict(broker=False,live=False,network=False,production=False),
        producer_repository='nam176hermes/Trading-Agent',
        producer_workflow_ref='nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main',
        producer_run_id=producer_run_id,producer_attempt=producer_attempt)


def test_github_v1_commitment_canonical_bytes_and_digest_are_unchanged():
    """The commitment lane needs the same frozen-bytes proof the backup lane already has."""
    from packages.alpha_lifecycle.research_custody import P3ResearchBatchCommitment
    payload=_github_commitment_payload()
    assert hashlib.sha256(canonical_json_bytes(payload)).hexdigest()==_GITHUB_COMMITMENT_BYTES
    commitment=P3ResearchBatchCommitment.model_validate_json(canonical_json_bytes(payload))
    assert commitment.digest==_GITHUB_COMMITMENT_DIGEST


@pytest.mark.parametrize(
    'run_id,attempt,backup_bytes,backup_digest,commitment_bytes,commitment_digest',
    _V1_RUN_IDENTITY_VECTORS)
def test_github_v1_accepts_run_identity_beyond_the_removed_upper_bounds(
        run_id,attempt,backup_bytes,backup_digest,commitment_bytes,commitment_digest):
    """Run ids and attempts above the briefly-added ceilings are valid v1 receipts.

    The ceilings 9999999999999 and 1000 were never part of v1. A historical receipt
    carrying a larger value must still parse to its original canonical bytes and digest.
    """
    from packages.alpha_lifecycle.research_custody import (
        P3ResearchBackupReceipt, P3ResearchBatchCommitment)
    backup=_github_backup_payload()
    backup.pop('digest')
    backup=_sealed(**dict(backup,producer_run_id=run_id,producer_attempt=attempt))
    assert hashlib.sha256(canonical_json_bytes(backup)).hexdigest()==backup_bytes
    assert P3ResearchBackupReceipt.model_validate_json(
        canonical_json_bytes(backup)).digest==backup_digest
    commitment=_github_commitment_payload(run_id,attempt)
    assert hashlib.sha256(canonical_json_bytes(commitment)).hexdigest()==commitment_bytes
    assert P3ResearchBatchCommitment.model_validate_json(
        canonical_json_bytes(commitment)).digest==commitment_digest


@pytest.mark.parametrize('value', [0,-1,-9999999999999])
def test_github_v1_still_rejects_non_positive_run_identity(value):
    """Removing the invented ceiling must not also remove the genuine v1 positive bound."""
    from packages.alpha_lifecycle.research_custody import (
        P3ResearchBackupReceipt, P3ResearchBatchCommitment)
    for payload,model in ((_github_backup_payload(),P3ResearchBackupReceipt),
                          (_github_commitment_payload(),P3ResearchBatchCommitment)):
        payload.pop('digest')
        for field in ('producer_run_id','producer_attempt'):
            with pytest.raises(ValueError):
                model.model_validate_json(canonical_json_bytes(
                    _sealed(**dict(payload,**{field:value}))))


def test_local_backup_receipt_round_trips_with_its_own_schema():
    """The LOCAL lane needs a distinct schema_version carrying LocalRunIdentity."""
    from packages.alpha_lifecycle.research_custody import P3LocalResearchBackupReceipt
    payload=_github_backup_payload()
    payload.pop('digest')
    for key in ('producer_repository','producer_workflow_ref','producer_run_id','producer_attempt'):
        payload.pop(key)
    payload['schema_version']='p3-local-research-backup-receipt-v1'
    receipt=P3LocalResearchBackupReceipt.model_validate_json(canonical_json_bytes(
        _sealed(**payload,identity=_LOCAL_IDENTITY)))
    assert receipt.identity.issuer_kind=='LOCAL'
    assert str(receipt.identity.run_id)==_LOCAL_IDENTITY['run_id']
    assert receipt.status=='READBACK_VERIFIED'


def test_local_schema_rejects_github_producer_fields():
    """A LOCAL receipt must not also declare GitHub producer provenance."""
    from packages.alpha_lifecycle.research_custody import P3LocalResearchBackupReceipt
    payload=_github_backup_payload()
    payload.pop('digest')
    payload['schema_version']='p3-local-research-backup-receipt-v1'
    with pytest.raises(ValueError):
        P3LocalResearchBackupReceipt.model_validate_json(canonical_json_bytes(
            _sealed(**payload,identity=_LOCAL_IDENTITY)))


def test_parse_research_backup_receipt_dispatches_on_schema_version():
    """One union parser resolves each lane without letting either accept the other."""
    from packages.alpha_lifecycle.research_custody import (
        P3LocalResearchBackupReceipt, P3ResearchBackupReceipt, parse_research_backup_receipt)
    github=_github_backup_payload()
    assert isinstance(parse_research_backup_receipt(canonical_json_bytes(github)),P3ResearchBackupReceipt)
    local=dict(github)
    local.pop('digest')
    for key in ('producer_repository','producer_workflow_ref','producer_run_id','producer_attempt'):
        local.pop(key)
    local['schema_version']='p3-local-research-backup-receipt-v1'
    parsed=parse_research_backup_receipt(canonical_json_bytes(_sealed(**local,identity=_LOCAL_IDENTITY)))
    assert isinstance(parsed,P3LocalResearchBackupReceipt)
    unknown=dict(github)
    unknown.pop('digest')
    unknown['schema_version']='p3-research-backup-receipt-v2'
    with pytest.raises(ValueError):
        parse_research_backup_receipt(canonical_json_bytes(_sealed(**unknown)))


@pytest.mark.parametrize('fault', [None, 'github_backup', 'wrong_local_identity'])
def test_local_commitment_consumes_matching_local_backup(inputs, fault):
    from packages.alpha_lifecycle.research_custody import validate_research_batch_commitment
    store, retain, source, _, dataset, dataset_ref, backup, commitment = inputs

    def local(value, schema, identity):
        value = json.loads(canonical_json_bytes(value))
        for key in ('digest', 'producer_repository', 'producer_workflow_ref',
                    'producer_run_id', 'producer_attempt'):
            value.pop(key)
        return _sealed(**dict(value, schema_version=schema, identity=identity))

    if fault != 'github_backup':
        identity = dict(_LOCAL_IDENTITY)
        if fault == 'wrong_local_identity':
            identity['attempt'] += 1
        backup = local(backup, 'p3-local-research-backup-receipt-v1', identity)
    commitment = local(dict(commitment, backup_receipt_ref=retain(backup)),
                       'p3-local-research-batch-commitment-v1', _LOCAL_IDENTITY)
    ref = retain(commitment)
    if fault:
        with pytest.raises(ValueError, match='producer|lane'):
            validate_research_batch_commitment(ref, source=source, policy_digest='5'*64,
                dataset_ref=dataset_ref, dataset=dataset, store=store)
    else:
        result = validate_research_batch_commitment(ref, source=source, policy_digest='5'*64,
            dataset_ref=dataset_ref, dataset=dataset, store=store)
        assert str(result.identity.run_id) == _LOCAL_IDENTITY['run_id']


_AUTHORITY_WORKFLOW_REF='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main'


def _v2(payload, schema):
    value={k:v for k,v in payload.items() if k!='digest'}
    return _sealed(**dict(value,schema_version=schema,producer_workflow_ref=_AUTHORITY_WORKFLOW_REF))


def test_github_v2_binds_the_protected_authority_workflow_and_leaves_v1_frozen():
    """v2 names the existing p3-authority.yml producer operation; v1 bytes never move."""
    from packages.alpha_lifecycle.research_custody import (
        P3ResearchBackupReceiptV2, P3ResearchBatchCommitmentV2, parse_research_backup_receipt,
        parse_research_batch_commitment)
    backup=_v2(_github_backup_payload(),'p3-research-backup-receipt-v2')
    assert isinstance(parse_research_backup_receipt(canonical_json_bytes(backup)),P3ResearchBackupReceiptV2)
    commitment=_v2(_github_commitment_payload(),'p3-research-batch-commitment-v2')
    assert isinstance(parse_research_batch_commitment(canonical_json_bytes(commitment)),P3ResearchBatchCommitmentV2)
    for schema,payload in (('p3-research-backup-receipt-v2',_github_backup_payload()),
                           ('p3-research-batch-commitment-v2',_github_commitment_payload())):
        stale=_sealed(**{k:v for k,v in dict(payload,schema_version=schema).items() if k!='digest'})
        with pytest.raises(ValueError):
            (parse_research_backup_receipt if 'backup' in schema else parse_research_batch_commitment)(
                canonical_json_bytes(stale))
    test_github_v1_canonical_bytes_and_digest_are_unchanged()
    test_github_v1_commitment_canonical_bytes_and_digest_are_unchanged()


@pytest.mark.parametrize('fault',[None,'v1_backup','run'])
def test_github_v2_commitment_requires_a_v2_backup_of_the_same_run(inputs,fault):
    from packages.alpha_lifecycle.research_custody import validate_research_batch_commitment
    store,retain,source,query,dataset,dataset_ref,backup,value=inputs
    backup_v2=backup if fault=='v1_backup' else _v2(dict(backup,producer_run_id=2 if fault=='run' else 1),
                                                      'p3-research-backup-receipt-v2')
    commitment=_v2(dict(value,backup_receipt_ref=retain(backup_v2)),'p3-research-batch-commitment-v2')
    ref=retain(commitment)
    def run():
        return validate_research_batch_commitment(ref,source=source,policy_digest='5'*64,
            dataset_ref=dataset_ref,dataset=dataset,store=store)
    if fault is None:
        assert run().schema_version=='p3-research-batch-commitment-v2'
    else:
        with pytest.raises(ValueError):
            run()
