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

Only P0-12 may request transient completion mode. The committed matrix remains
`QUALIFICATION_PENDING`; it is not rewritten to claim its own qualification.
The public command requires exactly two sealed P0-10 evidence trees plus the
canonical final-review receipt:

```bash
python scripts/check_p0_ci_closure.py \
  --matrix docs/implementation/p0-ci-closure-matrix.json \
  --qualification-receipt runtime/state/p0-qualification/run-1/manifest.json \
  --qualification-receipt runtime/state/p0-qualification/run-2/manifest.json \
  --final-review-receipt runtime/state/p0-qualification/final-review.json \
  --require-complete
```

Both evidence trees must pass the P0-10 strict validator at the exact current
HEAD and source tree, have distinct run identities, equal semantic-result
digests, and a source PASS. The canonical read-only review receipt must bind
the exact HEAD/tree, both manifest byte hashes, both semantic digests, both run
identities, and verdict `APPROVED`. Success returns `P0_SOURCE_COMPLETE`, which
earns E11 only. E12 remains `PENDING` and operator-owned until separately
authorized fast-forward promotion and post-promotion proof.

The final-review receipt is canonical JSON with a trailing newline and exact
top-level fields `schema_version`, `verdict`, `head_sha`,
`source_tree_sha256`, `receipts`, and `review_receipt_sha256`. Its two ordered
receipt entries use exact fields `path`, `manifest_sha256`,
`semantic_result_sha256`, `run_id`, and `run_attempt`. The self-hash is SHA-256
of those canonical bytes with `review_receipt_sha256` set to the empty string.
The qualification directory is owner-held mode `0500`; the review leaf is
owner-held mode `0400`. Human review `VERDICT: PASS` is represented as
`verdict: "APPROVED"` only after the exact bindings are populated.

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

`PENDING` is not a deferred runtime PASS. E11 is promoted only in the transient
completion verdict above; its committed source row remains truthful. E12
cannot be earned by this source checker.
