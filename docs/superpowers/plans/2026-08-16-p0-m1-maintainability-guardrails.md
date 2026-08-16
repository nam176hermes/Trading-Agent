# P0-M1 — Maintainability Guardrails Implementation Plan

> **Execution requirement for Hermes/Codex:** use `superpowers:using-git-worktrees` for isolation, `superpowers:executing-plans` to execute task-by-task, `superpowers:test-driven-development` for every executable guardrail, and `superpowers:verification-before-completion` before any PASS/COMPLETE claim.

## Goal

Freeze the already-qualified P0 behavior, prevent the large P0 governance modules from absorbing P1/P2 trading responsibilities, add executable maintainability guardrails and characterization coverage, and establish safe extraction boundaries **without changing P0 semantics**.

The intended terminal verdict is:

```text
P0_M1_COMPLETE_NO_EXTRACTION
READY_FOR_P1
```

A code extraction is **not required** to complete P0-M1. If a pure extraction is attempted, it is a separate optional packet and receives stronger qualification.

---

## Verified starting baseline

At plan creation:

```text
repository:
nam176hermes/Trading-Agent

canonical branch:
main

baseline SHA:
e0baa410cdcf0de4344d58ad82fd8a56788f84df
```

P0 is already source-qualified and promoted. P0-M1 must not reinterpret or reopen P0 qualification.

Current high-priority maintainability hotspots include:

```text
scripts/t_g03_capability_topology.py    362,662 bytes
scripts/check_artifact_firewall.py      141,810 bytes
scripts/check_p0_ci_closure.py           43,300 bytes
```

Existing topology characterization is already substantial:

```text
tests/governance/test_t_g03_capability_topology.py
```

and currently includes characterization of the final semantic projection. P0-M1 must reuse existing proof instead of duplicating it into another giant test file.

The current Makefile already separates portable and host authority lanes and contains:

```text
ci
ci-portable
ci-host-authority
ci-portable-topology
check-test-governance-topology
artifact-firewall-check
check-p0-ci-closure
```

P0-M1 should add one small portable guardrail target; it should not redesign the CI topology.

---

# 1. Architectural decision

## 1.1 P0-M1 is a guardrail packet, not a refactor packet

Mandatory work:

```text
record hotspots
        ↓
document responsibilities
        ↓
pin executable growth/import boundaries
        ↓
index existing characterization
        ↓
add only missing meta-characterization
        ↓
wire guardrail into portable CI
        ↓
clean-clone verification
        ↓
fresh adversarial review
        ↓
READY_FOR_P1
```

Optional work:

```text
at most one pure extraction
```

Only if a separate GO decision proves it is lower risk than leaving the code in place.

## 1.2 Frozen-for-growth does not mean frozen-for-bugfixes

For these files:

```text
scripts/t_g03_capability_topology.py
scripts/check_artifact_firewall.py
```

policy becomes:

```text
bug/security fixes:               allowed
responsibility-neutral shrinking: allowed
new P1/P2 responsibilities:       forbidden
substantial net growth:           forbidden unless explicitly reviewed
```

For:

```text
scripts/check_p0_ci_closure.py
```

policy becomes:

```text
MONITOR
```

They are not automatically refactored during P0-M1.

## 1.3 “Touch it, shrink it”

After P0-M1:

```text
if a later feature does not need a frozen P0 file
    → do not touch it

if a later feature needs a new responsibility
    → implement that responsibility in a new module
    → expose only a narrow adapter to the P0 layer if genuinely required

if a bugfix must touch a frozen P0 file
    → behavior fix and extraction are separate commits
    → do not mix semantic change with structural extraction
```

---

# 2. P0-M1 invariants

## M1-I01 — P0 semantic behavior remains unchanged

P0-M1 must not intentionally change:

```text
CLI commands or arguments
error codes
receipt schema versions
receipt required keys
evidence paths
capability classifications
external authority classifications
PASS / FAIL / DEFERRED semantics
validation-date authority
semantic-result projection meaning
native append-only publication semantics
artifact no-clobber semantics
portable / host lane reachability
production/live authorization
```

## M1-I02 — P0-M1 cannot become another large governance subsystem

New guardrail implementation should remain deliberately small.

Recommended budget:

```text
scripts/check_p0_maintainability.py       <= 500 logical lines
tests/governance/test_p0_m1_maintainability.py
                                          <= 800 logical lines
```

If the checker needs significantly more code than that, stop and simplify the policy.

## M1-I03 — No P1 business logic inside P0 governance scripts

The P0 governance layer must not become the home for:

```text
real Nautilus execution
real Nautilus reconciliation
strategy logic
market-data ingestion
portfolio optimization
Qlib
FinRL-X
TradingAgents
Freqtrade-derived runtime logic
broker/exchange execution
```

## M1-I04 — Characterization before extraction

No production P0 extraction may start until the relevant current behavior has explicit tests.

## M1-I05 — No mass formatting

Do not run whole-file format/reorder tools over the hotspot modules merely to make extraction easier. That destroys reviewability.

## M1-I06 — Portable source gate remains the primary source gate

