# P2 canonical data-platform source evidence

## Baseline and scope

P2 was rebased from remote `main` at
`8372641a67eac70d92e18f00258e3230678d7936` in external worktree
`trading-agent-worktrees/p2-v3`. The canonical checkout and prior dirty donor
worktree were not rewritten.

This source candidate implements the accepted P2-00 through P2-20 scope:

| Work package | Source outcome |
| --- | --- |
| P2-00..01 | Rebaseline, component boundaries, safety and ADR |
| P2-02 | Stable Arrow schema registry and explicit PIT contract |
| P2-03 | Append-only PostgreSQL/security-master resolver and snapshots |
| P2-04 | PIT corporate-action factors and immutable raw normalization |
| P2-05..07 | Private content-addressed store, partition/snapshot V2, deterministic Arrow/Parquet |
| P2-08..09 | Existing injected provider boundary plus raw-evidence/receipt adapter and provider-free fixture |
| P2-10 | OHLCV quality receipt and explicit provider-conflict policy |
| P2-11..12 | PIT snapshot selection and PyArrow/Polars/DuckDB parity |
| P2-13 | Deterministic Qlib CSV projection |
| P2-14, P2-18 | Existing P1 Nautilus artifacts projected from the security master |
| P2-15..17 | Immutable document registry, local PageIndex seam, recall/MRR benchmark |
| P2-19 | Measured Iceberg gate; dependency remains absent while closed |
| P2-20 | Provider-free three-of-three certification command and runbook |

## Verification contract

The source verdict requires focused P2/security-master tests, generated-contract
consistency, deterministic certification, dependency audit, and the repository's
portable gates. Canonical-root-only governance checks cannot be promoted from a
linked worktree; they must be replayed after integration in the canonical clean
checkout. Source qualification never implies runtime or live authority.
