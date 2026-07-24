# Foundation event-ledger PostgreSQL runtime evidence

Date: 2026-07-23

Command:

```text
make test-event-ledger-runtime-postgres
```

Result: exit `0`, `5 passed in 1.90s`.

The fresh disposable PostgreSQL run covered:

- canonical event and snapshot golden vectors;
- rejection of forged recomputed snapshots;
- retry/idempotency behavior;
- publication retention and permanent inbox claims;
- non-owner role denial;
- atomic event/state failure behavior; and
- deterministic replay ordering.

The cluster used slot 9 on port `56428`. After the command, the exact PGDATA
root, listener and PostgreSQL PID were all absent.
