# Job Plane Blocker Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Copy-ready split:** Use [the package index](2026-07-16-job-plane-blocker-closure-packages/00-index-and-common-gates.md) to copy Packages A–J independently. This master file remains the canonical combined plan.

**Goal:** Close the database-authority, source-provenance, hermetic-release, and recovery-approval blockers without starting Job Plane services or weakening paper-only containment.

**Architecture:** Work is split into four independently authorized planes: disposable PostgreSQL verification, clean source authority, hermetic static release, and runtime maintenance. Frozen migration 0006 remains immutable; forward-only 0007 verifies exact catalog and event history and performs only the global default-function ACL repair. Application, backend, and maintenance-only migrator become separate sealed components bound by one aggregate authority.

**Tech Stack:** Python 3.11, PostgreSQL 16, Alembic, psycopg, pytest, JSON Schema, npm, systemd, Git worktrees, python-build-standalone.

## Global Constraints

- This turn is plan-only: execute no task, start no PostgreSQL process (including disposable fixtures), and make no runtime/release change beyond writing this plan.
- Requested/effective mode remains `paper/paper`.
- `LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false` remain mandatory.
- Do not change kill-switch semantics or enable any live path.
- Do not call a provider, exchange, broker, or order endpoint.
- Do not start the runtime PostgreSQL cluster without a valid dual-reviewed 50-field recovery record.
- Do not apply 0005, 0006, or 0007 to the runtime database under recovery authority.
- Do not start Job API, worker, scheduler, or timer during blocker closure.
- Do not enqueue a job or execute SNAPSHOT.
- Keep the scheduler timer disabled; do not emit it into the release candidate.
- Never build a release from a dirty source tree.
- Never commit secrets, real approvals, environment files, dumps, caches, logs, runtime reports, or mutable evidence.
- Do not rewrite frozen migrations. Forward repair only.
- Do not use `pg_resetwal`, initialize runtime PGDATA, delete `postmaster.pid`, or overwrite recovery evidence.
- Every command that can start disposable PostgreSQL requires exact, unexpired disposable-test approval.
- Tasks that implement Release Authority v2 or perform a network-enabled input acquisition require the repository-mandated, scope-specific operator approval before execution.
- Runtime recovery, runtime migration, and Job service rollout require separate approvals.

---

## Dependency and authority map

```text
Canonical toolchain fix ─────────────────────────┐
Approval tooling commit ────────────────────────┤
Frozen 0006 baseline commit ────────────────────┤
                                                ├─> database-authority source commit
DISPOSABLE_PG_RED approval                       │
  -> 0006 adversarial RED proof                  │
  -> exact two-build 0006 catalog                │
  -> forward-only 0007                           │
  -> DISPOSABLE_PG_GREEN approval                │
  -> 0007/restore GREEN proof ───────────────────┘

database-authority source commit
  -> canonical backend-subtree review
  -> pinned CPython and wheel inputs
  -> release-tooling source verification
  -> final clean commit
  -> sealed app + backend + migrator components
  -> aggregate manifests
  -> two-build reproducibility
  -> tamper suite
  -> exact-path systemd verification
  -> reviewed candidate promotion to staging

separately approved MAINTENANCE_KIT_PROVISIONING
  -> root-owned migrator + authority + exact runbooks only
  -> no service/unit installation

dual-reviewed RUNTIME_RECOVERY
  -> recover/verify/backup/restore runtime at 0004
  -> controlled stop

separately reviewed RUNTIME_MIGRATION_BACKUP
  -> fresh backup/restore at exact approved entry state
  -> controlled stop for human review

separately reviewed RUNTIME_MIGRATION_0005_0007
  -> bind the fresh backup/restore digest
  -> 0005 -> frozen 0006 -> 0007
  -> authority verification
  -> controlled stop
```

The following approvals are deliberately non-interchangeable:

| Authority | Allows | Explicitly denies |
|---|---|---|
| `DISPOSABLE_PG_RED` | PostgreSQL 16 fixtures under `/tmp/phase4-postgres-*`; adversarial 0006 tests; exact hash-bound ACL-repair derivation inside an always-rolled-back transaction | Runtime PGDATA, port 55432, systemd, runtime credentials, any unreviewed SQL |
| `DISPOSABLE_PG_GREEN` | 0007, role-matrix, and restore tests on disposable fixtures | Runtime migration, Job services, runtime backup |
| `SOURCE_RELEASE_IMPLEMENTATION` | Tasks 9-15 source/tooling work, approved build-input acquisition, staging-only builds, and independently pinned bootstrap-interpreter/validator/materializer hashes | `/opt`, systemd installation, runtime PostgreSQL, service startup |
| `MAINTENANCE_KIT_PROVISIONING` | Future root-owned installation of only the sealed migrator (including exact runbooks/launchers) and authority | App/backend activation, unit installation, daemon reload, service startup, database access |
| `RUNTIME_RECOVERY` | Exact unchanged recovery runbook under the external 50-field approval record | 0005-0007, Job services, enqueue |
| `RUNTIME_MIGRATION_BACKUP` | Fresh custom dump and isolated restore at one exact entry state, then stop | Role/schema mutation, recovery retry, service rollout, enqueue, timer |
| `RUNTIME_MIGRATION_0005_0007` | Maintenance-only apply and verify after human review binds that fresh backup | Backup creation under the same approval, recovery retry, service rollout, enqueue, timer |
| `JOB_ROLLOUT` | Future API/worker rollout only | Out of scope for this plan |

Recommended execution packages:

| Package | Tasks | Primary blocker closed | Complexity | Gate before next package |
|---|---:|---|---|---|
| A | 1-3 | Dirty source, non-authorizing approval tooling, external contract generator, frozen 0006 baseline | M | Clean scoped commits; no PostgreSQL |
| B | 4-7 | Disposable authority, exhaustive catalog/event chain, forward-only 0007, all active head pins | XL | GREEN role/restore suite under disposable approvals |
| C | 8 | Canonical backend provenance | M | All 79 paths reviewed and subtree frozen |
| D | 9-11 | Hermetic Python/wheel closure and release tooling | XL | Offline closure, fixture reproducibility/tamper/systemd green |
| E | 12-13 | Immutable recovery source binding and separate migration authority contract | L | Both validators/runbooks green; examples remain non-authorizing |
| F | 14-15 | Exact clean commit and immutable staging candidate | L | Two final builds agree; no Git/runtime mutation |
| G | 16 | Root-owned maintenance kit | M, provisioning-bound | Exact sealed kit; no units/services installed |
| H | 17 | Human recovery authority | M, review-bound | Exact protected 50-field record; unexpired dual review |
| I | 18 | Runtime recovery/backup at 0004 | L, operational | Exact baseline and restore proof; controlled stop |
| J | 19 | Runtime backup authority, then separately signed migration to 0007 | L, operational/review-bound | Exact catalog/ACL/event authority; services still off |

Do not combine Packages G, H, I, or J into one approval or one implicit maintenance window.

## File responsibility map

- `scripts/generate_contracts.py`: canonical in-repository contract generator selection.
- `tests/jobs/_postgres.py`: sole disposable-cluster harness and technical interlock.
- `packages/job_authority/verifier.py`: read-only exact catalog and event-chain verification.
- `alembic/versions/0007_job_event_chain_authority.py`: frozen forward-only 0007 contract and ACL repair.
- `ops/postgres/job-plane-authority/`: reviewed stable catalog snapshots and manifest.
- `packages/runtime_release/backend_policy.py`: canonical backend subtree authority.
- `packages/runtime_release/hermetic_inputs.py`: strict input-record parsing and hash validation.
- `packages/runtime_release/hermetic_python.py`: safe standalone-Python materialization and relocation checks.
- `packages/runtime_release/wheelhouse.py`: offline wheel selection, hash, ABI, and license verification.
- `packages/runtime_release/release_manifests.py`: canonical component and aggregate manifest composition.
- `packages/runtime_release/source_authority.py`: protected external final-source authority validation.
- `ops/release-v2/build-component.py`: builds one sealed app, backend, or maintenance-only migrator component.
- `ops/release-v2/build-candidate.py`: offline, source-archive-only candidate orchestration.
- `ops/release-v2/compose-candidate.py`: composes authority without installing or running services.
- `ops/release-v2/verify-stage.py`: standalone fail-closed verifier.
- `docs/production/runbooks/postgresql-preserve-recover-v2.md`: recovery from sealed source/release authority, never a mutable checkout.
- `scripts/validate_job_plane_runtime_migration_approval.py`: separate one-attempt runtime migration authority.

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

### Task 4: Add a technical interlock for disposable PostgreSQL

**Files:**
- Modify: `tests/jobs/_postgres.py`
- Modify: `tests/jobs/test_postgres_harness.py`
- Create: `schemas/disposable-postgres-test-approval.schema.json`
- Create: `scripts/validate_disposable_postgres_approval.py`
- Create: `tests/jobs/test_disposable_postgres_approval.py`
- Create outside Git during execution: an unexpired, human-reviewed disposable-test approval record

**Interfaces:**
- Consumes: explicit operator approval for disposable PostgreSQL only.
- Produces: a harness that cannot accidentally target runtime PGDATA or port 55432.
- Required environment: `TRADING_TEST_DISPOSABLE_APPROVAL_RECORD` and exact `TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE` (`DISPOSABLE_PG_RED` or `DISPOSABLE_PG_GREEN`) in addition to `TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES`.
- The external record binds one scope, source commit/tree, expiry, distinct operator/reviewer identities, approved test paths/operation IDs, an optional exact SQL-file hash for RED catalog derivation, `/tmp/phase4-postgres-*`, loopback-only bind, forbidden ports 3002/8401/55432, runtime PGDATA exclusion, and a canonical record digest. Unknown/missing fields or placeholders reject; SQL execution rejects unless the RED record names the derivation operation and exact reviewed hash.
- Every database-starting test exposes a stable operation ID. Without an approval it is an explicitly reported skip; with a record for the other scope it remains a skip and cannot call a PostgreSQL executable. Final verification runs the no-approval suite first, then the exact RED and GREEN operation lists under separate commit-bound records—never the whole suite under one scope.

- [ ] **Step 1: Write mocked RED tests**

Tests must prove no call to `initdb`, `pg_ctl`, `psql`, `pg_dump`, or `pg_restore` unless all three controls are present and the protected record validates. They must reject missing/expired/wrong-scope records, source identity drift, same reviewer/operator, unapproved test paths, PGDATA outside `/tmp/phase4-postgres-*`, non-loopback bind, ports 3002/8401/55432, runtime database settings, and missing cluster marker.

- [ ] **Step 2: Implement the narrow interlock**

The validator exposes a reusable pure function consumed by the harness; the CLI and harness must make the same decision. The harness must set `cluster_name=trading-agent-disposable-tests`, choose an OS-assigned loopback port excluding 3002, 8401, and 55432, and stop the cluster in `finally`. Environment variables are only technical interlocks; only the exact reviewed external record supplies authority.

- [ ] **Step 3: Verify without starting PostgreSQL**

```bash
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_disposable_postgres_approval.py \
  tests/jobs/test_postgres_harness.py
```

Expected: mocked/unit tests pass and no PostgreSQL process is created.

- [ ] **Step 4: Commit**

```bash
git add tests/jobs/_postgres.py tests/jobs/test_postgres_harness.py \
  schemas/disposable-postgres-test-approval.schema.json \
  scripts/validate_disposable_postgres_approval.py \
  tests/jobs/test_disposable_postgres_approval.py
git commit -m "test: require explicit disposable postgres authority"
```

**Stop condition:** no exact disposable approval, any reference to runtime PGDATA, or any listener on 55432.

---

### Task 5: Produce adversarial RED proof and freeze catalog/repair inputs

**Files:**
- Create: `packages/job_authority/__init__.py`
- Create: `packages/job_authority/verifier.py`
- Create: `scripts/verify_job_plane_authority.py`
- Create: `tests/jobs/test_job_authority_verifier.py`
- Create: `tests/jobs/test_job_authority_catalog.py`
- Create: `tests/jobs/test_job_event_chain_authority.py`
- Create: `ops/postgres/job-plane-authority/query-contract-v1.json`
- Create: `ops/postgres/job-plane-authority/acl-repair-v1.sql`
- Create after two identical captures: `ops/postgres/job-plane-authority/catalog-0006-v1.snapshot`
- Create after review: `ops/postgres/job-plane-authority/catalog-0006-v1.manifest.json`
- Create after two identical rolled-back derivations: `ops/postgres/job-plane-authority/catalog-0007-v1.snapshot`
- Create after review: `ops/postgres/job-plane-authority/catalog-0007-v1.manifest.json`
- Create: `docs/implementation/job-plane-0006-exhaustive-audit.md`
- Create: `docs/implementation/job-plane-event-chain-verification.md`

**Interfaces:**
- `load_frozen_contract(path: Path) -> FrozenAuthorityContract`
- `load_migration_literals(path: Path) -> FrozenAuthorityContract`
- `capture_catalog(connection, contract) -> CatalogEvidence`
- `find_event_chain_violations(connection, contract) -> tuple[Violation, ...]`
- `verify_authority(connection, contract_path, migration_path) -> AuthorityEvidence`

The immutable records have these exact fields:

```python
@dataclass(frozen=True, slots=True)
class FrozenAuthorityContract:
    catalog_query_id: str
    catalog_sql: str
    event_chain_query_id: str
    event_chain_sql: str

@dataclass(frozen=True, slots=True)
class CatalogEvidence:
    query_id: str
    sha256: str
    row_count: int
    canonical_bytes: bytes

@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    job_id: str
    event_id: str | None
    sequence: int | None

@dataclass(frozen=True, slots=True)
class AuthorityEvidence:
    head: str
    catalog: CatalogEvidence
    event_chain_query_id: str
    violations: tuple[Violation, ...]
```

