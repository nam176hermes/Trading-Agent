# ADR: P2 canonical point-in-time data platform

## Status

Accepted for the P2 source candidate. This ADR grants no runtime, provider,
database, broker, deployment, paper-activation, or live-trading authority.

## Decision

P2 has one canonical immutable data path:

`provider evidence -> quality/normalization receipt -> Arrow schema -> Parquet partition -> PIT snapshot`

- Security-master identities and revisions are bitemporal, append-only, and
  projected into the existing fixed P1 paper contracts.
- Every query declares `valid_at`, a knowledge cutoff, and one visibility mode:
  market available, system observed, or as ingested.
- Raw evidence is content-addressed and immutable. Adjusted prices are derived;
  raw values are never overwritten.
- Arrow field IDs and semantics are stable. Evolution is additive and new
  fields must be nullable until a new data API epoch is deliberately adopted.
- Parquet is the source dataset format. PyArrow, Polars, and DuckDB must return
  identical canonical rows for a certified snapshot.
- Qlib, Nautilus, document retrieval, and future consumers are projections;
  none becomes canonical data authority.
- The PageIndex seam has a deterministic local implementation and benchmark.
  A hosted adapter is not enabled by source and requires separate provider
  authority.
- Apache Iceberg stays absent until a measured scale trigger opens its gate.

## Consequences

PIT leakage, schema mutation, evidence drift, ambiguous provider conflicts,
mixed snapshot contracts, and cross-engine query divergence fail closed. P2
adds DuckDB and Polars to the core dependency graph; no Qlib, PageIndex, or
Iceberg runtime dependency is required for the local source candidate.
