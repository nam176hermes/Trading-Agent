# WS-02C2 Task 3 report — BACKTEST worker lifecycle integration

## Status

Completed from the required Task 3 base
`f814b67ecb44ab3dc3c535280efbe87bc4ae00bf`. The implementation is source-only,
paper-only, and opt-in; the composition root does not discover an engine,
closure, sandbox, transport root, or environment from ambient state.

## Delivered behavior

- Added an opt-in engine BACKTEST path to `JobWorker`. The worker requests the
  exact `(SNAPSHOT, BACKTEST)` claim set only when a complete authority factory,
  spawn provider, and result validator have been explicitly injected. Without
  all three, the existing SNAPSHOT-only claim call and command path are
  unchanged.
- Preserved the database's existing `worker_claim_snapshot` capability and
  migration/schema unchanged. `WorkerRepository` accepts the exact opt-in
  source claim set so an explicitly composed worker does not reject it locally;
  the current database function remains SNAPSHOT-only until a separately
  reviewed activation changes database authority.
- A claimed BACKTEST is executable only when its payload is the exact
  `EngineBacktestPayload`. Legacy `BacktestPayload` claims finalize BLOCKED as
  `ENGINE_BACKTEST_AUTHORITY_REQUIRED` before authority derivation or child
  preparation.
- The accepted Task 1 factory derives the request after the fenced claim. The
  accepted Task 2 provider prepares the opaque engine spawn inside
  `ProcessRunner`, immediately before its hardened Popen boundary. Engine
  timeout authority now comes from the consumed closure; legacy callers must
  still supply the exact command timeout.
- Closure, sandbox, or protected-transport `EngineSpawnError` refusals finalize
  the still-CLAIMED attempt as BLOCKED with the provider's closed reason code.
  No start write, child identity, or stream artifact is fabricated.
- Engine children share the existing worker-owned pre-spawn control, start,
  heartbeat, lease renewal, cancellation, stale-fence behavior, safety
  re-attestation, process-group cleanup, result-validation progress checks,
  retry/reconciliation policy, final safety check, and atomic finalization.
- Added `EngineResultValidator`, which reads only the worker-captured stdout
  artifact through no-follow descriptor-relative traversal, verifies exact
  metadata/digest/size and the one-MiB capture bound, and rejects truncated or
  empty output.
- Stdout must be canonical newline-delimited `EngineEventEnvelope` bytes. The
  validator rejects malformed/noncanonical JSON, duplicate identities,
  non-contiguous sequences, and every event whose correlation, causation,
  engine run, timing, schema, producer, source commit, or config authority does
  not match the derived request.
- Before inspecting output, the validator independently re-derives the complete
  request from the exact `ClaimedJob`, request source commit, and request event
  time. A canonical event batch for another attempt therefore cannot be
  accepted.
- Accepted bytes are sealed under the worker-owned artifact root as a
  content-addressed JSONL artifact. `ValidatedEngineEventBatch` exposes the
  immutable event tuple and bounded validation metadata as the handoff for
  WS-02D. Success uses the existing finalization policy only after this handoff
  and a final lease/cancellation/safety check.
- `build_worker` can receive an explicit `engine_spawn_provider`; only then does
  it construct the worker-owned authority factory and result validator from the
  attested application revision and artifact root. Normal service composition
  supplies no provider, so this packet does not activate BACKTEST execution.

## TDD evidence

1. Result tests first failed because `services.job_worker.engine_results` did
   not exist. The minimal validator then made canonical fixture validation,
   sealing, and all malformed/noncanonical/duplicate/wrong-authority/truncated
   refusals pass.
2. Lifecycle tests first failed because `JobWorker` accepted no engine
   dependencies. The opt-in branch then made authorized success, cancellation
   before spawn/during child/during validation, stale lease, safety drift,
   closure/transport failure, and legacy BACKTEST refusal pass.
3. A process-runner regression first failed when engine callers supplied no
   external timeout. The runner now adopts the consumed closure timeout only
   for exact engine authority while retaining strict legacy timeout equality.
4. A composition regression first failed because `build_worker` had no explicit
   provider seam. The new keyword injects the provider without adding an
   ambient fallback or changing default service behavior.
5. A request-attribution mutation test initially demonstrated that canonical
   output matching a request from another attempt could be accepted. Exact
   request re-derivation from the claimed attempt now closes that gap.
6. A repository regression first failed because the opt-in claim set was
   rejected before the existing database capability call. Only the exact
   `(SNAPSHOT, BACKTEST)` set was added; BACKTEST-only and every other inactive
   combination remain rejected.

## Validation

- Focused authority/spawn/process/lifecycle/result/CLI suites:
  `316 passed`.
- `make check-contracts`: passed; generated contracts are unchanged.
- `make test-core`: `2589 passed, 229 skipped, 16 deselected`.
- `make audit`: passed in strict authority mode for core, backend, and
  dashboard at the required dirty Task 3 tree.
- `git diff --check`: passed.

## Scope exclusions

No event-ledger ingestion, event database table, schema change, migration,
dependency, lockfile, service start, database mutation, network request,
exchange/broker/account/order route, live gate, deployment change, or runtime
activation was added. The sealed batch is only the validated WS-02D handoff;
this task does not claim durable event idempotency or projections.

## Commit

`Integrate engine BACKTEST worker lifecycle`
