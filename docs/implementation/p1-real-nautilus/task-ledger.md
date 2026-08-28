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
| P1-U06 | P1-U05 | ACCEPTED | All 40 release items are classified and all eight fresh-process native scenarios match the exact Decimal oracle. |
| P1-U07 | P1-U06 | ACCEPTED | Three deterministic runs per runtime produced one digest each; all eight cross-version scenarios have no semantic drift. |
| P1-U08 | P1-U07 | ACCEPTED | 1.231/G1 is approved for P1-A/B only; legacy Phase4 profiles remain byte-identical on 1.227/schema 6. |
| P1-00 | P1-U08 | ACCEPTED | Exact P1 source parent, engine receipt, worktree topology and tool authority are pinned. |
| P1-01 | P1-00 | ACCEPTED | Existing real BacktestEngine path and all 33 U05 API surfaces are characterized without new runtime authority. |
| P1-02 | P1-00, P1-01 | ACCEPTED | Architecture, threat model, ownership and frozen-launcher boundaries are executable. |
| P1-03 | P1-02 | ACCEPTED | Four P1 input artifacts are closed, immutable, byte-bounded and canonical. |
| P1-04 | P1-02 | ACCEPTED | Independent review plus remediation rereview passed; event/state/semantic authority is closed. |
| P1-05 | P1-03, P1-04 | IN_REVIEW | Root schemas, stdlib-only sealed grammar and golden fixtures are generated deterministically. |
| P1-06 | P1-05 | BLOCKED | Await accepted P1-05 review before scaffolding the sealed runtime. |

P1-U tasks advance only in dependency order. `NT1231-U04-G1` is the accepted
qualification generation and the candidate remains inactive. P1 product work
may now start from P1-00. Exact 1.227/schema-6 rollback authority is unchanged.
These statuses grant no live, network-trading, or production authority.
