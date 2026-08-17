
# NautilusTrader 1.227.0 → 1.231.0 Upgrade Assessment

**Repository:** `nam176hermes/Trading-Agent`  
**Trading-Agent baseline reviewed:** `c8fb6f694b11c065d5b819614532e9a77aa8da4b`  
**Current engine baseline:** NautilusTrader `1.227.0` / `v1.227.0` / `280ae1762df51a492a4ce71506a40b5c8706def5`  
**Candidate engine baseline:** NautilusTrader `1.231.0` / `v1.231.0` / `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`  
**Assessment date:** 2026-08-16

## Executive verdict

```text
DECISION: UPGRADE_NOW_AS_A_GATED_P1_PRELUDE
ACTIVE_BASELINE_BEFORE_PROMOTION: 1.227.0
CANDIDATE_BASELINE: 1.231.0
ROLLBACK_BASELINE: 1.227.0
MIGRATE_TO_V2_NOW: NO
LIVE/PRODUCTION AUTHORITY: UNCHANGED / NOT AUTHORIZED
```

The upgrade is worth doing **before** implementation of the real P1 vertical slice. It is not safe to edit the current version constants and immediately call the upgrade complete. The correct move is to create a sealed `1.231.0` candidate beside the retained `1.227.0` closure, run API and semantic qualification, and promote the candidate only after all gates pass.

The key reason is sequencing. P1 will spend substantial effort certifying the engine closure, order/fill stream, final account state, event ledger, deterministic digest and portfolio-reducer parity. Building that evidence on `1.227.0` and upgrading afterward would invalidate or force repetition of the most expensive P1 work.

## Decision scorecard

| Dimension | Assessment | Why |
|---|---:|---|
| Direct value to P1 backtesting/accounting | **High** | Releases 1.228–1.231 include fixes around reset/account state, NETTING PnL, post-run realized PnL, venue registration, generated IDs and simulated-venue sequencing. |
| Direct API compatibility | **Moderate to high** | CPython 3.12 and the Cython v1 package remain supported; the direct `BacktestEngine`/model package paths remain present. Native probes are still mandatory. |
| Build-policy impact | **High but bounded** | Rust MSRV, Cython, setuptools and the dependency closure changed; the existing sealed build policies must be regenerated and re-attested. |
| Risk of upgrading after P1-A | **High** | It would force closure, callback, event, parity and E2E recertification after productization. |
| Risk of a gated upgrade before P1-A | **Moderate** | The delta is large, but dual-run comparison and rollback make it manageable. |
| Value of migrating to v2 in the same program | **Low / harmful now** | It would combine an engine-generation migration with a product vertical slice and destroy scope isolation. |

## Authoritative candidate identity

| Item | Pinned value |
|---|---|
| Release | NautilusTrader `1.231.0` Beta |
| Release date | 2026-08-02 UTC |
| Tag | `v1.231.0` |
| Annotated tag object | `d3e1685e979925d7b0ffacd1b3f442547686e18f` |
| Underlying commit | `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317` |
| Official source distribution SHA-256 | `142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f` |
| Official CPython 3.12 Linux wheel | `nautilus_trader-1.231.0-cp312-cp312-manylinux_2_35_x86_64.whl` |
| Official CPython 3.12 Linux wheel SHA-256 | `8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216` |
| Runtime family | `cython-v1` |
| Root/control-plane Python | CPython 3.11, unchanged |
| Isolated engine Python | CPython 3.12, unchanged |

The inspected GitHub tag metadata does not provide a verified release-signature authority, so the new source authority must not rely on the tag name alone. The plan binds the tag object, underlying commit, release asset digest, release metadata and artifact provenance, then independently builds the sealed candidate.

## What changed from 1.227.0

### 1. Release-line and support posture

`1.231.0` is intended to be the final 1.x release supporting the legacy Cython v1 core. New feature work moves to v2; critical v1 security backports are expected only for a limited transition period. This makes `1.231.0` the rational final v1 bridge, not a long-term excuse to avoid v2 planning.

### 2. Build and dependency closure

| Build/runtime item | 1.227.0 | 1.231.0 | Required Trading-Agent action |
|---|---|---|---|
| Python support | `>=3.12,<3.15` | `>=3.12,<3.15` | Keep sealed CPython 3.12. |
| Rust MSRV | `1.95.0` | `1.97.1` | Build and attest a new private Rust toolchain. |
| Cython | `3.2.4` | `3.2.9` | Regenerate the build-wheel cache and policy. |
| poetry-core | `2.3.1` | `2.3.1` | Keep the exact pin. |
| setuptools | `>=82` | `>=83` | Select and pin one reviewed wheel; do not leave an open range in the sealed cache. |
| pandas | `<3.0.0` | `<4.0.0` | Rebuild the runtime dependency closure and test result/statistics behavior. |
| pyarrow | `>=23.0.1` | `>=25.0.0` | Refresh the closure even if P1 does not use catalog/Arrow directly. |
| fsspec | bounded 2025–2026 range | exactly `2026.2.0` | Pin the exact candidate dependency. |
| timezone deps | `pytz` | newer `pytz` plus `tzdata` | Add and attest the new wheel set. |

