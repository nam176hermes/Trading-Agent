"""Real host GPG over synthetic bytes; no holdout or authority is created."""
import hashlib
from pathlib import Path
import subprocess

import pytest
from tests.p3.test_reference_input import reference_seed


def test_gpg_custody_roundtrip_and_integrity_rejections():
    from packages.alpha_lifecycle.local_custody import crypt_bundle
    binary=Path('/usr/bin/gpg')
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    version=subprocess.run([str(binary),'--version'],check=True,capture_output=True).stdout.splitlines()[0].decode()
    key=b'a'*64
    raw=b'{"synthetic":"custodian-only"}'
    kwargs=dict(binary=binary,binary_sha256=digest,version=version)
    encrypted=crypt_bundle(raw,key,**kwargs)
    assert raw not in encrypted
    assert crypt_bundle(encrypted,key,decrypt=True,**kwargs)==raw
    for value,secret in ((encrypted,b'b'*64),(encrypted[:-10],key)):
        with pytest.raises(ValueError):crypt_bundle(value,secret,decrypt=True,**kwargs)
    with pytest.raises(ValueError):crypt_bundle(raw,key,**dict(kwargs,binary_sha256='0'*64))
    with pytest.raises(ValueError):crypt_bundle(raw,key,**dict(kwargs,version='unapproved'))


def test_custody_producer_encrypts_exact_rows_and_reads_backup(reference_seed, tmp_path):
    import os
    from packages.alpha_lifecycle.local_custody import seal_custody_rows, decrypt_custody_bundle
    from packages.alpha_lifecycle.contracts.authority import CustodyRecord
    from packages.alpha_lifecycle.replica_store import _read
    from packages.data_catalog.artifact_store import LocalArtifactStore
    root, _, _, _, rows, buffer = reference_seed
    backup_root=tmp_path/'backup';backup_root.mkdir(mode=0o700)
    store=LocalArtifactStore(root);backup=LocalArtifactStore(backup_root)
    binary=Path('/usr/bin/gpg')
    config=dict(binary=binary,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        version=subprocess.run([str(binary),'--version'],capture_output=True,check=True).stdout.splitlines()[0].decode())
    result=seal_custody_rows(store=store,backup=backup,row_refs=(*rows,buffer),key=b'a'*64,
        custodian_uid=os.geteuid(),research_uid=os.geteuid()+1,custodian_identity='custodian',
        research_identity='research',access_policy_digest='a'*64,**config)
    custody=_read(store,result['custody_record_ref'],CustodyRecord)
    raw=decrypt_custody_bundle(custody,store=store,key=b'a'*64,**config)
    assert hashlib.sha256(raw).hexdigest()==custody.plaintext_bundle_digest
    assert backup.read_bytes(custody.ciphertext_ref)==store.read_bytes(custody.ciphertext_ref)
    assert result['qualification']=='PROPOSAL_ONLY'
    with pytest.raises(ValueError):
        decrypt_custody_bundle(custody.model_copy(update={'plaintext_bundle_digest':'0'*64}),
            store=store,key=b'a'*64,**config)


def test_custody_cli_refuses_wrong_uid_before_reading_private_inputs(tmp_path):
    import json, os
    from scripts.prepare_p3_local_custody import main
    configuration=tmp_path/'proposal.json'
    configuration.write_text(json.dumps({'custodian_uid':os.geteuid()+1}))
    with pytest.raises(ValueError,match='custodian UID'):
        main(['prepare','--configuration',str(configuration)])
