# Reviewed 0006 job-plane authority audit

This source-only record freezes the reviewed catalog evidence for the exact
`0006_job_transition_database_authority` head. It documents deterministic
evidence; it neither authorizes nor performs a runtime database operation.

## Bound inputs and catalog evidence

The catalog query ID is `job-plane-catalog-v1`. The immutable contract that
contains the catalog and event-chain query IDs has SHA-256
`e3c81648fa405456050cd86f8d47ce67bdca5cd82017c1dc88b5d40fbd70b914`.
The reviewed repair input has SHA-256
`c9c1b6e8b37cbb4b6820501e1f25848cb9f0c281d0034bcf4ffac253d112edfd`.

| Catalog | Rows | SHA-256 |
| --- | ---: | --- |
| 0006 | 723 | `b2dd91dbb12d585579e69b81394a530128fe84bc1dd2c7ef7683c9353eb1e4d1` |
| Derived 0007 | 724 | `1d83e9bc3f5cffe9e2dded41c33f46ce0b6d4395df84d3081d0b5132db487a40` |

The evidence was bound to source commit
`77ebc897d54aca3e19a4d4926a8172e4d2516954` and tree
`e4649bcd906b2a9187beb497cb411aa6749d0e8f`. The two independent 0006
captures were equal. The two independent 0007 derivations were equal, differed
from 0006, each began from the recorded 0006 catalog, and each left its
rollback capture unchanged. The reviewed secret scan passed.

## 0006 finding and limitation

The source test suite explicitly discloses the missing global default-function
ACL in 0006. Consequently, 0006 is a reviewed baseline and not final authority.
The 0007 catalog is evidence from an exact, hash-bound repair
derivation that was rolled back; it is not evidence that a forward migration
was applied to any runtime database.

Catalog tests cover the frozen query/repair surface, the complete reviewed
authority vocabulary, role membership and owner authority, safe role-setting
handling, alembic relation treatment, and adversarial catalog drift. They also
require the repair derivation to roll back on the same connection and reject
unknown setting values without returning them.

## Gate results and runtime separation

The RED_D executor safety command
`tests/jobs/test_disposable_postgres_approval.py tests/jobs/test_postgres_harness.py`,
run with all four collector controls unset, reported 88 passed and 2 skipped.
Separately, this Task 5 evidence source commit ran an approval-free relevant
suite with all four collector controls unset: `test_job_authority_verifier.py`,
`test_disposable_postgres_approval.py`, `test_postgres_harness.py`,
`test_job_authority_catalog.py`, and `test_job_event_chain_authority.py` under
`tests/jobs/`. That distinct scope reported 196 passed and 47 skipped. The
prior authorized RED_B catalog and event-chain suite reported 101 passed with
no failures, errors, or skips. The RED_D evidence collector reported 1 passed.
These distinct results classify the known 0006 gap; they do not weaken it or
convert it into approval.

The reviewed execution records state that runtime PostgreSQL was untouched.
No runtime evidence, settings, credentials, or connection data are included
in this repository record.