P0-M1 must stay source-safe:

```text
no broker/exchange
no production DB mutation
no deployment
no service restart
no scheduler mutation
no live credentials
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_ENABLED=false
```

---

# 3. Scope

## In scope

```text
maintainability inventory
hotspot policy
responsibility boundaries
file-size/growth guardrails
first-party import drift guard
critical-characterization index
missing meta-characterization tests
P1/P0 boundary tests
portable CI integration
documentation
clean-clone verification
fresh review
optional one-module pure extraction assessment
```

## Explicitly out of scope

```text
full decomposition of t_g03_capability_topology.py
full decomposition of check_artifact_firewall.py
P0 host qualification
release signing/immutability cleanup
authorization issue cleanup
real Nautilus runtime
market-data platform
Qlib
FinRL-X
TradingAgents
Freqtrade integration
production activation
live trading
receipt v2 redesign
filesystem publication redesign
schema migrations
renaming public CLI contracts
large package moves
```

---

# 4. Planned repository additions

Mandatory new files:

```text
docs/superpowers/plans/2026-08-16-p0-m1-maintainability-guardrails.md
docs/implementation/p0-maintainability-hotspots.json
docs/implementation/p0-maintainability-boundaries.md
docs/implementation/p0-m1-characterization-index.json
docs/implementation/p0-m1-extraction-assessment.md

scripts/check_p0_maintainability.py

tests/governance/test_p0_m1_maintainability.py
```

Expected modified files:

```text
Makefile
README.md                        # minimal note only, if useful
```

Existing tests may be extended only if a missing characterization is better located there:

```text
tests/governance/test_t_g03_capability_topology.py
tests/governance/test_t_g03f_validation_date.py
tests/governance/test_t_g03_external_authority_v2.py
```

Do not create another giant topology test module if the existing test already proves the invariant.

---

# 5. Task dependency graph

```text
M1-00  Freeze baseline + isolated worktree
   ↓
M1-01  Inventory hotspots
   ↓
M1-02  Executable maintainability checker
   ↓
M1-03  Responsibility-boundary contract
   ↓
M1-04  Characterization index
   ↓
M1-05  Fill only missing characterization gaps
   ↓
M1-06  P1 leakage / accidental-authority guard
   ↓
M1-07  Wire into ci-portable
   ↓
M1-08  Extraction GO/NO-GO assessment
   ↓
M1-09  Clean-clone verification
   ↓
M1-10  Fresh adversarial review
   ↓
P0_M1_COMPLETE_NO_EXTRACTION
   ↓
START P1
```

Optional after M1-10:

```text
P0-M1-E01  One pure extraction
```

It is not on the critical path to P1.

---

# Task M1-00 — Freeze the baseline and create an isolated worktree

**Owner:** Hermes  
**Worker:** Codex Terra medium  
**Reviewer:** Sol high

## Files

Create:

```text
docs/superpowers/plans/2026-08-16-p0-m1-maintainability-guardrails.md
```

## Step 1 — Fetch and verify exact current main

```bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git fetch --prune origin main

BASELINE_SHA="e0baa410cdcf0de4344d58ad82fd8a56788f84df"

CURRENT_MAIN="$(git rev-parse origin/main)"
printf 'origin/main=%s\n' "$CURRENT_MAIN"

test "$CURRENT_MAIN" = "$BASELINE_SHA"
```

### STOP condition

If `origin/main` no longer equals the baseline SHA:

```text
STOP
do not branch from stale P0
inspect intervening commits
regenerate hotspot measurements
rebase/rewrite this plan against the new main
```

Do not silently replace the SHA inside the plan.

## Step 2 — Verify working tree cleanliness

```bash
git status --short
git diff --quiet
git diff --cached --quiet
```

Expected:

```text
no tracked changes
```

## Step 3 — Create dedicated worktree

```bash
git worktree add \
  ../trading-agent-p0-m1 \
  -b p0-m1/maintainability-guardrails \
  "$BASELINE_SHA"

cd ../trading-agent-p0-m1

test "$(git rev-parse HEAD)" = "$BASELINE_SHA"
git status --short
```

## Step 4 — Save this plan

Save the exact plan as:

```text
docs/superpowers/plans/2026-08-16-p0-m1-maintainability-guardrails.md
```

## Step 5 — Commit plan only

```bash
git add -- \
  docs/superpowers/plans/2026-08-16-p0-m1-maintainability-guardrails.md

git commit -m "docs(p0-m1): add maintainability guardrails plan"
```

### M1-00 PASS

```text
isolated worktree
clean baseline ancestry
no source semantics changed
```

---

# Task M1-01 — Create the machine-readable hotspot inventory

**Worker:** Terra medium  
**Reviewer:** Sol high

## Files

Create:

```text
docs/implementation/p0-maintainability-hotspots.json
tests/governance/test_p0_m1_maintainability.py
```

## Step 1 — Write the failing test first

The manifest must use strict keys.

Required logical shape:

