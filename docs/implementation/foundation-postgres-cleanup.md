# Foundation PostgreSQL cleanup evidence

Date: 2026-07-23

Cleanup was checked after the initial failed run, the successful event-ledger
run and the successor restore-parity failure.

Final checks returned:

```text
CLEANUP_PASS_ALL_SUCCESSOR_ROOTS_ABSENT
CLEANUP_PASS_ALL_SUCCESSOR_LISTENERS_ABSENT
CLEANUP_PASS_ALL_SUCCESSOR_POSTGRES_PIDS_ABSENT
```

Verified targets:

- roots `/tmp/phase4-postgres-p02-48c2999-01` through `-09`;
- listeners on ports `56420` through `56428`; and
- PostgreSQL processes containing the exact successor root prefix.

The operator-managed port `55432` was never used or probed. Only protected
approval/plan records and sanitized documentation evidence were retained.

## Later-run cleanup observations

The later exact-authority run at
`dd1463a80b5a492d6f12b89f9aa69f03ce77416b` used roots
`/tmp/phase4-postgres-p02-dd1463a-1` through `-9` and ports `56420` through
`56428`. Historical controller output and a separate 2026-07-25 host inspection
reported:

```text
approved roots present: 0
approved PostgreSQL processes: 0
approved listeners: 0
```

The retained mode-`0600` files are:

- `/tmp/trading-agent-p02-approval-dd1463a/disposable-postgres-approval.json`;
- `/tmp/trading-agent-p02-approval-dd1463a/disposable-postgres-fixture-plan.json`;
- `/tmp/foundation-postgres-evidence-dd1463a/catalog-restore-semantic-evidence.json`.

Their hashes are recorded in `foundation-postgres-approval-evidence.md`. The
absence observations show no disposable residue at inspection time, but do not
replace a bound lifecycle transcript. The operator-managed port `55432`
remained outside the disposable boundary and was not probed.
