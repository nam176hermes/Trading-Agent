# Phase 3 Pre-Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision the approved localhost PostgreSQL 16 operational store, implement and test its Alembic schema and idempotent importer, run a real-data dry-run, and stop with real apply blocked for user review.

**Architecture:** A dedicated user-owned native PostgreSQL cluster on `127.0.0.1:55432` is managed with PostgreSQL 16 `initdb/pg_ctl`, independently of the distro `main` cluster and active trading services. Strict, versioned legacy scanners produce canonical import plans; explicit fixture-only apply transactions write through Psycopg, while the real legacy tree is dry-run only. Alembic owns all schema changes and a three-role permission model separates ownership, importing, and read-only API access.

**Tech Stack:** Python 3.11, PostgreSQL 16.14, Alembic, SQLAlchemy Core, Psycopg 3, psycopg-pool, Pydantic 2, pytest 9, uv.

## Global Constraints

- Never run real-data `--apply`; stop after real-data dry-run and pre-apply review package.
- Keep requested/effective mode `paper/paper`, both live gates false, canonical kill switch inactive, and orders/trades 30/0.
- Do not restart `trading-agent.service` or `trading-dashboard.service`; do not change port 3002 or Cloudflare.
- Do not call broker, exchange, credential, scheduler, or command paths.
- Open JSON, JSONL, and SQLite legacy sources read-only and preserve their hashes/stats.
- Do not stage or modify pre-existing `docs/audits/` artifacts.
- Do not print or commit passwords, DSNs, environment files, database dumps, legacy data, or runtime logs.
- Use deterministic 500-record chunks and normalization version `phase3-v1`.
- Use `TIMESTAMPTZ`, text plus `CHECK` constraints, and Alembic as the only schema mutation mechanism.
- Every PostgreSQL write is inside an explicit transaction with rollback coverage.

---

### Task 1: Native PostgreSQL Cluster and Secret-Safe Role Provisioning

**Files:**
- Create: `ops/postgres/README.md`
- Create: `ops/postgres/.env.example`
- Create: `ops/postgres/verify-cluster.sh`
- Create: `ops/postgres/provision-roles.sql`
- Create: `docs/implementation/phase-3-postgres-setup.md`

**Interfaces:**
- Consumes: installed PostgreSQL 16 binaries and user-owned data directory `~/.local/share/trading-agent/postgres/16/trading-agent`.
- Produces: localhost database `trading_agent`, roles `trading_owner`, `trading_migrator`, `trading_reader`, and protected env files under `~/.config/trading-agent/`.

- [ ] **Step 1: Reconfirm runtime invariants and cluster availability**

Run the existing read-only mode/gate/kill-switch/SQLite checks and:

```bash
pg_lsclusters
pg_isready -h 127.0.0.1 -p 55432
```

Expected: safety values match the global constraints and port 55432 is free. Do not request, receive, or process a sudo password.

- [ ] **Step 2: Create the dedicated cluster with explicit local settings**

Create a 0700 data directory and runtime socket directory. Generate the
bootstrap secret directly into a 0600 local admin environment file without
printing it, provide it to `initdb` through a protected temporary pwfile, and
remove the pwfile immediately:

```bash
/usr/lib/postgresql/16/bin/initdb \
  -D ~/.local/share/trading-agent/postgres/16/trading-agent \
  --username=postgres --auth-local=scram-sha-256 \
  --auth-host=scram-sha-256 --pwfile=<protected-pwfile>
/usr/lib/postgresql/16/bin/pg_ctl \
  -D ~/.local/share/trading-agent/postgres/16/trading-agent \
  -l ~/.local/state/trading-agent/postgres/trading-agent.log start
```

Set `cluster_name='trading-agent'`, `listen_addresses='127.0.0.1'`,
`port=55432`, a private Unix socket directory, SCRAM password encryption, and
safe connection/statement defaults before start. Expected: SQL
`SHOW cluster_name` returns `trading-agent`; `ss -ltn` shows only
`127.0.0.1:55432` for this cluster. The distro `16/main` cluster remains
independent on 5432.

- [ ] **Step 3: Create secret-safe operations templates**

Add `.env.example` containing only:

