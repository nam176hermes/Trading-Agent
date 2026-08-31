
# P1 Real Nautilus Engine Vertical Slice — v1.231.0 Rebased Architecture Design

**Repository:** `nam176hermes/Trading-Agent`  
**P1 source baseline:** [current-source-baseline.json](./current-source-baseline.json)
**Current rollback engine:** NautilusTrader `1.227.0` / `280ae1762df51a492a4ce71506a40b5c8706def5`  
**Target engine:** NautilusTrader `1.231.0` / `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`  
**Date:** 2026-08-16  
**Status:** Replacement design for implementation; paper/local only; no live or production authority.

## 1. Executive design decision

P1 remains a productization program, not a greenfield Nautilus integration. The repository already has a real sealed Cython-v1 `BacktestEngine`, worker process custody, canonical event validation, durable ingestion and independent portfolio accounting.

The architecture changes in one important way: before productization, P1 creates and qualifies a final-v1 `1.231.0` candidate beside the retained `1.227.0` rollback. After provenance, hermetic build, direct API, regression and dual-run gates, U08 may approve it for P1-A/B only; legacy Phase4 profiles remain on 1.227/schema 6.

## 2. Why upgrade before P1-A

```text
Build P1 on 1.227
  → certify closure/events/parity
  → later upgrade
  → repeat the expensive certification
```

is inferior to:

```text
qualify 1.231 once
  → promote exact final-v1 baseline
  → build P1 contracts/runtime/events/parity on that baseline
```

The latter avoids requalifying the highest-risk P1 surfaces after implementation.

## 3. Why not migrate to v2 in P1

The current root/engine split, generated launcher, native entry guard and direct Cython-v1 strategy/event APIs are already established. A v2 migration would change package layout, build system, native object semantics, event/callback behavior and possibly backtest orchestration while P1 is also introducing product contracts, event streams and durable accounting.

P1 therefore establishes an engine-neutral seam:

```text
BacktestSession protocol
   ├── runtime_v1 / Nautilus 1.231 Cython implementation  ← P1
   └── runtime_v2 / Rust+PyO3 implementation              ← future program
```

No process may import both packages under the shared `nautilus_trader` name.

## 4. Upgrade and runtime topology

```text
                           Trading-Agent root (Python 3.11)
                                      │
                     exact closure/profile selection by code
                                      │
          ┌──────────────────────┬──────────────────────────┐
          │                      │                          │
rollback verifier       promoted baseline verifier      P1 product profiles
1.227 / schema 6        1.231 / schema 7                1.231 / schema 8
          │                      │                          │
immutable closure       immutable qualification closure immutable product closure
non-default only        source/release baseline         real-backtest + local-paper
```

The two closures have separate source, toolchain, dependency, artifact and runtime roots. They share no writable state.

## 5. Schema-7 baseline and schema-8 product provenance contracts

Schema 7 extends the current closure authority with immutable release lineage:

```json
{
  "schema_version": 7,
  "runtime_family": "cython-v1",
  "engine_name": "nautilus_trader",
  "engine_version": "1.231.0",
  "engine_release_tag": "v1.231.0",
  "engine_release_tag_object": "d3e1685e979925d7b0ffacd1b3f442547686e18f",
  "engine_upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
  "engine_source_sha256": "142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f",
  "release_manifest_sha256": "<reviewed>",
  "provenance_attestations": [
    {"kind": "sigstore", "sha256": "<reviewed>"},
    {"kind": "in-toto", "sha256": "<reviewed>"}
  ],
  "python_identity": "CPython 3.12.x",
  "dependency_import_policy": "native-guarded-stdlib-first-sealed-wheel-path-v1",
  "files": []
}
```

The final implementation may normalize field grouping, but it must preserve these authorities. Schema 6 remains valid only under an explicit rollback verifier. Schema 8 must extend schema 7 with exact P1 profile name, protocol/event versions, entrypoint/argv, validator ID and complete `runtime_v1` product inventory; it must not weaken or replace any schema-7 release field.

## 6. Build authority

### Fixed candidate inputs

- CPython 3.12;
- Rust/Cargo 1.97.1 for the upstream engine build;
- separately pinned current native-guard Rust/Cargo authority;
- Cython 3.2.9;
- poetry-core 2.3.1;
- exact reviewed setuptools >=83 wheel;
- exact runtime dependency wheel set;
- approved source/tag/commit/provenance;
- Bubblewrap and native entry guard;
- existing LLVM 22.1.3 unless a separate evidence-backed change is approved.

### Build rules

- no network during build or runtime materialization;
- no ambient rustup, compiler, package index, pip/uv cache or site-packages;
- only private staging writable;
- exact file inventory and normalized reproducibility proof;
- official upstream wheel is a comparison oracle, not implicit runtime authority.

## 7. P1-A target architecture