`query-contract-v1.json` is the non-self-referential source of the reviewed query bytes. `acl-repair-v1.sql` contains exactly one fixed statement and no psql metacommand or dynamic SQL. The verifier reads 0007 literal constants through AST parsing, requires them to equal those frozen inputs byte-for-byte, and must not import or execute migration code. Migration hashes are recorded later in an external authority manifest; 0007 never contains its own SHA-256. The migration and Python verifier execute the same canonical serialization SQL bytes and compute SHA-256 over the same UTF-8, C-sorted, newline-terminated stream. Catalog bytes include the `alembic_version` relation definition but exclude its row value; exact head is queried and checked separately before/after catalog verification.

Catalog scope is the target database plus exactly `trading_owner`, `trading_migrator`, `trading_reader`, `trading_jobs`, `trading_job_api`, `trading_job_worker`, and `trading_job_scheduler`; include every membership edge touching a named role and global/schema default ACLs owned by `trading_owner`. Role settings use an exact reviewed safe-key policy: serialize only approved non-secret settings such as the fixed `search_path`; if any other key exists, report only its key name and fail without returning or hashing its value. Exclude unrelated ambient roles, OIDs, timestamps, physical filenames, password/verifier fields, and secret values. Include exact schemas, objects, columns, constraints, indexes, sequences, functions, triggers, policies/RLS, raw ACL/default ACL, owners, named-role attributes, and memberships.

- [ ] **Step 1: Write the frozen query contract and catalog drift tests before 0007 exists**

The query contract contains only query IDs and exact SQL; it contains no expected migration or catalog digest. Freeze `acl-repair-v1.sql` with only `ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;`. Cover extra event-suppressing trigger, disabled/altered trigger, changed function body/search path/result, overload, PUBLIC EXECUTE, wrong grantee/grant option, policy drift, RLS drift, extra relation/sequence/column ACL/default ACL, wrong owner, role membership, and role attribute drift. AST tests use a temporary synthetic migration until final 0007 exists.

- [ ] **Step 2: Write the complete event-chain matrix**

The query must report stable codes for no history, sequence start/gap/duplicate, bootstrap not `NULL -> QUEUED` or carrying an attempt ID, later NULL or disconnected `from_state`, an edge outside the 12 ordinary plus two retry edges in `packages/job_contracts/transitions.py`, final-state mismatch, and cross-job attempt. A retry edge is valid only when it immediately follows the terminal event for the same attempt, its referenced `attempt_number` is below `jobs.max_attempts`, and metadata matches one exact authority: `PROCESS_RETRY_SCHEDULED`/`WORKER` or `LEASE_EXPIRED_RETRY_SCHEDULED`/`RECOVERY`. It begins a new retry epoch. Report wrong actor/reason, changed/null attempt, forged or over-budget retry, an event after terminal without that retry, and duplicate terminal transition within one epoch. A later terminal result after a valid retry cycle is legal.

Add explicit negative fixtures for wrong actor, wrong reason, different/null attempt, retry after max attempts, retry not adjacent to its terminal event, and bootstrap with an attempt ID; add positive worker and recovery retry cycles followed by a second valid terminal event.

- [ ] **Step 3: Run pure verifier/unit tests without PostgreSQL**

```bash
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_verifier.py
```

- [ ] **Step 4: Commit the exact RED probe before requesting authority**

```bash
git add packages/job_authority/__init__.py \
  packages/job_authority/verifier.py \
  scripts/verify_job_plane_authority.py \
  tests/jobs/test_job_authority_verifier.py \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  ops/postgres/job-plane-authority/query-contract-v1.json \
  ops/postgres/job-plane-authority/acl-repair-v1.sql
git commit -m "test(db): add exact job authority verifier and RED probes"
```

- [ ] **Step 5: Validate and run under a commit-bound `DISPOSABLE_PG_RED` record**

```bash
ACL_REPAIR_SHA256="$(sha256sum \
  ops/postgres/job-plane-authority/acl-repair-v1.sql | awk '{print $1}')"
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_RED \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-tree "$(git rev-parse HEAD^{tree})" \
  --expected-sql-sha256 "$ACL_REPAIR_SHA256"
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_RED \
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py
```

Expected: the test command exits zero because it proves and classifies the intended 0006 authority gaps; no test is made green by weakening an assertion or bypassing the harness.

- [ ] **Step 6: Capture exact pre/post catalogs and commit reviewed RED evidence**

Build two fresh disposable databases from the committed source, capture both 0006 snapshots with the frozen query contract, and require byte equality. In each fixture, execute only the hash-bound `acl-repair-v1.sql` inside an explicit transaction, capture the candidate post-repair catalog, and roll back; require both post-repair captures to be byte-equal and prove the transaction left no change. Review the query/SQL surface and secret scan before accepting either snapshot and its non-null SHA-256 manifest. The deterministic audit documents record exact query IDs, hashes, violation codes, test totals, rollback proof, and the fact that runtime PostgreSQL was not touched.

```bash
git add ops/postgres/job-plane-authority/catalog-0006-v1.snapshot \
  ops/postgres/job-plane-authority/catalog-0006-v1.manifest.json \
  ops/postgres/job-plane-authority/catalog-0007-v1.snapshot \
  ops/postgres/job-plane-authority/catalog-0007-v1.manifest.json \
  docs/implementation/job-plane-0006-exhaustive-audit.md \
  docs/implementation/job-plane-event-chain-verification.md
git commit -m "docs(db): freeze reviewed 0006 authority evidence"
```

**Stop condition:** unequal captures, unstable fields, secret-bearing output, a surviving disposable process, or any runtime listener/PGDATA access.

---

### Task 6: Implement forward-only 0007 and prove restore authority

**Files:**
- Create: `alembic/versions/0007_job_event_chain_authority.py`
- Verify unchanged: `ops/postgres/job-plane-authority/catalog-0007-v1.snapshot`
- Verify unchanged: `ops/postgres/job-plane-authority/catalog-0007-v1.manifest.json`
- Create only after 0007 is final: `ops/postgres/job-plane-authority/authority-manifest-v1.json`
- Modify: `packages/job_contracts/transitions.py`
- Modify: `packages/job_contracts/__init__.py`
- Modify: `tests/jobs/test_job_transition_authority.py`
- Modify: `tests/jobs/test_job_transition_restore.py`
- Modify: `tests/jobs/test_job_role_permissions.py`
- Update: `docs/adr/ADR-job-transition-database-authority-v2.md`
- Create: `docs/implementation/job-plane-0007-disposable-evidence.md`

**Interfaces:**
- 0007 literal constants: `CATALOG_QUERY_ID`, `CATALOG_SNAPSHOT_SQL`, `EVENT_CHAIN_QUERY_ID`, `EVENT_CHAIN_VIOLATIONS_SQL`, and reviewed pre/post catalog digests.
- 0007 retains all eight fixed transition functions from 0006; it adds no generic mutation API.
- `authority-manifest-v1.json` binds the hashes of frozen 0006, final 0007, the query contract, `acl-repair-v1.sql`, both catalog snapshots, and both catalog manifests. No migration contains or attempts to predict its own hash.

- [ ] **Step 1: Implement the smallest forward repair**

Upgrade must require owner/session `trading_owner`, PostgreSQL 16, exact 0006 head, zero runtime-role sessions, and writer-blocking locks on jobs/attempts/events. It must require the reviewed 0006 catalog digest and zero event-chain violations, then execute only:

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner
REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
```

It must require the reviewed 0007 catalog digest, exact runtime-role DML denial, and exact existing fixed-function EXECUTE grants. `downgrade()` always raises.

- [ ] **Step 2: Freeze the migration and external authority manifest**

0007's literal query and repair SQL bytes must equal the already committed query/repair inputs. Insert the already reviewed pre/post catalog digests, then freeze 0007. Compute the final 0007 file hash only after its bytes stop changing and write it to `authority-manifest-v1.json`, never into 0007. Static tests reject any literal/input mismatch or placeholder. Commit only real 64-hex digests.

- [ ] **Step 3: Run no-PostgreSQL checks and commit the exact GREEN candidate**

```bash
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_verifier.py
git add alembic/versions/0007_job_event_chain_authority.py \
  packages/job_authority/verifier.py \
  packages/job_contracts/__init__.py \
  packages/job_contracts/transitions.py \
  ops/postgres/job-plane-authority/authority-manifest-v1.json \
  tests/jobs/test_job_authority_verifier.py \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  tests/jobs/test_job_transition_authority.py \
  tests/jobs/test_job_transition_restore.py \
  tests/jobs/test_job_role_permissions.py \
  docs/adr/ADR-job-transition-database-authority-v2.md
git commit -m "db: add forward-only job event chain authority"
```

- [ ] **Step 4: Validate and run under a commit-bound `DISPOSABLE_PG_GREEN` record**

```bash
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_GREEN \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-tree "$(git rev-parse HEAD^{tree})"
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_GREEN \
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  tests/jobs/test_job_transition_authority.py \
  tests/jobs/test_job_role_permissions.py \
  tests/jobs/test_repository_transition_capabilities.py
```

Expected: every catalog/event corruption rejects atomically; clean 0004 -> 0005 -> 0006 -> 0007 succeeds without inserting a job.

The GREEN gate must perform two independent fresh `0004 -> 0005 -> 0006 -> 0007` upgrades from the committed candidate. Capture the actual post-0007 catalog from each, require the two captures to be byte-equal, and require both to equal the rolled-back derived `catalog-0007-v1.snapshot`. Verify exact head separately as 0007. Any mismatch invalidates the authority manifest and stops; do not regenerate expected bytes from the failing run.

- [ ] **Step 5: Prove custom dump/restore without conflating global roles**

Use two wholly separate disposable clusters, each with database name `trading_agent`. The source reaches exact 0007 and creates a mode-0600 custom dump with `--create`. On the target, first create an exact 0004 baseline, run `ops/postgres/provision-job-roles.sql` there so its exact-name/head preflight succeeds, verify global attributes/memberships/settings, then drop only that disposable database while retaining the global roles. Restore the `--create` dump through the maintenance database with `pg_restore --create --exit-on-error`. Verify restored database-level ACLs separately from the independently provisioned globals, plus head, catalog digest, event-chain result, direct-DML denial, append-only denial, and counts. Never target runtime, use a differently named restore database, or use a globals dump that can carry password hashes.

- [ ] **Step 6: Commit only sanitized deterministic GREEN evidence**

```bash
git add docs/implementation/job-plane-0007-disposable-evidence.md \
  docs/implementation/job-plane-event-chain-verification.md
git commit -m "docs(db): record disposable 0007 authority evidence"
```

**Rollback:** stop and remove only disposable fixtures; revert source commit if rejected. Never downgrade a database.

---

### Task 7: Move every active head pin to 0007

**Files:**
- Modify: `apps/job_api/config.py`
- Modify: `services/job_worker/main.py`
- Modify: `services/job_store/worker_repository.py`
- Modify: `ops/systemd/job-api.env.example`
- Modify: `packages/runtime_release/v2.py`
- Modify: `ops/release-v2/build-stage.sh`
- Modify: `ops/release-v2/verify-stage.py`
- Modify: matching tests under `tests/jobs` and `tests/runtime_release`
- Create: `docs/production/runbooks/job-plane-transition-authority-rollout.md`

**Interfaces:**
- Required active chain: `0007 -> 0006 -> 0005 -> 0004`.
- Historical recovery and frozen migration references retain their original revision values.

- [ ] **Step 1: Write RED head-graph tests**

Tests must reject an API, worker, unit, or release whose expected head is 0006. Release source proof must include 0007, the query contract, both catalog snapshots/manifests, the external authority manifest, and the read-only verifier.

- [ ] **Step 2: Update active pins without global search-and-replace**

Use an ordered ancestry tuple instead of adding another fixed `great_grandparent` field. Do not modify 0006's own revision metadata, the pre-recovery 0004 runbook, or obsolete v1 provisioning into appearing v2-compatible.

The new rollout runbook must preserve by exact reference/hash the protected-stdin, first-use role procedure in `docs/production/runbooks/job-plane-role-split-rollout.md` and `ops/postgres/provision-job-roles.sql`; it stops if any job role already exists and contains no service-start, timer, enqueue, or SNAPSHOT step.

- [ ] **Step 3: Run focused tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/control_api/test_alembic_schema.py \
  tests/jobs/test_job_api.py tests/jobs/test_job_api_auth.py \
  tests/jobs/test_job_api_security.py tests/jobs/test_worker_lifecycle.py \
  tests/jobs/test_systemd_units.py tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_provisioning.py
git add apps/job_api/config.py services/job_worker/main.py \
  services/job_store/worker_repository.py ops/systemd/job-api.env.example \
  packages/runtime_release/v2.py ops/release-v2/build-stage.sh \
  ops/release-v2/verify-stage.py tests/control_api/test_alembic_schema.py \
  tests/jobs/test_job_api.py tests/jobs/test_job_api_auth.py \
  tests/jobs/test_job_api_security.py tests/jobs/test_worker_lifecycle.py \
  tests/jobs/test_systemd_units.py tests/jobs/test_job_transition_authority.py \
  tests/runtime_release/test_v2.py tests/runtime_release/test_v2_provisioning.py \
  docs/production/runbooks/job-plane-transition-authority-rollout.md
git commit -m "jobs: require verified 0007 database authority"
```

**Exit gate:** exactly one Alembic head exists and every executable path requires 0007.

---

### Task 8: Re-authorize backend provenance and close component source scopes

**Files:**
- Create: `ops/release-v2/inputs/backend-source-policy.json`
- Create: `ops/release-v2/inputs/app-source-policy.json`
- Create: `ops/release-v2/inputs/migrator-source-policy.json`
- Create: `packages/runtime_release/source_policy.py`
- Modify: `packages/runtime_release/backend_policy.py`
- Modify: `services/job_worker/command_registry.py`
- Modify: `scripts/smoke_phase4_backend_release.py`
- Modify: `tests/runtime_release/test_backend_release_smoke.py`
- Modify: `tests/runtime_release/test_v2.py`
- Create: `tests/runtime_release/test_component_source_policy.py`
- Create: `docs/implementation/job-plane-backend-provenance-review.md`

