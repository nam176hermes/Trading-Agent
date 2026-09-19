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
    LocalCustodyCipherProfile, decrypt_custody_bundle, seal_custody_rows,
)
from packages.alpha_lifecycle.local_qualification import _host_identity
from packages.alpha_lifecycle.pit_evidence import _reference
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArrowSchemaV1, DatasetPartitionManifestV3, PITQueryV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.runtime_release.config import read_protected_canonical_json_current
from scripts.seal_p3_dataset import _committed_source
from services.job_worker.p3_custodian_profile import (
    LocalCustodianProfile, read_custodian_profile, serve_profiled_release,
)


def prepare(configuration):
    if os.geteuid()!=configuration['custodian_uid']:
        raise ValueError('custody producer must run as the declared custodian UID')
    source=_committed_source()
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
    profile=LocalCustodyCipherProfile.model_validate_json(canonical_json_bytes(dict(
        schema_version='p3-local-custody-cipher-profile-v1',identity=identity,source=source,
        custodian_uid=os.geteuid(),store_root=str(store._root),key_ref=key_ref,
        gpg_binary_sha256=configuration['gpg_binary_sha256'],gpg_version=configuration['gpg_version'])))
    result.update(source=source,identity=identity,cipher_profile_proposal=profile,
        holdout_dataset_ref=store.put_bytes(canonical_json_bytes(datasets[0]),media_type='application/json'),
        buffer_dataset_ref=store.put_bytes(canonical_json_bytes(datasets[1]),media_type='application/json'))
    if _committed_source()!=source:
        raise ValueError('source changed during custody production')
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(canonical_json_bytes(result)+b'\n');stream.flush();os.fsync(stream.fileno())


def serve(profile_path, profile_digest, listener_fd):
    profile=read_custodian_profile(profile_path,profile_digest)
    if not isinstance(profile,LocalCustodianProfile) or os.geteuid()!=profile.endpoint.custodian_uid:
        raise ValueError('LOCAL custody listener requires its protected custodian UID')
    cipher_path=Path('/etc/trading-agent/local-qualification')/profile.identity.issuer_identity/'custody-cipher.json'

    def read_plaintext(custody):
        document,digest=read_protected_canonical_json_current(cipher_path)
        cipher=LocalCustodyCipherProfile.model_validate_json(canonical_json_bytes(document))
        if (cipher.identity!=profile.identity or cipher.source!=profile.request.source
            or cipher.custodian_uid!=os.geteuid()
            or _host_identity()!=(cipher.identity.host_id,cipher.identity.boot_id)):
            raise ValueError('custody cipher profile identity, source or host differs')
        _reference(cipher.key_ref,64,media='application/octet-stream')
        store=LocalArtifactStore(Path(cipher.store_root))
        raw=decrypt_custody_bundle(custody,store=store,key=store.read_bytes(cipher.key_ref),
            binary=Path('/usr/bin/gpg'),binary_sha256=cipher.gpg_binary_sha256,version=cipher.gpg_version)
        if read_protected_canonical_json_current(cipher_path)[1]!=digest:
            raise ValueError('custody cipher profile changed during decryption')
        return raw

    if listener_fd<=2:
        raise ValueError('custody requires a dedicated inherited listener')
    with socket.socket(fileno=listener_fd) as listener:
        if (listener.family!=socket.AF_UNIX or listener.type!=socket.SOCK_SEQPACKET
            or listener.getsockname()!=profile.endpoint.socket_path):
            raise ValueError('custody listener differs from protected endpoint')
        listener.settimeout(37*60)
        channel,_=listener.accept()
        with channel:
            serve_profiled_release(channel,profile_path=profile_path,profile_digest=profile_digest,
                read_plaintext=read_plaintext)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    prepare_parser=commands.add_parser('prepare')
    prepare_parser.add_argument('--configuration',type=Path,required=True)
    serve_parser=commands.add_parser('serve')
    serve_parser.add_argument('--profile',type=Path,required=True)
    serve_parser.add_argument('--profile-digest',required=True)
    serve_parser.add_argument('--listener-fd',type=int,required=True)
    args=parser.parse_args(argv)
    if args.command=='prepare':prepare(json.loads(args.configuration.read_bytes()))
    else:serve(args.profile,args.profile_digest,args.listener_fd)
    return 0


if __name__=='__main__':
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    raise SystemExit(main())
