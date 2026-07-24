# Phase 3 Performance Evidence

`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` ran against 2,186 reports, 23,961
report assets, 16,517 decisions, and 344 signals.

| Query | Planning ms | Execution ms | Indexes | Shared hit blocks |
|---|---:|---:|---|---:|
| Latest report | 0.078 | 0.034 | `ix_market_reports_as_of` | 4 |
| Decision count | 0.028 | 1.826 | `decisions_pkey` index-only scan | 161 |
| First page | 0.032 | 0.020 | `ix_decisions_as_of` | 3 |
| Deep offset 16,500 | 0.036 | 2.679 | `ix_decisions_as_of` | 245 |
| Asset filter | 0.325 | 0.752 | `ix_assets_symbol`, `ix_decisions_asset_as_of` | 58 |
| Action filter | 0.048 | 0.034 | `ix_decisions_action_as_of` | 3 |
| Date filter | 0.066 | 0.027 | `ix_decisions_as_of` | 3 |
| Decision detail | 0.145 | 0.031 | decision and asset primary keys | 6 |

All blocks were cache hits in this run; shared read blocks were zero. These
numbers describe only the reviewed local dataset. Deep offset is measurably
more expensive than the first page; cursor pagination remains a later-phase
contract change.
