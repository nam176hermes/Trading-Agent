# Phase 1–4 Completion Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close only the verifiable residuals from Nautilus-adoption workstreams 01–04: fresh offline provenance evidence, the explicitly deferred disposable-PostgreSQL gate, deterministic non-zero execution parity, and a bounded paper-engine compatibility proof.  Do not expand into production cutover, Release Authority v2, public-data ingestion, or live trading.

**Architecture:** The root Python 3.11 control plane remains dependency-isolated from Nautilus.  All real Nautilus execution stays in the already sealed external CPython 3.12 engine and exchanges only canonical, hash-bound files/envelopes with the root.  A pure-Python reference calculation and the legacy result are untrusted comparison inputs; neither can become runtime authority.  Each repair is a small independently reviewable packet with its own final gate.

**Tech Stack:** Python 3.11 root project, isolated CPython 3.12 Nautilus engine, private Rust 1.95.0/LLVM toolchains, Pydantic contracts, pytest, PostgreSQL 16 only in an explicitly authorised disposable environment.

## Global Constraints

- Keep both live approvals false.  Never contact an exchange, broker, account, order endpoint, or public-data provider.
- Root Python 3.11 must not import Nautilus or acquire a Nautilus dependency.  Do not alter `pyproject.toml` or `uv.lock` for these packets.
- Use only the sealed private Rust/LLVM/wheel caches under `/home/thenam176/.cache/trading-agent/nautilus`; never select a global Rust toolchain.
- Backtests and paper compatibility runs are one-shot, local fixture runs with no network, no database, no persistent service, and no runtime-authority write.
- Do not modify the immutable `codex_plan.zip`.  Maintain a repository-owned status record that links its required evidence instead.
- Do not run any PostgreSQL test until the operator grants approval for a disposable PostgreSQL 16 instance immediately before execution.  The approval must state the connection target is disposable and contain no production data.
- Every changed contract is canonical JSON, strict and hash-bound.  Reject unknown fields, duplicate keys, path escapes, stale digests, and absent authority bindings.
- Run focused tests first.  Each packet then requires `make audit`, `make check-contracts`, the relevant pytest set, and an independent final gate before merge.  Do not call aggregate validation a production action.

## Completion Ledger and Cut Line

The program tracker embedded in `codex_plan.zip` was never updated: it cannot serve as completion evidence.  Git history and prior gate reports show WS01–WS03 source work and WS04A–D merged, but they do not close the residuals below.

| Workstream | Actual residual | Closure packet | Completion evidence |
|---|---|---|---|
| WS01 | Toolchain/provenance cache has not been freshly reverified on the current checkout. | 01D | Offline verifier transcripts and redacted digest-only record. |
| WS02 | Runtime migrations/concurrency proof against PostgreSQL 16 was expressly deferred. | 02E | Required runtime tests against an operator-approved disposable DB. |
| WS03 | No source or acceptance residual identified. | Carry-forward only | Existing final gate plus unchanged-contract checks in 04E/04F. |
| WS04 | 04C is deliberately zero-order; 04D validates external evidence but does not produce non-zero execution or paper compatibility evidence. | 04E0, 04E, 04F | Attested simulation closure, scenario matrix, sealed comparison evidence, and isolated paper compatibility result. |

“Phase 1–4 complete” is permitted only after 01D, 02E, 04E0, 04E, and 04F have each passed their final gate.  Until 02E receives operator approval, report Phase 2 as **source-complete; runtime gate pending operator approval**, not complete.

---

## Task 1: Packet 01D — Fresh Offline Reproducibility Evidence

**Purpose:** Revalidate the already materialized Rust 1.95.0, LLVM, wheel cache, engine artifacts, and provenance policy without network access or rebuilding from unpinned inputs.

**Files:**

- Create: `docs/nautilus-adoption/phase-1-reverification.md`
- Read only: `engines/nautilus/toolchain-inputs.json`, `engines/nautilus/llvm-toolchain-policy.json`, `engines/nautilus/wheel-cache-policy.json`, `engines/nautilus/engine-build-policy.json`
- Read only: `scripts/verify_nautilus_provenance.py`, `scripts/prepare_nautilus_toolchain.py`, `scripts/prepare_nautilus_llvm_toolchain.py`, `scripts/prepare_nautilus_wheel_cache.py`, `scripts/build_nautilus_engine.py`