```text
Authenticated BACKTEST job
        ↓
Fenced worker claim
        ↓
EngineCommandEnvelope<RunBacktest>
        ↓
Hash-bound engine/config/catalog/target/data artifacts
        ↓
Attested v1.231 schema-8 P1 product CPython 3.12 closure
        ↓
Bubblewrap + native entry guard
        ↓
engines/nautilus/runtime_v1
        ├── secure bootstrap/input loader
        ├── catalog-driven CurrencyPair
        ├── QuoteTick + Bar conversion
        ├── target weight → exact quantity plan
        ├── real Nautilus Strategy
        ├── real BacktestEngine
        ├── callback/native observation collector
        └── canonical event projector
        ↓
Canonical EngineEvent JSONL
        ↓
Worker profile validator + sealed batch
        ↓
Durable engine-event ledger receipt
        ↓
Pure EngineEvent → portfolio entry projection
        ↓
Existing portfolio reducer
        ↓
Exact engine/reducer parity receipt
        ↓
Durable job success
```

## 8. Product runtime ownership

```text
packages/engine_contracts/
  Generic commands/envelopes only; no Nautilus/version-specific types.

packages/nautilus_runtime_contracts/
  P1 artifact/event/state-machine protocol; engine lineage fields are scalars.

engines/nautilus/runtime_v1/
  Only product package allowed to import Nautilus Cython v1.

services/job_worker/
  Authority, closure/input verification, spawn, capture, validation, ingestion.

packages/engine_event_ledger/
  Raw canonical event truth and receipts.

packages/engine_portfolio_projection/
  Pure validated event → portfolio entry adapter.

packages/portfolio_reducer/
  Independent engine-neutral accounting.

services/paper_runtime/
  P1-B custody/checkpoint/recovery; no duplicate engine daemon.
```

## 9. Release-specific regression requirements

The promoted runtime must retain executable tests for:

1. no duplicate account state on reset;
2. retained FX lookup across reset;
3. exact cash calculated-state behavior;
4. NETTING close/reopen realized PnL exactly once;
5. foreign-currency PnL correctness;
6. no duplicate post-run realized PnL;
7. safe venue-registration rollback;
8. reject non-positive leverage before execution;
9. no generated-ID collision after instrument re-registration;
10. safe simulated-venue multi-currency/FX/reset sequencing;
11. no panic for simulated exchange status queries;
12. explicit end-of-data/on_stop/pending-fill ordering.

These are upgrade gates and long-term regression assets, not disposable one-time probes.

## 10. P1-A active scope

- BTC/USDT spot;
- simulated BINANCE identity;
- CASH account;
- NETTING OMS;
- one instrument;
- 1-minute QuoteTick + Bar pairs;
- long/flat only;
- no leverage, shorting, network, provider or credentials;
- exact target schedule supplied upstream;
- exact v1.231 closure.

Broader multi-currency and position-cycle tests exist only to verify upstream fixes and future-proof accounting; they do not expand active execution scope.

## 11. Event and accounting lineage

Each run records:

```text
runtime_family = cython-v1
engine_version = 1.231.0
engine_upstream_commit = 27a8e54e7ac3c57d6cbf8891f0283dfbaee97317
closure_schema = 7
closure_digest = <exact>
request/config/input digests = <exact>
```

Raw event identity includes full custody/provenance. Semantic digest excludes only explicitly named run IDs/timestamps that do not alter business meaning. It never excludes order/fill sequence, price, quantity, fee, balance, position or PnL.

Corrected upstream post-run PnL is not duplicated into the canonical stream. Callback facts and post-run observations have distinct types and business identities.

## 12. P1-B local paper design

P1-B uses the same instrument factory, target planner, strategy, event schema, ledger projection and reducer. It runs the promoted v1.231 closure only, remains network-free, and integrates with the existing paper controller/custodian.

Checkpoint compatibility is exact-version and exact-closure. A v1.227 checkpoint cannot resume under v1.231 and must be classified `ENGINE_CHECKPOINT_MIGRATION_REQUIRED` or equivalent—not auto-replayed.

## 13. Rollback design

Rollback is code-policy rollback plus exact external closure selection, never an in-place rewrite:

1. stop new candidate claims;
2. preserve all candidate evidence/events;
3. select the exact reviewed v1.227 rollback policy commit;
4. verify schema-6 rollback closure;
5. do not resume v1.231 checkpoints under 1.227;
6. reconcile any ambiguous run before retry;
7. record reason, base/head SHA, closure digest and affected jobs.

## 14. Future v2 migration seam

After P1 certification, a separate program may implement:

```text
engines/nautilus/runtime_v2/
  ├── v2 source/build/closure authority
  ├── BacktestSessionV2
  ├── v2 callback/event adapter
  └── parity campaign against P1 canonical contracts
```

It must not change generic commands, event ledger or portfolio reducer merely to mirror v2 internals. The P1 canonical protocol is the compatibility target.