```dotenv
TRADING_DATABASE_HOST=127.0.0.1
TRADING_DATABASE_PORT=55432
TRADING_DATABASE_NAME=trading_agent
TRADING_DATABASE_USER=replace-with-role
TRADING_DATABASE_PASSWORD=replace-locally
TRADING_DB_POOL_MIN=1
TRADING_DB_POOL_MAX=5
TRADING_DB_STATEMENT_TIMEOUT_MS=5000
TRADING_STORE_BACKEND=legacy
```

Add `verify-cluster.sh` that checks config/listeners without reading passwords, and `provision-roles.sql` using psql variables for passwords rather than literals. Role definitions must set all three roles `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`; set `trading_reader` default transactions read-only.

- [ ] **Step 4: Create protected local environment files**

Generate secrets locally without echoing them, create `~/.config/trading-agent` mode 0700 and three files mode 0600, and provision roles/database through a protected temporary psql variable file or stdin. Remove temporary material immediately after success.

- [ ] **Step 5: Verify positive and negative permissions**

Create a temporary probe table through the owner after Alembic is available, then assert:

```text
reader SELECT succeeds
reader INSERT fails
reader UPDATE fails
migrator INSERT succeeds
migrator CREATE ROLE fails
migrator CREATE DATABASE fails
```

Never print credentials or full connection strings.

- [ ] **Step 6: Commit operations scaffolding**

```bash
git add ops/postgres docs/implementation/phase-3-postgres-setup.md
git commit -m "ops: add localhost PostgreSQL development stack"
```

### Task 2: Database Dependencies and Safe Configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `apps/control_api/trading_control/__init__.py`
- Create: `apps/control_api/trading_control/db.py`
- Create: `tests/control_api/test_database_config.py`

**Interfaces:**
- Consumes: split host/port/database/user/password environment fields.
- Produces: `DatabaseSettings.from_env()`, `redacted_database_identity()`, `connect()`, and `create_pool()`.

- [ ] **Step 1: Write failing configuration tests**

Tests must prove missing fields fail closed, numeric pool/timeout bounds are validated, repr/log identity excludes passwords, and reader connections request read-only transactions.

```python
def test_database_settings_repr_never_contains_password():
    settings = DatabaseSettings.from_env(valid_env(password="do-not-leak"))
    assert "do-not-leak" not in repr(settings)
    assert settings.redacted_identity() == {
        "host": "127.0.0.1", "port": 55432,
        "database": "trading_agent", "role": "trading_reader",
    }
```

- [ ] **Step 2: Run tests and observe RED**

```bash
uv run pytest -q tests/control_api/test_database_config.py
```

Expected: import failure for `trading_control.db`.

- [ ] **Step 3: Add pinned dependencies with uv**

```bash
uv add "alembic" "sqlalchemy" "psycopg[binary]" "psycopg-pool"
```

Do not hand-edit `uv.lock`.

- [ ] **Step 4: Implement minimal safe configuration**

`DatabaseSettings` stores split fields, exposes a runtime conninfo builder only at the connection boundary, validates localhost host and positive pool/timeout bounds, and overrides repr to the redacted identity. `connect()` passes `options=-c statement_timeout=<ms>` and `read_only=True` for the reader path.

- [ ] **Step 5: Run GREEN and full Phase 2 tests**

```bash
uv run pytest -q tests/control_api/test_database_config.py
uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock apps/control_api/trading_control tests/control_api/test_database_config.py
git commit -m "db: add safe PostgreSQL connection configuration"
```

