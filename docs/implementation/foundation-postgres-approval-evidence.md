# Foundation PostgreSQL approval evidence

Date: 2026-07-23

## Decision

The first successor approval passed, but its restore-parity attempt did not
close Package 2. The original sections below preserve that historical
checkpoint. A later exact authority and successful runtime proof supersede the
package status without authorizing production PostgreSQL access or live
trading.

## Successor authority

- Source commit: `48c2999ba386db51c5a0f22809dc9bc3d5653cc1`
- Source tree: `31d47719fbf079f86bfdd8dd96d58ed4e44f4731`
- Operator: `thenamnguyen`
- Independent reviewer: `hermes.agent`
- Reviewer result: `APPROVED`, no source blockers
- Approval record digest:
  `722d8cd458b44c5fbb2b19ba1fa3b203b43fdafba064c91f09f21857b989a28d`
- Fixture-plan digest:
  `f086c51dfef80f8b38410f972dfb9e63db50ffe0491b18418ef4e588613c7a70`
- Validity: `2026-07-23T18:51:26Z` through
  `2026-07-23T22:51:26Z`
- Protected external record:
  `/tmp/trading-agent-p02-approval-48c2999/disposable-postgres-approval.json`
- Protected external plan:
  `/tmp/trading-agent-p02-approval-48c2999/disposable-postgres-fixture-plan.json`
- Both protected files were regular, same-user files with mode `0600`.

The approval validator returned:

```text
VALID: disposable PostgreSQL authority record matches
```

The fixture validator returned nine exact slots and matched the successor
commit, tree, approval digest, validity window and Greenlight.

## Approved boundary

- Bind: `127.0.0.1`
- Ports: `56420` through `56428`
- PGDATA roots: `/tmp/phase4-postgres-p02-48c2999-01/data` through
  `/tmp/phase4-postgres-p02-48c2999-09/data`
- Database: `trading_agent_disposable_test`
- Lifecycle: `INITDB`, `START`, `RESTORE`, `STOP`, `DELETE`
- Forbidden ports: `3002`, `8401`, `55432`
- Runtime database settings: rejected if present

The approval and fixture plan are tied to the source above. They do not
authorize any later source commit.

## Superseding final authority and proof

- Final source commit: `dd1463a80b5a492d6f12b89f9aa69f03ce77416b`
- Final source tree: `d8b1983fef30ef97439aa94cd7db2820a66353b6`
- Record ID: `DISPOSABLE_POSTGRES_TEST_20260724_P02_DD1463A`
- Approval record digest:
  `d5de9aba700505127dab11eca1979bb16a760b3a97711bc11843f76cc86e74e8`
- Fixture-plan digest:
  `1ba8974706d6cbe642769144a136f8ff4a9d10d8a442d6f143582a8e51bc7482`
- Operator: `thenamnguyen`
- Reviewer: `hermes.agent`
- Approved lifecycle: `INITDB`, `START`, `RESTORE`, `STOP`, `DELETE`
- Bound interface: `127.0.0.1`, ports `56420` through `56428`
- Forbidden ports: `3002`, `8401`, `55432`
- Runtime settings: rejected if present
- Current-tree binding audit: all ten source-bound files are byte-identical

Final exact-authority commands returned:

```text
make test-event-ledger-runtime-postgres: 5 passed
make test-runtime-postgres: 8 passed
make test-runtime-dual-read: 1 passed
```

The retained sanitized semantic evidence records
`semantic_groups_equal=true`, `row_counts_equal=true`, and no differing
semantic subgroup. Final cleanup found zero approved roots, zero approved
PostgreSQL processes, and zero listeners on the approved ports.

```text
GO - POSTGRESQL RUNTIME PARITY CLOSED
```

This authority was source-bound and expired after the run. It cannot authorize
a retry, a different commit, the operator-managed database, or any production
mutation.
