# Package 2 - P0 Disposable PostgreSQL Runtime Parity

## Goal

Produce real PostgreSQL runtime proof for migration 0008, event-ledger snapshot/retry/retention behavior, inbox permanence and dual-read parity without touching the operator-managed PostgreSQL cluster.

## Why this is P0

The source-level database implementation is strong, but the assessment explicitly records runtime parity as `PENDING_APPROVAL`. Source tests cannot prove PostgreSQL catalog authority, transaction semantics, restore behavior or real SQL parity.

## Hard boundary

This package must use a disposable PostgreSQL 16 cluster with:

```text
PGDATA under /tmp or another approved disposable path
localhost-only bind
OS-assigned or explicitly approved non-runtime port
database name with a disposable/test suffix
runtime PostgreSQL host/port/PGDATA rejected
```

Forbidden:

```text
127.0.0.1:55432
operator-managed trading_agent database
runtime credentials
runtime PGDATA
```

An exact disposable-test approval record is required before starting the cluster. The approval record validates scope but does not replace the command-specific Greenlight Gate.

Before cluster start, restore, stop or PGDATA deletion, Hermes must present:

```text
exact command
all paths, ports and processes affected
rollback or cleanup command
approval-record digest and expiration
proof that runtime port 55432 and operator PGDATA are excluded
```

No command runs until Nam explicitly approves it. Use foreground or tracked bounded child processes only. Do not use systemd, schedulers or persistent services.

## In scope

- Validate disposable approval record.
- Start isolated PostgreSQL 16.
- Apply migrations from empty to current head, including 0008.
- Verify catalog, ACL, role, trigger, function and event-ledger authority.
- Execute event-ledger runtime targets.
- Execute runtime PostgreSQL and dual-read targets.
- Test dump/restore parity.
- Confirm cleanup leaves no listener or PostgreSQL PID.
- Update closure evidence only after tests pass.

## Out of scope

- Runtime DB recovery.
- Production migration.
- Service rollout.
- Scheduler enablement.
- Real research data writes.
- Live trading.

## Required approval record

The record must bind:

```text
source commit
source tree
test operations
test paths
SQL/migration hashes
PGDATA prefix
bind host
port policy
forbidden ports
cluster name
expiration
operator/reviewer identities
canonical record digest
```

Expired or mismatched records fail closed.

## Workstream A - Database identity and migration proof

Run:

```text
empty → head
prior supported revision → 0008
```

Verify:

- Alembic head.
- All expected tables and constraints.
- Fixed-precision behavior.
- Snapshot hashes.
- Outbox/inbox schema.
- Retention state.
- Event set immutability.
- No unexpected objects.

## Workstream B - Authority and ACL proof

Exhaustively inspect:

- schemas;
- tables;
- sequences;
- functions by full signature;
- triggers;
- policies/RLS;
- ownership;
- explicit/default ACLs;
- PUBLIC privileges;
- role membership.

Use effective ACL semantics for restore portability while retaining raw ACL digests as informational evidence.

Semantic digest groups:

```text
identity
owner
effective ACL
structural security metadata
functions
triggers and policies
```

Any owner, effective ACL, structural or identity drift is a blocker.

## Workstream C - Event-ledger runtime

Run the canonical target:

```bash
make test-event-ledger-runtime-postgres
```

Required behavior includes:

- initial event chain correctness;
- append-only events;
- retry and idempotency;
- event snapshot permanence;
- inbox permanence;
- retention behavior;
- state/event atomicity;
- no direct role bypass;
- replay determinism.

## Workstream D - Runtime repository parity

Run:

```bash
make test-runtime-postgres
make test-runtime-dual-read
```

Compare legacy/canonical and PostgreSQL results after removing nondeterministic envelope fields.

Classify differences:

```text
EXPECTED_NORMALIZATION
LEGACY_INVALID_RECORD
MIGRATION_BUG
QUERY_ORDERING_BUG
CONTRACT_BUG
```

Only the first two are potentially acceptable with explicit counts and evidence.

## Workstream E - Restore proof

Create a custom-format dump from disposable head and restore to another disposable database.

Verify:

- migration head;
- row counts;
- semantic catalog digests;
- ACL/owner/function/trigger parity;
- event-chain validity;
- role denials;
- no unexpected object.

Do not require byte-identical raw ACL representation where effective semantics are identical.

## Workstream F - Cleanup proof

After tests and under a separate command-specific Greenlight:

- stop the exact disposable cluster;
- delete only the approved disposable PGDATA path;
- verify no listener;
- verify no PostgreSQL PID;
- verify runtime port 55432 was never touched;
- preserve only sanitized evidence.

## Acceptance

All must exit 0:

```bash
make test-event-ledger-runtime-postgres
make test-runtime-postgres
make test-runtime-dual-read
```

Additional acceptance:

- disposable approval validator passes;
- runtime target identifiers are rejected;
- migration 0008 runtime behavior is proven;
- restore semantic catalog parity passes;
- dual-read has no unexplained difference;
- cleanup is complete;
- start, stop and deletion commands have separate Greenlight evidence.

## Stop conditions

Stop if:

- approval expires or mismatches;
- script can target runtime PostgreSQL;
- effective ACL, owner, structure or identity drifts;
- event chain is invalid;
- source or runtime data is written outside the disposable fixture;
- cleanup leaves a process/listener.

## Deliverables

```text
docs/implementation/foundation-postgres-approval-evidence.md
docs/implementation/foundation-postgres-disposable-setup.md
docs/implementation/foundation-postgres-migration-0008.md
docs/implementation/foundation-event-ledger-runtime.md
docs/implementation/foundation-postgres-dual-read.md
docs/implementation/foundation-postgres-restore-proof.md
docs/implementation/foundation-postgres-cleanup.md
```

## Final decision

```text
GO - POSTGRESQL RUNTIME PARITY CLOSED
```

or:

```text
NO-GO - POSTGRESQL RUNTIME PARITY NOT PROVEN
```
