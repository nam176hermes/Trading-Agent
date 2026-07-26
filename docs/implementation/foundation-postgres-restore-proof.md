# Foundation PostgreSQL restore proof

Date: 2026-07-26

```text
make test-runtime-postgres
exit=0
8 passed in 12.88s
output_sha256=4be9443ccb743014f5bdd6b6c675ad79bea7cab84f0a392dca3d10eff779456f
```

Restore used source slot 05 on port `56524` and target slot 06 on port `56525`.
It dumped and restored only `trading_agent_disposable_test` with a private custom
archive, `--exit-on-error` and session authorization preservation.

The sanitized artifact records:

```text
alembic_head=0008_trading_domain_ledger
semantic_groups_equal=true
row_counts_equal=true
differing_semantic_subgroups=[]
semantic_row_differences={}
```

Effective ACL, function, identity, owner, structural-security and
trigger/policy digests match. Table row counts match. Raw ACL hashes remain
informational because effective semantics are equal. The restored database also
proved event-chain and snapshot preservation, permanent inbox claims, retry
idempotency and non-owner denials.

Artifact SHA-256:
`22dd5c215741036a3a4b1db97790f6bc0a6fcfaefe581ace01c7e227399283db`.

```text
PASS - DISPOSABLE RESTORE SEMANTIC CATALOG PARITY VERIFIED
```

Earlier failed or transcript-incomplete checkpoints remain audit history only.