- [ ] **Step 1: Capture the exact source/policy identities before testing.**
  - Record `git rev-parse HEAD` and SHA-256 values for the four policy files in a local, uncommitted run log.
  - In the committed evidence document record only: checkout commit, policy file names and their SHA-256 values, verifier commands, PASS/FAIL, and the date.  Do not commit cache paths, archive contents, environment variables, or any credentials.

- [ ] **Step 2: Re-run the provenance verifier.**
  - Run: `uv run python scripts/verify_nautilus_provenance.py --root .`
  - Do not pass `--verify-upstream` in this packet: 01D is an offline reproducibility check.  A future upstream refresh is a separately reviewed dependency change.
  - Expected result: the pinned source identity, legal/provenance material, and policy binding pass without downloading anything.

- [ ] **Step 3: Verify every sealed private input and the external artifact.**
  - Run `python3.11 -I scripts/prepare_nautilus_toolchain.py --manifest engines/nautilus/toolchain-inputs.json --cache <sealed-rust-input-cache> --destination <sealed-rust-1.95.0-toolchain> --verify-materialized`. This read-only path must pass; do not use `--acquire` or `--materialize`.
  - Run `python3.11 -I scripts/prepare_nautilus_llvm_toolchain.py --policy engines/nautilus/llvm-toolchain-policy.json --cache <sealed-llvm-input-cache> --destination <sealed-llvm-toolchain> --verify-cache`, then repeat with `--verify-toolchain`.
  - Run `sha256sum <sealed-wheel-cache>/wheel-cache-manifest.json`, copy the printed digest literally, then run `python3.11 -I scripts/prepare_nautilus_wheel_cache.py --policy engines/nautilus/wheel-cache-policy.json --engine-policy engines/nautilus/engine-build-policy.json --cache <sealed-wheel-cache> --verify --manifest-sha256 <copied-wheel-manifest-sha256>` only if the copied digest is the operator-approved value.
  - Run `python3.11 -I scripts/build_nautilus_engine.py --policy engines/nautilus/engine-build-policy.json --python <sealed-cpython-3.12> --artifacts <selected-generation:nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c> --verify --verify-input-bindings --offline --input-cache <sealed-input-cache> --wheel-cache <sealed-wheel-cache> --wheel-cache-manifest-sha256 <copied-wheel-manifest-sha256> --cargo <sealed-rust-1.95.0-cargo> --llvm-toolchain <sealed-llvm-toolchain> --sandbox <approved-sandbox>`.
  - Expected result: all commands verify hash-bound inputs/artifacts and no command accesses the network or a global toolchain.

- [ ] **Step 4: Make failures diagnosable and preserve only safe evidence.**
  - Add the digest-only results table and command template to `phase-1-reverification.md`.
  - Add a negative command to the local run log showing a deliberately wrong wheel-manifest digest is rejected; do not commit the generated error path or cache contents.
  - Verify the document contains no absolute home paths, tokens, archive URLs, or artifact payloads.

- [ ] **Step 5: Gate and merge.**
  - Run `make audit`, `make check-contracts`, and rerun the provenance verifier.
  - Have an independent reviewer inspect the evidence document against the four policies and confirm no verification command performs acquisition.
  - Commit only the documentation evidence with `docs: record WS01 offline re-verification` after final-gate PASS.

## Task 2: Packet 02E — Authorised Disposable PostgreSQL Runtime Gate

**Purpose:** Close the sole Phase 2 residual: execute existing migration, ledger concurrency, and dual-read runtime tests against PostgreSQL 16.  This packet intentionally has no schema or application feature work.

**Files:**

- Create after an authorised run: `docs/nautilus-adoption/phase-2-runtime-verification.md`
- Read only: `scripts/run_required_runtime_pytest.py`, `Makefile`, `tests/control_api/test_postgres_api.py`, `tests/control_api/test_postgres_repositories.py`, `tests/control_api/test_alembic_schema.py`, `tests/event_ledger/test_snapshot_postgres_runtime.py`, `tests/control_api/test_dual_read.py`

