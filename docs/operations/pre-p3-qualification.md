# Pre-P3 qualification runbook

## Authority boundary

All commands remain source-only or use the approval-bound disposable
PostgreSQL 16 fixture. They do not accept a DSN, contact a provider or broker,
enable network trading, or alter live/production authority. Every receipt
requires network, broker, production, and live authority to remain `false`.

## Source candidate

Run from a clean canonical checkout after the source commit. Keep receipts in
a private external directory until review:

```bash
export PRE_P3_RECEIPT_DIR=/absolute/private/pre-p3-receipts
make qualify-p1-engine-lts-final \
  P1_LTS_FOUNDATION_RECEIPT=/absolute/private/p1-foundation.json \
  P1_LTS_NATIVE_RECEIPT=/absolute/private/p1-native.json \
  P1_LTS_OPERATOR_RECEIPT=/absolute/private/p1-operator.json \
  > "$PRE_P3_RECEIPT_DIR/p1-lts-native.json"
uv run python scripts/qualify_pre_p3.py p1-bridge \
  --p1-lts "$PRE_P3_RECEIPT_DIR/p1-lts-native.json" \
  --p1-h-output "$PRE_P3_RECEIPT_DIR/p1-h-complete-v1.json" \
  --p1-lts-output "$PRE_P3_RECEIPT_DIR/p1-lts-ready-v1.json"
make qualify-p2-source PRE_P3_RECEIPT_DIR="$PRE_P3_RECEIPT_DIR"
make qualify-p3-foundation PRE_P3_RECEIPT_DIR="$PRE_P3_RECEIPT_DIR"
```

`P1-H` remains held until all three exact external proofs exist. Source tests
cannot synthesize those proofs.

Each proof is canonical JSON with no trailing newline and exactly these keys:
`authority_limits`, `evidence_sha256s`, `execution_scope`, `schema`,
`source_commit`, `source_tree`, and `verdict`. `evidence_sha256s` must be a
non-empty sorted unique list of lowercase SHA-256 digests. All authority values
must be false, `execution_scope` must be `PAPER_LOCAL_ONLY`, and every proof
must bind the same clean source commit and tree. The expected schemas/verdicts
are, respectively, `trading-agent-p1-lts-foundation-proof/v1` / `PASS`,
`trading-agent-p1-lts-native-proof/v1` / `PASS`, and
`trading-agent-p1-lts-operator-acceptance/v1` / `ACCEPT`.

## Disposable PostgreSQL 16

Only after the existing protected GREEN fixture plan grants the exact
operation `p2-security-master-runtime-green-v1`:

```bash
make qualify-p2-runtime PRE_P3_RECEIPT_DIR="$PRE_P3_RECEIPT_DIR"
make qualify-p2-final PRE_P3_RECEIPT_DIR="$PRE_P3_RECEIPT_DIR"
```

Missing approval must fail/skip and produce no receipt. Never substitute an
existing, production, operator, or remotely hosted database.

## Final review

Copy the eight reviewed gate receipts into
`docs/implementation/pre-p3/receipts/`, regenerate
`docs/implementation/project-status.json`, and review the resulting diff.
Then run:

```bash
make check-project-status
make certify-pre-p3 PRE_P3_RECEIPT_DIR="$PRE_P3_RECEIPT_DIR"
```

`PRE_P3_READY` only permits P3 alpha development. It leaves `LIVE_ELIGIBLE`,
`LIVE_ENABLED`, network, broker, and production authority false.
