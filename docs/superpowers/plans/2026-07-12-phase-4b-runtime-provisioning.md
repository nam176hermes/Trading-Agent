# Phase 4B Runtime Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision attested immutable Phase 4 services, a minimal fresh
safety-evidence boundary, and a staged controlled SNAPSHOT/scheduler rollout
without exposing credentials or changing the active trading runtime.

**Architecture:** Phase 4B advances the audited Phase 4 application/backend
commits with code-reviewed manifest configuration and a safety exporter/client.
Immutable root-owned releases are then built from those final commits, while
root-owned manifests and protected env files supply exact digests. The worker
sees only the releases, a dedicated read-only semantic-input tree, a fresh
safety snapshot and its exact output paths; it never sees the active legacy
root.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, PostgreSQL 16, psycopg 3,
Alembic, pytest, systemd user units, SHA-256 manifests, Linux dirfd/
`O_NOFOLLOW`, Next.js verification only.

## Global Constraints

- Requested/effective mode remains `paper/paper`; both live gates remain
  `false`; canonical kill-switch semantics remain unchanged.
- Never access exchange/broker credentials, initialize/probe an exchange, send
  or cancel an order, or run DEBATE/REPLAY/BACKTEST real data.
- Never bind the whole active legacy root or expose `.env`, `.keys.enc`,
  exchange config, database owner credentials or the Job API token to a child.
- Releases/manifests are root-owned and non-writable by the runtime identity;
  no mutable worktree, symlink-to-worktree or startup manifest generation.
- Job API binds exactly `127.0.0.1:8401`; candidate dashboard is never deployed
  to port 3002; active agent/dashboard/Cloudflare are never restarted.
- Scheduler/timer remain disabled until API, worker, fixture safety and
  same-slot dedup gates pass; timer has `Persistent=false`.
- All behavior changes follow RED-GREEN-REFACTOR and receive an independent
  task review before the next runtime stage.

---

### Task 0: Resolve immutable source identity

**Files:**
- Modify: this plan only after the operator decision.

**Interfaces:**
- Consumes: audited bases `8eb6d26`, `51de1cf`, `843d449`.
- Produces: exact final application/backend commit IDs used by every release,
  manifest, unit and evidence document.

- [x] **Step 1:** Approve advancing application/backend commits for Phase 4B.
  The release names become `app-<final-phase4b-app-commit>` and
  `backend-<final-phase4b-backend-commit>`; the three supplied commits remain
  provenance bases.
- [x] **Step 2:** Reject any alternative that patches a Git archive during
  packaging or loads safety/digest authority through monkeypatching.
- [x] **Step 3:** Record the approved identity policy in the pre-provision and
  immutable-release evidence documents.

### Task 1: Deterministic release and manifest tooling

**Files:**
- Create: `packages/runtime_release/__init__.py`
- Create: `packages/runtime_release/manifest.py`
- Create: `scripts/build_phase4_release.py`
- Create: `scripts/verify_phase4_release.py`
- Create: `tests/runtime_release/test_manifest.py`
- Create: `tests/runtime_release/test_build.py`

**Interfaces:**
- Produces: `build_release(source_git_dir, commit, destination, policy)` and
  `verify_release(release_root, manifest_path, expected_digest, policy)`.
- Manifest entries contain canonical relative path, type, mode, size and
  SHA-256 in byte-sorted order; aggregate hash covers canonical JSON entries.

- [ ] **Step 1:** Write failing tests for exact commit export, exclusions,
  deterministic ordering, Python 3.11 interpreter identity, missing/modified/
  extra file rejection, unsafe link/mode/owner rejection and redacted errors.
- [ ] **Step 2:** Run `uv run pytest -q tests/runtime_release` and capture the
  expected missing-module failures.
- [ ] **Step 3:** Implement Git-object export without copying working-tree
  state, create a `--copies` Python 3.11 venv from locked dependencies, and
  generate canonical manifests outside the release.
- [ ] **Step 4:** Implement descriptor-anchored verification with no startup
  regeneration and no secret-bearing log values.
- [ ] **Step 5:** Run focused tests, tamper tests, compile and diff checks.
- [ ] **Step 6:** Commit `release: add deterministic phase 4b release tooling`.

### Task 2: Protected digest configuration and startup attestation

