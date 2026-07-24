# Phase 3 Pre-Apply Review

## Ready evidence

- PostgreSQL 16.14 dedicated user-owned cluster is healthy and localhost-only.
- Database and three least-privilege application roles exist.
- Alembic head and approved schema tests pass.
- Real-data planner is dry-run by default and created no PostgreSQL or legacy
  writes.
- All nine approved checkpoint hashes were recaptured unchanged.
- Report, signal, capability, cost-scope, and total decision-seen counts match
  the checkpoint.
- Schema/bootstrap custom dump restore drill passes.
- Contract, candidate, backend integration, and Phase 1 safety regressions pass.

## Blocking issues

1. Strict Phase 3 normalization rejects 136 decisions: 122 `WATCH` and 14
   `WATCH FOR EXIT`. Silent mapping to `NO_SIGNAL` would violate the approved
   ADR. The valid count is 16,517 rather than 16,653.
2. Fixture transactional apply/idempotency/resume/rollback/collision tests are
   not yet implemented.
3. The required legacy backend integration harness refreshed two known signal
   JSON outputs. They were not reverted in the dirty legacy worktree. They are
   outside the approved migration inventory, but the harness must be isolated
   before reuse to satisfy the Phase 3 no-source-write boundary.
4. Because of those blockers, the dry-run `would insert` count 43,038 is not an
   approved migration expectation.

## Source inventory comparison

Every approved recaptured hash equals the pre-change checkpoint:

```text
asset registry: 05f6fe43333a3b484aee0abb604b74a5c8e0cda251b526fe7c7b3f00bcad9c8b
report inventory: 4484acb8d1aa364f8c72368bdf273cf68f0d970503e6da8820f89b2057cf835d
latest report: ad26c0bc07c77b1f37af6497b339cdb40d7324e0c8f88dd61b7344fc103cfebc
decisions: 0e97979237e4f0eaee8bc20235696a278c5b91e765acb5da591c24a358f981a3
logical signals: 693f2985c61972fccde1d71a7b452f2fb9bec588a1f4e3a41995b8217974e030
logical orders/trades: ae1ee321d50f34c0504399dcaea07c799d2775be3e15cbce1433f68803973b09
scratchpad inventory: 0deb016be82bed327db5b197a480c35b15653ebb540071ed849f117680d5d732
fixture subset: 281a49e53146f8a9d09f4674f9caadcbe8c2543365ba204faa9fd0caae82195b
combined inventory: dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce
```

No real-data apply was run. Resolving the enum policy and completing fixture
transaction tests require another reviewed implementation step.

REAL-DATA APPLY STATUS: BLOCKED PENDING USER REVIEW
