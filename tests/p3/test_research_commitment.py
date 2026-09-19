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
    """A LOCAL UUID must not be smuggled through the numeric GitHub run-id field."""
    from packages.alpha_lifecycle.research_custody import P3ResearchBackupReceipt
    payload=_github_backup_payload()
    payload.pop('digest')
    for value in (_LOCAL_IDENTITY['run_id'],_LOCAL_IDENTITY['run_id'].replace('-',''),
                  int(_LOCAL_IDENTITY['run_id'].replace('-',''),16)):
        candidate=dict(payload,producer_run_id=value)
        with pytest.raises(ValueError):
            P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(_sealed(**candidate)))


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