LLVM 22.1.3 is **not** automatically changed. First prove whether the `1.231.0` source build succeeds with the existing reviewed LLVM toolchain. A compiler upgrade is a separate authority change and occurs only with evidence.

### 3. Fixes that matter to the P1 vertical slice

| Release | Relevant changes | P1 significance |
|---|---|---|
| 1.228.0 | Python-v1 throttler buffering fix; retained FX rates across cache reset; duplicate account-state reset fix; calculated cash/margin state fix; backtest end/shutdown fixes and clarified `on_stop`/latency ordering. | Requires reset, shutdown and account-event regression tests. |
| 1.229.0 | NETTING account-currency PnL fix across reused `PositionId`; foreign-currency trade PnL fix; local catalog/backtest non-ASCII ID fix; matching commission-side and portfolio scoping fixes in Rust-backed surfaces. | Directly relevant to independent reducer parity and future repeated position cycles. |
| 1.230.0 | Duplicate realized PnL fix in post-run analysis; event-store format change; unbounded HTTP buffering security fix. | The PnL fix matters. Nautilus `event_store` migration is not part of P1's own engine-event ledger, but absence of hidden use must be proved. Network fix is defense-in-depth; P1 remains network-disabled. |
| 1.231.0 | Backtest venue-registration rollback and non-positive leverage validation; generated-ID collision fix after instrument re-registration; simulated-venue multi-currency/FX/reset sequencing; simulated exchange status-query panic fix; final v1 toolchain refresh. | These affect session reuse/reset safety, instrument setup and candidate qualification. |

Most of the very large 1.231 feature list is v2-only and does not justify changing P1 architecture. The upgrade case comes from the final-v1 support position plus the cumulative v1/backtest/accounting fixes.

## Direct compatibility with the current Trading-Agent path

The current sealed launcher imports low-level v1 APIs such as `BacktestEngine`, `BacktestEngineConfig`, `FeeModel`, `FillModel`, `Strategy`, `OrderFilled`, `OrderRejected`, `QuoteTick`, `Bar`, `Money`, `Price`, `Quantity`, `Venue`, `OmsType` and `AccountType`. Those package surfaces remain present in `1.231.0`. The `BacktestEngine` constructor and `add_venue` entry point remain source-compatible at the inspected boundary, and the core fee/fill declaration files remain present.

This is not sufficient to declare compatibility. Cython extension ABI, callback ordering, cache/account state, result counters and post-run statistics must be tested inside the actual sealed CPython 3.12 candidate.

One inspected configuration default changed at the documented boundary: `BacktestEngineConfig.load_state` and `save_state` are documented as `True` in 1.227.0 and `False` in 1.231.0. The current launcher does not make those values explicit. The replacement plan therefore forbids relying on either version's default and requires `load_state=False` and `save_state=False` in every P1 backtest/paper configuration. The inspected `get_result()` boundary remains callable and adds a summary surface in 1.231.0; P1 must not treat that additive field as accounting authority without an explicit contract.

## Risks and controls

| Risk | Severity without controls | Control in the new plan |
|---|---:|---|
| 1,482-commit delta hides semantic drift | High | API map, native import probe, release-regression campaign and dual-run semantic diff. |
| Toolchain/cache policies become stale | High | New immutable Rust/build/runtime caches plus schema-7 candidate provenance and schema-8 P1 product provenance. |
| Candidate overwrites known-good closure | High | Side-by-side candidate; `1.227.0` remains rollback until promotion. |
| Fixed bugs change expected event/PnL outputs | Medium | Approved-drift ledger; business invariants, not blind byte equality. |
| v1 reaches end of feature development | Medium | Treat `1.231.0` as final-v1 bridge; preserve `BacktestSession` seam and schedule v2 separately. |
| Event-store format migration is overlooked | Medium | Inventory all Nautilus persistence imports/files; P1 must prove it uses its own event ledger only. |
| Official wheel differs from local source build | Medium | Use official wheel as a non-authoritative API/behavior oracle and record differences; runtime authority remains the reviewed sealed candidate. |

## Promotion decision rule

Promote `1.231.0` only if all conditions are true:

1. Exact release/tag/commit/source provenance verifies.
2. The sealed source wheel builds twice with identical normalized manifest and native inventory.
3. Every direct import and invoked signature passes in CPython 3.12.
4. Existing zero-order, execution-simulation and paper-compatibility suites pass.
5. Release-regression scenarios pass.
6. Dual-run differences are either zero or explicitly approved and explained by upstream fixes.
7. No event/order/fill/account/portfolio invariant weakens.
8. Root Python remains engine-neutral and all live/network gates remain closed.
9. `1.227.0` remains a reproducible rollback closure until P1-A certification.

If any condition fails, verdict is `HOLD_1_227`, not “patch around the test” and not “continue with partial qualification.”

## Final recommendation

```text
YES — UPGRADE BEFORE P1-A IMPLEMENTATION.
NO — DO NOT HOT-SWAP THE CURRENT ACTIVE PIN.
NO — DO NOT COMBINE THIS WITH A V2 MIGRATION.
```

Use the replacement plan in this package. Start with `P1-U00` only.
