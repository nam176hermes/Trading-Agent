# P2 data-platform qualification runbook

## Safety boundary

Qualification is deterministic and provider-free. It creates only private,
self-cleaning local temporary artifacts. It must not contact an exchange,
broker, account, order endpoint, hosted document service, or external data
provider, and it does not mutate PostgreSQL or protected runtime paths.

## Focused source qualification

From the canonical repository root or a clean external worktree:

```bash
uv sync --frozen
make check-contracts
uv run pytest -q tests/data_platform tests/security_master
uv run python scripts/certify_p2_data_platform.py
make qualify-p2-source PRE_P3_RECEIPT_DIR=/absolute/private/pre-p3-receipts
```

The certification command runs the P2 and security-master suites, then executes
the provider-free data path three times. `PASS` requires byte-identical receipts
for all repetitions, immutable evidence and Parquet seals, PIT selection,
PyArrow/Polars/DuckDB parity, Qlib projection, retrieval recall, revision-chain
closure, the T1/T2/T3 correction campaign, and a closed Iceberg gate.

## PostgreSQL qualification

Migration `0019_p2_security_master` is covered by static and repository tests.
A real PostgreSQL 16 migration is a separate approved disposable-runtime lane:

```bash
make test-p2-runtime-postgres
```

Absent runtime authority is `DEFERRED`, not permission to use an existing
database. Never point this lane at production or operator data.

## Failure handling

- Receipt drift: retain both outputs, stop, and identify the first differing
  digest. Never update expected values to hide nondeterminism.
- Query parity failure: quarantine the snapshot and inspect Arrow types and
  decimal/timestamp conversion before any consumer projection.
- Security-master ambiguity or revision failure: reject the entire revision;
  never select an arbitrary candidate.
- Iceberg gate opened: record the measured reason and create a separate design
  and dependency review. Do not install or activate Iceberg implicitly.
