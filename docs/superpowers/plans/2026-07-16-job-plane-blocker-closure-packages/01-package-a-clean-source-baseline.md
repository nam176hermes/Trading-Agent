# Package A — Clean Source Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone. Execute only Package A; do not continue into Package B automatically.

**Goal:** Create a clean isolated source baseline containing only reviewed approval tooling, canonical contract-generation dependencies, and the frozen 0006 source baseline.

**Architecture:** Preserve the original dirty candidate, transfer paths through a machine-readable review manifest, and commit scoped source changes in a new worktree. This package performs no PostgreSQL or runtime operation.

**Tech Stack:** Git worktrees, Python 3.11, pytest, npm, JSON Schema, Make.

## Global Constraints

- This file is non-authorizing until the user explicitly approves execution of Package A.
- Requested/effective mode remains `paper/paper`; both live gates remain false; kill-switch semantics remain unchanged.
- Do not start PostgreSQL, any Job Plane service, scheduler, timer, or listener.
- Do not enqueue a job, run SNAPSHOT, or call a provider, exchange, broker, or order endpoint.
- Do not reset, clean, overwrite, or rebase the existing dirty worktree.
- Do not read or print secret values, passwords, DSNs, environment values, or credential files.
- Do not add `openapi-typescript` or `openapi-zod-client` until the exact dev-dependency change receives explicit review.
- Keep migration 0005 and frozen 0006 byte-identical to their approved hashes.
- Stop on an unknown/secret-risk transfer path, dependency drift, failed test, or dirty integration worktree.

## Package Authority and Exit Gate

- **Entry:** approved base `e7141221423cc8d4fb3acfd757275e6d9eb69140`; original dirty candidate remains untouched.
- **Produces:** clean scoped commits for Tasks 1–3 and a complete transfer/exclusion record.
- **Exit:** approval example remains `DRAFT_NOT_AUTHORIZED`, contract generation is canonical, 0006 hash is exact, and Git is clean.
- **Next:** Package B requires a new explicit instruction and its own disposable PostgreSQL approvals.

---

### Task 1: Create an isolated integration worktree and commit approval tooling

**Files:**
- Source: current dirty worktree at `/home/thenam176/projects/trading-agent-worktrees/job-plane-recovery-candidate`
- Create worktree: `/home/thenam176/projects/trading-agent-worktrees/job-plane-authority-v4`
- Include: `schemas/postgres-recovery-approval-record.schema.json`
- Include: `ops/postgres/postgres-recovery-approval-record.example.yaml`
- Include: `ops/postgres/pending/postgres-recovery-approval-data-001-preparation.yaml`
- Include: `scripts/validate_postgres_recovery_approval.py`
- Include: `tests/production/test_postgres_recovery_approval.py`
- Include: `docs/implementation/postgres-recovery-approval-review.md`
- Include: `docs/superpowers/plans/2026-07-16-job-plane-blocker-closure.md`
- Include: `docs/superpowers/plans/2026-07-16-job-plane-blocker-closure-packages/*.md`
- Create: `docs/implementation/job-plane-v4-transfer-manifest.csv`
- Create: `docs/implementation/job-plane-v4-transfer-review.md`

**Interfaces:**
- Consumes: approved base commit `e7141221423cc8d4fb3acfd757275e6d9eb69140`.
- Produces: a clean branch containing the reviewed, permanently non-authorizing approval tooling.

- [ ] **Step 1: Create the worktree without changing the dirty source worktree**

```bash
git worktree add -b codex/job-plane-authority-v4 \
  /home/thenam176/projects/trading-agent-worktrees/job-plane-authority-v4 \
  e7141221423cc8d4fb3acfd757275e6d9eb69140
```

Expected: the new worktree is clean and the old candidate remains byte-for-byte untouched.

- [ ] **Step 2: Freeze a complete dirty-tree transfer manifest before copying any path**

