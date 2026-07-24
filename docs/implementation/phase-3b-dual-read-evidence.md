# Phase 3B Dual-read Evidence

All 16,517 PostgreSQL canonical decisions were joined to their exact legacy
decision IDs and compared using direct source semantics.

| Contract field | Result |
|---|---|
| `price_at_decision` | 0 differences; 16,517 `EXACT` |
| `report_snippet` | 0 semantic differences; 16,516 `EXACT`, 1 `UNKNOWN` stored NULL and rendered contract-compatible empty string |
| Cost session symbols | All 20 sessions equal after shared deterministic uppercase/sorted/unique normalization; ten symbols each |
| Asset lineage | 17/17 assets attributable; minimum 410 lineage rows per asset; zero orphan links |

Legacy still exposes 16,653 observations while PostgreSQL contains 16,517
canonical decisions. The accepted delta remains exactly the 136 quarantined
`WATCH`/`WATCH FOR EXIT` observations. No value was mapped into a canonical
action.

The repository dual-read suite compares the complete first page and all action
totals. The exhaustive evidence script compared every canonical decision's
price and snippet. Market/capability semantics remain unchanged.

```text
MIGRATION_BUG: 0
QUERY_ORDERING_BUG: 0
CONTRACT_BUG: 0
EXPECTED_NORMALIZATION: 136 quarantined observation states
```
