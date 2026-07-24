# Phase 3 PostgreSQL Operational Store Design

**Status:** approved in-session on 2026-07-11.

## Goal

Create a localhost-only PostgreSQL operational store with versioned schema,
complete legacy lineage, an idempotent and resumable importer, repository
selection between legacy and PostgreSQL, and evidence that the Phase 2 Control
API contract and Phase 1 safety posture do not change.

Phase 3 changes storage and attribution only. It does not change trading
signals, strategies, models, prompts, execution, scheduling, public routing, or
live-trading authority.

## Safety boundary

- Requested and effective modes remain `paper`.
- `LIVE_EXECUTION_ENABLED` and `LIVE_TRADING_APPROVED` remain independent and
  false.
- The canonical kill-switch resolver and fail-closed behavior remain unchanged.
- No broker, exchange, order, cancellation, credential-connectivity, or venue
  probe is allowed.
- The active agent, legacy dashboard, port 3002, and Cloudflare route are not
  restarted or cut over.
- JSON, JSONL, SQLite, reports, scratchpads, and the 5.2 GB legacy tree are
  read-only sources. They are neither moved nor rewritten.
- PostgreSQL writes use explicit transactions and have rollback tests.
- The importer never runs in apply mode without the separate `--apply` flag.

## Architecture

```text
Legacy JSON / JSONL / SQLite
            |
            v
Read-only legacy adapters
            |
            v
Strict versioned normalization
            |
            v
Dry-run planner / idempotent importer
            |
            v
PostgreSQL operational store
            |
            v
Repository protocols and query services
       +----+----+
       |         |
    legacy    postgres
       |         |
       +----+----+
            |
            v
Unchanged Phase 2 Control API contracts
```

The application selects one repository bundle at its composition root using
`TRADING_STORE_BACKEND=legacy|postgres`. Route handlers contain no SQL and no
backend-specific business rules. An invalid backend value fails startup. A
PostgreSQL failure does not silently fall back to legacy; readiness reports the
failure, while rollback is an explicit configuration change to `legacy`.

## PostgreSQL provisioning and roles

Use PostgreSQL 16 from the Ubuntu package, with a dedicated cluster named
`trading-agent` on port 55432. It binds only to `127.0.0.1`; local SCRAM rules
are used for database roles. Docker and Podman are not part of this design.

Roles are separated as follows:

- `trading_owner` owns the database/schema and runs Alembic. It is not used by
  the Control API.
- `trading_migrator` may read and write operational and migration tables but is
  not a superuser and cannot create roles or databases.
- `trading_reader` has `CONNECT`, schema `USAGE`, and `SELECT`; its default
  transaction is read-only.

Credentials live in a protected local environment file with mode 0600. The
repository contains only a secret-free `.env.example`. Health checks use
host, port, database, and user fields without embedding a password. Logs and
public errors never include a DSN.

## Schema

Alembic is the only mechanism that changes schema. PostgreSQL text columns with
`CHECK` constraints are preferred over PostgreSQL enum types so contract enum
growth and Alembic rollback remain explicit and portable.

### Canonical and operational tables

- `assets`: canonical identity, symbol, asset class, instrument type, base and
  quote currencies, and status. Symbol is indexed but is not globally unique.
- `market_reports`: report identity, `as_of`, `generated_at`, freshness,
  schema/normalization versions, provenance, source identity, and migration
  run.
- `market_asset_snapshots`: operational report fields required by the current
  Control API, keyed by report and asset. Large legacy payloads remain in the
  archive and may be referenced by `raw_evidence_ref`.
- `decisions`: stable Phase 2 decision ID, canonical asset ID, action,
  confidence, timestamps, report link, source identity, provenance, and run.
- `decision_signal_snapshots`: the typed signal metrics nested in each legacy
  decision, stored one-to-one with its decision.
- `signals`: historical SQLite or report-derived signals with distinct source
  lineage. Sources are not merged merely because normalized values match.
- `capability_evidence`: append-only history. Absence of evidence remains
  `UNKNOWN`; no capability is promoted to `PASS` by migration.
- `cost_summaries` and `cost_sessions`: only observed `UNKNOWN` or `ESTIMATED`
  evidence required for API parity. Full scratchpad payloads are not stored.
- `system_status_snapshots`: historical observations only. They never become
  current live-safety authority.
- `migration_runs`, `migration_source_files`, `migration_source_chunks`, and
  `migration_errors`: run lifecycle, inventory, resume checkpoints, counts,
  and sanitized quarantine evidence.
