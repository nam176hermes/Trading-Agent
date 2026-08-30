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
| P1-19 | P1-17, P1-18 | ACCEPTED | Exact P1 batches persist atomically with durable semantic projection authority; generic/Nautilus-v1 retain a hardened legacy route; independent spec/security review passed at source tree `a5e6924…`; PostgreSQL runtime authority remains deferred. |
| P1-20 | P1-04 | ACCEPTED | Pure engine-to-portfolio projection, exact six-decimal accounting, full instrument authority and monotonic observations passed independent spec/security review; raw canonical catalog-artifact digest semantics were requalified at source tree `dd140a4…`. |
| P1-21 | P1-19, P1-20 | ACCEPTED | Exact Decimal parity, deterministic trusted-prefix replay, durable authority binding and the post-ingest/pre-success receipt seam passed independent spec/security review; raw canonical catalog-artifact digest semantics were requalified at source tree `dd140a4…`; PostgreSQL runtime authority remains deferred. |
| P1-22A | P1-21 | ACCEPTED | Authenticated exact engine BACKTEST enqueue source authority, forward-only head 0013, unchanged runtime DB pin 0011 and raw catalog-digest seam passed independent spec/security review at source tree `dd140a4…`; PostgreSQL runtime authority remains deferred. |
| P1-22 | P1-16, P1-17, P1-18, P1-19, P1-20, P1-21, P1-22A | ACCEPTED | Exact request lineage, rational aggregate accounting, migrations through 0017, one-worker durable success/parity, complete captured PASS receipt and unchanged schema-8 closure passed fresh native qualification plus independent spec/security review at `021ea4b6…/375cfc0a…`. |
| P1-23 | P1-22 | ACCEPTED | Exact schema-8/G1 adversarial campaign passed 8 scenarios and 205 tests with zero skip; three durable jobs preserved semantic/accounting parity under distinct custody, and independent spec/security reviews passed at `b6563814…/efab9bc5…`. |
| P1-24 | P1-22 | ACCEPTED | Portable CI isolation, recursive growth budgets, pin inventory, clean candidate lineage, runbook and public-primitive provenance passed independent spec/security review at `b6563814…/efab9bc5…`. |
| P1-25 | P1-23, P1-24 | ACCEPTED_LOCAL | Exact `080a0786…/81ebb5c1…` schema-8 E2E, adversarial and portable gates plus independent spec/security reviews passed; remote `P1_A_COMPLETE` still requires an authorized PR, merge and protected-main proof. |
| P1-26 | P1-25 | AMENDED_BY_P1_27 | Engine-neutral v1 remains immutable at `12243c45…/55701417…`; P1-27 introduces `nautilus-paper-session-v2` for explicit exit-only Stop causality before integration. Network/live/production authority remains false. |
| P1-27 | P1-26 | ACCEPTED | Exact isolated Nautilus 1.231 paper runtime, protocol-v2 exit-only Stop causality, checkpoint/reconciliation custody, reset/re-registration, deterministic prefix projection and independent spec/security review passed at `4042be62…/90629801…`; network/live/production authority remains false. |
| P1-28 | P1-26, P1-27 | ACCEPTED | Exact `362e9275…/180a50f2…` controller/custodian integration, schema-8 closure `97185d4c…`, durable event/checkpoint custody and real `Start → ACK/EVENT/CHECKPOINT` public path passed independent spec/security review; disposable PostgreSQL migration runtime authority remains deferred and network/live/production authority remains false. |
| P1-29 | P1-27, P1-28 | ACCEPTED | Exact `e7e125ba…/140fa5f2…` durable intent/outcome chain, no-clobber recovery receipt, exact-prefix resume, six-boundary crash campaign, exit-only kill switch and fail-closed child liveness passed independent spec/security review with zero Critical/Important findings; network/live/production authority remains false. |
| P1-30 | P1-29 | READY | Certify backtest/local-paper semantic parity and the complete local P1 source evidence chain. |

P1-U tasks advance only in dependency order. `NT1231-U04-G1` is the accepted
qualification generation and the candidate remains inactive. P1 product work
may now start from P1-00. Exact 1.227/schema-6 rollback authority is unchanged.
These statuses grant no live, network-trading, or production authority.