- [ ] **Step 1: Stop at the authorisation gate.**
  - Before creating a database, starting a container/service, setting `DATABASE_URL`, or running a runtime target, obtain explicit operator approval for one disposable PostgreSQL 16 endpoint.
  - Record the approval reference, PostgreSQL major version, and the statement “disposable, no production data” in the evidence document.  Never record the DSN, hostname, username, password, or database contents.
  - If approval is absent, create no database and leave the packet status as `PENDING_OPERATOR_APPROVAL`; do not infer approval from this plan.

- [ ] **Step 2: Prove the runtime harness rejects a missing database.**
  - In a shell with no database DSN configured, run the required-test wrapper once for a single runtime test.
  - Expected result: it fails closed instead of silently skipping.  Record only the exit status and rejection class in the evidence document.

- [ ] **Step 3: Run the required PostgreSQL evidence set after approval.**
  - Configure the approved disposable endpoint only for the current shell.
  - Run `make test-runtime-postgres`.
  - Run `make test-event-ledger-runtime-postgres`.
  - Run `make test-runtime-dual-read`.
  - Expected result: every selected test executes (not skipped), migrations are exercised only in the disposable database, event-ledger concurrency assertions pass, and the DB is left disposable for the operator's cleanup policy.

- [ ] **Step 4: Write a non-sensitive evidence record.**
  - State the source commit, test target names, PostgreSQL major version, approval reference, executed/failed/skipped counts, and PASS/FAIL.
  - Explicitly state that no production database, migration target, scheduler, or service was touched.
  - Do not change migrations or application code unless a failing test exposes a real defect; such a defect opens a new tightly scoped fix packet with regression coverage.

- [ ] **Step 5: Gate and merge.**
  - Run `make audit`, `make check-contracts`, and the three runtime targets again only if the disposable environment is still authorised and clean.
  - Require an independent final-gate review that checks for an executed, non-skipped runtime report and the absence of DSNs/secrets.
  - Commit only the evidence document with `docs: close WS02 disposable PostgreSQL runtime gate` after PASS.

## Task 3: Packet 04E0 — Attested Simulation Transport and Runtime Closure

**Purpose:** Add the minimal closed protocol needed for 04E to reach the existing isolated CPython 3.12 spawn path.  This packet creates a distinct simulation command, a fifth hash-bound scenario input, a distinct closure/result-validator identity, and an offline materializer for a new runtime-closure generation.  It must not weaken or overload 04C's `RunBacktest` zero-order path.

**Files:**

- Create: `scripts/materialize_nautilus_runtime_closure.py`
- Create: `engines/nautilus/runtime-closure-policy.json`
- Create: `tests/foundation/test_nautilus_runtime_closure.py`
- Modify: `packages/engine_contracts/commands.py`
- Modify: `packages/engine_contracts/__init__.py`
- Modify: `packages/job_contracts/payloads.py`, `packages/job_contracts/api.py`, and `packages/job_contracts/__init__.py`
- Modify: `services/job_worker/engine_artifacts.py`
- Modify: `services/job_worker/engine_authority.py` and `services/job_worker/worker.py`
- Modify: `services/job_worker/engine_spawn.py`
- Modify: `services/job_worker/nautilus_closure.py`
- Modify: `services/job_worker/engine_results.py`
- Modify: `engines/nautilus/launcher/nautilus_backtest.py`
- Modify: `scripts/generate_contracts.py` and generated engine/Job API contract outputs through `make generate-contracts`
- Modify: `tests/engine_contracts/test_commands.py`
- Modify: `tests/engine_contracts/test_contract_generation.py`
- Modify: `tests/jobs/test_engine_artifacts.py`
- Modify: `tests/jobs/test_engine_spawn_provider.py`
- Modify: `tests/jobs/test_nautilus_closure.py`
- Modify: `tests/jobs/test_engine_result_validation.py`
- Modify: `tests/jobs/test_engine_authority.py`, `tests/jobs/test_engine_worker_lifecycle.py`, and `tests/control_api/test_generation.py`
- Modify: `tests/nautilus_backtest/test_launcher_protocol.py`
- Modify: `docs/nautilus-adoption/phase-1-reverification.md`

