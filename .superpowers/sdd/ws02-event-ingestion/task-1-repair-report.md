# WS-02D Task 1 repair — recovered batch authority

## Repair commit

`31495b9` — `fix: validate recovered engine batch authority`

## Blocker resolution

- Added one shared structural envelope validator used by both exact live batch
  ingestion and reconstructed receipt validation.
- Restart now rejects any receipt whose records do not share one exact
  `engine_run_id`, correlation id, causation id, initialization time, schema
  version, producer identity, source commit, and config digest.
- Existing receipt-to-first-record run validation and canonical batch digest,
  identity digest, count, sequence, and last-event checks remain fail-closed;
  because every recovered record must now share the first record's authority,
  no later record can escape the receipt/batch authority.
- No PostgreSQL, migration, worker lifecycle, service runtime, or external
  interaction was added.

## TDD regressions

The focused regressions failed before the production repair and passed after
it:

- one internally hash-consistent receipt spanning two engine runs;
- same-run receipt records with different correlation authority;
- same-run receipt records with different causation authority;
- same-run receipt records with different initialization authority;
- same-run receipt records with different producer authority;
- same-run receipt records with different source-commit authority;
- same-run receipt records with different config-digest authority.

## Verification

`uv run --frozen pytest -q tests/jobs/test_engine_event_ledger.py tests/jobs/test_engine_result_validation.py tests/jobs/test_package_boundaries.py`

Result: **40 passed in 2.28s**.

`git diff --check` also passed for the repair.
