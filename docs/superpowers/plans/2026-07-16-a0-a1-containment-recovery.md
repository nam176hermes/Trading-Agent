# A0/A1 Containment, Provenance, and PostgreSQL Recovery Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the source-level SAF-001, UI-001, and REL-001 containment gaps and publish a reviewed DATA-001 PostgreSQL recovery runbook without changing runtime or the database.

**Architecture:** Keep live authority absent and use explicit fail-closed state at every boundary. Promotion status is a typed three-value decision that defaults to `NO_GO`; deployment evidence is an observational source-to-process schema, not Release Authority v2; dashboard operational truth is one shared unknown-first model. PostgreSQL work in this branch is documentation only.

**Tech Stack:** Python 3.11, Pydantic 2, pytest, JSON Schema 2020-12, Next.js 16.2, React 19, TypeScript 5, Node test runner, systemd templates, PostgreSQL 16 documentation.

## Global Constraints

- Requested/effective mode remains `paper/paper`; `LIVE_EXECUTION_ENABLED=false`; `LIVE_TRADING_APPROVED=false`.
- Never start, stop, restart, enable, or disable a service or timer in this plan.
- Never connect to, recover, migrate, back up, restore, or otherwise write PostgreSQL in this plan.
- Never call a broker, exchange, provider, credential endpoint, or print secret values/DSNs.
- Do not modify Release Authority v2, point a unit at this checkout, build a release, or deploy the dashboard.
- Use RED-GREEN-REFACTOR for behavior changes and preserve the three existing lockfiles.
- Rollback means revert only this branch's source files; runtime and database remain untouched.

---

### Task 1: Exhaustive fail-closed deployment defaults

**Files:**
- Modify: `tests/consolidation/test_audit_canonical_repo.py`
- Modify: `tests/jobs/test_systemd_units.py`
- Modify: `tests/runtime_release/test_provision_script.py`
- Modify: `ops/phase4b/verify-installed.sh`

**Interfaces:**
- Consumes: Git index, tracked systemd services and env examples, fakeroot provisioning output.
- Produces: regression proof that no tracked `.mode` exists and every generated/tracked deployment environment is exactly `paper/false/false`.

- [ ] Add failing cases for root and nested tracked `.mode`, all six services, all three env examples, all four generated env files, and all four installed-verifier checks.
- [ ] Run the focused tests and confirm they fail only on the uncovered cases.
- [ ] Extend the read-only installed verifier to require the safe triplet in every service env file; do not change provisioning or runtime.
- [ ] Re-run focused tests and source audit until green.

### Task 2: Typed promotion and observational deployment evidence

**Files:**
- Create: `packages/deployment_evidence.py`
- Create: `ops/evidence/source-release-unit-pid.schema.json`
- Create: `tests/control_api/test_deployment_evidence.py`
- Modify: `scripts/capture_production_baseline.py`
- Modify: `tests/production/test_capture_production_baseline.py`
- Modify: `docs/production/promotion-status.json`
- Modify: `docs/production/production-readiness-baseline.md`

**Interfaces:**
- Consumes: source commit/tree, immutable release manifest identity, effective unit identity, reuse-safe PID identity.
- Produces: `PromotionDecision = NO_GO | GO_PAPER_PRODUCTION | GO_LIVE_LIMITED` and a strict `DeploymentEvidence` chain with explicit `VERIFIED | DRIFTED | UNAVAILABLE` states.

- [ ] Write failing tests for exact keys, enum values, safe paths/hashes, duplicate service IDs, source-to-release-to-unit-to-process link states, PID/start-ticks/command fingerprint, canonical JSON, and secret-like key rejection.
- [ ] Prove absent, malformed, drifted, or unavailable evidence resolves to `NO_GO`; baseline capture cannot emit a GO decision.
- [ ] Implement frozen Pydantic models and canonical serialization only; add no collector, publisher, systemctl/procfs reader, cutover, or authorization behavior.
- [ ] Generate/check the JSON Schema and update the current record to schema v2, historical source binding, explicit `NO_GO`, and an unavailable deployment-evidence reference without claiming deployment.

### Task 3: Dashboard unknown-first global truth

**Files:**
- Create: `apps/dashboard/src/lib/trading/operator-state.ts`
- Create: `apps/dashboard/src/components/trading/operator-state-provider.tsx`
- Create: `apps/dashboard/src/components/trading/operator-state-banner.tsx`
- Create: `apps/dashboard/tests/operator-state.test.mjs`
- Modify: `apps/dashboard/src/app/dashboard/layout.tsx`
- Modify: `apps/dashboard/src/components/trading/trading-sidebar.tsx`
- Modify: `apps/dashboard/src/components/trading/mode-toggle.tsx`
- Modify: `apps/dashboard/src/components/trading/system-status-banner.tsx`
- Modify: `apps/dashboard/src/components/trading/quick-actions.tsx`
- Modify: `apps/dashboard/src/app/dashboard/execution/page.tsx`
- Modify: `apps/dashboard/src/app/dashboard/page.tsx`

**Interfaces:**
- Consumes: strict `/api/trading/meta` response.
- Produces: `OperatorState` with availability `LOADING | AVAILABLE | UNAVAILABLE`, mode and kill state including `UNKNOWN`, nullable metrics, and `controlsEnabled=false` unless canonical truth is authoritative.

- [ ] Write table-driven RED tests for null, 401, 503, timeout, invalid JSON, missing/extra fields, and valid authoritative zero.
- [ ] Implement the pure parser/projector and shared provider/banner; unknown is the initial state and failures are unavailable.
- [ ] Wire safety-critical global UI to shared state, disable controls when unavailable, and remove PAPER/READY/ACTIVE/zero fallbacks.
- [ ] Prove source wiring and isolated dashboard tests; do not build or deploy production bytes.

### Task 4: Reviewed PostgreSQL preserve/recover runbook

**Files:**
- Create: `docs/production/runbooks/postgresql-preserve-recover.md`

**Interfaces:**
- Consumes: documented PostgreSQL 16 cluster identity, verified 0003 dump metadata, expected 0004 schema/count/ACL invariants.
- Produces: an operator-approved execution checklist with preservation, stop conditions, recovery, verification, backup, isolated restore, and rollback.

- [ ] Document the exact approval record required before any start/write and the invariant that all application services/timers stay stopped.
- [ ] Record the verified 0003 dump path, size, SHA-256, restore evidence, expected 0004 head, 26-table/count/constraint/index/trigger/ACL gates, and known ACL leakage risk.
- [ ] Document offline physical preservation before start, controlled one-shot recovery, explicit 0003/0004 branching, immediate new backup, isolated restore drill, and non-destructive rollback.
- [ ] Add stop conditions prohibiting stale-PID deletion, `pg_resetwal`, reinitialization, restoring over the original, downgrade, secret/DSN output, retries, or improvised repair.

### Task 5: Verification and handoff

**Files:**
- No new behavior files.

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: exact verification log, changed-file list, Git status, residual risks, and one recovery-review decision.

- [ ] Run focused tests first, then the user-requested canonical audit, contract, core, dashboard test/type/lint, diff check, and status commands.
- [ ] Do not run any test that reaches the production DB/runtime/provider; record every skipped check and why.
- [ ] Review the diff against SAF-001, UI-001, DATA-001, REL-001 and A0/A1; confirm no runtime, DB, lockfile, or secret change.
- [ ] End with exactly one decision: `GO FOR REVIEWED DB RECOVERY` or `NO-GO — CONTAINMENT/PLAN INCOMPLETE`.
