# P1 Real Nautilus Engine Vertical Slice — v1.231.0 Rebased Codex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for execution, `superpowers:test-driven-development` for every behavior change, `superpowers:requesting-code-review` after each task, and `superpowers:verification-before-completion` before any completion claim. Use `superpowers:using-git-worktrees` to isolate all implementation work.

**Goal:** First qualify and promote NautilusTrader `1.231.0` as the repository's final Cython-v1 baseline without losing the `1.227.0` rollback. Then productize the real low-level `BacktestEngine` path into an end-to-end durable BTCUSDT vertical slice and extend the same semantics into a network-free local paper session.

**Architecture:** Keep generic control-plane contracts and job custody in root Python 3.11. Keep every `nautilus_trader` import inside a sealed external CPython 3.12 runtime. Introduce schema 7 for the promoted v1.231 release/closure baseline and schema 8 for P1 product protocol/runtime inventory, emit a production-shaped canonical event stream, persist it in the existing engine-event ledger, project it through a pure adapter into the existing portfolio reducer, and require exact parity before job success.

**Tech stack:** Python 3.11 control plane, isolated CPython 3.12, NautilusTrader `1.231.0` Cython v1, Rust 1.97.1 candidate toolchain, Cython 3.2.9, Pydantic v2 contracts, PostgreSQL/fake repositories already present, Bubblewrap, native Rust entry guard, uv, pytest, GitHub Actions, Codex subagents.

**Trading-Agent planning baseline:** `c8fb6f694b11c065d5b819614532e9a77aa8da4b`  
**Current rollback engine:** `1.227.0` / `280ae1762df51a492a4ce71506a40b5c8706def5`  
**Candidate/promoted engine:** `1.231.0` / `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`  
**Official candidate sdist SHA-256:** `142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f`

---

## 1. Outcome and program split

### P1-U — Mandatory v1.231 rebaseline and promotion

```text
freeze 1.227 rollback
  → inventory every pin
  → classify 1.228–1.231 delta
  → bind release/tag/commit/provenance
  → build Rust/build/runtime caches
  → build sealed 1.231 candidate beside 1.227
  → run direct API probe
  → run release-regression campaign
  → dual-run semantic diff
  → promote 1.231 or HOLD_1_227
```

P1-U is a hard prerequisite. No productization task may begin on a candidate that has not passed P1-U08.

### P1-A — Real backtest vertical slice

```text
BACKTEST job
  → fenced claim
  → RunBacktest envelope
  → sealed artifact/schema-8 P1 product closure authority
  → Bubblewrap/native guard/CPython 3.12
  → real Nautilus 1.231 BacktestEngine
  → real orders/fills/account/cache
  → canonical multi-event JSONL
  → worker validation/sealing
  → durable engine-event ledger
  → engine→portfolio projection
  → portfolio reducer parity
  → durable job success
```

### P1-B — Local paper parity and recovery

P1-B reuses the exact P1-A target strategy, event protocol and accounting path in a long-lived, network-free local paper session controlled by the existing paper controller/custodian. It adds checkpoints, restart, reconciliation and kill-switch proofs. P1-A can merge first; the entire P1 program is not complete until P1-B passes.

---

## 2. Verified starting point and upgrade decision

The plan starts from these facts and requires P1-U00 to re-prove repository-local evidence:

- canonical Trading-Agent baseline is `c8fb6f694b11c065d5b819614532e9a77aa8da4b`;
- the current sealed engine is `1.227.0`, CPython 3.12, Rust 1.95.0 and Cython 3.2.4;
- the root remains Python 3.11 and may not import Nautilus;
- the repository already has real `BacktestEngine` execution, worker custody, Bubblewrap/native guard, event validation/ingestion, an engine-event ledger and an independent portfolio reducer;
- `1.231.0` is the candidate final-v1 baseline, not a v2 migration;
- the `1.227.0` closure remains immutable rollback until at least P1-A final certification;
- the active baseline changes only in P1-U08 after provenance, build, API, regression and dual-run gates.

The detailed decision evidence is in `trading-agent-nautilus-v1.227-to-v1.231-upgrade-assessment-2026-08-16.md`.

---

## 3. Architecture decisions that Codex must not reopen casually

### AD-0 — Upgrade before productization, but never by in-place hot swap

Build a side-by-side `1.231.0` candidate, qualify it, then atomically promote code-owned policy. A failed gate produces `HOLD_1_227`, not a compatibility shim or weakened test.

### AD-1 — Treat 1.231.0 as the final Cython-v1 bridge

P1 uses exact `1.231.0` after promotion. It does not track `develop`, `develop_v1`, `latest`, a version range or an RC. A future v2 migration is a separate program and implements the same engine-neutral session/event/accounting contracts in a separate runtime package and closure.

### AD-2 — Do not clone or vendor all of NautilusTrader

Use the exact approved release/source/closure. Copy or adapt only small version-pinned patterns when that reduces API-construction risk. Record every copied/adapted symbol in the provenance log.

### AD-3 — Continue with low-level `BacktestEngine` for P1

The current sealed path already qualifies the low-level engine. Create a `BacktestSession` seam so future `BacktestNode` or v2 implementations replace only engine assembly/data loading, not commands, events, worker custody, ledger or accounting.

### AD-4 — Engine receives targets, not alpha logic

Research/AI/risk layers produce `EngineTargetPortfolio`. No moving average, LLM, debate, Qlib or FinRL logic belongs in the isolated engine.

### AD-5 — Root never imports Nautilus

Only `engines/nautilus/runtime_v1/**` may import the promoted Cython-v1 package. A future v2 package must be separate. Both runtimes may never import as `nautilus_trader` in the same process.

### AD-6 — Full raw event truth plus independent accounting

P1 emits order/fill/position/account lifecycle facts, stores exact bytes, then independently projects/reduces them. Engine final summary is a parity target, not sole authority.

### AD-7 — Network disabled by construction

No provider credentials, venue adapters, DNS, HTTP, WebSocket, broker/account/order endpoints or live reconciliation. The v1.230 HTTP security fix does not relax this structural prohibition.

---

## 4. Global invariants

1. **Paper-only authority:** live and production approvals stay false.
2. **Promotion gate:** active v1.231 use requires accepted P1-U08 evidence on the exact closure digest.
3. **Rollback isolation:** v1.227 remains immutable, explicit and non-default; no mixed closure or shared writable state.
4. **Exact engine identity:** version 1.231.0, runtime family `cython-v1`, upstream commit `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`, promoted baseline schema 7, P1 product schema 8 and exact dependency policy.
5. **No moving authority:** no `latest`, branch, version range, ambient compiler/package/cache or client-selected runtime.
6. **No root Nautilus import:** enforced by source checker.
7. **No float:** prices, quantities, weights, balances, fees and PnL use canonical Decimal strings.
8. **Long/flat only:** one BTCUSDT spot instrument, target weight `[0,1]`, no leverage or short.
9. **No look-ahead:** L1 quote precedes bar decision; bar `ts_init` is close time.
10. **Real engine effects:** orders/fills come from actual Nautilus activity, never a parallel arithmetic simulator.
11. **Truthful event provenance:** callback facts, post-run observations and upstream-version lineage are distinct.
12. **Canonical event stream:** exact schema, contiguous sequence, request attribution, one completion last, no unknown fields.
13. **Raw and semantic identities are distinct:** semantic equivalence never weakens raw batch validation.
14. **Durability before success:** ledger receipt and portfolio parity precede job success.
15. **No uncertain retry:** ambiguous engine/ledger/paper outcome blocks for reconciliation.
16. **Frozen reference files:** old large launchers cannot grow.
17. **Existing qualification remains green:** zero-order, simulation and paper-compat tests cannot regress.
18. **No self-approval:** implementer and both fresh reviewers are distinct.
19. **No shared-file parallelism:** central verifier, worker, Makefile and CI edits are serialized.
20. **Evidence before claim:** no completion statement without fresh output on exact SHA and closure digest.

---

## 5. Codex subagent model policy

| Role | Default model | Reasoning | Use |
|---|---|---:|---|
| Program/architecture lead | `gpt-5.6-sol` | `xhigh` | ADRs, dependency graph, cross-boundary decisions |
| Supply-chain/runtime security | `gpt-5.6-sol` | `xhigh` | release provenance, toolchain, closure, sandbox, native guard |
| Execution semantics engineer | `gpt-5.6-sol` | `xhigh` | target sizing, orders, fills, state machines |
| Ledger/accounting/recovery engineer | `gpt-5.6-sol` | `xhigh` | idempotency, replay, parity, restart |
| Complex bounded implementation | `gpt-5.6-sol` | `high`/`xhigh` | isolated modules and integrations |
| Repository/API researcher | `gpt-5.6-terra` | `high` | inventories, generated probes and bounded characterization |
| Generator/docs/CI mechanic | `gpt-5.6-terra` | `high` | deterministic generation and runbooks |
| Narrow mechanical edit | `gpt-5.6-luna` | `medium` | only fully specified, non-authority work |
| Spec reviewer | fresh `gpt-5.6-sol` | `high`/`xhigh` | exact task compliance |
| Security/code-quality reviewer | fresh `gpt-5.6-sol` | `xhigh` | adversarial review |
| Final certification reviewer | fresh `gpt-5.6-sol` | `xhigh` | full-program audit |

If `xhigh` is unavailable, use the strongest supported setting and record the mapping. Never downgrade provenance, execution, accounting or recovery to Luna.

---

## 6. Worktree, branch and review protocol

```text
main (c8fb6f694b11c065d5b819614532e9a77aa8da4b)
  └── p1/nautilus-v1231-rebaseline       # P1-U integration
        ├── task/P1-U00-rollback-baseline
        └── ...
  └── p1/real-nautilus-v1231             # starts from accepted P1-U08 SHA
        ├── task/P1-00-baseline
        └── ...
```

For every task:

1. Record latest accepted integration SHA and exact external authority digest.
2. Create a fresh task worktree from that SHA.
3. Dispatch a fresh implementer with only the task contract and relevant accepted context.
4. Use red→green TDD and one coherent commit.
5. Dispatch a fresh spec reviewer.
6. Fix through a new implementer pass.
7. Dispatch a fresh code-quality/security reviewer.
8. Run focused, shared regression and boundary gates.
9. Cherry-pick only accepted commits into the integration branch.
10. Record SHA/evidence and remove the task worktree.

No subagent may push, merge, release, change branch protection, deploy, grant authority or submit credentials without explicit operator authorization.

### Recommended checkpoint PRs

1. **PR-U1:** P1-U00..P1-U02 — rollback inventory, delta, provenance.
2. **PR-U2:** P1-U03..P1-U05 — toolchain, candidate closure, API probe.
3. **PR-U3:** P1-U06..P1-U08 — regression, dual-run, promotion.
4. **PR-A:** P1-00..P1-05 — product architecture/contracts/generation.
5. **PR-B:** P1-06..P1-14 — modular isolated runtime.
6. **PR-C:** P1-15..P1-19 — closure/worker/ledger.
7. **PR-D:** P1-20..P1-25 — accounting/E2E/P1-A certification.
8. **PR-E:** P1-26..P1-30 — local paper parity/final certification.

---

## 7. Parallelization rules

| Wave | Tasks | Notes |
|---|---|---|
| 0 | P1-U00 | Freeze rollback and inventory first. |
| 1 | P1-U01, P1-U02 | Delta research and provenance may overlap; provenance ADR waits for impact review. |
| 2 | P1-U03 | One toolchain/dependency owner. |
| 3 | P1-U04 | Security-critical candidate build and schema-7 verifier. |
| 4 | P1-U05, then P1-U06 | API probe precedes full behavior campaign; fixtures can be prepared in parallel. |
| 5 | P1-U07 | Dual-runtime semantic diff. |
| 6 | P1-U08 | Atomic promotion/final upgrade review. |
| 7 | P1-00 | Product implementation baseline. |
| 8 | P1-01, P1-02 | Characterization and ADR. |
| 9 | P1-03, P1-04 | Separate contract files. |
| 10 | P1-05 | Generated protocol freeze. |
| 11 | P1-06..P1-10 | New isolated modules with explicit ownership. |
| 12 | P1-11..P1-14 | Integrate sequentially unless file ownership is disjoint. |
| 13 | P1-15, then P1-16 | Shared profile/closure files. |
| 14 | P1-17, P1-18 | Prefer sequential central integration. |
| 15 | P1-19, P1-20 | Ledger and pure projection can develop separately. |
| 16 | P1-21, then P1-22 | Accounting parity precedes E2E success. |
| 17 | P1-23, P1-24 | Qualification and CI/docs. |
| 18 | P1-25 | P1-A final review. |
| 19 | P1-26 | Paper protocol gate. |
| 20 | P1-27, P1-28 | Runtime first; integration tests may prepare. |
| 21 | P1-29 | Recovery gate. |
| 22 | P1-30 | Final certification. |

Never parallel-edit `engine_spawn.py`, `engine_results.py`, `nautilus_closure.py`, `worker.py`, paper controller/integration, Makefile, CI workflows, generated protocol/schema files or active engine policy files.

---

## 8. Standard reviewer prompts

### Spec-compliance reviewer

```text
Review only whether candidate <TASK_ID> satisfies every requirement, non-goal,
dependency and exact authority pin in the accepted plan. Read actual diff,
tests and fresh output. Report missing, extra or weakened behavior with evidence.
Return PASS only when complete and no unrequested scope exists.
```

### Code-quality/security reviewer

