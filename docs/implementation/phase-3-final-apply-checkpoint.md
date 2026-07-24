# Phase 3 Final Apply Checkpoint

Captured 2026-07-11T12:14:43-04:00 through 2026-07-11T12:19:09-04:00
(America/Toronto), before real-data apply.

## Runtime safety

| Invariant | Recaptured value |
|---|---|
| Requested mode | `paper` |
| Effective mode | `paper` |
| `LIVE_EXECUTION_ENABLED` | `false` |
| `LIVE_TRADING_APPROVED` | `false` |
| Canonical kill switch | `INACTIVE` |
| Orders / trades | `30 / 0` |
| Active agent | PID `4181928`, active, unchanged |
| Active legacy dashboard | PID `4183789`, active, unchanged |
| Dashboard listener | `0.0.0.0:3002`, unchanged |
| Cloudflared | PID `3283180`, unchanged |

Only the two named live gates were read from the active agent environment. No
credential, broker, exchange, order, mutation, scheduler, service restart, port
change, or tunnel change occurred.

## PostgreSQL pre-state

| Check | Result |
|---|---|
| Host / port | `127.0.0.1 / 55432` |
| Database | `trading_agent` |
| Alembic | `0002_quarantine_lineage (head)` |
| Application tables | 15, plus `alembic_version` |
| Domain rows | 0 |
| Migration / quarantine / audit rows | 0 |
| Listener | localhost only |

## Source recapture

| Artifact | SHA-256 |
|---|---|
| Canonical legacy asset registry | `05f6fe43333a3b484aee0abb604b74a5c8e0cda251b526fe7c7b3f00bcad9c8b` |
| Report source inventory | `4484acb8d1aa364f8c72368bdf273cf68f0d970503e6da8820f89b2057cf835d` |
| Latest valid report | `ad26c0bc07c77b1f37af6497b339cdb40d7324e0c8f88dd61b7344fc103cfebc` |
| Decisions JSONL | `0e97979237e4f0eaee8bc20235696a278c5b91e765acb5da591c24a358f981a3` |
| SQLite signals logical export | `693f2985c61972fccde1d71a7b452f2fb9bec588a1f4e3a41995b8217974e030` |
| SQLite orders/trades logical export | `ae1ee321d50f34c0504399dcaea07c799d2775be3e15cbce1433f68803973b09` |
| Scratchpad inventory | `0deb016be82bed327db5b197a480c35b15653ebb540071ed849f117680d5d732` |
| Deterministic fixture subset | `281a49e53146f8a9d09f4674f9caadcbe8c2543365ba204faa9fd0caae82195b` |
| Combined source inventory | `dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce` |

The combined inventory exactly matches the approved value. The planner
manifest is
`06964c9ce162bf0fefa637c0a04d86eaea9b21deae0060ddec1555ba63f20892`.
Fresh dry-run results were 43,055 canonical rows, 222 quarantine rows, 2,349
planned normalization audit rows, and zero updates. Decision reconciliation was
16,517 canonical plus 136 quarantine equals 16,653 source observations.
Decision file stat, report-file count, and logical SQLite signals were unchanged
by recapture and dry-run.

## Backup and restore gate

A final custom-format pre-apply dump was created outside Git at
`~/.local/share/trading-agent-backups/phase3-final-pre-apply-20260711T121909-0400.dump`.
It is 51,925 bytes, mode `0600`, with SHA-256
`8c96a98b7746bdaa606ab69c7a7c8ce1df7fcb8618cf52d394f8e1933a8fc2f6`.
No password appeared on the command line or in output.

The dump restored into temporary database
`trading_agent_restore_20260711_121909_0400`. The restored database contained
15 application tables, Alembic revision `0002_quarantine_lineage`, and zero
domain/migration rows. The temporary database was removed after the drill.

PRE-APPLY SAFETY, INVENTORY, EMPTY-TARGET, BACKUP, AND RESTORE GATES: PASS
