"""Private output retention cannot grant SQL job authority."""
import hashlib
import os
from pathlib import Path

import pytest

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes


def custody(tmp_path):
    from services.job_worker.p3_output import P3OutputCustody
    parent=tmp_path/'runs'
    parent.mkdir(mode=0o700)
    output=parent/('a'*64)
    output.mkdir(mode=0o700)
    retained=tmp_path/'retained'
    retained.mkdir(mode=0o700)
    store=LocalArtifactStore(retained)
    parent_fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    output_fd=os.open(output,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        handle=P3OutputCustody(parent_fd,output_fd,output.name,store)
    finally:
        os.close(parent_fd)
        os.close(output_fd)
    return handle,output,store


def test_verified_private_cas_is_retained_before_cleanup(tmp_path):
    handle,output,store=custody(tmp_path)
    (output/'artifacts').mkdir(mode=0o700)
    raw=canonical_json_bytes({'synthetic':True})
    artifact=output/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')
    artifact.write_bytes(raw)
    artifact.chmod(0o600)
    inventory_ref=handle.retain_and_cleanup()
    import json
    inventory=json.loads(store.read_bytes(inventory_ref))
    from packages.data_contracts import ArtifactRefV1
    assert store.read_bytes(ArtifactRefV1.model_validate(inventory[0]['artifact_ref'])) == raw
    assert not output.exists()
    handle.abandon()


@pytest.mark.parametrize('fault',['digest','symlink','fifo','oversize','replaced','permissions'])
def test_unsafe_outputs_are_preserved_and_never_imported(tmp_path,monkeypatch,fault):
    handle,output,store=custody(tmp_path)
    (output/'artifacts').mkdir(mode=0o700)
    raw=b'{"synthetic":true}'
    artifact=output/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')
    if fault == 'symlink':
        artifact.symlink_to(tmp_path/'unrelated')
    elif fault == 'fifo':
        os.mkfifo(artifact,mode=0o600)
    else:
        artifact.write_bytes(raw if fault != 'digest' else b'{}')
        artifact.chmod(0o600)
    if fault == 'oversize':
        monkeypatch.setattr('services.job_worker.p3_output.MAX_ATTEMPT_OUTPUT_BYTES',1)
    if fault == 'permissions':
        output.chmod(0o777)
    if fault == 'replaced':
        output.rename(output.with_name('preserved'))
        output.mkdir(mode=0o700)
        (output/'unrelated').write_bytes(b'preserve')
    before=set(os.listdir('/proc/self/fd'))
    try:
        with pytest.raises((OSError,ValueError)):
            handle.retain_and_cleanup()
    finally:
        handle.abandon()
    assert set(os.listdir('/proc/self/fd')) < before
    assert output.exists()
    assert not list(store._root.iterdir())


@pytest.mark.parametrize('nonempty',[False,True])
def test_abandon_only_removes_empty_owned_directory(tmp_path,nonempty):
    handle,output,_=custody(tmp_path)
    if nonempty:
        (output/'preserve').write_bytes(b'failed run evidence')
    handle.abandon()
    assert output.exists() is nonempty


def test_retention_preserves_private_files_until_validation(tmp_path):
    handle,output,store=custody(tmp_path)
    (output/'artifacts').mkdir(mode=0o700)
    raw=b'{"synthetic":true}'
    path=output/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')
    path.write_bytes(raw)
    path.chmod(0o600)
    inventory_ref=handle.retain()
    assert output.exists() and path.read_bytes() == raw
    assert store.read_bytes(inventory_ref)
    handle.abandon()
    assert output.exists()


def test_failed_second_descriptor_duplication_does_not_leak(tmp_path,monkeypatch):
    before=set(os.listdir('/proc/self/fd'))
    duplicate=os.dup
    opened=[]
    def fail_second(fd):
        if opened:
            raise OSError('synthetic descriptor exhaustion')
        result=duplicate(fd)
        opened.append(result)
        return result
    monkeypatch.setattr(os,'dup',fail_second)
    try:
        with pytest.raises(OSError,match='descriptor exhaustion'):
            custody(tmp_path)
        assert set(os.listdir('/proc/self/fd')) == before
    finally:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass


def test_partial_retention_failure_preserves_complete_private_evidence(tmp_path,monkeypatch):
    handle,output,store=custody(tmp_path)
    (output/'artifacts').mkdir(mode=0o700)
    original={}
    for number in range(2):
        raw=canonical_json_bytes({'synthetic':number})
        name=hashlib.sha256(raw).hexdigest()+'.blob'
        path=output/'artifacts'/name
        path.write_bytes(raw)
        path.chmod(0o600)
        original[name]=raw
    publish=store.put_bytes
    count=0
    def unavailable(raw,*,media_type):
        nonlocal count
        count+=1
        if count == 2:
            raise OSError('synthetic retained store outage')
        return publish(raw,media_type=media_type)
    monkeypatch.setattr(store,'put_bytes',unavailable)
    with pytest.raises(OSError,match='store outage'):
        handle.retain()
    handle.abandon()
    assert {p.name:p.read_bytes() for p in (output/'artifacts').iterdir()} == original


@pytest.mark.parametrize('lost',['inventory','artifact'])
def test_cleanup_requires_retained_readback_after_commit(tmp_path,lost):
    handle,output,store=custody(tmp_path)
    (output/'artifacts').mkdir(mode=0o700)
    raw=b'{"synthetic":true}'
    name=hashlib.sha256(raw).hexdigest()+'.blob'
    private=output/'artifacts'/name
    private.write_bytes(raw)
    private.chmod(0o600)
    inventory=handle.retain()
    (store._root/(inventory.locator if lost == 'inventory' else name)).unlink()
    try:
        with pytest.raises((ValueError,OSError)):
            handle.cleanup()
        assert private.read_bytes() == raw
    finally:
        handle.abandon()
