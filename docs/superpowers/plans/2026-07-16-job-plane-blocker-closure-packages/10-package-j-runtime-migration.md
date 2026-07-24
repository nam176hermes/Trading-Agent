# Package J — Runtime Backup and Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone but contains two invocations separated by a mandatory human-review stop.

**Goal:** Create a fresh migration rollback boundary, stop for human review, then apply only the approved forward suffix through exact 0007 while Job services remain off.

**Architecture:** `RUNTIME_MIGRATION_BACKUP` permits one read/backup/restore/stop sequence only. A later `RUNTIME_MIGRATION_0005_0007` record binds that evidence and permits one exact start/verify/forward-migrate/stop sequence.

**Tech Stack:** Root-owned migrator kit, PostgreSQL 16, pg_dump/pg_restore, Alembic 0005–0007, exact catalog/event verifier.

## Global Constraints

- This file is non-authorizing without two separate, exact, human-reviewed records.
- Invocation 1 must stop at `BACKUP_RESTORE_EVIDENCE_READY`; it cannot provision roles or run Alembic.
- Invocation 2 cannot begin until two humans review Invocation 1 and sign the migration record.
- Never reuse the recovery record, shared DB role, user-writable staging, API/worker identity, or mutable checkout.
- Do not downgrade, overwrite from backup automatically, delete evidence, or rewrite applied migrations.
- Do not start Job API, worker, scheduler, timer, enqueue, or SNAPSHOT.
- Keep `paper/paper`, live gates false/false, kill switch unchanged, and port 8401 closed.
- Do not print secrets, DSNs, role-setting values, or password verifiers.
- Stop on entry-state drift, backup/restore mismatch, role reuse, ACL/catalog/event-chain failure, duplicate attempt, or service activity.

## Package Authority and Exit Gate

- **Entry:** Package I controlled exact baseline and Package G root-owned maintenance kit.
- **Produces:** fresh reviewed backup evidence, then runtime head exact 0007 with role/ACL/event authority verified.
- **Exit:** target is stopped as required; API/worker/scheduler/timer remain inactive; no job row or research command was created.
- **Deferred:** Job API/worker rollout, timer, first SNAPSHOT, providers, dashboard deployment, and all live trading.

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
