# P1-22 BTCUSDT vertical-slice evidence

Status: `ACCEPTED` on source commit
`021ea4b6a4d438863ada926f8585fbdb15111877`, tree
`375cfc0a3c56f97ef060b1c24c98e1fad30863b2`. Fresh exact-source host
qualification and two independent reviews supersede the provisional
`e2193146…` run. Remote canonical status is unchanged.

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
`0017_p1_request_digest_authority`; it has no environment, CLI, client,
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

## Accepted qualification

The exact host test passed with migrations `0001`→`0017` and `1 passed in
94.82s`. A second fresh single-use Package6 stage captured the complete
canonical PASS receipt. Both runs used the unchanged sealed schema-8 closure
`b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b`.

| Evidence | Value |
|---|---|
| qualification source commit / tree | `021ea4b6a4d438863ada926f8585fbdb15111877` / `375cfc0a3c56f97ef060b1c24c98e1fad30863b2` |
| captured PASS receipt SHA-256 | `82a00fc3f067912d78b272175a19e9c038e5c9f90ac95365d88bad6b67da4c85` |
| Package6 approval raw SHA-256 | `d2931996c7750662a0c385d4d87645b8cbde16ea2bc02c548ab76ff0ba4d26b8` |
| runtime authority SHA-256 | `991040f06992e590bcbfff5a377d643efbea11f5d9fde1e69316f12822551251` |
| disposable PostgreSQL approval raw / canonical SHA-256 | `e76042f84efda9ba10c6e1bc87fc3a538ad9f3410d95ca3f9978f8de7bf2459b` / `02592ad264b0e1f38fe7cfdba4681b450f79ae2553ccb802c6a8397c40bdb99e` |
| job / attempt | `job_2ba8c3579432445fa9d701fe5f1e5bd1` / `attempt_54f2dbc94c644eb68c0abc05511f5ab1` |
| engine request SHA-256 | `70274f3dc7bcf4c590d7510ca50c6395b5de18d954691ebf4221d5e3d98643b6` |
| engine result / batch SHA-256 | `aa443a45dd3a4a8be5ca7775cf359fffaf25c507bde20e2810b4d51023fffe2a` |
| event receipt SHA-256 | `f1da38b9544735f1f731ba152c08e70e345f89c957ead28b3819406d69af228f` |
| parity receipt SHA-256 | `ddeb334fb8e2412541c891c9bb94e14023175023b15b844c16da357d62ea90f6` |
| semantic / final portfolio state SHA-256 | `bc4fdbfc9fbc5de0455a37158d243ae7026fc6cc5ff3e37a74686eee152a0f66` / `be64143deaa281250a8d5bd25c0d6e8e6fa0aff44819367766152e0ccfcbcbec` |
| event sequence / count / last digest | `2..15` / `14` / `a0ac18af49841a6b88cfd2ec06166c54c3def171252a7e51008e645433f8f7b6` |
| final job state / worker runs | `SUCCEEDED` / `1` |

The receipt reports one authenticated mutation, exact durable result/parity,
and `live_authorized=false`, `network_trading_authorized=false`, and
`production_authorized=false`. Disposable PostgreSQL was stopped and removed
after each fixture lifecycle.

Independent spec review returned PASS with `0 Critical`, `0 Important` after
`233 passed, 1 skipped`, static, contracts, maintainability, and P1 boundary
checks. Independent quality/security review returned PASS with `0 Critical`,
`0 Important`, `212 passed, 4 expected authority skips`, an additional
55-test authority/package subset, and zero security-scan findings. The review
confirmed exact rational split-fill and repeating-basis accounting, restart and
correction parity, closed request lineage, and unchanged custody/live safety.

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
