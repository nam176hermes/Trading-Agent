# Foundation PostgreSQL migration 0008 runtime evidence

Date: 2026-07-23

## Initial fail-closed run

The first authorized event-ledger run stopped at the 0007 catalog gate because
the historical frozen digest included database identity `trading_agent`, while
Package 2 requires `trading_agent_disposable_test`. Cleanup completed before
source repair.

Repair lineage:

- `94d12fc354a63427a8919e25d26dfe03fd10d732`: approval isolation fixes
- `48c2999ba386db51c5a0f22809dc9bc3d5653cc1`: exact disposable catalog
  identity binding

The repair retained the production database name and production 0006/0007
digests unchanged. It added distinct raw digests for the exact disposable
database and rejects every other database name or digest.

## Successor runtime result

`make test-event-ledger-runtime-postgres` exited `0`. Fresh PostgreSQL output
showed transactional upgrades:

```text
0001 -> 0002 -> 0003 -> 0004 -> 0005 -> 0006 -> 0007 -> 0008
5 passed
```

The runtime PostgreSQL suite also exercised empty-to-head, 0007-to-0008,
head-cycle and application-role permission paths before reaching the separate
restore blocker. Migration 0008 itself is runtime-proven on the approved
disposable PostgreSQL 16 fixture; Package 2 remains open because restore
semantic parity failed.
