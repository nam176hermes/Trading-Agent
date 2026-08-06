# WS-04D Mandatory Research Gates Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Add deterministic, fail-closed research evidence gates for the WS-04 fixture/catalog → target strategy → isolated Nautilus backtest path, and produce a hash-bound WS-04 closure record only when every required gate passes.

**Architecture:** A new root-Python `packages.research_validation` package accepts only typed, immutable evidence. It validates point-in-time observations, recursive replay attestations, walk-forward folds, cost stress scenarios, and a three-way reference/legacy/Nautilus comparison. A closure function binds the accepted report to the existing 04A catalog manifest and exact 04C command/result validation. Legacy evidence is comparison-only and cannot be selected as promotion authority. The package is pure Python 3.11 and never imports Nautilus or the legacy research backend.

**Tech Stack:** Python 3.11, Pydantic v2 strict immutable models, `Decimal`, existing `packages.data_catalog`, `packages.engine_contracts`, and `packages.nautilus_backtest` canonical SHA-256 helpers.

---

### Task 1: Establish strict, hash-bound research evidence contracts

**Files:**
- Create: `packages/research_validation/__init__.py`
- Create: `packages/research_validation/models.py`
- Create: `tests/research_validation/test_models.py`

**Step 1: Write the failing tests**

Add fixture builders for a `MarketDatasetManifestV1` and well-formed research evidence. Cover: strict rejection of unknown/float fields; a lookahead observation where `known_at` or feature event time follows the decision timestamp; an unstable recursive replay pair; duplicate/non-canonical indicator records; incomplete provenance bindings; and a legacy result selected as authority.

**Step 2: Run the focused tests to verify they fail**

Run: `uv run pytest tests/research_validation/test_models.py -q`
Expected: FAIL because the research-validation package does not exist.

**Step 3: Implement the smallest contracts**

Implement strict frozen Pydantic models with `schema_version="research-gate-evidence-v1"`:

- `PointInTimeObservation` binds one decision timestamp to a visible feature timestamp, source data digest, and `known_at`; it requires `feature_event_at <= known_at <= decision_at` and a non-empty source digest.
- `RecursiveIndicatorReplay` binds an indicator name, seed digest, prefix digest, replay digest, and positive sample count; the two state digests must be equal.
- `WalkForwardFold` has ordered train/validation/out-of-sample UTC intervals plus a bounded `Decimal` OOS return; folds will later be checked for non-overlap.
- `CostScenario` has a closed scenario name, fee/slippage bps, and net return.
- `ComparisonRecord` accepts only `reference`, `legacy`, or `nautilus`; it carries exact input/result/event digests and either `MATCH` or a named, non-empty explained difference.
- `ResearchProvenanceV1` binds the 04A manifest content/canonical-row digests, the four 04C input artifact digests, the validated 04C result digest, and a source commit.
- `ResearchGateEvidenceV1` contains canonically sorted tuples of all records and declares `promotion_authority="reference-and-nautilus"` as the only accepted authority.

All timestamps use the existing canonical UTC type; all hashes and commit values reuse engine-contract aliases. No file paths, provider dictionaries, live/paper execution controls, or external authority records are accepted.

**Step 4: Run the focused tests**

Run: `uv run pytest tests/research_validation/test_models.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add packages/research_validation tests/research_validation
git commit -m "feat(research): define hash-bound gate evidence"
```

### Task 2: Evaluate every required 04D gate deterministically

**Files:**
- Create: `packages/research_validation/evaluator.py`
- Create: `tests/research_validation/test_evaluator.py`
- Modify: `packages/research_validation/__init__.py`

**Step 1: Write failing evaluator tests**

Cover a complete passing report plus one independent blocking test for each mandatory gate: lookahead; recursive indicator stability; walk-forward temporal overlap or aggregate OOS shortfall; fee/slippage sensitivity monotonicity or stressed return shortfall; benchmark missing comparator/input drift/unexplained difference; and provenance drift. Verify evaluating identical evidence twice yields the same report digest.

**Step 2: Run the focused tests to verify they fail**

Run: `uv run pytest tests/research_validation/test_evaluator.py -q`
Expected: FAIL because `evaluate_research_gates` does not exist.

**Step 3: Implement a fail-closed evaluator**

Implement a frozen `ResearchGateReportV1` with a closed `GateName`/`GateStatus` family, deterministic failure codes, and a canonical `report_sha256`. `evaluate_research_gates(evidence)` must emit all six required gates:

1. `lookahead` validates every point-in-time observation and requires at least one.
2. `recursive_indicator_stability` requires at least one stable replay for every declared indicator.
3. `walk_forward` requires two or more chronologically non-overlapping folds, no train/validation/OOS overlap, and aggregate OOS return at or above the supplied immutable threshold.
4. `fee_slippage_sensitivity` requires baseline, fee stress, slippage stress, and combined stress scenarios; stress costs cannot be less than baseline and every stressed net return must meet the declared threshold.
5. `benchmark_comparison` requires exactly reference, legacy, and Nautilus comparison records against one input digest; only `MATCH` or named explained differences pass, and legacy is never authority.
6. `provenance_verification` checks the embedded `MarketDatasetManifestV1` against its bound content/canonical-row digests, matches the 04C market-data artifact to the canonical rows digest, and verifies the supplied `NautilusBacktestResult` values.

Any model-validation or semantic mismatch produces a failed gate report rather than a partial PASS; success requires all six explicit PASS statuses.

**Step 4: Run the focused tests**

Run: `uv run pytest tests/research_validation/test_evaluator.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add packages/research_validation tests/research_validation
git commit -m "feat(research): evaluate mandatory backtest gates"
```

### Task 3: Bind the report to the 04A–04C run and close WS-04 safely

**Files:**
- Create: `packages/research_validation/closure.py`
- Create: `tests/research_validation/test_closure.py`
- Modify: `packages/research_validation/__init__.py`
- Create: `docs/upgrade-plan/ws04-closure.md`

**Step 1: Write failing closure tests**

Build a real `EngineCommandEnvelope`/`EngineEventEnvelope` fixture from the existing 04C test convention. Cover: a PASS closure is stable across repeated invocation; result-envelope tampering is rejected by `validate_isolated_backtest_result`; report/manifest/request artifact drift blocks; non-PASS reports never emit a closure; and no legacy comparison can become a promotion authority.

**Step 2: Run the focused tests to verify they fail**

Run: `uv run pytest tests/research_validation/test_closure.py -q`
Expected: FAIL because `close_ws04_research` does not exist.

**Step 3: Implement the closure boundary**

Implement `close_ws04_research(manifest, request, event, evidence) -> Ws04ClosureV1`:

- validates the 04C envelope only through `validate_isolated_backtest_result`;
- requires `evidence.provenance` to bind the returned result and all four request artifact hashes, including `market_data.sha256 == manifest.canonical_rows_sha256`;
- evaluates all research gates and raises `ResearchClosureError` unless the report is wholly PASS;
- returns a frozen, canonical closure record containing only the manifest content digest, canonical-row digest, target/config/catalog/market-data artifact hashes, validated backtest result digest, research report digest, source commit, and a closure SHA-256.

Do not write to a runtime filesystem, start a worker, alter a scheduler, or create a promotion API. The closure is proof for WS-04 source acceptance, not operational or trading authority.

Document those boundaries and the required 04D evidence in `docs/upgrade-plan/ws04-closure.md`.

**Step 4: Run focused and surrounding tests**

Run:
```bash
uv run pytest tests/research_validation tests/nautilus_backtest/test_isolated_backtest.py tests/data_catalog/test_manifests.py -q
```
Expected: PASS.

**Step 5: Commit**

```bash
git add packages/research_validation tests/research_validation docs/upgrade-plan/ws04-closure.md
git commit -m "feat(research): close ws04 with mandatory evidence gates"
```

### Task 4: Independent review, final gate, and local merge

**Files:**
- Review the complete branch diff and run package-specific tests, then the repository’s safe gates.

**Step 1: Independent reviewer**

Request an independent Codex review of `main...codex/ws04d-research-gates`, concentrating on point-in-time leakage, authority escalation, canonical digest binding, Pydantic strictness, and accidental Nautilus/legacy imports. Resolve every blocking finding with tests.

**Step 2: Final gate**

Run:
```bash
make audit
make check-contracts
env TMPDIR=/tmp TEMP=/tmp TMP=/tmp make ci
```
Expected: PASS. Also verify `git status --short` is clean apart from intended changes and `git diff main...HEAD` contains no runtime activation or dependency graph change.

**Step 3: Local merge only after PASS**

Merge the reviewed branch into local `main` with a non-fast-forward merge commit. Do not push or change remotes.