```text
Review <TASK_ID> adversarially. Focus on provenance, moving authority, TOCTOU,
canonical parsing, Decimal precision, execution truthfulness, event order,
idempotency/replay, accounting, cleanup, rollback isolation, mixed v1/v2 state,
frozen-file growth and responsibility leakage. Return PASS only with no
unresolved HIGH or CRITICAL finding.
```

---

## 9. Task graph

```text
P1-U00
  ├─ P1-U01 ─┐
  └─ P1-U02 ─┴─ P1-U03 → P1-U04 → P1-U05 → P1-U06 → P1-U07 → P1-U08
                                                                      ↓
                                                                   P1-00
 ├─ P1-01 ─┐
 └─ P1-02 ─┴─ P1-03 ─┐
                 P1-04 ├─ P1-05
                       └─────────┐
P1-05 → P1-06/07/08/09/10 → P1-11 → P1-12 → P1-13 → P1-14
                                                      ↓
                                           P1-15 → P1-16
                                              ↓         ↓
                                           P1-17 → P1-18 → P1-19
                                               P1-04 → P1-20
                                           P1-19 + P1-20 → P1-21
                                                   ↓
                                                P1-22
                                             ↙         ↘
                                         P1-23       P1-24
                                             ↘         ↙
                                                P1-25
                                                   ↓
                                                P1-26 → P1-27 → P1-28 → P1-29 → P1-30
```

---

# Detailed Tasks

## P1-U00 — Freeze the 1.227 rollback baseline and inventory every engine pin

**Milestone:** MU0 — Upgrade bootstrap  
**Depends on:** None  
**Parallel wave:** 0  
**Primary implementer subagent:** Repository Baseline and Pin Inventory Lead — `gpt-5.6-terra`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `docs/implementation/p1-real-nautilus/upgrade/1.227-rollback-baseline.md; docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json; scripts/inventory_nautilus_pins.py; tests/governance/test_nautilus_pin_inventory.py`  
**Commit:** `docs(p1u): freeze Nautilus 1.227 rollback baseline`

### Objective

Create an exhaustive, executable inventory of every place where the current engine version, source, toolchain, closure or semantic expectation is pinned before any candidate change.

### Implementation steps

- Verify canonical `main` and clean tree at the reviewed Trading-Agent baseline. Record commit, tree, current branch protection evidence and existing P0/P0-M1 gate results.
- Inventory all literal and derived references to `1.227.0`, `v1.227.0`, the old upstream commit, tag object, source archive SHA, Cargo.lock/pyproject digests, Rust 1.95.0, Cython 3.2.4, setuptools 82, closure manifest versions, runtime profile names and expected result validators.
- Include source, tests, scripts, Makefile, CI, docs and external-authority templates. The inventory script must fail when a new unclassified pin appears.
- Record current sealed artifact/closure manifests and verification commands without copying private runtime bytes into Git.
- Declare the current 1.227 closure read-only rollback authority. No subsequent task may mutate it in place.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Seed a fake `1.227.0` pin in a fixture and prove the inventory test fails until classified.
- Run `make check-p0-baseline`, `make check-p0-maintainability`, and portable CI.
- The inventory has zero `UNKNOWN` entries and includes every policy named by `engines/nautilus/README.md`.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] Rollback baseline is reproducible and immutable.
- [ ] No runtime policy or active pin changed.
- [ ] Every later upgrade task cites the inventory record it owns.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U01 — Create the v1.227-to-v1.231 upstream delta and direct API impact ledger

**Milestone:** MU0 — Upgrade bootstrap  
**Depends on:** P1-U00  
**Parallel wave:** 1  
**Primary implementer subagent:** Nautilus API and Release Delta Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Owned files:** `docs/implementation/p1-real-nautilus/upgrade/v1.227-to-v1.231-impact.md; docs/implementation/p1-real-nautilus/upgrade/direct-api-contract.json; tests/nautilus_upgrade/test_direct_api_contract_source.py`  
**Commit:** `docs(p1u): map Nautilus 1.231 API and semantic delta`

### Objective

Reduce the 1,482-commit upstream delta to the exact build, import, callback, backtest, accounting and persistence surfaces used or planned by Trading-Agent.

### Implementation steps

- Pin release notes for 1.228.0, 1.229.0, 1.230.0 and 1.231.0 and classify each item as `V1_DIRECT`, `V1_INDIRECT`, `V2_ONLY`, `ADAPTER_ONLY`, `BUILD_ONLY`, or `NOT_RELEVANT`.
- Enumerate every direct Nautilus import and invoked member in existing launchers and the planned runtime. Record module, symbol, constructor/method signature, callback/event shape and upstream source path at both versions.
- Explicitly cover `BacktestEngine`, `BacktestEngineConfig`, venue/account registration, `FeeModel`, `FillModel`, `Strategy`, order/fill callbacks, cache/account reads, `BacktestResult`, `CurrencyPair`, QuoteTick/Bar and object conversion.
- Inventory any use of Nautilus `event_store`, cache persistence or state serialization. Do not infer that the project's own engine-event ledger automatically proves absence.
- Create an impact disposition for every changed direct file: `UNCHANGED_API`, `BEHAVIOR_REQUALIFY`, `ADAPT`, `BLOCK`, or `NOT_USED`.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- The source test fails when any existing direct import is absent from the contract.
- Every `BEHAVIOR_REQUALIFY` item maps to a later executable test in P1-U05/U06/U07.
- No v2-only feature is used to justify a v1 runtime change.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] The API ledger is complete enough to generate the import probe without hand-selected omissions.
- [ ] Unknown or ambiguous upstream changes remain blockers, not assumed-compatible entries.
- [ ] The v2 migration is explicitly out of scope for P1.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U02 — Establish immutable v1.231 release and source provenance authority

**Milestone:** MU1 — Supply-chain rebaseline  
**Depends on:** P1-U00, P1-U01  
**Parallel wave:** 1  
**Primary implementer subagent:** Supply-Chain Security Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/v1.231-provenance-policy.json; docs/adr/ADR-P1U-NAUTILUS-1.231-SOURCE-AUTHORITY.md; scripts/verify_nautilus_release_provenance.py; tests/nautilus_upgrade/test_release_provenance.py; docs/implementation/p1-real-nautilus/upgrade/provenance-evidence.md`  
**Commit:** `sec(p1u): pin Nautilus 1.231 release provenance`

### Objective

Bind the candidate to exact upstream release metadata, tag object, commit and content digests without trusting an unsigned tag name or ambient network state.

### Implementation steps

- Pin tag `v1.231.0`, annotated tag object `d3e1685e979925d7b0ffacd1b3f442547686e18f`, underlying commit `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`, official sdist SHA-256 `142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f` and official CPython 3.12 Linux wheel SHA-256 `8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216`.
- Verify release immutability metadata, SHA256SUMS, in-toto/Sigstore assets and provenance claims. Record exactly what is and is not cryptographically verified; the unsigned tag is not upgraded to a signed claim.
- Compare two candidate source authorities: exact commit archive versus official release sdist. Select one primary build source in the ADR and retain the other as an independent cross-check. Do not change source format silently.
- Require safe archive member layout, one expected root, no path traversal, no special files, exact Cargo.lock/pyproject/build.py hashes and no unexpected generated binaries.
- Design closure manifest schema 7 provenance fields: runtime family, release tag, tag object, upstream commit, source artifact digest, release-manifest digest and attestation digest(s). Preserve schema 6 for rollback verification only.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Mutation tests for tag object, commit, source SHA, release manifest or attestation digest fail closed.
- Offline verification succeeds from a fully populated private cache and makes no network call.
- Present-but-invalid provenance is failure; only absent external cache may be classified `DEFERRED`.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] One primary source authority is approved in ADR with a separately verified cross-check.
- [ ] Schema 7 fields are specified before implementation in shared verifier code.
- [ ] No candidate bytes are committed to the repository.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U03 — Refresh sealed Rust, build-wheel and runtime dependency policies for v1.231

**Milestone:** MU1 — Supply-chain rebaseline  
**Depends on:** P1-U02  
**Parallel wave:** 2  
**Primary implementer subagent:** Hermetic Build and Toolchain Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/candidates/v1.231/engine-build-policy.json; engines/nautilus/candidates/v1.231/input-cache-policy.json; engines/nautilus/candidates/v1.231/toolchain-inputs.json; engines/nautilus/candidates/v1.231/wheel-cache-policy.json; engines/nautilus/candidates/v1.231/runtime-dependency-policy.json; scripts/prepare_nautilus_toolchain.py only where generic capability is missing; scripts/write_nautilus_toolchain_inputs.py; scripts/prepare_nautilus_input_cache.py only where generic capability is missing; scripts/prepare_nautilus_wheel_cache.py only where generic capability is missing; tests/nautilus_upgrade/test_v1231_toolchain_policy.py`  
**Commit:** `build(p1u): define sealed Nautilus 1.231 toolchain`

### Objective

Create candidate-only immutable policies for the exact v1.231 source build and runtime closure while keeping the v1.227 caches untouched.

### Implementation steps

- Pin Rust/Cargo 1.97.1 for the upstream Nautilus engine source build only; acquire official components into an absent private cache and verify all archive digests before materialization. Keep the native entry guard on its separately reviewed Rust 1.95.0 authority unless a later guard-source task explicitly amends that policy.
- Pin Cython 3.2.9, poetry-core 2.3.1, an exact reviewed setuptools >=83 wheel, exact compatible numpy/packaging/pip wheels, and all runtime dependencies required by the built wheel.
- Resolve and manifest the runtime dependency changes, including pandas, pyarrow 25+, fsspec 2026.2.0, pytz/tzdata, click, msgspec, portion, tqdm and uvloop where applicable. No open range is runtime authority.
- Retain CPython 3.12. Do not move the engine to 3.13/3.14 during this upgrade.
- Retain the current LLVM 22.1.3 policy for the first build attempt. If an LLVM change is required, stop and create a separately reviewed task amendment with exact evidence.
- Generate candidate policies without replacing the active policies. Cross-policy digests must bind provenance, source, Cargo.lock, pyproject and wheel manifests.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Policy tests reject Rust 1.95, Cython 3.2.4, mutable wheels, missing tzdata, extra wheels, wrong CPython tags and dependency versions outside the resolved closure.
- Offline cache verification uses no package index or package installer.
- Two independent manifest generations from the same bytes are byte-identical.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] Candidate engine-build, rollback engine-build and native-guard toolchains can coexist without path collision or authority confusion.
- [ ] Every build/runtime dependency is exact and hash-bound.
- [ ] No ambient rustup, global compiler, pip cache or site-packages can satisfy a missing candidate input.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U04 — Build and attest a side-by-side sealed v1.231 candidate closure

**Milestone:** MU2 — Candidate build  
**Depends on:** P1-U03  
**Parallel wave:** 3  
**Primary implementer subagent:** Runtime Isolation and Closure Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `scripts/build_nautilus_engine.py; scripts/materialize_nautilus_runtime_closure.py; services/job_worker/nautilus_closure.py; services/job_worker/engine_spawn.py only for schema-7 generic validation; tests/nautilus_upgrade/test_v1231_candidate_build.py; tests/nautilus_upgrade/test_v1231_closure_schema7.py; docs/implementation/p1-real-nautilus/upgrade/candidate-build-evidence.md`  
**Commit:** `build(p1u): produce sealed Nautilus 1.231 candidate`

### Objective

Produce an externally stored, schema-7, no-network CPython 3.12 closure for v1.231 without mutating or selecting it as the active engine.

### Implementation steps

- Generalize existing build/materialization scripts only where required; keep all existing v1.227 validation paths green.
- Build the v1.231 wheel from the approved source inside Bubblewrap with network namespace disabled, host filesystem read-only and only candidate staging writable.
- Build twice from fresh staging. Compare wheel contents, native-library inventory, normalized metadata and closure manifest. Document any non-deterministic wheel bytes and require a stable normalized authority digest rather than ignoring the difference.
- Materialize the exact CPython interpreter, stdlib, built wheel, runtime dependencies, launchers and native guard into an external private closure. Bind every file, target, size, mode and SHA-256.
- Add schema-7 verification while preserving schema-6 rollback support. The active worker profile must still point to 1.227 until P1-U08.
- Run the official cp312 wheel only in a separate disposable oracle environment to compare imports/version/native inventory. It is not runtime authority unless the ADR explicitly changes that decision.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- No network syscall or writable host path is available during build/runtime materialization.
- Schema-7 verifier rejects wrong runtime family, tag object, commit, source digest, dependency policy, native guard, file inventory and unexpected wheel.
- The v1.227 closure still verifies unchanged.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] Candidate closure exists beside rollback and is not active by default.
- [ ] Candidate manifest is fully provenance-bound and externally reproducible.
- [ ] No central verifier accepts a version range or 'latest' alias.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U05 — Run the complete CPython 3.12 import and direct API compatibility probe

**Milestone:** MU3 — Compatibility qualification  
**Depends on:** P1-U01, P1-U04  
**Parallel wave:** 4  
**Primary implementer subagent:** Nautilus Native API Compatibility Engineer — `gpt-5.6-terra`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Owned files:** `tests/nautilus_upgrade/test_v1231_import_probe.py; tests/nautilus_upgrade/test_v1231_api_contract.py; tests/fixtures/nautilus_upgrade/v1.231-api-probe.json; engines/nautilus/launcher/nautilus_v1231_probe.py; docs/implementation/p1-real-nautilus/upgrade/api-probe-evidence.md`  
**Commit:** `test(p1u): qualify Nautilus 1.231 direct APIs`

### Objective

Prove every currently invoked and P1-planned Cython-v1 API exists and behaves at the minimum required contract inside the real sealed candidate.

### Implementation steps

