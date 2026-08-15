# P0 Canonical Baseline and CI Closure Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before any completion claim.

**Goal:** Qualify one canonical source baseline for `nam176hermes/Trading-Agent`, make the default CI fully portable and reproducible, close all portable source defects, isolate genuine native/external requirements behind explicit receipts, and prepare a fast-forward-only promotion to `main`.

**Architecture:** Keep the current contract-first, paper-only foundation. Use the existing candidate branch as the implementation base. The default source gate must never require Bubblewrap, user namespaces, operator-local executables, retained research corpora, protected PostgreSQL, production services, brokers, exchanges, or live credentials. Genuine host requirements remain testable in dedicated lanes and may be reported as `UNAVAILABLE` in portable CI, but they may never be reported as executed or passed. A separate host-authority gate must require real `PASS`.

**Tech Stack:** Python 3.11, pytest, Pydantic, Make, GitHub Actions, `uv`, Node 20, npm, JSON/TSV evidence artifacts, Git worktrees.

---

## 1. Verified starting point

This plan is based on the repository state verified on **2026-08-13**.

### Canonical branch

```text
branch: main
head:   19627785c140c502260f864e462fed9b9925436e
```

### Qualification candidate

```text
branch: codex/phase1-terra-autopilot-19627785c140
head:   417c17452ea31f0ca8c8e9893ac3c03a3a90a7c1
relation to main:
  ahead:  106 commits
  behind: 0 commits
```

The candidate is therefore a fast-forward descendant of `main`. P0 must qualify this candidate lineage rather than restart implementation from `main`.

### Current default workflow

The candidate workflow:

```text
.github/workflows/foundation.yml
```

uses Python 3.11, Node 20, `uv sync --frozen`, `npm ci`, and:

```bash
make ci-portable NONINTERACTIVE=1
```

It always uploads:

```text
runtime/state/ci-portable/**
```

### Current blocking result

The latest inspected candidate run stops at governance validation with:

```text
POLICY_DATE_CONTEXT_MISMATCH
```

The same run produced a valid sealed foundation context bound to:

```text
foundation_head_sha:        417c17452ea31f0ca8c8e9893ac3c03a3a90a7c1
foundation_run_id:           31724355034
foundation_validation_date:  2026-08-13
```

P0 must reproduce and fix the context-authority mismatch before interpreting later suite results.

### Current hosted-failure inventory

The retained inventory contains **62** cases:

| Classification | Count | Required P0 treatment |
|---|---:|---|
| `PORTABLE_SOURCE_DEFECT` | **32** | Fix source/test fixtures; zero may remain unresolved |
| `NATIVE_CAPABILITY_REQUIRED` | **24** | Real capability lane or structured `UNAVAILABLE` receipt |
| `EXTERNAL_AUTHORITY_REQUIRED` | **6** | Real authority lane or structured `UNAVAILABLE` receipt |

Exact subgroups:

```text
PORTABLE_SOURCE_DEFECT
├── 27 SRC-SEALEDUV-BWRAP-PREFLIGHT
├── 3  SRC-SEMANTIC-FIXTURE-IDENTITY
└── 2  SRC-PHASE4B-FAKEROOT-IDENTITY

NATIVE_CAPABILITY_REQUIRED
├── 16 NATIVE-BWRAP-OS-SANDBOX
└── 8  NATIVE-USERNS-ROOT-PROVISION

EXTERNAL_AUTHORITY_REQUIRED
├── 3 EXT-PHASE3B-CORPUS
└── 3 EXT-LEGACY-UV-AUTHORITY
```

---

## 2. Required P0 end state

P0 is complete only when all of the following are true:

1. A single candidate lineage descends from `main` without merge-base divergence.
2. `make ci` is the canonical source-safe gate.
3. `make ci-portable` passes from a clean hosted runner without host authorities.
4. Topology validation derives its date only from a validated sealed foundation context.
5. CLI or environment date overrides are rejected during topology audits.
6. All **32 portable source defects** are fixed and removed from the unresolved hosted inventory.
7. The remaining unresolved inventory contains exactly:
   - 24 native-capability cases;
   - 6 external-authority cases;
   - zero portable defects.
8. Portable CI can accept a properly bound `UNAVAILABLE` receipt for absent native/external resources, but never label that lane `PASS`.
9. `make ci-host-authority` requires real execution and returns non-zero for `UNAVAILABLE`.
10. CI artifacts are schema-validated, hash-bound, no-clobber, secret-safe, and tied to the exact head SHA and failure-inventory digest.
11. Two consecutive GitHub Actions executions on the same qualified SHA pass with the same semantic result digest.
12. Promotion to `main` is fast-forward only and occurs only after explicit operator authorization.
13. No production service, database, scheduler, broker, exchange, account, order endpoint, or live-trading gate is changed.

---

## 3. Non-goals

P0 must not include:

- real Nautilus backtest/paper engine integration;
- Qlib, FinRL-X, TradingAgents, or Freqtrade code harvesting;
- strategy or portfolio-algorithm changes;
- live execution;
- production deployment or release activation;
- production PostgreSQL access or migration;
- changing broker/exchange credentials;
- weakening risk, kill-switch, source-custody, or publication invariants;
- broad test skipping;
- synthesizing an external corpus or replacing an authority-bound executable.

Those belong to later packages.

---

## 4. P0 invariants

### P0-I01 — One source lineage

```text
origin/main
    ↓ fast-forward ancestry only
P0 candidate
    ↓ qualification
origin/main
```

No merge commit may hide divergence.

### P0-I02 — One validation-date authority

For topology audits:

```text
sealed foundation context
            ↓
foundation_validation_date
```

Forbidden sources:

```text
--today
FOUNDATION_VALIDATION_DATE
wall-clock date.today()
datetime.now()
GitHub runner local date
policy-file fallback interpreted as runtime authority
```

### P0-I03 — No false portable pass

```text
portable source defect      → FIX or FAIL
native capability absent    → UNAVAILABLE receipt
external authority absent   → UNAVAILABLE receipt
resource present but invalid→ FAIL
resource present and valid  → RUN and PASS/FAIL
```

### P0-I04 — One receipt cannot prove another lane

A Bubblewrap receipt cannot prove user-namespace availability. A corpus receipt cannot prove the retained UV executable. Every capability/authority code requires an exact receipt.

### P0-I05 — Production validators stay strict

Fixture portability must be fixed in test construction. Do not relax production UID/GID, source digest, executable identity, ownership, sandbox, publication, or authority checks.

### P0-I06 — Portable CI has no money-moving authority

The default GitHub workflow must have:

```yaml
permissions:
  contents: read
```

and no secrets, production environment, broker call, exchange call, migration, deployment, service restart, scheduler mutation, or live-trading action.

---

## 5. Execution order

```text
P0-00  Establish isolated candidate workspace
   ↓
P0-01  Pin machine-readable baseline
   ↓
P0-02  Reproduce date-context failure
   ↓
P0-03  Fix sealed validation-date authority
   ↓
P0-04  Fix 27 sealed-UV/Bubblewrap fixture defects
   ↓
P0-05  Fix 5 UID/GID and fakeroot fixture defects
   ↓
P0-06  Regenerate failure inventory and close portable lane
   ↓
P0-07  Harden native-capability receipts
   ↓
P0-08  Harden external-authority receipts
   ↓
P0-09  Normalize Make targets and GitHub workflow split
   ↓
P0-10  Seal deterministic evidence and artifact firewall
   ↓
P0-11  Close documentation and executable matrix
   ↓
P0-12  Clean-clone qualification and adversarial review
   ↓
P0-13  Fast-forward promotion and post-promotion proof
```

Do not run P0-04 through P0-10 in parallel when they modify the same topology, Makefile, inventory, or governance files.

---

# Task P0-00 — Establish the isolated qualification workspace

**Owner/model:** Hermes orchestrator with Codex `gpt-5.6-terra`, reasoning medium.  
**Reviewer:** `gpt-5.6-sol`, reasoning high.

**Purpose:** Ensure all work begins from the existing candidate, not stale `main`, and prevent accidental changes to another worktree.

**Files:**

- No source changes in the setup step.
- Later create:
  - `docs/superpowers/plans/2026-08-13-p0-canonical-baseline-ci-closure.md`

### Step 1 — Fetch and verify the remote graph

Run:

```bash
git fetch --prune origin \
  main \
  codex/phase1-terra-autopilot-19627785c140

git rev-parse origin/main
git rev-parse origin/codex/phase1-terra-autopilot-19627785c140

git merge-base --is-ancestor \
  origin/main \
  origin/codex/phase1-terra-autopilot-19627785c140

git rev-list --count \
  origin/main..origin/codex/phase1-terra-autopilot-19627785c140

git rev-list --count \
  origin/codex/phase1-terra-autopilot-19627785c140..origin/main
```

Expected at plan creation:

```text
origin/main:
19627785c140c502260f864e462fed9b9925436e

candidate:
417c17452ea31f0ca8c8e9893ac3c03a3a90a7c1

ancestor check: exit 0
ahead: 106
behind: 0
```

### Step 2 — Stop on drift

Stop immediately if:

- `origin/main` is no longer an ancestor;
- the candidate branch was force-pushed;
- the candidate head differs and its new commits have not been reviewed;
- the current worktree has unrelated modifications.

Do not merge `main` into the candidate. Reassess ancestry first.

### Step 3 — Create a dedicated worktree

```bash
git worktree add \
  ../trading-agent-p0-canonical-baseline \
  -b p0/canonical-baseline-ci-closure \
  origin/codex/phase1-terra-autopilot-19627785c140

cd ../trading-agent-p0-canonical-baseline
git status --short
```

Expected:

```text
clean worktree
```

### Step 4 — Verify no production/runtime path is mounted into the worktree

Check:

```bash
find . -xdev -type l -print
git submodule status
git worktree list --porcelain
```

No repository symlink or nested runtime authority may be introduced.

### Step 5 — Save this plan in the repository

Create:

```text
docs/superpowers/plans/2026-08-13-p0-canonical-baseline-ci-closure.md
```

with this exact plan.

### Step 6 — Commit

```bash
git add -- \
  docs/superpowers/plans/2026-08-13-p0-canonical-baseline-ci-closure.md

git commit -m "docs(p0): add canonical baseline and CI closure plan"
```

---

# Task P0-01 — Pin the P0 baseline in a machine-readable manifest

**Owner/model:** Codex `gpt-5.6-terra`, medium.  
**Reviewer:** Sol high.

**Files:**

- Create: `ops/consolidation/p0-canonical-baseline.json`
- Create: `tests/consolidation/test_p0_canonical_baseline.py`
- Modify: `scripts/audit_canonical_repo.py`
- Modify: `Makefile`

### Step 1 — Write the failing manifest test

The test must require:

```json
{
  "schema_version": "p0-canonical-baseline/v1",
  "base_branch": "main",
  "base_sha": "19627785c140c502260f864e462fed9b9925436e",
  "candidate_source_branch": "codex/phase1-terra-autopilot-19627785c140",
  "candidate_start_sha": "417c17452ea31f0ca8c8e9893ac3c03a3a90a7c1",
  "qualified_sha": null,
  "promotion_mode": "fast-forward-only",
  "paper_only": true,
  "live_execution_authorized": false
}
```

Also require:

- strict keys, no unknown fields;
- all SHA fields are lowercase 40-character hex;
- `base_sha` and `candidate_start_sha` exist in local Git history;
- both are ancestors of current `HEAD`;
- `qualified_sha`, when non-null, equals current qualified head and descends from `candidate_start_sha`;
- `paper_only` is `true`;
- `live_execution_authorized` is `false`.

### Step 2 — Run the failing test

```bash
uv run pytest -q \
  tests/consolidation/test_p0_canonical_baseline.py
```

Expected:

```text
FAIL: manifest missing
```

### Step 3 — Add the manifest and audit integration

Implement:

- strict JSON schema validation;
- ancestry checks using argument-vector subprocess calls, never shell interpolation;
- no remote network fetch inside the audit;
- clear error codes:
  - `P0_BASELINE_MISSING`
  - `P0_BASELINE_SCHEMA_INVALID`
  - `P0_BASELINE_SHA_MISSING`
  - `P0_BASELINE_ANCESTRY_INVALID`
  - `P0_LIVE_AUTHORITY_FORBIDDEN`.

Add a Make target:

```make
check-p0-baseline:
	$(PYTHON) scripts/audit_canonical_repo.py --portable --check-p0-baseline
```

### Step 4 — Run focused tests

```bash
uv run pytest -q \
  tests/consolidation/test_p0_canonical_baseline.py

make check-p0-baseline
```

Expected:

```text
PASS
```

### Step 5 — Run the existing portable canonical audit

```bash
uv run python scripts/audit_canonical_repo.py --portable
```

Expected:

```text
PASS
```

### Step 6 — Commit

```bash
git add -- \
  ops/consolidation/p0-canonical-baseline.json \
  tests/consolidation/test_p0_canonical_baseline.py \
  scripts/audit_canonical_repo.py \
  Makefile

git commit -m "feat(p0): pin canonical candidate baseline"
```

---

# Task P0-02 — Reproduce and instrument `POLICY_DATE_CONTEXT_MISMATCH`

**Owner/model:** Codex Sol, reasoning xhigh.  
**Reviewer:** Sol xhigh with fresh context.

**Files:**

- Modify: `tests/governance/test_t_g03f_validation_date.py`
- Modify: `tests/test_t_g03_capability_topology.py`
- Modify: `scripts/check_test_governance.py`
- Modify: `scripts/t_g03_capability_topology.py`
- Create: `tests/fixtures/governance/README.md` only if a static fixture is unavoidable; prefer temporary fixtures.

### Step 1 — Add a subprocess-level failing regression

The new test must reproduce the production command shape, not call only an internal helper:

```bash
uv run python scripts/check_test_governance.py \
  --policy docs/implementation/foundation-test-governance.json \
  --failure-inventory docs/implementation/foundation-hosted-failure-inventory.tsv \
  --topology-audit \
  --foundation-context-path <temporary-valid-context>
```

Test conditions:

- environment starts without `FOUNDATION_VALIDATION_DATE`;
- context has:
  - exact head SHA;
  - run ID;
  - ISO date;
  - inventory digest;
  - valid context digest;
- no `--today` is passed.

Expected behavior:

```text
exit 0
```

Before the fix, preserve evidence of the current failure:

```text
POLICY_DATE_CONTEXT_MISMATCH
```

### Step 2 — Add negative tests

Require all of the following:

1. `--today 2026-08-13` during topology audit:
   - exit non-zero;
   - code `POLICY_DATE_CONTEXT_MISMATCH`.

2. `FOUNDATION_VALIDATION_DATE=2026-08-13` during topology audit:
   - exit non-zero;
   - code `POLICY_DATE_CONTEXT_MISMATCH`.

3. Missing context:
   - code `POLICY_FOUNDATION_CONTEXT_REQUIRED`.

4. Context head SHA mismatch:
   - code `POLICY_FOUNDATION_HEAD_MISMATCH`.

5. Context inventory digest mismatch:
   - code `POLICY_FOUNDATION_INVENTORY_MISMATCH`.

6. Malformed context:
   - code `POLICY_FOUNDATION_CONTEXT_INVALID`.

7. Error output must not print the value of an injected environment variable.

### Step 3 — Run the tests and record current behavior

```bash
uv run pytest -q \
  tests/governance/test_t_g03f_validation_date.py \
  tests/test_t_g03_capability_topology.py
```

Expected before implementation:

```text
at least the clean-context regression fails
```

### Step 4 — Add safe diagnostics

Extend the error artifact with booleans and origin labels only:

```json
{
  "date_context_sources": {
    "cli_today_present": false,
    "environment_override_present": false,
    "sealed_context_present": true,
    "sealed_context_valid": true
  }
}
```

Do not record:

- arbitrary environment values;
- secrets;
- full environment dumps;
- raw credential-bearing paths.

### Step 5 — Re-run focused tests

```bash
uv run pytest -q \
  tests/governance/test_t_g03f_validation_date.py \
  tests/test_t_g03_capability_topology.py
```

The clean-context test may remain failing until P0-03, but every negative test and diagnostic assertion must be stable.

### Step 6 — Commit

```bash
git add -- \
  tests/governance/test_t_g03f_validation_date.py \
  tests/test_t_g03_capability_topology.py \
  scripts/check_test_governance.py \
  scripts/t_g03_capability_topology.py

git commit -m "test(ci): reproduce sealed validation-date authority mismatch"
```

---

# Task P0-03 — Make the sealed foundation context the only topology date authority

**Owner/model:** Codex Sol, xhigh.  
**Reviewer:** Sol xhigh, fresh context.

**Files:**

- Modify: `scripts/check_test_governance.py`
- Modify: `scripts/t_g03_capability_topology.py`
- Modify: `tests/governance/test_t_g03f_validation_date.py`
- Modify: `tests/test_t_g03_capability_topology.py`
- Modify: `Makefile` only if the caller currently passes a standalone date.

### Step 1 — Introduce explicit date-origin classification

Represent date input origins as an enum or equivalent strict values:

```text
SEALED_FOUNDATION_CONTEXT
CLI_OVERRIDE
ENVIRONMENT_OVERRIDE
POLICY_FALLBACK
WALL_CLOCK
NONE
```

For `--topology-audit`, only:

```text
SEALED_FOUNDATION_CONTEXT
```

is accepted.

### Step 2 — Validate context before deriving the date

Required order:

```text
read bytes
  ↓
parse strict schema
  ↓
verify context digest
  ↓
verify head SHA
  ↓
verify run ID
  ↓
verify failure-inventory digest
  ↓
parse foundation_validation_date
  ↓
use date in governance checks
```

Do not parse the date from an unverified object.

### Step 3 — Distinguish policy metadata from runtime date authority

A date or expiry field inside the governance policy may constrain validity, but it must not be treated as a standalone runtime validation-date source during topology audit.

Required semantic:

```text
sealed date = evaluation date
policy dates = constraints evaluated against sealed date
```

### Step 4 — Preserve negative override guards

Do not solve the current failure by silently unsetting or ignoring:

```text
--today
FOUNDATION_VALIDATION_DATE
```

The caller must stop supplying them. If they are present, topology audit must fail.

### Step 5 — Remove wall-clock fallback from topology paths

Search:

```bash
rg -n \
  'date\.today|datetime\.now|FOUNDATION_VALIDATION_DATE|--today' \
  scripts/check_test_governance.py \
  scripts/t_g03_capability_topology.py \
  .github/workflows/foundation.yml \
  Makefile
```

Expected:

- negative-test and non-topology support references may remain;
- topology execution has no wall-clock fallback;
- workflow/Make caller passes no standalone date.

### Step 6 — Run focused tests

```bash
uv run pytest -q \
  tests/governance/test_t_g03f_validation_date.py \
  tests/test_t_g03_capability_topology.py \
  tests/test_test_governance_audit.py
```

Expected:

```text
PASS
```

### Step 7 — Run the production command shape

```bash
rm -rf runtime/state/ci-portable/capability-topology \
       runtime/state/ci-portable/test-governance

make ci-portable-topology NONINTERACTIVE=1
make check-test-governance-topology NONINTERACTIVE=1
```

Expected:

- no `POLICY_DATE_CONTEXT_MISMATCH`;
- valid context and aggregate receipts;
- later suite failures may now surface and are handled in subsequent tasks.

### Step 8 — Adversarial verification

```bash
FOUNDATION_VALIDATION_DATE=2099-01-01 \
make check-test-governance-topology NONINTERACTIVE=1
```

Expected:

```text
non-zero
POLICY_DATE_CONTEXT_MISMATCH
```

Then rerun without the variable and require success through the date-authority stage.

### Step 9 — Commit

```bash
git add -- \
  scripts/check_test_governance.py \
  scripts/t_g03_capability_topology.py \
  tests/governance/test_t_g03f_validation_date.py \
  tests/test_t_g03_capability_topology.py \
  Makefile

git commit -m "fix(ci): derive topology date only from sealed context"
```

---

# Task P0-04 — Close 27 sealed-UV portable fixture defects

**Owner/model:** Codex Terra medium for mechanical fixture extraction; Sol xhigh for security review.  
**Reviewer:** Sol xhigh.

**Files:**

- Modify: `tests/foundation/test_nautilus_sealed_uv_exec.py`
- Optionally create: `tests/foundation/sealed_uv_test_fixtures.py`
- Modify production code only if a genuine source bug is demonstrated after fixture repair.
- Modify: `docs/implementation/foundation-hosted-failure-inventory.tsv` only after tests pass.

### Problem

Twenty-seven tests prove source, policy, descriptor, publication, or rejection semantics but construct a policy by statting:

