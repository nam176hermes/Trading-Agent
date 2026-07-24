# Phase 4 Durable Job Schema

## Implemented revision

Alembic revision `0004_durable_research_jobs` extends
`0003_contract_lineage_repair`. It has been applied to the local PostgreSQL
operational store. The six Phase 4 tables are currently empty; applying the
revision did not change the existing `43,055` canonical rows or `222`
quarantine rows.

| Table | Purpose |
|---|---|
| `jobs` | Canonical request, typed payload, idempotency identity, state, lease and result summary |
| `job_attempts` | Per-claim worker, process identity, outcome and bounded stream references |
| `job_events` | Append-only transition audit ordered by per-job sequence |
| `scheduler_heartbeats` | UTC tick and enqueue outcome, distinct from job completion |
| `job_artifacts` | Protected artifact reference, hash, size and validator metadata |
| `worker_heartbeats` | Worker state and optional current job/attempt identity |

The database constrains the four job types and nine canonical states. The
unique key `(job_type, idempotency_key)` is the durable deduplication boundary.
Foreign keys tie attempts, events, artifacts and heartbeat current-work
references to their canonical job. A trigger rejects normal `UPDATE` and
`DELETE` operations on `job_events`.

The migration grants only the required DML and revision-read privileges to the
dedicated non-owner `trading_jobs` role. It grants neither ownership nor DDL or
DELETE authority. A later role split can separate API, worker and scheduler
privileges without changing the schema contract.

No Phase 3/3B migration-run, canonical entity, quarantine evidence or legacy
source was rewritten. Normal rollback preserves the six tables for audit; a
schema downgrade is reserved for a separately approved maintenance operation.

References: [state-machine ADR](../adr/ADR-phase-4-job-state-machine.md),
[command-boundary ADR](../adr/ADR-phase-4-command-api-boundary.md), and
[pre-change checkpoint](phase-4-prechange-checkpoint.md).