- Generate the probe from P1-U01's direct API contract; do not hand-select a smaller import list.
- Import every required module under `python3.12 -I -S` through the native entry guard and sealed dependency path.
- Construct config, currencies, venue, instrument, QuoteTick, Bar, custom FeeModel, FillModel, Strategy, engine and account. Exercise add_venue/add_instrument/add_strategy/add_data/run/get_result/cache/account/dispose.
- Record the 1.227 versus 1.231 `BacktestEngineConfig` defaults and prove the production fixture explicitly sets `load_state=False`, `save_state=False`, `run_analysis=False`, and logging bypass instead of depending on version defaults.
- Characterize the additive `get_result()` summary surface but keep it outside P1 accounting authority unless a later contract task explicitly admits fields.
- Record exact callable signatures, enum/object formatting and callback event class fields needed by P1. Reject dynamic fallback or broad `hasattr` compatibility shims in production code.
- Run a bounded zero-order and one-order smoke to prove the import surface is not merely importable but executable.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- The probe emits one canonical bounded record and no ambient path information.
- Delete or rename each required symbol in a fixture module and prove the generated contract test fails.
- The candidate imports only from the mounted closure; root/site/user packages cannot satisfy imports.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] Every direct API entry is `PASS`, or the program stops with an explicit adaptation task amendment.
- [ ] No unexplained signature or callback drift remains.
- [ ] The probe itself grants no production or network authority.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U06 — Execute the v1.228-to-v1.231 backtest and accounting regression campaign

**Milestone:** MU3 — Compatibility qualification  
**Depends on:** P1-U04, P1-U05  
**Parallel wave:** 4  
**Primary implementer subagent:** Backtest and Accounting Regression Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `tests/nautilus_upgrade/regressions/**; scripts/qualify_nautilus_v1231_regressions.py; docs/implementation/p1-real-nautilus/upgrade/release-regression-matrix.json; docs/implementation/p1-real-nautilus/upgrade/release-regression-evidence.md`  
**Commit:** `test(p1u): qualify Nautilus 1.231 regression fixes`

### Objective

Turn the release-note items relevant to Trading-Agent into executable candidate gates rather than relying on upstream prose.

### Implementation steps

- Add a reset campaign proving no duplicate account-state event and no loss of retained FX lookup for retained instruments.
- Add cash-account calculated-state and balance-invariant checks with exact Decimal output.
- Add NETTING close/reopen cycles that reuse native PositionId semantics and verify account-currency realized PnL exactly once.
- Add foreign-currency trade/PnL and multi-currency simulated-venue reset/FX sequencing fixtures, even though P1-A's active profile remains one BTCUSDT cash account.
- Add post-run analysis checks proving realized PnL is not duplicated.
- Add venue-registration rollback, non-positive leverage rejection, instrument re-registration/generated-ID collision and simulated exchange order/account status query scenarios.
- Add end-of-data/on_stop/latency-deferred ordering scenarios and classify Cython-v1 versus Rust-only behavior explicitly.
- Add a bounded Python-v1 throttler buffer scenario only if P1/P1-B touches that component; otherwise record `NOT_USED` with source proof.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Every relevant release item has a scenario, an explicit `NOT_USED`, or a documented upstream-only boundary.
- Campaign fails on skip, xfail, missing evidence or duplicate scenario ID.
- All money/price/quantity comparisons are exact Decimal/canonical string comparisons, never tolerances over float.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] No candidate panic, duplicate accounting fact or unexplained PnL drift.
- [ ] P1 long/flat single-currency invariants remain strict despite broader regression fixtures.
- [ ] Any upstream defect discovered is recorded as a blocker or version-pinned local patch with independent review; it is not silently masked.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U07 — Dual-run v1.227 and v1.231 and approve the semantic drift ledger

**Milestone:** MU4 — Promotion qualification  
**Depends on:** P1-U05, P1-U06  
**Parallel wave:** 5  
**Primary implementer subagent:** Determinism and Semantic Parity Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `tests/nautilus_upgrade/test_dual_runtime_semantic_parity.py; scripts/compare_nautilus_runtime_versions.py; docs/implementation/p1-real-nautilus/upgrade/approved-drift-ledger.json; docs/implementation/p1-real-nautilus/upgrade/dual-run-evidence.md`  
**Commit:** `test(p1u): compare Nautilus 1.227 and 1.231 semantics`

### Objective

Compare known-good rollback and candidate using the same hash-bound inputs and distinguish legitimate upstream fixes from unexplained execution drift.

### Implementation steps

- Run the current zero-order, all eight execution-simulation scenarios, paper-compatibility probe and planned P1 long→flat fixtures against both sealed closures.
- Normalize only run-custody values explicitly excluded from semantic identity. Never normalize price, quantity, fee, fill/order count, event order, account balance, PnL, position or error classification.
- Produce raw batch hashes, semantic digests, final state records and a field-level diff for every scenario.
- Classify differences as `NONE`, `EXPECTED_UPSTREAM_FIX`, `APPROVED_CONTRACT_CHANGE`, or `UNEXPLAINED_BLOCKER`. Each non-none difference requires release-note/source/test evidence and fresh Sol approval.
- Repeat candidate runs at least three times from fresh processes and require stable semantic digest.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Mutation tests prove the comparator detects changed fill price, quantity, fee, event order, PnL, account balance, count and error class.
- The approved-drift ledger is canonical, versioned and empty of `UNEXPLAINED_BLOCKER` before promotion.
- Rollback and candidate execution occur in separate closures/processes with no shared writable state.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] Candidate business semantics are equal or improved with explicit evidence.
- [ ] No approval is based only on aggregate completion counters.
- [ ] Three candidate runs are deterministic at the semantic layer.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-U08 — Promote v1.231 as the P1 baseline and retain v1.227 rollback authority

**Milestone:** MU4 — Promotion qualification  
**Depends on:** P1-U07  
**Parallel wave:** 6  
**Primary implementer subagent:** Engine Baseline Promotion Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `docs/implementation/p1-real-nautilus/upgrade/p1-engine-baseline-receipt.json; tests/nautilus_upgrade/test_v1231_promotion.py; docs/implementation/p1-real-nautilus/task-ledger.md; docs/implementation/p1-real-nautilus/agent-task-matrix.csv`
**Commit:** `docs(p1u): approve Nautilus 1.231 P1 baseline`

### Objective

Approve the fully qualified candidate as the baseline for P1-A/B only. Do not switch global or legacy Phase4 runtime authority; the existing 1.227 schema-6 profiles remain unchanged.

### Implementation steps

- Require P1-U04C, P1-U05, P1-U06 and P1-U07 receipts to bind the same generation ID, generation digest and candidate closure digest.
- Record `p1-engine-baseline-receipt.json` with status `P1_BASELINE_APPROVED`, scope `P1_A_AND_P1_B_ONLY`, decision `PROMOTE_1_231_FOR_P1`, and target `p1_product_closure_schema: 8`.
- Hash-bind the existing job-worker loader and both active schema-6 Phase4 policies, and prove they remain byte-identical to the accepted 1.227 baseline.
- Keep candidate activation, global promotion, production, network-trading and live authority false. U08 changes no runtime policy, loader, engine artifact or external closure.
- Advance only P1-00 to `READY`; P1-15/P1-16 later derive the schema-8 P1 product closure from the approved schema-7 baseline.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Mixed G1/U04C/U05/U06/U07 receipts fail closed.
- Any change to the retained 1.227 loader or schema-6 policies fails closed.
- No version range, branch name, moving tag or ambient path can select the engine.
- [ ] Run `make check-p0-maintainability` and the relevant Nautilus source/closure boundary checks.
- [ ] Run the existing regression suite for every modified shared file.
- [ ] Inspect `git diff --check`, the complete diff, generated hashes and exact candidate SHA before commit.

### Acceptance checklist

- [ ] Program decision is `PROMOTE_1_231_FOR_P1` with status `P1_BASELINE_APPROVED` and scope `P1_A_AND_P1_B_ONLY`.
- [ ] Legacy Phase4 profiles remain on exact Nautilus 1.227/schema 6, byte-identical to the accepted baseline.
- [ ] P1 product closure target is schema 8; U08 creates no schema-7 adapter or global runtime switch.
- [ ] All existing engine qualification tests remain green or have separately approved evidence-based updates.
- [ ] P1 implementation tasks now depend on this accepted promotion SHA.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records accepted commit SHA, external authority digest and fresh verification evidence.

### Stop / rollback conditions

- Stop on any unexplained API, callback, event, account, PnL, dependency, provenance or closure drift.
- Stop if green tests require accepting a range/moving target, using ambient authority, weakening a fail-closed rule, skipping a scenario, or overwriting the 1.227 rollback closure.
- Revert/cherry-pick out the task if its accepted parent changed, a reviewer finds responsibility leakage, or evidence was collected against a different candidate digest.

## P1-00 — Pin the promoted v1.231 P1 baseline and create the implementation worktree topology

**Milestone:** M0 — Program bootstrap  
**Depends on:** P1-U08  
**Parallel wave:** 7  
**Primary implementer subagent:** Integration Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `docs/superpowers/plans/2026-08-16-p1-real-nautilus-v1.231.md; docs/implementation/p1-real-nautilus/baseline.md; .gitignore only if worktree directory policy requires it`
**Commit:** `docs(p1): pin real Nautilus program baseline`

### Objective

Establish one auditable implementation starting point from the accepted P1-U08 promotion SHA and a no-overlap topology before productization changes.

### Implementation steps

- Verify that the working repository is exactly `nam176hermes/Trading-Agent`, that `main` or the accepted integration base resolves to the reviewed Trading-Agent baseline plus the accepted P1-U08 promotion SHA, and that the tree is clean. Refuse to proceed from a different tree unless the integration lead records the new SHA and re-runs the baseline gates.
- Create a dedicated integration worktree for branch `p1/real-nautilus-engine`. Every implementation task uses a fresh child worktree created from the latest accepted integration commit; no subagent edits the primary checkout or another agent's worktree.
- Record tool versions and exact promoted engine lineage: root Python/uv, CPython 3.12 authority, Bubblewrap, engine-build Rust/Cargo 1.97.1, separately pinned native-guard Rust/Cargo authority, Cython 3.2.9, promoted baseline closure schema 7 and target P1 product closure schema 8, Node/npm, and Codex CLI/app version. Record capability absence as `DEFERRED`; never install or mount unreviewed authority merely to turn a gate green.
- Run and capture the existing P0/P0-M1 portable baseline. Store command, exit code, commit, tree SHA, and artifact hashes in the baseline document. Do not copy transient logs into the repository.
- Create a task-status table with states `NOT_STARTED`, `IN_PROGRESS`, `REVIEW`, `ACCEPTED`, `BLOCKED`, `DEFERRED`. Only the integration lead changes a task to `ACCEPTED` after both reviews and verification.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- `git status --short` is empty and `git rev-parse HEAD` equals the recorded baseline.
- `make check-p0-baseline` and `make check-p0-maintainability` pass.
- `make ci-portable NONINTERACTIVE=1` passes, or a pre-existing authority-only limitation is recorded without reclassification.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Baseline SHA and tree SHA are recorded.
- [ ] Integration branch and task worktree naming convention are documented.
- [ ] No source or generated contract changed.
- [ ] All later task branches are required to name their accepted parent SHA.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-01 — Characterize the existing real Nautilus path and the pinned upstream API

**Milestone:** M0 — Program bootstrap  
**Depends on:** P1-00  
**Parallel wave:** 8  
**Primary implementer subagent:** Repository Cartographer + Nautilus API Researcher — `gpt-5.6-terra`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Owned files:** `docs/implementation/p1-real-nautilus/current-runtime-characterization.md; docs/implementation/p1-real-nautilus/upstream-api-map.md; tests/p1_nautilus/test_existing_runtime_characterization.py`  
**Commit:** `test(p1): characterize pinned Nautilus runtime APIs`

### Objective

Prove what already works and pin the exact v1.231.0 API surfaces P1 will use, so implementation does not rebuild or guess.

### Implementation steps

- Treat P1-U01/P1-U05 as accepted upstream authority. Re-characterize only the exact P1 product path, and fail if implementation tries to bypass the generated direct API contract.
- Record runtime family `cython-v1`, engine version 1.231.0, upstream commit, promoted schema-7 baseline digest and target schema-8 P1 product lineage in every characterization packet.
- Include a forward seam map for a future `runtime_v2` implementation, but do not import, install or execute v2 in P1.
- Map the existing request path from `EngineBacktestPayload` through `BacktestEngineAuthorityFactory`, `EngineSpawnProvider`, Bubblewrap/native guard, the sealed CPython 3.12 launcher, `EngineResultValidator`, durable event ingestion, and job finalization.
- Characterize the real `BacktestEngine` execution-simulation path: venue/account setup, instrument source, QuoteTick/Bar ordering, strategy callbacks, fill and fee model behavior, cache/account reads, deterministic IDs, and disposal.
- Against the exact upstream tag `v1.231.0` / release commit, identify constructors and callback behavior for `CurrencyPair`, `Currency`, `Venue`, `BarType`, `QuoteTick`, `Bar`, `BacktestEngine`, `BacktestEngineConfig`, `FillModel`, `FeeModel`, `Strategy`, and the order/fill/position events used by P1.
- Run a minimal probe inside the sealed runtime to establish which callbacks actually fire for a market buy and a flattening sell. Do not invent `OrderAccepted` or position callbacks if v1.231.0 does not emit them in this configuration.
- Produce an API decision table: `USE`, `WRAP`, `COPY/ADAPT`, or `DO_NOT_USE`. Include exact upstream file/symbol references for any copied pattern.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Characterization test proves the existing launcher still imports and runs real `BacktestEngine` rather than the Decimal oracle.
- Callback probe output is canonical, bounded, and stored only as a test golden fixture.
- API map includes version, release commit, Python ABI, and every symbol P1 plans to call.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] No production behavior changes.
- [ ] The callback matrix is evidence-based.
- [ ] Every later task can cite a pinned API rather than use dynamic/introspective fallback.
- [ ] Any unsupported assumption becomes a plan amendment, not an implementation workaround.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-02 — Lock architecture, threat model, ownership boundaries, and growth budgets

