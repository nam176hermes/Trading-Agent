# Phase 4 Final Parity and Evidence Closure Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan task-by-task. Use `superpowers:test-driven-development` for every source change, `superpowers:systematic-debugging` for every runtime failure, `superpowers:requesting-code-review` at each review boundary, and `superpowers:verification-before-completion` before any PASS claim.

**Goal:** Determine and repair the remaining r9 parity mismatch without guessing, complete simulation and paper runtime qualification, close research evidence, pass the final repository gates, and fast-forward the reviewed Phase 4 branch locally.

**Architecture:** Preserve all existing runtime/helper generations as immutable forensic artifacts. Add a source-only, field-level digest observation boundary around the already proven post-launch event-validation failure; run exactly one bounded observation; classify the failure into a closed root-cause category; then execute only the corresponding minimal repair branch. Simulation parity remains the hard gate before paper, legacy/research evidence, final evidence publication, or merge.

**Tech Stack:** Python 3.11, Pydantic 2.13.4, pytest, uv frozen/offline environments, EngineSpawnProvider, Bubblewrap, schema-6 Nautilus runtime closures, sealed uv v6 descriptor-to-`execveat`, Rust 1.95.0 offline builds, canonical JSON/SHA-256 receipts, Git.

---

## Audited starting point

- Worktree: `/home/thenam176/.codex/worktrees/trading-agent/phase4-architectural-closure`
- Branch: `codex/phase4-architectural-closure`
- Audited HEAD: `615f630a6c6dc12d26e31fc033aeb1632a39a65d`
- Task 6 whole review v6: PASS.
- Task 7 authority freeze/no-drift review: PASS.
- Task 8 A4 import qualifications: PASS and reviewed.
- `runtime-closure-v12-r9-simulation`: present, attested, and currently rejected forensic evidence.
- r9 one-shot `long-accounting` diagnostic: PASS.
- Exact matrix blocker: first `long-accounting` run exits in `_validated_event()` because `validate_isolated_simulation_result()` rejects the actual canonical event before run 2.
- Existing whole-event SHA evidence does not identify the rejected field. No launcher, oracle, validator, campaign, or runtime functional change is authorized until Task 2 classifies it.
- `runtime-closure-v13-paper-compatibility`: absent.
- Existing untracked plan `docs/superpowers/plans/2026-08-08-phase4-task6-follow-on-custody.md` is preserved and is not edited by this plan.

## Global execution rules

1. Use the feature worktree above; do not work from the older integration checkout.
2. Keep live approvals false. Do not contact an exchange, broker, account, order endpoint, provider, database, or active mutation route.
3. Run dependency operations only with `UV_OFFLINE=1` and `--frozen`; run dashboard bootstrap with cached `npm ci --offline` when supported by the installed npm, otherwise use the repository's already-reviewed offline command.
4. Every external packet root must be newly created mode 0700. Every diagnostic/receipt file must be single-link mode 0400. Do not copy raw stdout, stderr, event JSON, paths, market rows, orders, or positions into Git evidence.
5. Never overwrite or delete a retained runtime/helper generation. Publication is no-clobber to a new name only.
6. Stop on the first unclassified failure. Do not retry a runtime command unless a new independent review and explicit operator authority cover the retry.
7. A review receipt is written by an agent that did not implement the change being reviewed.
8. Do not begin paper, legacy, evidence closure, or merge until the simulation matrix is independently reviewed PASS.

## Task 1: Add field-level event-validation observations

**Purpose:** Turn the current whole-event mismatch into a closed, digest-only field classification without changing event validation, the launcher, the oracle, the campaign, or runtime policy authority.

**Files:**

- Modify: `scripts/verify_nautilus_v12_r3_parity.py`
- Modify: `tests/nautilus_backtest/test_runtime_parity_verifier.py`
- Modify mechanically if required: `docs/implementation/foundation-exception-inventory.md`
- Create ignored report: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-1-report.md`
- Create ignored independent review: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-1-review.md`

**Interfaces and receipt contract:**

