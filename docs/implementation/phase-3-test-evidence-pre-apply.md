# Phase 3 Test Evidence Before Apply

## Passing evidence

| Check | Result |
|---|---|
| PostgreSQL cluster verification | PASS; 16.14, `trading-agent`, `127.0.0.1:55432`, data 0700 |
| Initial role checks | PASS; all three roles non-superuser/no create role/database; reader default read-only |
| Alembic current/history | PASS; `0001_phase3_operational_store (head)` |
| Schema tests | PASS; 15 tables, required uniques/indexes/FKs/checks, idempotent upgrade |
| Role permission tests | PASS; migrator write allowed; reader SELECT allowed; reader writes denied; migrator role/database creation denied |
| Python full suite | PASS; 41 tests, one known upstream TestClient warning |
| Identity/normalization tests | PASS; 9 tests |
| Dry-run planner/CLI tests | PASS; 2 tests |
| Contract drift | PASS |
| Candidate dashboard | PASS; tests, typecheck, lint, build |
| Backend standalone integration | PASS; 43/43 |
| Phase 1 backend safety regression | PASS; 85 passed, 2 connectivity skips |
| Empty-target dump/restore drill | PASS; 15 tables, head revision, zero domain rows |
| Real-data dry-run no-write proof | PASS |

## Incomplete mandatory evidence

Fixture apply, second-run idempotency, resume, failed-chunk retry, full-chunk
rollback, collision protection, and sanitized persisted quarantine tests are not
implemented yet. They remain mandatory before real apply can be considered.

The real-data valid-decision count also differs from the checkpoint because 136
unknown enum records are correctly rejected by the strict ADR policy. Therefore
this evidence package is not an apply approval.

The standalone backend integration harness refreshed the already runtime-owned
`signals/predscope_signals.json` and `signals/adanos_signals.json` at 11:28 EDT,
as documented previously in Phase 0. This command was required by the requested
regression list but conflicts with the Phase 3 no-legacy-write boundary. The
files were not reverted because the legacy worktree is dirty and user changes
must be preserved. Neither file is part of the approved Phase 3 source inventory,
whose nine hashes remained unchanged, but this side effect is an additional
pre-apply blocker and the harness must be isolated before it is run again.
