# Phase 4 deep analysis — 2026-08-08

## Outcome

Phase 4 remains blocked at real isolated-engine acceptance. The fixture,
strategy, result, closure, native-guard, artifact, diagnostic, and parity
source boundaries exist, but no candidate has completed one normal
`EngineSpawnProvider` execution. The roadmap-required paper-compatibility
launcher/result and sealed 04D evidence producer do not exist. Consequently
the 8×2 matrix, same-strategy paper proof, campaign research closure,
post-01D gate, sanitized PASS evidence, final review, and merge have not
occurred.

The repeated v12 through v12-r8 failures are not independent edge cases. They
show that the Python-level interpreter-state sanitizer is the wrong security
boundary.

## Evidence reviewed

- `engines/nautilus/launcher/nautilus_backtest.py`
- `engines/nautilus/native_entry_guard/src/main.rs`
- `engines/nautilus/runtime-closure-policy.json`
- `services/job_worker/nautilus_closure.py`
- `services/job_worker/engine_spawn.py`
- `scripts/materialize_nautilus_runtime_closure.py`
- `scripts/diagnose_nautilus_v12_runtime_failure.py`
- `scripts/verify_nautilus_v12_r3_parity.py`
- launcher, closure, spawn, diagnostic, parity, oracle, and research-gate tests
- sanitized Task 13, 16, and 19 runtime reviews
- `codex_plan.zip` WS-04 objective, required tests, and exit gate
- the prior six-task remediation packet's deferred paper/04D specification

No private diagnostic bytes, engine process, cache mutation, closure
materialization, Bubblewrap launch, database, provider, broker, paper, or live
path was used in this analysis.

## Findings

### P0 — Interpreter authority is duplicated at the wrong layer

The native guard already admits exactly one argv, executes CPython with
`-I -S`, supplies an empty environment, and is selected only after closure and
sandbox attestation. Bubblewrap mounts the interpreter, launcher, wheels, and
inputs from hash-bound read-only files. That is the enforceable process-entry
boundary.

The launcher then snapshots and rewrites `sys.modules`, `sys.meta_path`, and
package child attributes. This second authority is mutable Python state. Its
trusted set depends on which standard-library imports happened before the
snapshot. The v12-r6, v12-r7, and v12-r8 failures demonstrate three different
order-dependent parent/child states. Fixing one state changes the next state
instead of proving the model.

### P0 — Source tests do not reproduce the production import topology

The launcher-protocol tests load the launcher through `importlib` inside a
test-created interpreter script. Test setup imports standard-library parents
that direct native entry may not preload. The tests can prove local restoration
properties, but they cannot enumerate every lazy parent/child transition in
the Nautilus/pandas/NumPy import graph. Each accepted source fix therefore
validated one observed state while leaving the next production state unseen.

### P1 — Standard CPython wheel resolution already has a working reference

The immutable v3 launcher extracts the hash-bound wheels into a private root,
adds those exact roots to `sys.path`, and lets CPython resolve standard library
and wheel modules normally. That is the working compatibility reference, not
the final security contract: v3 inserts wheel roots before stdlib, which could
let a wheel shadow a standard-library top-level name. The replacement must
retain the native guard, empty environment, `-I -S`, stdlib-only initial path,
sealed mounts, and private extraction root, then append sealed wheel roots
after the verified stdlib roots. This keeps CPython package semantics without
giving a wheel precedence over stdlib and without rewriting module state.

### P1 — Import-policy semantics are not explicit closure authority

Schema 5 binds the launcher bytes and native guard but does not state which
dependency import model the launcher implements. A candidate using the broken
module-state sanitizer and one using native-guarded sealed paths are
semantically different despite sharing the same profile name. The replacement
must be a schema-6 field included in attestation and spawn validation so an old
candidate cannot be selected by digest substitution or schema downgrade.

### P1 — Runtime process handling is duplicated

The diagnostic and parity scripts separately implement prepare/consume/Popen,
descriptor cleanup, timeout, and output handling. The parity source handles
timeout and nonzero exit, but its focused tests do not directly execute those
two branches. This is the deferred Task 3/Task 6 coverage debt in the current
ledger and a future drift risk.

### P1 — Official generations are being used as import tests

Every launcher hypothesis requires a policy rebind and a new immutable
generation before the real import graph is exercised. This consumed multiple
forensic generations without improving architectural confidence. The next
packet needs a production-equivalent import qualification using the reviewed
source and sealed artifact graph before publishing the final official
candidate.

### P1 — Paper compatibility and campaign-level 04D evidence are absent

`codex_plan.zip` requires the same strategy implementation and configuration
to be paper-compatible before WS-04 can close. The repository has no
`nautilus_paper_compat.py`, root paper result validator, legacy comparison
adapter, or research evidence producer. The existing `close_ws04_research`
closes a single zero-order `RunBacktest`; it cannot honestly represent the
eight non-zero simulation scenarios plus the paper proof. Preserve that v1
API and add an explicit campaign-v2 evidence/closure contract instead of
silently broadening the meaning of the old digest.

The existing remediation authority reserves `runtime-closure-v13` for the
paper-compatibility profile. The final simulation successor must therefore be
`runtime-closure-v12-r9-simulation`; reusing v13 for simulation would collide
with a previously reviewed authority boundary.

### P1 — The final repository gate is environment-contaminated

`make test-all` in the shared worktree stops because the pre-existing untracked
`graphify-out/.graphify_ast.json` exceeds the repository scanner's size limit.
The artifact belongs to the operator and must not be deleted or excluded to
manufacture a PASS. Final gates must run in a clean detached checkout of the
exact candidate commit; the shared worktree is then used only for the reviewed
fast-forward merge.

### P2 — Evidence names and authority statements are stale

The current plan and simulation-closure document still refer to v12/v12-r3 as
the pending final generation. Rejected v12 through v12-r8 identities are not
represented in one canonical record. Final evidence must use the actual final
generation, list every rejected forensic generation before/after, and bind the
source commit, policy commit, schema, manifest, closure, artifact, toolchain,
01D, 8×2, oracle, and repository-gate results.

## Architecture decision

The replacement design assigns authority as follows:

1. Native guard owns exact process entry, argv, `-I -S`, and empty environment.
2. Closure attestation and EngineSpawnProvider own executable, mount, schema,
   sandbox, artifact, and input identity.
3. The launcher verifies that its initial `sys.path` contains only attested
   standard-library roots, extracts hash-bound wheels into a private empty
   root, and temporarily appends only those extracted roots after stdlib.
4. CPython owns standard-library package semantics. The launcher does not
   delete, replace, snapshot, or restore `sys.modules`, `sys.meta_path`, or
   standard-library parent attributes.
5. Closure schema 6 binds `dependency_import_policy` to
   `native-guarded-stdlib-first-sealed-wheel-path-v1`.

This removes the state machine that caused the failures while retaining the
enforceable external controls.

## Completion condition

Phase 4 is complete only after the architecture-reset source and schema are
independently approved; v12-r9 passes one normal diagnostic and the exact 8×2
matrix with independent oracle and byte parity; v13 proves the same sealed
strategy in a finite client-free paper boundary; the legacy comparison remains
non-authoritative; all six 04D gates close through campaign-v2 evidence; 01D
passes before and after; the clean-checkout repository gate passes; sanitized
evidence and the whole branch are independently reviewed; and the branch is
fast-forward merged locally without modifying operator-owned untracked files.
