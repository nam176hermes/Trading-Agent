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

The thirteen end-state bindings are deliberately ordered: E01--E10 cover the
source closure (inventory, exact-node execution, portable receipt semantics,
and sealed evidence custody); E11 is the two-same-SHA hosted-run condition and
E12 is authorized fast-forward promotion, both pending external evidence. E13
is the source-safe no-production/no-live-mutation invariant and is PASS.