- Replace the event-failure receipt payload with a versioned `phase4-parity-event-validation-failure-v2` schema.
- Add a private immutable field-commitment model containing exactly:
  - `field_name`
  - `canonical_type`
  - `actual_sha256`
  - `reference_sha256`
- Use a fixed reviewed inventory of validator-relevant fields:
  - command type;
  - payload digest and bindings;
  - scenario and input-artifact digests;
  - iterations and integer counters;
  - filled, remaining, and position quantities;
  - average entry price;
  - fees, realized PnL, and unrealized PnL;
  - precedence.
- Canonicalize each scalar with an explicit type tag before hashing so JSON integer `1`, decimal string `"1"`, boolean `true`, and missing values cannot collide.
- The external receipt contains only the fixed schema, scenario ID, failure class, actual/reference whole-event SHA-256, sorted mismatching field names, and the commitment tuple for mismatching fields.
- No raw values, event bytes, output bytes, filesystem paths, timestamps, arbitrary exception text, or traceback may enter the receipt.
- The Git report may state field names and the fixed failure class only; it must not copy field digests from the external receipt.
- Keep the existing descriptor-bound, no-clobber, mode-0400/single-link publisher and primary-error preservation.

**TDD steps:**

- [ ] Add a test that supplies an actual/reference pair differing in exactly one counter and asserts RED because v1 cannot identify the field.
- [ ] Add a test that differs in exactly one decimal value and proves the canonical type tag is bound.
- [ ] Add a test for missing versus explicit null/zero and prove distinct commitments.
- [ ] Add a multi-field test and require repository-order output, not caller order.
- [ ] Add adversarial tests proving raw values, event JSON, request paths, stderr, and exception text are absent.
- [ ] Add success, EngineSpawnError, preexisting receipt, partial-write, and parent/inode substitution regressions; those paths must retain their current behavior.
- [ ] Run RED:

```bash
cd /home/thenam176/.codex/worktrees/trading-agent/phase4-architectural-closure
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --frozen pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_runtime_parity_verifier.py \
  -k 'event_validation_failure and (field or commitment or canonical_type or no_raw)'
```

- [ ] Implement the minimal receipt-v2/commitment change. Do not change `_independent_reference_event`, `validate_isolated_simulation_result`, scenario fixtures, or launcher output.
- [ ] Run GREEN with the same focused command.
- [ ] Run affected gates:

```bash
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --frozen pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_runtime_parity_verifier.py \
  tests/nautilus_backtest/test_runtime_failure_diagnostic.py
make check-broad-handler-inventory
make check-secrets
git diff --check
```

- [ ] Commit source/tests with subject: `fix: classify parity event validation fields`.
- [ ] If the handler inventory moved, regenerate it canonically and commit the mechanical row update separately with subject: `docs: refresh parity observation inventory`.
- [ ] Request an independent review. Acceptance requires SPEC PASS / QUALITY PASS and zero Critical/Important findings before Task 2.

## Task 2: Run one bounded field-classification packet

**Purpose:** Exercise the reviewed receipt exactly once against the existing immutable r9 closure and identify the failing validator field set.

**Files:**

- Create ignored execution report: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-2-observation-report.md`
- Create ignored independent review: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-2-observation-review.md`
- Do not change tracked source in this task.

**Preflight:**

- [ ] Pin the reviewed Task 1 HEAD and require a clean tracked/index state; preserve the existing untracked historical plan.
- [ ] Snapshot identity, mode, size, and manifest SHA-256 for v3, v12 through v12-r9, and sealed uv v1 through v6.
- [ ] Assert r9 manifest SHA remains `b2c8a6c38d20f1baafb7e1a8fe43d3f9080d8786dee3ad33cc8fd103302aac07`.
- [ ] Assert v13 is absent.
- [ ] Run one `UV_OFFLINE=1 uv sync --frozen`, then prove the absolute root `.venv/bin/python -I -B` imports Pydantic 2.13.4.
- [ ] Re-attest retained rollback v3 as its actual schema 1 / `zero-order` authority and r9 read-only as schema 6 / `execution-simulation`; do not rematerialize either closure.
- [ ] Create a new private mode-0700 packet root and a new canonical campaign exactly once.