### Task 3: Versioned Alembic Operational Schema

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_phase3_operational_store.py`
- Create: `apps/control_api/trading_control/schema.py`
- Create: `tests/control_api/test_alembic_schema.py`
- Create: `docs/implementation/phase-3-schema.md`

**Interfaces:**
- Consumes: owner `DatabaseSettings` through environment; no DSN is stored in `alembic.ini`.
- Produces: revision `0001_phase3_operational_store` and the fourteen approved tables.

- [ ] **Step 1: Write failing empty-database schema tests**

The tests create an isolated database, run `command.upgrade(config, "head")`, and inspect:

```python
EXPECTED_TABLES = {
    "assets", "market_reports", "market_asset_snapshots", "decisions",
    "decision_signal_snapshots", "signals", "capability_evidence",
    "cost_summaries", "cost_sessions", "system_status_snapshots",
    "migration_runs", "migration_source_files", "migration_source_chunks",
    "migration_errors", "audit_events",
}
```

Assert head revision, foreign keys, named checks, required unique constraints, and required indexes.

- [ ] **Step 2: Run schema test and observe RED**

```bash
uv run pytest -q tests/control_api/test_alembic_schema.py
```

Expected: Alembic configuration/revision missing.

- [ ] **Step 3: Implement metadata and revision**

Define SQLAlchemy Core tables with UUID/string identities, `DateTime(timezone=True)`, explicit foreign keys, named text checks, and no PostgreSQL enum or raw legacy blob. The revision creates all tables/indexes and grants table/sequence privileges to migrator/reader roles. `downgrade()` raises a documented restore-based rollback error rather than destructively dropping the store.

- [ ] **Step 4: Verify schema and role permissions GREEN**

```bash
uv run pytest -q tests/control_api/test_alembic_schema.py
uv run alembic upgrade head
uv run alembic current
uv run alembic history
```

Expected: revision `0001_phase3_operational_store (head)`.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini alembic apps/control_api/trading_control/schema.py tests/control_api/test_alembic_schema.py docs/implementation/phase-3-schema.md
git commit -m "db: add versioned operational schema migrations"
```

### Task 4: Deterministic Identity, Chunking, and Strict Normalization

**Files:**
- Create: `apps/control_api/trading_control/identity.py`
- Create: `apps/control_api/trading_control/models.py`
- Create: `apps/control_api/trading_control/normalization.py`
- Create: `tests/control_api/test_migration_identity.py`
- Create: `tests/control_api/test_migration_normalization.py`

**Interfaces:**
- Produces: `NORMALIZATION_VERSION = "phase3-v1"`, `record_key()`, `sha256_file()`, `chunk_ranges()`, `normalize_decision_record()`, `normalize_report_record()`, and typed planned errors/audit events.

- [ ] **Step 1: Write failing identity tests**

```python
def test_record_key_matches_adr():
    assert record_key("decisions", "a" * 64, 7, "phase3-v1") == hashlib.sha256(
        b"decisions\0" + b"a" * 64 + b"\07\0phase3-v1"
    ).hexdigest()

def test_chunks_are_fixed_and_deterministic():
    assert list(chunk_ranges(1001, 500)) == [(1, 500), (501, 1000), (1001, 1001)]
```

Also prove changed content/index/version changes the key and equal canonical values from different sources keep distinct keys.

- [ ] **Step 2: Run RED**

```bash
uv run pytest -q tests/control_api/test_migration_identity.py tests/control_api/test_migration_normalization.py
```

- [ ] **Step 3: Implement strict typed normalization**

Decision confidence accepts only non-boolean int/float in `[0,1]`; string `"0.5"` is `INVALID_CONFIDENCE`. `STRONG SELL` maps to `STRONG_SELL` and produces planned audit code `NORMALIZED_ACTION_ALIAS`. Unknown asset/action, missing fields, invalid JSON, and ambiguous naive timestamps produce the approved error codes. Missing `known_at` remains `None` with `LEGACY_ESTIMATED`.

- [ ] **Step 4: Run GREEN**

```bash
uv run pytest -q tests/control_api/test_migration_identity.py tests/control_api/test_migration_normalization.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/control_api/trading_control tests/control_api/test_migration_identity.py tests/control_api/test_migration_normalization.py
git commit -m "migration: add strict versioned normalization primitives"
```

### Task 5: Read-Only Source Inventory and Dry-Run Planner

**Files:**
- Create: `apps/control_api/trading_control/sources.py`
- Create: `apps/control_api/trading_control/planner.py`
- Create: `tests/control_api/test_source_inventory.py`
- Create: `tests/control_api/test_dry_run_planner.py`
- Create: `docs/implementation/phase-3-migration-design.md`

**Interfaces:**
- Consumes: a source root and optional read-only PostgreSQL key lookup.
- Produces: deterministic `SourceInventory`, `MigrationPlan`, `DomainCounts`, `PlannedError`, and `PlannedAuditEvent` without writes.

- [ ] **Step 1: Write failing discovery and no-write tests**

Synthetic fixtures cover reports, decisions, SQLite signals, nine unknown capabilities, and newest twenty cost sessions. Tests assert semantic source discovery, deterministic inventory hashes, relative paths, sanitized invalid evidence, and database table counts unchanged before/after planning.

- [ ] **Step 2: Run RED**