```text
/usr/bin/bwrap
```

before reaching their primary assertion.

These are test-fixture defects. They must not be deferred as native-capability tests.

### Step 1 — Add a controlled executable fixture

Create a helper that:

1. creates a temporary regular file;
2. writes deterministic inert bytes;
3. sets executable mode;
4. opens/stat-checks it without following symlinks;
5. derives exact:
   - path;
   - SHA-256;
   - UID;
   - GID;
   - mode;
6. returns a policy binding object.

Example logical API:

```python
@dataclass(frozen=True)
class SandboxExecutableFixture:
    path: Path
    sha256: str
    uid: int
    gid: int
    mode: int

def create_synthetic_sandbox_executable(tmp_path: Path) -> SandboxExecutableFixture:
    ...
```

Do not make the synthetic executable claim to provide Bubblewrap isolation.

### Step 2 — Make pure-policy helpers accept an injected binding

Refactor helpers such as `_task3_policy` or `_write_policy` so tests can pass:

```python
sandbox_binding=synthetic_fixture
```

The helper must no longer stat `/usr/bin/bwrap` unless the specific test is classified as `NATIVE-BWRAP-OS-SANDBOX`.

### Step 3 — Keep native tests explicit

Tests that actually execute Bubblewrap or prove namespace/mount isolation must call an explicit helper such as:

```python
real_bubblewrap_binding()
```

and remain in the native capability lane.

Do not use the synthetic executable to make these tests green.

### Step 4 — Run the exact portable selection

Use the inventory-driven lane rather than a hand-maintained skip list:

```bash
uv run python scripts/t_g03_capability_topology.py \
  run-lane \
  --lane portable_source \
  --failure-inventory docs/implementation/foundation-hosted-failure-inventory.tsv \
  --foundation-context-path runtime/state/ci-portable/capability-topology/foundation-context.json \
  --evidence-root runtime/state/ci-portable/capability-topology
```

Also run:

```bash
uv run pytest -q \
  tests/foundation/test_nautilus_sealed_uv_exec.py
```

The full file may still report genuine native-capability cases on a host without Bubblewrap. The inventory-driven portable selection must pass all 27 target cases.

### Step 5 — Add anti-regression tests

Require:

- pure-policy helpers never open `/usr/bin/bwrap`;
- synthetic executable is regular, executable, non-symlink, and digest-bound;
- a changed synthetic executable digest is rejected;
- a symlink replacement is rejected;
- native tests cannot accidentally receive the synthetic marker;
- production validators remain unchanged.

### Step 6 — Update closure evidence

Do not simply delete the 27 rows. Move them to:

```text
docs/implementation/foundation-portable-defect-closure.tsv
```

with:

```text
test_node_id
former_classification
fix_commit
proof_command
proof_status
```

Remove them from the unresolved hosted inventory only after proof passes.

### Step 7 — Commit

```bash
git add -- \
  tests/foundation/test_nautilus_sealed_uv_exec.py \
  tests/foundation/sealed_uv_test_fixtures.py \
  docs/implementation/foundation-hosted-failure-inventory.tsv \
  docs/implementation/foundation-portable-defect-closure.tsv

git commit -m "fix(test): decouple portable sealed-uv proofs from bwrap"
```

Omit the optional helper path from `git add` if it was not created.

---

# Task P0-05 — Close five UID/GID and fakeroot fixture defects

**Owner/model:** Codex Sol high.  
**Reviewer:** Sol xhigh.

**Files:**

- Modify: `tests/runtime_release/test_semantic.py`
- Modify: `tests/runtime_release/test_provision_script.py`
- Modify test helpers in those files or a narrowly scoped shared fixture file.
- Do not weaken production identity validation.

## P0-05A — Three semantic identity fixtures

Exact tests:

```text
tests/runtime_release/test_semantic.py::
  test_semantic_attestation_uses_the_v2_authority_input_root

tests/runtime_release/test_semantic.py::
  test_stable_policy_accepts_valid_active_rotation_without_authority_rewrite

tests/runtime_release/test_semantic.py::
  test_attestation_exposes_every_exact_dynamic_semantic_identity
```

### Step 1 — Write negative identity tests first

For each fixture path, prove:

- current process UID/GID is accepted;
- `uid + 1` is rejected;
- `gid + 1` is rejected;
- production error does not leak unrelated data.

### Step 2 — Fix fixture construction

Replace hard-coded runtime identity:

```text
1000:1000
```

with:

```python
os.geteuid()
os.getegid()
```

only for the authority identity of the process executing the hermetic unit test.

### Step 3 — Run focused tests

```bash
uv run pytest -q \
  tests/runtime_release/test_semantic.py::test_semantic_attestation_uses_the_v2_authority_input_root \
  tests/runtime_release/test_semantic.py::test_stable_policy_accepts_valid_active_rotation_without_authority_rewrite \
  tests/runtime_release/test_semantic.py::test_attestation_exposes_every_exact_dynamic_semantic_identity
```

Expected:

```text
PASS
```

## P0-05B — Two fakeroot identity fixtures

Exact tests:

```text
tests/runtime_release/test_provision_script.py::
  test_root_snapshot_keeps_root_owned_boundary_for_user_owned_stage

tests/runtime_release/test_provision_script.py::
  test_fakeroot_generates_all_four_envs_with_exact_safe_environment
```

### Step 4 — Separate signed runtime identity from simulated payload ownership

Use distinct fields/helpers:

```text
signed_runtime_uid/gid
simulated_file_uid/gid
```

Required semantics:

- signed runtime identity matches the executing process;
- fakeroot may simulate target payload ownership;
- simulated ownership cannot overwrite or masquerade as signed runtime identity.

### Step 5 — Add adversarial tests

Require rejection when:

- signed UID/GID does not match the process;
- simulated owner is used as authority owner;
- fakeroot output changes ownership beyond the expected inventory;
- signed identity fields are missing.

### Step 6 — Run focused tests

```bash
uv run pytest -q \
  tests/runtime_release/test_provision_script.py::test_root_snapshot_keeps_root_owned_boundary_for_user_owned_stage \
  tests/runtime_release/test_provision_script.py::test_fakeroot_generates_all_four_envs_with_exact_safe_environment
```

Expected:

```text
PASS
```

### Step 7 — Record closure and commit

Move the five rows to the portable defect closure inventory only after passing.

```bash
git add -- \
  tests/runtime_release/test_semantic.py \
  tests/runtime_release/test_provision_script.py \
  docs/implementation/foundation-hosted-failure-inventory.tsv \
  docs/implementation/foundation-portable-defect-closure.tsv

git commit -m "fix(test): bind portable runtime identity to current process"
```

---

# Task P0-06 — Regenerate the inventory and close the portable-source lane

**Owner/model:** Codex Sol high.  
**Reviewer:** Sol xhigh.

**Files:**

- Modify: `docs/implementation/foundation-hosted-failure-inventory.tsv`
- Modify/Create: `docs/implementation/foundation-portable-defect-closure.tsv`
- Modify: `scripts/audit_test_governance.py`
- Modify: `scripts/t_g03_capability_topology.py`
- Modify: `tests/test_test_governance_audit.py`
- Modify: `tests/test_t_g03_capability_topology.py`
- Modify: `Makefile`

### Step 1 — Add a failing governance assertion

The unresolved hosted inventory must reject:

```text
classification == PORTABLE_SOURCE_DEFECT
source_fix_required == YES
dedicated_gate == planned:portable-source
```

after P0 source closure.

Expected final inventory:

```text
total unresolved: 30
native:           24
external:          6
portable defects:  0
```

### Step 2 — Preserve historical closure separately

`foundation-portable-defect-closure.tsv` must contain all 32 former rows with:

```text
test_node_id
source_file
former_capability_code
fix_commit
proof_command
proof_result_digest
closed_at_foundation_date
```

`closed_at_foundation_date` comes from the sealed context, not the wall clock.

### Step 3 — Reject stale or fabricated closure records

Tests must prove rejection for:

- missing test node;
- duplicate test node;
- non-existent fix commit;
- fix commit not in current history;
- proof command absent;
- malformed digest;
- a supposedly closed test still failing;
- a row present in both unresolved and closed inventories.

### Step 4 — Run governance tests

```bash
uv run pytest -q \
  tests/test_test_governance_audit.py \
  tests/test_t_g03_capability_topology.py
```

### Step 5 — Run portable topology

```bash
make ci-portable-topology NONINTERACTIVE=1
```

Expected:

```text
portable_source: PASS
native_capability: PASS or UNAVAILABLE receipt
external_authority: PASS or UNAVAILABLE receipt
aggregate source verdict: PASS
```

The aggregate must include the non-pass lane statuses explicitly.

### Step 6 — Commit

```bash
git add -- \
  docs/implementation/foundation-hosted-failure-inventory.tsv \
  docs/implementation/foundation-portable-defect-closure.tsv \
  scripts/audit_test_governance.py \
  scripts/t_g03_capability_topology.py \
  tests/test_test_governance_audit.py \
  tests/test_t_g03_capability_topology.py \
  Makefile

git commit -m "governance: close portable hosted-failure inventory"
```

---

# Task P0-07 — Harden native-capability receipts

**Owner/model:** Codex Sol xhigh.  
**Reviewer:** Sol xhigh, fresh context.

**Files:**

- Modify: `scripts/t_g03_capability_topology.py`
- Modify: `tests/test_t_g03_capability_topology.py`
- Modify: `docs/implementation/foundation-test-governance.json`
- Modify: `docs/implementation/foundation-test-governance.md`
- Modify: `Makefile`

### Required groups

```text
16 NATIVE-BWRAP-OS-SANDBOX
8  NATIVE-USERNS-ROOT-PROVISION
```

### Step 1 — Define the receipt schema

Each native capability receipt must contain:

```json
{
  "schema_version": "t-g03-capability-receipt/v1",
  "lane": "native_capability",
  "capability_code": "NATIVE-BWRAP-OS-SANDBOX",
  "status": "PASS|FAIL|UNAVAILABLE",
  "foundation_head_sha": "...",
  "foundation_run_id": "...",
  "foundation_context_sha256": "...",
  "failure_inventory_sha256": "...",
  "probe": {
    "command_id": "...",
    "exit_code": 0,
    "stdout_sha256": "...",
    "stderr_sha256": "..."
  },
  "selected_test_count": 16,
  "passed": 0,
  "failed": 0,
  "unavailable": 16
}
```

No raw secret-bearing stdout/stderr is required in the summary.

### Step 2 — Implement a real Bubblewrap probe

The probe must distinguish:

```text
binary absent                 → UNAVAILABLE
binary present but nonregular → FAIL
identity/digest policy invalid→ FAIL
namespace operation denied    → UNAVAILABLE only when classified host limitation
probe succeeds                → execute all 16 tests
test failure                  → FAIL
```

Do not use a synthetic executable in this lane.

### Step 3 — Implement a real user-namespace/root-provision probe

The probe must test the exact capability needed by the eight tests, such as an isolated:

```bash
unshare --user --map-root-user true
```

or the project’s stricter equivalent.

Required semantics:

```text
command absent                  → UNAVAILABLE
host policy prohibits uid map   → UNAVAILABLE
partial namespace setup         → FAIL
probe succeeds                  → execute all 8 tests
test failure                    → FAIL
```

### Step 4 — Define exit behavior by caller

For portable aggregation:

```text
PASS        accepted
UNAVAILABLE accepted but surfaced
FAIL        rejected
```

For host-authority qualification:

```text
PASS        accepted
UNAVAILABLE rejected
FAIL        rejected
```

### Step 5 — Add adversarial tests

Test:

- forged `PASS` without a successful probe;
- receipt for the wrong head SHA;
- receipt for the wrong inventory digest;
- capability code mismatch;
- selected-test count mismatch;
- missing test node;
- partial test execution;
- present-but-invalid resource mislabeled unavailable;
- duplicate receipt publication;
- receipt overwrite attempt.

### Step 6 — Run tests and lane

```bash
uv run pytest -q \
  tests/test_t_g03_capability_topology.py

make ci-portable-topology NONINTERACTIVE=1
```

### Step 7 — Commit

```bash
git add -- \
  scripts/t_g03_capability_topology.py \
  tests/test_t_g03_capability_topology.py \
  docs/implementation/foundation-test-governance.json \
  docs/implementation/foundation-test-governance.md \
  Makefile

git commit -m "feat(ci): seal native capability receipts"
```

---

# Task P0-08 — Harden external-authority receipts

**Owner/model:** Codex Sol xhigh.  
**Reviewer:** Sol xhigh.

**Files:**

- Modify: `scripts/t_g03_capability_topology.py`
- Modify: `tests/test_t_g03_capability_topology.py`
- Modify: `docs/implementation/foundation-test-governance.json`
- Modify: `docs/implementation/foundation-test-governance.md`
- Modify: `Makefile`

### Required groups

```text
3 EXT-PHASE3B-CORPUS
3 EXT-LEGACY-UV-AUTHORITY
```

### Step 1 — Implement Phase 3B corpus preflight

State classification:

```text
root absent
  → UNAVAILABLE

root present, manifest missing
  → FAIL

root present, count/hash mismatch
  → FAIL

root present, exact reviewed authority validates
  → run 3 tests
```

Do not:

- create a fake corpus;
- download replacement data;
- change reviewed counts to match the current host;
- mark an invalid partial corpus unavailable.

### Step 2 — Implement legacy UV authority preflight

State classification:

```text
exact path absent
  → UNAVAILABLE

path is symlink/special file
  → FAIL

digest/version/UID/GID/mode mismatch
  → FAIL

authority and sealed environment valid
  → run 3 tests
```

Do not substitute a different `uv` binary.

### Step 3 — Bind receipts to exact authority facts

Receipt must include only safe identity evidence:

```text
authority code
regular-file status
expected digest
observed digest
expected version
observed version
expected ownership/mode
observed ownership/mode
corpus manifest digest/counts
```

Do not include secrets or mutable research contents.

### Step 4 — Add adversarial tests

Require rejection for:

- absent authority labeled `PASS`;
- partial corpus labeled `UNAVAILABLE`;
- wrong UV binary accepted by version only;
- correct digest at wrong path;
- symlink replacement;
- authority changed after probe but before test;
- stale receipt from another head SHA;
- receipt reused across run IDs.

### Step 5 — Run tests

```bash
uv run pytest -q \
  tests/test_t_g03_capability_topology.py

make ci-portable-topology NONINTERACTIVE=1
```

Expected on a normal hosted runner:

```text
external_authority: UNAVAILABLE
portable aggregate: PASS with explicit unavailable status
```

Expected on the approved authority host:

```text
external_authority: PASS
```

### Step 6 — Commit

```bash
git add -- \
  scripts/t_g03_capability_topology.py \
  tests/test_t_g03_capability_topology.py \
  docs/implementation/foundation-test-governance.json \
  docs/implementation/foundation-test-governance.md \
  Makefile

git commit -m "feat(ci): seal external authority receipts"
```

---

# Task P0-09 — Normalize Make targets and split portable versus host workflows

**Owner/model:** Codex Terra medium.  
**Reviewer:** Sol high.

**Files:**

- Modify: `Makefile`
- Modify: `.github/workflows/foundation.yml`
- Create: `.github/workflows/host-authority.yml`
- Modify: `tests/test_test_all_host_split.py`
- Add/modify workflow source tests under the existing governance test suite.
- Modify: `README.md`

### Step 1 — Write target-graph tests

Evidence correction: the focused topology suite is
`tests/governance/test_t_g03_capability_topology.py` (not
`tests/test_t_g03_capability_topology.py`). At the reviewed base, the focused
validation-date command reports four fixture-only failures because fixture
collectors include the active 30-row inventory but omit the P0-06 32-row
closure ledger. Repair those collectors to include all governed active and
closure nodes; do not weaken production accounting or closure policy.

Required final graph:

```text
ci
└── ci-portable

ci-portable
├── one common/private source-safe prerequisite route
├── ci-portable-topology
├── check-test-governance-topology
├── artifact-firewall-check
└── audit-delivery-contract

ci-host-authority
├── check-p0-baseline
├── native capability lane with require-pass=true
├── external authority lane with require-pass=true
└── host/package qualification
```

Avoid executing common test suites twice by extracting one private/common prerequisite target.

### Step 2 — Refactor Make targets

Recommended logical form:

```make
ci-common-private:
	# source-safe tests/build/audits only

ci: ci-portable

ci-portable: ci-common-private ci-portable-topology \
	check-test-governance-topology \
	artifact-firewall-check \
	audit-delivery-contract

ci-host-authority:
	# explicit host authority qualification
```

No host target may be a prerequisite of `ci` or `ci-portable`.

### Step 3 — Harden the default workflow

`.github/workflows/foundation.yml` must include:

```yaml
permissions:
  contents: read

concurrency:
  group: foundation-${{ github.ref }}
  cancel-in-progress: true
```

Also require:

- `ubuntu-latest`;
- fixed Python/Node/uv versions;
- `uv sync --frozen`;
- `npm ci`;
- timeout;
- no `pull_request_target`;
- no production environment;
- no secrets;
- `make ci-portable NONINTERACTIVE=1`;
- always upload portable evidence;
- artifact retention bounded explicitly.

### Step 4 — Create the host-authority workflow

`.github/workflows/host-authority.yml`:

- `workflow_dispatch` only;
- protected GitHub environment, e.g. `trading-authority`;
- approved self-hosted labels;
- read-only repository permission unless a later separate plan authorizes more;
- calls:

```bash
make ci-host-authority NONINTERACTIVE=1
```

- `UNAVAILABLE` must fail this workflow;
- no deployment, activation, migration, broker, exchange, or live action.

### Step 5 — Add workflow adversarial tests

Reject:

- default workflow using `self-hosted`;
- host workflow triggered by pull request;
- `pull_request_target`;
- write permissions;
- secret interpolation in portable workflow;
- standalone validation-date env;
- production environment in portable workflow;
- missing artifact upload on failure;
- host target reachable from `ci-portable`.

### Step 6 — Run tests

```bash
uv run pytest -q \
  tests/test_test_all_host_split.py \
  tests/governance/test_t_g03f_validation_date.py \
  tests/governance/test_t_g03_capability_topology.py

make ci-portable NONINTERACTIVE=1
```

### Step 7 — Commit

```bash
git add -- \
  Makefile \
  .github/workflows/foundation.yml \
  .github/workflows/host-authority.yml \
  tests/test_test_all_host_split.py \
  tests/governance/test_t_g03f_validation_date.py \
  tests/test_t_g03_capability_topology.py \
  README.md

git commit -m "refactor(ci): isolate portable and host authority gates"
```

---

# Task P0-10 — Seal deterministic evidence and enforce the artifact firewall

**Owner/model:** Codex Sol high.  
**Reviewer:** Sol xhigh.

**Files:**

- Modify: `scripts/t_g03_capability_topology.py`
- Modify: `scripts/check_test_governance.py`
- Create: `scripts/check_artifact_firewall.py`
- Modify: `tests/governance/test_t_g03_capability_topology.py`
- Create/modify: `tests/test_artifact_firewall.py`
- Modify: `.github/workflows/foundation.yml`
- Modify: `Makefile`

### Step 1 — Define the final artifact layout

```text
runtime/state/ci-portable/
├── manifest.json
├── SHA256SUMS
├── capability-topology/
│   ├── sealed Foundation context/reservation/baseline/closure/aggregate files
│   ├── NATIVE-BWRAP-OS-SANDBOX.json
│   ├── NATIVE-BWRAP-OS-SANDBOX.artifacts/{receipt.json,governance.json,manifest.json}
│   ├── NATIVE-USERNS-ROOT-PROVISION.json
│   ├── NATIVE-USERNS-ROOT-PROVISION.artifacts/{receipt.json,governance.json,manifest.json}
│   ├── EXT-PHASE3B-CORPUS.json
│   ├── EXT-PHASE3B-CORPUS.artifacts/{receipt.json,governance.json,manifest.json}
│   └── EXT-LEGACY-UV-AUTHORITY.json plus its deterministic `.artifacts` bundle
├── test-governance/
│   ├── summary.json
│   └── error.json              # only on error
└── phase-evidence/
    └── ...
```

The exact auxiliary filenames come from the current strict schemas. Preserve
P0-07/P0-08 Architecture-A append-only bundle-then-marker acceptance: flat
native/external receipts, random staging leftovers, stale bundles, and glob-only
discovery are not authority evidence. Topology and governance commands write to
a private raw staging root; only the validated final publisher may create the
final root, and it must not merge with or overwrite an existing destination.

### Step 2 — Separate semantic digest from run metadata

The semantic result digest must exclude nondeterministic fields such as upload time.

Bind semantic output to a canonical projection of:

