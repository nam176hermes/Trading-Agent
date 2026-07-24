# ADR: Job transition database authority v2

Status: Accepted for source review and later disposable-database verification
only. Runtime migration, service rollout, and live execution remain outside
this decision.

## Context

`0006_job_transition_database_authority` remains immutable. Its fixed
`job_plane` transition functions prevent direct runtime state/event mutation,
but it omitted the global default-function-EXECUTE revoke for objects later
created by `trading_owner`. Task 5 captured the exact 0006 catalog and a
rolled-back derivation of the one-statement repair. It also froze the catalog
and event-chain queries independently of any migration module.

The reviewed canonical catalog digests are:

- 0006 pre-repair: `b2dd91dbb12d585579e69b81394a530128fe84bc1dd2c7ef7683c9353eb1e4d1`
- 0007 post-repair: `1d83e9bc3f5cffe9e2dded41c33f46ce0b6d4395df84d3081d0b5132db487a40`

Package 2 disposable verification retains those production digests unchanged.
Because the catalog query deliberately includes database identity, the exact
approved test database `trading_agent_disposable_test` has separately frozen
pre/post digests derived from the same reviewed snapshots by replacing only
the database and database-scoped role-setting identity fields. Revision 0007
accepts exactly `trading_agent` or `trading_agent_disposable_test` with their
corresponding raw digest; every other database identity remains rejected.

## Decision

Add forward-only revision `0007_job_event_chain_authority`, descending exactly
from 0006. It must run only as both `current_user` and `session_user`
`trading_owner`, on PostgreSQL 16, at the sole 0006 head, with no runtime-role
sessions. It acquires writer-blocking locks for `jobs`, `job_attempts`, and
`job_events`; then verifies the reviewed pre-repair catalog digest and zero
event-chain violations.

The only persistent catalog change is the frozen byte sequence in
`acl-repair-v1.sql`:

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
```

After that statement, 0007 requires the reviewed post-repair digest, denies
direct runtime DML for job state, attempts, and events, and verifies the exact
EXECUTE matrix for the existing eight fixed functions. It creates no function
and no generic mutation API. Downgrade always raises; a future defect requires
a separately reviewed forward repair.

The migration embeds only AST-readable literal query, repair, and digest
constants. The read-only verifier parses those literals without importing or
executing the migration. `authority-manifest-v1.json` binds the final migration
hash and every frozen input hash externally, avoiding a self-referential hash
inside 0007.

`packages.job_contracts` exports the ordinary and retry transition sets used
by the frozen event-chain checks, so source policy and the SQL vocabulary are
explicitly compared by tests.

## Consequences

The authority manifest is source evidence, not evidence that 0007 ran on any
database. The reviewed Task 5 snapshots remain unchanged. A separately
reviewed, commit-bound `DISPOSABLE_PG_GREEN` record must still prove two fresh
0004-to-0007 upgrades, byte-equal post-0007 catalogs, and the exact reviewed
snapshot before any GREEN evidence document may be created.

Custom dump/restore proof also remains pending that GREEN record. It must use
two distinct disposable clusters, a custom `--create` dump, independent global
role provisioning on the target, maintenance-database
`pg_restore --create --exit-on-error`, and independent checks of global roles,
database ACLs, head, catalog digest, event chain, direct-DML denial,
append-only denial, and counts. A database dump must not be treated as a role
or credential dump.

No part of this ADR authorizes runtime PostgreSQL access, Job Plane services,
job enqueueing, SNAPSHOT execution, or external provider/broker/exchange use.