Inventory every modified/untracked path in the original candidate using NUL-safe Git output. For each path record `path`, `git_status`, `classification`, `source_sha256_or_NOT_READ`, `destination`, `owning_task`, `include`, and `reviewer_decision`. Never content-read a path classified `SECRET_RISK`; record `NOT_READ`. Block on `UNKNOWN_REQUIRES_REVIEW`, a path changed after hashing, or an unreviewed inclusion. The review document records the original branch/HEAD/status count and excludes runtime, nondeterministic, local, secret-risk, and unrelated user changes.

- [ ] **Step 3: Reapply only the reviewed approval-tooling and plan paths**

Use `apply_patch` path by path. Include only the deterministic DATA-001 preparation draft whose human/current fields remain sentinels, the reviewed master plan, and its exact copy-ready package split; never include a human-filled or authoritative record. Compare SHA-256 values with the reviewed source artifacts.

- [ ] **Step 4: Run the focused suite**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q -p no:cacheprovider \
  tests/production/test_postgres_recovery_approval.py
```

Expected: 144 tests pass unless a deliberate new test changes the exact count; no PostgreSQL process is started.

- [ ] **Step 5: Commit only approval preparation and transfer evidence**

```bash
git add schemas/postgres-recovery-approval-record.schema.json \
  ops/postgres/postgres-recovery-approval-record.example.yaml \
  ops/postgres/pending/postgres-recovery-approval-data-001-preparation.yaml \
  scripts/validate_postgres_recovery_approval.py \
  tests/production/test_postgres_recovery_approval.py \
  docs/implementation/postgres-recovery-approval-review.md \
  docs/superpowers/plans/2026-07-16-job-plane-blocker-closure.md \
  docs/superpowers/plans/2026-07-16-job-plane-blocker-closure-packages \
  docs/implementation/job-plane-v4-transfer-manifest.csv \
  docs/implementation/job-plane-v4-transfer-review.md
git commit -m "ops(postgres): add non-authorizing recovery approval preparation"
```

**Exit gate:** committed example remains `DRAFT_NOT_AUTHORIZED`; no real approval enters Git.

---

### Task 2: Make contract generation canonical and worktree-portable

**Files:**
- Modify: `scripts/generate_contracts.py`
- Modify: `Makefile`
- Modify via npm: `apps/dashboard/package.json`
- Modify via npm: `apps/dashboard/package-lock.json`
- Modify: `tests/control_api/test_generation.py`
- Modify: `tests/jobs/test_contracts.py`
- Modify: `docs/contracts/contract-generation.md`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-dashboard-dependency-repair.md`

**Interfaces:**
- Produces: `make check-contracts` that uses only `apps/dashboard/node_modules` by default.
- Already-approved repair to preserve from the dirty candidate: exact dev dependency `@redocly/ajv@8.11.2`.
- Additional dependency review required before execution: exact dev dependencies `openapi-typescript@7.13.0` and `openapi-zod-client@1.18.3`.

- [ ] **Step 1: Write the failing provenance test**

The test must assert:

```python
assert generate_contracts.DEFAULT_TOOL_ROOT == ROOT / "apps" / "dashboard"
```

It must run the CLI with `CONTRACT_TOOL_ROOT` set to an invalid external directory and prove the default `--check` path does not read that ambient override. Explicit `--tool-root` remains available only for diagnostic use.

- [ ] **Step 2: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/control_api/test_generation.py
```

Expected: failure showing the current `ROOT.parent / "trading-dashboard"` default.

- [ ] **Step 3: Obtain dependency-change approval and update with npm**

```bash
cd apps/dashboard
npm install --save-dev --save-exact --ignore-scripts \
  @redocly/ajv@8.11.2 \
  openapi-typescript@7.13.0 \
  openapi-zod-client@1.18.3
