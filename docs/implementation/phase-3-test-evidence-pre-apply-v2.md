# Phase 3 Test Evidence Before Apply V2

| Check | Result |
|---|---|
| Migration repository | PASS; 48 tests |
| Contract drift | PASS |
| Alembic | PASS; `0002_quarantine_lineage (head)` |
| Fixture first/second apply | PASS; 503 then zero canonical inserts |
| Resume and failed retry | PASS |
| Mid-chunk rollback | PASS |
| Collision protection | PASS |
| Changed-source resume rejection | PASS |
| Sanitized persisted quarantine | PASS |
| Real-root apply guard | PASS |
| Real-data dry-run no-write | PASS |
| Candidate dashboard | PASS; tests, typecheck, lint, build |
| Isolated backend integration | PASS; 43/43 and live hashes/stats unchanged |
| Phase 1 safety suite | PASS; 85 passed, 2 intended connectivity skips |

Known non-blocking warning: Phase 2 FastAPI TestClient emits the previously
documented upstream deprecation warning. No broker connectivity test ran.

The active trading agent/dashboard were not restarted. No exchange, broker,
credential, scheduler, command API, public cutover, or real-data apply occurred.