```json
{
  "schema_version": "p0-maintainability-hotspots/v1",
  "baseline_sha": "e0baa410cdcf0de4344d58ad82fd8a56788f84df",
  "hotspots": [
    {
      "path": "scripts/t_g03_capability_topology.py",
      "status": "FROZEN_FOR_GROWTH",
      "baseline_bytes": 362662,
      "max_net_growth_bytes": 0,
      "responsibility_id": "P0_CAPABILITY_TOPOLOGY"
    },
    {
      "path": "scripts/check_artifact_firewall.py",
      "status": "FROZEN_FOR_GROWTH",
      "baseline_bytes": 141810,
      "max_net_growth_bytes": 0,
      "responsibility_id": "P0_ARTIFACT_FIREWALL"
    },
    {
      "path": "scripts/check_p0_ci_closure.py",
      "status": "MONITOR",
      "baseline_bytes": 43300,
      "responsibility_id": "P0_CLOSURE_CHECKER"
    }
  ]
}
```

Test rules:

```text
strict schema
unique paths
valid status enum
positive baseline sizes
existing file
regular file
not symlink
path stays under repository
baseline SHA exists in history
frozen files have max_net_growth_bytes
monitor files do not need a hard size ceiling
```

## Step 2 — Run failing test

```bash
uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py
```

Expected:

```text
FAIL because manifest does not exist
```

## Step 3 — Measure exact baseline sizes

Use Git object bytes where possible, not editor-rewritten files:

```bash
for path in \
  scripts/t_g03_capability_topology.py \
  scripts/check_artifact_firewall.py \
  scripts/check_p0_ci_closure.py
do
  printf '%s ' "$path"
  git cat-file -s "${BASELINE_SHA}:${path}"
done
```

Pin those values in the manifest.

## Step 4 — Add the manifest

No automatic timestamp field.

Do not include current wall-clock time in the policy.

## Step 5 — Re-run test

```bash
uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py
```

Expected:

```text
PASS
```

## Step 6 — Commit

```bash
git add -- \
  docs/implementation/p0-maintainability-hotspots.json \
  tests/governance/test_p0_m1_maintainability.py

git commit -m "test(p0-m1): pin P0 maintainability hotspots"
```

---

# Task M1-02 — Build the small executable maintainability checker

**Worker:** Terra medium  
**Reviewer:** Sol high

## Files

Create:

```text
scripts/check_p0_maintainability.py
```

Modify:

```text
tests/governance/test_p0_m1_maintainability.py
Makefile
```

## Checker responsibilities

The checker must do only four things:

```text
1. validate hotspot manifest
2. verify hotspot path custody
3. enforce frozen-file net growth
4. enforce first-party import-boundary drift
```

Do not turn it into a generic architecture framework.

## Step 1 — Add failing checker tests

Required tests:

### Manifest/custody

```text
missing manifest                → fail
unknown key                     → fail
duplicate hotspot              → fail
absolute path                   → fail
path traversal                  → fail
symlink hotspot                 → fail
non-regular hotspot             → fail
baseline SHA absent             → fail
baseline object/path absent     → fail
```

### Frozen growth

For a temporary repository fixture:

```text
same size                       → pass
smaller                         → pass
1 byte above ceiling            → fail
MONITOR file growth             → report, not fail solely by size
```

The checker must compare current source size to policy, but also report:

```text
current bytes
baseline bytes
delta bytes
status
```

### No automatic policy rewrite

There must be no `--update`, `--accept-current`, or equivalent mode.

Updating the hotspot manifest must remain a source-reviewed change.

## Step 2 — Implement checker

Recommended CLI:

```bash
uv run python scripts/check_p0_maintainability.py \
  --manifest docs/implementation/p0-maintainability-hotspots.json \
  --root .
```

Expected success output should be compact and deterministic:

```text
P0_MAINTAINABILITY_GUARD_PASS
```

Diagnostics go to stderr.

## Step 3 — Keep implementation bounded

Run:

```bash
python - <<'PY'
from pathlib import Path
p = Path("scripts/check_p0_maintainability.py")
print(sum(1 for _ in p.open()))
PY
```

Target:

```text
<= 500 logical/source lines
```

If it grows well beyond this, simplify rather than building more governance machinery.

## Step 4 — Add Make target

Add to `.PHONY`:

```text
check-p0-maintainability
```

Add:

```make
check-p0-maintainability:
	$(PYTHON) scripts/check_p0_maintainability.py \
		--manifest docs/implementation/p0-maintainability-hotspots.json \
		--root "$(CURDIR)"
```

Do not add it to `ci-portable` yet. That occurs only after all characterization is ready.

## Step 5 — Run

```bash
uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py

make check-p0-maintainability
```

Expected:

```text
PASS
P0_MAINTAINABILITY_GUARD_PASS
```

## Step 6 — Commit

```bash
git add -- \
  scripts/check_p0_maintainability.py \
  tests/governance/test_p0_m1_maintainability.py \
  Makefile

git commit -m "feat(p0-m1): enforce P0 hotspot growth guard"
```

---

# Task M1-03 — Define and enforce responsibility boundaries