- `audit_events`: storage, import, normalization, and control-plane audit
  events. It is not an order or execution event store.

Orders, fills, positions, live execution state, broker credentials, raw report
payloads, reflections, model binaries, candles, and runtime logs are outside
this phase.

### Lineage and time

Where applicable, operational records carry:

```text
schema_version
normalization_version
source_type
source_path
source_hash
source_record_index
source_record_fingerprint
provenance_quality
event_time
known_at
ingested_at
as_of
migration_run_id
```

All database timestamps use `TIMESTAMPTZ`. Directly evidenced time is `EXACT`;
an explicitly derived time is `DERIVED`. When legacy data lacks `known_at`, it
stays null and provenance is `LEGACY_ESTIMATED`. The importer never silently
sets `known_at=event_time`. `source_mtime` is informational inventory metadata
only and is excluded from identity and lineage decisions.

### Identity, constraints, and indexes

Canonical asset identity is unique across asset class, instrument type, base
currency, and quote currency. Principal uniqueness rules are:

```text
market_reports(source_hash, normalization_version)
market_asset_snapshots(report_id, asset_id)
decisions(source_hash, source_record_index, normalization_version)
signals(source_hash, source_record_index, normalization_version)
migration_source_chunks(run_id, source_hash, domain, first_record_index)
```

Principal query indexes are:

```text
market_reports(as_of DESC, report_id DESC)
decisions(as_of DESC, decision_id DESC)
decisions(asset_id, as_of DESC, decision_id DESC)
decisions(action, as_of DESC, decision_id DESC)
signals(as_of DESC, signal_id DESC)
capability_evidence(capability_id, last_run_at DESC)
```

## Importer and normalization

The CLI supports `--dry-run`, `--apply`, `--resume RUN_ID`, domain selection,
record limits, and an individual source path. Absence of `--apply` always means
dry-run. Dry-run may read PostgreSQL to calculate existing idempotency keys but
does not write either database or legacy source.

Dry-run reports records seen, valid, invalid, would insert, would skip, would
update, warnings, source hashes, normalization version, and inventory hash.
System/schema/source-read errors return a non-zero exit; ordinary invalid
legacy records are counted and do not crash the scan.

Normalization is strict and versioned. Safe aliases such as
`STRONG SELL -> STRONG_SELL` create a reason-coded audit event. Numeric
confidence remains numeric; strings are not silently coerced. Unknown assets,
unknown enum values, invalid confidence, missing required fields, and ambiguous
naive timestamps are quarantined. Missing `known_at` is retained as null and
marked `LEGACY_ESTIMATED`.

Required error codes are:

```text
INVALID_JSON
SCHEMA_VALIDATION_FAILED
MISSING_REQUIRED_FIELD
INVALID_ENUM
INVALID_CONFIDENCE
UNKNOWN_ASSET
DUPLICATE_SOURCE_RECORD
SOURCE_READ_ERROR
DATABASE_WRITE_ERROR
```

`migration_errors` stores only a sanitized message, payload hash, source
reference, record index, and reason code. It never stores a full invalid
payload.

## Transactions, idempotency, and resume

Report JSON is processed in one transaction per source file. Large JSONL and
SQLite-derived streams use deterministic chunks of 500 records. Each chunk is
fully validated before its valid rows, quarantine rows, audit events, and
checkpoint are committed atomically. An unexpected database error rolls back
the complete chunk; failure status is recorded in a separate short
transaction.

The record key is:

```text
sha256(domain + NUL + source_hash + NUL + source_record_index + NUL + normalization_version)
```

The same content, source position, domain, and version is skipped on rerun.
Changed source content produces a new source hash and new identity. Equal
normalized values from distinct provenance are not blindly deduplicated. Phase
3 never updates an existing canonical row; `records_updated` remains zero.
Any collision with different canonical content fails or quarantines rather
than overwriting a better record.

Resume verifies the original source root, inventory hash, normalization
version, and Alembic revision. Committed chunks are skipped; failed or pending
chunks are retried. Changed source content cannot resume the old run and must
start a new run.

## Repositories and current safety truth

Repository protocols cover market reports, decisions, signals, capabilities,
costs, and system status. Both implementations return the same canonical
Pydantic response models and use stable ordering with an ID tie-breaker.
Offset pagination is retained because changing to cursors would change the
Phase 2 contract.

Current system status deliberately combines two authorities:

- `.mode`, both hard gates, the canonical kill switch, live-price heartbeat,
  and read-only SQLite order/trade counts remain current safety truth.
- Research freshness and operational data health use the selected repository
  backend.

