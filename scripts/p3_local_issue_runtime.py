"""Issue the LOCAL runtime profile through one serialized authority admission.

Replaces the retired task-local preliminary issuer, which validated prerequisites and then
acted on them: a revocation landing in between was reported after the fact rather than
prevented, and the decision bound receipt digests, so an approval replaced by a different
but equally valid one for the same receipt went undetected.

Every step here happens under one admission from packages.local_authority. The admission
authorises exactly one bounded action. Publishing a runtime profile is one such action;
anything else obtains its own.

This issues no approval of its own and grants no authority beyond the profile the admitted
prerequisites already support. It starts no service and performs no production mutation.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.engine_contracts.serialization import canonical_json_bytes   # noqa: E402
from packages.local_authority import (                                     # noqa: E402
    AuthorityHeld, AuthorityStore, admit)
from packages.local_pre_p3 import LOCAL_PRE_P3_GATES, read_local_pre_p3    # noqa: E402
from packages.pre_p3_provenance import canonical_source_identity           # noqa: E402
from packages.runtime_release.staging_v2 import _validate_validity          # noqa: E402

REQUEST_PARENT = Path('/etc/trading-agent/local-p3-issuance')
ACTION = 'ISSUE_RUNTIME_PROFILE'
MAX_VALIDITY = timedelta(hours=2)


def _read_request(name: str) -> dict:
    """The only caller-supplied value names a protected request, never a path."""
    if not name or '/' in name or name in ('.', '..') or not name.endswith('.json'):
        raise AuthorityHeld('issuance request name is not a protected request filename')
    path = REQUEST_PARENT / name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
            raise AuthorityHeld('issuance request is not a protected root-owned file')
        raw = os.read(fd, 1 << 20)
    finally:
        os.close(fd)
    request = json.loads(raw)
    if canonical_json_bytes(request) != raw.rstrip(b'\n'):
        raise AuthorityHeld('issuance request is not canonical')
    if request.get('schema_version') != 'p3-local-runtime-issuance-request-v1':
        raise AuthorityHeld('issuance request schema is not accepted')
    return request


def _staged_material(request: dict) -> dict:
    """Read the Package6 staging material the profile must bind to.

    The profile is only meaningful against a staged application: validate_runtime_binding
    compares its roots, interpreter, authority digest and validity window against what the
    worker actually runs. Without a stage there is nothing to bind, and this fails closed
    rather than issuing a profile that no launcher could accept.
    """
    authority_path = Path(request['staging_authority_path'])
    if not authority_path.is_file():
        raise AuthorityHeld(f'staged authority material is absent: {authority_path}')
    raw = authority_path.read_bytes()
    material = json.loads(raw)
    validity = material.get('validity')
    _validate_validity(validity, now=datetime.now(UTC))
    application_root = Path(request['application_root'])
    application_python = Path(request['application_python'])
    for path in (application_root, application_python):
        if not path.exists():
            raise AuthorityHeld(f'staged application path is absent: {path}')
    return dict(static_authority_sha256=hashlib.sha256(raw).hexdigest(),
                authority_generated_at=validity['generated_at_utc'],
                authority_expires_at=validity['expires_at_utc'],
                application_root=str(application_root),
                application_python=str(application_python))


def _prepare(request: dict, snapshot):
    """Re-derive every admitted fact from the protected store, under the admission lock.

    `admit` calls this inside its critical section. Doing the semantic work before the lock,
    as this script used to, meant the prerequisites of one generation could be committed
    alongside the snapshot of another: nothing stopped an approval being replaced between
    the validation and the commitment.

    The identity returned here is the one the PRE_P3_READY receipt carries, not the one the
    request asked for. `admit` accepts the request only when the two are equal, which is the
    binding the earlier version claimed while in fact comparing the request with itself.
    """
    gates = read_local_pre_p3(ROOT)
    if tuple(gates) != LOCAL_PRE_P3_GATES:
        raise AuthorityHeld('protected prerequisite set is partial')
    receipt = gates['PRE_P3_READY']
    identity = receipt.identity.model_dump(mode='json')
    source = receipt.binding.source.model_dump(mode='json')
    if source != json.loads(canonical_json_bytes(canonical_source_identity(ROOT))):
        raise AuthorityHeld('admitted prerequisites do not describe the current source')
    # The staged material is read here too: its expiry bounds the profile, so reading it
    # outside the lock would bound the admission by a window that had already moved.
    material = _staged_material(request)
    issued = datetime.now(UTC).replace(microsecond=0)
    expires = min(issued + MAX_VALIDITY,
                  datetime.fromisoformat(material['authority_expires_at'].replace('Z', '+00:00')))
    if expires <= issued:
        raise AuthorityHeld('staged authority material has already expired')
    utc = lambda value: value.isoformat().replace('+00:00', 'Z')
    profile = dict(schema_version='p3-local-runtime-profile-v1',
        identity=identity, source=source,
        application_root=material['application_root'],
        application_python=material['application_python'],
        uid=request['worker_uid'],
        static_authority_sha256=material['static_authority_sha256'],
        package6_approval_sha256=request['package6_approval_sha256'],
        issued_at=utc(issued), expires_at=utc(expires), revoked=False)
    return dict(identity=identity, source=source, expires_at=utc(expires),
        bundle={'request.json': canonical_json_bytes(request) + b'\n',
                'prerequisites.json': canonical_json_bytes(
                    {gate: value.model_dump(mode='json') for gate, value in gates.items()}) + b'\n',
                'descriptor.json': canonical_json_bytes(profile) + b'\n'},
        snapshot_generation=snapshot.generation)


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        print(json.dumps(dict(status='HELD', reason='issuance runs as the store owner')))
        return 2
    if len(argv) != 2:
        print(json.dumps(dict(status='HELD', reason='usage: p3_local_issue_runtime.py REQUEST_NAME')))
        return 2
    try:
        request = _read_request(argv[1])
        source = json.loads(canonical_json_bytes(canonical_source_identity(ROOT)))
        if source != request['source']:
            raise AuthorityHeld('issuance request does not describe the current source')
        store = AuthorityStore(request['identity']['issuer_identity'])
        record = admit(store, action=ACTION, gate='PRE_P3_READY', gates=LOCAL_PRE_P3_GATES,
            proposal_identity=request['identity'], proposal_source=source,
            prepare=lambda snapshot: _prepare(request, snapshot))
    except AuthorityHeld as held:
        print(json.dumps(dict(status='HELD', reason=str(held))))
        return 1
    except Exception as error:                                   # noqa: BLE001
        print(json.dumps(dict(status='HELD', reason=f'{type(error).__name__}: {error}')))
        return 1
    print(json.dumps(dict(status='ADMITTED', action=ACTION, generation=record['generation'],
        token=record['token'], bundle_path=record['bundle_path'],
        expires_at=record['expires_at'],
        note='The profile is staged in the admitted bundle. Publication to the issuer store '
             'is a separate bounded action: p3_local_publish_runtime.py spends this '
             'admission exactly once.'),
        sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