**Milestone:** M0 — Program bootstrap  
**Depends on:** P1-00, P1-01  
**Parallel wave:** 8  
**Primary implementer subagent:** Architecture Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `docs/adr/ADR-P1-REAL-NAUTILUS-VERTICAL-SLICE.md; docs/implementation/p1-real-nautilus/threat-model.md; docs/implementation/p1-real-nautilus/module-ownership.md; docs/implementation/p1-real-nautilus/growth-budget.json; scripts/check_p1_nautilus_boundaries.py; tests/governance/test_p1_nautilus_boundaries.py`  
**Commit:** `arch(p1): lock real Nautilus module boundaries`

### Objective

Make module responsibilities executable constraints before implementation begins.

### Implementation steps

- Add a version-family boundary: `runtime_v1` may implement the engine-neutral session interface, while future `runtime_v2` must be a separate package and closure. Mixed v1/v2 imports in one process are forbidden.
- Make the final-v1 support posture explicit: P1 ships on 1.231.0, but no new generic contract may expose a Cython-specific object or identifier.
- Adopt the low-level `BacktestEngine` for P1 because it is already qualified and gives exact control over hash-bound JSON/JSONL input. Define a `BacktestSession` seam so a future `BacktestNode`/Parquet adapter replaces only assembly and data loading.
- Declare that only `engines/nautilus/runtime_v1/**` may import `nautilus_trader`. Root Python 3.11 packages, API code, ledger, reducer, and worker orchestration must remain engine-neutral.
- Freeze `engines/nautilus/launcher/nautilus_backtest.py` and `target_portfolio_strategy.py` for net growth. They remain qualification references until P1 cutover. New P1 responsibility must go to new modules.
- Define ownership: generic wire contracts; P1 profile contracts; isolated runtime; worker authority; raw event ledger; engine-to-portfolio projection; portfolio reducer; paper runtime. Explicitly prohibit strategy/research code, provider credentials, network clients, live adapters, and dashboard concerns from the engine slice.
- Threat-model digest substitution, symlink/inode replacement, closure drift, request/result misbinding, event sequence gaps, duplicate identities, semantic-digest instability, look-ahead timestamps, precision overflow, target oversizing, unsupported shorting, false success after partial output, restart ambiguity, and paper split-brain.
- Add a source checker that rejects root imports of `nautilus_trader`, new network/client imports in the isolated runtime, profile strings duplicated outside the profile policy module, and net growth of the old launchers.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Boundary checker fails on seeded forbidden imports and duplicate profile definitions.
- Boundary checker fails when old launcher bytes grow.
- Boundary checker allows tests and explicitly approved upstream characterization fixtures without granting runtime authority.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] ADR is approved before P1-03 starts.
- [ ] One module has one responsibility and one owner.
- [ ] Future BacktestNode adoption has a named seam.
- [ ] Live/network authority remains structurally impossible in P1.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-03 — Define strict P1 backtest input artifact contracts

**Milestone:** M1 — Contracts  
**Depends on:** P1-02  
**Parallel wave:** 9  
**Primary implementer subagent:** Contract Engineer — `gpt-5.6-sol`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Owned files:** `packages/nautilus_runtime_contracts/__init__.py; packages/nautilus_runtime_contracts/artifacts.py; packages/nautilus_runtime_contracts/versions.py; tests/nautilus_runtime_contracts/test_artifacts.py; tests/fixtures/p1_nautilus/contracts/**`  
**Commit:** `feat(p1): define real Nautilus artifact contracts`

### Objective

Turn the four existing `RunBacktest` artifact references into closed, versioned, engine-profile-specific documents.

### Implementation steps

- Make engine state persistence explicit in the artifact contract: `load_state=false`, `save_state=false`, `run_analysis=false`, logging bypass, and no dynamic `shutdown_on_error` capability probing.
- Create four immutable strict models: `P1EngineConfigurationV1`, `P1InstrumentCatalogV1`, `P1TargetScheduleV1`, and `P1MarketDataManifestV1`. Preserve the generic `RunBacktest` command; profile semantics live in these hash-bound artifacts.
- Engine configuration is exactly one CASH/NETTING simulated venue, fixed starting USDT balance, deterministic fill settings, fixed fee policy, `bar_execution=false`, no leverage, no shorting, no network, explicit `load_state=false`, `save_state=false`, `run_analysis=false`, logging bypass, and no dynamic `shutdown_on_error` capability probing.
- Instrument catalog is exactly one `crypto_spot` BTC/USDT instrument for the acceptance slice, but fields are generic enough to add another spot pair under a future schema: canonical IDs, base/quote currencies, price/size precision, tick/step, min quantity/notional, venue, and provenance digest.
- Target schedule contains one or more existing `EngineTargetPortfolio` values ordered by `effective_at`. P1 accepts only weights in `[0,1]`, one instrument, and long/flat transitions. Empty schedules, duplicate times, duplicate target IDs, negative weights, and total weight above one fail.
- Market-data manifest binds one canonical JSONL artifact, row count, first/last timestamps, quote/bar pair policy, timeframe `1m`, timestamp-on-close rule, data digest, catalog digest, and normalization version. Raw rows remain a separate hash-bound `market_data` artifact.
- Use decimal strings only. No float is accepted at any boundary. Unknown fields fail closed.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Write failing tests first for every unknown field, duplicate, invalid decimal, negative target, oversized weight, unsupported venue/account/product, wrong digest, timeframe mismatch, and timestamp-window violation.
- Golden canonical JSON round-trips byte-identically.
- Generated JSON Schema contains closed enums and canonical decimal constraints.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] The existing `EngineBacktestPayload` and `RunBacktest` envelope need no client-controlled profile selector.
- [ ] All four artifacts have explicit schema versions and maximum sizes.
- [ ] BTCUSDT acceptance fixtures are valid; unsupported shapes fail before spawn.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-04 — Define the P1 engine event vocabulary, state machine, and semantic digest

**Milestone:** M1 — Contracts  
**Depends on:** P1-02  
**Parallel wave:** 9  
**Primary implementer subagent:** Event Protocol Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `packages/nautilus_runtime_contracts/events.py; packages/nautilus_runtime_contracts/semantic.py; packages/nautilus_runtime_contracts/state_machine.py; tests/nautilus_runtime_contracts/test_events.py; tests/nautilus_runtime_contracts/test_semantic_projection.py`  
**Commit:** `feat(p1): define canonical Nautilus event stream`

### Objective

Define a production-shaped, fully validated event stream instead of one aggregate completion event.

### Implementation steps

- Bind engine lineage without making custody noise part of semantic equivalence: run-start/completion metadata must expose runtime family, exact engine version, upstream commit and closure digest, while semantic digest rules remain explicit.
- Define provisional event types backed by P1-01 callback evidence: run started, target accepted, target quantity planned, native order lifecycle events actually observed, fill, position observation/change, account observation, and run completed.
- Each event type gets an exact attribute schema. Decimal values are canonical strings; timestamps use canonical UTC text; identifiers have bounded grammars. An event with an extra/missing/wrong-type attribute fails.
- Separate event origin: `NAUTILUS_CALLBACK`, `NAUTILUS_CACHE_OBSERVATION`, or `CONTROL_PLANE`. Never label a synthesized summary as a native callback.
- Define stream transitions and cardinality. A run begins once, completes once, cannot complete before target processing, cannot report a fill before an order, and cannot emit events after completion. Optional upstream callbacks are modeled explicitly rather than fabricated.
- Keep envelope `event_time` bound to request custody time and sequence; put historical simulation time in a typed `simulation_time` attribute. This preserves the worker's request-time authority rule while retaining deterministic historical chronology.
- Define a semantic projection that excludes message IDs, job/attempt IDs, request custody time, source checkout path, native random identifiers, and other run-custody-only values, while including data/config digests, target facts, business order/fill facts, final cash/position/fees/PnL, and event order.
- Define deterministic UUIDv5 rules for emitted envelope IDs. Raw events retain exact envelope bytes; semantic digest is a separate product and may never replace raw identity validation.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- State-machine tests reject every illegal transition, sequence gap, post-completion event, duplicate completion, and incompatible final counters.
- Semantic digest is stable across different run IDs/custody timestamps but changes for price, quantity, fee, target, data digest, or event-order changes.
- Event schemas reject floats, NaN/Infinity text, unknown attributes, and unbounded identifiers.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Event protocol is versioned `nautilus-p1-event-stream-v1`.
- [ ] Raw identity and semantic equivalence are separate concepts.
- [ ] The event vocabulary is based on proven v1.231.0 behavior.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-05 — Generate a stdlib-only sealed protocol module and golden fixtures

**Milestone:** M1 — Contracts  
**Depends on:** P1-03, P1-04  
**Parallel wave:** 10  
**Primary implementer subagent:** Code Generation Engineer — `gpt-5.6-terra`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Owned files:** `scripts/generate_nautilus_p1_protocol.py; engines/nautilus/runtime_v1/generated_protocol.py; schemas/nautilus-p1-*.schema.json; tests/contracts/test_generate_nautilus_p1_protocol.py; tests/fixtures/p1_nautilus/golden/**; Makefile`  
**Commit:** `build(p1): generate sealed Nautilus protocol`

### Objective

Eliminate hand-maintained drift between root Pydantic contracts and the isolated runtime that cannot import root packages.

### Implementation steps

- Generate a stdlib-only module containing schema/version constants, allowed field sets, decimal/timestamp/identifier regexes, event attribute layouts, and canonical JSON helpers. It must not contain Pydantic or root imports.
- Generate JSON Schemas and positive/negative golden fixtures from the root contract source. Generated output is deterministic and checked by `--check`.
- Add explicit headers stating generated files must not be hand-edited. The generator sorts all keys/records and emits a trailing newline.
- Add `make generate-p1-nautilus-contracts` and `make check-p1-nautilus-contracts`; wire the check into portable CI exactly once after contract generation checks.
- Do not generate runtime execution logic or security decisions. Only grammar and version data are generated.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- First change a source constant and prove `--check` fails.
- Run generator twice and assert byte-identical output.
- Import generated module under isolated `python3.12 -I -S` with only stdlib available.
- Golden negative fixtures fail in both root models and generated validator with the same stable error code class.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] One source of truth for grammar.
- [ ] Sealed runtime has no root dependency.
- [ ] Portable CI detects stale generation.
- [ ] No new path reaches host authority.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-06 — Scaffold the modular sealed runtime and closed CLI

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-05  
**Parallel wave:** 11  
**Primary implementer subagent:** Runtime Isolation Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/__init__.py; engines/nautilus/runtime_v1/main.py; engines/nautilus/runtime_v1/bootstrap.py; engines/nautilus/runtime_v1/diagnostics.py; tests/p1_nautilus/test_runtime_bootstrap.py; tests/p1_nautilus/test_runtime_cli_source.py`  
**Commit:** `feat(p1): scaffold sealed Nautilus runtime`

### Objective

Create a new runtime entrypoint without adding responsibility to the large qualification launcher.

### Implementation steps

- The bootstrap must assert `nautilus_trader.__version__ == '1.231.0'` (or the exact supported equivalent) and exact schema-8 P1 product lineage before importing product modules.
- Implement one exact CLI profile: request path/sidecar plus mounted artifact paths supplied by the reviewed native guard argv. No arbitrary module, class, config path, output path, environment override, or shell token is accepted.
- Move the existing clean-entry checks into a small P1 bootstrap appropriate for the new file inventory: CPython 3.12, `-I -S`, safe path, ignored environment, no user site, expected `/proc/self/cmdline`, expected entrypoint, and no ambient cwd dependency.
- Reserve stdout exclusively for canonical `EngineEventEnvelope` JSONL. Diagnostics go to bounded ASCII-safe stderr with stable reason codes and no paths, secrets, Python reprs, or native object dumps.
- Use a top-level lifecycle that either emits a complete validated stream and exits zero or exits nonzero. Never emit `RunCompleted` after an exception; never catch `BaseException` merely to turn it into success.
- Keep `main.py` as composition only. Parsing, instrument construction, data loading, planning, strategy, event projection, and runner logic belong to dedicated modules.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- CLI rejects extra args, env-based overrides, unisolated Python, wrong entrypoint, non-regular request/sidecar, malformed digest, and stdout contamination.
- Error messages remain bounded and path-free.
- Boundary checker confirms no root import and no network/client modules.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] New entrypoint is under 200 logical lines.
- [ ] Old qualification launcher blob is unchanged.
- [ ] CLI has one supported execution profile and no ambient authority.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-07 — Implement descriptor-safe request and artifact loading

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-05, P1-06  
**Parallel wave:** 11  
**Primary implementer subagent:** Runtime Security Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/input_loader.py; tests/p1_nautilus/test_input_loader.py; tests/p1_nautilus/test_input_loader_adversarial.py`  
**Commit:** `feat(p1): seal runtime input loading`

### Objective

Validate the exact command and four mounted artifacts without pathname races or schema ambiguity.

### Implementation steps