Historical PostgreSQL status snapshots can therefore never claim current live
availability. `/v1/signals` preserves its Phase 2 meaning: latest report assets
combined with recent decisions. The internal PostgreSQL signals table does not
silently change the public endpoint semantics.

The OpenAPI and Pydantic response contracts remain byte-for-byte unchanged.
The selected backend is recorded only in internal structured logs and evidence
documents, not as a new public response field.

## Reconciliation and dual-read

Reconciliation records source files discovered and processed, reports,
report-asset rows, decisions, signals, capabilities, cost evidence, duplicates,
skips, invalid rows, quarantine rows, per-source hashes, the combined inventory
hash, and a deterministic canonical subset export hash.

The dual-read suite executes identical query DTOs through legacy and
PostgreSQL repository bundles. It covers the latest report, report assets,
first and last decision pages, deterministic decision samples, asset/action/date
filters, capabilities, cost summary, signals, freshness, and migrated status
fields. Nondeterministic envelope fields such as trace IDs and response
generation times are excluded from comparison.

Every mismatch is classified as one of:

```text
EXPECTED_NORMALIZATION
LEGACY_INVALID_RECORD
MIGRATION_BUG
QUERY_ORDERING_BUG
CONTRACT_BUG
```

The last three classifications block PostgreSQL readiness. The first two are
accepted only with a source reference, reason code, and explained count.

## Backup and restore gate

Before apply, source inventory hashes and count expectations are recaptured. If
the target PostgreSQL database contains data, a custom-format `pg_dump` is
created. The dump is restored to a temporary database, schema/reconciliation
queries run there, and the temporary database is removed only after the drill
passes. An empty target database is documented explicitly rather than
pretending a data backup was required.

The real-data dry-run is a mandatory stop point. Apply is prohibited until the
dry-run counts, 86 currently invalid report files, backup plan, and source
inventory are reviewed in-session. A changed source inventory invalidates the
review and requires another dry-run.

## Verification

Verification proceeds from small to broad:

1. Pure unit tests for hashing, source discovery, strict normalization, reason
   codes, and deterministic chunking.
2. Alembic migration tests against empty and existing databases.
3. PostgreSQL repository tests using isolated transactions and a dedicated test
   database.
4. Same-source rerun, resume, changed-source, quarantine, and chunk rollback
   tests.
5. Legacy/PostgreSQL repository parity and API regression under both backend
   values.
6. Contract drift generation checks.
7. Candidate dashboard tests, typecheck, lint, and build.
8. Phase 1 backend integration and safety regression tests.
9. Legacy file hash/stat and SQLite read-only side-effect proof.
10. Backup/restore drill and reconciliation against the restored database.
11. `EXPLAIN (ANALYZE, BUFFERS)` for latest report, decision first/deep page,
    decision count, and asset/action/date filters.
12. Final runtime evidence for paper/paper, false/false gates, inactive kill
    switch, and unchanged 30/0 orders/trades.

No partitioning, cache, materialized view, Redis, Celery, scheduler, command
endpoint, public deployment, execution refactor, or live enablement is included.

## Implementation and review sequence

1. Commit the approved design, pre-change checkpoint, and ADRs.
2. Provision native PostgreSQL 16 on localhost.
3. Add Alembic schema and database migration tests.
4. Add strict dry-run import and fixture evidence using TDD.
5. Run the real-data dry-run.
6. Stop for review of hashes, counts, invalid records, and backup plan.
7. Perform the PostgreSQL backup/restore drill.
8. Run explicit apply only after review approval.
9. Rerun and exercise resume/idempotency evidence.
10. Complete reconciliation, dual-read, and performance evidence.
11. Run a local PostgreSQL-backed Control API and candidate dashboard smoke.
12. Switch back to legacy, prove rollback, and rerun final safety checks.

Commits remain scoped to operations, schema, migration tracking, importer,
repositories, API configuration, tests, and evidence documentation. Database
volumes, dumps, credentials, `.env`, raw legacy data, models, reports, and logs
are never committed.

## Phase exit

Phase 3 may be declared ready only when the second apply inserts zero rows (or
only newly discovered source records), resume and rollback evidence pass,
dual-read has no unexplained difference, restore has been tested, the API
contract is unchanged, legacy hashes are unchanged, and runtime safety remains
paper/paper with false/false gates, inactive kill switch, and 30/0
orders/trades.

The only possible positive next-phase decision is:

`GO FOR PHASE 4 — DURABLE JOB QUEUE AND RESEARCH SCHEDULER`

This decision never authorizes live trading.
