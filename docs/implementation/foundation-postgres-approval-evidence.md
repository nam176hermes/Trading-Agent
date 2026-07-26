# Foundation PostgreSQL approval evidence

Date: 2026-07-26

## Decision

```text
GO - POSTGRESQL RUNTIME PARITY CLOSED
```

This decision covers only the approved disposable PostgreSQL 16 run. It grants
no production PostgreSQL, service, deployment or live-trading authority.

## Source and authority

- Commit: `b42607cb8c21d7a7b5ffeb854f08d62e9d15ff2f`
- Tree: `017481771e33a09ad6fdde26a61dacea88b1faad`
- PostgreSQL: `16.14`
- Record ID: `DISPOSABLE_POSTGRES_TEST_20260726_P02_B42607C`
- Operator and reviewer: `thenamnguyen`, `hermes.agent`
- Validity: `2026-07-26T06:02:03Z` to `2026-07-26T14:02:03Z`
- Approval canonical SHA-256:
  `e1d6b687450a8303ccd259a9bb5ce2b8b40e5fe739fff1c0c36f8454bc4dc06e`
- Approval file SHA-256:
  `fe7b9f3cde0382725ebddd08111a6bbde476786c9e4ac33c2a6bfa7fdcca1147`
- Fixture-plan canonical SHA-256:
  `6979b9b2be20d06ff0dcef3b88e7a597bf807669ec2e50581fe2f67d87ce0adb`
- Fixture-plan file SHA-256:
  `9e66e25e0b5700480d14e35ac572c1eb27273aec567f7937cdc06a0f3b514eb2`
- Greenlight SHA-256:
  `c4c2e0dd46b6c68837e35df729edfcb3a6dfb31a8c5bda2b8c3434f57d0955aa`

The validator returned `VALID`. Greenlight separately approved `START`,
`RESTORE`, `STOP` and `DELETE`; the validated lifecycle also included `INITDB`.

## Boundary

```text
bind=127.0.0.1
ports=56520..56528
roots=/tmp/phase4-postgres-p02-b42607c-01..09
database=trading_agent_disposable_test
mode=paper
live_gates=false
kill_switch=INACTIVE
forbidden_ports=3002,8401,55432
```

No runtime database variable was inherited. The worktree was clean before and
after the run.

## Durable evidence

Archive:

```text
/home/thenam176/.hermes/audits/trading-agent/package2/b42607c-20260726T061140Z
```

- Archive index: `8da94297942eeea47297004cb05bb7fc96a6d7f1e94d99ae604250f4330e848e`
- Transcript: `e9448aa32d4f620e7f59fe3f4bb7a5cba42ff40dadcdbff6ab7d7506f410128c`
- Manifest: `98d369cbd5bd6a5794178c1e0f90ffc92ff8d8bf7bb5cbe80991b01493f351d3`
- Restore evidence: `22dd5c215741036a3a4b1db97790f6bc0a6fcfaefe581ace01c7e227399283db`
- Runner: `22a7368503448aab266910a7d0ad9b95e9b47253d10a98fc1584b656e99de9ff`

Archived files are regular, non-symlink, same-user and mode `0400`. The manifest
binds exact argv, UTC times, exit codes, timeout flags, output digests, authority
and cleanup state.

## Results

```text
make test-event-ledger-runtime-postgres: exit 0, 5 passed in 2.21s
make test-runtime-postgres:              exit 0, 8 passed in 12.88s
make test-runtime-dual-read:             exit 0, 1 passed in 2.10s
```

No selected test skipped. Restore evidence records equal semantic groups and row
counts, with no differing subgroup or row difference. Fresh reconciliation found
zero approved roots, listeners and PostgreSQL processes.

Earlier partial runs lacked a protected complete transcript. They remain audit
history and do not control this decision.
