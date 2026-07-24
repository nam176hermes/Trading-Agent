# Job Plane Role Split Evidence

**Evidence date:** 2026-07-16
**Status:** `DISPOSABLE VERIFIED — RUNTIME NOT APPLIED`

## Boundary

This record distinguishes source-level `0005` role separation, forward `0006`
transition authority, disposable PostgreSQL verification, and the untouched
runtime database. It contains no credentials or DSN.

## Source authority

Migration `0005_job_plane_role_split` is frozen with parent
`0004_durable_research_jobs`. It expects separately provisioned identities:

- `trading_job_api`;
- `trading_job_worker`;
- `trading_job_scheduler`.

The shared `trading_jobs` identity becomes `NOLOGIN` and is not an allowed
runtime connection. The provisioner creates distinct login roles without
superuser, create-role, create-database, replication, inheritance, or RLS
bypass authority. Its test harness uses only test-only credentials in an
isolated `/tmp` PostgreSQL 16 cluster.

The runtime database was not started or authenticated in this session.
Therefore runtime role existence, head, ACLs, counts, and migration state are
`NOT VERIFIED`; `0005` was not applied to it.

## TDD evidence before `0006`

The focused transition-authority RED run used only the repository-provided
disposable PostgreSQL 16 harness. It produced five expected failures:

1. API direct cancellation could commit without a matching event.
2. Worker direct state mutation could commit without a matching event.
3. Direct mutation ACLs remained present.
4. The protected `job_plane` schema/functions were absent.
5. Alembic head was `0005_job_plane_role_split`, not the requested forward
   authority revision.

The disposable postmaster stopped and its temporary directory was removed by
the harness. No listener or `phase4-postgres-*` directory remained afterward.
No runtime PGDATA, systemd unit, application service, provider, or job row was
touched.

## Required disposable matrix

The final GREEN transcript must prove all of the following on a fresh
PostgreSQL 16 disposable cluster:

| Identity | Permitted | Required denial |
|---|---|---|
| Job API | fixed operator SNAPSHOT enqueue/cancel and read projections | claim, start, finalize, recovery, scheduler namespace, direct state/attempt/event DML, DDL |
| Worker | fixed claim/start/control/finalize/recovery plus narrowly retained heartbeat/artifact operations | enqueue, cancel, scheduler mutation, direct state/attempt/event DML, DDL |
| Scheduler | fixed scheduled SNAPSHOT enqueue plus scheduler heartbeat | operator namespace, cancel, claim/start/finalize/recovery, direct state/attempt/event DML, DDL |

It must additionally prove:

- no role membership or shared-login fallback;
- no role can delete, truncate, alter, create, or own application objects;
- append-only event protection remains enabled;
- row policies and column/table ACLs exactly match the reviewed catalog;
- API/worker/scheduler use their own direct login identities;
- custom-format schema restore into pre-provisioned exact roles preserves
  function owner, ACL, RLS, direct-DML denial, and event invariants.

## Disposable result

Fresh post-`0006` verification passed on repository-provided disposable
PostgreSQL 16 clusters:

| Gate | Result |
|---|---|
| Role/authority/NULL-fuzz/custom-restore group | `99 passed` |
| Repository enqueue/query/transaction group | `39 passed` |
| Cancellation/static fixed-capability group | `8 passed` |
| Worker claim/lease integration group | `13 passed` |
| Disposable PostgreSQL process after teardown | zero |
| Disposable `phase4-postgres-*` data directories after teardown | zero |

The 99-test gate proved exact direct login identities, cross-role denials, no
DDL authority, direct state/attempt/event DML denial, append-only events,
function-owner/signature/result/ACL postflight, fail-closed recovery evidence,
and restoration of those controls from a custom-format dump into a target with
the exact roles pre-provisioned. The disposable dump was created under umask
`077` with file mode `0600`; it and its temporary database were test artifacts
only and were removed after successful verification.

The full jobs/Alembic suite subsequently reached `780 passed, 3 failed`. The
three failures are canonical-dashboard AJV dependency-resolution failures,
not PostgreSQL migration or role-matrix failures. They still block the clean
candidate gate, so this document does not promote the source to a release.

Migration `0005` remains byte-for-byte frozen at SHA-256
`7b77d9abe0b5cfe84bf69ea60e47441179c99bcb533a6776f629cab103698f4e`.
Forward migration `0006` is uncommitted at SHA-256
`f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd`.

## Runtime and rollback

Runtime remains outside this record. It must stay at its recovered and
approved `0004` baseline until a fresh backup/restore gate, immutable installed
release, and exact `0005` approval exist. Because the runtime database is
offline, its current revision is `NOT VERIFIED`, not asserted.

On any disposable failure, keep application services inactive, preserve the
source/test evidence, repair only the candidate branch, and rerun against a
new disposable cluster. Never downgrade or edit runtime `alembic_version`,
delete job events, or reuse the shared database role.