**Single runtime action:**

- [ ] Invoke `scripts/verify_nautilus_v12_r3_parity.py` exactly once with:
  - rollback runtime v3 and artifacts-v1;
  - candidate runtime r9 and the Task 7 simulation artifact directory;
  - schema 1 rollback and schema 6 candidate;
  - the newly materialized canonical campaign;
  - a fresh transport root and PASS-record path.
- [ ] Do not retry regardless of outcome.

**Closed outcomes:**

1. **PASS record exists:** verify exact 8 scenarios x 2 runs, byte parity, independent event equality, independent result-digest equality, and exact transport cardinality; then proceed to Task 5 after independent review.
2. **Valid event-validation receipt/provenance pair exists:** stop before run 2/paper/r13; require sibling canonical schemas `phase4-parity-event-validation-failure-v3` and `phase4-parity-event-provenance-v1`, then independently validate the pair's mode-0400/single-link descriptor custody, no-clobber identities, receipt-to-provenance SHA binding, and recompute every actual/reference field commitment from the private provenance. Publish only mismatch field names and fixed class in the sanitized review; proceed to Task 3.
3. **Valid result-digest `.failure.json` exists:** validate its canonical schema, descriptor custody, and exit category; stop for a separate result-observation repair. Do not enter Task 3 or modify functional code.

**Observation incomplete (not an outcome):**

- Missing, partial, malformed, preexisting, or custody-invalid PASS records, event receipt/provenance pairs, or result-digest receipts are observation-incomplete. Stop fail-closed and return to Task 1 with a new reviewed source repair; do not count this stop as a closed outcome.
- Any launch/provider failure also stops fail-closed and is classified separately; do not treat it as a parity mismatch or a closed outcome.

**Postflight:**

- [ ] Verify all preflight identities unchanged.
- [ ] Confirm r13 is absent and no Task 8 downstream command ran.
- [ ] Independent review must produce SPEC PASS / QUALITY PASS for packet cardinality/custody and exactly one valid closed outcome, or an observation-incomplete stop that is not counted as an outcome.

## Task 3: Root-cause adjudication gate

**Purpose:** Convert the field set into one and only one repair authority. This task is read-only and creates no source commit.

**Files:**