**Worker:** Terra medium  
**Reviewer:** Sol xhigh for authority boundary

## Files

Create:

```text
docs/implementation/p0-maintainability-boundaries.md
```

Modify:

```text
docs/implementation/p0-maintainability-hotspots.json
scripts/check_p0_maintainability.py
tests/governance/test_p0_m1_maintainability.py
```

## Step 1 — Document exact responsibilities

### `scripts/t_g03_capability_topology.py`

Owns:

```text
P0 test-governance topology
portable/native/external lane orchestration
capability/authority classification
P0 receipts and their validation
P0 native candidate acceptance mechanics
P0 semantic-result projection
P0 topology evidence aggregation
```

Does not own:

```text
strategy algorithms
market-data ingestion
real Nautilus backtest execution
real Nautilus paper execution
broker/exchange APIs
portfolio optimizer
LLM reasoning
quant training
```

### `scripts/check_artifact_firewall.py`

Owns:

```text
P0 evidence-tree validation
manifest/checksum validation
artifact path/custody checks
secret-sensitive evidence screening
portable evidence publication validation
```

Does not own:

```text
trading-domain validation
market data
strategy
order lifecycle
Nautilus runtime behavior
```

### `scripts/check_p0_ci_closure.py`

Owns:

```text
historical P0 closure/qualification proof
```

It must not become a generic P1/P2 qualification engine.

## Step 2 — Pin first-party import baseline

At implementation time, parse each hotspot using Python AST and collect repository-local import roots.

Store the baseline set in the hotspot manifest, e.g.:

```json
"baseline_first_party_imports": [
  "scripts.materialize_sealed_uv_exec",
  "..."
]
```

Do not guess these values manually.

## Step 3 — Enforce import drift

For frozen hotspots:

```text
new standard-library import           allowed
new already-approved third-party util review normally
new repository-local import           FAIL unless manifest reviewed
new apps/services/engine runtime import FAIL
```

The checker should identify a repository-local import by matching top-level repository packages/directories, not by a brittle string keyword scan.

At minimum guard these roots:

```text
apps
services
packages
engines
legacy
native
ops
```

Existing imports remain grandfathered exactly as baseline.

A future new first-party import requires an explicit update to the hotspot policy and code review.

## Step 4 — Add negative tests

Tests must prove a frozen hotspot cannot newly import synthetic modules like:

```text
services.market_data
services.quant_research
services.agent_reasoning
packages.portfolio_strategy
engines.nautilus.runtime
```

without policy review.

## Step 5 — Run

```bash
uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py

make check-p0-maintainability
```

## Step 6 — Commit

```bash
git add -- \
  docs/implementation/p0-maintainability-boundaries.md \
  docs/implementation/p0-maintainability-hotspots.json \
  scripts/check_p0_maintainability.py \
  tests/governance/test_p0_m1_maintainability.py

git commit -m "feat(p0-m1): freeze P0 responsibility boundaries"
```

---

# Task M1-04 — Build a characterization index instead of duplicating tests

**Worker:** Sol high  
**Reviewer:** Sol high

## Files

Create:

```text
docs/implementation/p0-m1-characterization-index.json
```

Modify:

```text
tests/governance/test_p0_m1_maintainability.py
```

## Goal

The repository already has a very large topology test suite. P0-M1 should not create another 100k test file.

The characterization index records which existing exact test node proves each high-risk invariant.

## Step 1 — Discover exact existing node IDs

Run:

```bash
uv run pytest --collect-only -q tests/governance \
  > /tmp/p0-m1-governance-nodes.txt
```

Also collect tests that directly exercise artifact-firewall behavior:

```bash
rg -n \
  'check_artifact_firewall|artifact firewall|validate_published|publish-error' \
  tests
```

Do not invent a test node ID.

## Step 2 — Required characterization categories

The index must bind exact test nodes for:

```text
C01 final semantic projection ignores run-custody-only identity
C02 semantic projection changes when governed meaning changes
C03 sealed foundation validation date is authoritative
C04 CLI/env date override fails closed
C05 portable defect remains fail-closed
C06 native absent authority is deferred, not passed
C07 native present-invalid authority fails
C08 external absent authority is deferred
C09 external present-invalid authority fails
C10 native evidence publication is append-only/no rollback
C11 canonical acceptance is no-clobber/create-if-absent
C12 wrong head/context/inventory binding fails
C13 artifact manifest/checksum mismatch fails
C14 symlink/path substitution fails
C15 secret-bearing evidence fails
C16 portable lane cannot imply host qualification
```

Known existing source includes:

```text
tests/governance/test_t_g03_capability_topology.py
tests/governance/test_t_g03_external_authority_v2.py
tests/governance/test_t_g03f_validation_date.py
```

Use exact collected nodes.

## Step 3 — Strict index schema

Example logical shape:

```json
{
  "schema_version": "p0-m1-characterization-index/v1",
  "baseline_sha": "...",
  "contracts": [
    {
      "id": "C01",
      "description": "...",
      "test_node_ids": [
        "tests/governance/test_t_g03_capability_topology.py::test_..."
      ]
    }
  ]
}
```