**Interfaces:**
- Produces: a reviewed exact `legacy/research-backend` subtree OID plus per-file blob policy digest. It does not claim a final monorepo commit before later source tasks finish; Task 14 proves the subtree is unchanged and Task 15 binds it to the frozen root commit.
- Historical baseline `41f055b48033714c660f44cc20498b7545366e75` remains evidence only.
- Application policy allowlists only Job API, worker, job-store, required job/safety contracts, and their package metadata; it rejects Control API, scheduler, dashboard, tests, and unrelated `packages/`/`services/` even if the root wheel configuration would include them.
- Migrator policy allowlists only Alembic/config, the complete migration graph, exact role/authority SQL, read-only verifiers, the V2 recovery and runtime-migration runbooks plus their fixed launchers/validators, required contracts, and maintenance entrypoint; it contains no API/worker command module. Paths created in Tasks 12-13 are reserved by exact name here, then bound to their final Git blobs by Task 14/15.

Current evidence shows the candidate backend subtree `d9bc9992a56d46abd36141901c6bf495522e9616` differs from the legacy approved tree `b15af11d8600e042e20403dba982a3c1bc1b4b60`; only 35 of 79 audited paths are byte-equal.

- [ ] **Step 1: Generate and review all 79 path comparisons**

Classify every changed path by semantic effect, command reachability, safety effect, and release necessity. Do not approve by count alone.

- [ ] **Step 2: Write RED provenance tests**

Tests must reject a v2 manifest claiming the current canonical backend is legacy commit `41f055b...`, reject subtree drift, and reject any command other than `SNAPSHOT`. Component-policy tests must fail when any unlisted root package/service, Control API, scheduler, dashboard, test, migration, or maintenance file enters the wrong component; every included import must resolve wholly inside its component or an explicitly bound shared contract.

- [ ] **Step 3: Bind canonical identity**

V2 uses final root commit plus exact backend subtree and per-file blobs. Keep the old `APPROVED_PHASE4_BACKEND_COMMIT` only for explicit v1 compatibility; it must not authorize v2.

- [ ] **Step 4: Obtain human review of the immutable backend subtree policy and commit that policy**

```bash
git add ops/release-v2/inputs/backend-source-policy.json \
  ops/release-v2/inputs/app-source-policy.json \
  ops/release-v2/inputs/migrator-source-policy.json \
  packages/runtime_release/source_policy.py \
  packages/runtime_release/backend_policy.py \
  services/job_worker/command_registry.py \
  scripts/smoke_phase4_backend_release.py \
  tests/runtime_release/test_backend_release_smoke.py \
  tests/runtime_release/test_component_source_policy.py \
  tests/runtime_release/test_v2.py \
  docs/implementation/job-plane-backend-provenance-review.md
git commit -m "release: bind canonical backend subtree authority"
```

**Stop condition:** any of the 44 differences remains unreviewed or the subtree OID/per-file policy changes after approval. Later root commits are permitted only when `git rev-parse HEAD:legacy/research-backend` remains identical.

---

### Task 9: Split and seal hermetic Python and wheel inputs

**Files:**
- Create: `docs/adr/ADR-job-plane-hermetic-release-layout.md`
- Create: `ops/release-v2/inputs/python-3.11.15.json`
- Create: `ops/release-v2/inputs/app-wheel-selection.json`
- Create: `ops/release-v2/inputs/backend-wheel-selection.json`
- Create: `ops/release-v2/inputs/migrator-wheel-selection.json`
- Create: `ops/release-v2/inputs/build-wheel-selection.json`
- Create: `ops/release-v2/inputs/license-policy.json`
- Create: `ops/release-v2/inputs/host-abi-linux-x86_64.json`
- Create: `schemas/release-build-input-approval.schema.json`
- Create: `ops/release-v2/build-input-approval.example.json`
- Create: `scripts/validate_release_build_input_approval.py`
- Create: `packages/runtime_release/hermetic_inputs.py`
- Create: `packages/runtime_release/hermetic_python.py`
- Create: `packages/runtime_release/wheelhouse.py`
- Create: `ops/release-v2/acquire-inputs.py`
- Create: `ops/release-v2/bootstrap-build-driver.py`
- Create: `ops/release-v2/run-offline-build.py`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-hermetic-python-design.md`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-wheelhouse-closure.md`
- Test: `tests/runtime_release/test_hermetic_inputs.py`
- Test: `tests/runtime_release/test_hermetic_python.py`
- Test: `tests/runtime_release/test_wheelhouse.py`
- Test: `tests/runtime_release/test_build_input_approval.py`
- Test: `tests/runtime_release/test_offline_build_driver.py`

**Interfaces:**
- CPython input: version 3.11.15, tag `20260623`, asset `cpython-3.11.15+20260623-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`, SHA-256 `0604cd029b142dc223e131f17f5941c0c8d2d5074997c8178b515b19eea2a6c2`.
- External input root: `/home/thenam176/.local/share/trading-agent/release-inputs/v2`.
- The input root contains a sealed bootstrap interpreter at `python/runtime/bin/python3.11`; its file SHA-256 must equal the approved CPython-derived runtime manifest before it may validate or bootstrap anything.
- Application, backend, and maintenance-only migrator have separate wheel selections and component roots.
- The external build-input record binds exact scope, expiry, source commit/tree, approved immutable URLs/domains, artifact names/hashes/sizes, output/CAS root, network operation IDs, distinct reviewers, and canonical digest. It contains no credential and authorizes neither runtime nor service actions.
- `bootstrap-build-driver.py` materializes a separate pinned-CPython build driver from the local build-wheel closure. Final builds invoke no host `uv`, host Python, global site-packages, or working-tree script.
- `run-offline-build.py` requires a no-network namespace/sandbox, sets offline package-manager controls, executes the commit-exported builder under the pinned driver, and stops if network isolation is unavailable.

- [ ] **Step 1: TDD strict input records**

Reject latest URLs, hash/size drift, sdists, ambiguous wheels, unexpected domains, external/dangling links, special files, mutable inputs, unreviewed licenses, unresolved native DSOs, and every missing/expired/wrong-commit build-input approval. The CLI and library validator must make the same decision. Implement acquisition, safe standalone-Python materialization, wheel verification, build-driver bootstrap, and offline wrapper behind these tests, but perform no network acquisition yet.

- [ ] **Step 2: Verify and commit the exact acquisition/tooling authority before requesting network approval**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/runtime_release/test_hermetic_inputs.py \
  tests/runtime_release/test_hermetic_python.py \
  tests/runtime_release/test_wheelhouse.py \
  tests/runtime_release/test_build_input_approval.py \
  tests/runtime_release/test_offline_build_driver.py
git add docs/adr/ADR-job-plane-hermetic-release-layout.md \
  ops/release-v2/inputs/python-3.11.15.json \
  ops/release-v2/inputs/app-wheel-selection.json \
  ops/release-v2/inputs/backend-wheel-selection.json \
  ops/release-v2/inputs/migrator-wheel-selection.json \
  ops/release-v2/inputs/build-wheel-selection.json \
  ops/release-v2/inputs/license-policy.json \
  ops/release-v2/inputs/host-abi-linux-x86_64.json \
  schemas/release-build-input-approval.schema.json \
  ops/release-v2/build-input-approval.example.json \
  scripts/validate_release_build_input_approval.py \
  packages/runtime_release/hermetic_inputs.py \
  packages/runtime_release/hermetic_python.py \
  packages/runtime_release/wheelhouse.py \
  ops/release-v2/acquire-inputs.py \
  ops/release-v2/bootstrap-build-driver.py \
  ops/release-v2/run-offline-build.py \
  tests/runtime_release/test_hermetic_inputs.py \
  tests/runtime_release/test_hermetic_python.py \
  tests/runtime_release/test_wheelhouse.py \
  tests/runtime_release/test_build_input_approval.py \
  tests/runtime_release/test_offline_build_driver.py
git commit -m "release: define hermetic build input authority"
```

The commit above is the acquisition-tooling identity. No external record may authorize uncommitted acquisition code, and any later tooling/policy edit invalidates the pending record.

- [ ] **Step 3: Acquire inputs in a separately approved network-enabled build step**

Verify archive identity before extraction. Store wheels in a SHA-addressed external CAS, never Git or the final component. Root and backend `uv.lock` remain version authority; selection files are reviewed projections.

```bash
INPUT_ROOT=/home/thenam176/.local/share/trading-agent/release-inputs/v2
INPUT_TOOLING_COMMIT="$(git rev-parse HEAD)"
INPUT_TOOLING_TREE="$(git rev-parse HEAD^{tree})"
python3 scripts/validate_release_build_input_approval.py \
  --record "$RELEASE_BUILD_INPUT_APPROVAL_RECORD" \
  --expected-commit "$INPUT_TOOLING_COMMIT" \
  --expected-tree "$INPUT_TOOLING_TREE" \
  --expected-input-root "$INPUT_ROOT"
uv run --frozen python ops/release-v2/acquire-inputs.py fetch \
  --records-dir ops/release-v2/inputs \
  --input-root "$INPUT_ROOT" \
  --approval-record "$RELEASE_BUILD_INPUT_APPROVAL_RECORD" \
  --network-approved
uv run --frozen python ops/release-v2/acquire-inputs.py verify \
  --records-dir ops/release-v2/inputs \
  --input-root "$INPUT_ROOT" --offline
```

`--network-approved` is a technical acknowledgement and is accepted only when the separately reviewed build-input approval record also validates; it is never sufficient by itself.

- [ ] **Step 4: Materialize and seal the bootstrap standalone Python at the declared path**

Use the already committed, hash-validated materializer to extract the approved archive to the previously absent `$INPUT_ROOT/python/runtime/`, with final interpreter `$INPUT_ROOT/python/runtime/bin/python3.11`. Produce external `python-runtime-manifest.json` and `input-manifest.json`, fsync, remove write bits, then re-open and hash every path. Do not use a host-based venv. Reject UV/home/worktree/global-site references in shebangs, `.pth`, `pyvenv.cfg`, sysconfig, RPATH/RUNPATH, or text metadata. Prove relocation in two unrelated private roots before sealing the canonical input root.

- [ ] **Step 5: Close wheels, ABI, licenses, and the build driver**

Install only from local wheels using `--no-index`, `--find-links`, `--require-hashes`, and `--no-deps`. At this stage, build fixture/local component wheels twice with fixed `SOURCE_DATE_EPOCH` to qualify the tooling; source-derived release wheels are deliberately absent from the external CAS and are rebuilt twice from the final Task 14 archive in Task 15. Use static `readelf`, not `ldd`, for native closure. Remove pip/build tools/editable metadata after installation.

Materialize two independent copies of the build driver from the same pinned CPython/build-wheel inputs and require equal logical runtime manifests. Unit tests must prove `UV_OFFLINE=1`, `PIP_NO_INDEX=1`, isolated HOME/cache, no inherited Python/loader variables, network namespace enforcement, and fail-closed behavior when any control is unavailable.

- [ ] **Step 6: Reverify the sealed external closure and commit only sanitized deterministic evidence**

```bash
INPUT_ROOT=/home/thenam176/.local/share/trading-agent/release-inputs/v2
INPUT_TOOLING_COMMIT="$(python3 scripts/validate_release_build_input_approval.py \
  --record "$RELEASE_BUILD_INPUT_APPROVAL_RECORD" --print-field source_commit)"
INPUT_TOOLING_SCRATCH="$(mktemp -d /tmp/job-plane-input-tooling.XXXXXX)"
INPUT_TOOLING_EXPORT="$INPUT_TOOLING_SCRATCH/source"
git worktree add --detach "$INPUT_TOOLING_EXPORT" "$INPUT_TOOLING_COMMIT"
"$INPUT_ROOT/python/runtime/bin/python3.11" -I \
  "$INPUT_TOOLING_EXPORT/ops/release-v2/acquire-inputs.py" verify \
  --records-dir "$INPUT_TOOLING_EXPORT/ops/release-v2/inputs" \
  --input-root "$INPUT_ROOT" --offline --require-sealed
git add \
  docs/implementation/job-plane-hermetic-python-design.md \
  docs/implementation/job-plane-wheelhouse-closure.md
git commit -m "docs(release): record hermetic input closure"
git worktree remove "$INPUT_TOOLING_EXPORT"
```

`INPUT_TOOLING_EXPORT` is a safe materialization of `INPUT_TOOLING_COMMIT`, not the later mutable worktree. Evidence records its commit/tree, CPython artifact/interpreter hashes, input-manifest digest, wheel CAS digest, licenses, native ABI closure, and acquisition-record digest. Final source authority later binds these immutable values plus the unchanged policy/tool blobs.

**Stop condition:** unresolved Torch/CUDA/RDMA/MPI/UCX dependency or incomplete license/SBOM evidence. Reducing the backend dependency graph is a separate behavior-change phase and must not be hidden inside release closure.

---

### Task 10: Build sealed service/backend components and a migrator kit

**Files:**
- Create: `packages/runtime_release/release_manifests.py`
- Create: `ops/release-v2/build-component.py`
- Create: `ops/release-v2/build-candidate.py`
- Create: `ops/release-v2/compose-candidate.py`
- Modify: `packages/runtime_release/v2.py`
- Modify: `ops/release-v2/verify-stage.py`
- Modify: `docs/production/release-authority-v2.md`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-release-v2-hermetic-candidate.md`
- Test: `tests/runtime_release/test_v2.py`
- Test: `tests/runtime_release/test_v2_provisioning.py`
- Test: `tests/runtime_release/test_build_candidate.py`