- Create ignored classification: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-3-root-cause-classification.md`
- Read: `scripts/verify_nautilus_v12_r3_parity.py`
- Read: `packages/nautilus_backtest/result.py`
- Read: `packages/nautilus_backtest/reference.py`
- Read: `packages/nautilus_backtest/fixtures.py`
- Read: `packages/nautilus_backtest/scenarios.py`
- Read: `engines/nautilus/launcher/nautilus_backtest.py`
- Read: `tests/nautilus_backtest/test_reference.py`
- Read: `tests/nautilus_backtest/test_launcher_protocol.py`

**Classification procedure:**

- [ ] Reconstruct the actual-event field commitment from the retained external receipt without copying the raw event into Git.
- [ ] Recompute the independent reference commitment from the exact sealed campaign and command envelope.
- [ ] For domain result fields, compare both sides to the literal scenario facts already fixed in `tests/nautilus_backtest/test_reference.py`; do not use the launcher calculator as the third oracle.
- [ ] Select exactly one category:

| Category | Evidence | Authorized repair surface |
|---|---|---|
| A. Envelope/binding | command, payload, scenario, artifact, or precedence commitment differs while literal domain values agree | verifier binding construction or launcher envelope binding only |
| B. Counter/accounting | iterations/orders/fills/positions/quantities/price/fees/PnL differs and literal scenario facts select one side | only the side contradicted by the literal oracle |
| C. Canonical type/scale | numeric values are mathematically equal but canonical type/scale commitment differs | canonical serializer/validator boundary only |
| D. Campaign authority | input/scenario digest differs because selected sealed campaign bytes/inventory differ | campaign materializer/loader grammar only |
| E. Observation incomplete | receipt commitments cannot be independently recomputed or map to more than one category | no functional change; write a new observation repair plan |

- [ ] Record the exact mismatching field names, category, authoritative source, and rejected hypotheses in the ignored classification file.
- [ ] Obtain an independent read-only review of the classification. Task 4 may start only for A-D with SPEC PASS / QUALITY PASS. Category E stops this plan before functional repair.

## Task 4: Execute the single authorized repair branch

Only one branch below is executed. Every branch begins with a behavior-specific RED and ends with independent review.

### Branch A: Envelope or verifier binding repair

**Files:**

- Modify only the identified producer in `scripts/verify_nautilus_v12_r3_parity.py` or `engines/nautilus/launcher/nautilus_backtest.py`
- Modify: `tests/nautilus_backtest/test_runtime_parity_verifier.py`
- Modify if launcher-owned: `tests/nautilus_backtest/test_launcher_protocol.py`
- Modify mechanically if needed: `docs/implementation/foundation-exception-inventory.md`

**Steps:**

- [ ] Add a regression using the exact classified field and literal expected commitment.
- [ ] Prove RED without changing validators or unrelated event fields.
- [ ] Correct only the contradicted producer.
- [ ] Run the parity verifier, launcher protocol, diagnostic, inventory, secrets, and diff gates.
- [ ] If the launcher changes, use a source commit followed by policy-only rebind of both simulation and paper runtime policies, rebuild both native guards twice offline, and independently review the policy pair. r9 becomes permanently rejected and Task 5 must use r10.
- [ ] If only the verifier changes, do not rebind runtime policies and Task 5 may reuse r9 after read-only re-attestation.

### Branch B: Counter/accounting repair

**Files:**

- If the independent oracle is wrong, modify only: `packages/nautilus_backtest/reference.py`, `tests/nautilus_backtest/test_reference.py`, and verifier tests.
- If the actual engine adapter is wrong, modify only: `engines/nautilus/launcher/nautilus_backtest.py`, `tests/nautilus_backtest/test_launcher_protocol.py`, and exact accounting tests.
- If validation expectations are wrong, modify only: `packages/nautilus_backtest/result.py` and `tests/nautilus_backtest/test_result.py`.

**Steps:**

- [ ] Add a literal long-accounting regression for the exact mismatching field.
- [ ] Prove the third literal oracle selects one side and the other side fails RED.
- [ ] Correct only the rejected side; preserve all seven other scenario literals.
- [ ] Run all reference/result/launcher/parity source suites.
- [ ] A launcher/result runtime-authority change requires a source commit, policy-only rebind for both profiles, two reproducible offline native builds per profile, and a new r10 closure. A pure verifier/reference repair does not.

### Branch C: Canonical type/scale repair

**Files:**

- Modify the single boundary identified by Task 3 in `packages/nautilus_backtest/result.py`, `packages/nautilus_backtest/reference.py`, or `engines/nautilus/launcher/nautilus_backtest.py`
- Modify the corresponding exact unit tests and parity verifier regressions.

**Steps:**

- [ ] Add RED cases covering `int` versus `bool`, Decimal exponent/scale, signed zero, missing/null, and non-finite rejection as relevant to the classified field.
- [ ] Implement one canonical representation shared by actual/reference/validator without loosening accepted schema types.
- [ ] Run the full source suites and follow the same policy/rebuild rule as Branch B when a runtime-authority file changes.

### Branch D: Campaign authority repair

**Files:**

- Modify only: `packages/nautilus_backtest/fixtures.py`, `packages/nautilus_backtest/scenarios.py`, `scripts/materialize_phase4_campaign_inputs.py`, and their tests.

**Steps:**

- [ ] Add RED for the exact five-artifact inventory/digest mismatch.
- [ ] Repair canonical inventory/order/digest generation without changing scenario economics.
- [ ] Prove no-clobber materialization, descriptor-bound reads, mode 0400 members, fixed eight-scenario order, and deterministic campaign SHA.
- [ ] Do not rebind runtime policies if no policy-bound runtime source changed; Task 5 may reuse r9 with the new reviewed campaign.

**Common completion:**

- [ ] Commit source with a category-specific subject.
- [ ] Commit any policy-only rebind separately and prove it is a direct child of the reviewed source commit.
- [ ] Write `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-4-report.md`.
- [ ] Obtain an independent SPEC PASS / QUALITY PASS review with zero Critical/Important findings.

## Task 5: Close simulation qualification

**Purpose:** Produce the first independently reviewed PASS for the diagnostic and exact 8 x 2 simulation matrix.

**Generation selection:**

- Reuse r9 only when Task 4 changed no runtime-policy-bound source and the independent review explicitly permits reuse.
- If Task 4 changed launcher/result/runtime authority, materialize the new no-clobber destination `runtime-closure-v12-r10-simulation`; never overwrite r9.

**Files:**

- Create ignored execution report: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-5-simulation-report.md`
- Create ignored independent review: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-5-simulation-review.md`

**Steps:**

- [ ] Snapshot all retained closure/helper identities and assert the selected destination state.
- [ ] Run 01D preflight exactly once.
- [ ] Before any root-package controller, establish the current-worktree controller
  binding exactly once. The root project is wheel-installed, so ordinary frozen
  sync can retain stale project code and is insufficient:

```bash
phase4_source_root="$(git rev-parse --show-toplevel)"
(
  cd "${phase4_source_root}"
  UV_OFFLINE=1 uv sync --frozen --reinstall-package trading-agent-control-api
)
phase4_root_python="${phase4_source_root}/.venv/bin/python"
test -x "${phase4_root_python}"
"${phase4_root_python}" -I -B - "${phase4_source_root}" <<'PY'
import hashlib
import importlib
from pathlib import Path
import sys