**Files:**
- Modify: `services/job_worker/command_registry.py`
- Modify: `apps/job_api/config.py`
- Modify: `apps/job_api/main.py`
- Modify: `services/job_worker/main.py`
- Create: `packages/runtime_release/config.py`
- Test: `tests/jobs/test_command_registry.py`
- Test: `tests/jobs/test_job_api_auth.py`
- Test: `tests/jobs/test_worker_lifecycle.py`

**Interfaces:**
- Consumes root-owned manifest paths plus exact lowercase SHA-256 values from
  protected service configuration.
- Produces fail-closed Job API readiness and worker startup attestation; raw
  digests never enter logs or API responses.

- [ ] **Step 1:** Write RED tests for missing, malformed, mismatched and
  user-writable digest configuration, plus exact valid startup.
- [ ] **Step 2:** Implement protected-file configuration validation using
  retained dirfds, exact owner/mode policy and constant-time digest comparison.
- [ ] **Step 3:** Ensure Job API readiness reports not-ready and worker exits
  before claim/spawn when app/backend/command authority is absent.
- [ ] **Step 4:** Run job API/worker/registry focused suites and commit
  `runtime: require protected release authority`.

### Task 3: Safety-state exporter and worker client

**Files:**
- Create: `services/safety_state_exporter/__init__.py`
- Create: `services/safety_state_exporter/exporter.py`
- Create: `services/safety_state_exporter/main.py`
- Create: `services/job_worker/safety_state.py`
- Modify: `services/job_worker/main.py`
- Modify: `services/job_worker/worker.py`
- Test: `tests/jobs/test_safety_state_exporter.py`
- Test: `tests/jobs/test_safety_state.py`
- Test: `tests/jobs/test_worker_lifecycle.py`

**Interfaces:**
- Exporter consumes only exact `.mode`, canonical kill sentinel and its own
  systemd-supplied false gates; it atomically writes a 0600 JSON snapshot at a
  fixed runtime path every 2 seconds with a 6-second expiry.
- Worker validates strict schema, expected exporter commit, owner/mode,
  generated/expiry window and source fingerprint before spawn and heartbeat.

- [ ] **Step 1:** Write RED exporter tests for paper/false/false/INACTIVE,
  atomic replacement, no credential-path reads, ACTIVE/UNKNOWN resolution and
  bounded timestamps.
- [ ] **Step 2:** Write RED client tests for missing/stale/invalid/unsafe-owner
  snapshots and exact safe acceptance.
- [ ] **Step 3:** Implement exporter using explicit allowlisted file opens only;
  never enumerate the legacy root or read environment files.
- [ ] **Step 4:** Replace the worker's direct legacy-root safety provider with
  the snapshot client at construction, immediate pre-spawn and every heartbeat.
- [ ] **Step 5:** Test running fixture termination on ACTIVE/stale/invalid
  snapshot and prove no orphan/finalize-success path.
- [ ] **Step 6:** Commit `safety: add fresh phase 4b safety evidence boundary`.

### Task 4: Semantic-input provisioning contract

**Files:**
- Create: `scripts/build_phase4_semantic_manifest.py`
- Modify: backend `research_semantics.py`
- Test: backend `tests/test_phase4_research_only.py`

**Interfaces:**
- Builds the already-defined six-logical-file external manifest from explicitly
  named source files into a dedicated 0700 runtime input tree; manifest is
  root-owned 0444 and digest is provided through protected authority config.

- [ ] **Step 1:** Write RED tests for explicit source allowlist, no whole-tree
  copy, credential-name rejection, exact hashes, 30-minute maximum validity and
  immutable-release versus external-input classification.
- [ ] **Step 2:** Implement a dry-run-first builder that copies only six named
  JSON inputs to a staging directory using `O_NOFOLLOW`, 0600 files and atomic
  finalization; never include safety inputs.
- [ ] **Step 3:** Adapt backend authority loading to the reviewed protected
  digest source without accepting an unset value at runtime.
- [ ] **Step 4:** Run backend semantic/integrity/offline suites and commit
  `runtime: provision attested semantic inputs`.

### Task 5: Hardened units and provisioning script

**Files:**
- Create: `ops/phase4b/provision-root.sh`
- Create: `ops/phase4b/verify-installed.sh`
- Create: `ops/systemd/trading-safety-state-export.service`
- Modify: the four Phase 4 systemd units
- Modify: `ops/systemd/README.md`
- Test: `tests/jobs/test_systemd_units.py`
- Test: `tests/runtime_release/test_provision_script.py`

