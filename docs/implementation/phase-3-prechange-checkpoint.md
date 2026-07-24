# Phase 3 Pre-change Checkpoint

Captured: 2026-07-11T10:53:11-04:00 (America/Toronto).

This is a read-only checkpoint captured before PostgreSQL provisioning, schema
changes, dependency changes, or importer implementation. No broker, exchange,
order, credential, scheduler, public deployment, or mutation endpoint was
called.

## Repository identity

| Surface | Path | Branch | Commit | Worktree |
|---|---|---|---|---|
| Migration workspace | `/home/thenam176/projects/trading-agent-migration` | `codex/phase-2-control-api` | `82807eeb3270f874b9fc4dc273d1f35bbd8a566e` | only pre-existing untracked `docs/audits/` before this checkpoint |
| Candidate dashboard | `/home/thenam176/projects/trading-dashboard` | `codex/phase-2-control-api` | `4e846e6bee633de52039042215b772c518adbb34` | clean |
| Legacy backend/data | `/home/thenam176/.hermes/crypto-research` | `master` | `c976307` | 22 tracked and 1,820 untracked entries; preserved |
| Active legacy dashboard | `/home/thenam176/.hermes/trading-agent` in Git root `/home/thenam176/.hermes` | `main` | `a130303` | 168 tracked and 409 untracked repo-wide entries; preserved |

No reset, clean, checkout-overwrite, history rewrite, or legacy worktree edit is
permitted. Existing `docs/audits/` artifacts are not staged by Phase 3.

## Runtime safety baseline

| Invariant | Observed value |
|---|---|
| `trading-agent.service` | active, PID 4181928 |
| `trading-dashboard.service` | active, PID 4183789 |
| Active dashboard listener | `0.0.0.0:3002`; unchanged |
| Requested mode | `paper` |
| Effective mode | `paper` by the current two-gate safety policy |
| `LIVE_EXECUTION_ENABLED` | `false` |
| `LIVE_TRADING_APPROVED` | `false` |
| Canonical kill switch | absent, therefore `INACTIVE` |
| Orders | 30 |
| Trades | 0 |

Only the two required boolean gate values were emitted. No credential or full
service environment was printed. The active agent and dashboard were not
restarted.

## Host and PostgreSQL availability

| Check | Observed value |
|---|---|
| Filesystem | 1,007 GB total, 718 GB available, 25% used |
| PostgreSQL client | 16.14 |
| PostgreSQL server binary | not installed |
| System/user PostgreSQL service | inactive / inactive |
| `127.0.0.1:5432` | no response; no listener found |
| `127.0.0.1:55432` | no response; no listener found |
| Docker / Docker Compose | unavailable in this WSL distro |
| Podman / Podman Compose | unavailable |
| Existing database/role catalog | unavailable because no server is running |

The reviewed provisioning decision is native PostgreSQL 16, dedicated cluster
`trading-agent`, localhost port 55432. Installing the Ubuntu server package
requires an interactive local `sudo`; no password will be requested or passed
through Codex.

## Legacy count baseline

The scan reused Phase 2 strict normalization and read sources only.

| Domain | Observed value |
|---|---:|
| `report_*.json` files discovered | 2,272 |
| Valid market reports | 2,186 |
| Invalid market reports | 86 |
| Valid report asset rows | 23,961 |
| Distinct report symbols | 17 |
| Latest valid report | `2026-06-25T04:54:37.766581Z` |
| Latest valid report file | `report_20260625_045437.json` |
| Latest valid report assets | 10 |
| Decision nonblank records seen | 16,653 |
| Valid decisions | 16,653 |
| Invalid/blank decisions | 0 / 0 |
| SQLite signals | 344 |
| Distinct SQLite signal symbols | 11 |
| Scratchpad cost-source files | 2,271 |
| Current API cost-session scope | newest 20 sessions |
| Invalid lines in current cost scope | 0 |
| Observed LLM/tool events in current cost scope | 0 / 220 |
| Capabilities | 9 total, 0 verified, all `UNKNOWN` |

Report and decision symbols are covered by the Phase 1 canonical registry: ten
crypto assets plus the explicitly registered equities. Unknown symbols are not
accepted during migration.

## Checksum baseline

| Artifact | SHA-256 |
|---|---|
| Canonical legacy asset registry | `05f6fe43333a3b484aee0abb604b74a5c8e0cda251b526fe7c7b3f00bcad9c8b` |
| Report source inventory | `4484acb8d1aa364f8c72368bdf273cf68f0d970503e6da8820f89b2057cf835d` |
| Latest valid report | `ad26c0bc07c77b1f37af6497b339cdb40d7324e0c8f88dd61b7344fc103cfebc` |
| Decisions JSONL | `0e97979237e4f0eaee8bc20235696a278c5b91e765acb5da591c24a358f981a3` |
| SQLite signals deterministic logical export | `693f2985c61972fccde1d71a7b452f2fb9bec588a1f4e3a41995b8217974e030` |
| SQLite orders/trades deterministic logical export | `ae1ee321d50f34c0504399dcaea07c799d2775be3e15cbce1433f68803973b09` |
| Scratchpad cost-source inventory | `0deb016be82bed327db5b197a480c35b15653ebb540071ed849f117680d5d732` |
| Deterministic fixture subset | `281a49e53146f8a9d09f4674f9caadcbe8c2543365ba204faa9fd0caae82195b` |
| Phase 3 combined source inventory | `dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce` |

The combined inventory is the SHA-256 of labeled hashes for the asset registry,
market-report inventory, decisions file, scratchpad cost-source inventory, and
the deterministic SQLite signals export. Mutable equity snapshots, live prices,
WAL files, raw logs, reflections, and other out-of-scope archives are excluded.

## Read-only proof

The baseline scan left the decision file size/mtime unchanged and left the
`report_*.json` file count unchanged. SQLite was opened with read-only mode for
logical counts and hashes. No source was repaired, rewritten, moved, or
deleted.

The current SQLite database is actively written by the pre-existing agent, so
raw database-file checksums are not a valid immutable baseline. Phase 3 uses
stable logical exports for the migrated signal and safety-count subsets and
will take an explicit read-consistent snapshot before apply.

## Pre-apply gate

PostgreSQL apply is prohibited until all of the following have happened in this
session sequence:

1. PostgreSQL provisioning and Alembic migrations pass.
2. The real-data importer runs without `--apply`.
3. Dry-run explains 2,186 valid and 86 invalid reports, 23,961 report asset
   rows, 16,653 valid decisions, 344 SQLite signals, capability defaults, and
   cost evidence scope.
4. Source inventory and deterministic subset hashes are recaptured.
5. Backup and tested-restore commands are reviewed and executed as applicable.
6. The user reviews the dry-run, count expectations, invalid evidence, and
   backup result before explicit apply.
