# Foundation PostgreSQL cleanup evidence

Date: 2026-07-26

The operator separately approved `STOP` and `DELETE`. Each initialized cluster
used exact-PGDATA `pg_ctl stop -m immediate`, status and listener checks, then
validated parent, prefix, owner, type and symlink state before root deletion.

Verified scope:

```text
roots=/tmp/phase4-postgres-p02-b42607c-01..09
ports=56520..56528
approved roots present=0
approved listeners present=0
approved PostgreSQL processes present=0
```

The manifest records the same empty post-state and `decision=PASS` under
SHA-256 `98d369cbd5bd6a5794178c1e0f90ffc92ff8d8bf7bb5cbe80991b01493f351d3`.
Port `55432` was forbidden and never targeted by the approved command path. No
operator-managed service was stopped, queried or mutated.

Only sanitized mode-`0400` evidence remains at:

```text
/home/thenam176/.hermes/audits/trading-agent/package2/b42607c-20260726T061140Z
```

```text
PASS - DISPOSABLE POSTGRESQL ROLLBACK AND CLEANUP VERIFIED
```
