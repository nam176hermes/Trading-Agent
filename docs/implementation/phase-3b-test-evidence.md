# Phase 3B Test Evidence

| Check | Fresh result |
|---|---|
| Baseline migration suite | 72 passed, 1 skipped |
| Phase 3B source analysis | 6 passed |
| Planner/approval focused suite | 25 passed |
| Transactional real-plan test DB | PASS; first/second apply, chunk rollback, failed status, resume |
| Repository/contract focused suite | 9 passed |
| Full migration repository | 99 passed, 1 skipped in 201.14 seconds |
| Contract generation/drift | PASS; no generated diff |
| Candidate `npm test` | PASS; 5 client tests plus dashboard integration |
| Candidate `npx tsc --noEmit` | PASS |
| Candidate `npm run lint` | PASS |
| Candidate `npm run build` | PASS; 14 routes generated |
| Legacy isolated integration | 43/43 passed |
| Phase 1 safety suite | 85 passed, 2 intended connectivity skips |
| PostgreSQL API smoke | PASS; required GETs 200, POST/PUT 405 |
| Explicit legacy rollback smoke | PASS; decision total 16,653, paper/paper |

The only migration-suite warning is the pre-existing FastAPI/Starlette
TestClient deprecation. Contract generation emits pre-existing TypeScript AST
deprecation output but exits successfully and produces no drift.

The two safety skips are broker connectivity tests intentionally excluded from
this paper-only task. No exchange or broker probe ran. Before/after hashes for
both live signal files, decision file size/mtime, and report count were
identical around the 43/43 integration harness.

The Phase 3B transaction suite used `trading_agent_test`, not the active
database. It verified 43,055 canonical rows, exact/unknown value behavior,
41,039 asset lineage rows, second-apply zero changes, atomic chunk rollback,
failed run status, and matching-run resume.
