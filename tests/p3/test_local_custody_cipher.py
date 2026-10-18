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


def test_github_custody_producer_is_identity_free(tmp_path, monkeypatch):
    import os
    from scripts import prepare_p3_local_custody as module
    monkeypatch.setattr(module, '_committed_source', lambda: {'commit_sha': 'a'*40})
    with pytest.raises(ValueError, match='identity-free'):
        module.prepare({'custodian_uid': os.geteuid(), 'lane': 'GITHUB', 'identity': {'issuer_kind': 'LOCAL'}})


@pytest.mark.parametrize('fault', [None, 'host', 'source', 'uid', 'revoked', 'other_run'])
def test_github_custody_reader_binds_attested_run_host_and_source(monkeypatch, fault):
    import os
    from types import SimpleNamespace
    from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.engine_contracts.serialization import canonical_json_bytes
    from scripts import prepare_p3_local_custody as module
    from tests.p3.github_lane import GithubLane, HOST
    source = SourceIdentity(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='v1',
        closure_policy_sha256='c'*64, closure_sha256='d'*64)
    lane = GithubLane(monkeypatch, source=source, run_id=41, attempt=2, operation='p3-holdout-primary-v1')
    monkeypatch.setattr(module, '_host_identity', lambda: HOST)
    key = dict(content_sha256='e'*64, locator='e'*64+'.blob', size_bytes=64, media_type='application/octet-stream')
    cipher = dict(schema_version='p3-github-custody-cipher-profile-v1', host_id='0'*64 if fault == 'host' else HOST[0],
        source=dict(source.model_dump(mode='json'), commit_sha='9'*40) if fault == 'source' else source.model_dump(mode='json'),
        custodian_uid=os.geteuid()+(1 if fault == 'uid' else 0), store_root='/srv/custody', key_ref=key,
        gpg_binary_sha256='f'*64, gpg_version='gpg (GnuPG) 2.4.4')
    lane.documents[module.GITHUB_CIPHER_PATH] = cipher
    if fault == 'revoked':
        lane.drift('revoked', 'p3-holdout-primary-v1')
    run = 42 if fault == 'other_run' else 41
    profile = module.GithubCustodianProfile.model_construct(
        identity=GithubRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='GITHUB',
            issuer_identity='p3-authority', repository='nam176hermes/Trading-Agent', workflow_ref='p3-authority.yml',
            run_id=run, attempt=2))), request=SimpleNamespace(source=source))
    decrypted = []
    monkeypatch.setattr(module, 'decrypt_custody_bundle', lambda *a, **k: decrypted.append(k) or b'plaintext')
    monkeypatch.setattr(module, 'LocalArtifactStore', lambda root: SimpleNamespace(read_bytes=lambda ref: b'k'*64))
    reader = module.plaintext_reader(profile)
    if fault:
        with pytest.raises(ValueError):
            reader(object())
        assert decrypted == []
    else:
        assert reader(object()) == b'plaintext' and len(decrypted) == 1
