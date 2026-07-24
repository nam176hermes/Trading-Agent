# Package G — Root-Owned Maintenance Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone. It authorizes no PostgreSQL or service action.

**Goal:** Provision only a root-owned sealed migrator/authority maintenance kit for later recovery and migration sessions.

**Architecture:** Independently pin a protected launcher before elevation, descriptor-pin the staging source, verify the maintenance subset, and atomically install one non-writable maintenance parent without service payloads.

**Tech Stack:** Root-owned filesystem provisioning, SHA-256 manifests, pinned CPython verifier, immutable release tooling.

## Global Constraints

- This file is non-authorizing without an exact `MAINTENANCE_KIT_PROVISIONING` approval.
- The approval permits only the exact maintenance destination and one launcher hash/operation.
- Do not start or access PostgreSQL, PGDATA, a database listener, or any service/timer.
- Do not install app, backend, units, environment files, credentials, databases, logs, or mutable output roots.
- Never execute the launcher from a user-writable path; copy, root-own, hash-check, then execute.
- Keep `paper/paper`, false/false live gates, and kill-switch semantics unchanged.
- Do not call providers, brokers, exchanges, orders, jobs, or SNAPSHOT.
- Stop on destination existence, source TOCTOU, owner/mode/hash mismatch, subset-verifier mismatch, or approval scope overlap.

## Package Boundary

- Start only under a new, exact `MAINTENANCE_KIT_PROVISIONING` approval bound to Package F evidence.
- Stop after Task 16. Package H is a separate human-approval session.

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