- Open all files with `O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK`, require regular files, enforce exact size ceilings, read through descriptors, and verify SHA-256 before JSON parsing.
- Bind artifact mount filenames to the references in `RunBacktest`; reject duplicate artifact identities, mismatched media type, missing files, extra mounted artifact records, and command-window mismatch.
- Parse the generated grammar with duplicate-key detection. Reject BOMs, non-UTF-8, non-canonical numbers, floats, NaN/Infinity, unknown keys, and non-canonical timestamp spellings.
- Return immutable stdlib dataclasses/tuples. Downstream modules receive values, never authority-bearing paths or open mutable files.
- Re-stat/re-digest where the threat model requires post-read identity proof. If a pathname is replaced after descriptor acquisition, either continue only with the pinned descriptor or fail with the reviewed stable code; never silently switch objects.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Adversarial tests cover symlinks, FIFO/device, hardlink count, inode replacement, truncation, oversized file, digest mismatch, duplicate JSON keys, Unicode confusables, and TOCTOU swaps.
- Each failure produces nonzero exit and no stdout.
- Positive fixture yields identical immutable values across repeated reads.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Every consumed byte is hash-bound.
- [ ] No downstream module can reopen an input path.
- [ ] Input failure cannot produce a partial accepted event stream.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-08 — Build catalog-driven Nautilus currencies and instrument

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-01, P1-03, P1-05, P1-06  
**Parallel wave:** 11  
**Primary implementer subagent:** Nautilus Adapter Engineer — `gpt-5.6-sol`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/instrument_factory.py; tests/p1_nautilus/test_instrument_factory_source.py; tests/p1_nautilus/test_instrument_factory_native.py; docs/implementation/p1-upstream-provenance.md`  
**Commit:** `feat(p1): construct Nautilus instrument from catalog`

### Objective

Remove `TestInstrumentProvider` from the production-shaped path and construct the instrument from the hash-bound catalog.

### Implementation steps

- Use the v1.231 API contract and regression fixtures. Add a re-registration negative test so duplicate/generated-ID behavior cannot leak into product runtime assembly.
- Construct base/quote currencies and one `CurrencyPair`/spot instrument using exact v1.231.0 APIs characterized in P1-01. Pin all constructor arguments; do not use reflection, signature probing, or version fallbacks.
- Validate that Nautilus-rendered instrument ID, venue, symbol, product type, currency codes, precisions, increments, limits, and settlement currency equal the canonical catalog.
- Use canonical Decimal-to-Price/Quantity conversion helpers that reject out-of-range values before native construction. Never round a price or quantity without the catalog's explicit tick/step rule.
- Copy/adapt the smallest upstream constructor pattern if that is safer than reconstructing from memory. Record upstream tag, commit, path, symbol, and local divergence in the provenance document.
- Keep the acceptance profile BTCUSDT/BINANCE but do not hard-code via test-kit provider. The profile restriction is validated against catalog values.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Native test proves the resulting ID is exactly `BTCUSDT.BINANCE` and fields match the catalog.
- Reject wrong currency precision, zero/negative increments, min/max inconsistency, unsupported product, unknown currency, over-precision, and values outside Nautilus range.
- Source test proves `nautilus_trader.test_kit` is absent from `runtime_v1`.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] No test provider in production runtime.
- [ ] Instrument is entirely derived from hash-bound input.
- [ ] Upstream copying is recorded for maintenance, not license gating.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-09 — Implement deterministic QuoteTick/Bar market-data conversion

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-03, P1-05, P1-06, P1-07, P1-08  
**Parallel wave:** 11  
**Primary implementer subagent:** Market Data Semantics Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Owned files:** `engines/nautilus/runtime_v1/market_data_loader.py; tests/p1_nautilus/test_market_data_loader.py; tests/p1_nautilus/test_market_data_native.py`  
**Commit:** `feat(p1): load deterministic Nautilus market data`

### Objective

Convert canonical JSONL into monotonic native data without look-ahead or hidden execution assumptions.

### Implementation steps

- Add exact timestamp/order tests covering the 1.231 data-path behavior and the upstream shutdown/on_stop ordering clarification; quote-before-bar remains a project invariant.
- Validate exactly one quote/bar pair per sequence. Quote timestamp must be no later than the bar decision timestamp; rows are strictly ordered and fall within the command window.
- Treat the canonical bar timestamp as close time and set `ts_init` accordingly. Do not use an opening timestamp as initialization time. This rule is explicit because Nautilus execution timing depends on `ts_init`.
- Emit `QuoteTick` immediately before its paired `Bar` at the same deterministic initialization timestamp. Configure venue `bar_execution=false`; L1 quote state drives execution while the bar triggers the target strategy.
- Convert all prices/quantities through catalog precision helpers. Reject crossed quotes, nonpositive OHLC, invalid OHLC ranges, negative volume, timestamp duplicates, gaps forbidden by the manifest, and row/catalog digest mismatch.
- Load all data once for the P1 bounded slice. Expose a `MarketDataBatch` seam so a later `BacktestNode` or iterator adapter can replace only this module.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Golden native conversion asserts exact object order, IDs, price/size text, and nanosecond timestamps.
- Look-ahead tests fail when quote time follows decision/bar time or `ts_init` uses bar open.
- Mutation tests change one row and prove both row digest and semantic run digest change.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Input order is deterministic.
- [ ] Execution is quote-driven, not ambiguous OHLC path execution.
- [ ] Timestamp convention is documented and enforced.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-10 — Implement the long/flat target planner

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-03, P1-05, P1-06, P1-08, P1-09  
**Parallel wave:** 11  
**Primary implementer subagent:** Execution Planning Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/target_planner.py; tests/p1_nautilus/test_target_planner.py`  
**Commit:** `feat(p1): plan long-flat target quantities`

### Objective

Translate an upstream risk-approved target weight into an exact executable quantity without strategy intelligence or leverage.

### Implementation steps

- Reject zero/negative leverage and any candidate configuration that depends on v1.231's improved validation rather than enforcing the stricter project contract first.
- At each target effective time, derive target notional from current account equity and canonical target weight. For buys use the current ask; for flattening use current position quantity. No future bar/quote may influence sizing.
- Quantize down to the instrument step so execution never exceeds the target. Validate min quantity, min notional, available cash, fee reserve, and maximum absolute weight. A target that cannot satisfy minimums becomes an explicit no-order planning event, not a zero-quantity fake fill.
- Support only `current >= 0` and `target >= 0`. Any short, leverage, negative balance, cross-instrument target, or target above one fails before order submission.
- Return a pure immutable plan containing target ID, source signal IDs, effective time, current quantity, target quantity, delta, side, price basis, notional, and reason. It does not submit orders or read ambient state.
- Use local high-precision Decimal context and canonical text. Never use binary float.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Table tests cover zero→long, long→larger, long→smaller, long→flat, already-at-target, insufficient cash, fee reserve, min notional, step rounding, and precision limits.
- Property tests prove planned long quantity never exceeds the target notional or available cash.
- Negative weight/short and cross-instrument plans fail with stable codes.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Planner is pure and independently testable.
- [ ] No moving-average/alpha logic exists in engine code.
- [ ] Every order can be traced to an `EngineTargetPortfolio` and risk-approved signal IDs.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-11 — Implement the target-driven Nautilus strategy state machine

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-08, P1-09, P1-10  
**Parallel wave:** 12  
**Primary implementer subagent:** Nautilus Strategy Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/target_strategy.py; tests/p1_nautilus/test_target_strategy_source.py; tests/p1_nautilus/test_target_strategy_native.py`  
**Commit:** `feat(p1): execute canonical targets in Nautilus`

### Objective

Submit real Nautilus orders from target plans while keeping execution policy finite and auditable.

### Implementation steps

- Characterize and test pre-stop fills, `on_stop` ordering and pending-order state so the strategy never creates a false terminal event around engine shutdown.
- Create one sealed strategy class and frozen config. Subscribe only to the approved bar/quote types for the configured instrument. No dynamic imports, timers from wall clock, network clients, file I/O, or arbitrary callbacks.
- On the first eligible bar at or after a target's effective time, read current quote/account/position state, call the pure planner, and submit at most one order for that target. Track target IDs and prohibit duplicate submission.
- Use the exact order type selected by P1-01 characterization—prefer a market order settled against the preceding L1 quote unless evidence shows a deterministic incompatibility. Do not precompute or force fill prices in the strategy.
- Maintain explicit states such as `WAITING_FOR_TARGET`, `ORDER_WORKING`, `TARGET_REACHED`, `EXIT_ONLY`, `COMPLETED`, `FAILED`. A new target cannot race a working order; P1 uses a serial target schedule.
- Capture native callbacks into an event collector through a narrow interface. The strategy never writes JSONL directly and never declares final success.
- On stop, do not open exposure. P1 run completion requires schedule policy to reach its declared final state; a non-flat final target may remain open by design, but state and cash must be observed.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Source tests reject forbidden imports and multiple direct output channels.
- Native tests prove real order submission/fill for zero→long and long→flat.
- Duplicate bar/target callbacks do not duplicate orders.
- Rejection or inconsistent callback order puts the strategy in failed state and prevents completion.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Actual Nautilus orders/fills drive state.
- [ ] One target produces no more than one active order in P1.
- [ ] Strategy logic is target execution only.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-12 — Assemble and run the real BacktestEngine session

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-07, P1-08, P1-09, P1-10, P1-11  
**Parallel wave:** 12  
**Primary implementer subagent:** Backtest Runtime Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/session.py; engines/nautilus/runtime_v1/backtest_runner.py; tests/p1_nautilus/test_backtest_runner_native.py`  
**Commit:** `feat(p1): run production-shaped Nautilus backtest session`

### Objective

Create one production-shaped session that uses the pinned engine, input-built instrument, real matching, fees, strategy, cache, account, and disposal.

### Implementation steps

- Instantiate `BacktestEngineConfig` with explicit `load_state=False`, `save_state=False`, `run_analysis=False` and logging bypass. Do not branch on `shutdown_on_error`; root/launcher failure handling remains authoritative.
- Add reset/dispose/re-registration tests derived from P1-U06: no duplicate account state, no generated-ID collision, no stale FX or simulated-venue state between sessions.
- Define `BacktestSessionFactory` and `BacktestSession` interfaces. `BacktestEngineSession` is the P1 implementation; a future BacktestNode adapter must fit the same input/output seam.
- Instantiate `BacktestEngine` with explicit `load_state=False`, `save_state=False`, `run_analysis=False` and logging bypass. Configure one BINANCE CASH/NETTING venue, deterministic IDs, starting balance, L1 book/fill behavior, fee model, and `bar_execution=false` from validated config. Do not probe or branch on `shutdown_on_error`; launcher/root failure handling remains authoritative.
- Add the catalog-built instrument, data, event collector, and target strategy. Sort data once if required, run exactly once, and always dispose in `finally`.
- After run, read real cache/account/portfolio state through pinned APIs only. Reject more than one instrument/account/position, unknown orders, unresolved working orders at completion, rejected orders not reflected in event state, or engine counters inconsistent with collected events.
- Do not call the existing root Decimal oracle to produce results. The oracle remains a qualification comparator only.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Native session test asserts that module/class identities come from the sealed v1.231.0 wheel.
- Run produces real order, fill, position, cash, fee and engine result evidence.
- Injected engine exception, strategy rejection, and dispose failure remain non-success; cleanup failure is attached without hiding the primary failure.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Session owns all Nautilus imports and lifecycle.
- [ ] Runner output is immutable observed state plus collected native facts.
- [ ] No test-kit instrument or scenario-specific execution plan.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-13 — Project native callbacks and observations into canonical EngineEvent JSONL

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-04, P1-05, P1-11, P1-12  
**Parallel wave:** 12  
**Primary implementer subagent:** Native Event Projection Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/event_collector.py; engines/nautilus/runtime_v1/event_projector.py; engines/nautilus/runtime_v1/jsonl_writer.py; tests/p1_nautilus/test_event_projector.py; tests/p1_nautilus/test_event_stream_native.py`  
**Commit:** `feat(p1): emit canonical Nautilus event stream`

### Objective

Emit a complete, deterministic, request-bound stream from actual engine activity.

### Implementation steps

- Project raw native facts without duplicating corrected post-run realized PnL. Callback facts and post-run observations must remain distinguishable and deduplicated by explicit business identity.
- Collector stores only bounded immutable native facts needed by the event protocol. It normalizes native Decimal/Price/Quantity values immediately and never retains arbitrary native objects past projection.
- Project each fact to an exact event schema. Include origin and native type. Use the request's correlation/causation/run/config/source authority and contiguous sequences starting at two.
- Derive message IDs deterministically from request message ID plus semantic event ordinal/key. Keep envelope event time custody-safe; include historical simulation time as an attribute.
- Write each canonical envelope as one JSON line only after the full stream has passed the local state-machine validator. Buffering is acceptable because P1 datasets/output are bounded; this prevents partial stdout from being mistaken for a complete run.
- Compute raw batch SHA and semantic stream digest separately. Include semantic digest and final counters in `RunCompleted`; validate that the completion event does not create a circular digest domain.
- Do not emit events for facts not observed. Cache/account snapshots use observation event names, not callback names.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Three runs with different request IDs/times produce different raw batch hashes but the same semantic digest.
- One changed quote, target, fee, fill quantity, or event order changes semantic digest.
- Sequence, causation, payload digest and canonical bytes validate with root contracts.
- Partial writer/error injection yields no accepted completion stream.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Full stream is canonical JSONL and <=4096 events.
- [ ] Every fill/order fact is traceable to actual engine evidence.
- [ ] Semantic equivalence is deterministic without weakening raw identity.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-14 — Finalize run consistency checks and stable failure diagnostics

**Milestone:** M2 — Isolated runtime  
**Depends on:** P1-12, P1-13  
**Parallel wave:** 12  
**Primary implementer subagent:** Runtime Reliability Engineer — `gpt-5.6-sol`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/final_state.py; engines/nautilus/runtime_v1/errors.py; engines/nautilus/runtime_v1/main.py; tests/p1_nautilus/test_final_state.py; tests/p1_nautilus/test_runtime_fail_closed.py`  
**Commit:** `feat(p1): prove Nautilus final-state consistency`

