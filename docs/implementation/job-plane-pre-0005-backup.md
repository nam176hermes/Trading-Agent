# Job Plane Pre-0005 Backup Gate

**Date:** 2026-07-16
**Status:** `BLOCKED — BACKUP AND RESTORE NOT EXECUTED`
**Runtime migration state:** `NOT VERIFIED`; no `0005` apply occurred

## Purpose

This document defines the evidence required before migration `0005` may be
considered for the runtime PostgreSQL database. It records the current blocked
state; it is not a backup transcript, restore transcript, approval, or claim of
recoverability.

## Current gate state

The runtime cluster is offline with primary classification `STALE_PID` and
would require an approved crash-recovery start. Because no exact dual-reviewed
execution transcript was bound to this session, PostgreSQL was not started and
no authenticated database check ran.

| Required baseline | Current state |
|---|---|
| PostgreSQL accepting only on `127.0.0.1:55432` | `NOT VERIFIED`; currently no listener |
| Running PostgreSQL major `16` | `NOT VERIFIED`; offline binary is 16.14 |
| Database `trading_agent` exists | `NOT VERIFIED` |
| Alembic revision is exactly `0004_durable_research_jobs` | `NOT VERIFIED` |
| Canonical rows equal `43,055` | `NOT VERIFIED` |
| Quarantine rows equal `222` | `NOT VERIFIED` |
| Job-plane table counts captured and accepted | `NOT VERIFIED` |
| Integrity, catalog, ownership, role, and ACL gates pass | `NOT VERIFIED` |
| Custom-format pre-0005 dump exists with mode `0600` | `NOT CREATED` |
| Dump catalog is readable and hashed | `NOT EXECUTED` |
| Restore to a distinct disposable database passes | `NOT EXECUTED` |
| Restored head/counts/job tables equal source | `NOT EXECUTED` |

The recovery runbook documents an older fallback dump at revision
`0003_contract_lineage_repair`. Its documented path, size, hash, and historical
restore evidence were read from the runbook only; the dump itself was not
opened or revalidated in this diagnostic session. It is not a substitute for a
fresh, recovered pre-`0005` backup.

## Approval dependency

Backup and restore verification must occur only inside an exact, current,
dual-reviewed runbook transcript that explicitly authorizes:

- the single original-cluster recovery start and controlled stop;
- read-only runtime verification;
- immediate logical backup;
- a unique isolated restore target;
- protected credential handling without printing values; and
- the exact evidence, backup, preservation, and isolated-storage locations.

The reviewed recovery runbook SHA-256 is:

`feabc083b5fe35681fde63d8fbc45ae10e56b9938d575eec604718facf9aa15c`

That procedure is not execution approval. The current session had no
`APPROVAL_RECORD` binding and did not satisfy this dependency.

## Preconditions for a future backup attempt

All of these conditions must pass before `pg_dump`:

1. The exact approval transcript validates and remains inside its time window.
2. PGDATA and the complete pre-start log directory have been preserved,
   hashed, metadata-compared, and durably synced to independent storage.
3. The single approved recovery start succeeds without PANIC, WAL, checkpoint,
   control-file, timeline, or identity error.
4. PostgreSQL is bound only to `127.0.0.1:55432` and its expected protected
   Unix socket.
5. Server major is `16`, cluster identity matches the approval, database
   `trading_agent` exists, and recovery is no longer in progress.
6. Alembic has exactly one head and it is
   `0004_durable_research_jobs`. A head of `0003`, `0005`, multiple heads, or
   an unknown value is a stop condition for this gate.
7. Canonical count is exactly `43,055`, quarantine count exactly `222`, and all
   job-plane table counts are captured before backup.
8. Full relation/catalog integrity, constraints, indexes, triggers, functions,
   ownership, memberships, application-role access, and ACL checks match the
   approved baseline.
9. Backup and isolated-restore destinations are canonical, private, empty,
   nonsymlinked, on approved storage, and have sufficient capacity.
10. No Job API, worker, scheduler, timer, or job process is active.

## Future backup evidence procedure

Once every prerequisite passes under approval, the operator may create one
unique PostgreSQL custom-format dump outside Git and outside the repository.
The destination must be created under a protected backup parent with umask
`077`, final file mode `0600`, no pre-existing or partial-name collision, and
no password or DSN on the command line or in captured evidence.

Required evidence, without secret values:

- source cluster/database/system identity and exact revision;
- pre-backup canonical, quarantine, and job-plane counts;
- dump tool and server major versions;
- unique dump basename, owner, mode, byte size, SHA-256, and durable sync exit;
- readable `pg_restore --list` catalog and its hash;
- command exit codes and a secret-scan result for any captured stderr;
- explicit statement that runtime revision remained `0004` throughout.

The dump must never be committed, copied into the release candidate, or
treated as valid merely because its command exited zero.

## Future isolated restore verification

Restore must target a unique, preapproved disposable PostgreSQL 16 database and
must never overwrite or share identity with the original cluster/database.
Using protected admin access, the approved procedure must:

1. create or bind the unique isolated target;
2. restore the custom-format dump in the reviewed single-transaction mode;
3. verify exactly one Alembic head at `0004_durable_research_jobs`;
4. verify canonical count `43,055` and quarantine count `222`;
5. compare every captured job-plane table count with the source;
6. repeat the approved catalog, constraints, indexes, triggers, functions,
   owner, role, ACL, orphan, and integrity checks;
7. prove source and restored data/catalog evidence match; and
8. remove the temporary database only after every check passes and the evidence
   is durably retained.

If restore or comparison fails, retain the isolated target, `.partial` dump,
and protected diagnostics for independent review. Do not reuse names, delete
evidence, retry automatically, or alter the source database.

## 0005 boundary

Migration `0005` must first pass its separate disposable migration and DB-role
permission matrix. A successful backup/restore drill does not authorize its
runtime application.

This gate explicitly performed no:

- runtime migration;
- role creation, grant, revoke, DDL, DML, or SQL query;
- dump, restore, temporary database creation, or temporary database removal;
- application service/timer change; or
- job insertion or SNAPSHOT execution.

The runtime database therefore remains physically untouched by this session;
its logical revision remains `NOT VERIFIED`, not asserted as `0004`.

## Stop conditions

Stop without corrective SQL or retry if any of the following occurs:

- recovery/start does not complete safely;
- cluster/database identity or endpoint differs from the approved target;
- runtime head is not exactly `0004_durable_research_jobs`;
- canonical, quarantine, or job-plane counts differ from the approved baseline;
- any integrity, catalog, relation, owner, role, membership, ACL, trigger,
  function, constraint, or index check fails;
- dump creation, mode, hash, catalog readability, sync, or secret scan fails;
- isolated restore or source/restore parity fails;
- an application service starts, port `8401` opens, or any job row is inserted;
  or
- approval expires or any protected path/identity becomes ambiguous.

## Rollback boundary

Rollback means stop and preserve evidence, never rewind the runtime database.
Do not restore over original PGDATA, delete job/audit evidence, edit
`alembic_version`, downgrade schema, or use WAL surgery. No rollback action was
required or executed in this session because no backup, restore, or migration
was attempted.
