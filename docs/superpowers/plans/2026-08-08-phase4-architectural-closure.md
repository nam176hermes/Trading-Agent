# Phase 4 Architectural Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the order-dependent Python import-state sanitizer with a native-guarded stdlib-first sealed-wheel contract, prove the exact 8×2 backtest campaign and same-strategy finite paper compatibility, close the 04D campaign evidence, then record reviewed Phase 4 evidence and merge locally.

**Architecture:** Native guard, Bubblewrap, closure attestation, and `EngineSpawnProvider` remain the process and filesystem authority. The launcher validates a stdlib-only initial path, appends only private hash-bound extracted wheel roots after stdlib, and leaves CPython module state untouched. Closure schema 6 binds that import model; diagnostic and parity share one bounded process-launch primitive.

**Tech Stack:** Python 3.11 control plane, isolated CPython 3.12.3 engine, NautilusTrader 1.227.0, Rust/Cargo 1.95.0 native guard, Bubblewrap, Pydantic v2, pytest, SHA-256 canonical JSON.

## Global Constraints

- Preserve v12 through v12-r8 byte-for-byte as rejected forensic generations; never select, rerun, rename, overwrite, or delete them. Reserve v13 for paper compatibility and use v12-r9 for the final simulation successor.
- Root Python 3.11 must never import Nautilus; all engine execution remains isolated CPython 3.12.
- No network, database, provider, broker, account, paper, live, protected-config, service, scheduler, or global-Rust mutation.
- Live approvals remain false; the work authorizes no deployment or trading.
- Raw diagnostic, event, fixture, environment, and transient private-path data remains external and never enters Git or reports.
- Every source task uses RED→GREEN tests, an independent task review, and an actual committed diff.
- An official external generation requires explicit operator authorization and an absent no-clobber destination.
- Final aggregate gates run in a clean detached checkout of the exact candidate commit; preserve the shared worktree's untracked files.

---

### Task 1: Replace Python Module-State Sanitization

**Files:**
- Modify: `engines/nautilus/launcher/nautilus_backtest.py`
- Modify: `tests/nautilus_backtest/test_launcher_protocol.py`
- Create: `docs/adr/0004-native-guarded-sealed-wheel-imports.md`

**Interfaces:**
- Consumes: native guard exact argv and `_require_production_stdlib_sys_path()`.
- Produces: `_sealed_dependency_path_scope(roots: tuple[Path, ...])` and `_extract_sealed_wheels(wheels_root: Path, extraction_root: Path) -> tuple[Path, ...]`.

- [ ] **Step 1: Write the failing architecture regressions.**

Add direct `python3.12 -I -S` subprocess tests proving: initial path is stdlib-only; a module under an explicit sealed root imports; a module beside the current directory does not import; standard-library lazy parent/child imports work; `sys.modules` and `sys.meta_path` identities are not rewritten; and `sys.path` is restored on success and error.

- [ ] **Step 2: Run the regressions and require RED.**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_launcher_protocol.py \
  -k 'sealed_dependency_path or no_module_state_sanitizer'
```

Expected: failure because `_sealed_wheel_import_scope` still mutates module and finder state.

- [ ] **Step 3: Implement the bounded path scope.**

Use this contract:

```python
@contextmanager
def _sealed_dependency_path_scope(roots: tuple[Path, ...]):
    _require_production_stdlib_sys_path()
    original = tuple(sys.path)
    resolved = tuple(str(root.resolve(strict=True)) for root in roots)
    if len(resolved) != len(set(resolved)):
        raise ValueError("sealed dependency roots must be distinct")
    sys.path[:] = [*original, *resolved]
    try:
        yield
    finally:
        sys.path[:] = original
```

Delete `_SealedWheelFinder`, trusted-preload maps, provenance repair helpers,
and all `sys.modules`/`sys.meta_path`/parent-attribute synchronization. Refactor
the duplicated wheel extraction into `_extract_sealed_wheels`; retain existing
path traversal, symlink, directory, and bad-ZIP rejection. Both zero-order and
simulation profiles call the same helper and path scope. Add regressions that
a sealed top-level `json` or `importlib` package cannot shadow stdlib and that
wheel-root precedence is deterministic without changing stdlib precedence.

- [ ] **Step 4: Run focused and affected source tests.**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_launcher_protocol.py \
  tests/nautilus_backtest/test_isolated_backtest.py \
  tests/nautilus_backtest/test_target_strategy_source.py
make audit
make check-contracts
make check-broad-handler-inventory
git diff --check
```