### Objective

Make successful completion mean the engine stream, cache, account, strategy, and target schedule agree exactly.

### Implementation steps

- Every terminal diagnostic and evidence record must include exact v1.231 release/closure lineage; a v1.227 result under the active profile is a stable provenance error.
- Build an immutable final-state record from real cache/account data: balances, position quantity and average, realized/unrealized PnL, commissions, order/fill counts, last market timestamp, and terminal strategy state.
- Cross-check collected fills/orders against cache counters and final position/cash. Require all decimals finite and in canonical currency. Detect working/pending orders, rejected target without failure, missing account, duplicate position, and target-state mismatch.
- Only after consistency passes, append account/position observations and completion. Any failure exits nonzero and produces no completion event.
- Define stable error families (`INPUT_INVALID`, `PROFILE_UNSUPPORTED`, `ENGINE_SETUP_FAILED`, `ENGINE_EXECUTION_FAILED`, `EVENT_PROJECTION_FAILED`, `FINAL_STATE_MISMATCH`, `OUTPUT_FAILED`) and bounded diagnostic rendering.
- Keep raw exceptions chained internally for tests, but do not expose paths or native reprs in operator output.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Fault injection mutates cache counters, fees, final quantity, event count, or terminal strategy state and proves completion is withheld.
- Diagnostics snapshot tests enforce stable code and bounded message.
- `main` emits exactly the prevalidated stream plus newline on success and no stdout on pre-output failure.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Completion is a proof, not a best-effort summary.
- [ ] False success after partial/inconsistent engine state is impossible.
- [ ] Failure classification is useful without leaking authority details.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-15 — Centralize engine profile policy and admit the P1 closure manifest

**Milestone:** M3 — Closure and worker  
**Depends on:** P1-02, P1-06, P1-14  
**Parallel wave:** 13  
**Primary implementer subagent:** Runtime Authority Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `services/job_worker/engine_profiles.py; services/job_worker/engine_spawn.py; services/job_worker/nautilus_closure.py; engines/nautilus/native_entry_guard/src/main.rs; engines/nautilus/*policy*.json; tests/job_worker/test_engine_profiles.py; tests/job_worker/test_engine_spawn.py; tests/job_worker/test_nautilus_closure.py; tests/native/test_nautilus_entry_guard.py`  
**Commit:** `feat(p1): attest real Nautilus backtest profile`

### Objective

Add one code-owned `real-backtest-v1` profile without duplicating security-critical strings or weakening existing profiles.

### Implementation steps

- Use closure manifest schema 8 for the P1 real-backtest/paper product profiles and runtime family `cython-v1`. Preserve schema 7 only for the named promoted baseline/qualification profiles and schema 6 only for the named rollback verifier; no profile may accept multiple generations.
- Create a small pure profile-policy module containing exact profile name, semantic profile, entrypoint, guarded argv, validator ID, timeout, command type, required artifact names, protocol version, and manifest generation.
- Refactor existing zero-order/simulation/paper-compat profile constants to consume this policy only where characterization proves no semantic change. Do not broaden accepted argv or manifest fields through generic prefix matching.
- Add manifest schema generation 8 that binds P1 protocol/event versions and the complete `runtime_v1` inventory. Keep exact engine version 1.231.0, upstream commit, CPython identity, native guard provenance, dependency import policy, and Bubblewrap profile.
- Extend the native Rust guard with the exact P1 guarded argv. It must still hand off only to `/usr/bin/python3.12 -I -S` and the reviewed entrypoint.
- Keep source/profile selection operator-owned. A job payload cannot request another profile.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- All previous closure/profile tests pass unchanged.
- Seeded argv variations, additional flags, alternative entrypoint, profile substitution, validator substitution, manifest downgrade/upgrade, or protocol mismatch fail.
- Profile policy has exactly one definition per string and source checker rejects duplicates.
- Native guard binary tests prove exact handoff.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] No existing profile semantics change.
- [ ] P1 closure is fully inventory- and protocol-bound.
- [ ] Central policy prevents future profile-string drift.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-16 — Materialize, build, and qualify the P1 sealed runtime closure

**Milestone:** M3 — Closure and worker  
**Depends on:** P1-15  
**Parallel wave:** 13  
**Primary implementer subagent:** Build and Supply-Chain Engineer — `gpt-5.6-sol`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `scripts/materialize_nautilus_runtime_closure.py; scripts/build_nautilus_engine.py; scripts/qualify_nautilus_sealed_imports.py; engines/nautilus/runtime-closure-policy.json; Makefile; tests/nautilus_backtest/test_sealed_import_qualification.py; tests/p1_nautilus/test_p1_closure_qualification.py`  
**Commit:** `build(p1): materialize sealed Nautilus runtime v1`

### Objective

Produce an immutable external closure containing the new modular runtime and prove that it runs offline under the existing sandbox.

### Implementation steps

- Reuse the accepted P1-U04 candidate rather than rebuilding from ambient inputs. Rebuild only through the approved source/toolchain caches and compare against the promoted closure digest.
- Extend materialization policy to copy every `runtime_v1` source/generated file as an individually hashed immutable mount. Do not package root modules or expose the checkout inside the sandbox.
- Continue using the promoted, source-built, provenance-bound v1.231.0 wheel and exact CPython 3.12/stdlib/ELF closure. The official upstream wheel remains a comparison oracle only. Do not upgrade Nautilus during P1.
- Build and attest the native entry guard with its separately reviewed native-guard Rust/LLVM toolchains. Generate product manifest v8 and artifact manifest deterministically.
- Run sealed import qualification proving stdlib-first dependency path, no user site/environment, no network namespace, no writable engine files, exact entrypoint, and the expected module origin from sealed wheels.
- Add `make build-p1-nautilus-runtime` and `make qualify-p1-nautilus-runtime`, parameterized only by explicit external authority paths.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Tamper every class of closure file, manifest field, mode, path, digest, wheel, interpreter, native guard, or policy and prove attestation fails.
- Successful native smoke emits a valid minimal P1 event stream from a tiny hash-bound fixture.
- Source-only CI defers native qualification when authority is absent; it never reports PASS.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] One reproducible closure digest is recorded.
- [ ] No network or ambient package import.
- [ ] Existing zero-order/simulation qualifications remain green.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-17 — Add the dedicated multi-event worker result validator

**Milestone:** M3 — Closure and worker  
**Depends on:** P1-04, P1-14, P1-15  
**Parallel wave:** 14  
**Primary implementer subagent:** Worker Boundary Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `packages/nautilus_runtime/result.py; services/job_worker/engine_results.py; tests/nautilus_runtime/test_result.py; tests/job_worker/test_engine_results_p1.py`  
**Commit:** `feat(p1): validate full Nautilus event batches`

### Objective

Accept only a complete P1 event stream bound to the exact `RunBacktest` request and artifact set.

### Implementation steps

- Validator metadata must bind v1.231 engine version, upstream commit, runtime family and exact schema-8 P1 product closure digest in addition to request/event authority.
- Create a root profile validator that parses the event schemas/state machine, verifies command/result payload digests, request attribution, contiguous sequence, event-time rule, config/source/producer identity, artifact digest binding, one completion, counters, final summary and semantic digest.
- Add a new allowlisted validator ID to `EngineResultValidator`. Unlike current zero-order/simulation validators, it accepts a bounded multi-event batch and delegates to the P1 validator.
- Keep the existing generic canonical JSONL parsing and sealing. Do not duplicate file-read/custody code.
- Reject unknown P1 event types, mixed profile events, an old aggregate completion inside a P1 stream, completion not last, semantic digest mismatch, and any state-machine violation.
- Return a typed validated result with batch hash, semantic digest, event count, final account/position summary, and events for durable ingestion.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Negative matrix covers truncated line, noncanonical JSON, duplicate IDs, sequence gap, wrong causation/run/config/source, extra event after completion, wrong counters, wrong final cash/position/fees, and semantic mismatch.
- Existing validator tests remain green.
- Validated bytes sealed by the worker equal the exact stdout bytes.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Worker never treats a partial or aggregate-only event as P1 success.
- [ ] Validation is profile-specific and fail closed.
- [ ] Sealed batch can be independently replayed.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-18 — Compose P1 authority, artifact resolver, and worker dependencies

**Milestone:** M3 — Closure and worker  
**Depends on:** P1-03, P1-15, P1-16, P1-17  
**Parallel wave:** 14  
**Primary implementer subagent:** Application Integration Engineer — `gpt-5.6-sol`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `services/job_worker/main.py; services/job_worker/engine_authority.py only if characterization demands a no-semantic-change extension; services/job_worker/engine_artifacts.py; apps/job_api/config.py or deployment composition modules; tests/job_worker/test_p1_composition.py; tests/job_api/test_engine_backtest_submission.py`  
**Commit:** `feat(p1): compose real Nautilus worker authority`

### Objective

Wire existing generic `RunBacktest` job authority to the P1 closure without adding client-controlled execution authority.

### Implementation steps

- Reuse `EngineBacktestPayload`, `BacktestEngineAuthorityFactory`, and the four artifact references. Do not add a payload `profile`, executable, module, output path, or engine version.
- Configure the worker with a `NautilusClosureAttestor` fixed to the P1 profile, `HashBoundArtifactResolver`, `EngineSpawnProvider`, P1 result validator, and durable event ingestor.
- Require all four artifact bindings to be external sealed 0400 files with exact identity/digest. Deployment config supplies bindings; API clients supply only references already accepted by the job contract.
- Keep legacy `BacktestPayload` and snapshot command registry separate. P1 must not route through `services/job_worker/command_registry.py`.
- Add a read-only submission/inspection fixture through the existing authenticated job API only if the API already exposes BACKTEST jobs. Do not add dashboard mutation or a public unauthenticated endpoint.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Composition fails when any one engine component is absent, wrong profile closure is supplied, artifact binding is missing, or API payload attempts to select executable/profile.
- Claim derives the exact expected `RunBacktest` envelope.
- Legacy backtest and snapshot routing remain unchanged.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Client cannot choose runtime authority.
- [ ] Existing claim fence/lease/safety preflight remains in force.
- [ ] No new direct subprocess path.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-19 — Complete durable event-ledger ingestion for the P1 stream

**Milestone:** M4 — Persistence and accounting  
**Depends on:** P1-17, P1-18  
**Parallel wave:** 15  
**Primary implementer subagent:** Ledger Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `packages/engine_event_ledger/** only where generic capability is missing; services/job_store/** repository adapter as required; tests/engine_event_ledger/test_p1_stream_ingestion.py; tests/job_worker/test_p1_event_ingestion.py`  
**Commit:** `feat(p1): persist Nautilus event stream atomically`

### Objective

Persist the exact validated stream atomically and make retries/restarts unambiguous.

### Implementation steps

- Reuse `StoredEngineEvent`, batch receipts, canonical bytes, digest checks, and contiguous sequence rules. Extend only generic limits/projections proven necessary for a multi-event P1 batch.
- Make ingestion idempotent for the same job/attempt/batch and conflict on same message ID or sequence with different bytes. No overwrite, delete, rollback, or 'last write wins'.
- Bind the job result to the accepted batch receipt before final success. If ingestion outcome is uncertain, reload and compare the durable receipt; otherwise block for reconciliation.
- Expose a run projection with event type counts, last sequence/digest, batch hash and semantic digest read from the validated completion—not recomputed from unchecked storage.
- Add restart tests using the current fake/Postgres repository paths that reload stored state and produce the same projection.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Crash/fault injection before append, during append, after append before receipt, and after receipt before job finalization.
- Same batch retry is idempotent; changed batch conflicts.
- Stored canonical bytes revalidate and replay with no gaps.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Job success cannot precede durable receipt.
- [ ] Uncertain write never triggers a second engine run automatically.
- [ ] Restart can distinguish no result, accepted result, and conflict.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-20 — Project engine events into canonical portfolio accounting entries

**Milestone:** M4 — Persistence and accounting  
**Depends on:** P1-04  
**Parallel wave:** 15  
**Primary implementer subagent:** Domain Projection Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `packages/engine_portfolio_projection/__init__.py; packages/engine_portfolio_projection/models.py; packages/engine_portfolio_projection/projector.py; packages/engine_portfolio_projection/validation.py; tests/engine_portfolio_projection/**`  
**Commit:** `feat(p1): project Nautilus events to portfolio entries`

### Objective

Keep generic event storage separate from domain accounting by introducing one pure, deterministic adapter.

### Implementation steps

- Consume only a root-validated P1 stream plus the exact instrument catalog and opening-account authority. Do not let `portfolio_reducer` parse generic event attributes.
- Map real fill events to canonical `OrderEvent`, `FillEvent`, and `PortfolioFillEntry` using existing domain and `nautilus_mappings` primitives where appropriate. Preserve execution ID, side, quantity, price, fee, liquidity semantics, instrument definition, account and strategy identity.
- Create deterministic portfolio event IDs from engine message IDs. Project opening balance once, fills in stream order, final mark/account observations as explicitly typed entries, and no synthetic execution.
- Reject duplicate fill business identity, inconsistent order/fill linkage, wrong currency, quantity sign mismatch, position jump not explained by fills, or final engine summary inconsistent with projected effects.
- Keep this package pure: no database, files, clock, Nautilus import, or worker dependency.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Golden zero→long→flat stream projects exact opening/fill/mark entries.
- Duplicate/correction/bust policy is explicit; unsupported correction/bust fails rather than silently applies.
- Projection is deterministic across raw run-custody identity changes when business facts are equivalent.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Portfolio reducer remains engine-neutral.
- [ ] Every accounting delta traces to a validated engine event.
- [ ] Package can later accept paper/live events with the same P1 schema.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-21 — Prove portfolio-reducer parity with the Nautilus final state

