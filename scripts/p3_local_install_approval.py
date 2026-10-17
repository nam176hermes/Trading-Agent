"""Install protected LOCAL gate approvals through the authority protocol.

This is the writer B1 said was missing. The historical helper installed approvals with a bare
`os.replace` into the protected store: atomic per file, but outside the store's lock and
generation, so two installers could interleave, a reader that snapshotted at one generation
could be overtaken without noticing, and the attestation consumers check was left stale by
construction. `AuthorityStore.replace_approvals` is the participating writer; nothing in this
repository called it, which is the gap. This calls it.

What this is not. It does not decide whether a gate is earned. Reviewing a candidate receipt
against its prerequisites, its source closure and its policy digest is a separate act that
produces the approval bytes; this installs bytes that act has already produced, and it
installs them the only way the protocol admits. Keeping the two apart is deliberate: an
installer that also approved would be checking its own work, and the review is the part that
needs to be done by something other than the thing applying the result.

It grants no authority of its own, starts no service, performs no production mutation, and
contacts nothing. The store owner is root and is trusted; this does not defend against a
malicious owner, it keeps a cooperating writer from interleaving with a consumer.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.authority import (   # noqa: E402
    LocalQualificationApproval)
from packages.local_authority import (                       # noqa: E402
    AuthorityHeld, AuthorityStore, require_no_bypassing_writer)
from packages.local_pre_p3 import LOCAL_PRE_P3_GATES         # noqa: E402

STORE_ROOT = Path('/etc/trading-agent/local-qualification')


def _read_approvals(source: Path, issuer: str) -> dict[str, bytes]:
    """Every `<GATE>.json` in the directory, parsed before any of them is installed.

    Parsing first matters because the install is one transaction: a file that turns out to be
    malformed after the generation has moved would leave the store advanced for a write that
    could not complete.
    """
    if not source.is_dir():
        raise AuthorityHeld(f'{source} is not a directory of approvals')
    payloads: dict[str, bytes] = {}
    now = datetime.now(UTC)
    for path in sorted(source.glob('*.json')):
        gate = path.name[: -len('.json')]
        if gate not in LOCAL_PRE_P3_GATES:
            raise AuthorityHeld(f'{gate} is not a LOCAL pre-P3 gate')
        raw = path.read_bytes()
        approval = LocalQualificationApproval.model_validate_json(raw)
        if approval.issuer_identity != issuer:
            raise AuthorityHeld(f'{gate} approval belongs to issuer {approval.issuer_identity}')
        # An approval that is already expired is refused here rather than installed and
        # refused later by every snapshot: installing it would move the generation and leave
        # the store holding something no admission can ever use.
        if approval.expires_at <= now:
            raise AuthorityHeld(f'{gate} approval expired at {approval.expires_at.isoformat()}')
        payloads[gate] = raw
    if not payloads:
        raise AuthorityHeld(f'{source} contains no gate approvals')
    return payloads


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(json.dumps(dict(status='HELD',
            reason='usage: p3_local_install_approval.py ISSUER_IDENTITY APPROVAL_DIR')))
        return 2
    if os.geteuid() != 0:
        print(json.dumps(dict(status='HELD', reason='installation runs as the store owner')))
        return 2
    try:
        store = AuthorityStore(argv[1], root=STORE_ROOT)
        if not store.path.is_dir():
            raise AuthorityHeld(f'{store.path} is not a protected issuer store')
        payloads = _read_approvals(Path(argv[2]), store.issuer)
        generation = store.replace_approvals(payloads, LOCAL_PRE_P3_GATES)
        # Read the store back through the consumer's own check rather than reporting success
        # from the writer's point of view. A writer that says it published and a consumer that
        # refuses the result is the failure this whole protocol exists to prevent, so the
        # installer is not allowed to be the only witness that it worked.
        attestation = require_no_bypassing_writer(store, LOCAL_PRE_P3_GATES)
    except AuthorityHeld as held:
        print(json.dumps(dict(status='HELD', reason=str(held))))
        return 1
    except Exception as error:                                   # noqa: BLE001
        print(json.dumps(dict(status='HELD', reason=f'{type(error).__name__}: {error}')))
        return 1
    print(json.dumps(dict(status='INSTALLED', issuer=store.issuer,
        gates=sorted(payloads), generation=generation,
        store_digest=attestation['store_digest'],
        note='Approvals are installed under the protocol. This grants no authority beyond '
             'what the approved receipts already support.'), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