```bash
uv run pytest -q tests/control_api/test_source_inventory.py tests/control_api/test_dry_run_planner.py
```

- [ ] **Step 3: Implement deterministic scanners**

Reports are discovered as sorted `reports/report_*.json`; decision indices are one-based nonblank JSONL positions; SQLite uses `mode=ro` and a stable `ORDER BY id`; cost derives only metadata/events for the newest twenty semantic sessions and remains `UNKNOWN` or `ESTIMATED`; capability planning emits nine `UNKNOWN`, zero verified. Invalid evidence contains relative source path, payload SHA-256, record index, reason code, and sanitized message only.

- [ ] **Step 4: Run GREEN and side-effect checks**

```bash
uv run pytest -q tests/control_api/test_source_inventory.py tests/control_api/test_dry_run_planner.py tests/control_api/test_side_effects.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/control_api/trading_control tests/control_api/test_source_inventory.py tests/control_api/test_dry_run_planner.py docs/implementation/phase-3-migration-design.md
git commit -m "migration: add read-only dry-run legacy planner"
```

### Task 6: Fixture-Only Transactional Apply, Idempotency, and Resume

**Files:**
- Create: `apps/control_api/trading_control/writer.py`
- Create: `tests/control_api/test_fixture_importer.py`

**Interfaces:**
- Consumes: a `MigrationPlan` plus explicit `apply=True` and migrator connection.
- Produces: transactional migration run/file/chunk/domain/error/audit rows; rejects apply unless explicitly enabled.

- [ ] **Step 1: Write failing fixture apply tests**

Cover first apply counts, second apply zero inserts, sanitized errors, no payload retention, full-chunk rollback on injected database error, committed-chunk resume skip, failed-chunk retry, source-inventory mismatch rejection, updated count zero, and canonical-content collision without overwrite.

- [ ] **Step 2: Run RED**

```bash
uv run pytest -q tests/control_api/test_fixture_importer.py
```

- [ ] **Step 3: Implement explicit transactions**

Use Psycopg connection contexts. Reports commit per file; streams commit in 500-record chunks. Domain rows, planned errors, audit events, and chunk checkpoint commit together. Unexpected failures rollback the chunk; a separate transaction records failure status. Use `ON CONFLICT DO NOTHING` only after comparing canonical fingerprint; conflicting content becomes `DUPLICATE_SOURCE_RECORD` and never updates the existing row.

- [ ] **Step 4: Run GREEN and inspect database counts**

```bash
uv run pytest -q tests/control_api/test_fixture_importer.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/control_api/trading_control/writer.py tests/control_api/test_fixture_importer.py
git commit -m "migration: add fixture-only idempotent apply and resume"
```

### Task 7: Migration CLI with Dry-Run Default

**Files:**
- Create: `apps/control_api/trading_control/migrate.py`
- Create: `tests/control_api/test_migrate_cli.py`

**Interfaces:**
- Produces CLI options `--dry-run`, `--apply`, `--resume`, `--domain`, `--limit`, `--source-file`; no flag and `--dry-run` are equivalent.

- [ ] **Step 1: Write failing CLI tests**

Invoke `main()` against fixtures and prove default/explicit dry-run JSON outputs match, no table count changes, mutually exclusive apply/dry-run validation works, source-file containment rejects paths outside root, and a real-root safety guard requires a separate test-only override before apply.

- [ ] **Step 2: Run RED**

```bash
uv run pytest -q tests/control_api/test_migrate_cli.py
```

- [ ] **Step 3: Implement minimal CLI**

The CLI prints counts, hashes, normalization version, planned audits, warnings, and sanitized errors as deterministic JSON. It never logs the database password/DSN. Real source root plus `--apply` is rejected in this pre-apply implementation unless a fixture-only guard is present under tests.

- [ ] **Step 4: Run GREEN and fixture smoke**

```bash
uv run pytest -q tests/control_api/test_migrate_cli.py
PYTHONPATH=apps/control_api uv run python -m trading_control.migrate --source-root tests/fixtures/phase3
```

- [ ] **Step 5: Commit**

```bash
git add apps/control_api/trading_control/migrate.py tests/control_api/test_migrate_cli.py
git commit -m "migration: add dry-run-default import CLI"
```

### Task 8: Backup/Restore and Query Verification Harness

