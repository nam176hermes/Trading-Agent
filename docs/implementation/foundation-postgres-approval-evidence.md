# Foundation PostgreSQL approval evidence

Date: 2026-07-23

## Decision

The disposable PostgreSQL approval gate passed for the successor source, but
the Package 2 runtime gate did not close. This evidence is non-authorizing and
does not approve a retry, runtime PostgreSQL access, or live trading.

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
