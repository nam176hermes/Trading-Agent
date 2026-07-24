# Phase 3B Pre-change Checkpoint

Captured 2026-07-11T19:46:34-04:00 (America/Toronto), before Phase 3B source
analysis implementation, schema revision, or backfill.

## Repository and baseline tests

| Check | Result |
|---|---|
| Repository | `/home/thenam176/projects/trading-agent-migration` |
| Branch | `codex/phase-2-control-api` |
| Commit | `30c2c35775d034c95d05068ee8aae5f4638dd644` |
| Worktree | Only pre-existing untracked `docs/audits/`; preserved |
| Baseline suite | `72 passed, 1 skipped` in 78.56 seconds |

The two Phase 3B design/plan commits precede this checkpoint. No linked legacy
repository was edited, reset, cleaned, or staged.

## PostgreSQL checkpoint

The protected owner/reader environment files were loaded without printing a
password or DSN.

| Check | Result |
|---|---|
| Host / port | `127.0.0.1 / 55432` |
| Database / owner role | `trading_agent / trading_owner` |
| Database size | 125,729,815 bytes |
| Alembic | `0002_quarantine_lineage` |
| Application tables | 15, plus `alembic_version` |
| Reader default transaction read-only | `on` |

Canonical counts were:

```text
assets                    17
market_reports            2,186
market_asset_snapshots    23,961
decisions                 16,517
signals                   344
capability_evidence       9
cost_summaries            1
cost_sessions             20
canonical total           43,055
migration_errors          222
audit_events              2,349
```

Quarantine contains 122 `WATCH`, 14 `WATCH FOR EXIT`, and 86 invalid reports.
The original migration runs remain completed and unchanged:

- `ee409404-d587-4e7c-add8-d5b580ff4a48`: 43,055 inserted, zero updated or
  skipped, 222 invalid.
- `f510f07a-7064-4777-9e82-a442b49dce47`: zero inserted or updated, 43,055
  skipped, 222 source-invalid observations without duplicated quarantine.

## Source and canonical hashes

All hashes were freshly recaptured through read-only file access or SQLite
`mode=ro`.

| Artifact | SHA-256 |
|---|---|
| Asset registry | `05f6fe43333a3b484aee0abb604b74a5c8e0cda251b526fe7c7b3f00bcad9c8b` |
| Report inventory | `4484acb8d1aa364f8c72368bdf273cf68f0d970503e6da8820f89b2057cf835d` |
| Latest valid report | `ad26c0bc07c77b1f37af6497b339cdb40d7324e0c8f88dd61b7344fc103cfebc` |
| Decisions JSONL | `0e97979237e4f0eaee8bc20235696a278c5b91e765acb5da591c24a358f981a3` |
| SQLite signals logical export | `693f2985c61972fccde1d71a7b452f2fb9bec588a1f4e3a41995b8217974e030` |
| SQLite orders/trades logical export | `ae1ee321d50f34c0504399dcaea07c799d2775be3e15cbce1433f68803973b09` |
| Scratchpad inventory | `0deb016be82bed327db5b197a480c35b15653ebb540071ed849f117680d5d732` |
| Combined Phase 3 inventory | `dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce` |
| Planner manifest | `06964c9ce162bf0fefa637c0a04d86eaea9b21deae0060ddec1555ba63f20892` |

The stable PostgreSQL canonical export was executed twice in one read-only
connection. Both hashes were
`b458c23c4ae861408070356e9e387a9fad89e1ae302e862e3544c685c0be3e7c`.

## Runtime safety checkpoint

| Invariant | Observed value |
|---|---|
| Requested / effective mode | `paper / paper` |
| `LIVE_EXECUTION_ENABLED` | `false` |
| `LIVE_TRADING_APPROVED` | `false` |
| Kill switch | `INACTIVE` |
| Orders / trades | `30 / 0` |
| `trading-agent.service` | active, main PID `4181928` |
| `trading-dashboard.service` | active, main PID `4183789` |
| Port 3002 | `0.0.0.0:3002`, serving PID `4183820` |
| Cloudflared | PID `3283180` |

Only the two named gate variables were read from the active process
environment. No other service environment or credential was printed. No
service, scheduler, port, tunnel, exchange, broker, order, or trade operation
was invoked.

## Backup and restore gate

A custom-format dump was created outside Git:

```text
path: /home/thenam176/.local/share/trading-agent-backups/phase3b-prechange-20260711T194536-0400.dump
mode: 0600
size: 25,633,970 bytes
sha256: 56541c875d2edccec2dd1f4fd28c5d888c46a9c73a9f7666403d8fc4c137b161
```

The initial attempt correctly showed that the least-privilege owner cannot
create databases. The protected `postgres-admin.env` role was then used only
to create/drop the temporary database; `trading_owner` performed the restore.
No privilege or role configuration was changed.

Restore database `trading_agent_phase3b_restore_20260711_194605` verified:

```text
application tables: 15
Alembic revision: 0002_quarantine_lineage
canonical rows: 43,055
quarantine rows: 222
temporary database removed: yes
```

**PHASE 3B PRE-CHANGE BACKUP/RESTORE, SOURCE, DATABASE, AND SAFETY GATES: PASS**
