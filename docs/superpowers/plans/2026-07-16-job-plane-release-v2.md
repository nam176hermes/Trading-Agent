# Job Plane Containment and Release Authority v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close JOB-002, JOB-003, DATA-002, and the source half of SEC-001; produce a sealed Release Authority v2 candidate without changing PostgreSQL or runtime.

**Architecture:** Preserve the reviewed A1 recovery boundary at revision `0004_durable_research_jobs`, then introduce a separate admin role-provisioning step and Alembic `0005_job_plane_role_split`. Job mutations use a pinned opaque authority capability, idempotency is a complete request identity with a database-enforced scheduler namespace, and the worker activation policy exposes SNAPSHOT only. Release Authority v2 is a static one-commit stage plus a separate create-only activation record for exact fresh safety and semantic evidence; this session builds only the static candidate.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, psycopg 3, PostgreSQL 16/Alembic, pytest, Next.js 16/TypeScript, Node test runner, systemd, canonical JSON and SHA-256 complete-set manifests.

## Global Constraints

- Requested/effective mode remains `paper/paper`; `LIVE_EXECUTION_ENABLED=false`; `LIVE_TRADING_APPROVED=false`.
- The child process is research-only, receives a newly constructed allowlisted environment, uses exact list argv and `shell=False`, and never receives DB, broker, exchange, Job API, or live credentials.
- Runtime activation supports SNAPSHOT only; DEBATE, REPLAY, and BACKTEST stay dormant and cannot be enqueued or claimed by the candidate.
- Do not start, stop, restart, enable, disable, or repoint a service or timer. In particular, keep `trading-job-scheduler.timer` disabled.
- Do not connect to, recover, migrate, back up, restore, or write the runtime PostgreSQL cluster. Disposable PostgreSQL tests may use only the repository's isolated temporary cluster harness.
- Do not call any broker, exchange, provider, account, order, or credential endpoint; do not print secrets, protected environment values, or DSNs.
- Preserve all Prompt 1 changes and old worktrees. Do not reset, clean, or deploy the candidate.
- Use RED-GREEN-REFACTOR for behavior changes. Generated contracts change only through `make generate-contracts` and must pass `make check-contracts`.
- Release v2 must be built from one clean committed canonical Git object. A dirty-worktree build or a build from the pre-change HEAD is a rejection, not a candidate.
- Runtime rollout requires a separately approved B1 runbook and activation record; absence of that approval ends this session without migration, service, or job actions.

---

### Task 1: Pin authority before Job API composition and every mutation

**Files:**
- Create: `packages/runtime_release/job_plane.py`
- Modify: `packages/runtime_release/__init__.py`
- Modify: `apps/job_api/config.py`
- Modify: `apps/job_api/app.py`
- Modify: `apps/job_api/main.py`
- Modify: `tests/jobs/test_job_api_auth.py`
- Modify: `tests/jobs/test_job_api_security.py`

**Interfaces:**
- Produces `ValidatedJobPlaneAuthority` with immutable authority document identity/digest and `recheck_mutation()`.
- `JobApiSettings.authority_factory` returns the opaque capability; no boolean or environment-supplied digest is accepted.
- `run()` obtains the capability before constructing `JobRepository`; `create_app()` receives the pinned capability; enqueue and cancel recheck it immediately before repository access.

- [ ] Add RED tests where authority creation is absent, tampered, or raises; assert repository factory and `uvicorn.run` have zero calls.
- [ ] Add RED tests where startup authority is valid then rotates before enqueue/cancel; assert typed sanitized 503 and zero `enqueue`/`request_cancel` calls.
- [ ] Implement the minimal opaque capability over the protected v2/static authority identity. Keep detailed failures out of responses, logs, repr, and tracebacks.
- [ ] Wire startup order and mutation guards. Reads may remain authenticated/read-only, but readiness and every mutation require the same pin.
- [ ] Run `uv run pytest -q tests/jobs/test_job_api_auth.py tests/jobs/test_job_api_security.py` and keep generated API contract tests green.

### Task 2: Make client operations retry-stable and reserve the scheduler namespace

**Files:**
- Modify: `packages/job_contracts/api.py`
- Modify: `services/job_store/repository.py`
- Modify: `apps/job_api/app.py`
- Modify: `apps/dashboard/src/lib/trading/job-api.ts`
- Modify: `apps/dashboard/src/lib/trading/quick-actions-state.ts`
- Modify: `apps/dashboard/src/components/trading/quick-actions.tsx`
- Modify: `apps/dashboard/src/components/trading/run-pipeline-button.tsx`
- Modify: `apps/dashboard/src/app/api/trading/run/route.ts`
- Modify: `apps/dashboard/tests/trading-job-bff.test.mjs`
- Modify: `apps/dashboard/tests/dashboard-safety-state.test.mjs`
- Modify: `tests/jobs/test_contracts.py`
- Modify: `tests/jobs/test_job_api.py`
- Modify: `tests/jobs/test_repository_enqueue.py`