- [ ] **Step 5: Commit and independently review.**

```bash
git add engines/nautilus/launcher/nautilus_backtest.py \
  tests/nautilus_backtest/test_launcher_protocol.py \
  docs/adr/0004-native-guarded-sealed-wheel-imports.md
git commit -m "fix: replace Python import-state authority"
```

The reviewer must verify that no ambient path is admitted and that native
guard/Bubblewrap—not mutable Python state—owns process admission.

---

### Task 2: Bind the Import Architecture in Closure Schema 6

**Files:**
- Modify: `engines/nautilus/runtime-closure-policy.json`
- Modify: `scripts/materialize_nautilus_runtime_closure.py`
- Modify: `services/job_worker/nautilus_closure.py`
- Modify: `services/job_worker/engine_spawn.py`
- Modify: `tests/foundation/test_nautilus_runtime_closure.py`
- Modify: `tests/jobs/test_nautilus_closure.py`
- Modify: `tests/jobs/test_engine_spawn_provider.py`

**Interfaces:**
- Consumes: reviewed Task 1 source commit and launcher digest.
- Produces: schema-6 `dependency_import_policy="native-guarded-stdlib-first-sealed-wheel-path-v1"` in policy, manifest, attestation digest, and spawn validation.

- [ ] **Step 1: Add RED downgrade and identity tests.**

Tests must reject schema 6 when the field is missing, unknown, boolean, or
changed after prepare; reject schema 5 when the provider expects 6; and prove
the attestation digest changes when only the import policy changes. Legacy
schema 1–5 parsing remains unchanged for rollback/forensic reads.

- [ ] **Step 2: Run the schema tests and require RED.**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/foundation/test_nautilus_runtime_closure.py \
  tests/jobs/test_nautilus_closure.py \
  tests/jobs/test_engine_spawn_provider.py \
  -k 'schema or import_policy or downgrade'
```

- [ ] **Step 3: Implement schema 6.**

Add `dependency_import_policy` to the exact schema-6 field set and
`CompleteEngineClosureAttestation`. Require the exact literal
`native-guarded-stdlib-first-sealed-wheel-path-v1` during materialization, attestation,
prepare, and consume. Include it in the canonical closure digest. Set
`profile_manifest_schema_version` to `6`; set policy `source_commit` to the
reviewed Task 1 commit; update only reviewed launcher/source identities.

- [ ] **Step 4: Run gates, commit, and review.**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/foundation/test_nautilus_runtime_closure.py \
  tests/foundation/test_nautilus_native_entry_guard.py \
  tests/jobs/test_nautilus_closure.py \
  tests/jobs/test_engine_spawn_provider.py
make audit
make check-contracts
git diff --check
git add engines/nautilus/runtime-closure-policy.json \
  scripts/materialize_nautilus_runtime_closure.py \
  services/job_worker/nautilus_closure.py services/job_worker/engine_spawn.py \
  tests/foundation/test_nautilus_runtime_closure.py \
  tests/jobs/test_nautilus_closure.py tests/jobs/test_engine_spawn_provider.py
git commit -m "feat: bind sealed dependency import authority"
```

---

### Task 3: Unify Diagnostic and Parity Process Custody

**Files:**
- Create: `packages/nautilus_backtest/runtime_process.py`
- Modify: `packages/nautilus_backtest/__init__.py`
- Modify: `scripts/diagnose_nautilus_v12_runtime_failure.py`
- Modify: `scripts/verify_nautilus_v12_r3_parity.py`
- Create: `tests/nautilus_backtest/test_runtime_process.py`
- Modify: `tests/nautilus_backtest/test_runtime_failure_diagnostic.py`
- Modify: `tests/nautilus_backtest/test_runtime_parity_verifier.py`

**Interfaces:**
- Produces: `capture_prepared_engine_process(built, *, popen_factory=subprocess.Popen) -> CapturedEngineProcess` with bytes stdout/stderr and integer return code.

