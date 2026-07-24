# Job Plane PostgreSQL Recovery Evidence

**Date:** 2026-07-16
**Status:** `DIAGNOSED_READ_ONLY — RECOVERY NOT EXECUTED`
**Primary classification:** `STALE_PID`
**Required start behavior:** `CRASH_RECOVERY_REQUIRED`

## Scope and safety boundary

This record captures read-only diagnostics for the user-owned PostgreSQL 16
cluster expected at `127.0.0.1:55432`. It is not an execution approval and is
not evidence that PostgreSQL recovered successfully.

During this gate:

- PostgreSQL was not started, stopped, restarted, reconfigured, repaired, or
  migrated.
- No database connection authenticated, no SQL ran, and no database file was
  edited or deleted.
- No application service or timer was changed.
- No credential file was opened and no password, environment value, or DSN was
  read or printed.
- No Job API, worker, scheduler, job, SNAPSHOT, broker, exchange, or provider
  action ran.

## Read-only evidence

| Check | Observed evidence | Assessment |
|---|---|---|
| PostgreSQL binary | `postgres (PostgreSQL) 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)` | Binary version verified; running-server version is not verified |
| Data directory | `/home/thenam176/.local/share/trading-agent/postgres/16/trading-agent`; canonical directory; not a symlink; owner `thenam176`; mode `0700`; `PG_VERSION=16` | Present and readable |
| Internal symlinks | No symlink found beneath PGDATA on its filesystem | Pass for this read-only check |
| Static configuration | PostgreSQL parsed the exact PGDATA, port `55432`, `listen_addresses=127.0.0.1`, expected Unix socket directory, and cluster name `trading-agent` | No static config parsing error observed |
| `postmaster.pid` | Regular file, owner `thenam176`, mode `0600`; declared PID `143967`; declared PGDATA/port/socket/listen address match; line 8 is `stopping` | Stale shutdown state |
| Declared PID | `kill -0 143967` failed; PID is not live | Confirms stale PID rather than a bound live postmaster |
| `pg_ctl status` | `pg_ctl: no server running`; exit `3` | Cluster offline |
| TCP readiness | `127.0.0.1:55432 - no response`; exit `2` | Cluster unavailable |
| Unix readiness | Expected socket directory at port `55432` returned `no response`; exit `2` | Cluster unavailable |
| TCP listener | No listener on port `55432` | No TCP port conflict observed |
| Unix listener | No active Unix listener for `.s.PGSQL.55432` | No live Unix listener observed |
| Stale socket objects | `.s.PGSQL.55432` and `.s.PGSQL.55432.lock` remain in the protected socket directory | Consistent with interrupted shutdown; they were not removed |
| Control state | `Database cluster state: in production` | Not a cleanly stopped cluster |
| Latest checkpoint | Checkpoint time `Tue Jul 14 01:16:25 2026`; `pg_control` modified `01:16:28`; location `A/38CEC68`; REDO `A/38CEC30`; timeline `1` | Offline control metadata is readable |
| Referenced REDO segment | `000000010000000A00000003` exists, owner `thenam176`, mode `0600`, size `16,777,216` bytes | Presence verified; WAL correctness is not proven |
| Data page checksums | Version `0` | Checksums disabled, as previously documented |
| Lifecycle log | One `READY` event at `2026-07-11 11:15:34.744`; one smart-shutdown request at `2026-07-14 12:09:08.795`; zero clean-shutdown, checkpoint, or PANIC events after that request | Shutdown did not reach a recorded clean terminal state |
| Host boot | Current host boot time `2026-07-15T14:48:08Z` | Later host restart is consistent with external interruption, but does not prove its exact cause |
| Permissions | PGDATA `0700`; PostgreSQL config/HBA/control/WAL files and directories owned by `thenam176` with protected modes; socket and log directories `0700`; log file `0600` | No permission blocker observed |
| Capacity | Filesystem approximately `1007 GiB`, `176 GiB` used, `780 GiB` available; approximately 5% of inodes used | No immediate local-capacity blocker observed; runbook still requires independent preservation/backup capacity |
| Ambient PostgreSQL variables | No exported environment key beginning `PG` or `PSQL` was present during diagnostics | Diagnostics were not redirected by ambient libpq configuration |

The timestamps emitted by PostgreSQL control/log tooling above retain their
observed formatting. The host boot timestamp is explicitly UTC.

## Classification

The primary incident classification is `STALE_PID` because all of the
following hold simultaneously:

1. `postmaster.pid` exists and declares state `stopping`.
2. Its PID is dead.
3. `pg_ctl status` reports no server.
4. Neither TCP nor Unix socket has a listener.
5. Stale socket and lock objects remain.

