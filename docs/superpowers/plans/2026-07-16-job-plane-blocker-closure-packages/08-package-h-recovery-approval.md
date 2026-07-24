# Package H — Dual-Reviewed Recovery Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone and prepares authority only. It must not recover PostgreSQL.

**Goal:** Produce one exact, protected, dual-reviewed 50-field authority record for a single recovery attempt.

**Architecture:** Complete non-authorizing preparation evidence first, require two distinct humans, then let authenticated change control create the literal-TAB record consumed by the sealed V2 runbook.

**Tech Stack:** Protected evidence storage, Python validator, canonical literal-TAB record, human change control.

## Global Constraints

- This file does not authorize PostgreSQL startup or any runtime action by itself.
- Do not start PostgreSQL, access PGDATA, create a backup, migrate, or touch services/timers.
- Do not self-sign, fabricate, infer, or auto-fill human identities, decisions, timestamps, or signatures.
- The executable record has exactly 50 ordered fields; no YAML, comments, blanks, duplicates, extras, placeholders, password, or DSN.
- Requested/effective mode must remain `paper/paper`; live gates false/false; kill switch unchanged.
- Recovery scope excludes 0005–0007, Job services, enqueue, SNAPSHOT, and external calls.
- Stop on any missing sentinel, identity collision, expiry, runbook/PGDATA/maintenance hash mismatch, or safety-baseline mismatch.

## Package Boundary

- Start only after Package G is independently verified and current incident evidence is available.
- Stop after Task 17. Package I requires a separate explicit execution instruction from authenticated change control.

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