**Interfaces:**
- Builders require explicit source archive, source commit/tree, component output, and authority output; tests use private temporary roots.
- Final staging path naming is applied only in Task 15 after the source commit is frozen.
- Dashboard and Node are excluded from this Job Plane release.
- Component/manifests never serialize physical build roots. They bind relative component paths plus the same commit-qualified logical `/opt/trading-agent-v2/releases/${COMMIT}` prefix; the standalone verifier receives an explicit physical-to-logical root mapping.
- `build-component.py build --component {app,backend,migrator} --source-archive PATH --source-commit SHA --source-tree TREE --input-root PATH --output PATH` safely materializes only the validated archive and refuses a checkout path or existing output.
- `build-candidate.py --source-archive PATH --source-commit SHA --source-tree TREE --input-root PATH --build-driver PATH --output-root PATH --logical-prefix PATH` orchestrates one fully offline fresh candidate and refuses a working-tree source or existing output.
- `compose-candidate.py compose --app-root PATH --backend-root PATH --migrator-root PATH --unit-root PATH --unit-manifest PATH --authority-output PATH --source-commit SHA` recomputes the exact unit file set/hashes/modes before creating the exact eight-file authority root and refuses existing, writable, or unsealed input.
- `verify-stage.py --authority-root PATH --app-root PATH --backend-root PATH --migrator-root PATH --unit-root PATH --expected-commit SHA --expected-aggregate-sha256 SHA256` is standalone, read-only, and returns nonzero on any undeclared byte/path.
- The migrator contains `maintenance-authority-manifest.json`, a canonical subset manifest listing exact destination-relative migrator, runbook, launcher, and validator files/hashes/modes plus source commit. It contains neither authority-file hashes nor the aggregate digest, avoiding a cycle. `release-manifest.json` binds its SHA-256. `verify-stage.py --maintenance-only --maintenance-root PATH --expected-commit SHA --expected-aggregate-sha256 SHA256 --expected-maintenance-manifest-sha256 SHA256` first verifies the copied eight-file authority DAG against the independent aggregate SHA, then reads the release-manifest-bound subset digest and verifies only the maintenance files. It never claims absent app/backend/unit roots were checked.

- [ ] **Step 1: Write manifest-shape RED tests**

Authority root must contain exactly `release-manifest.json`, `python-runtime-manifest.json`, `wheelhouse-manifest.json`, `installed-packages-manifest.json`, `command-manifest.json`, `semantic-input-manifest.json`, `aggregate-manifest.json`, and `promotion-record.json`. Reject null digests, unknown keys, extra files, mutable paths, and circular digest dependencies.

Define the acyclic digest DAG explicitly: the first six manifests plus `promotion-record.json` are seven canonical-JSON leaves and may not reference `aggregate-manifest.json`; `release-manifest.json` binds component/file manifests, source policies, unit-bundle digest, and verifier identity, while promotion binds only candidate state/source identity. `aggregate-manifest.json` contains a fixed-order mapping of the seven leaf names to SHA-256 plus the aggregate logical digest, but never its own file hash. Protected external final-release evidence supplies the independent expected SHA-256 of canonical `aggregate-manifest.json`; standalone verification requires that expected value.

- [ ] **Step 2: Exercise application, backend, and migrator builders independently**

Builders materialize source only through Task 8's exact app/backend/migrator policies; they never use the root wheel's broad package discovery as release scope. In disposable fixture roots, application smoke imports only `apps.job_api.main` and `services.job_worker.main`. Backend verification is limited to import smoke, exact SNAPSHOT argv construction, empty/research-only child-environment validation, and fixture result-validator tests; it never invokes SNAPSHOT or any research/provider code. The maintenance-only migrator kit contains its own hermetic Python closure, Alembic/config, the complete exact migration graph through 0007, `ops/postgres/provision-job-roles.sql`, database/release authority verifiers, exact V2 recovery and migration runbooks, their fixed launchers/approval validators, and the bound maintenance-subset manifest. Offline smoke loads the revision graph and verifies every migration/tool/runbook/subset hash without connecting to a database. All three components are read-only and contain no `.git`, database, log, report, secret, UV path, or source checkout reference. Before composition, fsync every task-owned component/unit file and directory, remove write bits, re-open/re-hash by stable descriptor, and reject any path/inode/mode change. These are tooling tests, not the final candidate build.

- [ ] **Step 3: Compose candidate authority**

Aggregate authority binds final root commit, reviewed backend subtree, component roots, CPython archive/interpreter identities, locks/wheels, exact SNAPSHOT cwd/interpreter/argv/timeout/validator, semantic hashes, verifier, 0007 database ancestry, normalized API/worker unit-template policy, and the exact external `systemd-bundle-manifest.json` digest. Rendered path-specific units are not placed inside the eight-file authority root.

- [ ] **Step 4: Make every newly composed candidate fail closed by default**

```text
NO_GO
NOT_INSTALLED
NOT_RUNNING
```

The promotion state is a strict enum: `NO_GO`, `CANDIDATE_VERIFIED`, or future `INSTALLED` (not used in this plan). Composition can emit only `NO_GO`; it cannot accept a caller-supplied override. `CANDIDATE_VERIFIED` is created only by Task 15's copy-on-promote gate after all reproducibility, tamper, systemd, and independent-review evidence exists. No activation or runtime database health claim belongs in the static build.

- [ ] **Step 5: Run focused tests and commit the builder**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_provisioning.py \
  tests/runtime_release/test_build_candidate.py
git add packages/runtime_release/release_manifests.py \
  packages/runtime_release/v2.py \
  ops/release-v2/build-component.py \
  ops/release-v2/build-candidate.py \
  ops/release-v2/compose-candidate.py \
  ops/release-v2/verify-stage.py \
  docs/production/release-authority-v2.md \
  docs/implementation/job-plane-release-v2-hermetic-candidate.md \
  tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_provisioning.py \
  tests/runtime_release/test_build_candidate.py
git commit -m "release: compose sealed Release Authority v2 candidates"
```

---

### Task 11: Implement reproducibility, tamper detection, and exact systemd verification

**Files:**
- Create: `ops/release-v2/compare-builds.py`
- Create: `ops/release-v2/tamper-candidate.py`
- Create: `ops/release-v2/promote-candidate.py`
- Create: `packages/runtime_release/source_authority.py`
- Create: `schemas/final-source-authority.schema.json`
- Create: `schemas/release-candidate-promotion.schema.json`
- Create: `scripts/validate_final_source_authority.py`
- Create: `scripts/materialize_release_source.py`
- Create: `tests/runtime_release/test_release_reproducibility.py`
- Create: `tests/runtime_release/test_release_tamper.py`
- Create: `tests/runtime_release/test_release_promotion.py`
- Create: `tests/runtime_release/test_systemd_staging.py`
- Create: `tests/runtime_release/test_source_authority.py`
- Modify: unit rendering in `packages/runtime_release/v2.py`
- Modify: `ops/release-v2/provision-root.sh`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-reproducibility-evidence.md`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-tamper-evidence.md`
- Update: `docs/implementation/job-plane-systemd-staging-verification.md`

**Interfaces:**
- Produces: tested tooling for two-build logical equality, full tamper rejection, and units bound to real staging paths. Task 15 applies it to the frozen final commit.
- Units are written to a separate sealed `UNIT_ROOT`, never under the authority root. The authoritative bundle renders the same commit-qualified logical `/opt/trading-agent-v2/releases/${COMMIT}/...` paths in both independent builds; `UNIT_ROOT/systemd-bundle-manifest.json` binds source commit, those logical paths, normalized template policy, and rendered unit hashes. The aggregate manifest then binds that unit-manifest digest. Staging-path units are a separate, non-authoritative verification bundle. The units do not contain the aggregate digest, so no circular hash exists.
- A canonical external staging-verification envelope binds the full candidate aggregate/authoritative-unit digest, staging unit-bundle digest, intended absolute staging prefix, materialization-tool blob, and systemd output hashes. It never replaces `UNIT_ROOT` inside candidate authority. Promotion binds this envelope digest as qualification evidence.
- `compare-builds.py` requires explicit left/right app, backend, migrator, unit, and authority roots plus the expected commit.
- `tamper-candidate.py` requires explicit pristine app/backend/migrator/unit/authority roots, a pristine input-CAS root, independent expected aggregate/input digests, and a private scratch root. It runs one named case or `--case all` and never edits pristine inputs.
- `promote-candidate.py promote-pair` is copy-on-promote only. It accepts two byte-equal sealed `NO_GO` candidates plus a protected, independently reviewed one-use promotion record binding source authority, A/B/C comparison, tamper, staging-intent/final-intent systemd, aggregate, and both private output identities. In one operation it copies to two previously absent destinations, changes only each promotion leaf, recomputes aggregate authority, requires logical equality, fsyncs/seals, and emits `CANDIDATE_VERIFIED / NOT_INSTALLED / NOT_RUNNING`. It can never emit `INSTALLED`, mutate a quarantine input, publish staging, or touch `/opt`.
- The promotion leaf binds only the external pre-promotion qualification-record digest, never the final aggregate that contains that leaf. The final promoted A/B comparison supplies the independent expected final aggregate SHA-256, preserving the acyclic digest DAG.
- `source_authority.py` validates a protected external final-source record containing exact commit/tree/backend subtree, scoped-commit list, final verification-log digest, and Git blob hashes for every build/compose/compare/tamper/promotion/verifier/provisioning tool. Protected copies of the validator and safe source-archive materializer are pinned by the external `SOURCE_RELEASE_IMPLEMENTATION` approval, not by the record they parse. Task 15 may not derive release identity from ambient `HEAD`.
- `compose-candidate.py render-units --authority-path LOGICAL_PATH --app-source PATH --app-path LOGICAL_PATH --backend-source PATH --backend-path LOGICAL_PATH --unit-output PATH --source-commit SHA --path-mode {staging,final-intent}` validates physical sources separately from rendered logical paths and refuses scheduler/timer output or existing targets.
- `compose-candidate.py materialize-path-intent --verification-root PATH --logical-prefix ABSOLUTE_PATH --app-root PATH --backend-root PATH --migrator-root PATH --authority-root PATH --authoritative-unit-root PATH --verification-unit-root PATH --source-commit SHA --materialization-output PATH` first verifies the candidate against its authoritative unit root, separately verifies the non-authoritative staging unit bundle against the normalized template policy, materializes candidate bytes at the intended prefix and staging units only under `/etc/systemd/user`, then writes a canonical materialization record. After systemd runs, `compose-candidate.py seal-systemd-envelope --materialization-record PATH --stdout PATH --stderr PATH --output PATH` requires successful warning-free logs and writes the external envelope. Both commands reject digest substitution, `/`, traversal, existing output, and paths outside the approved staging or `/opt/trading-agent-v2/releases/${COMMIT}` prefixes.
- `compose-candidate.py materialize-final-intent --verification-root PATH --app-root PATH --backend-root PATH --migrator-root PATH --authority-root PATH --unit-root PATH --source-commit SHA --materialization-output PATH` copies only sealed task-owned inputs beneath the commit-qualified logical paths in the disposable verification root, emits a canonical materialization record for the same envelope finalizer, and never writes `/opt` itself.
- `provision-root.sh` accepts only named operation-specific protocols plus an independently supplied expected aggregate-manifest SHA-256. Fake-root tests qualify the complete split app/backend/migrator/unit/authority form for future use, but this plan authorizes only `--maintenance-only --candidate-root ... --destination ...`; that form verifies all five candidate subroots, rejects individual replacement-root, environment, or service arguments, and copies only authority plus the maintenance-filtered migrator containing exact runbooks/launchers. It rejects the legacy positional/monolithic protocol. Before any copy it hashes the migrator-contained `verify-stage.py`, compares that hash with an independently reviewed literal pin in the script, and invokes that sealed verifier over all candidate subroots with the external expected digest.

- [ ] **Step 1: Build twice in unrelated private fixture roots**

Compare source commit, backend subtree, CPython archive/interpreter hashes, locks, wheel selections, installed distributions, command/semantic manifests, complete file lists, and aggregate logical digest. Any binary exception must be path-specific and reviewed.

The safe archive materializer runs under pinned standalone CPython, rejects traversal, links, special files, duplicate/case-colliding paths, undeclared files, and mode/type drift, and proves the extracted paths against the exact Git tree/source policies before any exported code executes.

- [ ] **Step 2: Run every tamper case**

Modify application source, backend semantic input, migrator migration/role SQL, embedded interpreter, an installed distribution payload, a copied wheel in a private CAS clone, command argv, the sealed verifier, a rendered unit or its bundle manifest, add an unexpected executable, remove a manifest, and change promotion state. Component verification and input-CAS verification must each fail against the original independent expected digests. Rebuild a third throwaway candidate from pristine sealed inputs afterward and compare it to the first; merely reverifying an untouched candidate is insufficient.

- [ ] **Step 3: Render only API and worker units**

Require existing embedded interpreter/module/cwd, loopback 8401, worker concurrency one, distinct DB roles, no UV/worktree/global Python, no secret unit bytes, cleared Python/loader injection variables, and exact read-only app/backend/authority binds. Emit only the two units plus a non-secret `systemd-bundle-manifest.json` into `UNIT_ROOT`; do not emit scheduler service or timer. Materialize any required non-secret verification-only environment files inside the private verification root so `systemd-analyze` never resolves mutable operator files.

- [ ] **Step 4: Verify without installation**

```bash
systemd-analyze --user --recursive-errors=yes verify \
  "$UNIT_ROOT/trading-job-api.service" \
  "$UNIT_ROOT/trading-job-worker.service"