- [ ] **Step 1: Add a distinct closed simulation command and input identity.**
  - Add `RunBacktestSimulation`, never an optional profile on `RunBacktest`.  It carries the existing four artifact references, a required `simulation_scenario: ArtifactReference`, and the same strict canonical UTC window.
  - Keep `RunBacktest` byte-for-byte schema/semantics unchanged.  The two payload types must be disjoint in the engine-command union and every type guard must reject a forged/ambiguous command.
  - Tests first: reject missing scenario, a duplicate artifact reference, unknown fields, changed digest/media type, and a simulation command passed to a zero-order-only validator.

- [ ] **Step 2: Carry the fifth artifact through the authoritative spawn path.**
  - Generalise only the typed internal input sequence so the resolver selects exactly four named inputs for `RunBacktest` and exactly five for `RunBacktestSimulation`; never infer names from ambient request keys.
  - `HashBoundArtifactResolver`, sealed memfd snapshots, and Bubblewrap mounts must attest `simulation_scenario` exactly like the existing inputs.  It must be an external 0400 regular file, no symlink/ancestor escape, and included in the request/input digest.
  - Extend the durable job payload and worker authority factory so a claimed BACKTEST job derives `RunBacktestSimulation` only from its own fifth reference.  Refresh the disjoint Job API/engine contract outputs using `make generate-contracts`; never accept the scenario from a caller-provided engine envelope.
  - Tests first: missing scenario binding, a changed **simulation-scenario-only** inode/digest after prepare, an extra mounted input, a caller-forged simulation envelope, or a simulation request accepted by a four-input profile all fail closed.

- [ ] **Step 3: Attest two immutable closure profiles.**
  - Extend the closure manifest schema with an explicit `profile` field.  The only values are `zero-order` and `execution-simulation`.
  - `zero-order` retains `nautilus-backtest-result-v1` and the existing launcher argv.  `execution-simulation` uses `nautilus-backtest-simulation-result-v1` and an argv prefix that passes a literal `--profile execution-simulation` to the same sealed launcher.
  - The root attestor takes an explicit expected profile; it must reject a closure manifest with a profile/validator/argv mismatch and may never select a profile from an environment variable, file name, or caller-controlled string.
  - Tests first: a zero-order closure cannot launch a simulation command, an execution-simulation closure cannot launch a zero-order command, and tampering any profile/argv/validator/launcher digest fails before spawn.

- [ ] **Step 4: Keep stdout as the only sealed result transport.**
  - Do not add a writable output mount or output-path capability.  Both profiles emit exactly one canonical JSONL `EngineEventEnvelope` on captured stdout, which remains sealed by `EngineResultValidator`.
  - Add the explicit simulation validator ID to its allowlist.  It accepts only `RunBacktestSimulation`, the simulation completion event type, the exact five-input digest, and a scenario digest; it must reject all non-zero effects under the existing zero-order validator.
  - Tests first: a simulated event under the zero-order validator, a zero-order event under the simulation validator, an event with four-input digest, and a changed scenario digest all fail closed.

- [ ] **Step 5: Add a reproducible offline runtime-closure materializer.**
  - `materialize_nautilus_runtime_closure.py` consumes a strict policy, the sealed CPython 3.12 runtime base, the selected 01D engine artifact generation, and the repository launcher bytes.  It must create a **new**, previously absent external runtime-closure generation atomically; no existing closure is modified.
  - The policy binds exact base runtime manifest/artifact manifest digests, expected source launcher path, profile manifest schema, file inventory, modes, and source commit.  The materializer verifies all inputs before copying, produces only 0500 roots/0400 or 0500 regular sealed files, writes a canonical closure manifest, and attests the sealed private staging tree with the root attestor **before** it becomes visible at the final destination.  It must re-attest destination identity after rename.
  - The materializer has no acquisition mode and must use no network, global Python/Rust, or ambient dependency location.  Retain `runtime-closure-v3` untouched as rollback.
  - Tests first: pre-existing destination, base/artifact/launcher digest drift, unlisted file, profile mismatch, unsafe file mode, failed pre-publish attestation, and atomic publish failure all leave no selected generation and fail closed.

