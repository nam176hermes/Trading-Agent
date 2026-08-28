# P1 task ledger

P1 uses the accepted two-tier source baseline recorded in
`current-source-baseline.json`. The remote canonical source and the exact local
qualification source remain distinct. No row below grants artifact activation,
production, network-trading, or live authority.

| Task | Depends on | Status | Next acceptance |
|---|---|---|---|
| P1-R0 | None | ACCEPTED | Exact source commit/tree and zero candidate-bound delta accepted. |
| P1-U01 | P1-R0 | ACCEPTED | Release/API/semantic delta accepted for the exact qualification source. |
| P1-U02 | P1-U01 | ACCEPTED | Immutable v1.231 release/source provenance accepted. |
| P1-U03 | P1-U02 | ACCEPTED | Candidate-only toolchain and dependency policies accepted. |
| P1-U04 | P1-U03 | ACCEPTED | Sealed side-by-side v1.231 candidate generation accepted but inactive. |
| P1-U04C | P1-U04 | ACCEPTED | G1, P1-scoped/P1-specific/runtime-release host evidence, and three independent reviews accepted; global PG-only authority remains deferred. |
| P1-U05 | P1-U04C | ACCEPTED | All 33 API surfaces and 153 invocation mappings passed on exact G1; actual entry/fill/flatten callbacks were recorded without synthesis. |
| P1-U06 | P1-U05 | READY | Pass the release-regression and exact execution/accounting semantics campaign. |
| P1-U07 | P1-U06 | NOT_STARTED | Produce three deterministic candidate runs and zero unexplained semantic drift. |
| P1-U08 | P1-U07 | NOT_STARTED | Approve 1.231 for P1-A/B only or hold; legacy Phase4 profiles stay on 1.227. |

P1-U tasks advance only in dependency order. `NT1231-U04-G1` is the accepted
qualification generation and the candidate remains inactive. P1 product work
remains blocked until U05-U08 complete. Exact 1.227/schema-6 rollback authority
is unchanged. These statuses grant no live, network-trading, or production authority.