**Milestone:** M4 — Persistence and accounting  
**Depends on:** P1-19, P1-20  
**Parallel wave:** 16  
**Primary implementer subagent:** Accounting Verification Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `packages/engine_portfolio_projection/parity.py; tests/portfolio_reducer/test_nautilus_p1_parity.py; tests/portfolio_reducer/fixtures/p1_nautilus/**`  
**Commit:** `test(p1): prove Nautilus portfolio parity`

### Objective

Make independent canonical accounting agree exactly with the engine's observed terminal state.

### Implementation steps

- Include NETTING close/reopen and no-duplicate-realized-PnL regression fixtures from P1-U06 so parity remains valid across the upstream fixes.
- Replay projected portfolio entries through the existing reducer from a known opening snapshot.
- Compare final quantity, average entry, cash/balances, fees, realized PnL, unrealized PnL/mark policy, account currency and observed time to the P1 completion summary.
- Define tolerance as exact Decimal equality at catalog precision; do not use approximate float comparison. Any intentional representation difference must be normalized by a versioned rule.
- Bind the reducer state hash and replay prefix hash into a P1 parity receipt stored with the job result metadata.
- Prove replay from zero and replay from a trusted intermediate snapshot reach the same state.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Long entry, hold, flatten, fee-only, and partial-fill fixtures.
- Mutate one fill, fee, price, side, sequence, or opening balance and prove parity fails.
- Snapshot restart and full replay produce identical canonical state JSON/hash.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Independent ledger/reducer is not merely trusting the engine completion.
- [ ] Exact parity receipt exists.
- [ ] Accounting mismatch blocks job success or P1 qualification.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-22 — Build the end-to-end BTCUSDT real backtest vertical slice

**Milestone:** M5 — Vertical slice  
**Depends on:** P1-16, P1-17, P1-18, P1-19, P1-20, P1-21  
**Parallel wave:** 16  
**Primary implementer subagent:** Vertical Slice Integrator — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `tests/p1_nautilus/test_vertical_slice_e2e.py; tests/fixtures/p1_nautilus/e2e/**; scripts/run_p1_nautilus_vertical_slice.py; Makefile; docs/implementation/p1-real-nautilus/e2e-evidence.md`  
**Commit:** `test(p1): certify real Nautilus vertical slice`

### Objective

Exercise the whole production-shaped path from an authenticated durable job to final canonical portfolio state.

### Implementation steps

- Build sealed external fixtures for one BINANCE BTCUSDT spot run with 1-minute quote/bar pairs, starting USDT, a risk-approved target schedule that opens a long and later flattens, and a fixed fee policy.
- Submit/insert a BACKTEST job through the real job contract, claim it, derive `RunBacktest`, attest closure/input authority, spawn Bubblewrap/native guard/CPython 3.12, run real BacktestEngine, capture canonical JSONL, validate, seal, ingest, project, reduce, and finalize.
- Record exact evidence: source commit/tree, closure digest, artifact digests, request digest, raw batch hash, semantic digest, ledger receipt, parity receipt, final job state and final portfolio state hash.
- Ensure stdout/stderr/result artifacts remain bounded and no checkout/authority paths enter public metadata.
- Add one operator script that orchestrates the same path from explicit fixture/authority arguments; it must default to dry validation and never accept network credentials.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Expected lifecycle includes at least a real submitted order and fill for entry and flatten, terminal zero position, starting cash minus fees/PnL, and exact reducer parity.
- Run with missing native authority is `DEFERRED`, not PASS.
- Run with malformed input or runtime failure is BLOCKED/FAILED per stable reason, never SUCCEEDED.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] One command produces a complete evidence packet.
- [ ] No mock engine or TestInstrumentProvider is used.
- [ ] Vertical slice reaches durable accounting and job success.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-23 — Run determinism, restart, and adversarial qualification campaigns

**Milestone:** M5 — Vertical slice  
**Depends on:** P1-22  
**Parallel wave:** 17  
**Primary implementer subagent:** Adversarial Test Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `tests/p1_nautilus/adversarial/**; scripts/qualify_p1_nautilus.py; docs/implementation/p1-real-nautilus/adversarial-matrix.json`  
**Commit:** `test(p1): adversarially qualify Nautilus vertical slice`

### Objective

Reduce false confidence by attacking every authority, protocol, execution, persistence, and replay boundary.

### Implementation steps

- Carry P1-U07's approved-drift comparator into the qualification campaign. Any new semantic drift from the promoted digest is a blocker, not a golden-fixture update by default.
- Run the same business fixture three times with distinct jobs/attempts and prove identical semantic digest/final portfolio state but distinct raw request/batch identities.
- Inject closure/file/digest/mode/owner/path/symlink/inode/argv/profile/guard/interpreter drift; input schema/precision/timestamp/look-ahead errors; engine exception/rejection/pending order; stdout truncation/extra line/sequence gap/wrong causation; ledger collision/uncertain commit; projection/accounting mismatch; restart at every custody boundary.
- Verify no failure is reclassified as DEFERRED unless the required native/external authority is genuinely absent. Present-but-invalid authority is FAIL/BLOCKED.
- Measure bounded runtime, memory, event count, output size, closure attestation time, and replay time. Set generous evidence-based ceilings; do not optimize before measuring.
- Produce a machine-readable matrix with scenario, expected class, observed code, command, exit status, and evidence digest.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Qualification script fails if any scenario is skipped, duplicated, unexpectedly passes, or lacks evidence.
- Semantic digest mutation tests and replay restart tests are mandatory.
- Fuzz only bounded parsers/serializers; do not fuzz native/live network surfaces.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] All critical adversarial cases pass expected outcomes.
- [ ] Three-run determinism proof exists.
- [ ] No unresolved HIGH/CRITICAL finding.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-24 — Add CI lanes, maintainability guards, runbook, and upstream provenance

**Milestone:** M5 — Vertical slice  
**Depends on:** P1-22  
**Parallel wave:** 17  
**Primary implementer subagent:** Release Engineering Lead — `gpt-5.6-terra`, reasoning `high`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `high`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `Makefile; .github/workflows/foundation.yml or a dedicated reviewed workflow; docs/operations/p1-nautilus-runbook.md; docs/implementation/p1-upstream-provenance.md; docs/implementation/p1-real-nautilus/growth-budget.json; scripts/check_p1_nautilus_boundaries.py`  
**Commit:** `ci(p1): gate real Nautilus qualification`

### Objective

Make P1 reproducible and keep its new modules from becoming the next monolith.

### Implementation steps

- Add a pin-inventory CI gate and a candidate lineage report. CI must fail if 1.227 becomes active again accidentally, if `latest` appears, or if the v1.231 source/toolchain/dependency cross-digests drift.
- Add source/portable targets for contracts, boundary checks, pure planner/projection/accounting, and worker validation; native targets for sealed closure/runtime/E2E; external-authority lanes for retained caches/toolchains.
- Wire portable checks once into canonical CI. Native/external lanes publish explicit PASS/FAIL/DEFERRED evidence and cannot grant live/production authority.
- Set initial file growth budgets from the accepted P1-A blobs. Prefer modules under roughly 300–500 logical lines; require a responsibility review before a module crosses its budget. Freeze old launchers for growth.
- Document build, qualification, dry-run E2E, evidence interpretation, cleanup, rollback, and troubleshooting. State clearly that P1 has no exchange network, credentials, live execution, leverage, or shorting.
- Record every copied/adapted upstream snippet with exact tag/commit/path/symbol and local tests. This is a maintenance/provenance control even though license review is not a project blocker.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- `make check-p1-nautilus-contracts`, `make check-p1-nautilus-boundaries`, `make test-p1-nautilus-source`, `make test-p1-nautilus-native`, `make qualify-p1-nautilus`, and `make test-p1-nautilus-e2e` have documented semantics.
- CI graph test proves portable targets cannot reach host/native/external authority.
- Growth checker fails on seeded oversize/import violations.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Fresh operator can reproduce qualification from explicit authorities.
- [ ] CI distinctions remain honest.
- [ ] Maintainability constraints are executable.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-25 — Perform independent final review and promote P1-A

**Milestone:** M5 — P1-A promotion  
**Depends on:** P1-23, P1-24  
**Parallel wave:** 18  
**Primary implementer subagent:** Integration Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `docs/implementation/p1-real-nautilus/P1-A-FINAL-REVIEW.md; no source unless review finds an issue`  
**Commit:** `docs(p1): certify real Nautilus backtest slice`

### Objective

Declare the real backtest slice complete only from fresh evidence on the final candidate tree.

### Implementation steps

- Final review must confirm P1-A evidence was produced only on the v1.231 schema-8 P1 product closure derived from the promoted schema-7 baseline and that the v1.227 rollback was not used to manufacture a pass.
- Dispatch a fresh spec reviewer that reads the plan/ADR and final diff without relying on implementer summaries. Then dispatch a separate security/code-quality reviewer focused on authority, event truthfulness, execution semantics, accounting and maintainability.
- Resolve findings in separate fix tasks with new reviews; never edit during the final review task and self-approve.
- Run the full portable, native, adversarial and E2E gates from a clean worktree at the exact candidate SHA. Record tree SHA, closure digest, all command exit statuses, test counts, evidence hashes, known limitations, and authority classification.
- Open a checkpoint PR `P1-A Real Nautilus Backtest Vertical Slice`; do not merge/push/release without user authorization. After merge, rerun required CI on canonical main and prove final tree identity or exact approved merge semantics.
- Tag status `P1_A_COMPLETE` only after post-merge main proof.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Fresh final verification, not cached output.
- No unresolved reviewer finding.
- Main protection/required checks succeed after authorized merge.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Real BacktestEngine path is end-to-end and accounting-complete.
- [ ] P0/P0-M1 remain green and frozen.
- [ ] P1-A grants no paper/live/exchange authority.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-26 — Define finite paper-session commands, events, and custody protocol

**Milestone:** M6 — P1-B local paper parity  
**Depends on:** P1-25  
**Parallel wave:** 19  
**Primary implementer subagent:** Paper Architecture Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `packages/nautilus_runtime_contracts/paper.py; packages/engine_contracts only for proven missing generic fields; docs/adr/ADR-P1B-LOCAL-PAPER-SESSION.md; tests/nautilus_runtime_contracts/test_paper.py`  
**Commit:** `feat(p1b): define local Nautilus paper protocol`

### Objective

Define a long-lived but network-free paper session that reuses the P1 event/accounting protocol.

### Implementation steps

- Paper protocol remains engine-neutral and versioned independently. It may carry lineage metadata but must not expose Cython-v1 classes or require a v1-specific client command.
- Use existing generic command names where possible: `StartPaperEngine`, `SubmitTargetPortfolio`, `StopPaperEngine`, `InspectEngineRun`, and reconciliation. Define profile-specific payload constraints rather than new arbitrary commands.
- Define a framed stdin/control or custodian transport with request IDs, session ID, command sequence, acknowledgements, event sequence, EOF/stop semantics, bounded message sizes, and one writer/one owner.
- Paper input is a deterministic local market-data stream or replay feed; no adapter credentials, sockets, DNS, HTTP/WebSocket, broker/account API, or exchange endpoint.
- Define session states `CREATED`, `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`, `FAILED`, `RECONCILIATION_REQUIRED`. A command after failure or sequence gap is rejected.
- Specify durable checkpoint authority: last accepted command, last emitted event, semantic state hash, child identity, closure digest, and portfolio state hash.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Protocol rejects command duplication, gaps, wrong session, replay with changed bytes, oversized frame, unknown command, target during STOPPING, and restart without matching checkpoint.
- Paper event stream remains compatible with P1-A projector/reducer.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Paper semantics are finite and no-network.
- [ ] One authority owns child/session lifecycle.
- [ ] Recovery decisions have explicit evidence.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-27 — Implement the long-lived isolated local paper runtime

**Milestone:** M6 — P1-B local paper parity  
**Depends on:** P1-26  
**Parallel wave:** 20  
**Primary implementer subagent:** Paper Runtime Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `engines/nautilus/runtime_v1/paper_main.py; engines/nautilus/runtime_v1/paper_session.py; engines/nautilus/runtime_v1/control_channel.py; engines/nautilus/runtime_v1/paper_runner.py; tests/p1_nautilus/test_paper_runtime_native.py`  
**Commit:** `feat(p1b): run local Nautilus paper session`

### Objective

Reuse instrument, market-data, planner, strategy, event projector and accounting observations in a session that accepts sequential targets over time.

### Implementation steps

