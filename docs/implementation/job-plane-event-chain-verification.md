# Job-plane event-chain verification

The frozen event-chain query ID is `job-plane-event-chain-v1`. It is bound with
the catalog query in the immutable query contract, SHA-256
`e3c81648fa405456050cd86f8d47ce67bdca5cd82017c1dc88b5d40fbd70b914`.
This is source-test verification evidence, not a disclosure of event records.

## Verified violation vocabulary and coverage

The event-chain source tests freeze the transition and violation vocabularies,
reject explicit extras, classify retry validity before assigning epochs, and
cover the bigint sequence boundary. Their negative fixtures verify
`NO_HISTORY`, `SEQUENCE_START`, `SEQUENCE_GAP`, `SEQUENCE_DUPLICATE`,
`BOOTSTRAP_EDGE`, `BOOTSTRAP_ATTEMPT`, `LATER_NULL_FROM_STATE`,
`DISCONNECTED_FROM_STATE`, `UNAPPROVED_EDGE`, `FINAL_STATE_MISMATCH`, and
`CROSS_JOB_ATTEMPT`.

Retry and terminal-state fixtures verify `RETRY_WRONG_ACTOR`,
`RETRY_WRONG_REASON`, `RETRY_ATTEMPT_CHANGED`, `RETRY_FORGED_ATTEMPT`,
`RETRY_OVER_BUDGET`, `RETRY_NOT_ADJACENT`, `RETRY_METADATA`,
`EVENT_AFTER_TERMINAL`, and `DUPLICATE_TERMINAL_IN_EPOCH`. Positive fixtures
cover valid worker and recovery retry epochs followed by a valid second
terminal result. These are source-test claims only; this document does not
assert the contents of any catalog or event stream.

## Gate results and runtime separation

The RED_D executor safety command
`tests/jobs/test_disposable_postgres_approval.py tests/jobs/test_postgres_harness.py`,
run with all four collector controls unset, reported 88 passed and 2 skipped.
Separately, this Task 5 evidence source commit ran an approval-free relevant
suite with all four collector controls unset: `test_job_authority_verifier.py`,
`test_disposable_postgres_approval.py`, `test_postgres_harness.py`,
`test_job_authority_catalog.py`, and `test_job_event_chain_authority.py` under
`tests/jobs/`. That distinct scope reported 196 passed and 47 skipped. The
RED_B catalog/event-chain suite reported 101 passed with no failures, errors,
or skips; the RED_D evidence collector reported 1 passed. The evidence record
also proves that both candidate-repair transactions were rolled back without
changing their post-rollback catalog captures.

Runtime PostgreSQL was untouched. No runtime connection data, settings,
credentials, event records, or catalog rows are stored in this documentation.