## Step 4 — Checker test

`test_p0_m1_maintainability.py` must validate:

```text
all required IDs present
unique IDs
at least one node per ID
every referenced file exists
every exact node is collected by pytest
no wildcard node
no xfail-only placeholder used as sole proof
```

Do not shell-interpolate node IDs.

## Step 5 — Run

```bash
uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py
```

Then:

```bash
uv run pytest -q \
  $(python scripts/check_p0_maintainability.py \
      --manifest docs/implementation/p0-maintainability-hotspots.json \
      --characterization-index docs/implementation/p0-m1-characterization-index.json \
      --print-characterization-nodes)
```

If adding `--print-characterization-nodes` makes the checker too complex, create a tiny separate safe helper or invoke nodes from a checked JSON reader in Make. Do not bloat the main checker.

## Step 6 — Commit

```bash
git add -- \
  docs/implementation/p0-m1-characterization-index.json \
  tests/governance/test_p0_m1_maintainability.py \
  scripts/check_p0_maintainability.py

git commit -m "test(p0-m1): pin P0 characterization contract"
```

---

# Task M1-05 — Fill only genuine characterization gaps

**Worker:** Sol high  
**Reviewer:** Sol xhigh

## Rule

First evaluate the index.

If all C01–C16 already have adequate existing proof:

```text
DO NOT ADD DUPLICATE CHARACTERIZATION TESTS
```

Record:

```text
CHARACTERIZATION_GAPS=0
```

If a gap exists, add the smallest possible test to the most relevant existing file.

Examples:

```text
semantic projection gap
→ tests/governance/test_t_g03_capability_topology.py

date authority gap
→ tests/governance/test_t_g03f_validation_date.py

external authority gap
→ tests/governance/test_t_g03_external_authority_v2.py
```

## Strict prohibition

P0-M1 does not modify production P0 code merely because a new characterization test fails.

If a test shows real current-P0 behavior violates the already-qualified contract:

```text
STOP P0-M1
classify as new P0 defect
create separate bugfix packet
do not hide it under maintainability work
```

## Run all indexed characterization

```bash
uv run pytest -q \
  tests/governance/test_t_g03_capability_topology.py \
  tests/governance/test_t_g03_external_authority_v2.py \
  tests/governance/test_t_g03f_validation_date.py
```

Add other exact existing files discovered by the index.

## Commit only if tests were actually added

Example:

```bash
git commit -m "test(p0-m1): close P0 characterization gaps"
```

If no gaps exist, there should be no empty/no-op commit.

---

# Task M1-06 — Add the P1 leakage and accidental-authority guard

**Worker:** Sol high  
**Reviewer:** Sol xhigh

## Files

Modify:

```text
tests/governance/test_p0_m1_maintainability.py
docs/implementation/p0-maintainability-boundaries.md
```

Optionally add a small dedicated test only if clarity improves:

```text
tests/governance/test_p0_m1_p1_boundary.py
```

## Required assertions

### A. P0 frozen hotspots cannot become a P1 implementation surface

Tests should synthesize import drift and ensure the maintainability checker rejects it.

### B. `make ci` remains source-safe

Parse Makefile structure sufficiently to prove:

```text
ci → ci-portable
```

and no dependency chain from:

```text
ci
ci-portable
```

reaches:

```text
ci-host-authority
```

Avoid executing host targets as part of this structural test.

### C. No live capability in portable gate

Preserve:

```text
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_ENABLED=false
```

in portable CI.

If existing workflow governance tests already prove this, reference them in the characterization index rather than duplicate logic.

### D. P1 real Nautilus must live outside the frozen P0 topology

Document intended future boundary:

```text
P1 real runtime
    ↓ narrow capability/report interface
P0 governance observes facts

P0 governance
    ✗ does not implement execution
```

## Run

```bash
uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py
```

plus any existing Make/workflow governance tests identified during discovery.

## Commit

```bash
git add -- \
  tests/governance/test_p0_m1_maintainability.py \
  docs/implementation/p0-maintainability-boundaries.md

git commit -m "test(p0-m1): prevent P1 authority leakage into P0"
```

---

# Task M1-07 — Wire the maintainability guard into portable CI

**Worker:** Terra medium  
**Reviewer:** Sol high

## Files

Modify:

```text
Makefile
README.md                       # minimal, optional
```

No workflow edit should be required if Foundation already invokes `make ci-portable`.

## Step 1 — Add the target to the source-safe gate

Add:

```text
check-p0-maintainability
```

to a source-safe prerequisite path executed exactly once by `ci-portable`.

Do not accidentally make `ci-portable` call the full characterization suite twice if it is already included in the normal root tests.

The checker itself should run once.

## Step 2 — Add reachability test

Ensure:

```text
make ci-portable
    → check-p0-maintainability
```

and:

```text
make ci-portable
    ↛ ci-host-authority
```

## Step 3 — Minimal README note

If useful, add a small section:

```text
P0 governance files are frozen for growth.
New trading runtime responsibilities belong in P1+ modules.
See docs/implementation/p0-maintainability-boundaries.md.
```

