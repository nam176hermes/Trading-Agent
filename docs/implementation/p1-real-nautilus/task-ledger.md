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
| P1-05 | P1-03, P1-04 | ACCEPTED | Deterministic root schemas, stdlib-only sealed grammar and golden fixtures passed two independent reviews; Foundation run 33214806868 passed. |
| P1-06 | P1-05 | ACCEPTED | Exact isolated CPython entry, closure-owned schema-8 lineage seam and bounded diagnostics passed independent spec/security review at 507279a. |
| P1-07 | P1-05, P1-06 | ACCEPTED | Descriptor-safe JSON/JSONL loading and fractional-time bounds passed independent review at `3533d6b`. |
| P1-08 | P1-04, P1-05, P1-06 | ACCEPTED | Catalog-built native instrument, exact G1 bounds, reset/re-registration and sealed closure custody passed independent review at `02b7a49`. |
| P1-09 | P1-03, P1-05, P1-06, P1-07, P1-08 | ACCEPTED | Quote-before-bar conversion, catalog increments/projection, timestamp bounds and exact G1 Bubblewrap evidence passed independent review at `02b7a49`. |
| P1-10 | P1-03, P1-05, P1-06, P1-08, P1-09 | ACCEPTED | Pure long/flat sizing, fee reserve, minimums and exact 16-decimal arithmetic passed independent review at `3f1f9d3`. |
| P1-11 | P1-08, P1-09, P1-10 | ACCEPTED | Real serial market orders/fills, zero-delta suppression, shutdown safety and scalar callback evidence passed independent spec/security review at `a2baafa`. |
| P1-12 | P1-07, P1-08, P1-09, P1-10, P1-11 | ACCEPTED | Exact G1 session, scalar callback/cache identity, long/flat accounting and disposal passed independent spec/security review at `b6b1143`. |
| P1-13 | P1-04, P1-05, P1-11, P1-12 | ACCEPTED | Canonical bounded EngineEvent JSONL, exact schedule/command authority and raw/semantic custody passed independent spec/security review at `2d8f1be`. |
| P1-14 | P1-06, P1-07, P1-12, P1-13 | ACCEPTED | Exact G1 final-state/accounting proof, zero-order completion and stable fail-closed diagnostics passed independent spec/security review at `f03d089`. |
| P1-15 | P1-02, P1-06, P1-14 | ACCEPTED | Exact code-owned schema-8 profile, G1/U08/manifest authority, descriptor-safe closure custody and unchanged legacy schema-6 authority passed independent spec/security review at `8f527b8`. |
| P1-16 | P1-15 | ACCEPTED | Exact-source schema-8 closure `75467781…` at `a596169`, native qualification and immutable custody passed independent spec/security review; G1 stayed unchanged and inactive. |
| P1-17 | P1-04, P1-14, P1-15 | ACCEPTED | Dedicated bounded multi-event result validation, semantic custody and durable metadata seam passed independent spec/security review at source tree `d116945…`. |
| P1-18 | P1-03, P1-15, P1-16, P1-17 | ACCEPTED | Code-owned closure `75467781…`, artifact resolver, result validator and inactive worker composition passed independent spec/security review at `5c4518f`; P1 ledger acceptance remains fail-closed for P1-19. |

P1-U tasks advance only in dependency order. `NT1231-U04-G1` is the accepted
qualification generation and the candidate remains inactive. P1 product work
may now start from P1-00. Exact 1.227/schema-6 rollback authority is unchanged.
These statuses grant no live, network-trading, or production authority.