**Files:**
- Create: `ops/postgres/backup.sh`
- Create: `ops/postgres/restore-drill.sh`
- Create: `apps/control_api/trading_control/query_checks.py`
- Create: `tests/control_api/test_query_checks.py`
- Create: `docs/implementation/phase-3-backup-restore-plan.md`

**Interfaces:**
- Produces password-safe exact backup/restore commands and prepared `EXPLAIN (ANALYZE, BUFFERS)` queries without claiming real-data performance.

- [ ] **Step 1: Write failing query harness tests**

Assert each approved decision/report query sets the statement timeout, uses parameter binding, and references the expected index columns. Assert backup scripts rely on protected environment/`.pgpass`, never command-line passwords.

- [ ] **Step 2: Run RED, implement, then GREEN**

```bash
uv run pytest -q tests/control_api/test_query_checks.py
```

Implement exact empty-target and non-empty-target paths. For the current empty target, test schema/bootstrap with a temporary restore database and document that no operational data dump is required before real apply.

- [ ] **Step 3: Run restore drill and commit**

```bash
ops/postgres/restore-drill.sh
git add ops/postgres apps/control_api/trading_control/query_checks.py tests/control_api/test_query_checks.py docs/implementation/phase-3-backup-restore-plan.md
git commit -m "ops: add PostgreSQL backup restore and query checks"
```

### Task 9: Real-Data Dry-Run, Inventory Recapture, and Pre-Apply Review

**Files:**
- Create: `docs/implementation/phase-3-dry-run-results.md`
- Create: `docs/implementation/phase-3-invalid-record-review.md`
- Create: `docs/implementation/phase-3-pre-apply-review.md`
- Create: `docs/implementation/phase-3-test-evidence-pre-apply.md`

**Interfaces:**
- Consumes: read-only legacy root `/home/thenam176/.hermes/crypto-research`.
- Produces: reviewed counts/hashes/invalid classifications and an explicit real-apply block.

- [ ] **Step 1: Capture before-state hashes/stats and PostgreSQL counts**

Capture the nine checkpoint hashes, source stats, migration/domain table counts, runtime invariants, service PIDs, listener ownership, and current Git states. Do not hash mutable SQLite files directly.

- [ ] **Step 2: Run real-data dry-run without apply**

```bash
PYTHONPATH=apps/control_api uv run python -m trading_control.migrate \
  --source-root /home/thenam176/.hermes/crypto-research \
  --dry-run
```

Expected checkpoint explanation: 2,272 report files, 2,186 valid, 86 invalid,
23,961 report asset rows, 16,653 decisions seen/valid, 344 SQLite signals,
9 capabilities and 0 verified, newest 20 cost sessions with unknown/estimated
quality, and zero updates.

- [ ] **Step 3: Review invalid reports**

Group all 86 by approved reason code and schema category. Record relative path,
payload hash, sanitized explanation, and a redacted representative sample; do
not copy payload bodies.

- [ ] **Step 4: Recapture and compare inventory**

Recompute asset registry, report inventory, latest report, decision, logical
signals, logical order/trade, scratchpad inventory, fixture subset, and combined
hashes. If any input changed, explain it and rerun dry-run before using results.

- [ ] **Step 5: Run complete pre-apply verification**

```bash
uv run pytest -q
uv run python scripts/generate_contracts.py --check
npm test
npx tsc --noEmit
npm run lint
npm run build
```

Run candidate commands in `/home/thenam176/projects/trading-dashboard`, then
run the documented Phase 1 backend integration and safety commands. Reconfirm
paper/paper, false/false, inactive kill switch, unchanged 30/0, unchanged
service PIDs, port 3002, and source hashes/stats.

- [ ] **Step 6: Write review package and commit**

The final line of `phase-3-pre-apply-review.md` must be exactly:

```text
REAL-DATA APPLY STATUS: BLOCKED PENDING USER REVIEW
```

```bash
git add docs/implementation/phase-3-dry-run-results.md \
  docs/implementation/phase-3-invalid-record-review.md \
  docs/implementation/phase-3-pre-apply-review.md \
  docs/implementation/phase-3-test-evidence-pre-apply.md
git commit -m "docs: add phase 3 pre-apply dry-run evidence"
```

- [ ] **Step 7: Stop**

Do not run real-data `--apply`. Report either `READY FOR USER REVIEW BEFORE REAL-DATA APPLY` or `NOT READY FOR APPLY — BLOCKERS REMAIN`.
