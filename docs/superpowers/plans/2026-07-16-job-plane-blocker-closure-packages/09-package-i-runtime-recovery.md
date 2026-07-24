# Package I — Runtime PostgreSQL Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone. Execute only the exact sealed recovery runbook and stop afterward.

**Goal:** Safely recover the existing PostgreSQL cluster, verify the exact approved baseline, create/restore-test a backup, and stop without Job Plane rollout.

**Architecture:** Execute from the root-owned maintenance kit under one-use recovery authority, preserve cold data first, permit at most the approved existing-cluster start, and fail closed on baseline drift.

**Tech Stack:** PostgreSQL 16, pg_ctl/pg_dump/pg_restore through sealed launcher, Alembic baseline verification, protected evidence.

## Global Constraints

- This file is non-authorizing without the exact unexpired Package H `RUNTIME_RECOVERY` record.
- Follow only the sealed root-owned runbook/launcher bytes; never execute a checkout or staging copy.
- No `pg_resetwal`, reinitialization, data-file deletion, unsafe PID deletion, or automatic retry.
- Do not apply 0005, 0006, or 0007 under recovery authority.
- Do not start Job API, worker, scheduler, timer, enqueue, or SNAPSHOT.
- Keep `paper/paper`, live gates false/false, kill switch unchanged, and port 8401 closed.
- Do not print credentials, passwords, DSNs, environment values, or password verifiers.
- Stop on revision/count/identity/listener drift, unsafe recovery requirement, backup/restore failure, or approval mismatch.

## Package Boundary

- Start only when the sealed Package G kit and exact one-use Package H record both verify.
- Stop after Task 18. Package J needs new, non-interchangeable migration authorities.

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