**Interfaces:**
- Root script consumes prebuilt staging releases/manifests and installs with
  exact owner/mode, never reads a password, never starts a service and is
  idempotent.
- Units reference exact final release paths; worker sees only releases,
  manifests, semantic input, safety JSON and output/artifact paths.

- [ ] **Step 1:** Write RED static tests for exact paths, no legacy-root bind,
  no credential visibility, fixed service order, localhost network families,
  `UMask=0077`, timer disabled/non-persistent and no shell.
- [ ] **Step 2:** Implement units and an idempotent root provisioning script
  with pre/post hashes, ownership and permission assertions.
- [ ] **Step 3:** Run unit tests and syntax verification against a staged root.
- [ ] **Step 4:** Commit `ops: add phase 4b immutable provisioning`.
- [ ] **Step 5:** Because passwordless sudo is unavailable, stop and ask the
  operator to run the exact committed root script. Never request a password or
  substitute user-owned `/opt` authority.

### Task 6: Installed authority verification

**Files:**
- Create/update the immutable-release, command-manifest,
  semantic-input-manifest and systemd-provisioning evidence documents.

- [ ] **Step 1:** Verify installed release/manifests exact-set hashes and pinned
  digests.
- [ ] **Step 2:** Perform isolated tamper copies: modified, missing and extra
  file all fail while installed authority remains untouched.
- [ ] **Step 3:** Run `systemd-analyze --user verify` and require exit zero.
- [ ] **Step 4:** Recheck safety and active PIDs before any service start.

### Task 7: Safety exporter and Job API rollout

**Files:**
- Update runtime-smoke and final-runtime evidence documents only.

- [ ] **Step 1:** Install/start exporter, observe at least three refreshes and
  validate owner/mode/freshness/source fingerprint.
- [ ] **Step 2:** Start Job API only; prove exact loopback listener, live/ready,
  missing/wrong token rejection and valid authenticated list.
- [ ] **Step 3:** Verify no public/Cloudflare route, no token in process logs,
  payload or client artifacts.
- [ ] **Step 4:** Recheck active services, paper safety and orders/trades.

### Task 8: Worker fixture runtime gates

**Files:**
- Update worker runtime and rollback evidence documents.

- [ ] **Step 1:** Start one worker and verify fresh idle heartbeat.
- [ ] **Step 2:** Run allowlisted fixture jobs for claim/lease/events,
  cancellation, timeout and artifact permissions without legacy output.
- [ ] **Step 3:** Use an isolated safety snapshot fixture to prove ACTIVE,
  stale and invalid transitions terminate/block with no orphan.
- [ ] **Step 4:** Exercise expired-lease recovery in the test database; never
  crash a real snapshot after output.
- [ ] **Step 5:** Recheck credentials absent from child environment/logs.

### Task 9: Controlled real SNAPSHOT

**Files:**
- Update `phase-4b-snapshot-evidence.md`.

- [ ] **Step 1:** Recapture report inventory/hash, safety, active PIDs and
  orders/trades; require explicit unique idempotency key.
- [ ] **Step 2:** Enqueue exactly one SNAPSHOT and observe QUEUED, CLAIMED,
  RUNNING and terminal events plus one attempt/lease.
- [ ] **Step 3:** Require exit zero, fresh attributable report, schema/result
  validation, protected artifact hash and relative result reference before
  `SUCCEEDED`.
- [ ] **Step 4:** Prove no exchange/broker initialization, no credential/audit
  pollution, and orders/trades remain `30/0`.

### Task 10: Scheduler, rollback and final acceptance

**Files:**
- Complete scheduler, rollback, final-runtime and known-limitations evidence.

- [ ] **Step 1:** Manually run outside-slot oneshot and require
  `SKIPPED_NOT_SLOT`.
- [ ] **Step 2:** Inject the same current UTC slot twice and require one
  canonical job plus `ENQUEUED`/`DEDUPLICATED`, with no duplicate attempt.
- [ ] **Step 3:** Enable the non-persistent timer only after Tasks 6–9 pass;
  observe a heartbeat and one real `00`/`30` slot.
- [ ] **Step 4:** Drill rollback in timer→scheduler→worker→API→exporter order,
  prove no orphan and preserved events/artifacts, then restart in safe order if
  acceptance requires services left active.
- [ ] **Step 5:** Run the full main/backend/dashboard/contracts/Alembic/systemd
  chain, recapture safety/runtime and issue only the required GO/NO-GO phrase.
