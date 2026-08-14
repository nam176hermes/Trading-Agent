# P0 executable CI closure

`p0-ci-closure-matrix.json` is the canonical source contract for P0 invariants
and end-state requirements. `make check-p0-ci-closure` validates every bound
source path, exact pytest node, Make target, workflow, and evidence reference.

The committed state is `QUALIFICATION_PENDING`. It validates source bindings;
it does not manufacture a runtime receipt or qualification verdict.

```
QUALIFICATION_PENDING != P0_SOURCE_COMPLETE
P0_SOURCE_COMPLETE != P0_HOST_QUALIFIED
P0_HOST_QUALIFIED != PRODUCTION ACTIVATED
PRODUCTION ACTIVATED != LIVE TRADING ENABLED
```

Only P0-12 may request completion mode with an exact-head sealed P0-10 final
evidence receipt. Completion mode reuses the published-evidence validator and
rejects stale, partial, mutable, or noncanonical evidence.

The thirteen end-state bindings are one-for-one with the plan's exit
conditions. The checker rejects a requirement ID whose executable binding is
substituted for another condition:

| ID | Exact end-state condition | P0-11 status |
| --- | --- | --- |
| P0-E01 | Candidate ancestry and machine-readable baseline remain valid | PASS |
| P0-E02 | Sealed Foundation date is the sole date authority; CLI/environment overrides fail | PASS |
| P0-E03 | The 27 sealed-UV, 3 UID/GID, and 2 fakeroot portable defects have exact-node closure proofs | PASS |
| P0-E04 | Zero portable source defects remain; active topology is exactly 24 native plus 6 external nodes | PASS |
| P0-E05 | Native capability receipts are per-code, fail closed, and cannot prove another lane | PASS |
| P0-E06 | External authority receipts are per-code, fail closed, and cannot synthesize authority | PASS |
| P0-E07 | `ci` reaches portable authority only and cannot reach host authority | PASS |
| P0-E08 | Foundation is read-only/portable while host authority is manual, protected, and dispatch-only | PASS |
| P0-E09 | The final artifact manifest, hidden paths, checksums, semantic digest, and custody validate | PASS |
| P0-E10 | The executable closure matrix is canonical, collected, reachable, and fail closed | PASS |
| P0-E11 | Two green hosted runs at one SHA have equal semantic digests and final adversarial review PASS | PENDING |
| P0-E12 | Explicitly authorized fast-forward promotion and post-promotion `main` CI proof exist | PENDING |
| P0-E13 | Production and live authority remain unavailable and no production/live mutation occurred | PASS |

`PENDING` is not a deferred runtime PASS. E11 can change to `PASS` only in
explicit completion mode with the exact sealed final receipt; E12 remains
operator-owned and cannot be earned by this source checker.
