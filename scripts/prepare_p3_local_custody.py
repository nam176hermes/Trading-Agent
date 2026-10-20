"""Seal private custody proposals or serve one admitted connection through existing owners."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import secrets
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.alpha_lifecycle.data_view import seal_research_dataset
from packages.alpha_lifecycle.local_custody import (
    GithubCustodyCipherProfile, LocalCustodyCipherProfile, decrypt_custody_bundle, seal_custody_rows,
)
from packages.alpha_lifecycle.local_qualification import _host_identity
from packages.alpha_lifecycle.pit_evidence import _reference
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArrowSchemaV1, DatasetPartitionManifestV3, PITQueryV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.runtime_release.config import read_protected_canonical_json_current
from scripts.seal_p3_dataset import _committed_source
from services.job_worker.p3_custodian_profile import (
    GithubCustodianProfile, LocalCustodianProfile, read_custodian_profile, serve_profiled_release,
)

GITHUB_CIPHER_PATH=Path('/etc/trading-agent-p3/github/custody-cipher.json')


def prepare(configuration):
    if os.geteuid()!=configuration['custodian_uid']:
        raise ValueError('custody producer must run as the declared custodian UID')
    source=_committed_source()
    # GITHUB custody precedes any run: it binds this host, and the hosted session binds the run.
    github=configuration.get('lane')=='GITHUB'
    if github:
        if 'identity' in configuration:
            raise ValueError('GITHUB custody is identity-free until a run is attested')
        identity=None
    else:
        identity=LocalRunIdentity.model_validate_json(canonical_json_bytes(configuration['identity']))
        if _host_identity()!=(identity.host_id,identity.boot_id):
            raise ValueError('custody producer host or boot differs')
    output=Path(configuration['output'])
    if output.exists() or output.resolve().is_relative_to(ROOT):
        raise ValueError('custody proposal output must be new and outside source')
    store=LocalArtifactStore(Path(configuration['store_root']))
    backup=LocalArtifactStore(Path(configuration['backup_root']))
    datasets=[]
    for name,segment in (('holdout','HOLDOUT'),('buffer','BUFFER')):
        item=configuration[name]
        dataset=seal_research_dataset(tuple(DatasetPartitionManifestV3.model_validate_json(
            canonical_json_bytes(partition)) for partition in item['partitions']),
            PITQueryV1.model_validate_json(canonical_json_bytes(item['query'])),
            ArrowSchemaV1.model_validate_json(canonical_json_bytes(item['schema'])),store)
        if dataset.segment!=segment:
            raise ValueError('custody dataset namespace differs')
        datasets.append(dataset)
    key=secrets.token_hex(32).encode()
    key_ref=store.put_bytes(key,media_type='application/octet-stream')
    if (backup.put_bytes(key,media_type='application/octet-stream')!=key_ref
        or backup.read_bytes(key_ref)!=key):
        raise ValueError('custody private key backup differs')
    result=seal_custody_rows(store=store,backup=backup,row_refs=(*datasets[0].row_refs,*datasets[1].row_refs),
        key=key,custodian_uid=configuration['custodian_uid'],research_uid=configuration['research_uid'],
        custodian_identity=configuration['custodian_identity'],research_identity=configuration['research_identity'],
        access_policy_digest=configuration['access_policy_digest'],binary=Path('/usr/bin/gpg'),
        binary_sha256=configuration['gpg_binary_sha256'],version=configuration['gpg_version'])
    pins=dict(source=source,custodian_uid=os.geteuid(),store_root=str(store._root),key_ref=key_ref,
        gpg_binary_sha256=configuration['gpg_binary_sha256'],gpg_version=configuration['gpg_version'])
    profile=(GithubCustodyCipherProfile.model_validate_json(canonical_json_bytes(dict(
            schema_version='p3-github-custody-cipher-profile-v1',host_id=_host_identity()[0],**pins)))
        if github else LocalCustodyCipherProfile.model_validate_json(canonical_json_bytes(dict(
            schema_version='p3-local-custody-cipher-profile-v1',identity=identity,**pins))))
    result.update(source=source,identity=identity,cipher_profile_proposal=profile,
        holdout_dataset_ref=store.put_bytes(canonical_json_bytes(datasets[0]),media_type='application/json'),
        buffer_dataset_ref=store.put_bytes(canonical_json_bytes(datasets[1]),media_type='application/json'))
    if _committed_source()!=source:
        raise ValueError('source changed during custody production')
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(canonical_json_bytes(result)+b'\n');stream.flush();os.fsync(stream.fileno())


def materialize(store_root, acquisitions, output):
    """Holdout/buffer partitions and their PIT query from retained public-archive acquisitions."""
    import time
    from datetime import UTC, datetime, timedelta
    from packages.alpha_lifecycle.acquisition import daily_arrow_table, validate_acquisition_receipt
    from packages.alpha_lifecycle.data_view import _RANGES
    from packages.alpha_lifecycle.pit_evidence import materialize_daily_revision
    from packages.data_contracts import ArtifactRefV1
    if output.exists() or output.resolve().is_relative_to(ROOT):
        raise ValueError('custody segment output must be new and outside source')
    store=LocalArtifactStore(store_root)
    refs=[ArtifactRefV1.model_validate_json(line) for line in acquisitions.read_text().splitlines() if line]
    days=[validate_acquisition_receipt(ref,store).day for ref in refs]
    first,last=_RANGES['HOLDOUT'][0],_RANGES['BUFFER'][1]
    if days!=[first+timedelta(days=offset) for offset in range((last-first).days+1)]:
        raise ValueError('acquisitions do not match exact holdout and buffer coverage')
    ingested_at=datetime.now(UTC)  # after every acquisition; never backdated
    segments={}
    for name,dataset,(start,end) in (('holdout','p3.holdout.daily',_RANGES['HOLDOUT']),
                                     ('buffer','p3.buffer.daily',_RANGES['BUFFER'])):
        selected=[ref for ref,day in zip(refs,days) if start<=day<=end]
        segments[name]=dict(partitions=[materialize_daily_revision(ref,store,ingested_at=ingested_at,
            dataset=dataset).partition.model_dump(mode='json') for ref in selected],
            schema=daily_arrow_table(selected[0],store)[0].model_dump(mode='json'))
    cutoff=datetime.now(UTC).replace(microsecond=0)+timedelta(seconds=1)
    query=dict(mode='SYSTEM_OBSERVED',valid_at=(last+timedelta(days=1)).isoformat()+'T00:00:00Z',
        cutoff=cutoff.isoformat().replace('+00:00','Z'))
    for item in segments.values():item['query']=query
    while datetime.now(UTC)<=cutoff:  # sealing requires the query cutoff to have passed
        time.sleep(0.2)
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(canonical_json_bytes(segments)+b'\n');stream.flush();os.fsync(stream.fileno())


def plaintext_reader(profile):
    """Decrypt only through the protected cipher pins of the profile's own lane."""
    github=isinstance(profile,GithubCustodianProfile)
    cipher_path=(GITHUB_CIPHER_PATH if github else
        Path('/etc/trading-agent/local-qualification')/profile.identity.issuer_identity/'custody-cipher.json')

    def read_plaintext(custody):
        document,digest=read_protected_canonical_json_current(cipher_path)
        if github:
            from packages.alpha_lifecycle.github_run import read_github_run
            cipher=GithubCustodyCipherProfile.model_validate_json(canonical_json_bytes(document))
            # Release is an admission point: a fresh attestation of exactly this run and host.
            run,_=read_github_run(profile.identity.run_id,profile.identity.attempt,admission=True)
            bound=(cipher.host_id==run.host_id and _host_identity()==(run.host_id,run.boot_id))
        else:
            cipher=LocalCustodyCipherProfile.model_validate_json(canonical_json_bytes(document))
            bound=(cipher.identity==profile.identity
                and _host_identity()==(cipher.identity.host_id,cipher.identity.boot_id))
        if (not bound or cipher.source!=profile.request.source or cipher.custodian_uid!=os.geteuid()):
            raise ValueError('custody cipher profile identity, source or host differs')
        _reference(cipher.key_ref,64,media='application/octet-stream')
        store=LocalArtifactStore(Path(cipher.store_root))
        raw=decrypt_custody_bundle(custody,store=store,key=store.read_bytes(cipher.key_ref),
            binary=Path('/usr/bin/gpg'),binary_sha256=cipher.gpg_binary_sha256,version=cipher.gpg_version)
        if read_protected_canonical_json_current(cipher_path)[1]!=digest:
            raise ValueError('custody cipher profile changed during decryption')
        return raw
    return read_plaintext