- Run only the promoted v1.231 local closure. Add long-lived reset/checkpoint tests for stale FX, duplicate account state and generated IDs based on the upstream regression campaign.
- Factor shared engine/session assembly only where P1-A characterization tests prove behavior is preserved. Do not fork a second strategy or event schema.
- Start one isolated child under an exact new closure profile. Feed deterministic local market updates and target commands through the reviewed framed channel; emit canonical events through a separate bounded channel/custodian contract.
- Use Nautilus APIs characterized for sandbox/paper behavior. If P1-01/P1-B characterization shows `TradingNode` is necessary, confine it behind the existing session seam; do not expose provider configuration to the control plane.
- Checkpoint after each accepted command/event batch. On stop, enter exit-only state, cancel/settle according to explicit policy, emit final observations, and close channels cleanly.
- Any channel corruption, child identity change, sequence ambiguity, or engine exception transitions to FAILED/RECONCILIATION_REQUIRED and prevents automatic restart/trading.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Native session starts, consumes multiple bars, opens long, receives flat target, closes, and stops with exact ledger/reducer parity.
- Kill child at defined points and prove checkpoint classification.
- Network namespace and source scanners prove no network client is reachable.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Same target strategy/event projector as backtest.
- [ ] Long-lived session has explicit custody.
- [ ] No live venue adapter or credential.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-28 — Integrate with the existing paper runtime controller and custodian

**Milestone:** M6 — P1-B local paper parity  
**Depends on:** P1-26, P1-27  
**Parallel wave:** 20  
**Primary implementer subagent:** Paper Integration Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `services/paper_runtime/nautilus_session.py; services/paper_runtime/nautilus_checkpoint.py; minimal reviewed edits to controller.py/integration.py/custodian_client.py; do not grow evidence.py unless a responsibility is proven; tests/paper_runtime/test_nautilus_session.py`  
**Commit:** `feat(p1b): integrate Nautilus with paper custody`

### Objective

Use existing process custody and safety gates rather than creating a parallel paper service.

### Implementation steps

- Add a narrow Nautilus session adapter under new modules. The existing controller remains the lifecycle owner and the existing custodian remains the child/process boundary where its contract fits.
- Keep changes to large existing files minimal: dependency injection and dispatch only. Put protocol/checkpoint/reconciliation logic in new cohesive modules with growth budgets.
- Require current paper-only safety evidence, kill switch, mode authority, child closure digest, and checkpoint identity before start or target submission.
- Persist accepted event batches through the same engine event ledger and portfolio projection/reducer path as P1-A.
- Do not let controller state, custodian state, event ledger, and portfolio snapshot disagree silently; mismatch transitions to reconciliation required.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Controller rejects start/target when safety evidence or closure/checkpoint differs.
- Exactly one child/session authority exists.
- Existing paper-runtime tests and evidence contracts remain green.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] No parallel daemon or second kill switch.
- [ ] Large `evidence.py` does not receive P1 execution responsibility.
- [ ] Paper events reach durable accounting.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-29 — Implement restart, reconciliation, and kill-switch behavior

**Milestone:** M6 — P1-B local paper parity  
**Depends on:** P1-28  
**Parallel wave:** 21  
**Primary implementer subagent:** Recovery and Safety Engineer — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `services/paper_runtime/nautilus_recovery.py; services/paper_runtime/nautilus_reconciliation.py; services/job_worker/recovery.py only if generic recovery contract needs extension; tests/paper_runtime/test_nautilus_recovery.py; tests/paper_runtime/test_nautilus_reconciliation.py`  
**Commit:** `feat(p1b): reconcile Nautilus paper recovery`

### Objective

Ensure a crash or uncertain outcome never causes duplicate exposure or automatic unsafe continuation.

### Implementation steps

- Recovery evidence binds exact engine version/closure. A checkpoint from v1.227 cannot resume under v1.231; classify it as explicit migration/reconciliation required.
- Define restart matrix for: no child/no checkpoint, child running/checkpoint current, child gone/checkpoint clean stop, child gone after accepted target before events, events durable before checkpoint, checkpoint ahead of ledger, closure/source/config drift, and kill switch engaged.
- Automatic resume is allowed only when command/event/checkpoint/ledger/portfolio prefixes match exactly and the local child outcome is provable. Otherwise block and require explicit reconciliation.
- Kill switch prevents new targets, enters stop/exit-only policy, records evidence, and never clears itself from child output.
- Reconciliation compares child/session checkpoint, event ledger last sequence/digest, portfolio state hash, target schedule cursor and final engine observation. No exchange query exists in P1-B.
- Add no-clobber recovery receipts and stable reason codes.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- Crash at every boundary around target acceptance, order submit, fill, event persist, checkpoint persist and stop.
- No scenario duplicates an order or target after restart.
- Kill-switch race tests prove no new opening order after engagement.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] Uncertain outcome is terminally blocked, not retried.
- [ ] Safe exact-prefix resume is deterministic.
- [ ] Recovery evidence is durable and no-clobber.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.

## P1-30 — Certify local paper parity and close P1

**Milestone:** M6 — P1 final certification  
**Depends on:** P1-29  
**Parallel wave:** 22  
**Primary implementer subagent:** Final Certification Lead — `gpt-5.6-sol`, reasoning `xhigh`  
**Spec-compliance reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Code-quality/security reviewer:** fresh subagent — `gpt-5.6-sol`, reasoning `xhigh`  
**Owned files:** `tests/p1_nautilus/test_paper_vertical_slice_e2e.py; scripts/qualify_p1_nautilus_paper.py; docs/implementation/p1-real-nautilus/P1-FINAL-CERTIFICATION.md; Makefile; CI workflow`  
**Commit:** `docs(p1): certify real Nautilus engine vertical slice`

### Objective

Prove the same target/execution/event/accounting semantics across backtest and local paper replay, then promote with fresh post-merge evidence.

### Implementation steps

- Final certification records v1.231 as the final-v1 bridge and opens a separate non-blocking v2 migration discovery item; P1 itself does not mix runtimes.
- Run an identical deterministic market/target sequence through P1-A backtest and P1-B local paper. Compare normalized semantic event stream and final portfolio state; document explicitly allowed lifecycle differences.
- Run restart/kill-switch/reconciliation campaign and verify every unsafe/uncertain state blocks.
- Dispatch fresh spec and security reviewers, resolve findings in separate tasks, and run all P0, P1-A and P1-B gates from a clean final candidate.
- Prepare a final PR and post-merge main proof under user authorization. Record source/tree, closure/profile digests, protocol versions, test counts, evidence hashes and known non-goals.
- Declare `P1_COMPLETE` only after main CI succeeds. Status must still say `PAPER_LOCAL_ONLY`, `NETWORK_DISABLED`, `LIVE_NOT_AUTHORIZED`, `PRODUCTION_NOT_AUTHORIZED`.

### TDD and verification

- [ ] Write the smallest failing test or executable boundary check first.
- [ ] Run the focused test and confirm it fails for the intended missing behavior, not for an environment/setup error.
- [ ] Implement the minimum coherent change inside the owned files.
- [ ] Run the focused tests until green.
- `make test-p1-nautilus-paper`, `make qualify-p1-nautilus-paper`, full portable CI, native qualification, and adversarial suite.
- Backtest/paper semantic parity fixture.
- Post-merge main workflow success.
- [ ] Run `make check-p0-maintainability` and `make check-p1-nautilus-boundaries`.
- [ ] Run the relevant existing regression suite for every modified shared file.
- [ ] Commit only after the worktree is clean except for the intended task diff.

### Acceptance checklist

- [ ] P1-A and P1-B are both complete.
- [ ] Backtest/local-paper share contracts, strategy, events and accounting.
- [ ] No live/exchange authority was introduced.
- [ ] Spec reviewer returns `PASS` with no missing requirement.
- [ ] Quality/security reviewer returns `PASS` with no unresolved HIGH or CRITICAL issue.
- [ ] Integration lead records the accepted commit SHA and verification evidence in the task ledger.

### Stop / rollback conditions

- Stop if the task requires live credentials, network access, an unreviewed executable, a profile selected from client input, or growth of a frozen P0/P1 reference file.
- Stop if a test can be made green only by weakening a fail-closed rule, broadening an exception, accepting unknown fields, skipping native proof, or changing an existing contract without a version bump.
- Revert/cherry-pick out the task commit if an independent reviewer finds responsibility leakage or if the accepted parent SHA changed before integration.



# Upgrade-aware program gates

Before any P1 product task, P1-U08 must record `PROMOTE_1_231_FOR_P1` with status `P1_BASELINE_APPROVED`, scope `P1_A_AND_P1_B_ONLY`, exact G1/schema-7 baseline digest, and target schema-8 product lineage. The accepted legacy Phase4 1.227/schema-6 loader and policies remain unchanged. Before P1-A completion, the real-backtest profile must use an exact schema-8 product closure. Before P1 final completion, all backtest and paper evidence must name `1.231.0` and the schema-8 product closure derived from the approved P1 baseline.

A future v2 migration is deliberately not a P1 completion gate. It is opened as a separate discovery/architecture item only after P1 final certification.

# 10. Program-wide verification gates

## Source/portable gates

```bash
make check-p0-baseline
make check-p0-maintainability
make check-p1-nautilus-contracts
make check-p1-nautilus-boundaries
make test-p1-nautilus-source
make check-contracts
make audit-portable
make ci-portable NONINTERACTIVE=1
```

These gates must run without importing the external Nautilus wheel or claiming native qualification.

## Native P1-A gates

```bash
make build-p1-nautilus-runtime
make qualify-p1-nautilus-runtime
make test-p1-nautilus-native
make test-p1-nautilus-e2e
make qualify-p1-nautilus
```

Exact external authority paths, digests and toolchain identities must be supplied. Missing authority is `DEFERRED`; invalid authority is failure.

## P1-B gates

```bash
make test-p1-nautilus-paper
make qualify-p1-nautilus-paper
```

No network is enabled. These prove only local paper session semantics and recovery.

## Regression gates

Run all existing engine-contract, job-worker, process-runner, engine-event-ledger, portfolio-reducer, paper-runtime, zero-order, execution-simulation, paper-compatibility, P0 baseline and maintainability tests affected by each diff.

# 11. Risk register

| Risk | Consequence | Prevention |
|---|---|---|
| Rebuilding an already-qualified path | months of duplicate work | P1-01 characterization and reuse mandate |
| Growing old launcher monolith | future high-risk extraction | frozen growth checker and runtime_v1 modules |
| Client-selected profile/executable | arbitrary execution authority | code-owned profile policy and generic RunBacktest only |
| Test-kit instrument leaks to runtime | fixture semantics in production path | catalog-built instrument and source checker |
| Look-ahead bar timestamp | false backtest performance | quote-before-bar and close-time `ts_init` contract |
| Float/round-up sizing | risk target exceeded | Decimal-only floor-to-step planner |
| Synthesized event presented as native | false audit trail | explicit event origin and callback matrix |
| Aggregate completion trusted as accounting | undetected drift | independent projection/reducer parity |
| Partial stdout accepted | false success | all-or-nothing local validation plus worker validator |
| Uncertain ledger write retried | duplicate execution | receipt reconciliation and no automatic rerun |
| Parallel shared-file edits | integration regressions | ownership waves and single integration lead |
| Upstream breaking changes | API drift | exact 1.231.0 pin and no upgrade in P1 |
| Paper child/control split-brain | unsafe continuation | one controller/custodian and checkpoint reconciliation |
| “Deferred” used to hide invalid authority | false green | absent-only deferral rule |
| Massive paper evidence module grows further | maintainability debt | new P1-specific modules and growth budgets |

# 12. Definition of done

## P1-A

- [ ] P1-A exact candidate SHA and tree recorded.
- [ ] Catalog-built real BTCUSDT instrument; no TestInstrumentProvider.
- [ ] Real BacktestEngine order and fill callbacks observed.
- [ ] Full canonical event stream accepted and sealed.
- [ ] Durable event-ledger receipt verified.
- [ ] Independent portfolio replay equals engine final state exactly.
- [ ] Three-run semantic determinism passes.
- [ ] Restart and adversarial campaigns pass.
- [ ] Existing P0 and Nautilus qualification suites remain green.
- [ ] New modules respect growth/ownership budgets.
- [ ] Fresh spec and security reviews pass.
- [ ] Authorized merge followed by canonical-main CI success.
- [ ] Status explicitly says no paper/live/network/production authority.

## Entire P1

- [ ] All P1-A conditions.
- [ ] Local paper runtime reuses P1-A strategy/events/accounting.
- [ ] Existing paper controller/custodian owns the child.
- [ ] Checkpoint, restart, reconciliation and kill-switch campaigns pass.
- [ ] Backtest and local-paper normalized semantic outcomes agree.
- [ ] Fresh final reviews pass.
- [ ] Authorized merge followed by canonical-main CI success.
- [ ] Final status:
  - `P1_COMPLETE`
  - `PAPER_LOCAL_ONLY`
  - `NETWORK_DISABLED`
  - `LIVE_NOT_AUTHORIZED`
  - `PRODUCTION_NOT_AUTHORIZED`

# 13. Mandatory completion report format

```text
TASK / PROGRAM:
BASE SHA:
HEAD SHA:
TREE SHA:
CLOSURE DIGEST:
NAUTILUS VERSION / UPSTREAM COMMIT:
PROFILE / PROTOCOL VERSION:
FILES CHANGED:
TESTS ADDED:
FOCUSED TEST COMMANDS + EXIT:
REGRESSION COMMANDS + EXIT:
NATIVE AUTHORITY STATUS:
EVENT BATCH SHA:
SEMANTIC DIGEST:
LEDGER RECEIPT:
PORTFOLIO STATE HASH:
SPEC REVIEW:
SECURITY/QUALITY REVIEW:
KNOWN LIMITATIONS:
LIVE AUTHORITY:
PRODUCTION AUTHORITY:
VERDICT:
```

Any missing field prevents a completion claim.

# 14. Intended repository location

Save this plan in the repository as:

```text
docs/superpowers/plans/2026-08-16-p1-real-nautilus-v1.231.md
```

The design companion belongs at:

```text
docs/implementation/p1-real-nautilus/design.md
```