```text
head SHA
source tree
foundation context
failure inventory
policy digest
selected test node IDs
test outcomes
capability/authority statuses
```

Run metadata may separately contain:

```text
run ID
attempt when available
generated_at_utc
```

Two attempts on the same head/tree/date/policy/inventory/nodes/outcomes/statuses
must yield the same semantic digest even when run ID, attempt, timestamps,
Foundation self-hash, receipt self-hashes, and filesystem identity differ. The
semantic projection still binds Foundation head/date meaning and receipt
outcomes/counts. Complete bytes and run identity are bound independently by the
manifest integrity binding and exact sorted `SHA256SUMS` entries.

### Step 3 — Enforce no-clobber publication

Evidence writing must:

1. require a private current-user staging root and validate every ancestor;
2. open directories and leaves descriptor-relatively with no-follow semantics;
3. accept only the closed manifest-listed layout and reject traversal,
   duplicates, hardlinks, symlinks, special files, and extra files;
4. validate topology/governance schemas and Architecture-A relationships;
5. write canonical manifest/checksums, fsync, and validate retained bytes;
6. publish with Linux `renameat2(RENAME_NOREPLACE)` at the exact destination;
7. resolve ambiguous success from retained/named identity and bytes without
   unlinking or rolling back a possibly foreign published set;
8. leave failed staging private and inert; and
9. reject mutation at every seal/publication boundary.

Reuse the retained-FD, canonical JSON, hashing, and no-replace primitives. Do
not duplicate or weaken the P0-07/P0-08 per-code transactions.

### Step 4 — Expand the artifact firewall

Reject evidence containing patterns for:

```text
TRADING_MASTER_KEY
LIVE_EXECUTION_ENABLED=true
LIVE_TRADING_ENABLED=true
password
secret
api_key
authorization
private key material
database URL credentials
exchange/broker credential fields
```

Inspect retained structured bytes, including nested/list stdout and stderr
values. Avoid broad substring false positives for documentation, node IDs,
redacted values, hashes, and safe status keys. Diagnostics must never print the
secret value and may expose only a closed error code, relative identity/hash,
and structured key category.

### Step 5 — Add adversarial tests

Test:

- symlink artifact root;
- existing destination;
- partial manifest;
- digest mismatch;
- duplicate file;
- extra unmanifested file;
- stale head SHA;
- stale inventory/context/policy and run/head bindings;
- nested structured stdout/stderr secret values and known credential formats;
- redacted and safe near-misses;
- mutable evidence after manifest generation and boundary replacement races;
- hardlink/symlink/special/missing/extra leaves;
- attempted marker/bundle overwrite or flat-receipt fallback; and
- run-metadata variants with equal semantic digests plus a semantic mutation
  that changes the digest.

### Step 6 — Run tests and inspect artifact

```bash
uv run pytest -q \
  tests/governance/test_t_g03_capability_topology.py \
  tests/test_artifact_firewall.py

actionlint .github/workflows/foundation.yml .github/workflows/host-authority.yml
git diff --check
```

Run a bounded real final-set construction at exact committed HEAD only when the
existing schemas support a truthfully labeled local qualification namespace.
Otherwise defer the authoritative `make ci-portable NONINTERACTIVE=1` gate to
P0-12; never fabricate a GitHub run identity.

### Step 7 — Commit

```bash
git add -- \
  scripts/t_g03_capability_topology.py \
  scripts/check_test_governance.py \
  scripts/check_artifact_firewall.py \
  tests/test_t_g03_capability_topology.py \
  tests/test_artifact_firewall.py \
  .github/workflows/foundation.yml \
  Makefile

git commit -m "feat(ci): seal deterministic portable evidence"
```

---

# Task P0-11 — Close the executable P0 matrix and documentation

**Owner/model:** Codex Terra medium.  
**Reviewer:** Sol high.

**Files:**

- Create: `docs/implementation/p0-ci-closure-matrix.json`
- Create: `docs/implementation/p0-ci-closure.md`
- Modify: `docs/implementation/foundation-test-governance.md`
- Modify: `README.md`
- Create: `scripts/check_p0_ci_closure.py`
- Create: `tests/test_p0_ci_closure.py`
- Modify: `Makefile`
- Validate unchanged: `ops/consolidation/p0-canonical-baseline.json` with
  `qualified_sha: null`

### Step 1 — Write the failing closure-matrix test

Every P0 requirement must bind:

```text
requirement_id
implementation_paths
test_node_ids
make_target
workflow
evidence_path
required_status
```

Example:

```json
{
  "requirement_id": "P0-I02",
  "implementation_paths": [
    "scripts/check_test_governance.py",
    "scripts/t_g03_capability_topology.py"
  ],
  "test_node_ids": [
    "tests/governance/test_t_g03f_validation_date.py::..."
  ],
  "make_target": "check-test-governance-topology",
  "workflow": ".github/workflows/foundation.yml",
  "evidence_paths": ["runtime/state/ci-portable/capability-topology/foundation-context.json"],
  "required_status": "PASS"
}
```

### Step 2 — Implement the checker

`check_p0_ci_closure.py` must reject:

- missing implementation file;
- missing test;
- uncollected test node;
- unknown Make target;
- workflow not invoking the target;
- duplicate requirement;
- unresolved portable defect;
- missing evidence binding;
- live-trading authority enabled;
- a complete state unless explicit completion mode validates an exact-head,
  sealed P0-10 final evidence receipt through the published-evidence validator.

### Step 3 — Add Make target

```make
check-p0-ci-closure:
	$(PYTHON) scripts/check_p0_ci_closure.py \
	  --matrix docs/implementation/p0-ci-closure-matrix.json
```

Add it to `ci-portable` only after its own tests pass.

### Step 4 — Update documentation

Document clearly that `QUALIFICATION_PENDING` is not `P0_SOURCE_COMPLETE`, and
that source completion does not authorize any later authority:

```text
P0 SOURCE COMPLETE
```

does not imply:

```text
HOST AUTHORITY QUALIFIED
PRODUCTION ACTIVATED
LIVE TRADING ENABLED
```

README must state:

- `make ci` is source-safe;
- `make ci-host-authority` is explicit and operator-managed;
- host `UNAVAILABLE` receipts are not PASS;
- real Nautilus integration is P1.

### Step 5 — Update baseline manifest

Keep `qualified_sha: null`. P0-12 owns clean-clone qualification and binds the
exact candidate head in its sealed final receipt; do not set a future or
self-referential source SHA.

### Step 6 — Run tests

```bash
uv run pytest -q \
  tests/test_p0_ci_closure.py \
  tests/consolidation/test_p0_canonical_baseline.py

make check-p0-ci-closure
make check-p0-baseline
```

### Step 7 — Commit

```bash
git add -- \
  docs/implementation/p0-ci-closure-matrix.json \
  docs/implementation/p0-ci-closure.md \
  docs/implementation/foundation-test-governance-evidence.md \
  README.md \
  scripts/check_p0_ci_closure.py \
  tests/test_p0_ci_closure.py \
  Makefile \

git commit -m "docs(ci): close executable P0 qualification matrix"
```

---

## P0-11R correction — Recursive-Make argument authority

P0-11 remains `QUALIFICATION_PENDING`, with `qualified_sha: null`. Before
P0-12 qualification, close the recursive-Make argument seam without changing
the reviewed `ci-portable` custody wrapper, Makefile behavior, workflows, or
the existing control surface.

For every recursive-Make graph edge other than the exact approved
`ci-portable` custody wrapper, recognize only a whole, unconditional
`$(MAKE)` recipe followed by one or more plain target names. Each target must
match `[A-Za-z0-9_.-]+`, must not begin with `-`, and must not contain `=`.
Multiple targets remain authoritative because canonical routes may execute
several targets in one submake.

Options and assignments contribute no graph edges in any position. This
includes short, combined, and long options; the `--` marker; option arguments;
and every `NAME=value` form, including overrides for `MAKE`, `MAKEFLAGS`,
`GNUMAKEFLAGS`, `MFLAGS`, `MAKEOVERRIDES`, `SHELL`, `.SHELLFLAGS`, and `PATH`.
Unsafe recursive invocations in unrelated recipes may remain executable, but
must not invalidate the canonical Makefile and must contribute zero closure
authority.

Prove the correction test-first with real GNU Make in temporary repositories:
the accepted single- and multi-target forms must execute their sentinels, and
dry-run, touch, alternate-makefile, assignment, long-option, combined-option,
and after-target mutations must demonstrate skipped execution before being
rejected as route-unreachable graph evidence. Preserve every other P0-11
closure check and keep P0-12, promotion, production, and live authority out of
scope.

---

# Task P0-12 — Clean-clone qualification and adversarial final review

**Owner/model:** Hermes orchestrator.  
**Implementer:** No new feature work unless verification finds a defect.  
**Final reviewer:** Sol xhigh with fresh context and no prior verdict.

### Step 1 — Create a separate clean qualification clone/worktree

Do not clean the development worktree destructively.

```bash
git worktree add \
  ../trading-agent-p0-qualification \
  HEAD

cd ../trading-agent-p0-qualification
git status --short
```

Expected:

```text
clean
```

### Step 2 — Install only locked dependencies

```bash
uv sync --frozen

cd apps/dashboard
npm ci
cd ../..
```

### Step 3 — Run focused gates

```bash
make check-p0-baseline
make check-p0-ci-closure

uv run pytest -q \
  tests/governance/test_t_g03f_validation_date.py \
  tests/test_t_g03_capability_topology.py \
  tests/test_test_governance_audit.py \
  tests/test_test_all_host_split.py \
  tests/consolidation/test_p0_canonical_baseline.py \
  tests/test_p0_ci_closure.py
```

### Step 4 — Run full portable CI twice locally

```bash
rm -rf runtime/state/ci-portable runtime/state/p0-qualification
make ci-portable NONINTERACTIVE=1

mkdir -p -m 0700 runtime/state/p0-qualification
cp -a runtime/state/ci-portable runtime/state/p0-qualification/run-1

rm -rf runtime/state/ci-portable
make ci-portable NONINTERACTIVE=1

cp -a runtime/state/ci-portable runtime/state/p0-qualification/run-2
```

Compare semantic digests:

```bash
python - <<'PY'
import json
from pathlib import Path

a = json.loads(Path("runtime/state/p0-qualification/run-1/manifest.json").read_text())
b = json.loads(Path("runtime/state/p0-qualification/run-2/manifest.json").read_text())

assert a["semantic_result_sha256"] == b["semantic_result_sha256"]
print(a["semantic_result_sha256"])
PY
```

### Step 5 — Verify host gate fails closed on an ordinary host

Run only when safe and non-mutating:

```bash
make ci-host-authority NONINTERACTIVE=1
```

Expected on a host lacking authority:

```text
non-zero
native/external lane reported UNAVAILABLE
no production mutation attempted
```

Do not require host PASS for the portable source-complete verdict.

### Step 6 — Push the qualification branch only after explicit authorization

The implementation plan itself does not authorize push.

When authorized:

```bash
git push -u origin p0/canonical-baseline-ci-closure
```

### Step 7 — Require two GitHub Actions attempts on the exact same head SHA

Both must show:

```text
portable source: PASS
native lane: PASS or bound UNAVAILABLE
external lane: PASS or bound UNAVAILABLE
aggregate source verdict: PASS
artifact firewall: PASS
closure matrix: PASS
```

The semantic result digest must match.

### Step 8 — Final adversarial review

Reviewer must inspect:

1. exact diff from `417c174...` to final candidate;
2. topology date authority;
3. all 49 portable-defect closure proofs;
4. all 317 active authority receipt paths (58 native and 259 external), with
   the exact 366 governed and 6,131 portable-remainder accounting;
5. portable/host Make target reachability;
6. workflow permissions and triggers;
7. artifact publication and no-clobber semantics;
8. secret firewall;
9. baseline ancestry;
10. absence of production/live mutation.

Required reviewer output:

```text
VERDICT: PASS | FAIL

BLOCKING FINDINGS:
- severity
- file and exact line
- invariant
- concrete failure path
- minimal remediation
- adversarial test

PORTABLE SOURCE:
PASS | FAIL

NATIVE CAPABILITY RECEIPTS:
PASS | FAIL

EXTERNAL AUTHORITY RECEIPTS:
PASS | FAIL

FAST-FORWARD ELIGIBILITY:
YES | NO

PRODUCTION/LIVE AUTHORIZATION:
UNAVAILABLE
```

After a human `VERDICT: PASS`, encode that result as the canonical
`runtime/state/p0-qualification/final-review.json` receipt defined in
`docs/implementation/p0-ci-closure.md`, including exact HEAD/tree, both
manifest byte hashes, semantic digests, and run identities. Seal the
qualification root and review leaf to modes `0500` and `0400`, then require the
public checker to prove E11 without changing the committed pending matrix:

```bash
python scripts/check_p0_ci_closure.py \
  --matrix docs/implementation/p0-ci-closure-matrix.json \
  --qualification-receipt runtime/state/p0-qualification/run-1/manifest.json \
  --qualification-receipt runtime/state/p0-qualification/run-2/manifest.json \
  --final-review-receipt runtime/state/p0-qualification/final-review.json \
  --require-complete
```

Expected verdict: `P0_SOURCE_COMPLETE`. This proves E11 only; E12 remains
pending until the separately authorized P0-13 promotion and post-promotion
proof.

### Step 9 — Stop on any blocker

Do not proceed to promotion if:

- any portable defect remains;
- CI is green because of a broad skip;
- an unavailable lane is labeled PASS;
- candidate no longer fast-forwards from `main`;
- semantic digest differs between identical runs;
- artifact contains secrets;
- production/live behavior changes;
- final reviewer returns FAIL.

---

## P0-12R correction — Qualification fixture drift and report-directory custody

P0-12 stopped at exact clean candidate
`a764877e69fb5873c5c570cb4eb6e3e3ea3cafa3` after the corrected six-file
focused command reported 302 passed and three failed. The source matrix remains
`QUALIFICATION_PENDING`, `qualified_sha` remains null, and qualification may
restart only from the resulting independently reviewed remediation SHA.

Two `tests/governance/test_t_g03f_validation_date.py` fixtures must model the
reviewed P0-10 `ci-portable` order and binding without changing the Makefile:
create and privacy-check the private raw evidence root, capture Foundation
context using that exact root, allocate the disposable CI wrapper, and pass the
same raw root to recursive Make. The fresh root, not the caller/default
`$(TEST_EVIDENCE_DIR)`, is authoritative for the capture argument, capture
environment, and recursive Make environment. Preserve the existing observable
portable-route publisher and canonical-wrapper edge tests.

In both `scripts/check_critical_coverage.py` and
`scripts/check_test_governance.py`, only the exact requested report directory
may be tightened from a current-user-owned group/world-writable mode to `0700`.
A writable intermediate must fail closed without mode or identity mutation,
child creation, or artifact publication. Root-owned sticky `/tmp` remains a
trusted ancestor, a missing exact leaf below safe parents may be created as
`0700`, and exact-root tightening, symlink/special/foreign-owner rejection,
descriptor/no-follow publication, error types, and error messages remain
unchanged. Keep the two helpers behaviorally identical and use the smallest
condition change.

Prove the defect test-first: reproduce the three P0-12 failures, add a direct
test-governance writable-intermediate mutation test, then add paired coverage
for exact-root tightening, writable-intermediate rejection without mutation,
safe missing-leaf creation, and safely synthesizable symlink, special, and
foreign-owner parents. Run the affected tests and all P0 baseline, closure,
contract, workflow-lint, compile, and clean-tree gates in the remediation
brief, then repeat the affected set in a fresh standalone local clone at the
final commit. This packet does not authorize P0-12 qualification continuation,
push, hosted identity or dispatch, P0-13, dependency or lock changes,
production/live action, or runtime mutation.

---

## P0-12R2 correction — Stale D0 portable-private route fixture

Push-triggered Foundation run `31814654385`, attempt 1, at exact reviewed SHA
`f273090807764aef9d773e2f1efe2918d1b07137` stopped in D0 with 9 passed and
one failed before portable evidence publication. The failing node is
`tests/foundation/test_d0_closure.py::test_portable_ci_uses_a_private_linux_temp_root`.
Its exact-route fixture still expects the pre-P0-11 `ci-portable-private`
target list, while the reviewed current route additionally includes
`check-portable-defect-closure`, `check-p0-baseline`, and
`check-p0-ci-closure`.

P0-12R2 may correct only that stale exact-route assertion. The test must retain
all private Linux temporary-root, cleanup, capture-root, recursive-Make, and
single-execution assertions and must bind the complete current portable-private
route, including closure, baseline, governance, firewall, and delivery gates.
The Makefile route and `scripts/check_p0_ci_closure.py` recursive reachability
proof remain authoritative and unchanged; no duplicate route execution or host
gate may be introduced.

Capture the exact current node failure before changing the fixture, then run
the corrected node, the D0/host-split/P0-closure affected packet, D0/baseline/
closure/contract source gates, workflow lint, the corrected six-file P0-12
focused set, and the affected packet in a fresh standalone local clone at the
final commit. This packet does not authorize Makefile or workflow changes,
dependency or lock changes, inventory or matrix changes, push or remote branch
update, hosted rerun or dispatch, P0-12 qualification continuation, P0-13,
production/live/runtime/DB/service/scheduler action, or broker/exchange access.

---

## P0-12R3 correction — Scanner-clean adversarial firewall fixtures

Push-triggered Foundation run `31817143919`, attempt 1, at exact reviewed SHA
`04f366f315a838c4c8c095b85b56ba7cc93f0358` passed D0 11/11 and then
failed deterministically in `make check-secrets` before artifact publication.
The only findings were the private-key canaries at
`tests/test_artifact_firewall.py:458` and `:559` and the credential-URI
canary at `:459`.

P0-12R3 may change only those three credential-shaped test fixture literals.
Construct byte-identical runtime canaries from scanner-clean source fragments,
preserving their firewall categories, payload bytes, digest assertions,
redaction assertions, and manifested-leaf scan behavior. Do not weaken or
change the secret-hygiene scanner, Makefile, workflows, artifact-firewall
production code, dependencies, locks, inventories, matrix state, or
qualification artifacts.

Capture the exact three-finding `make check-secrets` RED at the required base,
then require scanner GREEN, the full artifact-firewall suite, the combined
secret-hygiene/firewall suite, D0/baseline/closure/contract source gates,
workflow lint, and clean diff/status evidence. This packet does not authorize
push, remote update, hosted rerun or dispatch, P0-12 continuation, P0-13,
production/live/runtime/DB/service/scheduler action, or broker/exchange access.

---

## P0-12R4 correction — Generated broad-handler inventory drift

Push-triggered Foundation run `31818664353`, attempt 1, at exact reviewed SHA
`c8d25398bcda8cc284cfd4701bf6a1ebf416a73b` passed D0 and secret hygiene, then
failed after the legacy backend reported 506 passed and two skipped. The exact
failing node was
`tests/test_live_execution_policy.py::test_machine_readable_broad_handler_inventory_has_exact_tracked_coverage`.
The canonical broad-handler generator observes 478 rows while the marked
inventory block documents 470: 18 observed rows are missing and 10 documented
rows are stale. Every difference is `TOOLING_MIGRATION` in
`scripts/audit_canonical_repo.py`, `scripts/check_artifact_firewall.py`, or
`scripts/t_g03_capability_topology.py`; no legacy production-handler source
change is required.

P0-12R4 may refresh only that marked machine-readable block by running
`uv run python scripts/check_broad_handler_inventory.py --write`. It must first
capture the exact node RED and exact 18-add/10-remove set, then independently
inspect every row and reject any non-tooling or unexpected path. Generator
logic, legacy source/tests, Makefile, workflows, dependencies, locks,
inventories outside the marked block, matrix state, and qualification artifacts
remain unchanged. The focused node, canonical `--check`, full legacy policy
module, secret/D0/P0/contract gates, workflow lint, diff checks, and clean state
must pass before independent review.

This packet does not authorize push, remote update, hosted rerun or dispatch,
P0-12 continuation, P0-13, promotion, production/live/runtime/DB/service/
scheduler action, or broker/exchange access.

---

## P0-12R5 correction — Hosted workflow Node runtime alignment

Push-triggered Foundation run `31820434403`, attempt 1, at exact reviewed SHA
`fa8855a138bc72d3bbfb80448ccfa2495390b6ff` passed the legacy backend with 507
passed and two skipped, then failed dashboard tests under workflow Node
20.20.2 with eight passed and 24 failed. The dashboard test harness imports
`node:module.registerHooks`, which requires Node 22.15.0 or newer; the reviewed
local Node 22.23.0 run passed all 171 dashboard tests. No artifact was uploaded
and the known failing SHA must not be rerun.