- [ ] **Step 6: Materialize and verify the selected simulation closure.**
  - After source tests pass, materialize an external generation named `runtime-closure-v6-simulation` below the private Nautilus cache using only the current sealed inputs and the selected 01D artifact.  Do not commit its files.  Retain `runtime-closure-v4-simulation` and `runtime-closure-v5-simulation` as rejected forensic candidates: do not select, overwrite, or delete them.
  - Run independent read-only closure attestation for both `runtime-closure-v3` (zero-order) and the new v6 generation (execution-simulation), and run the selected 01D full input-binding verifier before and after materialization.
  - Commit source/tests/docs only as `feat: attest Nautilus execution-simulation closure` after focused tests, `make audit`, and `make check-contracts` pass.

## Task 4: Packet 04E — Deterministic Non-Zero Backtest Parity

**Purpose:** Replace the deliberate 04C zero-order limitation with a bounded, fixture-only execution-simulation profile.  It must prove long/short accounting, partial fills, same-bar stop/take-profit precedence, stale quote rejection, zero liquidity, session boundaries, and deterministic event digests in the isolated engine.

**Files:**

- Create: `packages/nautilus_backtest/scenarios.py`
- Create: `packages/nautilus_backtest/reference.py`
- Create: `tests/nautilus_backtest/test_scenarios.py`
- Create: `tests/nautilus_backtest/test_reference.py`
- Create: `tests/nautilus_backtest/test_execution_parity.py`
- Modify: `packages/nautilus_backtest/result.py`
- Modify: `packages/nautilus_backtest/__init__.py`
- Modify: `engines/nautilus/launcher/nautilus_backtest.py`
- Modify: `packages/nautilus_engine_cli/cli.py`
- Modify: `tests/nautilus_backtest/test_result.py`
- Modify: `tests/nautilus_engine_cli/test_cli.py`

- [ ] **Step 1: Write strict scenario and expected-result contracts first.**
  - Add immutable `BacktestScenarioV1` and `BacktestExpectedOutcomeV1` models with canonical JSON rendering and SHA-256 digest methods.
  - A scenario must bind: fixture catalog entry digest, instrument identity, ordered market events, target position/weight, fee/slippage parameters, liquidity limit, stale-quote threshold, session policy, stop/take-profit values, and precedence rule.
  - Permit only the eight named scenarios: `long-accounting`, `short-accounting`, `partial-fill`, `same-bar-stop-take-profit`, `stale-quote`, `zero-liquidity`, `session-boundary`, and `event-digest`.
  - Reject floats, unknown fields, duplicated keys, non-finite decimals, unbound catalog entries, and all paths outside the hash-bound input mount.
  - Tests first: prove canonical round-trip/digest stability and one rejection per forbidden condition.

- [ ] **Step 2: Generalise the zero-order result validation without weakening it.**
  - Refactor `packages/nautilus_backtest/result.py` so `NautilusBacktestResult` binds `scenario_digest`, execution counts, position state, fees, realised/unrealised P&L, and deterministic event digest.
  - Preserve the existing fixed zero-order rule as an explicit `zero-order` scenario profile; do not make it silently accept execution effects.
  - Add a separate validation branch for the `execution-simulation` profile.  It must compare every result field against `BacktestExpectedOutcomeV1` and reject a missing or mismatched scenario digest.
  - Tests first: existing zero-order tests remain green; a non-zero result fails under `zero-order` and passes only with an exactly matching execution scenario.

- [ ] **Step 3: Implement a pure-Python deterministic reference calculator.**
  - `reference.py` consumes only `BacktestScenarioV1`, performs Decimal-only accounting, and returns the same outcome fields/digest schema as the engine result.  It is a test oracle, not an execution authority.
  - Define same-bar stop/take-profit precedence as an explicit contract value; test both the accepted policy and a rejected unknown policy.
  - Tests first: fixture examples cover each of the eight named scenarios, including long/short fee accounting, residual order on partial fill, no fill for stale/zero-liquidity inputs, and deterministic session transition.

