# P1-22 BTCUSDT vertical-slice evidence

Status: `AMENDMENT_QUALIFICATION_PENDING`. The prior `e2193146…` flat-scenario
run exposed one complete native path, but independent review found missing
request-digest evidence plus marked-open and split-fill accounting cases. A
fresh exact-source qualification supersedes that provisional run. Remote
canonical status is unchanged.

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

The Package6 host lane uses `scripts/build_p1_package6_host_authority.py`.
`prepare-static` builds a sealed stage and disposable native/approval material
from an exact clean source identity through the existing offline Release v2
builder. `activate-and-exec` performs one complete static stage attestation,
publishes a fresh six-second PAPER safety snapshot, refreshes only the rotating
activation evidence, and injects that exact in-memory authority into the P1
operator. Generic Job API and worker startup keep their normal full
attestations. The builder is not selectable through their CLI, environment, or
API surfaces. Static, semantic, custodian, source, approval, interpreter, or
safety freshness drift is `BLOCKED` before authenticated enqueue.
The generic Package6 SNAPSHOT/0011 capability is validated only as platform
authority and is never treated as P1 execution approval. The same reviewed
record binds a closed semantic policy containing the exact
`p1-vertical-slice.execute-once` BACKTEST/0014 operation. `activate-and-exec`
consumes that exact operation capability, with the same static authority,
semantic digest, and arguments, immediately after the final stage recheck and
six-second safety refresh.

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

The generic Job API, worker service, systemd composition, and accepted runtime
database pin remain `0011_engine_backtest_worker_authority`. P1-22 has one
dedicated code-owned disposable composition at
`0014_p1_product_closure_rotation`; it has no environment, CLI, client,
service, or production selector.

After complete preflight, `--execute` constructs the existing P1 worker and
checks its exact disposable database identity before authenticated enqueue. It
then performs exactly one worker `run_once` and reloads the exact enqueued job.
`run_once=True` is not success authority. `PASS` requires one terminal
`SUCCEEDED` attempt, the exact result hash, and one persisted P1 result artifact
whose closed `EngineEventBatchReceipt` and `P1PortfolioParityReceipt` agree on
job, attempt, engine run, batch, semantic digest, event count, terminal
sequence, and terminal event digest. The public evidence binds both receipt
digests and the final portfolio state hash. Missing, mixed, failed, blocked,
cancelled, retried, stale, or other-job evidence is `BLOCKED`.

The default lane remains canonical `DEFERRED` and performs no job mutation.
This source seam does not itself launch PostgreSQL or Nautilus and does not
change generic runtime or release authority.

All receipts keep live, production, and network-trading authority false.

## Superseded qualification

The disposable host lane passed on 2026-08-30 with the schema-8 P1
closure
`b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b`.
The focused operator test completed in `93.87s` with `1 passed` and produced:

| Evidence | Value |
|---|---|
| Package6 approval SHA-256 | `3bd2abec4afbc934d6dfa41c39c998095c16145c97881179bf0df007ba1bd7d7` |
| disposable PostgreSQL approval SHA-256 | `110509bb38e3c9cc58ed3eae2662909659dd55164f8ea8c96f95d740b781b735` |
| engine-result/batch SHA-256 | `aec38fb18251791b509623eac726448b4f838b4f80a84185ce914850595ddde0` |
| semantic digest | `bc4fdbfc9fbc5de0455a37158d243ae7026fc6cc5ff3e37a74686eee152a0f66` |
| event sequences | `2..15` (`14` events) |
| final cash / position | `1007781.489627 USDT` / `0` |
| fees / realized / unrealized PnL | `2007.791239` / `9789.280866` / `0` |

The path used one authenticated enqueue, one worker run and one durable flat
result. It is retained as historical evidence only and does not close P1-22.
Disposable PostgreSQL was stopped and removed by the fixture lifecycle. No
production, network, or live authority was granted; absent host inputs still
produce the canonical `DEFERRED` receipt.
