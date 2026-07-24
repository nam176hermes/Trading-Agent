# Job Transition Database Authority

**Evidence date:** 2026-07-16
**Status:** `SOURCE/DISPOSABLE VERIFIED — UNCOMMITTED — RUNTIME NOT APPLIED`

## Finding under remediation

Migration `0005_job_plane_role_split` narrowed runtime identities and used
application transactions, but retained direct column-level state/attempt DML
and direct event insertion for the roles that needed each workflow. A holder
of one of those credentials could omit the matching event even though the
reviewed repository normally wrote state and event atomically.

The disposable RED run reproduced that failure. This is a database-authority
defect, not merely missing unit coverage.

## Chosen design

The accepted source design is documented in
`docs/adr/ADR-job-transition-database-authority.md` in the isolated candidate.
It uses a new forward-only migration:

`0006_job_transition_database_authority`

with parent:

`0005_job_plane_role_split`

`0005` is not rewritten. The longer `0006` revision requires widening
`public.alembic_version.version_num` from `VARCHAR(32)` to `VARCHAR(64)` in the
forward migration before Alembic stamps the new head.

## Required authority surface

Exactly eight fixed `SECURITY DEFINER` functions are allowed in an
owner-controlled `job_plane` schema:

- `api_enqueue_snapshot`;
- `api_cancel_snapshot`;
- `scheduler_enqueue_snapshot`;
- `worker_claim_snapshot`;
- `worker_start_snapshot`;
- `worker_control_snapshot_lease`;
- `worker_finalize_snapshot`;
- `worker_recover_expired_snapshot`.

Each function must be owned by `trading_owner`, use PL/pgSQL, set
`search_path=pg_catalog`, fully qualify application relations, contain no
dynamic SQL, reject the wrong `SESSION_USER`, and expose only its one exact
role-specific signature. `PUBLIC` and the two wrong runtime roles must lack
`EXECUTE`; grant option is forbidden.

All direct runtime-role job-state, attempt-state, and event-insert authority
must be revoked, including the column ACLs inherited from `0005`. Nine mutation
RLS policies must be removed while the fourteen reviewed read/heartbeat/
artifact policies remain exact.

## Atomicity and input rules

Every state-changing capability locks the job first, then any attempt, derives
the next event sequence under that lock, mutates state, and inserts the matching
append-only event in the same transaction. Invalid source state, stale fence,
expired lease, wrong actor namespace, wrong role, or malformed fixed input
must fail without state or event changes.

API and scheduler enqueue are SNAPSHOT-only. The database boundary must enforce
the exact canonical payload and fingerprint, not merely accept an arbitrary
JSON object from a direct credential holder:

- canonical payload: `{"requested_as_of":null,"scope":"default"}`;
- fingerprint:
  `dc993577d7fe81a0fc6b23e281e0b7e2a182d557143cfa312d21078271b4091a`.

Operator idempotency keys may not use `schedule:`. Scheduler keys must use the
exact valid Gregorian `schedule:snapshot:<UTC-minute>` namespace. Dedupe may
return the existing job only when payload fingerprint, actor, priority, and
namespace identity all match.

## Disposable acceptance evidence

Fresh disposable proof covered all of the following:

1. direct API and worker exploit statements raise insufficient privilege and
   make zero state/event changes;
2. every repository lifecycle path works through its fixed function;
3. invalid state/fence/role/payload/namespace calls write nothing;
4. retries append terminal and requeue events in one transaction;
5. recovery handles result reconciliation and process-identity observations
   without a transition/event split;
6. catalog owner, signature, language, security flags, search path, ACL, RLS,
   constraints, trigger, and Alembic head are exact;
7. a custom-format disposable restore preserves the authority surface; and
8. the temporary PostgreSQL process and directory are gone after the suite.

The combined authority, role, NULL-fuzz, and custom-format restore gate passed
99 tests. Repository-focused groups passed 39 and 8 tests, and worker
claim/lease integration passed 13 tests. The harness left zero temporary
PostgreSQL processes and zero temporary data directories. An independent
source security review found no remaining P0/P1 defect in the reviewed
migration at SHA-256:

`f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd`

The exact fixed authority surface consists of eight functions. Function owner,
input signature, exact return type, `SECURITY DEFINER`, fixed search path, and
complete EXECUTE grantee allowlist are migration postflight assertions and
restore-parity assertions. Unexpected grantees fail the gate. Complete,
partial, all-null, and systematically null required recovery observations were
tested fail closed; incomplete process identity becomes `UNVERIFIABLE` and
cannot be requeued.

The application repositories now call only the fixed lifecycle capabilities
for enqueue/cancel/claim/start/control/finalize/recovery. Final artifact writes
and finalization share one application transaction. A retry clears stale
result, error, cancellation, lease, and completion residue before requeue while
appending the terminal and requeue events atomically.

The broader jobs/Alembic suite is not wholly green: `780 passed, 3 failed`
because the canonical dashboard lacks a declared `@redocly/ajv` dependency for
three cross-language contract tests. This prevents a final clean commit but
does not invalidate the disposable PostgreSQL result above.

## Residual P2 hardening

- Expired-lease recovery still trusts the worker's non-null process
  observation; separating recovery authority would narrow that trust.
- Result/artifact sealing is atomic through the repository transaction rather
  than wholly contained in one database capability.
- The compatibility `heartbeat()` helper can renew before returning `false`
  when an optional expected-state check mismatches; runtime control uses the
  fixed lease capability instead.
- The migration preflight can further attest protected trigger-function bodies
  and ambient non-runtime role memberships/ACLs.
- `trading_owner` remains intentional audited break-glass/schema authority.

## Runtime boundary and rollback

The existing `0005` runtime runbook explicitly prohibits `SECURITY DEFINER`
and cannot authorize `0006`. Runtime use therefore needs a new independently
reviewed backup/migration/rollback procedure and exact approval after recovery.

This migration is forward-only. On a disposable failure, repair the candidate
source and recreate the disposable database. On any future runtime failure,
leave Job API/worker/scheduler inactive, preserve rows/events/artifacts and
backup evidence, and use only an approved forward repair. Never downgrade,
delete audit events, or restore the old shared role.