Do not turn README into a maintainability specification.

## Step 4 — Run focused source checks

```bash
make check-p0-maintainability

uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py
```

## Step 5 — Run complete portable gate

```bash
make ci-portable NONINTERACTIVE=1
```

Expected:

```text
PASS
```

No host qualification is required.

## Step 6 — Verify hotspot sizes

```bash
python - <<'PY'
from pathlib import Path
for path in [
    "scripts/t_g03_capability_topology.py",
    "scripts/check_artifact_firewall.py",
    "scripts/check_p0_ci_closure.py",
    "scripts/build_runtime_capability_report.py",
]:
    p = Path(path)
    print(path, p.stat().st_size)
PY
```

Expected for the two frozen files:

```text
no net growth
```

Prefer exact unchanged bytes because P0-M1 should not touch them.

## Step 7 — Commit

```bash
git add -- Makefile README.md

git commit -m "ci(p0-m1): enforce maintainability guard in portable gate"
```

Omit README from staging if it was not changed.

---

# Task M1-08 — Formal GO/NO-GO assessment for optional extraction

**Worker:** Sol high  
**Reviewer:** Sol xhigh

## Files

Create:

```text
docs/implementation/p0-m1-extraction-assessment.md
```

## Purpose

Do not extract code merely to make file-size metrics prettier.

Every proposed extraction must pass all criteria.

## Candidate eligibility criteria

A candidate is eligible only if all are true:

```text
[ ] pure deterministic computation/data
[ ] no filesystem mutation
[ ] no descriptor/inode custody
[ ] no subprocess
[ ] no network
[ ] no environment authority
[ ] no wall-clock authority
[ ] no receipt publication
[ ] no capability classification
[ ] no PASS/FAIL/DEFERRED decision
[ ] no schema/version/path/error-code change
[ ] no semantic-result meaning change
[ ] existing characterization already covers it
[ ] <= ~500 moved logical lines
[ ] extraction can preserve old import/API surface via shim if required
```

## Candidate types that may be considered

Examples only:

```text
pure canonical JSON helpers
pure digest helpers
immutable constants
pure schema field sets
pure dataclasses with no authority behavior
```

## Explicitly forbidden during P0-M1

Do not extract:

```text
native candidate publication
path custody
TOCTOU-sensitive code
descriptor lifecycle
authority probes
external authority checks
classification transitions
receipt acceptance
validation-date authority
semantic policy decisions
```

## Required assessment verdict

Choose exactly one:

```text
NO_EXTRACTION_REQUIRED
```

or:

```text
OPTIONAL_EXTRACTION_APPROVED:
<exact symbol set>
<source file>
<target module>
<characterization nodes>
```

### Recommended default

Unless there is an unusually clean pure component:

```text
NO_EXTRACTION_REQUIRED
```

This is the preferred P0-M1 outcome.

## Commit

```bash
git add -- docs/implementation/p0-m1-extraction-assessment.md

git commit -m "docs(p0-m1): record extraction go-no-go assessment"
```

---

# Task M1-09 — Clean-clone/worktree verification

**Owner:** Hermes  
**Worker:** Sol high

## Step 1 — Verify diff scope

```bash
git diff --name-status \
  e0baa410cdcf0de4344d58ad82fd8a56788f84df...HEAD
```

Expected mandatory change set should be limited to:

```text
docs/superpowers/plans/...
docs/implementation/p0-maintainability-hotspots.json
docs/implementation/p0-maintainability-boundaries.md
docs/implementation/p0-m1-characterization-index.json
docs/implementation/p0-m1-extraction-assessment.md
scripts/check_p0_maintainability.py
tests/governance/test_p0_m1_maintainability.py
Makefile
README.md                              optional
existing characterization test files optional only when filling real gaps
```

## Immediate FAIL if mandatory packet modified

Without a separate defect or optional-extraction packet, fail if diff includes:

```text
scripts/t_g03_capability_topology.py
scripts/check_artifact_firewall.py
scripts/check_p0_ci_closure.py
receipt/publication implementation
.github/workflows/foundation.yml
.github/workflows/host-authority.yml
production deployment configuration
live-trading configuration
```

## Step 2 — Create clean verification worktree

```bash
git worktree add \
  ../trading-agent-p0-m1-verify \
  HEAD

cd ../trading-agent-p0-m1-verify

git status --short
```

Expected:

```text
clean
```

## Step 3 — Locked dependency setup

```bash
uv sync --frozen

(
  cd apps/dashboard
  npm ci
)
```

## Step 4 — Run maintainability guard

```bash
make check-p0-maintainability
```

Expected:

```text
P0_MAINTAINABILITY_GUARD_PASS
```

## Step 5 — Run focused governance

```bash
uv run pytest -q \
  tests/governance/test_p0_m1_maintainability.py \
  tests/governance/test_t_g03_capability_topology.py \
  tests/governance/test_t_g03_external_authority_v2.py \
  tests/governance/test_t_g03f_validation_date.py
```

Include other exact files from the characterization index.

