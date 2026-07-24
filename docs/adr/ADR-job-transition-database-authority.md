# ADR: Job Transition Database Authority

## Status

Accepted for source and disposable-database verification. Runtime migration and
service rollout require separate operator approval.

Revision `0006` is a frozen source baseline, not a release-ready or final
database-authority head. Its verification does not bind the complete
trigger/function/policy/RLS/raw ACL/default-ACL catalog and does not prove the
complete event chain. A separately reviewed forward-only `0007`, backed by
disposable-database RED/GREEN catalog and history evidence, remains required
before any runtime migration or Job Plane rollout.

## Context

Migration `0005_job_plane_role_split` gives the Job API, worker, and scheduler
distinct PostgreSQL identities and narrows their table and column grants. The
normal repositories update a job and append its event in one application
transaction. That transaction is atomic, but the database grants still let a
holder of an application credential issue the permitted state statement
without issuing the matching append-only event. API and scheduler identities
can likewise insert an initial `QUEUED` row separately from event sequence 1.

The append-only trigger prevents rewriting an event after it exists; it cannot
prove that an event was created for every state change. This leaves database
state capable of contradicting the audit stream even though the reviewed
repository path is correct.

## Decision

Add the forward-only revision
`0006_job_transition_database_authority`, descending from the frozen
`0005_job_plane_role_split` revision. Do not rewrite `0005`, including when a
runtime database is still at `0004`.

Revision `0006` creates an owner-controlled `job_plane` schema containing a
small set of fixed lifecycle functions:

- operator SNAPSHOT enqueue and cancellation;
- scheduled SNAPSHOT enqueue;
- worker claim, start, lease control, finalization, and expired-lease recovery.

Each entry point is `SECURITY DEFINER`, owned by `trading_owner`, fixes its
allowed actor and job type internally, checks the exact `SESSION_USER`, uses
`search_path=pg_catalog`, and fully qualifies every application relation. The
functions contain no dynamic SQL and return only a narrow role-appropriate
projection. A caller cannot supply a table, column, role, actor type, event
sequence, source state, or SQL fragment.

The catalog contract pins each function's complete identity arguments and
exact result type. A function with the right name and inputs but a broader
result projection is not equivalent authority and fails migration postflight.

The Job API and scheduler lose direct `jobs` INSERT/UPDATE authority. The
worker loses direct `jobs` UPDATE and `job_attempts` INSERT/UPDATE authority.
All three identities lose direct `job_events` INSERT authority. They receive
EXECUTE only on their exact lifecycle functions, without grant option. Read
projections and the existing non-transition heartbeat/artifact capabilities
remain separately constrained by ACL and RLS.

Every state-changing function locks the job before deriving the next event
sequence. A function updates the job and any attempt, then inserts the matching
event in the same PostgreSQL statement and transaction. Initial enqueue creates
sequence 1. A retry creates both its terminal event and requeue event. An event
failure therefore rolls the associated mutation back even when a caller uses
autocommit.

The schema owner remains the only schema and break-glass authority. This ADR
claims that approved runtime roles cannot commit a state transition without its
event; it does not claim that a database superuser or `trading_owner` is unable
to perform reviewed emergency DML.

## Required invariants

- Each job has event sequence 1 from no state to `QUEUED`.
- Event sequences are unique and contiguous per job.
- After every committed lifecycle operation, the latest event target equals
  the job state.
- A stale state, fence, worker, attempt, or lease writes no state or event.
- A missing or partial stored child-process identity is `UNVERIFIABLE`; expired
  lease recovery blocks it and cannot requeue it as an observed-absent child.
- Invalid transition, retry, actor, namespace, or function caller fails closed.
- Runtime roles have no direct job-state, attempt-outcome, or event-insert DML.
- Function owner, signature, security attributes, search path, and EXECUTE ACL
  are exact catalog evidence and release inputs.

## Alternatives rejected

- **Application transactions only:** correct for reviewed code, but do not
  constrain a direct credential holder.
- **Deferred audit validation:** detects inconsistency late and makes ordered
  cross-table state/event completeness difficult to express without retaining
  direct mutation authority.
- **A trigger enabled by a custom GUC:** application roles can set arbitrary
  custom GUC values, so the bypass token is forgeable.
- **One generic transition function:** a broad caller-selected state operation
  becomes a new privileged command language. Fixed lifecycle functions expose
  less authority.
- **Rewriting revision `0005`:** destroys the reviewed migration identity and
  makes applied and source histories ambiguous.

## Consequences

State policy now exists in Python contracts and fixed PL/pgSQL routines.
Disposable integration tests must prove their parity for every allowed and
denied path. Repository code becomes smaller at mutation sites but the database
functions become security-critical release content. Job API/worker readiness,
systemd templates, semantic authority, and Release Authority v2 must require
exact head `0006_job_transition_database_authority` and bind the new migration
digest.

Connections must authenticate directly as one of the three runtime roles.
Using a shared pool login plus `SET ROLE` intentionally fails the
`SESSION_USER` check.

The existing `0005` rollout runbook pins head `0005` and prohibits
`SECURITY DEFINER`; it does not authorize this migration. Runtime use requires
a separately reviewed `0006` procedure and exact approval.

## Backup and restore

Custom database dumps do not provision cluster roles or their credentials.
Restore targets must pre-provision the exact role identities without exporting
passwords. Restore the schema as `trading_owner` without disabling triggers or
discarding ACLs, then verify function ownership, security attributes, exact
EXECUTE grants, direct-DML denials, RLS, append-only enforcement, event
invariants, and the exact Alembic head. Database CONNECT/TEMP privileges and
global role flags require separate cluster-level verification.

## Failure and rollback

Revision `0006` is intentionally forward-only. On any migration, catalog,
function, restore, or role-matrix failure, keep application services inactive,
preserve jobs/events/artifacts and evidence, and apply only a separately
reviewed forward repair. Do not downgrade, delete audit events, or restore the
old shared runtime role.
