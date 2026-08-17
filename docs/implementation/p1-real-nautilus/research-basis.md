
# P1 v1.231 Research Basis and Repository Evidence

**Repository:** `nam176hermes/Trading-Agent`  
**Reviewed baseline:** `c8fb6f694b11c065d5b819614532e9a77aa8da4b`  
**Date:** 2026-08-16

## Current repository evidence

### Engine build and closure

- `engines/nautilus/README.md` defines an isolated CPython 3.12 source-wheel build, private Rust/LLVM/build-wheel caches, Bubblewrap network isolation and sealed runtime artifacts.
- `engine-build-policy.json`, `input-cache-policy.json`, `toolchain-inputs.json` and `wheel-cache-policy.json` currently pin `1.227.0`, commit `280ae1762df51a492a4ce71506a40b5c8706def5`, Rust 1.95.0, Cython 3.2.4, poetry-core 2.3.1 and setuptools 82.0.1.
- `services/job_worker/nautilus_closure.py` expects exact `1.227.0` and closure schema generations through 6; P1-U introduces schema 7 for the promoted v1.231 baseline, and productization introduces schema 8 for new P1 profiles.

### Real execution already exists

- The current sealed launcher instantiates real `BacktestEngine`, venue, CASH/NETTING account, instrument, strategy, QuoteTick/Bar data and custom fee model.
- It runs the engine, receives native fills, reads cache orders/positions/account/commissions and emits a hash-bound canonical result.
- The target strategy submits actual Nautilus orders and observes `OrderFilled`/`OrderRejected` callbacks.

### Worker and durability already exist

- Engine command authority derives from a fenced durable claim.
- Inputs are exact external 0400 hash-bound artifacts.
- Spawn uses Bubblewrap, native entry guard and sealed request transport.
- Worker validation parses canonical JSONL, binds it to request authority, seals accepted bytes, ingests into the engine-event ledger and verifies a durable receipt before job success.
- The engine-event ledger and portfolio reducer are separate engine-neutral foundations.

## Upstream v1.231 facts

- Released 2026-08-02 UTC and intended as the final 1.x release supporting the legacy Cython v1 core.
- `develop` moves to v2-only; critical v1 security backports move to `develop_v1` for a limited transition period.
- Exact tag object: `d3e1685e979925d7b0ffacd1b3f442547686e18f`.
- Exact underlying commit: `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`.
- Official sdist SHA-256: `142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f`.
- Official cp312 manylinux x86_64 wheel SHA-256: `8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216`.
- CPython requirement remains `>=3.12,<3.15`.
- Build pins change to Rust 1.97.1, Cython 3.2.9 and setuptools >=83; poetry-core stays 2.3.1.
- Runtime dependencies move, including pyarrow >=25, pandas <4, exact fsspec 2026.2.0 and timezone package changes.

## Release-delta classification

### Relevant v1/general fixes

- Python-v1 throttler buffering.
- cache reset FX retention.
- duplicate account-state reset.
- calculated account state.
- backtest end/shutdown/on_stop ordering.
- NETTING and foreign-currency PnL statistics.
- duplicate post-run realized PnL.
- venue registration rollback and leverage validation.
- generated ID collision after instrument re-registration.
- simulated-venue multi-currency/FX/reset sequencing.
- simulated exchange status-query panic.

### Mostly v2-only and not P1 justification

- v2 BacktestNode inspection, v2 subclassing/bindings, v2 live node, v2 event persistence, v2 adapters and v2 performance work.

### Build-only but mandatory

- Rust/Cython/setuptools/dependency closure updates.

## Consequent decisions

1. Upgrade before P1-A, through P1-U gates.
2. Retain v1.227 as immutable rollback until P1-A is certified.
3. Use exact v1.231 Cython-v1 runtime; no v2 migration in P1.
4. Add closure manifest schema 7 release provenance, then schema 8 for the P1 product profile/protocol inventory.
5. Keep low-level `BacktestEngine` and add an engine-neutral session seam.
6. Turn relevant release notes into executable regression tests.
7. Use dual-run semantic comparison rather than blind golden regeneration.
8. Keep official wheel as an independent oracle unless ADR explicitly promotes it.
