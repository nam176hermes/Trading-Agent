# Phase 3 Second Apply and Idempotency

The identical approved source and guard conditions were applied again after the
first-run integrity gate passed.

| Field | Result |
|---|---|
| Run ID | `f510f07a-7064-4777-9e82-a442b49dce47` |
| Start / finish | `2026-07-11T16:33:15.238550Z` / `2026-07-11T16:33:35.874144Z` |
| Duration | 20.635618 seconds |
| Canonical inserted / skipped / updated | 0 / 43,055 / 0 |
| Invalid source observations | 222 |
| Canonical counts changed | no |
| Quarantine / audit rows changed | no; remained 222 / 2,349 |
| New source files / chunks | 2,295 / 2,328 tracking rows |

All 4,656 chunks across both runs are `COMMITTED`; none are failed or pending.
There are zero duplicate quarantine identities and zero duplicate source-scoped
normalization audits. First-run provenance was not overwritten.

The dedicated `trading_agent_test` rehearsal demonstrated failed-chunk rollback
and resume at the current Alembic revision. A completed real run is rejected
with an explicit policy message rather than reopened. Default real-root apply
without approval was tested again and rejected.