**Interfaces:**
- Browser command field `operationId` is exactly 32 lowercase hex characters, created once per explicit operator intent and reused unchanged for a retry.
- BFF derives `dashboard:<action>:<operationId>` and never creates a server-side operation UUID.
- Public OPERATOR requests reject any `schedule:` key. Internal scheduler requests accept only `schedule:snapshot:<YYYY-MM-DDTHH:MMZ>`, actor `SCHEDULER`, type `SNAPSHOT`, and priority `0`.
- Dedupe identity is payload fingerprint plus actor type, actor id, and priority; trace id is excluded.

- [ ] Add RED dashboard tests simulating a dropped first response and replaying the same command; assert the two upstream bodies carry the same idempotency key and only one client operation identity.
- [ ] Add RED BFF tests for missing/malformed `operationId`; assert 400 and zero upstream calls. Remove empty-body/server-generated snapshot compatibility.
- [ ] Add RED contract/API tests for OPERATOR `schedule:` requests and internal namespace/actor/type/priority mismatch; assert 422 and zero repository calls.
- [ ] Add RED repository tests proving actor-id, actor-type, and priority mismatch produce 409 conflict while an exact retry deduplicates to one job/event.
- [ ] Implement the smallest validators and explicit conflict comparison; keep `payload_fingerprint` payload-only.
- [ ] Generate contracts and run focused Python/dashboard tests.

### Task 3: Split database authority with a non-reversible forward gate

**Files:**
- Create: `ops/postgres/provision-job-roles.sql`
- Create: `alembic/versions/0005_job_plane_role_split.py`
- Create: `docs/production/runbooks/job-plane-role-split-rollout.md`
- Modify: `ops/postgres/provision-roles.sql`
- Modify: `services/job_store/config.py`
- Modify: `services/job_store/repository.py`
- Modify: `apps/job_api/config.py`
- Modify: `apps/job_api/main.py`
- Modify: `services/job_worker/main.py`
- Modify: `services/job_scheduler/main.py`
- Modify: `tests/jobs/_postgres.py`
- Modify: `tests/jobs/test_job_role_permissions.py`
- Modify: `tests/jobs/test_alembic_jobs_schema.py`
- Modify: `tests/control_api/test_alembic_schema.py`

**Interfaces:**
- Admin script provisions independent `trading_job_api`, `trading_job_worker`, and `trading_job_scheduler` logins and converts `trading_jobs` to `NOLOGIN` without exposing or reusing passwords.
- Alembic `0005_job_plane_role_split` requires all three roles, revokes legacy/default ACL leakage, grants exact table/column privileges, and enforces namespace/actor policy at the database boundary.
- Each composition root requires its exact DB username. `trading_jobs` is never accepted by API, worker, or scheduler.

- [ ] Add RED disposable-cluster tests for exact login flags, no memberships, distinct credentials, exact table/column ACLs, default ACL cleanup, and shared/reader/migrator denial on all job tables.
- [ ] Add RED cross-role DML tests: API cannot read/write leases or worker/scheduler state; worker cannot enqueue or write scheduler heartbeat; scheduler cannot cancel/claim/read leases; all roles cannot DELETE/TRUNCATE/DDL.
- [ ] Add RED tests for fresh install and `0004` to `0005` upgrade. Production runtime probes remain catalog-only `BEGIN READ ONLY` and are documented, not executed.
- [ ] Refactor API cancellation SQL to update cancellation-only columns so no API lease/result privilege is required.
- [ ] Implement admin role provisioning and 0005 grants/policies/triggers. `downgrade()` must raise a clear unsupported error; rollback preserves evidence and never restores the shared role.
- [ ] Update expected Alembic head to `0005_job_plane_role_split` in code/tests/templates used by the candidate.
- [ ] Write a separate B1 rollout runbook: require healthy `0004`, zero shared-role sessions, preservation/backup, role provisioning, apply 0005, counts/head/ACL/RLS/default-ACL verification, backup, and stop before service activation.

### Task 4: Bind exact dynamic evidence and make the worker SNAPSHOT-only

**Files:**
- Modify: `packages/runtime_release/semantic.py`
- Modify: `services/job_worker/safety_state.py`
- Modify: `services/job_worker/command_registry.py`
- Modify: `services/job_worker/environment.py`
- Modify: `services/job_worker/process_runner.py`
- Modify: `services/job_worker/results.py`
- Modify: `services/job_worker/worker.py`
- Modify: `services/job_worker/main.py`
- Modify: `services/job_store/worker_repository.py`
- Modify: `tests/runtime_release/test_semantic.py`
- Modify: `tests/jobs/test_safety_state.py`
- Modify: `tests/jobs/test_command_registry.py`
- Modify: `tests/jobs/test_child_environment.py`
- Modify: `tests/jobs/test_process_runner.py`
- Modify: `tests/jobs/test_result_validation.py`
- Modify: `tests/jobs/test_worker_lifecycle.py`