worktree = Path(sys.argv[1]).resolve()
source = worktree / "services/job_worker/nautilus_closure.py"
module = importlib.import_module("services.job_worker.nautilus_closure")
installed = Path(module.__file__).resolve()
assert installed.is_relative_to(worktree / ".venv"), installed
assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(installed.read_bytes()).digest()
assert module._MANIFEST_FIELDS_V6
PY
"${phase4_root_python}" -I -B scripts/diagnose_nautilus_v12_runtime_failure.py --help >/dev/null
```

  This read-only proof rejects a canonical-checkout or other foreign installed
  module, requires byte-identical source and installed closure-attestor bytes,
  and requires the schema-6 attestor plus controller-help import. It does not
  materialize or execute a runtime.
- [ ] If r10 is required, run both reviewed import qualifications, independently review receipts, materialize r10 exactly once no-clobber, and independently attest schema 6/profile/source/policy/guard/launcher/mount inventory.
- [ ] After the r10 attestation PASS, create one fresh private mode-0700 packet campaign destination that is absent immediately before publication; materialize the canonical campaign exactly once, require the fixed repository-ordered eight-scenario manifest, mode-0500 scenario roots, mode-0400 members, descriptor/no-clobber custody, and retain the sanitized campaign digest only outside Git/evidence.
- [ ] Run exactly one `long-accounting` diagnostic through normal EngineSpawnProvider against that reviewed campaign; require exit 0, empty stderr, one prepare/consume/process, and request-only sealed transport.
- [ ] Run the parity verifier exactly once.
- [ ] Capture the verifier controller's direct exit before any status write,
  pipeline, grouping, logging command, or review command can replace `$?`.
  The packet wrapper is ad hoc rather than source-owned, so use this exact
  contract (with the reviewed absolute arguments substituted once):

```bash
set +e
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 "${phase4_root_python}" -I -B \
  scripts/verify_nautilus_v12_r3_parity.py [reviewed-absolute-arguments] \
  > "$packet/parity-controller.stdout" 2> "$packet/parity-controller.stderr"
