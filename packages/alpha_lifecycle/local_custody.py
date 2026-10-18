"""Bounded GPG transport for private custodian bundles; grants no release authority."""
import hashlib
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import tempfile
from typing import Annotated, Literal

from pydantic import Field

from packages.alpha_lifecycle.sandbox_policy import MAX_VIEW_BYTES
from services.job_worker.engine_spawn import _memfd_create, _sealed_memfd
from packages.alpha_lifecycle.contracts.authority import CustodyRecord, LocalRunIdentity
from packages.alpha_lifecycle.contracts.base import StrictModel, SourceIdentity, Text, Sha256
from packages.alpha_lifecycle.contracts.data import DailyBar, BufferOpen
from packages.alpha_lifecycle.custody import build_attestation
from packages.alpha_lifecycle.data_view import validate_dates
from packages.alpha_lifecycle.replica_store import _read
from packages.alpha_lifecycle.research_custody import _seal
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.data_contracts import ArtifactRefV1


class LocalCustodyCipherProfile(StrictModel):
    """Protected issuer-installed storage/binary pins; never a disclosure grant."""
    schema_version: Literal['p3-local-custody-cipher-profile-v1']
    identity: LocalRunIdentity
    source: SourceIdentity
    custodian_uid: Annotated[int, Field(gt=0)]
    store_root: Text
    key_ref: ArtifactRefV1
    gpg_binary_sha256: Sha256
    gpg_version: Text


class GithubCustodyCipherProfile(StrictModel):
    """Hosted-lane storage/binary pins for this host's custodian; never a disclosure grant."""
    schema_version: Literal['p3-github-custody-cipher-profile-v1']
    host_id: Sha256
    source: SourceIdentity
    custodian_uid: Annotated[int, Field(gt=0)]
    store_root: Text
    key_ref: ArtifactRefV1
    gpg_binary_sha256: Sha256
    gpg_version: Text


def crypt_bundle(data: bytes, key: bytes, *, binary: Path, binary_sha256: str,
                 version: str, decrypt: bool = False) -> bytes:
    """Keep secrets off argv/disk; verify MDC and bound output before returning it."""
    maximum = MAX_VIEW_BYTES + 65536
    if (type(data) is not bytes or not 0 < len(data) <= maximum
        or type(key) is not bytes or re.fullmatch(rb'[0-9a-f]{64}', key) is None
        or not binary.is_absolute()):
        raise ValueError('custody cipher input or key is invalid')
    executable = secret = output = -1
    try:
        executable = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(executable)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('custody GPG executable ownership is unsafe')
        with os.fdopen(os.dup(executable), 'rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != binary_sha256:
            raise ValueError('custody GPG binary digest differs')
        command = f'/proc/self/fd/{executable}'
        probe = subprocess.run([command, '--version'], pass_fds=(executable,),
            capture_output=True, timeout=10, check=False, env={'PATH':'/usr/bin:/bin', 'LC_ALL':'C'})
        if probe.returncode or probe.stdout.splitlines()[0].decode() != version:
            raise ValueError('custody GPG version differs')
        secret = _sealed_memfd('p3-custody-key', key+b'\n', mode=0o600)
        os.lseek(secret, 0, os.SEEK_SET)
        output = _memfd_create('p3-custody-result')

        def bounded_child():
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        with tempfile.TemporaryDirectory(prefix='p3-gpg-') as home:
            arguments = [command, '--no-options', '--homedir', home, '--batch', '--yes',
                '--no-tty', '--no-autostart', '--no-symkey-cache', '--pinentry-mode', 'loopback',
                '--passphrase-fd', str(secret), '--output', '-']
            arguments += (['--decrypt'] if decrypt else ['--symmetric', '--cipher-algo', 'AES256',
                '--compress-algo', 'none', '--s2k-mode', '3', '--s2k-digest-algo', 'SHA512',
                '--s2k-count', '65011712'])  # Fixed work factor avoids agent calibration/autostart.
            completed = subprocess.run(arguments, input=data, stdout=output, stderr=subprocess.DEVNULL,
                pass_fds=(executable, secret), env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':home},
                timeout=60, check=False, preexec_fn=bounded_child)
            size = os.fstat(output).st_size
            if completed.returncode or not 0 < size <= (MAX_VIEW_BYTES if decrypt else maximum):
                raise ValueError('custody cipher integrity or output bound failed')
            result = os.pread(output, size, 0)
            if len(result) != size:
                raise ValueError('custody cipher output was incomplete')
            return result
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('custody cipher process failed') from error
    finally:
        for descriptor in (output, secret, executable):
            if descriptor >= 0:
                os.close(descriptor)


def seal_custody_rows(*, store, backup, row_refs, key, custodian_uid, research_uid,
                     custodian_identity, research_identity, access_policy_digest, **cipher):
    """Encrypt a complete sealed HOLDOUT/OPEN_ONLY bundle under the actual custodian UID."""
    if (os.geteuid()!=custodian_uid or custodian_uid==research_uid or len(row_refs)!=366
        or store._root==backup._root or store._root in backup._root.parents
        or backup._root in store._root.parents):
        raise ValueError('custody producer UID, row inventory or backup root differs')
    rows=tuple(_read(store,ref,DailyBar) for ref in row_refs[:-1])
    buffer=_read(store,row_refs[-1],BufferOpen)
    validate_dates('HOLDOUT',tuple(row.date for row in rows))
    validate_dates('BUFFER',(buffer.date,))
    raw=canonical_json_bytes({ref.locator:store.read_bytes(ref).decode() for ref in row_refs})
    ciphertext=crypt_bundle(raw,key,**cipher)
    if crypt_bundle(ciphertext,key,decrypt=True,**cipher)!=raw:
        raise ValueError('custody encryption readback differs')
    ciphertext_ref=store.put_bytes(ciphertext,media_type='application/octet-stream')
    if (backup.put_bytes(ciphertext,media_type='application/octet-stream')!=ciphertext_ref
        or backup.read_bytes(ciphertext_ref)!=ciphertext):
        raise ValueError('custody backup readback differs')
    attestation=build_attestation(ciphertext_ref=ciphertext_ref,
        plaintext_bundle_digest=hashlib.sha256(raw).hexdigest(),access_policy_digest=access_policy_digest,
        custodian_identity=custodian_identity,research_identity=research_identity,
        custodian_uid=custodian_uid,research_uid=research_uid,row_refs=row_refs)
    attestation_ref=store.put_bytes(canonical_json_bytes(attestation),media_type='application/json')
    custody=_seal(dict(schema_version='p3-custody-record-v1',holdout_commitment=attestation.holdout_commitment,
        ciphertext_ref=ciphertext_ref,plaintext_bundle_digest=attestation.plaintext_bundle_digest,
        custodian_identity=custodian_identity,research_identity=research_identity,
        custodian_attestation_ref=attestation_ref,access_policy_digest=access_policy_digest,
        classification='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND'),CustodyRecord)
    custody_ref=store.put_bytes(canonical_json_bytes(custody),media_type='application/json')
    return dict(custody_record_ref=custody_ref,custodian_attestation_ref=attestation_ref,
        ciphertext_ref=ciphertext_ref,qualification='PROPOSAL_ONLY')


def decrypt_custody_bundle(custody, *, store, key, **cipher):
    custody=CustodyRecord.model_validate(custody)
    raw=crypt_bundle(store.read_bytes(custody.ciphertext_ref),key,decrypt=True,**cipher)
    if hashlib.sha256(raw).hexdigest()!=custody.plaintext_bundle_digest:
        raise ValueError('custody plaintext bundle digest differs')
    return raw