P0-12R5 may add one repository-shape contract requiring exactly one
`node-version` entry with scalar value `22` in each of Foundation and Host
Authority, then change only those two workflow scalar values from `20` to
`22`. Capture the focused contract RED before either workflow edit. Preserve
every other workflow byte and semantic, including triggers, permissions,
runners, live gates, steps, cache paths, Make commands, artifact rules, and
the protected host environment. Dashboard source, package manifests,
dependencies, lockfiles, Makefile, matrix state, and qualification artifacts
remain unchanged.

Repository verification showed that `scripts/check_p0_ci_closure.py` is the
exact structural authority for both workflows and separately retains the old
approved Node scalar. After capturing a focused checker RED against the
otherwise-valid Node 22 workflows, P0-12R5 must also change only that one
approved checker scalar from `20` to `22`. Do not broaden its parser,
allowlist, accepted step shape, workflow semantics, or error behavior.

Run the focused and affected repository-shape, D0, and P0-closure tests; all
four dashboard gates under `TMPDIR=/tmp TMP=/tmp TEMP=/tmp`; secret, D0,
baseline, closure, contract, and workflow-lint gates; and an independent
byte-level comparison proving the workflows differ from the exact base only
at the two approved scalar values. Write the local task report and leave a
clean committed worktree. This packet does not authorize push, remote update,
hosted rerun or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler action, or broker/exchange
access.

---

## P0-12R6 correction — Nanoid production-audit remediation

Push-triggered Foundation run `31822937716`, attempt 1, at exact reviewed SHA
`b3784f3e408e033cd8b3b6e15b45ea7845575a88` passed the legacy backend with
507 passed and two skipped, then passed all 171 dashboard tests, typecheck,
lint, and the production build. It failed only at
`make audit-dependencies-production`: the dashboard's reviewed `nanoid`
override and resolved lock entry remain at `3.3.17`, while
`GHSA-2v37-7h3g-55p8` requires `3.3.18` or newer. No artifact was uploaded and
the known failing SHA must not be rerun.

P0-12R6 may change only the exact dashboard `nanoid` override from `3.3.17`
to the smallest fixed version `3.3.18`, regenerate the dashboard lock with npm,
and add one exact regression binding the override and the single resolved lock
entry. Capture both the focused regression RED and the unchanged production
dependency-audit RED before the package-manager change. Do not hand-edit the
lock, run a broad `npm audit fix`, change the audit threshold, add a skip, or
update any unrelated direct, development, transitive, or overridden package.

Require npm-owned package and lock regeneration, `npm ci`, production and full
npm audit, all dashboard tests, typecheck, lint, and build, plus the affected
repository-shape, D0, P0 closure, secret, baseline, contract, and workflow-lint
gates. Prove the package and lock diff contains only the authorized nanoid
resolution and integrity metadata. Write the local report and leave the
committed worktree clean for independent review.

This packet does not authorize push, remote update, hosted rerun or dispatch,
P0-12 continuation, P0-13, promotion, production/live/runtime/DB/service/
scheduler action, or broker/exchange access.

---

## P0-12R7 correction — Hosted remainder fixture and derived-inventory drift

Push-triggered Foundation run `31824638303`, attempt 1, at exact reviewed SHA
`db4bc66829cb12d8c6f17bf9820848a2bf8514c4` passed D0, secret hygiene, the
legacy backend with 507 passed and two skipped, all 171 dashboard tests,
typecheck, lint, build, and dependency audit. The bounded portable root
remainder then reported 16 failed, 6025 passed, and 281 skipped before the
aggregate stopped fail-closed at `EXACT_EXECUTION_NONPASS`. No qualification
artifact was uploaded and the known failing SHA must not be rerun.

The 16 failures reduce to seven stale or environment-coupled test authorities.
P0-12R7 may update only the canonical dashboard manifest and lock digests in
the derived Nautilus baseline-inventory JSON plus the four affected governance
test modules. Make both native `AVAILABLE` tests use deterministic retained
probe fixtures rather than real Bubblewrap/unshare discovery; construct the
legacy `0775` exception against an exact temporary component root while
retaining the arbitrary-root negative; pass an explicit unavailable
`native_probe_factory` into the closure proof instead of monkeypatching the
obsolete `_native_preflight`; and construct an existing nonancestor commit in
a temporary Git authority instead of assuming `git rev-list --all --not HEAD`
is nonempty. Production native `BROKEN` and external path checks remain
fail-closed and unchanged.

Update the hosted-route literal and the test observer's approved recursive-Make
tuple to the exact current `ci-portable-private` route, including
`check-portable-defect-closure`, `check-p0-baseline`, and
`check-p0-ci-closure`. This is test authority only: do not broaden the observer
parser or approved grammar. Capture the exact 16-node RED before these test or
derived-data edits, then require the exact 16 GREEN, the complete affected
files, a fresh standalone-clone affected/root-remainder packet, baseline,
closure, contract, workflow-lint, and diff/clean-state checks.

No script, Makefile, workflow, dependency, lockfile, source inventory, matrix,
or qualification artifact may change. This packet does not authorize push,
remote update, hosted rerun or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler action, or broker/exchange access.

---

## P0-12R8 correction — Diagnostic-only publication on root-remainder non-pass

Push-triggered Foundation run `31827924223`, attempt 1, at exact reviewed SHA
`5b98ced41aa6bcdc7946a158a4f392f55d519847` collected 6,384 root tests after
29 governed deselections, reserved 62 governed nodes, and executed the exact
6,322-node remainder as 6,041 passed and 281 skipped. The topology correctly
stopped fail-closed at `EXACT_EXECUTION_NONPASS`; because the final artifact
firewall is later in the successful route, the strict raw failure diagnostic
was stranded and no artifact was uploaded. The known failing SHA must not be
rerun.

P0-12R8 may add one early, diagnostic-only artifact-firewall action for this
exact root-remainder failure state. It must validate the sealed Foundation
context and reservation, locked inventory, baseline, exact remainder, custody
binding, and the existing strict failure diagnostic before projecting only
canonical provenance, counts, exact skipped node IDs, closed reason classes,
and commitment/policy digests. It must use a distinct non-acceptance schema,
contain no raw reason, secret, PASS aggregate, receipt, native, governance
acceptance, or policy relaxation, and reuse retained-descriptor, no-follow,
single-link, sealed-mode, atomic no-clobber, checksum, and final-validation
semantics from the existing firewall.

The outer `ci-portable` catch may invoke this action exactly once only when the
canonical failure diagnostic exists, and must always return the original
nonzero status. Successful publication and later governance-error routes must
remain unchanged. Capture a focused RED before implementation, then cover
valid publication plus malformed, stale, foreign, symlink, hardlink, mode,
owner, replacement, coexistence, and destination no-clobber attacks; prove raw
reasons are absent, the original exit is preserved, and no acceptance or PASS
artifact is emitted. Require focused and full affected tests, a standalone
affected/root-remainder packet, static closure/contract/workflow gates, exact
diff review, ignored report, and a clean worktree for independent review.

This packet does not authorize skip or native-policy changes, push, remote
update, hosted rerun or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler action, or broker/exchange access.

---

## P0-12R9 correction — Generated broad-handler line-drift refresh

Push-triggered Foundation run `31832512245`, attempt 1, at exact reviewed SHA
`88f0dc0cb6819e9972778d23ba831755f9766cf2` failed before topology after the
legacy backend reported 506 passed, two skipped, and one failed. The sole
failure was
`tests/test_live_execution_policy.py::test_machine_readable_broad_handler_inventory_has_exact_tracked_coverage`;
the common gate therefore produced no artifact, as expected, and the known
failing SHA must not be rerun.

The canonical generator observes the same 478 broad handlers documented by
the inventory, but P0-12R8 shifted two existing
`scripts/check_artifact_firewall.py` handler line numbers. The exact delta is
two added and two stale rows, all `TOOLING_MIGRATION` with `RAISE` disposition:
`Exception` moves from line 458 to 476 and `BaseException` moves from line 499
to 517. No broad-handler form, classification, disposition, or production
path changed.

P0-12R9 may refresh only the marked machine-readable inventory block by
running `uv run python scripts/check_broad_handler_inventory.py --write`.
Capture the exact node RED first, inspect every added and removed row, then
prove the generator changed only that marked block. Generator logic,
production and test source, Makefile, workflows, dependencies, locks, source
inventories, matrix state, and qualification artifacts remain unchanged.
Require the exact node, canonical inventory check, full legacy policy module,
P0-12R8 focused packet, baseline, closure, contracts, source/security audits,
workflow lint, and exact diff/clean-state gates before independent review.

This packet does not authorize push, remote update, hosted rerun or dispatch,
P0-12 continuation, P0-13, promotion, production/live/runtime/DB/service/
scheduler action, or broker/exchange access.

---

## P0-12R10 correction — Private runner-temp final artifact lineage

Hosted Foundation run `31833372257` reached the intended root-remainder
non-pass with exactly 6,063 passed and 281 skipped nodes. The P0-12R8 catch
invoked the failure-only publisher exactly once, but publication failed closed
with `ARTIFACT_FIREWALL_REJECTED LAYOUT`. Reconstructing the exact partial
evidence shape and counts proves those retained raw bytes publish successfully
under a private temporary lineage. The same validated bytes fail at the former
repository-local destination when an ancestor above the checkout is mode 0775:
the absolute-lineage validator reports `artifact path ancestor is writable`
before creating `runtime/state/ci-portable`.

Ignoring writable checkout ancestors is not safe: after the publisher closes
its retained descriptors, a same-user namespace swap could make the later
upload action re-resolve foreign bytes. P0-12R10 therefore keeps
`_validate_lineage` fully strict and moves the final artifact for all three
portable routes (success, governed error, and early root-remainder failure) to
one deterministic private `RUNNER_TEMP` path bound to the exact GitHub run ID
and attempt. The outer `ci-portable` wrapper must overwrite any inherited
artifact-path value with that closed derivation, export it to recursive Make,
reject pre-existing destination occupancy, and pass the same exact destination
to every publisher. The Foundation workflow must upload only that exact
runner-temp path, including hidden files; it must never upload the raw evidence
root or re-resolve a repository-local artifact.

Strict TDD must first prove the current Make/workflow contract still targets
the checkout and demonstrate an upload-style reread observing foreign bytes
after a repository-ancestor namespace swap. GREEN requires the exact private
run/attempt-bound path across the success, governed-error, and failure-only
routes, while preserving single invocation, original nonzero status, no-clobber,
sealed final modes, and `if: always()` upload behavior. Malformed run/attempt,
inherited destination injection, stale occupancy, symlink/hardlink/unsafe mode
or owner, replacement, coexistence, raw-root upload, and workflow/Make path
drift must fail closed. Raw Foundation context, reservation, baseline,
remainder, custody, inventory, closure, strict diagnostic validation,
source-tree binding, secret-safe projection, skip/native acceptance, and PASS
semantics remain unchanged.

Require full P0-12R8 adversarial, artifact-firewall, topology, Make-route,
repository-shape, D0, baseline, closure, P0 contracts, source/security audit,
actionlint, and exact diff gates before independent review. Dependencies,
locks, remote update, hosted rerun or dispatch, P0-12 continuation, P0-13,
promotion, production/live/runtime/DB/service/scheduler mutation,
broker/provider access, and live trading remain out of scope.

---

## P0-12R10 fix round 1 — Recursive Make command-variable confinement

Independent review of implementation `79f0d8956c85179cd0f055b37169a05addf3cf96`
found that the outer shell's canonical exported
`PORTABLE_CI_ARTIFACT_ROOT` is not authoritative inside recursive GNU Make.
A command-line assignment, an inherited `MAKEFLAGS` assignment, or an explicit
`MAKEOVERRIDES` assignment retains command-variable precedence in
`ci-portable-private`; both Make expansion and the recipe shell therefore see
an attacker-selected path instead of the run/attempt-bound `RUNNER_TEMP` path.

P0-12R10 fix round 1 must capture the real recursive-GNU-Make RED for all three
injection forms before implementation. GREEN must establish the canonical
private destination as the exact child-Make authority for success,
governance-error, and early-failure publication without broadening the closed
recursive-Make grammar. The implementation may alter only the outer Make
boundary, its exact closure checker mirror, and focused regression tests.
Artifact schemas, publisher validation, strict lineage, skip/native policy,
PASS semantics, workflow upload path, dependencies, and locks remain
unchanged. The caught failure status must remain original and all three routes
must remain destination-identical.

Require the focused RED/GREEN, full failure-output and P0 closure modules,
affected topology/Make-route/static gates, a fresh standalone affected packet,
exact diff review, corrected full plan-commit identifiers in the ignored
report, and a clean committed worktree before another independent review. No
push, hosted rerun or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler mutation, broker/provider access,
or live trading is authorized.

---

## P0-12R11 correction — Private publication parent below runner temp

Hosted Foundation run `31839312983` passed the common gate, collected 6,417
remainder candidates after 29 deselections from 6,446 root nodes, reserved 62
governed nodes, and executed the exact 6,355-node remainder as 6,074 passed
plus 281 skipped. The failure-only publisher was invoked, then failed closed as
`ARTIFACT_FIREWALL_REJECTED LAYOUT`; the exact runner-temp upload path remained
empty. The known failing SHA must not be rerun.

The R10 final destination is a direct child of `RUNNER_TEMP`. The artifact
firewall correctly requires `destination.parent` itself to be current-user
owned, so a trusted root-owned runner-temp directory cannot be the terminal
publication parent even though a private `mktemp` child under it validates.
P0-12R11 may correct only that destination boundary: atomically create one
deterministic, run/attempt-bound, current-user-owned mode-0700 publication
parent below `RUNNER_TEMP`, fail on any preoccupancy, and publish to one absent
fixed child beneath it. The workflow must upload only that exact child path.

Strict TDD must first reproduce the root-owned sticky runner-temp case and
prove directory, symlink, file, unsafe-mode, foreign-owner, and replacement
attempts fail closed before unsafe publication. GREEN must retain exact
run/attempt binding, R10 command-variable confinement, fully strict lineage,
single destinations across success, governed-error, and early-failure routes,
original caught status, no raw upload, no clobber, and sealed final artifact
semantics. Update only the Make wrapper, its exact closure authority, the
Foundation upload scalar, and affected exact tests; do not alter publisher
schemas, skip/native policy, PASS semantics, dependencies, or locks.

Require focused and full affected tests, a fresh standalone affected packet,
P0 baseline/closure/contracts/source/security/inventory/actionlint/diff gates,
an ignored report, and a clean committed worktree before independent review.
No push, hosted rerun or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler mutation, broker/provider access,
or live trading is authorized.

---

## P0-12R11 review clarification — Architecture-A workflow handoff

Review of `0f0a467b9b013210dae3d53743d1b55d6e8a9ac5` observed that an arbitrary
same-runner-UID process could rename the sealed publication parent after the
publisher returns and place foreign bytes at the workflow upload path. This is
a real property of pathname re-resolution, but that persistent same-UID forger
is explicitly outside the approved Architecture-A threat boundary rather than
a defect to suppress with another same-UID pathname check.

The accepted P0 boundary trusts root-owned sticky `RUNNER_TEMP` against
ordinary cross-user and workspace mutations, and the current-user-owned
mode-0700 child against other users. Retained descriptors, identity postchecks,
and atomic no-clobber publication protect the transaction through its
linearization point. After publication returns, the exclusive workflow handoff
assumes no arbitrary same-job-UID forger. A stronger same-UID keyed-provenance
claim requires privilege separation or remote attestation and is outside P0.

Artifacts recovered from the workflow are untrusted inputs. A consumer must
run the exact published-evidence validator with expected head, source-tree,
and semantic bindings before acceptance; upload success alone is never
authority. Add a narrow characterization only because existing tests separately
covered clean download round-trip and malformed published trees but did not
mutate a downloaded copy. No publisher, Make, workflow, path, schema, policy,
dependency, or lock change is authorized by this clarification.

Require the focused downloaded-artifact characterization, P0 closure,
contracts, source/security, inventory, actionlint, and diff checks before
independent re-review. No push, hosted rerun or dispatch, P0-12 continuation,
P0-13, promotion, production/live/runtime/DB/service/scheduler mutation,
broker/provider access, or live trading is authorized.

---

## P0-12R12 correction — Closed failure-publication stage diagnostic

Hosted Foundation run `31842890035` passed the common gate, collected 6,427
remainder candidates after 29 deselections from 6,456 root nodes, reserved 62
governed nodes, and executed the exact 6,365-node remainder as 6,084 passed
plus 281 skipped. The failure-only publisher was invoked and failed closed as
`ARTIFACT_FIREWALL_REJECTED LAYOUT`; the exact private-child upload path
remained empty. The known failing SHA must not be rerun.

Local diagnosis must use the real source-tree identity and separately exercise
raw binding, projection construction, source-tree binding, and final
publication through a trusted root-owned sticky `/tmp`, a newly created
current-user-owned mode-0700 parent, and an absent artifact child. It must also
exercise a hosted-shaped diagnostic projection with exactly 6,365 remainder
nodes, 6,084 passed nodes, and 281 skipped observations. If those locally
knowable stages all pass and the hosted-only failure cannot be reproduced,
P0-12R12 may add one closed, nonsecret failure-stage classification to the
failure-only publisher.

The only public stage values are `RAW_BINDING`, `PROJECTION`, `SOURCE_TREE`,
and `PUBLICATION`. Classification must preserve the existing rejection code
and category while disclosing no exception message, raw path, raw reason,
secret-shaped value, digest, policy content, or acceptance/PASS meaning. Add
strict tests that force each boundary, prove exact closed output and original
CLI status, and prove hostile underlying messages cannot cross the boundary.
Do not change raw validation, projection schema, skip acceptance, native
policy, PASS semantics, Make routing, workflows, dependencies, or locks.

Require focused and full artifact-firewall tests, broad-handler inventory,
P0 baseline/closure/contracts/source/security/actionlint/diff gates, an ignored
report, and a clean committed worktree before independent review and one new
hosted attempt on the reviewed SHA. This packet does not itself authorize a
push, hosted run or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler mutation, broker/provider access,
or live trading.

---

## P0-12R12 fix round 1 — Drop retained hostile exception context

Independent review of `cfb108a41eeb80800fd4be8fa42e7ac42e10b3a8`
found that each sanitized `FailurePublicationError` is constructed and raised
inside the active underlying exception handler. Python therefore retains the
hostile exception in `__context__`; `raise ... from None` suppresses normal
rendering but does not remove that reachable message, raw path, digest, or raw
reason authority.

Capture strict RED at the real raw-binding, projection, source-tree, and
publication boundaries. Exercise each boundary's exact supported
`FirewallError`, `OSError`, and `ValueError` paths, plus the source-tree
`subprocess.SubprocessError` path. The public wrapper must have both
`__context__` and `__cause__` equal to `None`, contain no hostile underlying
value in its fields, preserve the exact safe code/category/stage projection,
and retain CLI exit 2 and exact closed output.

P0-12R12 fix round 1 may change only the control flow needed to leave the
active handler before raising the already-sanitized wrapper, its focused tests,
and generator-owned broad-handler inventory line rows if they shift. Do not
broaden any catch, add a raw exception field, weaken redaction, or change raw
validation, schemas, skip/native/PASS policy, Make, workflows, dependencies,
or locks.

Require focused/full affected tests, a fresh standalone affected packet, P0
baseline/closure/contracts/source/security/inventory/actionlint/diff gates, an
ignored report update, a clean commit, and independent re-review. No push,
hosted rerun or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler mutation, broker/provider access,
or live trading is authorized.

---

## P0-12R13 correction — Closed source-tree failure diagnostics

Hosted Foundation run `31846573511` collected 6,444 root candidates after 29
deselections from 6,473 nodes and executed the exact remainder as 6,101 passed
plus 281 skipped. The expected `EXACT_EXECUTION_NONPASS` failure publisher then
reported `ARTIFACT_FIREWALL_REJECTED LAYOUT SOURCE_TREE`; the exact upload path
remained empty. Local standalone execution with the same collection, plus the
common and dashboard subsets, leaves tracked Git clean. The hosted-only source
tree cause is therefore not recoverable from the current closed stage.