```

Capture stdout and stderr separately and require exit zero, empty stderr, and no warning/error text. Assert the unit bundle contains exactly the API unit, worker unit, and manifest. Also verify final-intent `/opt/trading-agent-v2/...` units in a disposable `--root` tree with `--recursive-errors=yes` and the same zero-warning rule. Do not install units, reload the daemon, or start a service. Results here qualify the tooling; final candidate paths are verified again in Task 15.

- [ ] **Step 5: TDD copy-on-promote authority**

Tests must reject promotion before every qualification digest is present, same reviewer/requester, expired or wrong-source record, a candidate not in exact `NO_GO`, any changed component/unit/input digest, pre-existing destination, in-place mutation, `INSTALLED`/`RUNNING`, and a second/replayed use. Two independent promotions from byte-equal NO_GO candidates must produce byte-equal logical authority. Promotion failures leave source quarantine roots unchanged and no destination labeled verified.

- [ ] **Step 6: Replace the legacy provisioning protocol in fake roots**

Write RED tests proving the old monolithic/positional invocation, omitted required roots, working-tree verifier path, mismatched aggregate digest, mutable root, scheduler/timer unit, and verifier-pin drift all fail before any destination write. For `--maintenance-only`, also reject any app/backend/unit/env/service argument, missing or mismatched maintenance-subset manifest, absent sealed runbook/launcher, full-candidate verifier masquerading as subset verification, or destination outside the exact maintenance prefix. Fake-root tests model the independently approval-pinned protected-launcher copy/hash-before-execute protocol and reject execution from a user-writable path. Implement only the named protocols above. This task never writes `/opt`, invokes `systemctl`, or installs units.

- [ ] **Step 7: Re-pin the privileged verifier only after its final bytes freeze**

Compute the final SHA-256 of `ops/release-v2/verify-stage.py`, update the independent literal pin in `ops/release-v2/provision-root.sh`, and add a test that recomputes and requires equality. The pin must not be derived from a manifest controlled by the same candidate. Any later verifier edit invalidates Task 11 and requires a new pin, tests, clean-source freeze, and release build.

- [ ] **Step 8: Run focused tests and commit verification tooling**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/runtime_release/test_release_reproducibility.py \
  tests/runtime_release/test_release_tamper.py \
  tests/runtime_release/test_release_promotion.py \
  tests/runtime_release/test_systemd_staging.py \
  tests/runtime_release/test_source_authority.py \
  tests/runtime_release/test_v2_provisioning.py
git add ops/release-v2/compare-builds.py \
  ops/release-v2/tamper-candidate.py \
  ops/release-v2/promote-candidate.py \
  ops/release-v2/provision-root.sh \
  packages/runtime_release/source_authority.py \
  packages/runtime_release/v2.py \
  schemas/final-source-authority.schema.json \
  schemas/release-candidate-promotion.schema.json \
  scripts/validate_final_source_authority.py \
  scripts/materialize_release_source.py \
  tests/runtime_release/test_release_reproducibility.py \
  tests/runtime_release/test_release_tamper.py \
  tests/runtime_release/test_release_promotion.py \
  tests/runtime_release/test_systemd_staging.py \
  tests/runtime_release/test_source_authority.py \
  tests/runtime_release/test_v2_provisioning.py \
  docs/implementation/job-plane-reproducibility-evidence.md \
  docs/implementation/job-plane-tamper-evidence.md \
  docs/implementation/job-plane-systemd-staging-verification.md
git commit -m "release: verify reproducibility tamper and systemd paths"
```

**Exit gate:** pre-freeze builds have equal logical authority, all tamper cases reject, both systemd verification modes exit zero with no warnings, the future installer accepts only the split sealed protocol, and ports/services remain unchanged.

---

### Task 12: Bind recovery to sealed source authority without touching the dirty canonical checkout

**Files:**
- Preserve unchanged: `docs/production/runbooks/postgresql-preserve-recover.md`
- Create: `docs/production/runbooks/postgresql-preserve-recover-v2.md`
- Modify: `schemas/postgres-recovery-approval-record.schema.json`
- Modify: `ops/postgres/postgres-recovery-approval-record.example.yaml`
- Modify: `ops/postgres/pending/postgres-recovery-approval-data-001-preparation.yaml`
- Modify: `scripts/validate_postgres_recovery_approval.py`
- Modify: `tests/production/test_postgres_recovery_approval.py`
- Update: `docs/implementation/postgres-recovery-approval-review.md`

**Interfaces:**
- Resolves the hardcoded mutable `REPO=/home/thenam176/projects/trading-agent` blocker without cleaning, replacing, symlinking, or overwriting that dirty checkout.
- V2 still consumes exactly the runbook's 50 ordered literal-TAB fields. It uses existing `SOURCE_COMMIT`, `SOURCE_TREE`, `RUNBOOK_SHA256`, `MIGRATION_SHA256`, and `CHANGE_ARTIFACT[_SHA256]` bindings; it does not repurpose a field ambiguously or reduce any identity check.
- Runtime recovery never trusts the user-writable staging candidate. The sealed maintenance root is derived, not supplied: `/opt/trading-agent-v2/maintenance/${SOURCE_COMMIT}`. It contains root-owned `migrator/` (including `runbooks/`) and `authority/` subroots provisioned only by Task 16 under separate authority. The matching aggregate digest and independent verifier pin must prove source commit/tree, complete migration graph, exact sealed runbook/launcher bytes, and migration hash. Tests emulate this layout under a private verification root; this task does not write `/opt`.

- [ ] **Step 1: Write RED source-identity tests**

Require V1 to remain byte-identical. V2 must reject the dirty canonical checkout, an arbitrary source path, symlink/hardlink alias, wrong owner/mode, absent/mutable migrator root, wrong commit/tree, runbook drift, migration drift, manifest drift, verifier-pin mismatch, and promotion other than `CANDIDATE_VERIFIED / NOT_INSTALLED / NOT_RUNNING`. It must perform these checks before any mkdir, PostgreSQL command, service action, or target write.

- [ ] **Step 2: Implement immutable-source preflight**

Replace only V1's Git-worktree source preflight in the new V2 runbook. V2 derives the commit-qualified root-owned maintenance path, opens the sealed runbook/launcher/migrator/authority files by stable descriptors, verifies ownership/modes and independent pinned verifier bytes, validates the exact eight-manifest authority plus migrator file manifest, and checks 0004 migration hash from the sealed component. Keep every PGDATA, process, listener, link-count, evidence, approval, safety, one-start, stop, backup, restore, and forbidden-action gate from V1 unchanged or stricter.

- [ ] **Step 3: Re-extract and prove the exact 50-field preparation contract**

Update schema/template/validator only for the new immutable runbook version/hash and derived-source evidence. Tests must still prove exactly 50 transcript keys, 67 human/current preparation sentinels unless an independently reviewed schema change documents a different count, `additionalProperties=false`, permanent `YAML_PREPARATION_ONLY`, and no automatic conversion/signature.

- [ ] **Step 4: Run focused tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/production/test_postgres_recovery_approval.py
git add docs/production/runbooks/postgresql-preserve-recover-v2.md \
  schemas/postgres-recovery-approval-record.schema.json \
  ops/postgres/postgres-recovery-approval-record.example.yaml \
  ops/postgres/pending/postgres-recovery-approval-data-001-preparation.yaml \
  scripts/validate_postgres_recovery_approval.py \
  tests/production/test_postgres_recovery_approval.py \
  docs/implementation/postgres-recovery-approval-review.md
git commit -m "ops(postgres): bind recovery to sealed source authority"
```

**Exit gate:** V1 is unchanged; V2 is at least as strict and can validate a future Task 16 root-owned maintenance kit without touching either dirty checkout or user-writable staging candidate. No approval exists yet.

---

### Task 13: Define a separate one-attempt runtime migration authority

**Files:**
- Create: `schemas/job-plane-runtime-migration-backup-approval.schema.json`
- Create: `schemas/job-plane-runtime-migration-approval.schema.json`
- Create: `ops/postgres/job-plane-runtime-migration-backup-approval.example.json`
- Create: `ops/postgres/job-plane-runtime-migration-approval.example.json`
- Create: `scripts/validate_job_plane_runtime_migration_backup_approval.py`
- Create: `scripts/validate_job_plane_runtime_migration_approval.py`
- Create: `tests/production/test_job_plane_runtime_migration_backup_approval.py`
- Create: `tests/production/test_job_plane_runtime_migration_approval.py`
- Create: `ops/postgres/verify-job-roles-pre-0005.sql`
- Update: `docs/production/runbooks/job-plane-transition-authority-rollout.md`
- Create: `docs/implementation/job-plane-runtime-migration-approval-review.md`

**Interfaces:**
- Creates two new non-interchangeable authority contracts; the 50-field recovery record never authorizes either migration backup or runtime migration.
- The backup-only record binds exact read-only entry-state verification, one custom dump/isolated restore operation, protected output paths, and a controlled stop. It permits no target role/schema mutation.
- Canonical JSON is mode 0600, contains no credential/DSN, forbids unknown/missing/placeholders, and binds one operation, one target, one source/release, one backup/restore proof, one entry state, and one attempt within a short validity window.
- Allowed entry states are `PRE_ROLE_PROVISION_0004`, `POST_ROLE_PROVISION_PRE_MIGRATION`, `POST_0005`, and `POST_0006`. The latter three are continuation-only and bind prior successful evidence plus exact current catalog/head; none authorizes role rotation or downgrade.

- [ ] **Step 1: Freeze the backup-only authority schema and negative tests**

Bind target/entry identity, exact root-owned maintenance kit, one target-cluster start/read/stop sequence, permitted dump/isolated-restore command IDs, output/temporary-cluster roots, expiry/nonce, reviewer separation, and stop conditions. Reject every role/schema SQL command, non-read-only target statement, Job service action, pre-existing output, wrong head/catalog/count, additional target start, or attempt to continue after producing evidence. Its successful terminal state is a stopped target plus `BACKUP_RESTORE_EVIDENCE_READY — STOP FOR MIGRATION REVIEW`.

- [ ] **Step 2: Freeze the separate migration authority schema and negative tests**

Bind approval/version/status, created/expiry, distinct operator/reviewer decisions and timestamps, cluster/system ID, host/port/database and PGDATA fingerprint, exact entry head/counts/job counts, paper/false/false/service/port state, the already completed fresh backup path/mode/hash and restore-evidence hash, final source commit/tree, root-owned maintenance-kit and aggregate/migrator digests, embedded interpreter hash, role-script/read-only-role-verifier hashes, migration 0005/0006/0007 hashes, authority-manifest digest, permitted command IDs, entry state, one-attempt nonce, stop conditions, rollback reference, and canonical record digest. Reject expired/replayed records, target drift since backup, missing/unreviewed backup evidence, shared-role reuse, service activity, wrong entry state, or any extra command. This record cannot be signed until the backup-only operation has stopped and two humans have reviewed its exact evidence.

- [ ] **Step 3: Implement a read-only exact post-provision verifier**

`verify-job-roles-pre-0005.sql` must run only at exact 0004 and prove the three new roles, disabled legacy `trading_jobs`, role flags/memberships/settings, database/schema/object ownership, zero job rows, and absence of 0005 schema changes. It never creates/alters a role, reads password verifiers, or prints settings values. The main authority verifier supplies exact POST_0005/POST_0006 catalog checks. Together they enable reviewed continuation after role provisioning or an ancestor migration succeeded.

- [ ] **Step 4: Rewrite the future migration runbook order**

The sealed runbook has an explicit two-authority pause. First it validates the backup-only record, verifies the entry state, creates/restores the fresh dump, seals evidence, and stops without target role/schema mutation. Only after a separate human-reviewed migration record binds that evidence may a new invocation revalidate unchanged entry state and proceed. For `PRE_ROLE_PROVISION_0004`, it then executes the existing transactional protected-stdin first-use role script, seals `POST_ROLE_PROVISION_PRE_MIGRATION` evidence, and proceeds. For continuation, it skips completed steps only after the appropriate read-only verifier exactly matches the bound prior evidence and head. It executes only remaining forward migrations from the root-owned sealed maintenance kit and has no service, timer, enqueue, or SNAPSHOT step.

- [ ] **Step 5: Run focused tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/production/test_job_plane_runtime_migration_backup_approval.py \
  tests/production/test_job_plane_runtime_migration_approval.py \
  tests/jobs/test_job_role_permissions.py
git add schemas/job-plane-runtime-migration-backup-approval.schema.json \
  schemas/job-plane-runtime-migration-approval.schema.json \
  ops/postgres/job-plane-runtime-migration-backup-approval.example.json \
  ops/postgres/job-plane-runtime-migration-approval.example.json \
  scripts/validate_job_plane_runtime_migration_backup_approval.py \
  scripts/validate_job_plane_runtime_migration_approval.py \
  tests/production/test_job_plane_runtime_migration_backup_approval.py \
  tests/production/test_job_plane_runtime_migration_approval.py \
  ops/postgres/verify-job-roles-pre-0005.sql \
  docs/production/runbooks/job-plane-transition-authority-rollout.md \
  docs/implementation/job-plane-runtime-migration-approval-review.md
git commit -m "ops(postgres): define one-attempt job migration authority"
```

**Exit gate:** both schemas/validators and the sealed two-invocation runbook are source-verified; examples remain `DRAFT_NOT_AUTHORIZED`; no runtime record is fabricated and backup evidence cannot authorize mutation.

---

### Task 14: Freeze and verify the final clean source candidate

**Files:**
- Create before the final test run: `docs/implementation/job-plane-clean-candidate-v4.md`
- Create before the final test run: `docs/implementation/job-plane-authority-preparation-final.md`
- Create before the final test run: `docs/implementation/job-plane-residual-risks-v3.md`
- External evidence only: exact final verification log and its SHA-256
- External evidence only: mode-0600 final-source authority record and mode-0500 protected copies of its validated source-authority validator, safe archive materializer, and reviewed provisioning launcher

**Interfaces:**
- Produces: the exact clean commit/tree consumed by Task 15.
- Avoids impossible self-reference: a file committed inside the candidate does not claim to contain its own Git commit hash. The external verification record and release manifest bind final `HEAD`.
- The external record binds final commit/tree, reviewed backend subtree/per-file policy, scoped commit list, verification-log digest, acquisition-record digest, immutable input/CAS manifest digest, source-policy blobs, and Git blob hashes for every build, compose, compare, tamper, promotion, verifier, and provisioning tool. It is reviewed outside Git and contains no secret.
- Task 15 consumes this protected record; it must not discover or substitute identity from ambient `HEAD`.

- [ ] **Step 1: Require a clean source tree before writing the deterministic gate document**