This is not `STOPPED_CLEANLY`: `pg_control` remains `in production`, and the
log has no clean-shutdown record after the smart-shutdown request. Therefore an
approved start must be treated as `CRASH_RECOVERY_REQUIRED`, not as an ordinary
clean start.

No evidence currently supports `PORT_CONFLICT`, `CONFIG_ERROR`,
`PERMISSION_ERROR`, or `DATA_DIRECTORY_MISSING`. No PANIC, invalid-checkpoint,
or missing-checkpoint event was found in the existing log, and the referenced
REDO segment exists. This does **not** prove WAL integrity; `WAL_ERROR` can only
be ruled out through the single, evidence-captured recovery attempt authorized
by the reviewed runbook.

## Root-cause hypothesis

**Confidence: medium-high.** PostgreSQL received a smart-shutdown request, but
the postmaster was externally terminated or lost with its host/WSL/session
before shutdown completed. That sequence explains the dead PID, `stopping`
marker, stale socket objects, absent clean-shutdown record, and control state
`in production`.

The diagnostics do not identify whether the interruption was a host shutdown,
WSL termination, process kill, or another external event. Treating any one of
those mechanisms as proven would exceed the evidence.

## Approval and runbook blockers

The reviewed procedure is
`docs/production/runbooks/postgresql-preserve-recover.md`, with current and
previously frozen SHA-256:

`feabc083b5fe35681fde63d8fbc45ae10e56b9938d575eec604718facf9aa15c`

The file states `REVIEWED PROCEDURE — NOT EXECUTION APPROVAL`. It is currently
untracked and absent from source HEAD `9641281`; a future execution transcript
must bind an approved clean source commit/tree and the exact reviewed runbook
hash.

No usable execution approval was available in this diagnostic session:

- the `APPROVAL_RECORD` environment key was absent;
- no canonical approval-record path was supplied;
- the operator prompt did not contain the required exact, time-bounded,
  dual-reviewed transcript fields; and
- protected approval or credential files were intentionally not opened or
  searched.

This establishes that the current session has no recovery/start authority. It
does not assert that no approval artifact exists elsewhere in protected
storage.

## Explicitly not executed or verified

The following remain `NOT EXECUTED` or `NOT VERIFIED`:

- cold preservation of PGDATA and the complete PostgreSQL log directory;
- PostgreSQL recovery/start or stop;
- authenticated server identity and PostgreSQL runtime version;
- existence and identity of database `trading_agent`;
- Alembic head `0004_durable_research_jobs`;
- canonical count `43,055` and quarantine count `222`;
- all job-plane table counts;
- constraints, indexes, triggers, functions, ownership, memberships, and ACLs;
- application-role access;
- relation/catalog integrity and WAL recovery correctness;
- logical backup, dump hash/catalog, disposable restore, or restore parity;
- migration `0005` on the runtime database.

## Safe next approval gate

Before any write or start, a distinct operator and reviewer must provide the
exact transcript required by Section 4 of the frozen runbook. It must bind the
approved runbook SHA, clean source commit/tree, system identity, expected
catalog evidence, independent preservation/backup destinations, isolated
restore target, execution window, and the one-start/one-stop permissions.

Only after that transcript validates may the operator execute the runbook in
order: assert all application units inactive, preserve and hash PGDATA plus the
complete log directory on independent storage, recheck identity/capacity, and
perform exactly one fail-closed maintenance start with complete recovery-log
capture. No application service start is included in this gate.

Stop immediately on identity drift, copy/hash failure, insufficient independent
capacity, unexpected listener, recovery error, PANIC, WAL/checkpoint error,
head/count/catalog/ACL mismatch, or approval expiry.

## Prohibitions

- Do not manually delete, rename, truncate, or edit `postmaster.pid`, the Unix
  socket, or its lock file.
- Do not use `pg_resetwal`, `initdb`, reinitialize PGDATA, edit control/WAL
  files, or restore over the original cluster.
- Do not retry a failed recovery start without a new reviewed procedure and
  approval.
- Do not migrate the runtime database to `0005` in this recovery step.
- Do not start Job API, worker, scheduler, timer, or any trading/research job.
- Do not print or place credentials, passwords, environment values, or DSNs in
  commands, logs, or evidence.

## Read-only commands used

The diagnostic command set was limited to `stat`, `realpath`, `namei`, `find`,
`df`, `postgres --version`, selected safe `postgres -D ... -C <setting>`
lookups, `pg_controldata`, `pg_ctl status`, `pg_isready`, `ss`, a process table
without argument/environment values, filtered lifecycle-log summaries,
`uptime -s`, `sha256sum`, and read-only Git identity/status checks.