- [ ] **Step 1: Write RED custody tests.**

Cover successful capture, nonzero return, timeout kill/reap, Popen failure,
descriptor closure before wait, non-bytes output rejection, and one-call
cardinality. Add direct parity regressions for timeout and nonzero exit.

- [ ] **Step 2: Implement one bounded primitive.**

The primitive owns Popen, closes `close_after_spawn_fds` in `finally`, applies
the attested timeout, kills and reaps once on timeout, and returns immutable
captured bytes. It performs no schema/oracle policy. Diagnostic records the
nonzero result; parity requires return code zero, empty stderr, and one
canonical event line.

- [ ] **Step 3: Run, commit, and review.**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_runtime_process.py \
  tests/nautilus_backtest/test_runtime_failure_diagnostic.py \
  tests/nautilus_backtest/test_runtime_parity_verifier.py
make audit
make check-contracts
git diff --check
git add packages/nautilus_backtest/runtime_process.py \
  packages/nautilus_backtest/__init__.py \
  scripts/diagnose_nautilus_v12_runtime_failure.py \
  scripts/verify_nautilus_v12_r3_parity.py \
  tests/nautilus_backtest/test_runtime_process.py \
  tests/nautilus_backtest/test_runtime_failure_diagnostic.py \
  tests/nautilus_backtest/test_runtime_parity_verifier.py
git commit -m "refactor: centralize isolated engine process custody"
```

The reviewer must confirm the shared primitive does not import Nautilus and
does not weaken diagnostic containment or parity rejection.

---

### Task 4: Add Production-Equivalent Sealed Import Qualification

**Files:**
- Create: `scripts/qualify_nautilus_sealed_imports.py`
- Create: `engines/nautilus/launcher/import_probe.py`
- Create: `tests/nautilus_backtest/test_sealed_import_qualification.py`
- Create: `tests/nautilus_backtest/test_import_probe.py`
- Modify: `Makefile`

**Interfaces:**
- Produces: `make qualify-nautilus-sealed-imports`, an offline read-only gate over the selected CPython, wheel graph, launcher, native guard, and Bubblewrap identities; it publishes no closure.
- CLI: `--policy`, `--base-runtime`, `--artifact-directory`, `--sandbox`, and absent external `--receipt`; all paths must be absolute.

- [ ] **Step 1: Write RED CLI tests.**

Require absolute sealed paths, exact artifact manifest/wheel hashes, private
roots, empty environment, exact `-I -S`, no network mode, bounded timeout,
empty stderr, and a canonical digest-only import receipt. Reject relative,
symlinked, writable, stale, and ambient dependency inputs.

- [ ] **Step 2: Implement the import-only gate.**

Execute the reviewed launcher import bootstrap through Bubblewrap using this
exact in-sandbox command:

```text
/engine/bin/python3.12 -I -S /qualification/import_probe.py \
  --entry-launcher /qualification/entry-launcher.py \
  --wheel-directory /engine/wheels
```

The probe loads the policy-selected entry launcher from the fixed path and
calls its `_qualification_import_graph()` export. For the simulation launcher,
that export calls `_require_production_stdlib_sys_path`,
`_extract_sealed_wheels`, and `_sealed_dependency_path_scope`, imports exactly
`numpy`, `pandas`, and `nautilus_trader`, then calls
`_load_target_portfolio_strategy`. Task 5 adds the same export to the paper
launcher using the shared support path. The qualification script builds a
private canonical minimal manifest containing the exact policy-bound launcher
and strategy records and mounts it read-only at
`/engine/closure-manifest.json`; it mounts all launchers at their production
targets. The probe must not construct a backtest or paper engine. The
qualification script hash-binds the CPython executable, entry launcher, probe,
strategy, minimal manifest, complete launcher/wheel inventory, and Bubblewrap
binary into one canonical private mode-0400 receipt. It publishes no official
runtime root.

- [ ] **Step 3: Run, commit, and review.**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_sealed_import_qualification.py \
  tests/nautilus_backtest/test_import_probe.py
make audit
make check-contracts
git diff --check
git add scripts/qualify_nautilus_sealed_imports.py \
  engines/nautilus/launcher/import_probe.py \
  tests/nautilus_backtest/test_sealed_import_qualification.py \
  tests/nautilus_backtest/test_import_probe.py Makefile
git commit -m "test: qualify sealed Nautilus imports before publication"
```