Record scoped commits, dependency changes, excluded old dirty-worktree paths, test commands, stop conditions, and the rule that dynamic final identity/evidence remains external. Do not copy logs, runtime evidence, build outputs, approvals, or placeholders into Git.

- [ ] **Step 2: Commit the deterministic gate document**

```bash
git add docs/implementation/job-plane-clean-candidate-v4.md \
  docs/implementation/job-plane-authority-preparation-final.md \
  docs/implementation/job-plane-residual-risks-v3.md
git commit -m "docs: define final clean candidate evidence gate"
git diff --check
git status --short --branch
```

Expected: status is clean. The documentation-only commit must not change `git rev-parse HEAD:legacy/research-backend` from the reviewed subtree.

- [ ] **Step 3: Run complete verification on that exact final commit**

The final commit differs from the earlier RED/GREEN proof commits, so obtain two new short-lived, exact-final-commit records: one `DISPOSABLE_PG_RED`, one `DISPOSABLE_PG_GREEN`. They remain non-interchangeable. First run the broad suite with all disposable controls unset; every database-starting operation must be an explicit intended skip. Then run only the reviewed RED operation list under the RED record and only the reviewed GREEN/restore operation list under the GREEN record.

```bash
FINAL_COMMIT="$(git rev-parse HEAD)"
FINAL_TREE="$(git rev-parse HEAD^{tree})"
make audit
make check-contracts
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_RED_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_RED \
  --expected-commit "$FINAL_COMMIT" \
  --expected-tree "$FINAL_TREE"
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_GREEN_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_GREEN \
  --expected-commit "$FINAL_COMMIT" \
  --expected-tree "$FINAL_TREE"
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs tests/control_api/test_alembic_schema.py
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
  TRADING_TEST_DISPOSABLE_APPROVAL_RECORD="$TRADING_TEST_DISPOSABLE_RED_APPROVAL_RECORD" \
  TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_RED \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
  TRADING_TEST_DISPOSABLE_APPROVAL_RECORD="$TRADING_TEST_DISPOSABLE_GREEN_APPROVAL_RECORD" \
  TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_GREEN \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  tests/jobs/test_job_transition_authority.py \
  tests/jobs/test_job_role_permissions.py \
  tests/jobs/test_repository_transition_capabilities.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q tests/runtime_release
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/production/test_postgres_recovery_approval.py
cd apps/dashboard
npm test
./node_modules/.bin/tsc --noEmit
npm run lint
npm run build
cd ../..
git diff --check
test "$(git rev-parse HEAD)" = "$FINAL_COMMIT"
test "$(git rev-parse HEAD^{tree})" = "$FINAL_TREE"
git status --short --branch
```

Also run the documented isolated backend offline suite, standalone integration suite, and Phase 1 safety suite. Save fresh totals, exact command lines, timestamps, exit codes, `FINAL_COMMIT`, `FINAL_TREE`, and a sanitized log digest in the protected external evidence root. Require zero modified, staged, and untracked paths when all checks finish.

- [ ] **Step 4: Seal and independently review final source authority**

Create the external record from the already captured `FINAL_COMMIT`/`FINAL_TREE`; do not recompute those values in Task 15. Require the reviewed backend subtree to be unchanged, all recorded tool/source-policy blob IDs to resolve at that exact commit, the Task 9 input-policy blobs and acquisition/CAS hashes to match, and the verification log digest to match the completed run. Copy the committed validator, source materializer, and provisioning launcher to the protected evidence root, record their Git blobs and file SHA-256 values, make all three non-writable, then validate the record with pinned CPython in isolated mode. The future `MAINTENANCE_KIT_PROVISIONING` approval independently pins the protected launcher hash before elevation; the launcher's self-declared pins are never the trust root.

```bash
PINNED_INPUT_PYTHON="$RELEASE_INPUT_ROOT/python/runtime/bin/python3.11"
"$PINNED_INPUT_PYTHON" -I scripts/validate_final_source_authority.py \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" \
  --expected-commit "$FINAL_COMMIT" \
  --expected-tree "$FINAL_TREE" \
  --expected-verification-log-sha256 "$FINAL_VERIFICATION_LOG_SHA256"
test "$(stat -c '%a' "$FINAL_SOURCE_AUTHORITY_RECORD")" = 600
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

The record must be canonical, placeholder-free, and independently reviewed. If any source byte changes afterward, invalidate the record and repeat Task 14 from the test run.

**Exit gate:** exact final commit is clean and fully green; the protected final-source authority is valid and reviewed; target runtime PostgreSQL and every Job service remain untouched.

---

### Task 15: Build and verify the final immutable staging candidate

**Files:**
- Read only: protected final-source authority and exact source commit from Task 14
- External outputs only: application, backend, migrator, authority, and unit roots plus exact logs/manifests under the approved release-candidate/evidence roots

**Interfaces:**
- Produces: a static `CANDIDATE_VERIFIED / NOT_INSTALLED / NOT_RUNNING` authority bound to the exact Task 14 commit.
- Makes no Git change. If any source or deterministic documentation must change, abandon the build, return to Task 14, create a new commit, and rebuild from zero.
- `COMMIT`, `TREE`, backend subtree, tool hashes, and input-policy hashes come only from the validated external final-source record. Ambient `HEAD` is checked for equality but never used as authority.
- Final staging publication is one atomic parent rename to `/home/thenam176/.local/share/trading-agent/release-candidates/job-plane-${COMMIT}/`, containing exact `app/`, `backend/`, `migrator/`, `authority/`, and `units/` subroots. No commit-qualified public staging path exists while state is `NO_GO`.
- Both builds use separately materialized build drivers from the pinned standalone CPython and local build-wheel CAS. No command in this task invokes `uv`, a host Python, a global site-package, or a working-tree script.

- [ ] **Step 1: Validate source authority and create two independent source exports**

Invoke the protected Task 14 copy of `validate_final_source_authority.py` with the checksum-pinned standalone input Python. Its `--print-field` mode returns one already-validated scalar and rejects control characters or noncanonical records. Verify the local repository contains that exact commit/tree/backend subtree and is clean; equality is a check, not a source of identity. Create two independent `git archive` exports of that exact commit, require identical archive hashes, extract them into unrelated private roots, and revalidate their file/blob policies before executing any exported tool.

```bash
BASE=/home/thenam176/.local/share/trading-agent/release-candidates
INPUTS=/home/thenam176/.local/share/trading-agent/release-inputs/v2
EVIDENCE=/home/thenam176/.local/share/trading-agent/release-evidence/v2
PINNED_INPUT_PYTHON="$INPUTS/python/runtime/bin/python3.11"
TRUSTED_SOURCE_VALIDATOR="$EVIDENCE/tools/validate_final_source_authority.py"
TRUSTED_SOURCE_MATERIALIZER="$EVIDENCE/tools/materialize_release_source.py"
test "$(sha256sum "$PINNED_INPUT_PYTHON" | cut -d' ' -f1)" = \
  "$SOURCE_RELEASE_APPROVED_INPUT_PYTHON_SHA256"
test "$(sha256sum "$TRUSTED_SOURCE_VALIDATOR" | cut -d' ' -f1)" = \
  "$SOURCE_RELEASE_APPROVED_VALIDATOR_SHA256"
test "$(sha256sum "$TRUSTED_SOURCE_MATERIALIZER" | cut -d' ' -f1)" = \
  "$SOURCE_RELEASE_APPROVED_MATERIALIZER_SHA256"

COMMIT="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field source_commit)"
TREE="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field source_tree)"
BACKEND_TREE="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field backend_subtree)"
EXPECTED_INPUT_DIGEST="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field input_manifest_sha256)"
EXPECTED_VERIFIER_SHA="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field release_verifier_sha256)"

test "$(git rev-parse HEAD)" = "$COMMIT"
test "$(git rev-parse HEAD^{tree})" = "$TREE"
test "$(git rev-parse HEAD:legacy/research-backend)" = "$BACKEND_TREE"
test -z "$(git status --porcelain=v1 --untracked-files=all)"

EXPORT_A="$(mktemp -d "$BASE/.source-a-$COMMIT.XXXXXX")"
EXPORT_B="$(mktemp -d "$BASE/.source-b-$COMMIT.XXXXXX")"
git archive --format=tar -o "$EXPORT_A/source.tar" "$COMMIT"
git archive --format=tar -o "$EXPORT_B/source.tar" "$COMMIT"
test "$(sha256sum "$EXPORT_A/source.tar" | cut -d' ' -f1)" = \
  "$(sha256sum "$EXPORT_B/source.tar" | cut -d' ' -f1)"
"$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_MATERIALIZER" \
  --archive "$EXPORT_A/source.tar" --output "$EXPORT_A/source" \
  --authority-record "$FINAL_SOURCE_AUTHORITY_RECORD"
"$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_MATERIALIZER" \
  --archive "$EXPORT_B/source.tar" --output "$EXPORT_B/source" \
  --authority-record "$FINAL_SOURCE_AUTHORITY_RECORD"
```

- [ ] **Step 2: Build two candidates with independently bootstrapped offline drivers**

Bootstrap `DRIVER_A` and `DRIVER_B` from the exact standalone-Python/build-wheel input closure, then invoke each commit-exported `run-offline-build.py` under its own driver. The wrapper must prove a no-network namespace, isolated HOME/cache, cleared Python/loader variables, `PIP_NO_INDEX=1`, `UV_OFFLINE=1`, and source-archive-only input before it invokes `build-candidate.py`. Each build creates app, backend, migrator, authoritative final-intent unit, and authority subroots in a new private output root.

```bash
SCRATCH_A="$(mktemp -d "$BASE/.build-a-$COMMIT.XXXXXX")"
SCRATCH_B="$(mktemp -d "$BASE/.build-b-$COMMIT.XXXXXX")"
DRIVER_A="$SCRATCH_A/driver"
DRIVER_B="$SCRATCH_B/driver"
BUILD_A="$SCRATCH_A/candidate"
BUILD_B="$SCRATCH_B/candidate"
FINAL_PREFIX="/opt/trading-agent-v2/releases/$COMMIT"

"$PINNED_INPUT_PYTHON" -I "$EXPORT_A/source/ops/release-v2/bootstrap-build-driver.py" \
  --input-root "$INPUTS" --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --output "$DRIVER_A"
"$PINNED_INPUT_PYTHON" -I "$EXPORT_B/source/ops/release-v2/bootstrap-build-driver.py" \
  --input-root "$INPUTS" --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --output "$DRIVER_B"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/run-offline-build.py" \
  --driver-root "$DRIVER_A" --input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --builder "$EXPORT_A/source/ops/release-v2/build-candidate.py" -- \
  --source-archive "$EXPORT_A/source.tar" --source-commit "$COMMIT" \
  --source-tree "$TREE" --input-root "$INPUTS" --build-driver "$DRIVER_A" \
  --output-root "$BUILD_A" \
  --logical-prefix "$FINAL_PREFIX"
"$DRIVER_B/runtime/bin/python3.11" -I \
  "$EXPORT_B/source/ops/release-v2/run-offline-build.py" \
  --driver-root "$DRIVER_B" --input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --builder "$EXPORT_B/source/ops/release-v2/build-candidate.py" -- \
  --source-archive "$EXPORT_B/source.tar" --source-commit "$COMMIT" \
  --source-tree "$TREE" --input-root "$INPUTS" --build-driver "$DRIVER_B" \
  --output-root "$BUILD_B" \
  --logical-prefix "$FINAL_PREFIX"
```

- [ ] **Step 3: Compare and seal the independent NO_GO aggregate without publishing**

Require equality of source/tree/backend identity, all three component identities, CPython archive/interpreter, locks, wheel/installed-package manifests, commands, semantic inputs, migration authority through 0007, exact unit bundle, complete file lists, `NO_GO / NOT_INSTALLED / NOT_RUNNING`, and aggregate logical digest. `compare-builds.py` writes a mode-0600 external comparison record containing both independently computed `aggregate-manifest.json` SHA-256 values. Review that record, require equality, and use its value as the independent expected NO_GO aggregate digest; it is not read from a candidate-controlled pointer. Keep both builds only in private quarantine roots.

```bash
QA_APP="$BUILD_A/app"
QA_BACKEND="$BUILD_A/backend"
QA_MIGRATOR="$BUILD_A/migrator"
QA_AUTHORITY="$BUILD_A/authority"
QA_UNIT="$BUILD_A/units"
NO_GO_COMPARISON_EVIDENCE="$EVIDENCE/no-go-comparison-$COMMIT.json"
CANDIDATE_ROOT="$BASE/job-plane-$COMMIT"

test ! -e "$CANDIDATE_ROOT"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" \
  --left-app "$BUILD_A/app" --left-backend "$BUILD_A/backend" \
  --left-migrator "$BUILD_A/migrator" --left-unit "$BUILD_A/units" \
  --left-authority "$BUILD_A/authority" \
  --right-app "$BUILD_B/app" --right-backend "$BUILD_B/backend" \
  --right-migrator "$BUILD_B/migrator" --right-unit "$BUILD_B/units" \
  --right-authority "$BUILD_B/authority" --expected-commit "$COMMIT" \
  --evidence-output "$NO_GO_COMPARISON_EVIDENCE"
test "$(stat -c '%a' "$NO_GO_COMPARISON_EVIDENCE")" = 600
EXPECTED_AGGREGATE_SHA="$("$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" verify-evidence \
  --record "$NO_GO_COMPARISON_EVIDENCE" \
  --print-field aggregate_manifest_sha256)"

SEALED_VERIFIER="$QA_MIGRATOR/ops/release-v2/verify-stage.py"
test "$(sha256sum "$SEALED_VERIFIER" | cut -d' ' -f1)" = "$EXPECTED_VERIFIER_SHA"
"$QA_MIGRATOR/runtime/bin/python3.11" -I "$SEALED_VERIFIER" \
  --authority-root "$QA_AUTHORITY" --app-root "$QA_APP" \
  --backend-root "$QA_BACKEND" --migrator-root "$QA_MIGRATOR" \
  --unit-root "$QA_UNIT" --expected-commit "$COMMIT" \
  --expected-promotion-state NO_GO \
  --expected-aggregate-sha256 "$EXPECTED_AGGREGATE_SHA"