parity_exit=$?
printf 'parity=%s\n' "$parity_exit" > "$packet/parity-status.txt"
```

  Do not wrap the verifier in a pipeline, command group, command substitution,
  `if` condition, or retry loop. `parity-status.txt` is authoritative only
  when it contains that immediately captured direct controller exit.
- [ ] Run the source-only exit-contract regression before the packet:

```bash
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --frozen pytest -p no:cacheprovider -q \
  tests/nautilus_backtest/test_runtime_parity_verifier.py \
  -k 'post_two_run_oracle_event_mismatch or parity_cli_returns_failed_verification_exit_code_directly'
```

  This plan regression proves the CLI maps a failed verifier invocation to
  exit 1; it does not authorize a runtime invocation.
- [ ] Require:
  - exact repository-ordered eight scenarios;
  - exactly two normal EngineSpawnProvider runs per scenario;
  - run1 bytes equal run2 bytes;
  - actual canonical event equals the independent reference event;
  - launcher result digest equals the independent root result digest;
  - exact transport inventory/cardinality and sealed forensic modes;
  - no failure receipt.
- [ ] Run 01D post-run exactly once.
- [ ] Independently review diagnostic, matrix, oracle independence, byte/result parity, cardinality, and retained-generation identities.
- [ ] Any failure stops before Task 6. Do not use a partial matrix as PASS evidence.

## Task 6: Materialize and qualify paper v13

**Purpose:** Close the paper compatibility boundary only after simulation parity is green.

**Files:**

- Create ignored execution report: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-6-paper-report.md`
- Create ignored independent review: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-6-paper-review.md`

**Steps:**

- [ ] Assert `runtime-closure-v13-paper-compatibility` is absent and all retained identities equal the Task 5 postflight snapshot.
- [ ] Use the reviewed paper import receipt or regenerate it once if a runtime-policy-bound source changed; independently review any new receipt.
- [ ] Materialize v13 exactly once, no-clobber.
- [ ] Independently attest schema 6, `paper-compatibility` profile, paper native guard, launcher/probe/strategy/wheel inventory, source commit, upstream commit, and policy digest.
- [ ] Run the finite paper compatibility harness exactly once through normal EngineSpawnProvider.
- [ ] Require exact eight-scenario inventory, finite init/dispose lifecycle, canonical input, digest-only self-bound result, and fixed protected result destination.
- [ ] Prove no network/client/provider/broker/order/persistence/live path can be reached.
- [ ] Independently review v13 publication and paper result. Any failure stops before Task 7.

## Task 7: Close legacy and 04D research evidence

**Purpose:** Produce the six-gate research closure from the reviewed simulation, paper, and legacy records.

**Files:**

- Create ignored execution report: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-7-research-report.md`
- Create ignored independent review: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-7-research-review.md`

**Steps:**

- [ ] Verify sealed uv v6 pair and policy with the reviewed absolute policy path.
- [ ] Use `--execute-pair` for both fixed actions so verify-to-exec remains descriptor/kernel-bound; do not execute uv by pathname.
- [ ] Run frozen/offline legacy sync in the exact five-variable sanitized environment.
- [ ] Execute the literal no-`-I` legacy adapter command for all eight ordered scenarios.
- [ ] Require each fixed result to be one-link mode 0400, schema-valid, bound to the sealed campaign member, and `legacy_selected=false`; mismatch classifications remain bounded and cannot grant promotion authority.
- [ ] Have an independent custody reviewer derive and hand back exactly four expected aggregate digests: parity, paper, legacy, and campaign.
- [ ] Invoke the public research closer with the four sealed paths plus the four reviewer-provided digests.
- [ ] Require exact ResearchCampaignEvidenceV2 closure and all six gates PASS:
  - point-in-time;
  - recursive replay;
  - non-overlapping walk-forward folds;
  - oracle-derived OOS threshold;
  - cost stress;
  - parity/paper/legacy custody.
- [ ] Run 01D post-evidence if Task 5 post-01D is no longer the final runtime mutation boundary.
- [ ] Independent review must recompute derived bindings and confirm the closer did not mint authority from caller-constructed evidence.

## Task 8: Final repository gates, sanitized evidence, and local merge

**Purpose:** Freeze Phase 4 as a reviewed local integration candidate without deployment or live mutation.

**Files:**

- Create: `docs/nautilus-adoption/phase-4-final-runtime-verification.md`
- Modify: `docs/nautilus-adoption/phase-4-simulation-closure.md`
- Modify the canonical Phase 4 program tracker identified by `rg -n 'Phase 4|04D|paper compatibility' docs/nautilus-adoption docs/superpowers`
- Create ignored gate report: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-8-final-gate-report.md`
- Create ignored evidence review: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-8-evidence-review.md`
- Create ignored whole-branch review: `.superpowers/sdd/2026-08-09-phase4-final-parity-and-evidence-closure/task-8-whole-branch-review.md`

**Evidence content:**

- Record the selected simulation generation (r9 or r10), v13 paper generation, manifest/policy/source/native-guard digests, diagnostic cardinality, exact 8 x 2 matrix result, independent oracle/byte/result parity, paper result, legacy/research six-gate result, 01D pre/post, and final source gates.
- Mark r9 rejected if r10 was required; preserve all rejected generations as forensic evidence.
- Include no raw stdout/stderr, private paths, event bodies, market rows, order/fill/account data, or external receipt digests that are not explicitly approved for evidence.

**Clean-clone gates:**

- [ ] Create a fresh private detached clone at the final candidate.
- [ ] Bootstrap root, legacy, and dashboard dependency graphs frozen/offline.
- [ ] Run:

```bash
make audit-release
make check-contracts
make check-broad-handler-inventory
make check-secrets
make test-all
make ci
git diff --check
git status --short
```

- [ ] If `make audit-release` rejects the intentionally retained untracked historical plan in the feature worktree, run it only in the clean clone; do not delete the user-owned plan to make the feature tree appear clean.
- [ ] Remove only task-owned clone/dependency/output roots after validating exact identity; prefer recoverable trash and verify absence.

**Reviews and commits:**

- [ ] Write sanitized evidence docs only after all runtime/research checks pass.
- [ ] Obtain independent evidence review before committing docs.
- [ ] Commit evidence with subject: `docs: record Phase 4 runtime closure evidence`.
- [ ] Obtain a fresh whole-branch review from merge base `914c541` through final HEAD. Acceptance is SPEC PASS / QUALITY PASS, no Critical/Important findings, no Task 5 policy drift outside reviewed repairs, and no live/runtime authority widening.

**Local fast-forward integration:**

- [ ] Snapshot both worktrees' status and preserve all unrelated/untracked user files.
- [ ] Fast-forward `codex/ws01-ws04-remediation` to the reviewed feature HEAD with `git merge --ff-only`.
- [ ] Fast-forward local `main` to `codex/ws01-ws04-remediation` with `git merge --ff-only` only if the branch topology is unchanged and the whole-branch review explicitly authorizes it.
- [ ] Run final `git log --oneline --decorate -n 12`, `git diff --check`, and status checks in both checkouts.
- [ ] Do not push, deploy, change services/schedulers/databases, enable live approvals, or start production cutover.

## Definition of done

Phase 4 is complete only when all statements below are true:

- The event mismatch has a reviewed field-level root cause and a minimal reviewed repair.
- The selected simulation generation passes one diagnostic and the exact 8 scenarios x 2 normal EngineSpawnProvider matrix with independent event, byte, and result-digest parity.
- v13 paper compatibility is materialized once and passes its finite no-live qualification.
- The legacy eight-scenario campaign and ResearchCampaignEvidenceV2 six gates pass.
- 01D pre/post and clean-clone repository gates pass.
- Sanitized evidence and the whole branch receive independent PASS reviews.
- Local integration completes by fast-forward only, with no push, deployment, database/service mutation, or live-trading authority change.