Run the real gate only under a separately authorized external packet. A PASS
is required before any official generation is materialized.

---

### Task 5: Build the Finite Same-Strategy Paper Compatibility Boundary

**Files:**
- Create: `engines/nautilus/launcher/nautilus_paper_compat.py`
- Create: `packages/nautilus_backtest/paper_compat.py`
- Create: `scripts/verify_nautilus_paper_compatibility.py`
- Create: `tests/nautilus_backtest/test_paper_compat.py`
- Modify: `engines/nautilus/launcher/import_probe.py`
- Modify: `tests/nautilus_backtest/test_import_probe.py`
- Modify: `tests/nautilus_backtest/test_sealed_import_qualification.py`
- Modify: `engines/nautilus/native_entry_guard/src/main.rs`
- Modify: `scripts/materialize_nautilus_runtime_closure.py`
- Modify: `packages/engine_contracts/commands.py`
- Modify: `packages/engine_contracts/__init__.py`
- Modify: `services/job_worker/engine_artifacts.py`
- Modify: `services/job_worker/engine_spawn.py`
- Modify: `services/job_worker/nautilus_closure.py`
- Modify: `scripts/generate_contracts.py` and generated contracts through `make generate-contracts`
- Modify: `tests/foundation/test_nautilus_native_entry_guard.py`
- Modify: `tests/jobs/test_engine_artifacts.py`
- Modify: `tests/jobs/test_engine_spawn_provider.py`
- Modify: `tests/jobs/test_nautilus_closure.py`

**Interfaces:**
- Produces: `ValidatePaperCompatibility`, `PaperCompatibilityResultV1`, profile `paper-compatibility`, and a finite root harness that never enters normal worker/job authority.
- Consumes: the exact manifest-bound `TargetPortfolioStrategy`, catalog, engine configuration, strategy configuration, and reviewed parity campaign digest.
- Harness CLI: `--candidate-closure`, `--artifact-directory`, `--sandbox`, `--campaign-directory`, `--parity-record`, `--transport-root`, and absent `--record`.

- [ ] **Step 1: Add RED command and authority tests.**

The strict command has no host, port, provider, broker, credential, database,
client, output-path, or persistent-runtime field. It carries exactly three
read-only artifact references plus `strategy_source_sha256` and
`scenario_campaign_sha256`. Production worker authority and Job API parsing
must reject it; only the explicit research harness may call
`EngineSpawnProvider` with it.

- [ ] **Step 2: Make the native guard profile-specific at build time.**

Add required build-time `NAUTILUS_GUARD_PROFILE` and bind it into both admitted
and executed argv. Build separate deterministic binaries for
`execution-simulation` and `paper-compatibility`; a binary built for one must
reject the other's launcher/profile. Keep exact `-I -S`, fixed paths, and empty
environment. Generalize materializer/attestor/spawn profile tables without
adding a runtime-selected or wildcard profile.

- [ ] **Step 3: Implement the finite paper launcher and root validator.**

The launcher uses the same manifest-bound strategy source and sealed dependency
path helper as backtest, constructs a client-free Nautilus strategy/config
boundary, proves initialization and disposal, emits exactly one canonical
`PaperCompatibilityValidated` line, and exits. It never starts a persistent
`TradingNode`, provider, broker, account, or background service. The root
harness owns one prepare/consume/captured-process call and writes only a sealed
digest-only result record. Its `_qualification_import_graph()` export must use
the same import-only probe contract as simulation and must not construct paper
engine state.

- [ ] **Step 4: Generate contracts, gate, commit, and review.**

```bash
make generate-contracts
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/foundation/test_nautilus_native_entry_guard.py \
  tests/jobs/test_engine_artifacts.py \
  tests/jobs/test_engine_spawn_provider.py \
  tests/jobs/test_nautilus_closure.py \
  tests/nautilus_backtest/test_paper_compat.py
make audit
make check-contracts
make check-broad-handler-inventory
git diff --check
```

