# Job Plane Runtime PostgreSQL Recovery v2

**Evidence date:** 2026-07-16
**Status:** `BLOCKED BEFORE FIRST WRITE — APPROVAL RECORD ABSENT`

## Decision

PostgreSQL recovery was not executed. The Part 2 prose approval does not meet
the exact execution-authority contract in
`docs/production/runbooks/postgresql-preserve-recover.md`.

That runbook is explicitly a reviewed procedure, not approval. Section 4
requires one access-controlled, literal-TAB, dual-reviewed transcript with 50
exact fields, a maximum four-hour window, distinct operator/reviewer
attestations, authenticated change artifact, exact source/catalog/cluster
identities, approved independent destinations, and each explicit `ALLOW_*`
and risk-acceptance value. The launcher must receive its canonical protected
path through the `APPROVAL_RECORD` environment key.

The current prompt supplies none of those exact fields and no protected record
path. A key-name-only environment check reported `APPROVAL_RECORD=ABSENT` and
no ambient PostgreSQL/PSQL keys. No environment value was printed.

Reviewed procedure SHA-256:

`feabc083b5fe35681fde63d8fbc45ae10e56b9938d575eec604718facf9aa15c`

## Fresh read-only preflight

| Check | Observation |
|---|---|
| Target `pg_ctl status` | no server running, exit 3 |
| `127.0.0.1:55432` | closed; readiness returns no response |
| Target PostgreSQL processes | none owning the target PGDATA |
| Other host PostgreSQL processes | present under a different system account; not the target user cluster |
| Target PGDATA | real directory, runtime user/group owner, mode `0700` |
| Stale PID evidence | `postmaster.pid` remains from the interrupted shutdown |
| Job API/worker | inactive/dead, disabled, PID 0 |
| Scheduler/timer | inactive; timer disabled |
| Port 8401 | closed |
| Mode/live containment | `PAPER/PAPER`, false/false, kill switch inactive |

The prior offline classification remains `STALE_PID`: the declared PID was
dead, the control state was `in production`, the log ended after a smart
shutdown request without a clean terminal record, and a later approved start
would perform crash recovery. The exact external interruption mechanism is not
proven.

## Authorization boundary

The first parser boundary would fail at the required
`APPROVAL_RECORD` binding. If that guard were bypassed, the runbook's first
write would create its protected evidence directory; even that directory
creation is prohibited before transcript validation. Service stops and the
single original-cluster start occur later and were not reached.

Therefore no service stop, directory creation, PID/socket removal, PostgreSQL
start, recovery write, SQL connection, or cluster stop occurred. In particular,
`postmaster.pid` was not deleted or edited.

## Runtime baseline result

Because PostgreSQL remained offline, all authenticated runtime facts are
`NOT VERIFIED`:

- server version and `cluster_name`;
- runtime database identity;
- pre-migration Alembic revision;
- canonical `43,055` and quarantine `222` counts;
- Job Plane table counts;
- relation/catalog integrity, roles, grants, and ACLs.

No runtime migration was attempted. It is accurate to say this session did not
apply `0005` or `0006`; it is not accurate to assert the offline database's
logical head is `0004` without recovery and authentication.

## Required next authority

Recovery remains blocked until the exact Section 4 record is generated and
independently reviewed against the current runbook/source/catalog/cluster and
destination identities. A new prompt that merely restates general permission
does not substitute for that record.