- [ ] **Step 4: Extend the isolated launcher and CLI with a fixture-only simulation command.**
  - Keep the current `nautilus-backtest` command's zero-order behavior unchanged.
  - Add an explicit `nautilus-backtest-simulate` envelope path in `packages/nautilus_engine_cli/cli.py`; it accepts a sealed scenario input and writes only a sealed result envelope to its authorised output path.
  - In `engines/nautilus/launcher/nautilus_backtest.py`, accept only the `execution-simulation` profile, apply no provider/broker configuration, mount only the exact catalog/scenario files read-only, and execute the deterministic in-process fixture feed.
  - Construct orders from the bound target positions using the scenario's execution parameters.  No subprocess may receive a host root bind, writable input mount, network socket, database DSN, or arbitrary strategy/module path.
  - Tests first: malformed envelopes, changed input digest, non-zero target under the old command, unexpected mount, and network/provider configuration all fail closed.  Valid scenario produces a result envelope whose digest matches the root validator.

- [ ] **Step 5: Run differential parity tests and make divergence actionable.**
  - For all eight scenarios, invoke the isolated CPython 3.12 engine through the normal hash-bound spawn path, parse its sealed result in Python 3.11, and compare it field-for-field to the pure-Python reference outcome.
  - A mismatch must include scenario digest and field names only; never echo unrestricted fixture data or paths.
  - Add a deterministic replay test: two independent fixture runs produce byte-identical canonical result envelopes and event digests.

- [ ] **Step 6: Gate and merge.**
  - Run the focused `tests/nautilus_backtest` and `tests/nautilus_engine_cli` suites, `make audit`, `make check-contracts`, `make test-all`, and `make verify-nautilus-engine` using only the sealed external cache.
  - Independent reviewer verifies root Python has no Nautilus import/dependency, all simulator inputs are hash-bound, and tests cover all eight roadmap cases.
  - Commit as `feat: add WS04 deterministic execution parity` only after final-gate PASS.

## Task 5: Packet 04F — Sealed Differential Evidence and Paper-Engine Compatibility

**Purpose:** Prove that the same strategy intent used in 04E is paper-engine compatible and create the actual sealed evidence that 04D can evaluate.  This is a one-shot fixture compatibility smoke, never a persistent paper service and never a public-data or broker integration.

**Files:**

- Create: `packages/research_validation/producers.py`
- Create: `packages/nautilus_backtest/paper_compat.py`
- Create: `tests/research_validation/test_producers.py`
- Create: `tests/nautilus_backtest/test_paper_compat.py`
- Modify: `packages/research_validation/models.py`
- Modify: `packages/research_validation/artifacts.py`
- Modify: `packages/research_validation/__init__.py`
- Modify: `packages/nautilus_engine_cli/cli.py`
- Modify: `engines/nautilus/launcher/nautilus_backtest.py`
- Modify: `docs/upgrade-plan/ws04-closure.md`

- [ ] **Step 1: Define a strategy-intent binding shared by simulation and paper compatibility.**
  - Add a strict `StrategyIntentV1` containing the target portfolio, scenario digest, risk/position limits, and execution-simulation profile digest.
  - 04E must emit this binding; 04F must reject hand-authored or digest-mismatched intent.  The paper compatibility launch must consume exactly this binding, not a Python strategy import path.
  - Tests first: canonical round-trip, unknown field rejection, and a changed target/risk limit invalidate the binding.

- [ ] **Step 2: Add a one-shot isolated paper compatibility launcher.**
  - Add a `nautilus-paper-compat` command that accepts the sealed strategy-intent and the same fixture/capability mounts as 04E, instantiates the matching Nautilus strategy configuration, processes a finite fixture sequence, writes one sealed compatibility result, and exits.
  - It must not start `StartPaperEngine` as a long-lived worker, bind a port, configure a data/execution client, access a provider/broker, or persist runtime authority.  It is an API/strategy compatibility test only.
  - Tests first: command rejects network/provider/broker settings, arbitrary module paths, writable fixture mounts, and a mismatched intent digest; valid input exits and emits the expected sealed result.

