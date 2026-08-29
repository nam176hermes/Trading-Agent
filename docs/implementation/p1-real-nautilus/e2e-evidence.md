# P1-22 BTCUSDT vertical-slice evidence

Status: `SOURCE_PREFLIGHT_READY`; native/database execution: `DEFERRED`.

The operator lane is `make qualify-p1-nautilus-vertical-slice`. With no
external authority it emits one canonical `DEFERRED` receipt and performs no
job mutation. A partial or invalid native/PostgreSQL family is `BLOCKED`.
Supplied paths never enter the public receipt.

The effective worker authority remains the existing Package6 staging
attestation. Complete preflight also binds the sandbox executable to the exact
`sandbox_path` and `sandbox_sha256` in
`engines/nautilus/sealed-uv-exec-policy.json`. Transport, disposable PostgreSQL
approval, and its source bindings must validate before worker or closure
attestation can execute the reviewed sandbox capability probe.

## Fixture authority

| Input | SHA-256 |
|---|---|
| engine configuration | `38fa348e0422607052851028ed84b2478740d930ce09832dc5e42cbb86b78f60` |
| instrument catalog | `22a6c061b06d0eef539509a5cfa4a1128843a80b1f48eb473a9b65126f74d822` |
| target schedule | `c4002efb2f0f2b14c94699db59ef8c5733602e41c3bfe60999670fb7c0671470` |
| two-row market JSONL | `d390750a1d51b6f333efc7092cd99f2c6752ca6ab51daeaa800171ea92005c9c` |

The fixed window is `2026-08-05T12:00:00Z` through
`2026-08-05T12:01:00Z`. Product authority remains account
`p1-btcusdt-fixture-account`, strategy `p1-target-strategy-v1`, starting cash
`1000000 USDT`, `TAKER`, and reconciliation source `VENUE`.

## Execution boundary

The authenticated engine enqueue function exists only at source migration
`0013_engine_backtest_enqueue_authority`, while the accepted runtime database
pin remains `0011_engine_backtest_worker_authority`. Even after all supplied
external paths validate, `--execute` therefore returns `BLOCKED` with
`P1_RUNTIME_REVISION_AUTHORITY_UNAVAILABLE`. P1-22 does not bypass Job API
readiness, change the runtime pin, launch PostgreSQL/Nautilus, or claim an E2E
PASS. A separately reviewed runtime-revision authority amendment must close
that seam before native execution evidence can be recorded.

All receipts keep live, production, and network-trading authority false.
