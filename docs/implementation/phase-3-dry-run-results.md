# Phase 3 Real-Data Dry-Run Results

Captured 2026-07-11 against
`/home/thenam176/.hermes/crypto-research`, without `--apply`.

## Result

| Metric | Result | Checkpoint expectation |
|---|---:|---:|
| Report files discovered | 2,272 | 2,272 |
| Valid reports | 2,186 | 2,186 |
| Invalid reports | 86 | 86 |
| Report asset rows | 23,961 | 23,961 |
| Decisions seen | 16,653 | 16,653 |
| Valid decisions | 16,517 | 16,653 |
| Invalid decisions | 136 | 0 |
| SQLite signals | 344 | 344 |
| Capabilities | 9 | 9 |
| Verified capabilities | 0 | 0 |
| Cost sessions | newest 20 | newest 20 |
| Would insert | 43,038 | blocked from approval |
| Would skip | 0 | 0 on empty target |
| Would update | 0 | 0 |
| Planned invalid/quarantine | 222 | 86 before strict enum policy |
| Duration | 2.584423 seconds | informational |

Planner inventory hash:
`06964c9ce162bf0fefa637c0a04d86eaea9b21deae0060ddec1555ba63f20892`.
This is the planner's manifest hash. The approved combined checkpoint hash was
recaptured separately and remained unchanged.

## Count difference

The 136 invalid decisions are explained exactly by unapproved legacy action
values:

```text
WATCH: 122
WATCH FOR EXIT: 14
```

Phase 2 treated unknown actions as `NO_SIGNAL`, so its adapter counted every
JSONL line as valid. The approved Phase 3 policy says unknown enums are invalid
and must not be silently coerced. No new alias was added and the expected count
was not rewritten. Resolving this requires an explicit normalization-policy
decision and tests.

## Cost evidence

Cost scope remains the newest twenty legacy scratchpad sessions. It is
`UNKNOWN` or `ESTIMATED`, never exact accounting. The current scope observed
zero `llm_call` events and 220 tool-result events at checkpoint time.

## No-write proof

- Counts across all fifteen PostgreSQL operational/migration tables were
  identical before and after dry-run.
- Decision file size/mtime was identical before and after.
- The number of `report_*.json` files was identical before and after.
- `records_updated` was zero.
- No migration run, error, audit event, checkpoint, or domain row was created.

Real-data apply was not invoked and remains blocked.