- [ ] **Step 3: Produce hash-bound differential evidence instead of accepting an unverified assertion.**
  - Implement `produce_research_evidence(...)` in `packages/research_validation/producers.py`.  It accepts only: a sealed 04E engine result, a sealed reference result, a sealed legacy projection artifact, and a sealed 04F compatibility result.
  - Verify every digest and contract identity, compare `metric_values`, strategy intent, event digest, and scenario membership, then write `ResearchEvidenceArtifactReference` in the existing 04D schema.  The legacy projection remains comparison-only and cannot request promotion or execution.
  - Extend `ResearchGateEvidence` only with explicit compatibility and scenario bindings required to make the comparison unambiguous; preserve fail-closed parsing for pre-04F evidence.
  - Tests first: happy-path evidence closes through `close_ws04_research`; each altered digest, legacy mismatch, mismatched strategy intent, missing scenario, and fake paper result is rejected.

- [ ] **Step 4: Evaluate the generated evidence through the existing 04D closure.**
  - For every 04E scenario, produce the sealed reference/engine/legacy/paper inputs, run the producer, then call `close_ws04_research` with the producer's reference.
  - Confirm the 04D audit event records a pass only for exact matches and contains no promotion authority, database write, network action, or execution approval.
  - Tests first: aggregate campaign fails if any one required scenario lacks a passing 04D closure; an all-pass campaign yields a deterministic campaign digest.

- [ ] **Step 5: Document the boundary and update the repository-owned tracker.**
  - Amend `docs/upgrade-plan/ws04-closure.md` to distinguish offline external evidence acceptance (04D) from the new locally produced, sealed differential evidence (04F).
  - Create `docs/nautilus-adoption/program-tracker.md`.  It must cite the immutable ZIP tracker as the planning source, list 01D/02E/04E/04F evidence locations, and use the exact status terms `COMPLETE`, `PENDING_OPERATOR_APPROVAL`, or `NOT_STARTED`.
  - Do not state program completion unless all four closure packets have PASS evidence; the tracker must say explicitly that none of these packets authorises production, live trading, or a persistent paper runtime.

- [ ] **Step 6: Gate and merge.**
  - Run focused `tests/research_validation` and `tests/nautilus_backtest`, then `make audit`, `make check-contracts`, `make test-all`, and `make verify-nautilus-engine`.
  - Independent final gate verifies the command is finite/local, compares same strategy intent across simulation and paper compatibility, checks all evidence is hash-bound, and confirms neither root Python nor the legacy artifact gains authority.
  - Commit as `feat: close WS04 paper compatibility evidence` only after PASS.

## Task 6: Final Program Gate

- [ ] Confirm 01D evidence passes on the final merge commit and the private toolchain cache remains the only selected Rust/LLVM/wheel source.
- [ ] Confirm 02E has an operator-approved, non-skipped PostgreSQL 16 runtime report.  If it does not, stop and report the program as pending; do not waive this criterion.
- [ ] Confirm existing WS03 final-gate tests still pass and no 04E/04F contract change weakens decimal-only, explicit-state, fail-closed behavior.
- [ ] Confirm 04E’s eight scenario results and 04F’s paper-compatibility results close through the 04D evidence evaluator.
- [ ] Run `make audit`, `make check-contracts`, `make test-all`, and `make verify-nautilus-engine`; record commands and outcome in `program-tracker.md` without secrets or absolute runtime paths.
- [ ] Request one final independent Codex review focused on the diff from the pre-01D commit through the candidate merge commit.  Merge only on PASS, then mark the tracker `COMPLETE`.

## Deferred by Design

The following stay outside this completion plan and must not be inferred from a PASS: Release Authority v2, production cutover, actual brokerage/exchange access, public-data adapters, persistent paper workers, promotion APIs, database production migration, and any live-execution capability.