P0-12R13 may refine only `SOURCE_TREE` with one exact closed command substage
and one closed reason. Public output is limited to
`SOURCE_TREE <substage> <reason>`. The allowed substages are `DIFF_INDEX` for
the existing index-versus-head command, `DIFF_FILES` for the existing
worktree-versus-index command, `HEAD_BINDING` for the checked `rev-parse HEAD`
binding, and `TREE_ENUMERATION` for the checked `ls-tree` identity input. The
allowed reasons distinguish `DRIFT`, `MISMATCH`, `COMMAND_FAILURE`,
`SPAWN_FAILURE`, and `OUTPUT_FAILURE` only where that reason can arise for the
named command. No filename, path, argv, cwd, return code, output, hash, raw
message, or underlying exception may be retained or printed.

Strict TDD must pressure the real `_source_tree_identity` command boundaries:
diff return-code drift versus command failure, checked head mismatch/command/
output failure, tree-enumeration command failure, and `OSError` or
`subprocess.SubprocessError` at every exact command. It must also prove the
failure publisher preserves the safe code/category/stage, adds only the exact
closed substage/reason, retains no cause or context, emits exact CLI exit 2,
and creates no artifact.

Keep the existing Git commands and clean-tree/head/tree invariants, R12
exception-context closure, failure projection schemas, skip/native/PASS
policy, Make routing, workflows, dependencies, and locks unchanged. Refresh
only generator-owned broad-handler inventory rows if exact line movement
requires it. Require focused/full affected tests, a fresh standalone affected
packet, P0 baseline/closure/contracts/source/security/inventory/actionlint/diff
gates, an ignored report, a clean commit, and independent review. No push,
hosted rerun or dispatch, P0-12 continuation, P0-13, promotion,
production/live/runtime/DB/service/scheduler mutation, broker/provider access,
or live trading is authorized.

---

## P0-12R14 correction — Content-authoritative source-tree checks

Hosted Foundation run `31849930552` collected 6,466 root candidates after the
exact 29 deselections from 6,495 nodes and executed the exact remainder as
6,123 passed plus 281 skipped. The expected `EXACT_EXECUTION_NONPASS` failure
publisher then reported `SOURCE_TREE DIFF_INDEX DRIFT`; the exact upload path
remained empty.

Safe-lineage standalone evidence isolates this as a Git stat-cache false
positive rather than tracked source mutation. Common execution left
`diff-index` at status 0. After the root remainder, both legacy stat-sensitive
checks returned status 1 while status porcelain plus cached and worktree diffs
were empty; a status refresh returned both legacy checks to status 0. A clean
clone reproduces the boundary with only an mtime touch: legacy `diff-index` and
`diff-files` return 1 while both content-authoritative comparisons return 0.

P0-12R14 may replace only the two existing stat-sensitive command argv with
these exact read-only content checks:

```text
git diff-index --cached --quiet HEAD --
git diff --quiet --no-ext-diff --no-textconv --
```

The first remains the `DIFF_INDEX` index-versus-HEAD invariant. The second
remains the `DIFF_FILES` worktree-versus-index invariant. The later checked
`rev-parse HEAD` binding and `ls-tree` identity remain unchanged. Do not invoke
`update-index`, refresh the index, mutate the worktree, or accept a status-only
cleanliness claim.

Strict TDD must exercise real temporary Git repositories and prove an
mtime-only touch is accepted without changing the returned tree identity,
while staged content, unstaged content, staged or unstaged executable-mode
changes, and other actual tracked drift still fail at the exact applicable
closed substage. Existing hostile command/spawn/output cases must bind the new
exact argv and continue to retain no raw value or exception context.

Change only the two command argv and focused tests. Refresh generator-owned
broad-handler inventory rows only if canonical generation proves exact line
movement. Do not weaken source cleanliness, change classification schemas,
skip/native/PASS policy, Make routing, workflows, dependencies, or locks.
Require focused/full affected tests, a fresh standalone affected packet, P0
baseline/closure/contracts/source/security/inventory/actionlint/diff gates, an
ignored report, a clean commit, and independent review. No push, hosted rerun
or dispatch, P0-12 continuation, P0-13, promotion, production/live/runtime/DB/
service/scheduler mutation, broker/provider access, or live trading is
authorized.

---

## P0-12R14 fix round 1 — Isolate worktree comparison from the real index

Independent review of `4b82e6476248813eb2d7ea0a26be9a9e8388a6d4`
proved that `git diff --quiet --no-ext-diff --no-textconv --` may refresh and
rewrite the repository's real index even when content is clean. A deterministic
future-mtime fixture changed the index bytes, inode, nanosecond mtime/ctime, and
stored stat-cache entry, and changed the legacy stale signals from 1/1 to 0/0.
`GIT_OPTIONAL_LOCKS=0` alone does not prevent this write and is prohibited as a
standalone fix.

Preserve the two source-cleanliness invariants without permitting any real-index
mutation. First run a non-mutating cached comparison of the real index against
the expected Foundation head SHA, with caller-provided `GIT_INDEX_FILE`
authority removed. Only after that succeeds, create one task-private
mode-0700 temporary directory under root-owned sticky `/tmp`, initialize an
alternate index from the same expected head SHA using `git read-tree`, and run
the exact worktree content comparison with `GIT_INDEX_FILE` bound to that
alternate index. `GIT_OPTIONAL_LOCKS=0` may accompany these commands as defense
in depth but is not the isolation authority. Remove the private directory
synchronously before the existing checked head binding and tree enumeration.

Strict RED must prove an mtime-only source check preserves the real index bytes,
stable stat identity, nanosecond mtime/ctime, and exact `git ls-files --debug`
stat-cache projection, and leaves both legacy stale signals at 1/1 after the
check. It must retain exact staged/unstaged content and executable-mode drift
classification, bind the expected head SHA and alternate-index environment,
reject hostile caller `GIT_INDEX_FILE`/`GIT_OPTIONAL_LOCKS` overrides, classify
read-tree/diff command and spawn failures without raw disclosure, and prove the
task-private directory is removed on success and failure.

Change only the minimum source-tree command orchestration and focused tests.
Keep the R13 closed diagnostic schema, source-cleanliness policy, later head and
tree bindings, skip/native/PASS policy, Make, workflows, dependencies, and locks
unchanged. Refresh generator-owned broad-handler inventory rows only if exact
line movement requires it. Require focused/full affected tests, a fresh
standalone affected packet, P0 baseline/closure/contracts/source/security/
inventory/actionlint/diff gates, an ignored report update, a clean fix commit,
and independent re-review. No push, hosted rerun or dispatch, P0-12
continuation, P0-13, production/runtime, broker/provider, or live action is
authorized.

---

# Task P0-13 — Fast-forward promotion and post-promotion proof

**Owner:** Operator.  
**Codex role:** Read-only verification unless separately authorized for the exact Git action.

### Preconditions

```text
[ ] P0 final reviewer PASS
[ ] Two green workflow attempts on same SHA
[ ] Semantic digests equal
[ ] Candidate is descendant of origin/main
[ ] Candidate behind main by zero commits
[ ] Worktree/index clean
[ ] Explicit operator authorization to update main
```

### Step 1 — Reverify immediately before promotion

```bash
git fetch --prune origin main p0/canonical-baseline-ci-closure

git merge-base --is-ancestor \
  origin/main \
  origin/p0/canonical-baseline-ci-closure

test "$(git rev-list --count \
  origin/p0/canonical-baseline-ci-closure..origin/main)" -eq 0
```

### Step 2 — Promote by fast-forward only

Preferred Git operation after authorization:

```bash
git push origin \
  origin/p0/canonical-baseline-ci-closure:refs/heads/main
```

Do not:

- force-push;
- create a merge commit;
- squash away qualification history;
- delete the candidate immediately.

### Step 3 — Verify remote head

```bash
git fetch origin main
git rev-parse origin/main
```

Expected:

```text
exact qualified SHA
```

### Step 4 — Require post-promotion CI

The workflow on `main` must pass with the same semantic digest as the qualified candidate.

### Step 5 — Update status documentation only if needed

No document may claim:

```text
host authority qualified
production deployed
Nautilus real runtime activated
live trading enabled
```

unless separately proven.

### Step 6 — Retain rollback provenance

Keep:

```text
codex/phase1-terra-autopilot-19627785c140
p0/canonical-baseline-ci-closure
```

until the post-promotion run and source audit pass.

---

## 6. Required final test matrix

| Gate | Required portable result | Required host result |
|---|---|---|
| `make check-p0-baseline` | PASS | PASS |
| `make check-p0-ci-closure` | PASS | PASS |
| Validation-date tests | PASS | PASS |
| Portable source lane | PASS | PASS |
| Native Bubblewrap lane | PASS or UNAVAILABLE | PASS |
| Native user-namespace lane | PASS or UNAVAILABLE | PASS |
| Phase 3B corpus lane | PASS or UNAVAILABLE | PASS |
| Legacy UV authority lane | PASS or UNAVAILABLE | PASS |
| Artifact firewall | PASS | PASS |
| Dashboard typecheck/build | PASS | PASS |
| Canonical portable audit | PASS | PASS |
| Host authority audit | not required | PASS |
| Production mutation | FORBIDDEN | FORBIDDEN |
| Live trading | UNAVAILABLE | UNAVAILABLE |

---

## 7. Commit sequence

Recommended commits:

```text
1. docs(p0): add canonical baseline and CI closure plan
2. feat(p0): pin canonical candidate baseline
3. test(ci): reproduce sealed validation-date authority mismatch
4. fix(ci): derive topology date only from sealed context
5. fix(test): decouple portable sealed-uv proofs from bwrap
6. fix(test): bind portable runtime identity to current process
7. governance: close portable hosted-failure inventory
8. feat(ci): seal native capability receipts
9. feat(ci): seal external authority receipts
10. refactor(ci): isolate portable and host authority gates
11. feat(ci): seal deterministic portable evidence
12. docs(ci): close executable P0 qualification matrix
```

Do not combine all P0 work into one commit. Each commit must have focused test evidence and be independently reviewable.

---

## 8. Hermes/Codex operating protocol

### Orchestrator

Hermes Agent:

```text
model: gpt-5.6-sol
reasoning: high
```

Responsibilities:

- enforce task order;
- create isolated worktrees;
- prevent overlapping edits;
- collect test evidence;
- reject broad skips;
- retain per-task review packets;
- stop at explicit approval boundaries.

### Worker routing

| Tasks | Worker |
|---|---|
| P0-00, P0-01, P0-09, P0-11 | Terra medium |
| P0-02, P0-03 | Sol xhigh |
| P0-04 mechanical fixture extraction | Terra medium |
| P0-04 security review | Sol xhigh |
| P0-05 through P0-08 | Sol high/xhigh |
| P0-10 | Sol high, reviewer xhigh |
| P0-12 final review | Sol xhigh fresh context |

### Per-task worker output

Every worker must return:

```text
TASK:
STATUS: COMPLETE | PARTIAL | BLOCKED

BASE SHA:
HEAD SHA:

FILES CHANGED:
- ...

TESTS ADDED:
- ...

COMMANDS RUN:
- command
  exit code
  concise result

INVARIANTS PRESERVED:
- ...

UNRESOLVED:
- ...

DIFF REVIEW NOTES:
- ...

NEXT SAFE TASK:
- ...
```

No worker may push, merge, deploy, migrate, restart production, access a broker/exchange, or enable live trading without separate explicit authorization.

---

## 9. P0 completion checklist

```text
[ ] Candidate ancestry verified
[ ] Machine-readable baseline manifest PASS
[ ] Current date-context failure reproduced
[ ] Sealed context is sole topology date authority
[ ] CLI date override rejected
[ ] Environment date override rejected
[ ] 27 sealed-UV fixture defects fixed
[ ] 3 semantic UID/GID fixture defects fixed
[ ] 2 fakeroot identity fixture defects fixed
[ ] Unresolved portable defects = 0
[ ] Native unresolved cases = 24
[ ] External unresolved cases = 6
[ ] Native receipts fail closed
[ ] External receipts fail closed
[ ] `ci` cannot reach host authority
[ ] Default workflow is read-only and portable
[ ] Host workflow is manual/protected
[ ] Artifact manifest and SHA256SUMS validate
[ ] Artifact firewall PASS
[ ] Closure matrix PASS
[ ] Clean-clone portable run 1 PASS
[ ] Clean-clone portable run 2 PASS
[ ] Semantic result digests match
[ ] Final adversarial reviewer PASS
[ ] Fast-forward eligibility YES
[ ] Explicit promotion authorization received
[ ] `main` post-promotion CI PASS
[ ] Production/live authority remains unavailable
```

---

## 10. P0 exit verdict

Use exactly one of:

```text
P0_SOURCE_COMPLETE
```

Meaning:

- portable canonical source is qualified;
- main may be fast-forwarded after authorization;
- host capabilities/authorities may still be unavailable;
- production and live remain unavailable.

```text
P0_HOST_QUALIFIED
```

Meaning:

- `P0_SOURCE_COMPLETE`;
- all native and external lanes ran on the approved host and passed;
- still does not authorize production activation or live trading.

```text
P0_NOT_READY
```

Meaning:

- any portable defect, governance mismatch, evidence defect, divergence, or final-review blocker remains.

For the current workstream, the required target is:

```text
P0_SOURCE_COMPLETE
```

`P0_HOST_QUALIFIED` may be obtained separately when the approved host inputs are available.

---

## 11. Approved implementation decisions

The operator approved the following corrections on **2026-08-13** before
implementation began:

1. The source-controlled baseline manifest keeps `qualified_sha` as `null`.
   A commit cannot contain its own Git object ID without creating a different
   commit. Exact qualification authority therefore belongs to a sealed
   runtime/CI qualification receipt whose head SHA must equal the checked-out
   `HEAD`.
2. `check_p0_ci_closure.py` must validate that sealed receipt against the
   current `HEAD` when closure is marked complete; it must not require a
   self-referential SHA in the source manifest.
3. Closure evidence that names a `fix_commit` uses a two-commit sequence:
   first commit the code and tests, then commit the evidence that references
   that already-existing fix commit. A closure row may not claim the same
   commit that contains the row.

These decisions replace conflicting `qualified_sha` and same-commit closure
instructions elsewhere in this plan. All other requirements remain binding.

---

## Evidence-corrected execution

This correction was approved by the operator on 2026-08-13 and supersedes
conflicting P0-02/P0-03 text in the committed plan.

### Verified facts

- GitHub Actions run `31724355034` is bound to candidate head
  `417c17452ea31f0ca8c8e9893ac3c03a3a90a7c1`.
- `POLICY_DATE_CONTEXT_MISMATCH` appeared while the existing negative test
  suite was running. The run subsequently reported `5681 passed, 281 skipped`.
- The blocking topology result was
  `UNSAFE_RAW_REASON_NONACCEPTANCE`, followed by failure of
  `test-portable-root-remainder`, `ci-portable-topology`, and `ci-portable`.
- The real topology governance test path is
  `tests/governance/test_t_g03_capability_topology.py`.
- The real governance CLI is launched as
  `python -m scripts.check_test_governance` and uses `--allowlist`,
  `--inventory`, `--topology-evidence-root`, and
  `--foundation-context-path`.
- The locked inventory is
  `tests/fixtures/t-g03a-hosted-failure-inventory.tsv`.
- Foundation context schema `t-g03a-foundation-context/v1` contains run ID,
  head SHA, validation date, and self-hash. It does not contain an inventory
  digest. Do not fabricate that field in P0-02.

### Revised P0-02 - Verify and instrument the real date-authority boundary

Goal: prove the current clean sealed-context path succeeds through the actual
production subprocess contract, keep CLI/environment overrides fail-closed,
and add redacted source diagnostics. Do not manufacture a clean-context
failure and do not leave an intentional failing test.

Required work:

1. Append this evidence correction to the tracked implementation plan under
   an `Evidence-corrected execution` section and commit that documentation
   separately as `docs(p0): correct CI failure evidence`.
2. Add a subprocess-level regression using the real module entry point and
   real option names. With a valid temporary v1 foundation context and the
   production topology inputs, an environment without
   `FOUNDATION_VALIDATION_DATE`, and no `--today`, it must exit 0.
3. Add subprocess-level negative coverage proving `--today` and
   `FOUNDATION_VALIDATION_DATE` each exit non-zero with
   `POLICY_DATE_CONTEXT_MISMATCH`.
4. On a date-context error, the JSON error artifact may add only:
   `cli_today_present`, `environment_override_present`,
   `sealed_context_present`, and `sealed_context_valid`. Values are booleans;
   never include the override value, arbitrary environment data, credentials,
   or raw external paths. `sealed_context_valid` must mean the context was
   actually validated, not merely present.
5. Preserve v1 context validation order and semantics already implemented:
   strict schema/canonical bytes, self-hash, run ID, head SHA, then date use.
   Do not add an inventory digest or redesign receipts in this packet.
6. Follow TDD. RED must demonstrate a missing subprocess/diagnostic behavior,
   not a fabricated production defect. GREEN requires all focused validation
   date and topology tests to pass.
7. Commit implementation as
   `test(ci): verify sealed validation-date authority`.

#### P0-02 review correction: explicit context preflight

The full topology-governance command is downstream-coupled to root topology
receipts plus legacy and dashboard execution, so it cannot isolate the P0-02
date-authority boundary. P0-02 therefore uses the explicit production CLI
`--topology-context-preflight` together with `--topology-audit` and explicit
production allowlist, locked inventory, topology evidence root, and foundation
context inputs. This preflight validates the active run/head-bound strict v1
context and the production allowlist policy at its sealed date, then exits
without executing component suites or claiming topology acceptance. The full
topology command remains unchanged for the downstream closure packet.

### Revised P0-03 - Close the actual hosted topology blocker

After P0-02 review passes, reproduce
`UNSAFE_RAW_REASON_NONACCEPTANCE` through the exact portable topology lane,
write a failing regression against the real unsafe-reason nonacceptance path,
implement the minimal fail-closed correction, and verify the focused failure
diagnostic/topology suites plus the production Make lane. Do not weaken raw
reason rejection, receipt custody, or redaction.

### Later tasks

Before starting P0-04 or later, revalidate every named path, inventory count,
classification, and command against the candidate. Record corrections in the
tracked plan rather than creating nonexistent files or synthetic failures.

### Evidence-corrected P0-04

P0-04 qualifies the existing sealed-UV fixture repair instead of repeating
source work. The locked inventory still lists 27
`PORTABLE_SOURCE_DEFECT` / `SRC-SEALEDUV-BWRAP-PREFLIGHT` nodes, while the
candidate already contains commit `1b1b47a4c51e106e3f2aab96613a80886422ccba`
(`test: repair portable Phase 1 fixtures`). That change supplies a private
synthetic sandbox only for policy-only proofs and retains real Bubblewrap for
native execution/isolation proofs. At the reviewed P0-03 head, the complete
sealed-UV test module collects 57 nodes and passes 57/57 locally.

The unresolved inventory is consequently historical with respect to candidate
source. P0-04 must run the exact 27 locked nodes and review the security and
test-fidelity invariants, but it must not duplicate the existing source change
or update unresolved/closure TSVs. P0-06 alone owns the atomic 32-row closure
schema and inventory mutation.

### Evidence-corrected P0-05

P0-05 qualifies the existing identity-fixture repair instead of repeating
source work. The locked inventory still lists three
`SRC-SEMANTIC-FIXTURE-IDENTITY` and two
`SRC-PHASE4B-FAKEROOT-IDENTITY` nodes, while candidate commit
`1b1b47a4c51e106e3f2aab96613a80886422ccba`
(`test: repair portable Phase 1 fixtures`) already updates the semantic and
fakeroot fixtures for current-process signed identity and distinct simulated
file ownership.

The unresolved inventory is historical with respect to candidate source.
P0-05 must run the exact five locked nodes and review signed-runtime versus
simulated-ownership behavior, adding source or test changes only for a
demonstrated RED. It must not update unresolved/closure TSVs. P0-06 alone owns
the atomic 32-row closure schema and inventory mutation.

### Evidence-corrected P0-05R

P0-05's exact five identity fixtures are qualified, but the full provision
module exposes four dynamic `unshare` fixture failures that must be repaired
before P0-06. The dynamic tests create their synthetic destination root below
default `/tmp`, so the unchanged release verifier reaches that fixture through
the group/world-writable mode-1777 `/tmp` ancestor and rejects it before the
tests reach their intended provisioning assertions. Running the same four
nodes with only the temporary-directory anchor moved beneath the current
user-owned mode-0700 `/run/user/<euid>` directory passes, confirming fixture
ancestry as the root cause and confirming that `unshare` is available.

P0-05R may change only the test fixture construction: the dynamic harness root
must use a safely created, current-user-owned private ancestor whose descendants
meet the production verifier's ownership and mode expectations. It must retain
real `unshare --user --map-root-user` execution, test-owned bounded cleanup, and
strict production verification. Production provisioning scripts, release
policy, inventories, dependencies, runtime state, and live gates remain
unchanged. The full provision module and the exact five P0-05 locked nodes must
pass before P0-06 may begin.

