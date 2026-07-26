# Foundation PostgreSQL migration 0008 runtime evidence

Date: 2026-07-26

```text
make test-event-ledger-runtime-postgres
exit=0
5 passed in 2.21s
```

PostgreSQL emitted the complete transactional path:

```text
0001 -> 0002 -> 0003 -> 0004 -> 0005 -> 0006 -> 0007 -> 0008
```

The broader runtime target also exercised empty-to-head, `0007` to `0008`, head
cycle and application-role permission paths. Its runner rejects skips.

Migration head `0008_trading_domain_ledger` was verified for canonical event and
snapshot persistence, append-only authority, idempotency, outbox retention,
permanent inbox claims, atomic failures, role denials and deterministic replay.
The archived manifest binds source identity, migration hashes, exact argv,
output digest and exit code.

```text
PASS - MIGRATION 0008 POSTGRESQL RUNTIME BEHAVIOR PROVEN
```

Earlier fail-closed and transcript-incomplete checkpoints remain in Git history.
