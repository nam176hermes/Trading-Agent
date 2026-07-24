# Package C — Backend Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone. Execute only Package C.

**Goal:** Establish reviewed canonical backend provenance and exact source policies for app, backend, and maintenance-only migrator components.

**Architecture:** Review all 79 backend paths semantically, bind the canonical subtree/per-file blobs, and prevent broad package discovery from expanding any release component.

**Tech Stack:** Git object identity, Python 3.11, pytest, JSON source policies.

## Global Constraints

- This file is non-authorizing until the user explicitly approves execution of Package C.
- Keep `paper/paper`, false/false live gates, and kill-switch semantics unchanged.
- Do not start PostgreSQL, services, timers, listeners, jobs, SNAPSHOT, or external calls.
- Historical backend commit `41f055b...` is evidence only and cannot authorize v2.
- Review every changed backend path; do not approve provenance by count or documentation claim.
- App, backend, and migrator source allowlists must be disjoint except for explicitly bound shared contracts.
- Backend command authority remains SNAPSHOT-only; no DEBATE/REPLAY/BACKTEST runtime work.
- Do not include secrets, runtime data, reports, logs, databases, or mutable paths.
- Stop on any unreviewed path, subtree drift, unresolved import outside policy, or command expansion.

## Package Authority and Exit Gate

- **Entry:** exact clean Package B source with active head pins at 0007.
- **Produces:** reviewed backend subtree OID, per-file policy digest, and exact component source policies.
- **Exit:** all 79 paths are classified/reviewed and later commits may proceed only if the backend subtree remains identical.
- **Next:** Package D requires explicit Release Authority v2 implementation and build-input approvals.

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