```

- [ ] **Step 4: Run every tamper case and perform an actual third clean rebuild**

Application source, backend semantic input, migrator migration/role SQL, interpreter, installed package, copied CAS wheel, argv, sealed verifier, rendered unit/bundle, unexpected executable, missing manifest, and promotion-state mutations must each fail against the original independent expected digests. The tamper tool copies both candidate roots and selected input CAS objects before mutation. Afterward independently bootstrap `DRIVER_C`, export the exact source again, build `BUILD_C` from the pristine input root with network disabled, and compare C to the published A. Reverification of untouched A alone does not satisfy this gate.

```bash
TAMPER_ROOT="$(mktemp -d "$BASE/.tamper-$COMMIT.XXXXXX")"
TAMPER_EVIDENCE="$EVIDENCE/tamper-$COMMIT.json"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/tamper-candidate.py" \
  --source-app "$QA_APP" --source-backend "$QA_BACKEND" \
  --source-migrator "$QA_MIGRATOR" --source-unit "$QA_UNIT" \
  --source-authority "$QA_AUTHORITY" --source-input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --expected-aggregate-sha256 "$EXPECTED_AGGREGATE_SHA" \
  --scratch-root "$TAMPER_ROOT" --case all \
  --evidence-output "$TAMPER_EVIDENCE"

EXPORT_C="$(mktemp -d "$BASE/.source-c-$COMMIT.XXXXXX")"
SCRATCH_C="$(mktemp -d "$BASE/.build-c-$COMMIT.XXXXXX")"
DRIVER_C="$SCRATCH_C/driver"
BUILD_C="$SCRATCH_C/candidate"
git archive --format=tar -o "$EXPORT_C/source.tar" "$COMMIT"
"$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_MATERIALIZER" \
  --archive "$EXPORT_C/source.tar" --output "$EXPORT_C/source" \
  --authority-record "$FINAL_SOURCE_AUTHORITY_RECORD"
"$PINNED_INPUT_PYTHON" -I "$EXPORT_C/source/ops/release-v2/bootstrap-build-driver.py" \
  --input-root "$INPUTS" --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --output "$DRIVER_C"
"$DRIVER_C/runtime/bin/python3.11" -I \
  "$EXPORT_C/source/ops/release-v2/run-offline-build.py" \
  --driver-root "$DRIVER_C" --input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --builder "$EXPORT_C/source/ops/release-v2/build-candidate.py" -- \
  --source-archive "$EXPORT_C/source.tar" --source-commit "$COMMIT" \
  --source-tree "$TREE" --input-root "$INPUTS" --build-driver "$DRIVER_C" \
  --output-root "$BUILD_C" \
  --logical-prefix "$FINAL_PREFIX"
"$DRIVER_C/runtime/bin/python3.11" -I \
  "$EXPORT_C/source/ops/release-v2/compare-builds.py" \
  --left-app "$QA_APP" --left-backend "$QA_BACKEND" \
  --left-migrator "$QA_MIGRATOR" --left-unit "$QA_UNIT" \
  --left-authority "$QA_AUTHORITY" \
  --right-app "$BUILD_C/app" --right-backend "$BUILD_C/backend" \
  --right-migrator "$BUILD_C/migrator" --right-unit "$BUILD_C/units" \
  --right-authority "$BUILD_C/authority" --expected-commit "$COMMIT"
```

- [ ] **Step 5: Verify exact staging and final-intent systemd paths with zero warnings**

Render a separate non-authoritative staging bundle against the exact not-yet-published `CANDIDATE_ROOT` paths, while validating bytes from the quarantine A roots. Materialize those future absolute paths beneath an alternate verification root, then verify. Separately materialize the authoritative final-intent `/opt` bundle. Both invocations use `--recursive-errors=yes`, name exactly the API and worker units, capture stdout/stderr outside the materialized roots, require empty stderr and no warning/error output, and assert that no scheduler/timer unit exists. Do not publish, install, daemon-reload, start, or enable anything.

```bash
STAGING_UNIT_SCRATCH="$(mktemp -d "$BASE/.staging-units-$COMMIT.XXXXXX")"
STAGING_UNIT_ROOT="$STAGING_UNIT_SCRATCH/units"
STAGING_MATERIALIZATION="$STAGING_UNIT_SCRATCH/materialization.json"
STAGING_ENVELOPE="$EVIDENCE/staging-systemd-envelope-$COMMIT.json"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" render-units \
  --authority-path "$CANDIDATE_ROOT/authority" \
  --app-source "$QA_APP" --app-path "$CANDIDATE_ROOT/app" \
  --backend-source "$QA_BACKEND" --backend-path "$CANDIDATE_ROOT/backend" \
  --unit-output "$STAGING_UNIT_ROOT" --source-commit "$COMMIT" \
  --path-mode staging
test "$(find "$STAGING_UNIT_ROOT" -maxdepth 1 -type f -printf '%f\n' | sort | paste -sd, -)" = \
  "systemd-bundle-manifest.json,trading-job-api.service,trading-job-worker.service"
STAGING_INTENT_ROOT="$STAGING_UNIT_SCRATCH/root"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  materialize-path-intent --verification-root "$STAGING_INTENT_ROOT" \
  --logical-prefix "$CANDIDATE_ROOT" \
  --app-root "$QA_APP" --backend-root "$QA_BACKEND" \
  --migrator-root "$QA_MIGRATOR" --authority-root "$QA_AUTHORITY" \
  --authoritative-unit-root "$QA_UNIT" \
  --verification-unit-root "$STAGING_UNIT_ROOT" \
  --source-commit "$COMMIT" \
  --materialization-output "$STAGING_MATERIALIZATION"
systemd-analyze --user --root="$STAGING_INTENT_ROOT" --recursive-errors=yes verify \
  /etc/systemd/user/trading-job-api.service \
  /etc/systemd/user/trading-job-worker.service \
  >"$STAGING_UNIT_SCRATCH/systemd.stdout" \
  2>"$STAGING_UNIT_SCRATCH/systemd.stderr"
test ! -s "$STAGING_UNIT_SCRATCH/systemd.stderr"
if rg -i '\b(warn|error|failed)\b' "$STAGING_UNIT_SCRATCH/systemd.stdout"; then exit 1; fi
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  seal-systemd-envelope --materialization-record "$STAGING_MATERIALIZATION" \
  --stdout "$STAGING_UNIT_SCRATCH/systemd.stdout" \
  --stderr "$STAGING_UNIT_SCRATCH/systemd.stderr" \
  --output "$STAGING_ENVELOPE"

FINAL_INTENT_SCRATCH="$(mktemp -d "$BASE/.final-intent-$COMMIT.XXXXXX")"
FINAL_INTENT_ROOT="$FINAL_INTENT_SCRATCH/root"
FINAL_INTENT_MATERIALIZATION="$FINAL_INTENT_SCRATCH/materialization.json"
FINAL_INTENT_ENVELOPE="$EVIDENCE/final-intent-systemd-envelope-$COMMIT.json"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  materialize-final-intent --verification-root "$FINAL_INTENT_ROOT" \
  --app-root "$QA_APP" --backend-root "$QA_BACKEND" \
  --migrator-root "$QA_MIGRATOR" --authority-root "$QA_AUTHORITY" \
  --unit-root "$QA_UNIT" --source-commit "$COMMIT" \
  --materialization-output "$FINAL_INTENT_MATERIALIZATION"
systemd-analyze --user --root="$FINAL_INTENT_ROOT" --recursive-errors=yes verify \
  /etc/systemd/user/trading-job-api.service \
  /etc/systemd/user/trading-job-worker.service \
  >"$FINAL_INTENT_SCRATCH/systemd.stdout" \
  2>"$FINAL_INTENT_SCRATCH/systemd.stderr"
test ! -s "$FINAL_INTENT_SCRATCH/systemd.stderr"
if rg -i '\b(warn|error|failed)\b' "$FINAL_INTENT_SCRATCH/systemd.stdout"; then exit 1; fi
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  seal-systemd-envelope --materialization-record "$FINAL_INTENT_MATERIALIZATION" \
  --stdout "$FINAL_INTENT_SCRATCH/systemd.stdout" \
  --stderr "$FINAL_INTENT_SCRATCH/systemd.stderr" \
  --output "$FINAL_INTENT_ENVELOPE"
if find "$STAGING_UNIT_ROOT" "$FINAL_INTENT_ROOT/etc/systemd/user" \
  -type f | rg -q 'scheduler|timer'; then exit 1; fi
systemctl --user show \
  trading-job-api.service trading-job-worker.service \
  trading-job-scheduler.service trading-job-scheduler.timer \
  -p ActiveState -p UnitFileState -p MainPID -p NRestarts
if ss -ltnp | rg -q ':(55432|8401)\b'; then exit 1; fi
```

- [ ] **Step 6: Review qualification evidence, copy-on-promote twice, and publish once**

Seal hashes for the source authority, NO_GO A/B and A/C comparisons, tamper record, input closure, staging-intent and final-intent systemd envelopes, exact output path, and runtime-invariant observation into a mode-0600 promotion record. A distinct human reviewer must approve it after all prior steps finish. Then use one `promote-pair` invocation to create two private `CANDIDATE_VERIFIED` copies; compare and independently verify them. Only after that final comparison passes may one complete parent be atomically renamed to `CANDIDATE_ROOT`. The NO_GO quarantines and second promoted copy remain evidence until review; no component is published piecemeal.

```bash
PROMOTE_SCRATCH_A="$(mktemp -d "$BASE/.promote-a-$COMMIT.XXXXXX")"
PROMOTE_SCRATCH_B="$(mktemp -d "$BASE/.promote-b-$COMMIT.XXXXXX")"
PROMOTED_A="$PROMOTE_SCRATCH_A/candidate"
PROMOTED_B="$PROMOTE_SCRATCH_B/candidate"
FINAL_COMPARISON_EVIDENCE="$EVIDENCE/final-comparison-$COMMIT.json"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/promote-candidate.py" promote-pair \
  --left-app "$QA_APP" --left-backend "$QA_BACKEND" \
  --left-migrator "$QA_MIGRATOR" --left-unit "$QA_UNIT" \
  --left-authority "$QA_AUTHORITY" \
  --right-app "$BUILD_B/app" --right-backend "$BUILD_B/backend" \
  --right-migrator "$BUILD_B/migrator" --right-unit "$BUILD_B/units" \
  --right-authority "$BUILD_B/authority" \
  --promotion-record "$PROMOTION_AUTHORITY_RECORD" \
  --left-output "$PROMOTED_A" --right-output "$PROMOTED_B"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" \
  --left-app "$PROMOTED_A/app" --left-backend "$PROMOTED_A/backend" \
  --left-migrator "$PROMOTED_A/migrator" --left-unit "$PROMOTED_A/units" \
  --left-authority "$PROMOTED_A/authority" \
  --right-app "$PROMOTED_B/app" --right-backend "$PROMOTED_B/backend" \
  --right-migrator "$PROMOTED_B/migrator" --right-unit "$PROMOTED_B/units" \
  --right-authority "$PROMOTED_B/authority" --expected-commit "$COMMIT" \
  --expected-promotion-state CANDIDATE_VERIFIED \
  --evidence-output "$FINAL_COMPARISON_EVIDENCE"
EXPECTED_FINAL_AGGREGATE_SHA="$("$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" verify-evidence \
  --record "$FINAL_COMPARISON_EVIDENCE" \
  --print-field aggregate_manifest_sha256)"

FINAL_SEALED_VERIFIER="$PROMOTED_A/migrator/ops/release-v2/verify-stage.py"
test "$(sha256sum "$FINAL_SEALED_VERIFIER" | cut -d' ' -f1)" = \
  "$EXPECTED_VERIFIER_SHA"
"$PROMOTED_A/migrator/runtime/bin/python3.11" -I "$FINAL_SEALED_VERIFIER" \
  --authority-root "$PROMOTED_A/authority" --app-root "$PROMOTED_A/app" \
  --backend-root "$PROMOTED_A/backend" --migrator-root "$PROMOTED_A/migrator" \
  --unit-root "$PROMOTED_A/units" --expected-commit "$COMMIT" \
  --expected-promotion-state CANDIDATE_VERIFIED \
  --expected-aggregate-sha256 "$EXPECTED_FINAL_AGGREGATE_SHA"
test ! -e "$CANDIDATE_ROOT"
mv "$PROMOTED_A" "$CANDIDATE_ROOT"
```

- [ ] **Step 7: Reverify the published staging root and stop boundary**

Run the same independently pinned verifier after the atomic parent rename and require no physical-path serialization or byte drift. Record final candidate path, all non-null digests, source/backend identities, interpreter/version/hash, lock/wheel closure, A/B/A-C/final-promoted comparisons, every tamper result, zero-warning systemd results, promotion record digest, and runtime invariant checks. Verify Git remains at the source-authority commit; never substitute current `HEAD` into evidence.

```bash
PUBLISHED_VERIFIER="$CANDIDATE_ROOT/migrator/ops/release-v2/verify-stage.py"
test "$(sha256sum "$PUBLISHED_VERIFIER" | cut -d' ' -f1)" = "$EXPECTED_VERIFIER_SHA"
"$CANDIDATE_ROOT/migrator/runtime/bin/python3.11" -I "$PUBLISHED_VERIFIER" \
  --authority-root "$CANDIDATE_ROOT/authority" \
  --app-root "$CANDIDATE_ROOT/app" --backend-root "$CANDIDATE_ROOT/backend" \
  --migrator-root "$CANDIDATE_ROOT/migrator" --unit-root "$CANDIDATE_ROOT/units" \
  --expected-commit "$COMMIT" --expected-promotion-state CANDIDATE_VERIFIED \
  --expected-aggregate-sha256 "$EXPECTED_FINAL_AGGREGATE_SHA"
