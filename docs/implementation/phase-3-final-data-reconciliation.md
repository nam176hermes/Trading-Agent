# Phase 3 Final Data Reconciliation

## Source to PostgreSQL counts

| Domain | Source | PostgreSQL |
|---|---:|---:|
| Reports discovered / valid / invalid | 2,272 / 2,186 / 86 | 2,186 reports / 86 quarantine |
| Report asset snapshots | 23,961 | 23,961 |
| Decisions seen / canonical / quarantine | 16,653 / 16,517 / 136 | 16,517 / 136 |
| Signals | 344 | 344 |
| Capabilities / verified | 9 / 0 | 9 / 0 |
| Cost summary / sessions | 1 / 20 | 1 / 20 |
| Assets | 17 | 17 |

`WATCH` is 122 and `WATCH FOR EXIT` is 14. Neither value occurs in canonical
decisions. Cost quality remains `UNKNOWN` or `ESTIMATED`; capability remains
`UNKNOWN`.

The deterministic canonical export excludes migration run IDs, ingestion and
creation timestamps, and other nondeterministic tracking fields. Two immediate
exports produced the same SHA-256:

`b458c23c4ae861408070356e9e387a9fad89e1ae302e862e3544c685c0be3e7c`

Deterministic five-row samples were checked for reports, decisions, signals,
quarantine, and normalization audit. Imported report, report-asset, decision,
signal, capability, cost-summary, and cost-session records had their available
schema lineage populated. The `assets` table has no direct source-lineage
columns; that is a Phase 3 acceptance blocker, not a count adjustment.

All nine legacy hashes, including combined inventory
`dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce`,
were unchanged after migration and final verification. Isolated integration
checks also proved report count, decision stat, logical SQLite signals, and the
two live signal outputs unchanged.