Commit only the listed source, generated contract, and test files. The review
must prove same strategy bytes/config semantics, finite execution, exact
profile-specific native guards, and continued production-worker rejection.
Checked-in policy identity tests may remain RED only for the explicitly stale
source/launcher/native-guard leaves that Task 7 replaces.

---

### Task 6: Build Campaign Inputs, Legacy Comparison, and 04D Campaign Closure

**Files:**
- Create: `scripts/materialize_phase4_campaign_inputs.py`
- Create: `scripts/close_phase4_research_evidence.py`
- Create: `legacy/research-backend/nautilus_parity_adapter.py`
- Create: `legacy/research-backend/tests/test_nautilus_parity_adapter.py`
- Create: `packages/research_validation/producers.py`
- Create: `tests/research_validation/test_producers.py`
- Modify: `scripts/verify_nautilus_v12_r3_parity.py`
- Modify: `scripts/diagnose_nautilus_v12_runtime_failure.py`
- Modify: `tests/nautilus_backtest/test_runtime_parity_verifier.py`
- Modify: `tests/nautilus_backtest/test_runtime_failure_diagnostic.py`
- Modify: `packages/research_validation/models.py`
- Modify: `packages/research_validation/evaluator.py`
- Modify: `packages/research_validation/closure.py`
- Modify: `packages/research_validation/__init__.py`
- Modify: `tests/research_validation/test_models.py`
- Modify: `tests/research_validation/test_evaluator.py`
- Modify: `tests/research_validation/test_closure.py`

**Interfaces:**
- Produces: one sealed eight-scenario campaign input manifest, `VerifiedScenarioComparisonV1`, `ResearchCampaignEvidenceV2`, and `Ws04CampaignClosureV2`.
- Preserves: `ResearchGateEvidenceV1`, `Ws04ClosureV1`, and legacy's comparison-only status.
- Campaign CLI: `--destination` only; it writes all eight fixed fixtures and rejects an existing destination.
- Parity CLI adds required `--campaign-directory` to its existing rollback/candidate/artifact/sandbox/transport/record arguments.
- Diagnostic CLI adds required `--campaign-directory` and consumes only its exact `long-accounting` member.
- Legacy CLI: `--campaign-directory`, one exact `--scenario-id`, and absent `--record`.
- Research CLI: `--campaign-directory`, `--parity-record`, `--paper-record`, `--legacy-record-directory`, and absent sealed `--evidence-root`.

- [ ] **Step 1: Materialize one canonical campaign authority.**

Write the exact eight sorted scenarios and their five artifacts to a private
external root with mode-0400 files and one canonical manifest. Refactor the
parity verifier to consume this root instead of privately regenerating then
deleting a different copy. Expand its digest-only record to bind source,
strategy, catalog, configuration, scenario, independent-reference result/event,
and Nautilus result/event digests.

- [ ] **Step 2: Add the isolated legacy adapter.**

The adapter runs only in `legacy/research-backend` with that component's frozen
dependency graph. It consumes one manifest-selected scenario and SHA sidecar,
uses the preserved local legacy backtest, emits one canonical comparison line,
and exits. It cannot import root packages, call collectors/brokers, write
portfolio state, or become promotion authority. Every mismatch requires one
bounded reviewed classification.

- [ ] **Step 3: Add campaign-v2 evidence without changing v1 meaning.**

`ResearchCampaignEvidenceV2` requires exactly eight unique sorted comparisons,
the paper result, verified point-in-time observations, stable recursive replay,
two non-overlapping walk-forward folds, all four cost scenarios, provenance,
and legacy dispositions. `close_ws04_research_campaign` returns eight scenario
closures plus one campaign digest only when reference and Nautilus match
field-for-field, paper binds the same strategy/config/campaign, all six 04D
gates pass, and legacy is never selected.

- [ ] **Step 4: Gate both dependency graphs, commit, and review.**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_runtime_parity_verifier.py \
  tests/research_validation
cd legacy/research-backend
UV_OFFLINE=1 uv sync --frozen --extra test
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --frozen --extra test pytest -p no:cacheprovider -q \
  tests/test_nautilus_parity_adapter.py