**Interfaces:**
- Semantic attestation returns exact active-file SHA, version-manifest SHA, semantic input fingerprint, version, and expiry; capability equality includes them.
- Safety preflight returns exact snapshot SHA, generated/expiry times, and PAPER/false/false state. Long jobs retain an initial/final digest chain while every heartbeat must pass current safety.
- Active command authority contains SNAPSHOT only and exact argv `<attested-python> -I -B main.py --mode snapshot --research-only`.
- Result validation requires the report's semantic fingerprint to match the spawn-bound value and persists sanitized authority/semantic/safety lineage.

- [ ] Add RED tests for semantic rotation between attest/consume, missing or mismatched report fingerprint, safety rotation/staleness, and persisted lineage.
- [ ] Add RED tests proving DEBATE/REPLAY/BACKTEST cannot produce a capability, claim, command, or spawn in this activation policy.
- [ ] Add RED ordering tests requiring final authority deadline and safety preflight immediately before `Popen`.
- [ ] Add RED environment tests proving repr redaction and absence of DB, broker, exchange, generic API, Job API, and live credentials.
- [ ] Implement typed evidence values and carry them through command, process outcome, result validation, and repository metadata without logging sensitive paths/values.
- [ ] Derive worker heartbeat code identity from attested authority; reject `TRADING_CODE_COMMIT` override.
- [ ] Move sealed validated results under the worker-owned artifact root and cover persistence/unit bindings in tests.

### Task 5: Build and verify Release Authority v2 without activation

**Files:**
- Create: `packages/runtime_release/v2.py`
- Create: `ops/release-v2/build-stage.sh`
- Create: `ops/release-v2/verify-stage.py`
- Create: `ops/release-v2/provision-root.sh`
- Create: `ops/release-v2/rollback.sh`
- Create: `tests/runtime_release/test_v2.py`
- Create: `tests/runtime_release/test_v2_provisioning.py`
- Create: `docs/production/release-authority-v2.md`
- Modify: `packages/runtime_release/config.py`
- Modify: `packages/runtime_release/manifest.py`
- Modify: `packages/runtime_release/provisioning.py`
- Modify: `packages/runtime_release/backend_policy.py`
- Modify: `packages/runtime_release/__init__.py`

**Interfaces:**
- Static authority binds one source commit/tree, component prefixes/trees, complete app/backend/dashboard artifacts, three lock hashes, Python/Node identities, generated contracts, Alembic `0005`, SNAPSHOT-only command manifest, unit/drop-in/effective-command identity, verifier hash, stage path/seal version, and prior release digest.
- Separate `ReleaseActivationV2` binds static release digest to exact current safety and semantic evidence. Its builder exists and is testable, but no activation record is created in this session.
- External stdlib verifier executes outside the sealed stage and accepts no digest from service environment variables.

- [ ] Add RED mutation matrix for extra/missing file, symlink, hardlink, xattr, wrong owner/mode, source/tree/prefix, lock/interpreter/contract/head/command/unit/drop-in/ExecStart/verifier/stage-path mismatch, unknown keys, and v1 confusion.
- [ ] Implement canonical strict models and complete-set verifier. Preserve v1 paths/schema as legacy; never reinterpret them as v2.
- [ ] Implement one-Git-object export for root, stripped backend subtree, and stripped dashboard subtree. Build/install only from frozen locks in offline mode and record component build identities.
- [ ] Generate candidate system units for distinct service identities and credential references; do not install them. Fake-root tests prove rejected/partial stages cannot become active and previous release remains rollback authority.
- [ ] Seal candidate read-only, execute no staged code after sealing, and verify it twice from independent verifier processes.

### Task 6: Verification, immutable candidate evidence, and no-runtime handoff

**Files:**
- Create: `docs/production/job-plane-source-verification-2026-07-16.md`
- Create: `docs/production/release-v2-candidate-2026-07-16.json`

**Interfaces:**
- Produces exact command log, candidate path/digests, changed-file list, rollback instructions, runtime skips, and residual risks without secret values.

- [ ] Run focused RED/GREEN tests after each task, then `make check-contracts`, `uv run pytest -q tests/jobs tests/control_api/test_alembic_schema.py`, and `uv run pytest -q tests/runtime_release`.
- [ ] Run dashboard `npm test`, `./node_modules/.bin/tsc --noEmit`, `npm run lint`, and the v2 dashboard build gate from its owning component.
- [ ] Run canonical audit and `make audit-release`. Commit only reviewed task changes so the candidate can bind one clean HEAD; do not push.
- [ ] Build the candidate in a non-runtime cache path, run the external verifier twice, compare complete manifests, and record only non-secret identities/digests.
- [ ] Run `git diff --check`, `git status --short --branch`, read-only `systemctl --user show ...`, and `ss -ltnp | rg ':8401\b'`. Do not start anything when they show inactive/absent.
- [ ] Independently review the full branch for Prompt 2 constraints. Runtime evidence remains explicitly absent because DB health failed and rollout approval was not supplied.
- [ ] Rollback is `git revert` of this branch's commits plus deletion of the uninstalled cache candidate; runtime DB, rows, events, artifacts, units, and services remain untouched.