systemctl --user show \
  trading-job-api.service trading-job-worker.service \
  trading-job-scheduler.service trading-job-scheduler.timer \
  -p ActiveState -p UnitFileState -p MainPID -p NRestarts
if ss -ltnp | rg -q ':(55432|8401)\b'; then
  exit 1
fi
test "$(git rev-parse HEAD)" = "$COMMIT"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

Record `paper/paper`, false/false, kill switch INACTIVE, orders/trades 30/0, and zero Job Plane rows only as the last accepted historical evidence with its original timestamp. Do not reopen SQLite or claim a fresh runtime database observation.

**Exit gate:** one atomic staging parent exists only after two private promoted copies agree and verify from the independently pinned migrator-contained verifier plus external aggregate digest; three NO_GO clean builds agree around the tamper suite; systemd verification is warning-free; Git is unchanged; PostgreSQL remains offline; Job services remain inactive; 8401 remains closed; no job-insert path was invoked. Absolute zero-row status remains historical evidence until an approved runtime read is possible.

---

## Hard preparation-session stop boundary

An implementation session for this preparation plan must stop after Task 15. It may report only that no job-insert path was invoked and cite the last accepted zero-row evidence with its timestamp; while runtime PostgreSQL is offline it must not claim a fresh absolute row count. Tasks 16-19 below are follow-up plans, not continuation authority. Each requires a new explicit user invocation and its own reviewed approval. No approval obtained for Tasks 1-15 silently authorizes `/opt` provisioning, recovery, runtime backup/migration, or service rollout.

---

### Task 16: Provision a root-owned maintenance kit under separate authority

**Files:**
- Read only: Task 15 `CANDIDATE_ROOT/migrator`, `authority`, and protected external aggregate/source evidence
- Read only: Task 14 protected mode-0500 copy of `provision-root.sh`
- External root-owned output only: `/opt/trading-agent-v2/maintenance/${SOURCE_COMMIT}`
- Create after execution: a timestamped maintenance-kit provisioning evidence document

**Interfaces:**
- Consumes: a new exact `MAINTENANCE_KIT_PROVISIONING` approval that independently pins the protected launcher SHA-256, maintenance-subset manifest SHA-256, source/full aggregate, destination, and one operation. Task 15's source/build approval is insufficient.
- Produces: root-owned, non-writable `migrator/` (including `runbooks/`) and `authority/` subroots only. It installs no app/backend, systemd unit, environment file, timer, credential, database file, or mutable output directory.
- Uses the split provisioning protocol and independently pinned verifier from Task 11. The kit's sealed runbooks/launchers are the only bytes Tasks 18-19 may execute.

- [ ] **Step 1: Validate staging and provisioning authority without privileged writes**

Require exact source commit/tree, candidate promotion `CANDIDATE_VERIFIED / NOT_INSTALLED / NOT_RUNNING`, aggregate SHA, maintenance-subset manifest SHA, migrator/runbook/launcher hashes, protected provisioning-launcher hash, absent destination, root filesystem capacity/mount identity, and explicit output allowlist. Reject a candidate owned from a different commit, any writable/extra path, service/unit action, or approval that also names recovery/migration.

- [ ] **Step 2: Provision only the maintenance kit**

Never execute a launcher directly from candidate or user-writable evidence storage. First copy the protected launcher bytes, without executing them, to a previously absent root-owned mode-0500 temporary path. Compare the destination SHA-256 with the independently approval-bound literal using a reviewed operator command; only an exact match may be elevated/executed. The trusted root-owned launcher then invokes `--maintenance-only`, opens every staging path with no-follow/stable descriptors, verifies the full candidate and bound maintenance-subset manifest, copies only those descriptor-pinned bytes into `/opt/trading-agent-v2/maintenance/.incoming-${SOURCE_COMMIT}`, and rejects any source inode/mode/hash change across the copy. It fsyncs, applies root ownership/non-write modes, reopens/re-hashes every destination file, and atomically renames the parent to `${SOURCE_COMMIT}`. On any error, no final path exists. Do not install units, daemon-reload, start a process, open a port, or access PostgreSQL.

- [ ] **Step 3: Verify the immutable maintenance identity and stop**

Run the root-owned pinned interpreter/verifier in `--maintenance-only` mode from the final kit. Require the independently expected full aggregate and maintenance-subset manifest hashes, compare every migrator/runbook/launcher/authority byte, verify no app/backend/unit/credential/runtime files exist, and record mount/owner/mode evidence. Do not call normal full-candidate verification on the intentionally absent roots. If filesystem immutability stronger than root-owned non-write modes is required by policy, make that a separate reviewed provisioning operation; do not silently invoke `chattr` or fs-verity tooling.

**Exit gate:** exact root-owned maintenance kit exists and verifies; PostgreSQL remains offline; no service/unit is installed or started; port 8401 remains closed. This does not authorize recovery.

---

### Task 17: Obtain dual-reviewed runtime recovery authority

**Files:**
- External only: the protected literal-TAB 50-field `APPROVAL_RECORD`
- Update after execution: runtime recovery evidence document; never commit the authoritative record

**Interfaces:**
- Consumes: final clean commit/tree, exact root-owned Task 16 maintenance-kit identity, sealed V2 runbook/launcher hash, frozen 0004 hash, independent expected catalog evidence, current target identity, and two distinct human reviewers.
- Produces: unexpired one-use authority for recovery only.

- [ ] **Step 1: Populate all 67 preparation sentinels through authenticated review**

The operator and distinct reviewer must supply current identities, attestations, hashes, paths, target revalidation, recovery outcomes, approval window, and change-control evidence. No agent signs for a person.

- [ ] **Step 2: Create the external mode-0600 literal-TAB record**

It must contain exactly the runbook's 50 fields in order, no YAML, comments, blanks, duplicate, extra, placeholder, password, or DSN.

- [ ] **Step 3: Validate preparation completeness without claiming authorization**

```bash
python3 scripts/validate_postgres_recovery_approval.py --schema-only \
  "$PREPARATION_RECORD"
python3 scripts/validate_postgres_recovery_approval.py \
  --trusted-evidence-root "$TRUSTED_EVIDENCE_ROOT" \
  "$PREPARATION_RECORD"
```

Operational paths are supplied by the authenticated change process and are not printed in logs. The schema-only command must emit `NON-AUTHORIZING`. The completeness command must still exit nonzero because YAML is permanently preparation-only; its sole terminal rejection may be `YAML_PREPARATION_ONLY`, with no missing, mismatch, expiry, identity, or safety error. The executable literal-TAB record is validated by the reviewed V2 runbook preflight, not by this YAML validator.

**Exit gate:** preparation has no error beyond the deliberate YAML authorization boundary, and authenticated change control creates the exact protected literal-TAB record for one recovery attempt. It does not authorize 0005-0007.

---

### Task 18: Execute runtime recovery in a dedicated approved session

**Files:**
- Execute only: `/opt/trading-agent-v2/maintenance/${SOURCE_COMMIT}/migrator/runbooks/postgresql-preserve-recover-v2.md` and its sealed launcher
- Reference-only source identity: exact Git blob/hash of `docs/production/runbooks/postgresql-preserve-recover-v2.md`; never execute a checkout copy
- Create: a new timestamped recovery evidence document

**Interfaces:**
- Produces: target cluster verified at 0004, a mode-0600 custom dump, an isolated successful restore, and a controlled final stop.

- [ ] **Step 1: Execute the approved runbook exactly**

Preserve cold PGDATA; perform at most the approved original start; verify PostgreSQL 16, exact cluster/listener identity, head 0003/0004, canonical 43,055, quarantine 222, and expected zero row counts in the Job Plane tables. If and only if the record permits, migrate 0003 to frozen 0004.

- [ ] **Step 2: Backup and restore before any later migration**

Create a per-database custom dump mode 0600 and compute SHA-256. Do not use `pg_dumpall --globals-only` or capture password hashes. Provision the isolated cluster's required baseline global roles independently through the reviewed protected-input procedure, restore the database dump, then verify global role identity separately from database head/count/catalog/ACL identity.

- [ ] **Step 3: Stop the target cluster as required by the runbook**

Leave all Job services inactive and port 8401 closed.

**Failure rule:** no automatic retry. Stop if safely possible, preserve PGDATA copies/dumps/logs/evidence, and require a new approval.

---

### Task 19: Create a fresh migration backup, then apply 0005 through 0007 under separate authority

**Files:**
- Execute only: `/opt/trading-agent-v2/maintenance/${SOURCE_COMMIT}/migrator/runbooks/job-plane-transition-authority-rollout.md` and its sealed launcher
- External first invocation: one backup-only record validated by the sealed `validate_job_plane_runtime_migration_backup_approval.py`
- External second invocation: one later human-reviewed migration record validated by the sealed `validate_job_plane_runtime_migration_approval.py`
- Create: a timestamped runtime migration evidence document

**Interfaces:**
- Consumes: a cleanly recovered/stopped exact-0004 cluster, root-owned Task 16 maintenance kit, backup-only authority, then a separately reviewed migration authority that binds the resulting fresh backup/restore proof.
- Produces: runtime database at exact 0007 while every Job service remains off.
- Executes only from the root-owned Task 16 maintenance kit; never from user-writable staging, a mutable checkout, or the API/worker environment.

- [ ] **Step 1: Validate backup-only authority and exact entry state**

Using the backup-only record, perform at most the exact existing-cluster start needed for read-only inspection; then reverify target/system identity, record-bound entry head/catalog, canonical/quarantine/job counts, paper/false/false, inactive Job units, closed 8401, root-owned migrator/runbook/manifest hashes, and backup nonce. An actual state different from the approved entry state stops without mutation. The allowlisted command set contains target start/read/stop and dump/isolated-restore verification only.

- [ ] **Step 2: Create and restore-test a fresh pre-0005 dump**

Create and restore-test a fresh dump of the actual approved entry state before any new role/schema mutation. Do not reuse only the recovery dump as the migration rollback boundary. Separate independently provisioned global-role evidence from database dump/ACL evidence. Seal path/mode/hash, restore evidence, target head/catalog/counts, and maintenance-kit identity; stop the target, then stop the invocation with `BACKUP_RESTORE_EVIDENCE_READY`. Do not provision roles or run Alembic.

- [ ] **Step 3: Obtain and validate a new migration record that binds Step 2**

Two distinct humans review the exact Step 2 evidence and sign a short-lived one-attempt migration record that permits its own single existing-cluster start/verify/migrate/stop sequence. On the new invocation, reverify the backup evidence digest and prove target system/head/catalog/counts are unchanged since backup. Any drift or record that also authorized backup creation stops. This is the first point at which role/schema mutation may become authorized.

- [ ] **Step 4: Provision roles or verify the approved continuation state**

For `PRE_ROLE_PROVISION_0004`, require all three job roles absent, run the exact sealed transactional protected-stdin role script, then seal `POST_ROLE_PROVISION_PRE_MIGRATION` evidence. If any role already exists, stop; do not normalize/reuse it. For a continuation entry, do not rerun provisioning: require the read-only exact role/catalog verifier and the prior evidence digest. Any role drift requires a separate rotation/remediation plan.

- [ ] **Step 5: Apply only the remaining forward migrations**

Using the sealed migrator interpreter and protected owner connection, apply only the missing suffix of 0005, frozen 0006, and 0007. After each committed revision, seal head/catalog/count evidence. At 0007 run the exact catalog verifier, event-chain verifier, cross-role denial matrix, function/trigger/policy checks, and job-table counts. Never use an API, worker, scheduler, shared, or mutable-checkout identity for migration.

- [ ] **Step 6: Stop the database and retain evidence**

Leave API, worker, scheduler, and timer inactive; do not enqueue a job.

**Failure rule:** preserve data and evidence, keep services off, and record the exact achieved entry state. A successful role step followed by migration failure becomes `POST_ROLE_PROVISION_PRE_MIGRATION`; a committed 0005/0006 becomes `POST_0005`/`POST_0006`. Resume only with a new record for that exact continuation state. Prepare forward-only 0008 only for a proven schema/authority defect, not merely because an earlier step already succeeded. Never downgrade or automatically overwrite the original from backup.

---

## Final acceptance gate

Blocker closure is complete only when all statements are true:

- Approval tooling remains schema-exact, non-authorizing, and green.
- Canonical contract generation uses no external sibling checkout.
- Frozen 0006 hash is unchanged.
- Two independent 0006 catalog captures match.
- Two independent 0007 catalog captures match.
- Every catalog drift and malformed event-chain case rejects.
- 0007 survives custom dump/restore with exact role and ACL authority.
- All active source/release/unit pins require 0007.
- The canonical backend subtree has explicit reviewed authority; v2 does not claim legacy commit `41f055b...`.
- Final source commit and tree are clean and fully tested.
- App/backend/migrator releases contain embedded CPython and complete offline wheel closures.
- Two clean builds produce the same logical authority.
- Every tamper case fails verification.
- Staging and final-intent systemd units verify without installation.
- Candidate remains `NO_GO` until all qualification evidence is reviewed; only a copy-on-promote pair may create `CANDIDATE_VERIFIED` before one atomic staging publication.
- Runtime maintenance uses only the separately approved root-owned Task 16 kit and sealed runbooks, never user-writable staging.
- Runtime recovery completes under its own valid approval.
- A backup-only migration gate stops for review before any role/schema mutation; a later separate approval reaches exact 0007.
- Job API, worker, scheduler, and timer remain inactive; port 8401 remains closed; no job-insert path is invoked. Any absolute row count is timestamped and labeled historical until runtime verification is separately approved.

## Explicitly deferred

- Installing app/backend/systemd units to `/opt`; Task 16's maintenance-only kit is separately scoped and installs no service payload.
- Starting Job API or worker.
- Enabling or running scheduler/timer.
- Enqueueing the first SNAPSHOT.
- Provider-backed research execution.
- Dashboard deployment or Cloudflare changes.
- Any live-limited or live-trading work.