cd /home/thenam176/projects/trading-agent
make audit
make check-contracts
git diff --check
```

The independent review must verify that every research datum is derived from a
sealed artifact or deterministic calculation, not a caller-supplied PASS flag
or digest, and that v1 APIs retain their original semantics.

---

### Task 7: Rebind Both Schema-6 Policies After the Final Source Commit

**Files:**
- Modify: `engines/nautilus/runtime-closure-policy.json`
- Create: `engines/nautilus/paper-compatibility-runtime-closure-policy.json`
- Modify: policy-binding tests in `tests/foundation/test_nautilus_runtime_closure.py`

**Interfaces:**
- Produces: simulation and paper policies bound to the same reviewed final source commit and exact profile-specific native-guard binaries.

- [ ] **Step 1: Reproducibly build both native guards in disposable roots.**

Use only private Rust 1.95.0, verified LLVM, locked/offline Cargo inputs, a
private temporary Cargo home, and separate target directories. Verify a second
build of each profile is byte-identical; bind each binary digest and size to
its policy. Do not write global Rust state or an external runtime closure.

- [ ] **Step 2: Rebind only reviewed authority leaves.**

The simulation policy uses schema 6, profile `execution-simulation`, import
policy `native-guarded-stdlib-first-sealed-wheel-path-v1`, and final Task 6
source/launcher/native-guard identities. The paper policy uses schema 6,
profile `paper-compatibility`, semantic profile
`nautilus-paper-compatibility-v1`, its exact launcher inventory and paper guard,
and the same source/upstream/artifact/toolchain authorities.

- [ ] **Step 3: Gate, policy-only commit, and independent review.**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q \
  tests/foundation/test_nautilus_runtime_closure.py \
  tests/foundation/test_nautilus_native_entry_guard.py \
  tests/jobs/test_nautilus_closure.py \
  tests/jobs/test_engine_spawn_provider.py
make audit
make check-contracts
git diff --check
```

The reviewer compares normalized policy trees and must account for every
changed leaf. No materialization or Bubblewrap execution belongs to this task.

---

### Task 8: Qualify, Publish v12-r9/v13, Run Parity, and Close 04D

**Files:**
- Create externally after explicit authorization: private campaign, import,
  diagnostic, parity, paper, legacy, and research roots.
- Materialize no-clobber:
  `/home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v12-r9-simulation`
  and `/home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v13-paper-compatibility`.

- [ ] **Step 1: Preflight once and prove both destinations absent.**

Pin the exact Task 7 commit; require a clean tracked tree; capture v12 through
v12-r8 identities; validate v3 with `artifacts-v1`; validate both selected
policies/artifacts/toolchains/sandbox; then run exactly:

```bash
python3.11 -I scripts/build_nautilus_engine.py \
  --policy engines/nautilus/engine-build-policy.json \
  --python /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3/files/usr/bin/python3.12 \
  --artifacts /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --verify \
  --verify-input-bindings \
  --offline \
  --input-cache /home/thenam176/.cache/trading-agent/nautilus/input-cache \
  --wheel-cache /home/thenam176/.cache/trading-agent/nautilus/wheel-cache \
  --wheel-cache-manifest-sha256 0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b \
  --cargo /home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0/bin/cargo \
  --llvm-toolchain /home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain \
  --sandbox /usr/bin/bwrap
```

- [ ] **Step 2: Run both import qualifications before publication.**

Create one external private packet root and run the qualification CLI exactly
once for each policy:

```bash
phase4_runtime_root="$(mktemp -d -p /tmp phase4-v12-r9-v13-XXXXXX)"
chmod 0700 "${phase4_runtime_root}"
python3.11 -I scripts/qualify_nautilus_sealed_imports.py \
  --policy /home/thenam176/projects/trading-agent/engines/nautilus/runtime-closure-policy.json \
  --base-runtime /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3 \
  --artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --sandbox /usr/bin/bwrap \
  --receipt "${phase4_runtime_root}/simulation-import-receipt.json"
python3.11 -I scripts/qualify_nautilus_sealed_imports.py \
  --policy /home/thenam176/projects/trading-agent/engines/nautilus/paper-compatibility-runtime-closure-policy.json \
  --base-runtime /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3 \
  --artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --sandbox /usr/bin/bwrap \
  --receipt "${phase4_runtime_root}/paper-import-receipt.json"
```