## Step 6 — Run full portable CI

```bash
make ci-portable NONINTERACTIVE=1
```

Expected:

```text
PASS
```

## Step 7 — Verify no source semantic hotspot changes

```bash
for path in \
  scripts/t_g03_capability_topology.py \
  scripts/check_artifact_firewall.py \
  scripts/check_p0_ci_closure.py
do
  test \
    "$(git rev-parse "e0baa410cdcf0de4344d58ad82fd8a56788f84df:${path}")" = \
    "$(git rev-parse "HEAD:${path}")"
done
```

For the mandatory no-extraction packet, expected:

```text
exact Git blobs unchanged
```

This is stronger than a size comparison.

---

# Task M1-10 — Fresh adversarial review

**Reviewer:** Codex Sol xhigh, fresh context  
**Implementer:** none during review unless a finding is returned

## Review input

Reviewer must receive:

```text
BASE:
e0baa410cdcf0de4344d58ad82fd8a56788f84df

HEAD:
current P0-M1 candidate

PLAN:
docs/superpowers/plans/2026-08-16-p0-m1-maintainability-guardrails.md

DIFF:
base...HEAD

HOTSPOT POLICY:
docs/implementation/p0-maintainability-hotspots.json

BOUNDARIES:
docs/implementation/p0-maintainability-boundaries.md

CHARACTERIZATION INDEX:
docs/implementation/p0-m1-characterization-index.json

EXTRACTION ASSESSMENT:
docs/implementation/p0-m1-extraction-assessment.md

CI EVIDENCE:
make ci-portable result
```

## Reviewer must answer

```text
VERDICT: PASS | FAIL

P0_SEMANTIC_CHANGE:
NO | YES

HOTSPOT_GROWTH_GUARD:
PASS | FAIL

RESPONSIBILITY_BOUNDARY:
PASS | FAIL

CHARACTERIZATION_COVERAGE:
PASS | FAIL

P1_LEAKAGE_GUARD:
PASS | FAIL

PORTABLE_CI:
PASS | FAIL

PRODUCTION_LIVE_BOUNDARY:
PASS | FAIL

OPTIONAL_EXTRACTION:
NOT_REQUIRED | APPROVED | REJECTED

READY_FOR_P1:
YES | NO
```

Every blocking finding must include:

```text
severity
file + exact line
violated invariant
concrete failure path
minimal remediation
adversarial regression test
```

## Mandatory PASS condition

For the default packet:

```text
P0_SEMANTIC_CHANGE: NO
READY_FOR_P1: YES
```

If reviewer finds a semantic behavior change in P0:

```text
P0_M1_NOT_READY
```

and the change must either be removed or moved to a separately qualified defect packet.

---

# 6. Optional packet P0-M1-E01 — One pure extraction

## Default recommendation

Skip this packet and start P1.

Run it only if M1-08 explicitly returns:

```text
OPTIONAL_EXTRACTION_APPROVED
```

## Why it is separate

The mandatory P0-M1 packet can prove P0 hotspot Git blobs unchanged.

Any extraction necessarily changes a previously qualified P0 implementation blob and therefore deserves stronger qualification.

## Rules

### One source responsibility only

At most:

```text
one source file
one pure symbol family
one target helper module
```

Do not decompose the whole file.

### Move-only first commit

First extraction commit:

```text
move exact pure code
preserve public names/import shims
no semantic edit
no cleanup
no renaming spree
```

Second cleanup commit is allowed only after equivalence proof.

### Characterization before and after

Run exact indexed nodes before extraction and after extraction.

### Full portable CI

```bash
make ci-portable NONINTERACTIVE=1
```

### Strong reproducibility proof

Because a qualified P0 implementation was changed, recommended:

```text
two independent portable CI executions
same HEAD
same source tree
same semantic result
```

### Fresh Sol xhigh review

Reviewer must trace the moved symbols and prove:

```text
no authority transition changed
no error behavior changed
no schema changed
no evidence changed
```

### Fail behavior

Any semantic discrepancy:

```text
REVERT OPTIONAL EXTRACTION
retain P0-M1 mandatory guardrails
proceed to P1 without extraction
```

P0-M1 must not be held hostage by optional cleanup.

---

# 7. Suggested P0-M1 commit sequence

Mandatory sequence:

```text
1. docs(p0-m1): add maintainability guardrails plan

2. test(p0-m1): pin P0 maintainability hotspots

3. feat(p0-m1): enforce P0 hotspot growth guard

4. feat(p0-m1): freeze P0 responsibility boundaries

5. test(p0-m1): pin P0 characterization contract

6. test(p0-m1): close P0 characterization gaps
   # only if gaps actually exist

7. test(p0-m1): prevent P1 authority leakage into P0

8. ci(p0-m1): enforce maintainability guard in portable gate

9. docs(p0-m1): record extraction go-no-go assessment
```

Do not squash everything into one giant implementation commit during development. Review can later decide merge strategy while preserving exact qualification requirements.

---

# 8. Model routing

## Hermes orchestrator

```text
model: gpt-5.6-sol
reasoning: high
```