### Evidence-corrected P0-06

P0-06 owns one atomic governance migration from the locked 62-row hosted
failure inventory to a 30-row active unresolved inventory plus a separate
32-row portable-defect closure ledger. The active inventory retains exactly 24
native-capability and six external-authority nodes; no portable source defect
or `SRC-*` receipt code remains active. The closure ledger uses the reviewed
qualifying commits `871a10f3949c93fe1129c13de09b165b44af62a0` for the 27
sealed-UV rows and `46cd93582dc0fd7750a768d3e54cc3cfd7003510` for the five
identity rows, avoiding self-referential closure evidence.

Portable PASS requires one strict run/head/Foundation-context-bound closure
proof artifact produced by executing the exact 32 closed nodes through an
independently constructed argument vector. A closure digest or absence of
active `SRC-*` receipts is not proof. The portable-root remainder must subtract
both the 30 active unresolved nodes and the 32 closed nodes; accounting then
reconciles the remainder, the four active native/external receipt groups, and
the one closure proof so every collected node appears exactly once. Stale
`SRC-*` receipt or governance artifacts, an overlapping/reintroduced portable
row, missing or forged closure evidence, and native/external mapping drift all
fail closed.

### Evidence-corrected P0-07

P0-07 introduces the native-only strict receipt schema
`t-g03a-native-capability-receipt/v2`; the existing flat v1 schema remains
valid only for the two external-authority codes until P0-08. Native v2 binds
the exact 16 Bubblewrap or eight user-namespace nodes, run/head/Foundation
context/date, active-inventory digest, a closed probe command ID, retained
executable digest, hash-only probe outputs, exact execution counts, outcome,
and both completeness and receipt hashes. A stale native v1 receipt, forged
PASS, mixed mapping, noncanonical payload, or incomplete governance record
fails closed.

Native availability must be established with the real retained executable,
not version/help output or a synthetic replacement. Bubblewrap executes a
fixed inert command with the user, PID, and network namespace isolation needed
by the native group through the policy-bound `/usr/bin/bwrap`; user namespace
provisioning executes the fixed equivalent of
`/usr/bin/unshare --user --map-root-user true`. Absence or an exact reviewed
host namespace-policy denial may produce DEFERRED. A present-invalid identity,
unrecognized diagnostic, timeout, partial setup, output drift, replacement, or
test failure produces FAIL and can never publish PASS.

The executable identity remains retained from probe through exact test
execution and a named-versus-held postcheck. Native receipt and PASS-governance
artifacts use private regular no-clobber publication and retained-identity
reads. Portable aggregation may accept an explicit bound DEFERRED while
surfacing it; an explicit host-require-pass validator rejects DEFERRED, but P0-09
alone owns wiring that mode into a separate workflow. The standalone native
target must create the authoritative context, custody, reservation, and
portable-root baseline required to run the exact native groups once on either
an available or unavailable host.

### Evidence-corrected P0-07R

The first real standalone P0-07 native execution proved the Bubblewrap probe
available, selected the locked 16-node group exactly once, and then exposed a
test-fixture path defect in four CLI namespace nodes. All four failed before
their CLI assertions because the console script shebang names
`.venv/bin/python`, while the fixture created and mounted only the sibling
`.venv/bin/python3` target inside its otherwise closed Bubblewrap filesystem.
Bubblewrap therefore reported `execvp .../.venv/bin/trading-agent-nautilus: No
such file or directory`; this is fixture-path RED evidence, not evidence that
the native capability is unavailable.

P0-07R is a prerequisite fixture-only packet. It may add exactly the missing
`.venv/bin/python` mount-parent declaration and a read-only bind at that path,
using the same already validated `RESOLVED_INTERPRETER` bytes that the fixture
mounts at `.venv/bin/python3`. It must not change node selection, production
code, CLI arguments, assertions, namespace flags, network isolation, or the
four governed tests' intended behavior. The exact four nodes and the complete
CLI test module must pass before P0-07 resumes.

#### P0-07R fix round 1

The first fixture packet incorrectly expressed the required shebang target as
`Path(sys.executable)`. That passed when pytest itself was launched through the
`.venv/bin/python` shebang, but the exact governance runner is launched as
`python -m` and reports `.venv/bin/python3`. The real standalone lane therefore
still produced the same four fixture failures, with 12 of the 16 Bubblewrap
nodes passing before fail-closed termination.

The fixture target must instead be the fixed console-script shebang location
`CLI.parent / "python"`, independent of the interpreter path used to launch
pytest. The read-only bind source remains the already validated
`RESOLVED_INTERPRETER`; no shebang bytes are parsed or executed to construct
authority. Both launcher shapes and the real exact 16-plus-eight native lane
must pass before P0-07 resumes.

### Evidence-corrected P0-07 architecture A

Repeated review of the flat native PASS receipt/governance publication found
an architectural conflict: a retained descriptor proves the inode that was
opened, but POSIX `unlinkat` and `renameat2` act on the current directory entry.
A concurrent same-user replacement can therefore turn any post-publication
rollback into deletion or displacement of foreign evidence. A successful
no-clobber write followed by an exception is also ambiguous. P0-07 must no
longer revoke or delete published native evidence.

Native publication becomes an append-only candidate-bundle transaction. Each
native code stages a random private mode-0700 directory containing canonical
native-v2 receipt bytes, optional exact PASS governance, and a strict canonical
manifest binding code, run, head, Foundation date/context, inventory, receipt
identity/hash, governance presence/hash, exact nodes/counts, probe, outcome,
and its own self-hash. Every leaf and the directory are fsynced. After a
retained-authority postcheck, Linux `renameat2(RENAME_NOREPLACE)` atomically
publishes the directory at deterministic `<CODE>.artifacts`. The bundle remains
unaccepted until a second retained-authority postcheck succeeds.

The existing canonical `<CODE>.json` filename is the sole acceptance marker
and is installed atomically/no-clobber only after that second check. Its bytes
must exactly equal the bundle receipt. PASS requires exact sealed-custody
governance inside the bundle; DEFERRED forbids governance. On identity drift
before the marker, publish a canonical strict-v2 FAIL marker if the name is
still absent and leave the candidate bundle inert. A publisher exception after
a possibly successful marker write is resolved only by safe retained-parent
reread of the exact expected marker plus the fully validated bundle. Foreign,
invalid, stale, symlinked, replaced, or legacy-flat occupancy is a conflict;
it is never deleted or overwritten. Random staging leftovers are inert and
never glob-accepted.

No receipt, governance, bundle, marker, staging entry, or foreign artifact is
rolled back or unlinked by the native transaction. A post-marker authority
check cannot safely revoke acceptance and is diagnostic only; all security
postchecks occur before the marker linearization point. Native validators,
aggregate, governance audit, and Make artifact assertions must require the
canonical marker plus deterministic secure bundle/manifest. Flat external v1
evidence is unchanged until P0-08. Legacy flat native governance and stale
bundle layouts are rejected, so final native evidence must be regenerated on
the exact post-migration head.

### Evidence-corrected P0-08 architecture A

P0-08 migrates only the two external-authority codes from the flat
`t-g03a-capability-receipt/v1` shape to strict
`t-g03a-external-authority-receipt/v2`; native v2 semantics remain unchanged.
External v2 binds the exact three-node group, run, head, Foundation date and
context, active-inventory digest, strict code-specific safe authority facts,
execution counts, outcome, completeness hash, and self-hash. It never records
absolute authority paths, corpus or database values, research contents,
credentials, raw command output, or environment secrets.

External authority qualification is one retained/snapshotted session rather
than a tuple-only probe. Phase-3B validates the fixed root, every ancestor and
required direct entry without following symlinks, the production analyzer's
reviewed inventory digest/counts, and a canonical metadata commitment; its
root identity is retained through exact execution and pre-acceptance
postchecks. Legacy UV retains the exact fixed executable descriptor through
version, frozen offline sync, exact execution, and named-versus-held
postchecks, while independently snapshotting the exact legacy closure entries.
Only an entirely absent declared authority may produce ABSENT/DEFERRED.
Partial, invalid, drifted, timed-out, output-drifted, or nonpassing authority
produces FAIL and may never fall back to DEFERRED.

External v2 publishes only through the append-only Architecture-A primitive:
a private random mode-0700 candidate bundle, fsynced strict manifest and
optional exact PASS governance, authority postcheck, atomic
`RENAME_NOREPLACE` to deterministic `<CODE>.artifacts`, retained reread plus a
second authority postcheck, then exact canonical receipt bytes published last
as `<CODE>.json` acceptance marker. No external or native transaction evidence
is unlinked, rolled back, overwritten, quarantine-deleted, or glob-accepted.
Foreign occupancy and staging leftovers are preserved and fail closed;
ambiguous marker success resolves only by exact safe reread of marker and
bundle.

Portable aggregation accepts external PASS or genuine whole-authority
ABSENT/DEFERRED and surfaces DEFERRED; `validate-external --require-pass`
rejects DEFERRED for the later host workflow. The standalone
`test-external-authorities` target must build custody and reservations, collect
the portable-root baseline exactly once, and execute each VALID exact
three-node group exactly once without repeating common suites. P0-08 updates
the maintained topology design and implementation-evidence documents only; it
does not start P0-09, change native semantics, fabricate/download authority,
substitute UV, update dependencies or locks, access the network, mutate a
database/service/runtime, or enable production/live authority.

---

## P0-12R15 Packet A correction — Portable Nautilus preflight fixture closure

Hosted Foundation run `31853608129` at exact clean pushed head
`d404e6000b7fc682d7fd5437f0a3175caa7ee81f` published a validated diagnostic
for the exact 6,412-node root remainder: 6,131 passed and 281 skipped. The
reviewed classification at
`/tmp/p0-12-run-31853608129-diagnosis.md` (SHA-256
`6d583a02db40109087e616c5ba95cebecba899c250812ec58ae9e0064ff54059`)
separates 17 portable fixture defects from the remaining genuine native and
external authority obligations.

Packet A owns only those 17 portable nodes. Eight runtime-closure rejection
tests currently inherit a broad fixture that requires `/usr/bin/bwrap` even
though their primary rejection occurs before native-guard construction and
sandbox attestation. Nine job-closure rejection tests similarly require or
copy Bubblewrap before manifest/profile/inventory rejection, even though the
attestor rejects before `_sandbox_proof`. Strict RED must first demonstrate
that the exact 17 nodes skip before their primary rejection assertions.

The minimum fix deconflates synthetic rejection inputs from native capability.
Rejection-only nodes use an inert or nonexistent sandbox binding and explicit
forbidden-call sentinels for native build and sandbox execution. Every success,
publication, or real attestation node retains its existing real Bubblewrap and
sealed-toolchain requirements. Production materializers, attestors, policy,
the skip allowlist, active native/external inventory, Make routing, workflows,
dependencies, locks, and runtime/live authority remain unchanged.

After the exact 17 nodes pass with zero skip, xfail, or xpass, add them to the
portable-defect closure ledger under the new former code
`SRC-NAUTILUS-PREFLIGHT-FIXTURE-GATING`, using the already-created fixture-fix
commit as each historical proof authority. The closure becomes exactly 49
rows, while the active inventory remains 30 and the governed set becomes 79.
Against the current 6,474-node root baseline, moving the same 17 nodes from
the ordinary remainder into the closure proof makes the staged Packet A root
remainder exactly 6,395. The hosted 6,131-passed count is not the topology
remainder; 6,131 becomes the final remainder only after the separately reviewed
Packets B and C route the remaining 264 nodes. Update only the derived locked
closure/count/hash contracts required by this migration.

Require exact-17 RED/GREEN evidence, the full two affected Nautilus test
modules, portable-closure/topology/failure-diagnostic/artifact-firewall/P0
closure checks, and a clean standalone affected packet. Stop for independent
review after committed evidence. This packet does not authorize push, hosted
rerun or dispatch, Packet B or C, P0-13, production/runtime/database/service
mutation, broker/provider access, or live trading.

---

## P0-12R16 Packet B correction — Nautilus capability and authority topology

Packet B owns exactly 36 still-active Nautilus nodes from the reviewed final
map `/tmp/p0-12-run-31853608129-node-map.tsv` (SHA-256
`b7e8fa87d51af2f3f9dd65759ae167766de2046eb252a108ede28b1a8950cb0f`):
two additional `NATIVE-BWRAP-OS-SANDBOX` nodes, 22
`NATIVE-NAUTILUS-SEALED-TOOLCHAINS` nodes, ten
`NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX` nodes, and two
`EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS` nodes. The staged active inventory is
therefore 66 rows: 58 native and eight external. With the already-reviewed
49-row portable closure, the governed set is 115 and the 6,474-node baseline
has a 6,359-node remainder. Packet B does not change the closure ledger or the
skip allowlist.

The existing native-v2 receipt and probe schemas remain byte-for-byte exact for
`NATIVE-BWRAP-OS-SANDBOX` and `NATIVE-USERNS-ROOT-PROVISION`. The two new
multi-authority native codes use
`t-g03a-native-multi-authority-receipt/v3`. Its top-level fields are the v2
fields plus `authority`; its `probe` has exactly `command_id`, `exit_code`,
`stdout_sha256`, and `stderr_sha256`, and never mislabels an aggregate digest
as `executable_sha256`. Toolchain receipts use the closed command ID
`NAUTILUS_SEALED_TOOLCHAINS_V1` and an authority object containing exactly:

```text
authority_kind
rust_root_status
llvm_root_status
rust_policy_sha256
llvm_policy_sha256
rust_manifest_sha256
rust_tree_sha256
rust_file_count
llvm_manifest_sha256
llvm_tool_count
llvm_resource_header_count
```

The authority kind is `NAUTILUS_SEALED_TOOLCHAINS_V1`; both root statuses must
be `PRIVATE_CURRENT_USER_SEALED_DIRECTORY` for PASS. The values bind the exact
checked-in Rust 1.95.0 and LLVM 22.1.3 policies, their canonical materialized
manifests, the Rust policy tree digest/count, and the complete LLVM tool and
resource-header counts. The composite code uses the closed command ID
`NAUTILUS_SEALED_BUILD_SANDBOX_V1`. Its authority has exactly
`authority_kind`, `toolchains`, and `sandbox`; `toolchains` is the complete
toolchain object above and `sandbox` contains exactly `regular_file_status`,
`policy_sha256`, `expected_sha256`, `observed_sha256`, `expected_uid`,
`observed_uid`, `expected_gid`, `observed_gid`, `expected_mode`, and
`observed_mode`. The composite authority kind is
`NAUTILUS_SEALED_BUILD_SANDBOX_V1`, and the sandbox facts independently bind
the checked-in sealed-UV policy and retained `/usr/bin/bwrap` identity.

Native-v3 qualification is a retained multi-descriptor session. It validates
the exact checked-in policies and complete private sealed trees, retains the
Rust root, LLVM root, required tool leaves, and composite Bubblewrap identity,
and repeats exact named-versus-held policy/tree/manifest checks before each
Architecture-A publication linearization point. Only an absent required root
or leaf is `UNAVAILABLE/NATIVE_COMPONENT_ABSENT` and may produce DEFERRED.
Partial presence, unsafe ownership/mode/lineage, policy or manifest mismatch,
probe failure, and replacement are `BROKEN/FAIL`. The v3 parser, manifest, and
validator are code-discriminated; native-v2 bytes, parser rules, artifacts,
and outcome rules do not change.

External v2 adds only the third code-discriminated authority-key set for
`EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS`; the Phase-3B and legacy-UV schemas stay
exact. The new authority contains exactly `authority_kind`,
`base_root_status`, `artifact_root_status`, `runtime_policy_sha256`,
`base_manifest_sha256`, `base_file_count`, `base_file_inventory_sha256`,
`artifact_manifest_sha256`, `artifact_wheel_sha256`, and
`artifact_wheel_size`. It retains both private reviewed directories, validates
them with the production runtime-closure policy validators, and repeats exact
ancestor/root/manifest/inventory checks through Architecture-A publication.
Only both declared roots being absent is `ABSENT/DEFERRED`; one missing root,
unsafe lineage or ownership/mode, mismatch, or replacement is
`PARTIAL|INVALID|DRIFTED/FAIL`.

Production defaults are the already-declared fixed authorities:
`/home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0`,
`/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain`,
`/home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3`, and
`/home/thenam176/.cache/trading-agent/nautilus/artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c`.
Private path injection exists only as explicit retained-session factory
arguments for adversarial tests; no CLI flag or environment variable may
select a different authority. Portable absence publishes DEFERRED. Existing
`validate-native --require-pass` and `validate-external --require-pass` reject
any DEFERRED host qualification, so Packet B requires no Make or workflow route
change.

Strict RED must prove the missing exact mapping, stale native-v2 use for each
new native code, missing code-discriminated sessions, and missing external
authority schema before implementation. GREEN must cover exact node mapping,
absence versus partial/unsafe states, policy and manifest mismatch, retained
identity replacement, native-v2 non-regression, canonical receipt/manifest
validation, Architecture-A no-clobber transactions, portable DEFER, host
require-pass rejection, and exact 66/49/115/6,474/6,359 accounting. No packet
may download, provision, synthesize, or self-approve a toolchain, cache, or
sandbox, and Packet B does not authorize push, hosted rerun, Packet C, P0-13,
production/runtime/database/service mutation, broker/provider access, or live
trading.

---

## P0-12R17 Packet C correction — Disposable PostgreSQL external authority topology

Packet C owns the 228 reviewed disposable-PostgreSQL logical nodes in the
final map `/tmp/p0-12-run-31853608129-node-map.tsv` (SHA-256
`b7e8fa87d51af2f3f9dd65759ae167766de2046eb252a108ede28b1a8950cb0f`).
The map contains 183 GREEN-only nodes, 22 RED-only nodes, and 23 event-chain
nodes whose existing fixture conditionally changes both operation authority
and migration head. Those 23 logical nodes must be parameterized into distinct
`DISPOSABLE_PG_RED` and `DISPOSABLE_PG_GREEN` physical node IDs. Each physical
node then belongs to one ordinary external-authority code; Packet C does not
introduce a composite or dual receipt.

The final active inventory adds exactly 251 rows:
`EXT-DISPOSABLE-PG-GREEN` owns 206 nodes (183 plus the 23 GREEN parameters),
and `EXT-DISPOSABLE-PG-RED` owns 45 nodes (22 plus the 23 RED parameters).
Root collection rises from 6,503 to 6,526 solely because of the 23 added
parameters. After the unchanged 29 deselections, the portable baseline is
6,497. Active inventory becomes 317 rows (58 native and 259 external); with
the unchanged 49-row closure, the governed set is 366 and the exact portable
remainder is 6,131.

Both new codes extend the ordinary strict external-v2 receipt with separate,
code-discriminated authority facts. Qualification accepts only externally
issued, current-user-private, no-follow approval records that pass the existing
strict canonical validators and bind the exact Foundation commit/tree, scope,
approved operation set, source bindings, constraints, two distinct human
review identities, and a validity window no longer than 24 hours. GREEN also
requires its independently approved fixture plan because the four planned
GREEN nodes are
`tests/control_api/test_alembic_schema.py` at the reviewed operations. RED has
no planned node and therefore forbids rather than fabricates a RED fixture
plan. Missing complete code authority is `ABSENT/DEFERRED`; partial, stale,
foreign-scope, unsafe, malformed, mismatched, replaced, or expired material is
`INVALID|PARTIAL|DRIFTED/FAIL`. Host `--require-pass` continues to reject
DEFERRED.

Preflight is side-effect-free: it may validate retained files, source identity,
exact operation and slot sets, checked PostgreSQL-16 executable identities,
and safe environment policy, but it must not call `initdb`, `pg_ctl`, reserve
a port, create a fixture root, connect to PostgreSQL, or enter the database
harness context. Exact execution is the sole database-starting boundary. Each
session retains original record/plan descriptors and snapshots, copies only
validated canonical bytes into no-clobber mode-0600 files below a task-private
mode-0700 execution root, and supplies a fresh code-specific pytest environment
using the existing singular harness variables. Ambient disposable-PG and
runtime-database controls are scrubbed before the exact RED or GREEN overlay.
Original and copied authority identities are postchecked through both
Architecture-A publication checks, then only the task-owned private copy root
is removed.