cd ../..
```

Reject the change if any existing package version changes.

- [ ] **Step 4: Implement the canonical default**

Set:

```python
DEFAULT_TOOL_ROOT = ROOT / "apps" / "dashboard"
```

The parser default must be `DEFAULT_TOOL_ROOT`; do not derive it from ambient `CONTRACT_TOOL_ROOT`. Makefile must call the normal default rather than an external sibling.

- [ ] **Step 5: Verify GREEN and commit**

```bash
cd apps/dashboard && npm ci --ignore-scripts && cd ../..
env -u CONTRACT_TOOL_ROOT -u DASHBOARD_ROOT make check-contracts
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/control_api/test_generation.py tests/jobs/test_contracts.py
git diff --check
git add Makefile scripts/generate_contracts.py apps/dashboard/package.json \
  apps/dashboard/package-lock.json tests/control_api/test_generation.py \
  tests/jobs/test_contracts.py \
  docs/contracts/contract-generation.md \
  docs/implementation/job-plane-dashboard-dependency-repair.md
git commit -m "build(dashboard): own canonical contract generator toolchain"
```

**Exit gate:** the command passes even when `/home/thenam176/projects/trading-dashboard` is absent or unreadable.

---

### Task 3: Freeze the reviewed 0006 source baseline

**Files:**
- Include unchanged: `alembic/versions/0005_job_plane_role_split.py`
- Create from reviewed source: `alembic/versions/0006_job_transition_database_authority.py`
- Include current repository/job authority changes and their existing tests
- Modify: `tests/jobs/test_job_transition_authority.py`
- Update: `docs/adr/ADR-job-transition-database-authority.md`

**Interfaces:**
- Produces: an immutable committed 0006 object from which disposable catalog evidence can be derived.
- Approved hashes: 0005 `7b77d9abe0b5cfe84bf69ea60e47441179c99bcb533a6776f629cab103698f4e`; 0006 `f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd`.

- [ ] **Step 1: Add static identity tests**

Tests must hash both migration files and require the exact values above. They must also assert 0006 descends from 0005 and has a forward-only downgrade.

- [ ] **Step 2: Apply the reviewed source paths with explicit diff review**

Require every copied path to be `INCLUDE_IN_CANDIDATE` in Task 1's transfer manifest and recheck its recorded source hash immediately before applying it. Do not copy caches, local configuration, runtime artifacts, signed approvals, or generated evidence. Keep the known 0006 verification limitation documented; this commit is a frozen baseline, not a release-ready head.

- [ ] **Step 3: Run static and repository tests that do not start PostgreSQL**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_repository_transition_capabilities.py \
  tests/jobs/test_state_machine.py \
  tests/runtime_release/test_v2.py
git diff --check
```

- [ ] **Step 4: Commit the baseline**

```bash
git add alembic/versions/0006_job_transition_database_authority.py \
  apps/job_api/config.py docs/production/release-authority-v2.md \
  ops/release-v2/build-stage.sh ops/release-v2/provision-root.sh \
  ops/release-v2/verify-stage.py ops/systemd/job-api.env.example \
  packages/runtime_release/v2.py services/job_store/repository.py \
  services/job_store/worker_repository.py services/job_worker/main.py \
  tests/control_api/test_alembic_schema.py tests/jobs/test_job_api.py \
  tests/jobs/test_job_api_auth.py tests/jobs/test_job_api_security.py \
  tests/jobs/test_job_role_permissions.py \
  tests/jobs/test_repository_cancel_acl.py \
  tests/jobs/test_repository_enqueue.py tests/jobs/test_repository_queries.py \
  tests/jobs/test_repository_transactions.py \
  tests/jobs/test_repository_transition_capabilities.py \
  tests/jobs/test_systemd_units.py tests/jobs/test_worker_claims.py \
  tests/jobs/test_worker_leases.py tests/jobs/test_worker_lifecycle.py \
  tests/jobs/test_worker_recovery.py tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_provisioning.py \
  docs/adr/ADR-job-transition-database-authority.md
git commit -m "db: preserve frozen job transition authority baseline"
```

**Exit gate:** Git is clean; the 0006 file hash is exact; documentation still marks 0006 insufficient as final authority.

---