Responsibilities:

```text
task ordering
worktree isolation
no overlapping edits
evidence collection
stop conditions
review packets
```

## Codex workers

| Task | Model |
|---|---|
| M1-00 | Terra medium |
| M1-01 | Terra medium |
| M1-02 | Terra medium |
| M1-03 | Terra medium implementation + Sol xhigh review |
| M1-04 | Sol high |
| M1-05 | Sol high + Sol xhigh review |
| M1-06 | Sol high + Sol xhigh review |
| M1-07 | Terra medium |
| M1-08 | Sol high |
| M1-09 | Sol high |
| M1-10 | **Sol xhigh fresh context** |
| Optional M1-E01 | Terra/Sol high implementer, Sol xhigh reviewer |

---

# 9. Worker output contract

Every worker returns:

```text
TASK:
STATUS: COMPLETE | PARTIAL | BLOCKED

BASE SHA:
HEAD SHA:

FILES CHANGED:
- ...

PRODUCTION P0 HOTSPOT BLOBS CHANGED:
YES | NO

TESTS ADDED/UPDATED:
- ...

COMMANDS RUN:
- command
  exit code
  concise result

P0 SEMANTICS:
UNCHANGED | CHANGED | UNKNOWN

HOTSPOT DELTAS:
- path
- baseline bytes
- current bytes
- delta

INVARIANTS PRESERVED:
- ...

UNRESOLVED:
- ...

NEXT SAFE TASK:
- ...
```

If:

```text
P0 SEMANTICS = CHANGED or UNKNOWN
```

Hermes must stop progression to the next task and request review/classification.

---

# 10. P0-M1 acceptance criteria

Mandatory completion checklist:

```text
[ ] Baseline main SHA verified
[ ] Isolated worktree used
[ ] Hotspot manifest committed
[ ] Exact baseline byte sizes pinned
[ ] t_g03 marked FROZEN_FOR_GROWTH
[ ] artifact firewall marked FROZEN_FOR_GROWTH
[ ] closure checker marked MONITOR
[ ] Maintainability checker strict and small
[ ] Frozen-file net growth enforced
[ ] First-party import drift enforced
[ ] No automatic policy rewrite mode
[ ] Responsibility boundaries documented
[ ] Characterization index uses exact collected test nodes
[ ] C01-C16 all have proof
[ ] Missing gaps filled only where necessary
[ ] P1 leakage guard PASS
[ ] `ci-portable` invokes maintainability guard
[ ] `ci-portable` still cannot reach host-authority lane
[ ] Full `make ci-portable` PASS
[ ] Mandatory packet leaves t_g03 Git blob unchanged
[ ] Mandatory packet leaves artifact-firewall Git blob unchanged
[ ] Mandatory packet leaves P0 closure checker Git blob unchanged
[ ] Production/live unchanged and unavailable
[ ] Extraction assessment complete
[ ] Fresh Sol xhigh review PASS
[ ] READY_FOR_P1 = YES
```

---

# 11. Final verdicts

Use exactly one.

## Preferred

```text
P0_M1_COMPLETE_NO_EXTRACTION
READY_FOR_P1
```

Meaning:

```text
P0 semantics frozen
maintainability guardrails active
hotspots protected from growth
characterization indexed
P1 boundary enforced
no risky P0 refactor performed
```

## Optional extraction also completed

```text
P0_M1_COMPLETE_WITH_BOUNDED_EXTRACTION
READY_FOR_P1
```

Only after the optional extraction receives its stronger verification.

## Not ready

```text
P0_M1_NOT_READY
```

Use if any of these remain:

```text
maintainability checker can be bypassed
frozen hotspot grows unexpectedly
new first-party responsibility leaks into P0
characterization gap remains
portable CI fails
P0 semantics changed
production/live boundary changed
final reviewer fails
```

---

# 12. What P0-M1 must accomplish strategically

After P0-M1, development should behave like this:

```text
P0
├── stable
├── qualified
├── guarded
└── grows only for bug/security fixes

P1
├── new real Nautilus runtime modules
├── new execution adapters
├── new reconciliation modules
└── narrow interfaces back to existing contracts

P2+
├── market data
├── strategies
├── quant research
├── portfolio intelligence
└── agent reasoning
```

P0-M1 is successful if future P1/P2 work naturally creates **new cohesive modules** instead of adding hundreds of lines to the P0 governance monoliths.

---

# 13. Immediate follow-up after P0-M1

Do not start a deep refactor.

Proceed directly to:

```text
P1 — Real Nautilus Engine Vertical Slice
```

Recommended initial P1 vertical slice remains:

```text
BTC/USDT spot
1h historical bars
long/flat
no leverage
deterministic baseline strategy
TargetPortfolio
canonical risk
real Nautilus backtest
paper runtime
orders/fills/fees
event ledger
portfolio replay
restart/reconciliation
live forbidden
```

The next **deep maintainability checkpoint** should occur after P1 vertical slice completion, before broad P2 expansion. At that point the system will have real runtime use cases to guide extraction boundaries instead of refactoring by guesswork.
