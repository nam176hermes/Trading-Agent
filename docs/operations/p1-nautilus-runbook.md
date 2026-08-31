# P1 Nautilus qualification runbook

## Safety boundary

These commands qualify the sealed Nautilus Trader 1.231 P1 product closure only.
They do not authorize network access, broker or exchange clients, live trading,
production activation, leverage, shorting, deployment, service changes, or
database mutation. Legacy Phase4 profiles remain on schema 6 / 1.227.

Run from the canonical repository root or an external clean worktree. Use a
Linux-native private temporary directory for host/native qualification.

## Source checks

```bash
make check-p1-nautilus-contracts
make check-p1-nautilus-boundaries
make check-p1-nautilus-lineage
make check-p1-nautilus-pin-inventory
make test-p1-nautilus-source
```

These targets need no native runtime. Any nonzero exit is `FAIL`. The lineage
report must say P1 1.231, legacy 1.227 unchanged, and all authority flags false.

## Native authority

### Materialize and qualify the schema-8 product runtime

This lane derives a task-owned schema-8 product runtime from the already sealed
G1 artifacts; it does not rebuild or modify G1. Supply all exact authorities:

```bash
export P1_NAUTILUS_BASE_RUNTIME=/absolute/path/to/sealed/base-runtime
export P1_NAUTILUS_ARTIFACT_DIRECTORY=/absolute/path/to/NT1231-U04-G1-artifacts
export P1_NAUTILUS_RUNTIME_DESTINATION=/absolute/task-owned/new-destination
export P1_NAUTILUS_SANDBOX=/usr/bin/bwrap
export P1_NAUTILUS_CARGO=/absolute/path/to/accepted/cargo
export P1_NAUTILUS_LLVM_TOOLCHAIN=/absolute/path/to/accepted/llvm
export P1_NAUTILUS_SOURCE_COMMIT=$(git rev-parse HEAD)
make build-p1-nautilus-runtime
```

The destination must be absent and task-owned. To revalidate existing sealed
artifacts without materializing again:

```bash
export P1_NAUTILUS_QUALIFICATION_RECEIPT=/absolute/task-owned/qualification.json
make qualify-p1-nautilus-runtime
```

All four qualification inputs (`BASE_RUNTIME`, `ARTIFACT_DIRECTORY`,
`SANDBOX`, `QUALIFICATION_RECEIPT`) absent means `DEFERRED`; a partial or
supplied-invalid set means `FAIL`.

### Execute product tests

Set both values from the exact policy-owned G1 cache:

```bash
export P1_NAUTILUS_PYTHON=/absolute/path/to/sealed/files/usr/bin/python3.12
export P1_NAUTILUS_CLOSURE_MANIFEST=/absolute/path/to/sealed/closure-manifest.json
export PYTHONHASHSEED=0
```

Then run:

```bash
make test-p1-nautilus-native
make qualify-p1-nautilus
make test-p1-nautilus-e2e
```

Both variables absent means `DEFERRED`; one missing or supplied-invalid means
`FAIL`. `PASS` is valid only when evidence binds the exact clean source
commit/tree, `NT1231-U04-G1`, schema-8 closure, and the promoted P1 baseline.

## Troubleshooting and recovery

- Source identity or lineage failure: stop, restore a clean accepted checkout,
  and rerun source checks. Do not update a golden digest to hide drift.
- Closure or interpreter failure: discard the task-owned cache and recover the
  exact sealed G1 from its immutable receipts. Do not rebuild G1 in this lane.
- Semantic/accounting/custody mismatch: treat as a blocker; retain the receipt
  and diagnose before another qualification attempt.
- Missing native authority: record `DEFERRED`; never fabricate paths or CI IDs.

Temporary qualification directories are private and self-cleaning. Remove only
task-owned caches after confirming no active process uses them. Rollback means
returning the P1 product decision to `HOLD_P1`; it never mutates legacy 1.227
policies or activates any runtime.