def serve(profile_path, profile_digest, listener_fd):
    from services.job_worker.p3_session import HOSTED_SESSION_MINUTES
    profile=read_custodian_profile(profile_path,profile_digest)
    if (not isinstance(profile,(LocalCustodianProfile,GithubCustodianProfile))
            or os.geteuid()!=profile.endpoint.custodian_uid):
        raise ValueError('custody listener requires its protected custodian UID')
    read_plaintext=plaintext_reader(profile)

    if listener_fd<=2:
        raise ValueError('custody requires a dedicated inherited listener')
    with socket.socket(fileno=listener_fd) as listener:
        if (listener.family!=socket.AF_UNIX or listener.type!=socket.SOCK_SEQPACKET
            or listener.getsockname()!=profile.endpoint.socket_path):
            raise ValueError('custody listener differs from protected endpoint')
        listener.settimeout(HOSTED_SESSION_MINUTES*60)
        channel,_=listener.accept()
        with channel:
            serve_profiled_release(channel,profile_path=profile_path,profile_digest=profile_digest,
                read_plaintext=read_plaintext)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    prepare_parser=commands.add_parser('prepare')
    prepare_parser.add_argument('--configuration',type=Path,required=True)
    materialize_parser=commands.add_parser('materialize')
    materialize_parser.add_argument('--store',type=Path,required=True)
    materialize_parser.add_argument('--acquisitions',type=Path,required=True)
    materialize_parser.add_argument('--output',type=Path,required=True)
    serve_parser=commands.add_parser('serve')
    serve_parser.add_argument('--profile',type=Path,required=True)
    serve_parser.add_argument('--profile-digest',required=True)
    serve_parser.add_argument('--listener-fd',type=int,required=True)
    args=parser.parse_args(argv)
    if args.command=='prepare':prepare(json.loads(args.configuration.read_bytes()))
    elif args.command=='materialize':materialize(args.store,args.acquisitions,args.output)
    else:serve(args.profile,args.profile_digest,args.listener_fd)
    return 0


if __name__=='__main__':
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    raise SystemExit(main())