Independent review must verify both receipts and the common stdlib-first path
contract. Stop before materialization on any failure.

- [ ] **Step 3: Materialize and diagnose v12-r9 once.**

```bash
python3.11 -I scripts/materialize_nautilus_runtime_closure.py \
  --policy /home/thenam176/projects/trading-agent/engines/nautilus/runtime-closure-policy.json \
  --base-runtime /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3 \
  --artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --destination /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v12-r9-simulation \
  --sandbox /usr/bin/bwrap \
  --cargo /home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0/bin/cargo \
  --llvm-toolchain /home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain
```

Independently attest schema/profile/import policy and all identities. Run
the following campaign/diagnostic commands exactly once:

```bash
python3.11 -I scripts/materialize_phase4_campaign_inputs.py \
  --destination "${phase4_runtime_root}/campaign"
mkdir -m 0700 "${phase4_runtime_root}/diagnostic-transport"
python3.11 -I scripts/diagnose_nautilus_v12_runtime_failure.py \
  --rollback-closure /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3 \
  --rollback-artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts-v1 \
  --candidate-closure /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v12-r9-simulation \
  --artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --sandbox /usr/bin/bwrap \
  --campaign-directory "${phase4_runtime_root}/campaign" \
  --transport-root "${phase4_runtime_root}/diagnostic-transport" \
  --diagnostic-record "${phase4_runtime_root}/diagnostic-record.json"
```

This is exactly one `long-accounting` through the normal diagnostic harness. Require
exit zero, empty stderr, one canonical event, one prepare/consume/Popen, empty
transport, and unchanged forensic identities. Stop and preserve v12-r9 on any
failure; no retry or matrix is allowed in that packet.

- [ ] **Step 4: Run the exact simulation campaign.**

In a new packet, invoke:

```bash
mkdir -m 0700 "${phase4_runtime_root}/parity-transport"
python3.11 -I scripts/verify_nautilus_v12_r3_parity.py \
  --rollback-closure /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3 \
  --candidate-closure /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v12-r9-simulation \
  --rollback-artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts-v1 \
  --artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --sandbox /usr/bin/bwrap \
  --campaign-directory "${phase4_runtime_root}/campaign" \
  --transport-root "${phase4_runtime_root}/parity-transport" \
  --record "${phase4_runtime_root}/parity-record.json"
```

Require 8 ordered scenarios × 2 byte-identical normal engine runs, 16/0/0,
independent root-oracle equality, no stderr/skip, and stable attestation.

- [ ] **Step 5: Materialize and run v13 paper compatibility once.**

```bash
python3.11 -I scripts/materialize_nautilus_runtime_closure.py \
  --policy /home/thenam176/projects/trading-agent/engines/nautilus/paper-compatibility-runtime-closure-policy.json \
  --base-runtime /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3 \
  --artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --destination /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v13-paper-compatibility \
  --sandbox /usr/bin/bwrap \
  --cargo /home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0/bin/cargo \
  --llvm-toolchain /home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain
```

Independently attest it, then run:

```bash
mkdir -m 0700 "${phase4_runtime_root}/paper-transport"
python3.11 -I scripts/verify_nautilus_paper_compatibility.py \
  --candidate-closure /home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v13-paper-compatibility \
  --artifact-directory /home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c \
  --sandbox /usr/bin/bwrap \
  --campaign-directory "${phase4_runtime_root}/campaign" \
  --parity-record "${phase4_runtime_root}/parity-record.json" \
  --transport-root "${phase4_runtime_root}/paper-transport" \
  --record "${phase4_runtime_root}/paper-record.json"
```

Require one finite successful process, `compatible=true`,
same strategy/config/catalog/campaign digests, no clients/network/persistence,
and unchanged simulation/forensic generations.

- [ ] **Step 6: Produce legacy comparisons and close campaign-v2 evidence.**

Run the legacy adapter exactly once for each manifest scenario ID, then close
evidence:

```bash
mkdir -m 0700 "${phase4_runtime_root}/legacy-records"
phase4_scenario_ids=(long-accounting short-accounting partial-fill same-bar-stop-take-profit stale-quote zero-liquidity session-boundary event-digest)
for phase4_scenario_id in "${phase4_scenario_ids[@]}"; do
  legacy/research-backend/.venv/bin/python -I legacy/research-backend/nautilus_parity_adapter.py \
    --campaign-directory "${phase4_runtime_root}/campaign" \
    --scenario-id "${phase4_scenario_id}" \
    --record "${phase4_runtime_root}/legacy-records/${phase4_scenario_id}.json"
done
python3.11 -I scripts/close_phase4_research_evidence.py \
  --campaign-directory "${phase4_runtime_root}/campaign" \
  --parity-record "${phase4_runtime_root}/parity-record.json" \
  --paper-record "${phase4_runtime_root}/paper-record.json" \
  --legacy-record-directory "${phase4_runtime_root}/legacy-records" \
  --evidence-root "${phase4_runtime_root}/research-evidence"
```

Require eight scenario closures, six 04D gates PASS, one deterministic campaign
closure, and legacy authority false. Run the exact 01D command from Step 1
again.

- [ ] **Step 7: Independent runtime/evidence-source review.**

The reviewer checks invocation cardinalities, oracle independence, byte
parity, paper finiteness, all 04D derivations, before/after identities, pre/post
01D, and the absence of raw output. Any failure preserves both new closures as
rejected and blocks repository evidence.

---

### Task 9: Final Clean Gate, Sanitized Evidence, Review, and Merge

**Files:**
- Create: `docs/nautilus-adoption/phase-4-v12-r9-v13-verification.md`
- Create: `docs/nautilus-adoption/program-tracker.md`
- Modify: `docs/nautilus-adoption/phase-4-simulation-closure.md`

- [ ] **Step 1: Run all final gates in a clean local clone.**

Do not delete or ignore the operator-owned `graphify-out` blocker in the shared
worktree. After all source/runtime reviews, create a clean clone outside the
repository at the exact candidate commit and install only from frozen offline
caches:

```bash
phase4_gate_root="$(mktemp -d -p /tmp phase4-final-gate-XXXXXX)"
chmod 0700 "${phase4_gate_root}"
git clone --no-local /home/thenam176/projects/trading-agent "${phase4_gate_root}/repo"
git -C "${phase4_gate_root}/repo" checkout --detach "$(git rev-parse HEAD)"
cd "${phase4_gate_root}/repo"
UV_OFFLINE=1 uv sync --frozen
cd apps/dashboard
npm ci --offline
cd "${phase4_gate_root}/repo/legacy/research-backend"
UV_OFFLINE=1 uv sync --frozen --extra test
cd "${phase4_gate_root}/repo"
make audit-release
make check-contracts
make check-broad-handler-inventory
make test-all
make ci
git diff --check
```

Keep the clone until independent review records its exact commit and counts;
then remove only that validated task-owned temporary root.

- [ ] **Step 2: Write sanitized evidence and tracker closure.**

Record both source/policy commits; schema/import policy; v3, v12-r9, and v13
manifest/closure digests; artifact/native guard/Rust/LLVM/Bubblewrap identities;
two qualification receipts; 01D pre/post; eight scenario rows with two event
hashes and reference/Nautilus digests; 16/0/0; paper result; legacy dispositions;
six 04D gate and campaign digests; v12–v12-r8 before/after identities; and all
clean-clone commands/counts. Include no raw event, fixture, diagnostic,
environment, or transient path.

- [ ] **Step 3: Evidence review and evidence-only commit.**

```bash
git add docs/nautilus-adoption/phase-4-v12-r9-v13-verification.md \
  docs/nautilus-adoption/program-tracker.md \
  docs/nautilus-adoption/phase-4-simulation-closure.md
git commit -m "docs: close Phase 4 Nautilus backtest gates"
```

The evidence reviewer verifies every claim against reviewed external receipts
and proves no production source changed after Task 8 runtime review.

- [ ] **Step 4: Whole-branch review and local fast-forward merge.**

Review from `git merge-base main HEAD` through `HEAD`, triage every deferred
ledger finding, and require a clean tracked/staged tree while preserving all
untracked operator files. Only after PASS:

```bash
git switch main
git merge --ff-only codex/ws01-ws04-remediation
```

Do not push, deploy, start services, select a runtime, or authorize paper/live
trading.
