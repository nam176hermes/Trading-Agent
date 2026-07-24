# Package E — Runtime Authority Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone. Execute only Package E; it creates non-authorizing contracts and runbooks.

**Goal:** Bind PostgreSQL recovery to a sealed maintenance source and define separate backup-only and migration authorities without touching runtime.

**Architecture:** Preserve the historical V1 runbook, create a stricter V2 rooted in the future maintenance kit, and split runtime migration into backup/stop/review and later mutation invocations.

**Tech Stack:** Python 3.11, JSON Schema, pytest, PostgreSQL runbook design, immutable manifest verification.

## Global Constraints

- This file is non-authorizing until the user explicitly approves source implementation of Package E.
- Do not start PostgreSQL—including disposable clusters—or access PGDATA.
- Do not apply any migration, create a backup, install `/opt` content, or start/modify a service/timer.
- Keep `paper/paper`, false/false live gates, and kill-switch semantics unchanged.
- Preserve V1 runbook bytes exactly; V2 may only be equally strict or stricter.
- Recovery authority never authorizes 0005–0007.
- Backup-only authority never authorizes role/schema mutation.
- Migration authority cannot be signed until humans review a completed fresh backup/restore proof.
- Do not fabricate reviewer identity, signature, approval, or runtime evidence.
- Stop on schema ambiguity, mutable-source dependency, authority overlap, or any executable example that becomes authorizing.

## Package Authority and Exit Gate

- **Entry:** Package D source/verifier protocols and exact Package B 0007 authority.
- **Produces:** V2 recovery runbook/validator plus two non-interchangeable migration authority schemas, validators, and sealed runbook flow.
- **Exit:** examples remain `DRAFT_NOT_AUTHORIZED`; validators are green; no runtime record or mutation exists.
- **Next:** Package F requires final source/release and disposable verification approvals.

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