Operator inputs are fixed external paths, not source data or self-approval:
`DISPOSABLE_PG_RED_APPROVAL_RECORD`,
`DISPOSABLE_PG_GREEN_APPROVAL_RECORD`, and
`DISPOSABLE_PG_GREEN_FIXTURE_PLAN`. The topology translates them internally to
the harness's existing `TRADING_TEST_DISPOSABLE_*` environment for one code at
a time; raw paths, record contents, reviewer identities, database values, and
credentials never enter receipts. The ordinary portable Make/workflow route
supplies none of these inputs and receives honest DEFERRED receipts, so no
workflow change is authorized and Make changes require separate concrete
evidence that internal environment injection cannot carry the fixed boundary.

Strict RED must cover the 23 missing physical node parameters, missing exact
251-row inventory and derived hashes/counts, absent code-specific external-v2
schemas, and missing retained sessions. GREEN must cover exact map projection,
scope-separated execution environments, whole-authority absence, partial and
unsafe inputs, expiry/source/operation/slot mismatch, symlink/hardlink/mode/
owner/replacement attacks, forbidden child-process and database-start behavior
during preflight, exact PASS/DEFER/FAIL receipts, no-clobber publication,
external-v2 non-regression, and final 6,526/29/6,497/317/49/366/6,131
accounting. Real PASS remains blocked until independent humans issue exact
final-commit RED and GREEN records plus the GREEN fixture plan. Packet C may
not synthesize those records, start a database for preflight or local proof,
weaken skip/outcome policy, access production/runtime data, push, dispatch a
hosted run, start P0-13, or enable production/live authority.

### P0-12R17 authority-schema erratum — separate RED evidence code

The initial Packet C correction incorrectly assigned every RED node to one
approval input. Two nodes request the same reviewed SQL through distinct stable
operation IDs: the ordinary derivation node uses
`jobs-authority-catalog-derivation-red-v1`, while
`tests/jobs/test_job_authority_catalog.py::test_capture_reviewed_catalog_evidence_for_operator_review`
internally uses `jobs-authority-catalog-evidence-derivation-red-v1`. The
existing approval schema intentionally allows exactly one `red_sql_binding`
bound to exactly one operation ID. One record therefore cannot authorize both
without weakening the reviewed-operation boundary.

Packet C must preserve that schema and split only the evidence node into
`EXT-DISPOSABLE-PG-RED-EVIDENCE`, supplied by the fixed external input
`DISPOSABLE_PG_RED_EVIDENCE_APPROVAL_RECORD`. The ordinary
`EXT-DISPOSABLE-PG-RED` code becomes 44 nodes and uses
`DISPOSABLE_PG_RED_APPROVAL_RECORD`; the new evidence code owns exactly one
node. Both remain ordinary external-v2 receipts with independent retained
authority sessions and exact operation/binding validation. Cross-code records,
wrong SQL-binding operation IDs, additional approved operations, and missing
or substituted records fail closed.

The evidence session creates its own empty task-private mode-0700 output
directory and injects it only as
`TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR` for that exact subprocess.
The directory is retained and postchecked through publication, then removed as
task-owned state. Its raw database-derived files never enter receipts or the
portable artifact. No composite or multi-record receipt is introduced.

Accounting is unchanged: GREEN 206 plus ordinary RED 44 plus RED evidence one
equals 251 PostgreSQL nodes; active remains 317, closure 49, governed 366, and
the 6,497-node portable baseline remainder remains 6,131. Strict RED/GREEN must
prove the exact one-node split and wrong-record/binding/cross-code rejection
before any valid-session implementation proceeds.

### P0-12R17 fix round 1 — sealed source identity and exact Git environment

Independent review found that the real disposable-PostgreSQL qualifier calls
`_pg_source_tree`, which discovers `git` through ambient `PATH`. The existing
no-child preflight test injected a fake qualifier and therefore did not cover
that real boundary. Review also found that the exact child environment retains
ambient `GIT_*` variables and caller `PATH`; PG authority tests that resolve
source identity or enumerate tracked files can consequently execute against a
foreign Git directory, index, object store, configuration, worktree, diff
driver, or executable.

Fix round 1 must remove source-tree discovery from the real qualification
boundary. Qualification receives the already sealed Foundation commit/tree as
explicit values and validates both approval and GREEN-plan source identities
against those values; it must not spawn a subprocess. The retained session
must preserve that binding through its postcheck and Architecture-A
publication. The exact PG child environment must remove every ambient `GIT_*`
key and must not inherit caller `PATH`. It may add only a closed Git environment
needed by the existing source-identity tests: a fixed trusted executable search
path plus explicit system/global-config isolation and optional-lock/prompt
controls. No database harness, operation semantics, authority record, fixture
plan, receipt, PASS/DEFER/FAIL, inventory, accounting, Make, or workflow
contract changes.

