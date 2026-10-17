"""Spend one admitted ISSUE_RUNTIME_PROFILE and publish the profile the worker reads.

This is the consumer half of the authority transaction. Until it existed, `consume` had no
production caller: an admission was committed, the bundle was staged, and nothing ever spent
it, so the one bounded action it authorised stayed available for as long as its deadline
held. The review was right to refuse to call that an integration.

The effect published here is `runtime.json` in the issuer store, which is exactly the path
`services.job_worker.p3_local_runtime.require_local_runtime` reads. Publication happens
inside the consumption's critical section, after the admission has been recorded as spent,
so a crash between the two leaves the admission unusable rather than the effect repeatable.

This grants no authority of its own. The profile it publishes carries only what the admitted
prerequisites already support, it starts no service, and it performs no production mutation.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.engine_contracts.serialization import canonical_json_bytes   # noqa: E402
from packages.local_authority import (                                     # noqa: E402
    AuthorityHeld, AuthorityStore, _publish, consume)
from packages.local_pre_p3 import LOCAL_PRE_P3_GATES, read_local_pre_p3    # noqa: E402

PROFILE_ROOT = Path('/etc/trading-agent/local-qualification')
ACTION = 'ISSUE_RUNTIME_PROFILE'
GATE = 'PRE_P3_READY'


def _publish_profile(store: AuthorityStore, record: dict, bundle: Path) -> Path:
    """Install the admitted descriptor as the profile the worker reads.

    The descriptor is taken from the admitted bundle, never rebuilt here: rebuilding it
    would mean publishing something the admission never covered. `consume` has already
    verified every bundle member against the committed manifest before this runs.
    """
    descriptor = json.loads((bundle / 'descriptor.json').read_bytes())
    if descriptor.get('schema_version') != 'p3-local-runtime-profile-v1':
        raise AuthorityHeld('admitted descriptor is not a LOCAL runtime profile')
    if descriptor['identity'] != record['identity'] or descriptor['source'] != record['source']:
        raise AuthorityHeld('admitted descriptor does not carry the admitted identity or source')
    if descriptor['expires_at'] != record['expires_at'] or descriptor['revoked'] is not False:
        raise AuthorityHeld('admitted descriptor validity differs from the admission')
    path = store.path / 'runtime.json'
    _publish(path, canonical_json_bytes(descriptor) + b'\n', 0o444)
    return path


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        print(json.dumps(dict(status='HELD', reason='publication runs as the store owner')))
        return 2
    if len(argv) != 2:
        print(json.dumps(dict(status='HELD',
            reason='usage: p3_local_publish_runtime.py ISSUER_IDENTITY')))
        return 2
    try:
        store = AuthorityStore(argv[1], root=PROFILE_ROOT)
        # Identity and source are re-derived from the protected prerequisites rather than
        # read out of the commit record: a consumer that took them from the record would be
        # checking the record against itself. A change between this read and the lock is not
        # a window -- it moves the generation, and the snapshot check inside `consume`
        # refuses an admission whose store has moved.
        gates = read_local_pre_p3(ROOT)
        if tuple(gates) != LOCAL_PRE_P3_GATES:
            raise AuthorityHeld('protected prerequisite set is partial')
        receipt = gates[GATE]
        published: list[Path] = []
        record = consume(store, action=ACTION, gate=GATE, gates=LOCAL_PRE_P3_GATES,
            identity=receipt.identity.model_dump(mode='json'),
            source=receipt.binding.source.model_dump(mode='json'),
            apply=lambda committed, bundle: published.append(
                _publish_profile(store, committed, bundle)))
    except AuthorityHeld as held:
        print(json.dumps(dict(status='HELD', reason=str(held))))
        return 1
    except Exception as error:                                   # noqa: BLE001
        print(json.dumps(dict(status='HELD', reason=f'{type(error).__name__}: {error}')))
        return 1
    print(json.dumps(dict(status='PUBLISHED', action=ACTION,
        generation=record['generation'], token=record['token'],
        profile_path=str(published[0]), expires_at=record['expires_at'],
        note='The admission is spent. A further profile requires a fresh admission.'),
        sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
