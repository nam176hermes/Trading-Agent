# Job Plane Recovery Gate Residual Risks v2

**Evidence date:** 2026-07-16

| Priority | Severity | Residual risk | Required closure |
|---:|---|---|---|
| 1 | P0 | A prose approval could be mistaken for the exact dual-reviewed recovery transcript. | Supply the protected 50-field Section 4 record, authenticated change artifact, identities, hashes, destinations, and live approval window. Never bypass `APPROVAL_RECORD`. |
| 2 | P1 | Runtime head, integrity, counts, and ACLs remain unknown while the interrupted cluster is offline. | Complete the single approved preservation/recovery start and stop on any identity, WAL, head, count, or catalog drift. |
| 3 | P1 | No fresh pre-migration backup/restore evidence exists. | After accepted recovery, create mode-0600 custom dump, hash/catalog it, restore into the approved isolated target, and prove complete parity. |
| 4 | P1 | `0005`/`0006` have no runtime execution transcript. | Independently approve the exact migration runbook, hashes, backup identity, roles, and rollback/forward-repair boundary. |
| 5 | P1 | `0006` does not yet exhaustively attest inherited policy/ACL/trigger/function authority. | Add RED corruption/drift tests, exhaustive catalog/body checks, disposable GREEN, and independent re-review before commit. |
| 6 | P1 | `0006` historical validation permits malformed first events and broken transition chains. | Reject noncanonical first events and every chain break in pre/postflight; test corrupted contiguous histories. |
| 7 | P1 | Current v2 builder cannot construct a hermetic Python 3.11 runtime. | Review and pin a complete runtime archive and wheel-only build closure, then prove final paths/sys.path/metadata cannot escape. |
| 8 | P1 | Release artifact selection is the whole three-component repository rather than minimal Job API/worker/backend command authority. | Introduce deterministic allowlisted source/artifact proof while retaining full commit provenance. |
| 9 | P1 | No real v2 builder, relocation, reproducibility, tamper/rebuild, or activation proof exists. | Add successful network-denied two-build equality, post-move import/start, tamper fail, rebuild pass, and reviewed runtime activation/attestation. |
| 10 | P2 | NPM retains one low and three moderate advisories. | Triage separately; do not mix an unrelated audit-fix upgrade into this minimal dependency repair. |
| 11 | P2 | Systemd manager scope, distinct identities, protected env/evidence files, write roots, and home/legacy masking are unresolved. | Resolve in a separate exact-path unit/provisioning review after a materialized release exists. |

No residual-risk item authorizes a Job service, timer, job, SNAPSHOT, provider,
broker, exchange, dashboard deployment, or live-trading action.
