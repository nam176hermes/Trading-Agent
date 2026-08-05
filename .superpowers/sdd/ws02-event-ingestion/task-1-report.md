# WS-02D Task 1 — engine event ledger core

## Outcome

Implemented the engine-neutral event ledger core and hermetic in-memory
repository boundary. The implementation accepts only an exact, sealed
`ValidatedEngineEventBatch`; it does not parse raw child output, depend on a
provider object, reinterpret domain events, access PostgreSQL, mutate a
migration, or integrate the worker lifecycle.

## Implementation

- Added strict, frozen engine-event storage, batch receipt, projection, count,
  and restart-state models under `packages.engine_event_ledger`.
- Added typed invalid-batch, canonical identity conflict, and sequence-block
  failures. Sequence blocks retain the run id, expected/actual sequence, and a
  stable `SEQUENCE_GAP` or `SEQUENCE_REGRESSION` reason.
- Added canonical replay that verifies stored envelope bytes/digest/identity,
  contiguous per-run sequence, sorted event-type counts, last sequence, and
  last digest.
- Added `EngineEventLedgerRepository` and `InMemoryEngineEventLedger` under
  `services.job_store.engine_event_repository`.
- The fake stages every record, sequence check, projection, and receipt before
  changing state. Exact retry returns the retained receipt object; changed
  canonical message identity or changed receipt authority conflicts; gap or
  regression leaves all state untouched.
- Added authoritative state export/restart, receipt lookup, replay, and
  projection recovery. Restart rejects sequence gaps and event/receipt states
  that could not have committed atomically.
- Kept the existing `packages.event_ledger` domain-event types and repository
  entirely separate.

## TDD coverage

`tests/jobs/test_engine_event_ledger.py` covers:

- canonical storage, receipt fields, deterministic counts, and projection;
- exact duplicate with no extra stored event or projection effect;
- changed canonical content conflict and rollback;
- cross-batch gaps and regressions with typed reasons;
- a first-batch gap before the required initial engine event sequence;
- an intra-batch gap with complete rollback;
- exact public input type and reconstructed seal/metadata authority;
- changed receipt authority over identical event bytes;
- restart receipt recovery and deterministic projection replay;
- fail-closed recovery for sequence gaps and non-atomic receipt/event state;
- public API exclusion of domain-event and provider types.

Each new behavior was introduced by a focused failing test before the minimal
implementation change.

## Verification

Passing:

- `uv run --frozen pytest -q tests/jobs/test_engine_event_ledger.py` — 14 passed.
- `uv run --frozen pytest -q tests/jobs/test_engine_event_ledger.py tests/jobs/test_engine_result_validation.py tests/jobs/test_package_boundaries.py` — 33 passed.
- `uv run --frozen pytest -q -m "not runtime_postgres" tests/event_ledger` — 110 passed, 5 deselected.
- `make check-contracts` — passed.
- `make audit` — passed on branch `codex/nt-ws02-event-ingestion`.
- `git diff --check` and Python compileall for the added modules — passed.

Broader unrelated failures observed:

- `uv run --frozen pytest -q -m "not runtime_postgres" tests/jobs` completed
  with 1175 passed, 215 skipped, and 4 host/current-branch failures outside
  Task 1: a real-provider FD target race, the pre-existing engine worker fixture
  finishing BLOCKED, and two systemd credential fixtures under the Windows
  mounted temp path.
- `make test-core` was stopped after 5m38s once 76
  `tests/foundation/test_package6_controller_closure.py` cases had failed at
  the shared staging-authority fixture (`StagingAuthorityError` under the host
  temp path). At interruption, 833 tests had passed, 13 skipped, and 16 were
  deselected. No Task 1 file appeared in the failures.

No database, migration, persistent service, exchange, broker, order endpoint,
or other external interaction was executed.
