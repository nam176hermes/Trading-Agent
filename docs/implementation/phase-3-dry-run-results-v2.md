# Phase 3 Real-Data Dry-Run Results V2

Captured after fixture transaction and integration-isolation tests, without
real-data `--apply`.

## Canonical reconciliation

| Domain | Count |
|---|---:|
| Reports discovered | 2,272 |
| Valid reports | 2,186 |
| Invalid report quarantine | 86 |
| Report asset snapshots | 23,961 |
| Decision observations seen | 16,653 |
| Canonical decisions | 16,517 |
| Decision quarantine | 136 |
| `WATCH` | 122 |
| `WATCH FOR EXIT` | 14 |
| SQLite signals | 344 |
| Capabilities / verified | 9 / 0 |
| Cost sessions | newest 20, UNKNOWN or ESTIMATED |

Invariant: `16,517 + 136 = 16,653`.

## Planned rows

Canonical domain rows total 43,055:

```text
assets 17
market_reports 2,186
market_asset_snapshots 23,961
decisions 16,517
signals 344
capability_evidence 9
cost_summaries 1
cost_sessions 20
```

Migration tracking is reported separately:

```text
migration_runs 1
migration_source_files 2,295
migration_source_chunks 2,328
quarantine rows 222 (86 reports + 136 decisions)
planned normalization audit rows 2,349
would skip 0
would update 0
```

Planner inventory hash remained
`06964c9ce162bf0fefa637c0a04d86eaea9b21deae0060ddec1555ba63f20892`.
Approved combined inventory remained
`dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce`.
Duration was 3.352798 seconds.

## No-write proof

All fifteen staging PostgreSQL table counts were identical before and after.
Both live signal file hashes/stats, decision JSONL stat, report count, and
logical SQLite sources were unchanged. No migration run, domain row, error,
audit event, or checkpoint was written to `trading_agent`.
