# Phase 3 Dual-Read Evidence

The same repository DTOs and API contract were exercised through legacy and
PostgreSQL backends.

| Query | Result |
|---|---|
| Latest market report and 10 assets | equal |
| Signals endpoint asset and recent-decision IDs | equal |
| Decision total | legacy 16,653; PostgreSQL canonical 16,517 |
| First-page IDs | equal |
| Canonical last-page IDs | equal; 17 rows |
| Canonical action filters except `NO_SIGNAL` | equal |
| `NO_SIGNAL` | legacy 140; PostgreSQL 4; accepted delta 136 |
| Asset filters | deltas sum to the same 136 quarantined observations |
| Date from 2026-06-25 | 176 / 176 |
| Date from 2026-06-24 | 849 / 842; seven quarantined observations |
| Capabilities | equal after ordering fix; all `UNKNOWN` |
| Cost summary scalar fields | equal |

The 136 total/action/asset/date differences are
`EXPECTED_NORMALIZATION`: Phase 2 mapped `WATCH` and `WATCH FOR EXIT` to
`NO_SIGNAL`; PostgreSQL does not.

Blocking differences remain:

- `MIGRATION_BUG`: the approved schema does not store decision
  `price_at_decision` independently. Deriving it from signal close differs for
  12,783 of 16,517 canonical decisions.
- `MIGRATION_BUG`: the schema does not store `report_snippet`; 16,516 canonical
  source decisions have a non-empty value.
- `CONTRACT_BUG`: `cost_sessions` does not store `symbols`; all 20 reviewed
  sessions contain the ten canonical crypto symbols.

`reflected` is false for all reviewed canonical decisions, so its current
default does not cause drift. Confidence, asset mapping, canonical action,
latest report, first/last ordering, and signal semantics did not drift.

The endpoint is treated as canonical decisions in PostgreSQL mode, so its total
is 16,517. The candidate wording is `Canonical Decisions`. No WATCH value was
mapped to a canonical action and no breaking response field was added.