Strict RED precedes production edits and exercises the real qualifier rather
than an injected substitute. It must prove qualification invokes no subprocess
and that hostile `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`,
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_CONFIG*`,
`GIT_EXTERNAL_DIFF`, and `PATH` values cannot influence the exact child. GREEN
must preserve the existing child tests' legitimate Git access through the
closed trusted path without starting or connecting to PostgreSQL. Stop after
focused and affected verification for independent re-review; no push, hosted
dispatch/rerun, P0-13, production/runtime/database/service mutation, or live
authority is authorized.

### P0-12R18 correction — hosted deterministic fixtures and operation-ID uniqueness

Hosted run `31865258494` at exact
`c9fca10c24506196e8748e073e5faebc6d0c98b0` exposed exactly three source-test
failures before artifact publication. This packet corrects only those three
closed contracts; it does not authorize database execution, PostgreSQL
qualification, skip/outcome changes, inventory or accounting changes, Make,
workflow, dependency, lock, production, runtime, or live changes.

The native classification test must not infer `PROBE_PENDING` from the hosted
machine's real `/usr/bin/unshare`. It uses an injected task-private executable
and retained probe/session seam so the classification assertion remains exact
and deterministic while preserving real absent, unsafe, and BROKEN behavior.
The receipt-first failure-diagnostic fixture must keep the original absent
authority facts for the three pre-Packet-C external codes and use the exact
code-specific absent disposable-PostgreSQL authority shape for each of the
three PG codes. It must not weaken receipt validation or diagnostic ordering.

The job-authority evidence capture operation ID remains the exact public value
`jobs-authority-catalog-evidence-capture-red-v1`, but the topology authority
must not introduce a second identical full string literal into the global
source scan. Compose the topology value from stable fragments or one shared
non-test authority without weakening the existing uniqueness assertion; audit
the other evidence/derivation IDs for the same collision class.

Strict RED is the exact three hosted nodes at the reviewed source state. GREEN
requires all three exact nodes, their full affected modules, unchanged root
collection, static contracts/P0/broad-handler checks, and a fresh standalone
affected packet. Stop at a clean committed SHA for independent review; no push,
hosted rerun/dispatch, P0-13, database/service mutation, or live action.

### P0-12R19 correction — deterministic bwrap classification fixture

Hosted run `31866447559` at exact
`d70d0ae5a83f2dc2211a3fcbd09eeb31c154658e` exposed one remaining source-test
failure before artifact publication. The R18 native classification node no
longer assumes a real `/usr/bin/unshare`, but its later bwrap branch still calls
the trusted production policy and asserts `PROBE_PENDING`. A hosted runner with
no `/usr/bin/bwrap` correctly returns `UNAVAILABLE`, making that test fixture
host-dependent.

The classification test must instead retain a task-private executable and an
exact matching injected policy/session for the bwrap branch. It must preserve
the production probe record, descriptor/named identity, policy digest,
authority projection, and postcheck semantics. The same node continues to
exercise real missing and nonregular leaf classification, and the existing
multi-authority absent-bwrap branch remains fail-closed. Production native
classification and trusted-policy defaults do not change.

Strict RED must first reproduce the hosted absence at the exact existing node.
GREEN requires that exact node, its full affected module, unchanged collection,
static inventory/contracts/P0 checks, and a fresh standalone affected run.
Stop at a clean committed SHA for independent review; no production
classification, Make, workflow, dependency, lock, PostgreSQL/database, P0-13,
hosted rerun/dispatch, service, broker/provider, or live change is authorized.

### P0-12R20 correction — safe absent authority lineage

Hosted run `31867825954` at exact
`f563bb0752e9401dfd50ab11c61c02b4f8b75a68` passed the 6,131-node root
remainder and 49-node portable closure, then failed because
`NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX` preflight was `BROKEN`. The fixed
authority roots are below `/home/thenam176`; on the hosted runner that
intermediate directory is absent. The existing parent-chain snapshot requires
every parent to exist, so two genuinely absent roots are misclassified as an
unsafe identity. The same latent condition affects the Nautilus external
runtime-closure roots.

This packet adds a separate strict absent-lineage snapshot used only by native
Nautilus multi-authority and external Nautilus runtime-closure absence. It
walks from the filesystem anchor to the first missing component, validates
every existing prefix directory under the existing ownership/type/mode policy,
and records both exact prefix identities and the exact missing suffix. It
returns absence only when both required roots have valid missing boundaries.
Partial roots, unsafe or foreign ancestors, symlinks, special files, and any
other invalid path remain fail-closed.

The absence postcheck must reproduce the same retained prefix identities and
missing suffix. Any new component, boundary movement, replacement, symlink, or
ownership/mode mutation rejects before receipt publication. Present-authority
paths retain their existing parent snapshot, retained descriptors, qualifiers,
and postchecks; the generic Phase3B, legacy-UV, and other external open helpers
are not broadened.

The prefix identity is not pathname-only evidence. The qualifier must open and
retain a no-follow directory descriptor for the nearest existing safe ancestor,
bind its opened identity to the validated named identity, and return that
descriptor in the native or external session custody. Postcheck verifies the
held descriptor, revalidates the named prefix identities, and checks the first
missing component relative to the held descriptor with no symlink following.
All descriptors close through the existing session context-manager lifecycle.
This retained-FD requirement prevents a pathname replacement window between
qualification and Architecture-A publication.

Strict RED must cover hosted-shaped missing intermediate roots for both native
multi-authority and Nautilus external authority before implementation. GREEN
adds appearance, symlink, unsafe-mode, and partial-root attacks in existing
collected nodes, preserves collection and receipt/accounting contracts, and
runs full topology/external affected and fresh standalone packets. No policy,
Make, workflow, dependency, lock, PostgreSQL/database, P0-13, production,
runtime/service, broker/provider, or live change is authorized.

### P0-12R21 correction — sealed late-topology failure evidence

Hosted run `31869750204` at exact
`6fb87d3d420301556f717ba31618356ceffd0889` passed the 6,131-node root
remainder and the 49-node portable closure, then terminated at native topology
preflight. Its raw Architecture-A FAIL marker was retained, but the outer Make
catch recognized only the root-remainder failure diagnostic. The upload step
therefore had no final artifact, and the public log exposed only the closed
terminal `BROKEN` classification.

The outer catch must retain the root-remainder diagnostic as its first and only
matching branch. If it is absent, it invokes a separate topology-failure
publisher once, ignores only that publisher's result, and preserves the nested
Make status. The new publisher accepts exactly one validated native or external
Architecture-A FAIL marker/bundle plus the sealed Foundation/inventory
authority and any validated earlier receipts. It emits a distinct sealed
diagnostic projection containing only Foundation bindings, the terminal code,
lane, closed preflight state/fact class, expected count, and receipt/manifest
hashes. It cannot publish governance, acceptance, raw test output, paths,
exception text, command arguments, or PASS evidence.

The packet reuses the existing retained-FD snapshot, no-follow, mode sealing,
checksum, and `RENAME_NOREPLACE` publication boundary. Malformed or foreign
markers, partial root/closure progress, multiple terminal failures, unrelated
raw entries, and terminal-diagnostic coexistence reject without publication.
It does not change native/external qualification, policy denials, skip policy,
workflow upload rules, PostgreSQL authority, dependency/lock state, or live
authority. The next hosted run remains non-qualifying until its sealed
diagnostic is independently inspected and the underlying native preflight
cause receives its own RED/GREEN/review packet.

### P0-12R22 correction — stable retained-FD probe argv identity

Hosted diagnostic run `31872615757` at exact
`ae1b0c1afe9408dd9c94963b9b10381197f6110d` published the sealed R21 artifact.
It binds the terminal native code `NATIVE-USERNS-ROOT-PROVISION` to
`BROKEN` / `NATIVE_PROBE_INVALID` after the portable root and closure passed.
Local reproduction shows the retained executable is invoked as
`/proc/self/fd/<descriptor>`, so util-linux derives that variable pathname as
its diagnostic program name. The closed denial contract expects the stable
program name `unshare`; an otherwise valid policy denial therefore cannot
match and is classified BROKEN.

The bounded correction keeps execution bound to the same retained no-follow
descriptor by passing `/proc/self/fd/<descriptor>` as the explicit subprocess
`executable`, while passing the fixed code-specific `argv[0]` (`unshare` or
`bwrap`) in the argument vector. Exact namespace operations, scrubbed
environment, descriptor custody, digest/identity postchecks, timeout, and the
closed denial byte sets remain unchanged. No denial string, outcome policy,
skip policy, PostgreSQL authority, Make/workflow route, dependency, lock,
production, database, broker/provider, or live boundary is broadened.

Strict RED must prove both native codes still use the retained FD while
requiring the fixed `argv[0]`; GREEN must cover the native classification
matrix, full topology module, compile, broad-handler inventory, and diff
checks. Independent review is required before any further remote update or
hosted qualification attempt.

### P0-12R23 correction — retained legacy-UV absence boundary

Hosted run `31886123542` at exact
`05c40ac728d50c84b8dabd5ac50f1932cabab60e` proves R22 advanced through the
native lane, then published a sealed terminal failure for
`EXT-LEGACY-UV-AUTHORITY` with `INVALID` / `AUTHORITY_INVALID`. The fixed
external executable `/home/thenam176/.local/bin/uv` is genuinely absent on the
hosted runner because an intermediate `/home/thenam176` component is absent.
The generic present-executable opener requires a complete ancestor chain and
therefore labels this safe missing boundary INVALID before it can classify the
executable as absent. The checked-out legacy source root remains present, so
the old two-components-absent branch is unreachable in portable hosted CI.

The bounded correction reuses the existing retained nearest-safe-ancestor
absence contract before the present legacy authority path. When the UV path
has an exact safe missing suffix, qualification returns only the existing
`ABSENT` / `AUTHORITY_EXECUTABLE_ABSENT` projection, retains the safe ancestor
descriptor, and postchecks the held/named prefix plus first missing component.
The legacy source root is not read or qualified when its required external UV
executable is absent. Appearance, unsafe or foreign ancestry, symlink/special
replacement, descriptor/name divergence, and present-UV plus missing/invalid
legacy closure remain fail-closed. Present legacy qualification and its
scoped real-root ancestry exception remain unchanged.

Strict RED must reproduce a hosted-shaped missing intermediate for UV while a
complete legacy root is present, require one retained absence descriptor, and
prove appearance rejects at postcheck. GREEN must retain existing partial and
unsafe classifications and pass the external-v2/topology/failure consumers,
collection, compile, inventory, and diff checks. No authority record,
PostgreSQL/DB operation, policy/skip change, Make/workflow route, dependency,
lock, production, broker/provider, or live change is authorized. Independent
review is required before another remote update.

### P0-12R24 correction — retained Phase3B corpus absence boundary

Hosted run `31887391308` at exact
`80a5ffd9b21071f424471e6e01d3568b39456f42` proves the native and legacy-UV
corrections advanced to `EXT-PHASE3B-CORPUS`, then published a sealed terminal
`INVALID` / `AUTHORITY_INVALID` receipt. Its fixed reviewed-corpus root
`/home/thenam176/.hermes/crypto-research` is absent below a missing
`/home/thenam176` component on the hosted runner. The Phase3B opener still
requires a complete parent chain before its leaf-absence branch, so the safe
missing boundary is mislabeled INVALID.

The bounded correction applies the already reviewed retained
nearest-safe-ancestor absence helper before the Phase3B present-corpus path.
Safe missing corpus authority returns only the existing
`ABSENT` / `AUTHORITY_ROOT_ABSENT` projection with one retained descriptor and
the shared held/named/first-missing postcheck. The corpus analyzer and required
entries are not read when the authority root is absent. Unsafe or foreign
ancestry, symlinks, special entries, appearance, rename/replacement, and
present-but-partial corpus contents remain fail-closed. The complete present
corpus validator and authority facts remain unchanged.

Strict RED must reproduce the hosted-shaped missing intermediate, require the
retained descriptor, and prove appearance rejection in an existing canonical
test node. GREEN must include initial symlink/0777 rejection, the full
external-v2/topology packet, closure node identity, consumers, compile,
generated inventory, and diff checks. No outcome/policy/skip/schema,
PostgreSQL/DB authority, Make/workflow, dependency/lock, production,
broker/provider, or live change is authorized. Independent review is required
before the next remote attempt.

### P0-12R25 correction — validate the completed closure without re-execution

Hosted run `31888119622` at exact
`8a59067f97ac48072cc4d7b0a99e399bd2e90215` passed the 6,131-node portable
remainder and the 49-node portable closure, then completed the native and
external topology lanes. The later explicit `check-portable-defect-closure`
target invoked the execution action `check-closure` a second time after the
topology recipe had destroyed its private custody-extension build directory.
It therefore failed first with `portable root collection requires native
custody identity`. Merely preserving or rebuilding the extension is not a
valid repair: the target would then execute the same 49 nodes again and collide
with the already published no-clobber closure proof.

The bounded correction keeps the required Make target and target graph, but
changes that post-topology target to a distinct `validate-closure` action. The
new action loads the sealed Foundation context and baseline, derives the sealed
custody policy from the baseline, and validates the existing private closure
governance/proof pair with the current inventory, closure ledger, head, date,
context, custody, node, digest, command, and outcome bindings. It performs no
pytest execution and publishes no artifact. The topology target remains the
single owner of `check-closure` execution and no-clobber proof publication.

Strict RED must prove the post-topology target still routes to the executing
action and that no validate-only CLI exists. GREEN must prove that an already
published closure validates after the live custody environment has been
removed, while missing, malformed, foreign, stale, or custody-mismatched proof
or governance evidence still rejects. Update the exact Make observer and CLI
action contract without changing the recursive target graph or P0 matrix node
identity. Run focused closure/Make tests, the P0 closure checker, affected
consumers, compile/inventory/diff gates, and a fresh standalone packet before
independent review. No native/external outcome or authority policy, skip
policy, PostgreSQL/DB authority, workflow, dependency/lock, production,
broker/provider, or live boundary may change.

### P0-12R26 correction — retire migrated PostgreSQL skip authority

Hosted run `31889276195` at exact
`200ff5c4cc864a997c4d09d9f5f280f2c019b298` passed the complete source-safe
suite, portable remainder, closure, native/external topology, and the R25
validate-only closure target. It then failed in `check-test-governance-topology`
because all 228 root PostgreSQL skip approvals were stale. Packet C moved those
228 logical nodes into the explicit disposable-PostgreSQL external authority
lane, including expansion of 23 dual-scope logical nodes into 46 exact
authority executions, but left their prior ordinary-root skip entries in
`tests/skip-allowlist.yaml`.

The bounded correction removes exactly the 228 root entries whose outcome is
`skipped`, category is `DISPOSABLE_POSTGRES_REQUIRED`, and approval type is
`disposable-postgres-test-approval-v1`. This is an authority reduction: those
tests can no longer be accepted as ordinary CI skips and remain governed only
by the reviewed external authority receipts. Preserve exactly the 29 root
`runtime_postgres` deselections and the two legacy provider-credential skips;
do not alter their reasons, approvals, dates, or outcomes. Update the maintained
governance evidence counts and every derived tracked digest using its canonical
generator or audit mechanism.

Strict RED must prove the tracked allowlist still contains exactly 228 migrated
root skip authorities. GREEN must prove none remain, the allowlist is exactly
31 entries (29 root deselections and two legacy skips), the 251 physical
PostgreSQL inventory rows remain external authority, topology/accounting counts
are unchanged, and the full governance comparison accepts the resulting hosted
observation shape. Run focused allowlist/governance/topology consumers,
baseline/contracts/P0/static gates, fresh standalone verification, and
independent review before another push. Do not mint PostgreSQL approval, run a
database, weaken skip/outcome policy, change Make/workflow routing, dependencies
or locks, or touch production, broker/provider, or live authority.

### P0-12R27 diagnostic — classify final acceptance publication stage

Hosted run `31890844450` at exact
`cd35075205c9b2e0eda90dca2fe260a7ad1a0b8f` proves the R26 allowlist
correction: the portable remainder passed 6,131 nodes, the closure passed 49,
the clean baseline passed, topology produced its valid portable DEFER result,
and test governance accepted 6,860 passed observations with only the retained
29 deselections, two legacy skips, and 17 externally blocked authorities. The
final acceptance publisher then rejected with the undifferentiated
`ARTIFACT_FIREWALL_REJECTED LAYOUT`; its failure catch also could not publish a
topology-only diagnostic because topology had already completed successfully.
No artifact exists, so the failed run does not prove whether raw binding,
projection construction, source-tree identity, or final publication rejected.

The bounded diagnostic correction adds only a closed final-publication stage
token: `RAW_BINDING`, `PROJECTION`, `SOURCE_TREE`, or `PUBLICATION`. It must
retain the existing error code/category, suppress every underlying exception,
path, value, digest, argv, stdout, and stderr, and expose source-tree substage
and reason only through the already reviewed closed source-tree enum. The
acceptance schema, validators, evidence bytes, receipts, topology, Make and
workflow routes, skip/outcome policy, and publication decisions remain
byte-for-byte unchanged.

Strict RED must inject one hostile failure at each real boundary from an
existing collected test node and prove that the current CLI cannot name the
closed stage without disclosing hostile values. GREEN must prove exact closed
tokens, no retained cause/context, unchanged success publication, and all
existing failure/topology publishers. Run focused firewall tests, affected
Make/closure consumers, collection/static gates, a fresh standalone packet,
and independent security review before one new hosted push attempt. Never
rerun `cd35075205c9b2e0eda90dca2fe260a7ad1a0b8f`. Do not alter PostgreSQL/DB
authority, execute a database, weaken policy, or touch production, live,
broker/provider, dependency, or lock boundaries.

### P0-12R28 diagnostic — classify final raw-binding substage

Hosted run `31892046623` at exact
`d05ecf8b0cf3ce3b722a98ada1bc9f46c93aad53` reproduced the complete safe
source route and ended with the new exact stage
`ARTIFACT_FIREWALL_REJECTED LAYOUT RAW_BINDING`. The artifact count is zero,
so that run proves the destination, source-tree and publication stages were not
reached, but it cannot distinguish the raw source snapshot, root inventory,
accounting/context, topology inventory, Architecture-A receipts, governed
report, semantic binding, or retained raw postcheck.

The bounded diagnostic correction adds one second-level closed token only when
the final stage is `RAW_BINDING`: `SOURCE_SNAPSHOT`, `ROOT_INVENTORY`,
`ACCOUNTING`, `TOPOLOGY_INVENTORY`, `RECEIPTS`, `GOVERNANCE_REPORT`,
`SEMANTIC_BINDING`, or `RAW_POSTCHECK`. No underlying error message, path,
identity, count, digest, receipt field, exception, stdout, or stderr may cross
the boundary. Later final stages retain their R27 output. Raw validators and
their order, accepted schemas/bytes, topology/governance semantics, Make and
workflow routes, and all publication decisions remain unchanged.

Strict RED must inject each raw substage through the existing collected CLI
node and show that R27 emits only `RAW_BINDING`. GREEN must emit exactly one
allowlisted substage, retain no cause/context, and keep all four R27 stage
attacks, positive publication, failure/topology publishers, collection and
static gates green. Use the canonical broad-handler generator, a fresh
standalone packet and independent security review before one new hosted push.
Never rerun `d05ecf8b0cf3ce3b722a98ada1bc9f46c93aad53`. Do not change acceptance,
policy, skip/outcome, PostgreSQL/DB authority, dependencies/locks, production,
live, broker, or provider boundaries.

### P0-12R29 diagnostic — classify final semantic-binding substage

Hosted run `31894293654` at exact
`c7d824cbca592a5adcafd9437f78e296103b7a91` completed the safe source route
with 6,860 passed observations, two retained legacy skips, 29 deselections and
17 explicit authority blocks, then ended with the exact closed rejection
`ARTIFACT_FIREWALL_REJECTED LAYOUT RAW_BINDING SEMANTIC_BINDING`. Artifact
count is zero. This proves the raw snapshot, root/accounting, topology receipt
and governed-report inventory boundaries passed, but does not identify the
failing operation inside semantic binding.

The next bounded diagnostic adds one closed semantic substage only when the
primary stage is `RAW_BINDING` and raw substage is `SEMANTIC_BINDING`:
`TOPOLOGY_PROJECTION`, `PAYLOAD_BINDING`, `GOVERNANCE_DOCUMENT`,
`DISCLOSURE_BINDING`, `GOVERNANCE_PROJECTION`, `TOPOLOGY_PAYLOAD_COPY`, or
`RUN_METADATA`. Existing closed code/category must be retained; every message,
path, value, receipt field, count, digest, exception, cause/context, stdout and
stderr remains suppressed. Validation order, accepted evidence and semantic
bytes, schemas, receipts, Make/workflow routes, publication decisions and all
policy are unchanged.

Strict RED/GREEN must exercise all seven closed boundaries from an existing
collected firewall node, retain the R27/R28 taxonomy and hostile-data
suppression, and preserve positive publication. Run focused firewall and
affected topology/governance consumers, collection/static gates, a fresh
standalone packet and independent security review before one new hosted push.
Never rerun `c7d824cbca592a5adcafd9437f78e296103b7a91`. Do not mint PostgreSQL
authority, run a database, relax skip/outcome or acceptance policy, or change
production, live, broker/provider, dependency or lock boundaries.

### P0-12R30 correction — canonical reconciled disclosure parity

Hosted run `31895551027` at exact
`edf516ce31fd5581cd01560a83abd923b15f337e` ended with the closed rejection
`ARTIFACT_FIREWALL_REJECTED LAYOUT RAW_BINDING SEMANTIC_BINDING
DISCLOSURE_BINDING`, with no artifact. The governed report currently embeds the
four fields returned by `aggregate_receipts`, while the final publisher
correctly requires equality with `reconcile_portable_root_accounting`, which
adds the already validated `portable_root_remainder_status=PASS` and exact
string `baseline_candidate_count`. The two independently validated views are
therefore deterministically unequal.

The bounded correction makes `audit_topology_root_records` return the same
six-field canonical disclosure after it has validated the exact baseline,
remainder execution/deferral union, closure proof and receipt accounting. Keep
the final publisher's exact equality unchanged. Do not synthesize counts from
unvalidated report data, weaken accounting, change evidence or receipt schemas,
or alter Make/workflow, skip/outcome, PostgreSQL authority, dependency, lock,
production or live boundaries.

Strict RED must update the existing topology-audit disclosure assertion to the
six-field contract and fail on the current four-field result. GREEN must prove
the exact remainder PASS and baseline count derived from the retained baseline,
then run hosted-disclosure, firewall, topology/governance consumers, collection
and static gates, fresh standalone verification and independent review. Never
rerun `edf516ce31fd5581cd01560a83abd923b15f337e`; after approval use exactly one
new push-triggered hosted attempt.

### P0-12R31 diagnostic — classify final publication substage

Hosted run `31896607371` at exact
`26ed4d1da676e4a286fc8a8187c661f1a96ceaab` passed raw semantic and corrected
disclosure binding, then ended with the closed rejection
`ARTIFACT_FIREWALL_REJECTED LAYOUT PUBLICATION`; artifact count is zero. The
run proves R30 closed its defect but cannot identify which retained-custody,
projection, sealing, rename or final-validation operation rejected.

Add an internal non-throwing publication-stage tracer that records one closed
token immediately before each existing operation without adding catches or
changing control flow: `INPUT_BINDING`, `STAGING_LINEAGE`,
`DESTINATION_LINEAGE`, `DESTINATION_ABSENCE`, `SOURCE_SNAPSHOT`,
`PROJECTION_VALIDATION`, `MANIFEST_BUILD`, `CANDIDATE_BUILD`,
`STAGING_POSTCHECK`, `DESTINATION_POSTCHECK`, `CANDIDATE_SEAL`,
`SEALED_VALIDATION`, `ATOMIC_RENAME`, or `PUBLISHED_VALIDATION`. Emit the token
only below final stage `PUBLICATION`. Preserve existing closed code/category
and suppress every message, path, identity, mode, digest, exception,
cause/context, stdout and stderr.

Strict RED/GREEN must exercise every allowlisted token from an existing
collected publisher node, prove invalid stage/token combinations reject, retain
positive publication and all existing boundary-hook behavior, and show no
manifest/evidence byte or acceptance change. Run focused firewall and affected
consumers, collection/static gates, a fresh standalone packet and independent
review before exactly one new hosted push attempt. Never rerun
`26ed4d1da676e4a286fc8a8187c661f1a96ceaab`. Do not weaken custody, lineage,
no-clobber, sealing, validators, skip/outcome or authority policy; do not mint
PostgreSQL approval, run a database or alter production/live/dependencies.

### P0-12R32 diagnostic — classify projection-validation substage

Hosted run `31897847824` at exact
`e272e644097541f2aaec2a257c7213fabd47e676` completed 6,860 tests with two
authority-gated skips and exact 6497/6526 portable collection, then ended with
`ARTIFACT_FIREWALL_REJECTED LAYOUT PUBLICATION PROJECTION_VALIDATION`. Artifact
count is zero. R31 therefore proves input and both lineages, destination
absence and the retained source snapshot passed; rejection occurred while
validating the private final projection, before manifest or candidate creation.

Add one closed observational projection substage beneath exactly
`PUBLICATION PROJECTION_VALIDATION`: `ROOT_LAYOUT`, `GOVERNANCE_LAYOUT`,
`TOPOLOGY_LAYOUT`, `PHASE_LAYOUT`, `FOUNDATION_CONTEXT`, `RESERVATION`,
`LOCKED_INPUTS`, `BASELINE`, `COLLECTION`, `REMAINDER`,
`REMAINDER_GOVERNANCE`, `CLOSURE`, `RECEIPTS`, `AGGREGATE`,
`FINAL_GOVERNANCE`, or `SECRET_SCAN`. Mark a token immediately before the
existing operation. Do not add a catch, expose validator text/data, or change
accepted layouts, schemas, bytes, ordering, control flow or policy. Preserve
the R27–R31 closed code/category/stage taxonomy and cause/context suppression.

Strict RED/GREEN must exercise every token from the existing collected
firewall node and prove the real successful projection traverses the exact
ordered token sequence. Run the full firewall/failure packet, affected
topology/governance consumers, collection/static gates, a fresh standalone
packet and independent review before exactly one new push-triggered hosted
attempt. Never rerun `e272e644097541f2aaec2a257c7213fabd47e676`. Do not
accept the currently rejected projection, weaken validation or custody, mint
PostgreSQL authority, run a database, or alter Make/workflow, skip/outcome,
dependencies/locks, production or live boundaries.

### P0-12R33 correction — canonical collection-report parity

Hosted run `31898882340` at exact
`0c789bbcdb3c63f4955bb1a27b72847c6fefd398` ended at
`PUBLICATION PROJECTION_VALIDATION COLLECTION`, with no artifact. An exact
local reconstruction proves the canonical governance plugin emits six keys,
including `summary`, and 6,526 records: 6,497 `collected` candidates plus 29
disjoint collection-phase `deselected` records (17 `runtime_postgres`, 12
`host_coupled`). The topology baseline validator already validates this report
and binds its exact digest; the firewall incorrectly requires five keys,
exactly 6,497 rows and only `collected` records.

Align the firewall's independent collection validator with the canonical
collector contract: exact six-key schema; exact summary recomputed from rows;
collected IDs equal the sealed sorted candidate list; deselected IDs are
unique and disjoint; every row has exact fields/component/phase; collected
reason is empty; deselected reason is exactly one of the two marker-selection
reasons. Do not convert deselections into candidates, execution PASS or
acceptance evidence, and do not remove the topology validator or digest
binding.

Strict RED must make the shared final-projection fixture include a canonical
summary and both deselection forms, causing the current firewall to reject.
GREEN must accept that exact valid shape and reject count, summary, duplicate,
overlap, reason, phase and outcome mutations. Run affected firewall/topology
consumers, collection/static gates, standalone verification and independent
review before exactly one new push-triggered run. Never rerun
`0c789bbcdb3c63f4955bb1a27b72847c6fefd398`; do not change marker selection,
skip/outcome policy, Make/workflow, PostgreSQL authority, database execution,
dependencies/locks, production or live boundaries.

### P0-12R34 diagnostic — classify collection invariant group

Hosted run `31900271653` at exact
`120902855323c542428293217161a55fbcda8095` failed closed before artifact
publication with the exact token
`ARTIFACT_FIREWALL_REJECTED LAYOUT PUBLICATION PROJECTION_VALIDATION COLLECTION`.
The retained log SHA-256 is
`90f0029cdf08970f63b6820f39cc4ae41472af16065a6b37b2c0307d577e947f`.
Local reconstruction validates the obvious R33 collection document: exact six
keys, 6,497 collected candidates, 29 disjoint collection-phase deselections,
12 host-coupled and 17 runtime-PostgreSQL reasons, and exact sorted candidate
equality. Do not infer or change the failing invariant from this local success.

Add one closed observational detail beneath exactly
`PUBLICATION PROJECTION_VALIDATION COLLECTION`: `DOCUMENT_SCHEMA`,
`RECORD_SCHEMA`, `REASON_DOMAIN`, `SUMMARY_BINDING`, `CANDIDATE_BINDING`, or
`DESELECTION_BINDING`. Mark the token immediately before each existing
collection invariant group and preserve the prior code, category, stage,
publication substage, and projection-validation substage. Thread only the
closed token through `FinalPublicationError` and CLI output after `COLLECTION`.
Never expose a path, value, digest, validator message, cause, or context.

This packet is diagnostic-only. It must not change collection acceptance,
document or receipt schemas, marker/deselection policy, candidate accounting,
Make, workflow, dependencies/locks, external authority, PostgreSQL/database,
production/runtime, or live behavior. Strict RED/GREEN must cover all six
closed tokens, map one semantic mutation to each first failing invariant group,
preserve all earlier token sequences, keep collection exactly 6,526, and run
focused/affected/static plus fresh standalone verification. Stop for independent
review without push or hosted rerun; never rerun `120902855323c542428293217161a55fbcda8095`.

### P0-12R35 diagnostic — classify collection document-schema predicate

Independent review approved R34 and exact reviewed HEAD
`7018c0d92025ae7786be4b3d7cad667474b0323b` was pushed. Its single authorized
hosted run `31902374455` failed closed with zero artifacts and the exact token
`ARTIFACT_FIREWALL_REJECTED LAYOUT PUBLICATION PROJECTION_VALIDATION COLLECTION DOCUMENT_SCHEMA`.
The retained log `/tmp/p0-12-r34-run-31902374455.log` has SHA-256
`3adf78de2956aad2c8073690dbfd7493fc33cc291edeef277faa0a923aee2952`.
That SHA is terminal and must not be rerun. The token does not identify which
existing document predicate failed and does not authorize inference of an
additional field or a change to accepted collection bytes.

Add one closed observational detail beneath exactly
`PUBLICATION PROJECTION_VALIDATION COLLECTION DOCUMENT_SCHEMA`, in existing
evaluation order: `KEY_SET`, `SCHEMA_VERSION`, `COMPONENT`, `COLLECTION_ONLY`,
`EXIT_STATUS`, or `TESTS_TYPE`. `KEY_SET` begins before the existing object-leaf
read so absent, malformed, or non-object document bytes remain fail-closed with
the first closed document predicate rather than losing the nested diagnostic.
Split the existing conjunction only enough to identify the first rejecting
predicate; preserve every accepted value, rejection code/category/message and
all parent tokens. Thread the closed token through `FinalPublicationError` and
append it in CLI output only after `DOCUMENT_SCHEMA`. Never expose a path,
value, digest, validator message, cause, or context.

Strict RED/GREEN must map a mutation to each of the six tokens, reject unknown
or cross-parent tokens, retain prior R34 outputs, preserve exactly 6,526
collected tests, and run focused, affected, static, and fresh standalone gates.
This packet is diagnostic-only: no collection/schema/policy acceptance change,
Make/workflow change, authority, PostgreSQL/database, dependency/lock,
production/runtime, or live action. Stop for independent review without push,
dispatch, hosted run, or any rerun of
`7018c0d92025ae7786be4b3d7cad667474b0323b`.

### P0-12R36 fix — canonical collection producer bytes

Independent review approved R35 and exact reviewed HEAD
`962d0e1e0b7dd16c22e39e41fdf14aef7f792b5d` was pushed. Its single authorized
hosted run `31903985385` failed closed with zero artifacts and the exact token
`ARTIFACT_FIREWALL_REJECTED LAYOUT PUBLICATION PROJECTION_VALIDATION COLLECTION DOCUMENT_SCHEMA KEY_SET`.
The retained log `/tmp/p0-12-r35-run-31903985385.log` has SHA-256
`1d764a051f9cb9668571b510613cf13fe74db012d72c810cc22ade78874abfe0`.
That SHA is terminal and must not be rerun.

The exact producer/consumer mismatch is now proven. Collection-only output from
`scripts/test_governance_pytest.py` is emitted through `_atomic_json` as
indented JSON plus a trailing newline. The local exact report is 1,726,341 raw
bytes while its canonical compact representation is 1,399,997 bytes; parsed
values are equal but bytes are not. The firewall correctly rejects this before
the key predicate because `_object_leaf` requires exact canonical UTF-8 JSON
bytes. Do not relax, bypass, or special-case the firewall consumer.

Make only collection-only governance reports use compact canonical UTF-8 JSON
bytes with sorted keys, compact separators, and no trailing newline. Preserve
the current pretty JSON plus newline for ordinary execution reports unless a
separate exact contract proves a global migration safe. Keep `_atomic_json`'s
validated directory, retained descriptor, exclusive temporary creation,
fsync, no-follow, no-clobber link, atomic replacement, postcheck and cleanup
semantics unchanged.

Strict RED must exercise the real governance plugin in an existing collected
test and compare collection-only raw bytes to an independently derived compact
canonical encoding. GREEN must also characterize ordinary report bytes as
unchanged and retain the existing no-clobber/custody attack coverage. A real
collect-baseline path must produce canonical raw bytes and the unchanged
firewall must advance through every collection document predicate. Preserve
exactly 6,526 collected tests and run focused, affected, static and fresh
standalone verification. Scope is limited to the producer, existing tests and
an exact topology fixture only if required. No policy/schema/marker, firewall,
Make/workflow, authority, PostgreSQL/database, dependency/lock, production or
live change. Stop for independent review without push, dispatch, hosted run or
any rerun of `962d0e1e0b7dd16c22e39e41fdf14aef7f792b5d`.

### P0-12R37 fix — canonical authoritative governance producer bytes

Independent review approved R36 and exact reviewed HEAD
`e95897647e800b84ef97e3df6ae830ef11685330` was pushed. Its single authorized
hosted run `31905403310` failed closed with zero artifacts and the exact token
`ARTIFACT_FIREWALL_REJECTED LAYOUT PUBLICATION PROJECTION_VALIDATION REMAINDER_GOVERNANCE`.
The retained log `/tmp/p0-12-r36-run-31905403310.log` has SHA-256
`4aaf37bbc1cc7193846504d59878e99aa81bf3405c85065ce3eba4342d85ee96`.
That SHA is terminal and must not be rerun.

The exact producer/consumer mismatch is the same byte contract already proven
for collection-only output, now at an authoritative execution report. The
exact governance runner attaches a successfully parsed
`TEST_GOVERNANCE_CUSTODY_POLICY`, but the R36 producer selects canonical bytes
only for collection-only reports. The firewall correctly requires every
authoritative governance object leaf to equal its compact canonical UTF-8 JSON
encoding. Do not relax or special-case the firewall or topology consumer.

Extend producer canonical mode to reports which are collection-only or which
carry a successfully parsed object custody policy. Preserve ordinary reports
with neither signal byte-for-byte as indented JSON plus one trailing newline.
Malformed or non-object custody policy input must continue to fail before any
report write. Keep the validated directory, retained descriptor, exclusive
temporary creation, fsync, no-follow, no-clobber link, atomic replacement,
postcheck and cleanup transaction unchanged.

Strict RED/GREEN must use existing collected governance tests to prove exact
compact bytes for valid custody reports, unchanged pretty bytes for ordinary
reports, and no output for malformed custody input. Retain existing no-clobber
and descriptor-custody attack coverage. Real closure and remainder governance
production must emit canonical bytes, and the unchanged firewall must advance
beyond `REMAINDER_GOVERNANCE`, ideally through the bounded final projection.
Preserve exactly 6,526 collected tests and run focused, affected, static and
fresh standalone verification. Scope is limited to the producer and existing
tests. No firewall/topology, policy/schema, Make/workflow, authority,
PostgreSQL/database, dependency/lock, production or live change. Stop for
independent review without push, dispatch, hosted run or any rerun of
`e95897647e800b84ef97e3df6ae830ef11685330`.
