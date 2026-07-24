# Phase 4 Durable Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PostgreSQL durable research-job path with a loopback command API, one fenced paper-safe worker, a UTC half-hour snapshot scheduler, and authenticated dashboard BFF routes.

**Architecture:** The migration repository owns contracts, schema, repository, Job API, worker, scheduler, and unit definitions. The legacy backend gains only a tested `--research-only` seam, while the isolated hardened dashboard branch gains BFF routes and canonical job UI. The existing Control API, active services, port 3002, Cloudflare, live gates, kill switch, strategy, models, and prompts remain unchanged.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, psycopg 3, SQLAlchemy/Alembic, pytest, Next.js/TypeScript/Vitest, PostgreSQL, systemd user units.

## Global Constraints

- Never enable live trading, change either live gate, change kill-switch semantics, submit/cancel an order, probe/initialize an exchange, or read trading credentials.
- Keep the Control API read-only; Job API binds only `127.0.0.1:8401` and is never exposed through Cloudflare.
- Browser code never receives the Job API token; dashboard BFF authentication remains fail-closed.
- No shell, `shell=True`, client executable/argv/cwd/env/output path/timeout, dashboard spawn, or filesystem fallback.
- Production worker concurrency is one; scheduler enqueues only `SNAPSHOT` at UTC minute `00` and `30`, with no historical catch-up.
- Do not start/enable the scheduler timer until enqueue, lease, allowlist, and worker-safety tests pass.
- Do not restart the active agent/dashboard or alter port 3002/Cloudflare during implementation.
- Preserve all dirty linked-repository changes; edit only isolated worktrees and make focused commits.
- Every child forces `TRADING_MODE=paper`, `LIVE_EXECUTION_ENABLED=false`, and `LIVE_TRADING_APPROVED=false` and excludes service/trading credentials.
- Raw unbounded stdout/stderr never enters PostgreSQL; artifact directories/files are `0700`/`0600`.

## File map

- `packages/job_contracts/`: strict enums, payload models, canonical fingerprints, state transition policy, API DTOs.
- `services/job_store/`: PostgreSQL settings, transaction repository, claims, leases, events, heartbeats, recovery primitives.
- `apps/job_api/`: loopback FastAPI configuration, auth middleware/dependency, endpoints, error envelopes, entrypoint.
- `services/job_worker/`: safety provider, command registry, process/artifact runner, result validators, worker loop/recovery.
- `services/job_scheduler/`: UTC slot decision, repository enqueue, heartbeat, CLI entrypoint.
- `alembic/versions/0004_durable_research_jobs.py`: durable schema and append-only enforcement.
- `ops/systemd/`: hardened Job API, worker, scheduler service, and timer units.
- `/home/thenam176/.hermes/crypto-research/main.py`: research-only execution seam (edit via a dedicated isolated backend worktree, never the dirty runtime tree).
- `/home/thenam176/.local/share/codex-worktrees/trading-agent-security-phase4/trading-agent/`: hardened dashboard BFF/UI integration branch.

---

### Task 1: Baseline and package wiring

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/conftest.py`
- Create: `packages/__init__.py`
- Create: `packages/job_contracts/__init__.py`
- Create: `services/__init__.py`
- Create: `services/job_store/__init__.py`
- Create: `services/job_worker/__init__.py`
- Create: `services/job_scheduler/__init__.py`
- Create: `apps/job_api/__init__.py`
- Test: `tests/jobs/test_package_boundaries.py`

**Interfaces:**
- Produces importable `packages.job_contracts`, `services.job_store`, `apps.job_api`, `services.job_worker`, and `services.job_scheduler` package roots.
- Preserves existing Control API imports and test discovery.

- [ ] **Step 1: Capture the clean baseline**

Run: `uv sync --all-groups && uv run pytest -q`

Expected: existing migration/control tests pass. Record exact count; stop and document any unrelated pre-existing failure.

- [ ] **Step 2: Write a failing boundary test**

```python
def test_phase4_package_roots_are_importable():
    import apps.job_api
    import packages.job_contracts
    import services.job_scheduler
    import services.job_store
    import services.job_worker
```

Run: `uv run pytest -q tests/jobs/test_package_boundaries.py`

Expected: FAIL because Phase 4 package roots do not exist.

- [ ] **Step 3: Add package roots and hatch/test configuration**

Add the new source packages to Hatch wheel packaging and `tests/jobs` to pytest test paths. Package `__init__.py` files must export nothing with runtime side effects.

- [ ] **Step 4: Verify package and regression tests**

Run: `uv run pytest -q tests/jobs/test_package_boundaries.py tests/control_api/test_side_effects.py`

Expected: PASS; importing Phase 4 packages does not initialize exchange/broker or mutate files.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml packages services tests
git commit -m "jobs: scaffold phase 4 service boundaries"
```

### Task 2: Strict contracts, fingerprints, and transition policy

**Files:**
- Create: `packages/job_contracts/enums.py`
- Create: `packages/job_contracts/payloads.py`
- Create: `packages/job_contracts/fingerprint.py`
- Create: `packages/job_contracts/transitions.py`
- Create: `packages/job_contracts/api.py`
- Modify: `packages/job_contracts/__init__.py`
- Test: `tests/jobs/test_contracts.py`
- Test: `tests/jobs/test_state_machine.py`

**Interfaces:**
- Produces `JobType`, `JobState`, `ActorType`, `JobPayload`, `parse_payload(job_type, value)`, `payload_fingerprint(model)`, and `validate_transition(current, target, reason_code)`.
- Payloads: `SnapshotPayload(scope: Literal["default"], requested_as_of: None)`, `DebatePayload(asset: str, horizon: Literal["1d"])`, `ReplayPayload(session_id: str)`, `BacktestPayload(asset: str, strategy_id: Literal["legacy-binary-report-v1"], date_from: None, date_to: None)`.

- [ ] **Step 1: Write failing strict-payload and fingerprint tests**

```python
def test_payload_fingerprint_is_order_independent():
    left = parse_payload(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None})
    right = parse_payload(JobType.SNAPSHOT, {"requested_as_of": None, "scope": "default"})
    assert payload_fingerprint(left) == payload_fingerprint(right)

@pytest.mark.parametrize("field", ["executable", "argv", "cwd", "environment", "output_path", "timeout"])
def test_command_fields_are_forbidden(field):
    with pytest.raises(ValidationError):
        parse_payload(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None, field: "x"})
```

Also cover unknown type, 8 KiB canonical payload cap, unknown asset, replay path/traversal, metacharacters/newlines/flag injection, unsupported horizon/date range, stable symbol canonicalization, and deterministic compact JSON.

Run: `uv run pytest -q tests/jobs/test_contracts.py`

Expected: FAIL on missing contracts.

- [ ] **Step 2: Implement minimal strict models and fingerprinting**

Use `ConfigDict(extra="forbid", frozen=True)`, registry-backed uppercase assets, anchored session IDs such as `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`, sorted-key compact JSON, and SHA-256.

- [ ] **Step 3: Write failing state-transition table tests**

```python
@pytest.mark.parametrize("source,target", ALLOWED_CASES)
def test_allowed_transition(source, target):
    validate_transition(source, target, "TEST_REASON")

@pytest.mark.parametrize("source,target", FORBIDDEN_CASES)
def test_forbidden_transition(source, target):
    with pytest.raises(InvalidTransition):
        validate_transition(source, target, "TEST_REASON")
```

Cover every approved transition, retry-only guard, missing reason/trace rejection, terminal cancel no-op, and arbitrary transition denial.

- [ ] **Step 4: Implement the centralized policy and API DTOs**

DTOs expose sanitized job/attempt/event/artifact metadata only; no raw output, token, lease token, process environment, or request headers.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest -q tests/jobs/test_contracts.py tests/jobs/test_state_machine.py`

Expected: PASS.

```bash
git add packages/job_contracts tests/jobs
git commit -m "jobs: add strict contracts and state machine"
```

### Task 3: Alembic durable queue schema and role permissions

**Files:**
- Create: `alembic/versions/0004_durable_research_jobs.py`
- Modify: `ops/postgres/provision-roles.sql`
- Modify: `ops/postgres/.env.example`
- Test: `tests/jobs/test_alembic_jobs_schema.py`
- Test: `tests/jobs/test_job_role_permissions.py`

**Interfaces:**
- Produces revision `0004_durable_research_jobs` and tables `jobs`, `job_attempts`, `job_events`, `scheduler_heartbeats`, `job_artifacts`, `worker_heartbeats`.
- Enforces unique `(job_type,idempotency_key)`, state/type/outcome checks, foreign keys, query/claim indexes, JSON payload, lease fields, and event update/delete rejection for `trading_jobs`.

- [ ] **Step 1: Write an empty-database migration test**

Upgrade a temporary PostgreSQL database from base to head, assert the exact revision/table/column/constraint/index set, then downgrade only in the disposable database.

Run: `uv run pytest -q tests/jobs/test_alembic_jobs_schema.py`

Expected: FAIL because revision 0004 is absent.

- [ ] **Step 2: Add the schema migration**

Use timezone-aware timestamps, JSONB payload/metadata, bounded varchar fields, named checks, cascade only from jobs to attempt/event/artifact children, and no cascade from canonical legacy tables. Install a trigger that raises on application-role event update/delete.

- [ ] **Step 3: Write and pass permissions tests**

Provision a temporary `trading_jobs` role and assert no ownership/DDL/DELETE, allowed queue operations, and rejected event UPDATE/DELETE. Skip only when the explicitly documented test-admin connection is unavailable.

Run: `uv run pytest -q tests/jobs/test_alembic_jobs_schema.py tests/jobs/test_job_role_permissions.py`

Expected: PASS.

- [ ] **Step 4: Verify full migrations and commit**

Run: `uv run pytest -q tests/control_api/test_alembic_schema.py tests/jobs/test_alembic_jobs_schema.py`

Expected: PASS from empty DB and 0003 upgrade paths.

```bash
git add alembic/versions/0004_durable_research_jobs.py ops/postgres tests/jobs
git commit -m "db: add durable job queue schema"
```

### Task 4: Transactional repository, enqueue, list, and cancel

**Files:**
- Create: `services/job_store/config.py`
- Create: `services/job_store/repository.py`
- Create: `services/job_store/records.py`
- Create: `services/job_store/errors.py`
- Create: `services/job_store/__init__.py`
- Test: `tests/jobs/test_repository_enqueue.py`
- Test: `tests/jobs/test_repository_queries.py`
- Test: `tests/jobs/test_repository_transactions.py`

**Interfaces:**
- Produces `JobRepository.enqueue(request, *, trace_id) -> EnqueueResult`, `list_jobs(filters)`, `get_job(job_id)`, `request_cancel(job_id, actor, trace_id)`, and transactional `_transition`.
- `EnqueueResult.outcome` is `ENQUEUED` or `DEDUPLICATED`; conflicting fingerprints raise `IdempotencyConflict`.

- [ ] **Step 1: Write failing enqueue/idempotency tests**

Cover first enqueue, same type/key/fingerprint same ID, different fingerprint conflict, two concurrent transactions one row, actor/trace persistence, and rollback proving no job without event.

Run: `uv run pytest -q tests/jobs/test_repository_enqueue.py`

Expected: FAIL on missing repository.

- [ ] **Step 2: Implement enqueue with conflict-safe SQL**

Canonicalize before transaction; use unique-conflict recovery and compare the stored fingerprint. Insert `QUEUED` plus `ENQUEUED` event transactionally. Never persist authentication or environment material.

- [ ] **Step 3: Write failing query/cancel tests**

Assert stable `requested_at DESC, job_id DESC`, all filters/page bounds, detail ordering, `QUEUED -> CANCELLED`, `CLAIMED/RUNNING -> CANCEL_REQUESTED`, and terminal no-op.

- [ ] **Step 4: Implement query/cancel and atomic transition helpers**

Every update includes expected current state; an update count other than one raises a typed stale-transition error before commit.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest -q tests/jobs/test_repository_enqueue.py tests/jobs/test_repository_queries.py tests/jobs/test_repository_transactions.py`

Expected: PASS.

```bash
git add services/job_store tests/jobs
git commit -m "jobs: add idempotent enqueue repository"
```

### Task 5: Localhost Job Command API and authentication

**Files:**
- Create: `apps/job_api/__init__.py`
- Create: `apps/job_api/config.py`
- Create: `apps/job_api/auth.py`
- Create: `apps/job_api/errors.py`
- Create: `apps/job_api/app.py`
- Create: `apps/job_api/main.py`
- Test: `tests/jobs/test_job_api_auth.py`
- Test: `tests/jobs/test_job_api.py`
- Test: `tests/jobs/test_job_api_security.py`

**Interfaces:**
- Produces `create_app(settings, repository)`, authenticated `/v1/jobs` POST/GET, `/v1/jobs/{id}` GET, `/v1/jobs/{id}/cancel` POST, and health endpoints.
- Entrypoint rejects non-loopback bind configuration and defaults to `127.0.0.1:8401`.

- [ ] **Step 1: Write failing auth/readiness/body-limit tests**

Assert missing/invalid token rejection, `hmac.compare_digest` path, no token in response/log/traceback, 16 KiB body rejection, liveness independent of DB, and readiness 503 for DB/revision/token failures.

- [ ] **Step 2: Implement settings, auth, errors, and health**

Use a server-only token setting with no printable representation. Add request trace middleware without logging headers. Do not add CORS because direct browser access is unsupported.

- [ ] **Step 3: Write failing endpoint tests**

Cover create/deduplicate/409, filters/stable pages, detail with attempts/events/artifacts, cancellation rules, strict payload errors, sanitized 500s, and repository unavailability without fallback.

- [ ] **Step 4: Implement endpoint adapters**

Map repository exceptions to typed 409/422/503 responses. Return canonical records and never raw stdout/stderr or lease tokens.

- [ ] **Step 5: Verify no Control API mutation drift and commit**

Run: `uv run pytest -q tests/jobs/test_job_api*.py tests/control_api/test_api.py tests/control_api/test_side_effects.py`

Expected: PASS; generated Control API remains GET-only.

```bash
git add apps/job_api tests/jobs
git commit -m "api: add localhost job command service"
```

### Task 6: Legacy backend research-only seam

**Files:**
- Create isolated backend worktree/branch from `/home/thenam176/.hermes/crypto-research` without touching the dirty runtime checkout.
- Modify in that worktree: `main.py`
- Test in that worktree: `tests/test_phase4_research_only.py`

**Interfaces:**
- Produces CLI `--research-only`; `run_pipeline(symbols, enable_debate=False, enable_risk_personas=False, pad=None, allow_execution=True)`; snapshot/debate/backtest pass `allow_execution=not args.research_only`.
- Research-only invocation never loads shared `.env`, checks/stops positions, imports `paper_trader`, `broker`, `execute_live`, or exchange bridge, or calls execution functions.

- [ ] **Step 1: Create a clean isolated backend worktree and record its base**

Run non-destructive `git worktree add -b codex/phase-4-research-only /home/thenam176/.local/share/codex-worktrees/trading-agent-phase4-backend 0b977fe110f2fd66c3bc7e981b8531cb5dd7a8ac`, then confirm the runtime checkout status/hash is unchanged.

- [ ] **Step 2: Write failing parser/import/execution tests**

```python
def test_research_only_skips_execution(monkeypatch):
    monkeypatch.setattr(main, "check_stops", forbidden, raising=False)
    result = asyncio.run(main.run_pipeline(["BTC"], allow_execution=False))
    assert "paper_trader" not in imported_execution_modules()
    assert orders_and_trades() == baseline_counts
```

Use fixtures to stub collectors/LLM/output and assert shared dotenv loaders and all execution imports/calls are absent. Also prove normal legacy behavior is unchanged when the flag is absent.

- [ ] **Step 3: Implement the minimal seam**

Move execution imports behind `allow_execution`; guard stop sweep, backtest gate, execution block, and shared dotenv load. Reject `--research-only` with non-approved CLI modes. Do not alter strategy/model/prompt code or research output schemas.

- [ ] **Step 4: Verify isolated backend safety**

Run: `uv run pytest -q tests/test_phase4_research_only.py tests/test_phase1_safety.py tests/test_live_execution_policy.py`

Expected: all pass; no exchange initialization/order call; runtime checkout hashes/counts unchanged.

- [ ] **Step 5: Commit in the isolated backend worktree**

```bash
git add main.py tests/test_phase4_research_only.py
git commit -m "safety: add phase 4 research-only command seam"
```

### Task 7: Allowlisted commands and worker safety preflight

**Files:**
- Create: `services/job_worker/command_registry.py`
- Create: `services/job_worker/safety.py`
- Create: `services/job_worker/environment.py`
- Create: `services/job_worker/errors.py`
- Test: `tests/jobs/test_command_registry.py`
- Test: `tests/jobs/test_worker_safety.py`
- Test: `tests/jobs/test_child_environment.py`

**Interfaces:**
- Produces immutable `CommandSpec`, `COMMAND_REGISTRY`, `build_command(job) -> BuiltCommand`, `SafetyProvider.snapshot()`, `assert_safe(snapshot)`, and `build_child_environment(source)`.
- Fixed timeouts: snapshot 900s, debate 1200s, replay 120s, backtest 900s. Snapshot max attempts 2; manual types 1.

- [ ] **Step 1: Write failing registry tests for every job type**

Assert exact executable/cwd/argv prefix including `--research-only`, validated asset/session mapping, fixed timeouts/validator IDs, and inability to alter executable/module/cwd/env/output/additional argv.

- [ ] **Step 2: Implement immutable registry and mapping**

Resolve executable/cwd from protected service configuration at startup, validate both once, and construct argv lists only. Never accept a user path or command fragment.

- [ ] **Step 3: Write failing preflight/environment matrix tests**

Parameterize requested/effective modes, both gates, kill switch ACTIVE/UNKNOWN, type/payload/registry/cwd failures; assert subprocess is never called. Assert forced paper values and absence of service token, dashboard secrets, database-owner and known exchange/broker key names.

- [ ] **Step 4: Implement fail-closed safety and environment construction**

Use existing canonical file/env safety semantics read-only. Environment starts empty and copies only explicit approved keys. Unknown/missing safety state is blocked with the required reason code.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest -q tests/jobs/test_command_registry.py tests/jobs/test_worker_safety.py tests/jobs/test_child_environment.py`

Expected: PASS and no subprocess/exchange side effects.

```bash
git add services/job_worker tests/jobs
git commit -m "worker: add paper-safe command allowlist"
```

### Task 8: Claims, leases, fencing, and recovery

**Files:**
- Create: `services/job_store/worker_repository.py`
- Create: `services/job_worker/recovery.py`
- Test: `tests/jobs/test_worker_claims.py`
- Test: `tests/jobs/test_worker_leases.py`
- Test: `tests/jobs/test_worker_recovery.py`

**Interfaces:**
- Produces `claim_next(worker_id, lease_seconds, trace_id)`, `start_attempt`, `heartbeat`, `finalize`, and `recover_expired_leases(process_inspector)` with token predicates.
- `ProcessIdentity(pid, process_group, start_ticks, command_fingerprint)` fences PID reuse.

- [ ] **Step 1: Write failing concurrent-claim tests**

Use two PostgreSQL transactions to prove `SKIP LOCKED`, single claim, priority/FIFO ordering, attempt count, lease token, and transactional attempt/event creation.

- [ ] **Step 2: Implement claim and token-fenced updates**

Do not keep transactions open across execution. Every heartbeat/start/finalize SQL predicate includes job ID, expected state, worker ID, and raw lease token; logs receive only its hash.

- [ ] **Step 3: Write failing lease/recovery tests**

Cover valid/wrong/stale renew/finalize, lease expiry with absent child, matching live child blocked without retry, PID reuse, attempts exhausted, possible-result reconciliation, and accurate events/status.

- [ ] **Step 4: Implement conservative recovery**

Read `/proc` through an injectable inspector. Requeue only after positive child absence and fixed retry eligibility; otherwise transition to a reasoned `BLOCKED` state.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest -q tests/jobs/test_worker_claims.py tests/jobs/test_worker_leases.py tests/jobs/test_worker_recovery.py`

Expected: PASS including two-transaction fencing tests.

```bash
git add services/job_store/worker_repository.py services/job_worker/recovery.py tests/jobs
git commit -m "worker: add leased claims and fenced recovery"
```

### Task 9: Bounded process runner, result validation, cancellation, timeout

**Files:**
- Create: `services/job_worker/artifacts.py`
- Create: `services/job_worker/results.py`
- Create: `services/job_worker/process_runner.py`
- Create: `services/job_worker/worker.py`
- Create: `services/job_worker/main.py`
- Test: `tests/jobs/test_artifacts.py`
- Test: `tests/jobs/test_result_validation.py`
- Test: `tests/jobs/test_process_runner.py`
- Test: `tests/jobs/test_worker_lifecycle.py`

**Interfaces:**
- Produces `ArtifactWriter`, `ResultValidator`, `ProcessRunner.run(command, environment, timeout_seconds, heartbeat) -> ProcessOutcome`, `JobWorker.run_once() -> bool`, heartbeat loop, cancellation/safety termination, and worker heartbeat persistence.

- [ ] **Step 1: Write failing artifact/output tests**

Assert root/file permissions, 1 MiB cap, continuous pipe drain, observed-byte hashes, truncation metadata, relative refs, and no raw output in job/attempt rows.

- [ ] **Step 2: Implement protected streaming artifacts**

Open directories/files with restrictive modes independent of ambient umask. Sanitize tails only in memory/log metadata; persist ref/hash/size/truncated flags.

- [ ] **Step 3: Write failing result-validator tests**

Cover exit zero + fresh valid result success; nonzero, missing, stale, ambiguous, and invalid schema failure. Replay must match the validated session. Finalization ambiguity must block reconciliation.

- [ ] **Step 4: Implement fixed validators**

Use existing market-report contracts for report-producing jobs and freshness relative to attempt start. Hash the accepted artifact and store only metadata.

- [ ] **Step 5: Write failing lifecycle tests**

Mock subprocess and clock to cover start-new-session, `shell=False`, child identity capture, heartbeat renew, cancellation SIGTERM/SIGKILL, timeout, mid-run kill-switch/mode/gate drift, stale lease, retry policy, and completed resume/no-op.

- [ ] **Step 6: Implement runner and worker loop**

Immediately before process creation, call `prepare_immediate_spawn(job)`. It
attests the complete release, issues and consumes a short-lived monotonic
capability, and checks expiry both before and after full re-attestation. It
returns only an opaque, short-deadline `PreparedSpawn`. In the direct `Popen`
boundary call `consume_prepared_spawn(prepared)` exactly once and use those
fields immediately; delayed, forged, or repeated consumption blocks. There is
no startup capability, prepared token, or built command to cache. Record only
the consumed command's capability fingerprint. Then run safety preflight;
heartbeat faster than lease duration; terminate exact process group; stop
renewing after termination; finalize only with the current lease token.

- [ ] **Step 7: Verify and commit**

Run: `uv run pytest -q tests/jobs/test_artifacts.py tests/jobs/test_result_validation.py tests/jobs/test_process_runner.py tests/jobs/test_worker_lifecycle.py`

Expected: PASS with mocked commands and no exchange/order call.

```bash
git add services/job_worker tests/jobs
git commit -m "worker: add cancellation timeout and result validation"
```

### Task 10: UTC scheduler and heartbeat outcomes

**Files:**
- Create: `services/job_scheduler/slots.py`
- Create: `services/job_scheduler/scheduler.py`
- Create: `services/job_scheduler/main.py`
- Test: `tests/jobs/test_scheduler_slots.py`
- Test: `tests/jobs/test_scheduler_repository.py`

**Interfaces:**
- Produces `slot_for_tick(now_utc) -> Slot | None`, `schedule_tick(now, repository, identity) -> SchedulerOutcome`, and heartbeat outcomes `ENQUEUED`, `DEDUPLICATED`, `SKIPPED_NOT_SLOT`, `FAILED`.

- [ ] **Step 1: Write failing injected-time tests**

Assert 12:00/12:30 enqueue, 12:01/12:29/12:31 skip, host timezone independence, exact slot/idempotency strings, snapshot-only job type, and no historical catch-up.

- [ ] **Step 2: Implement pure UTC slot policy**

Reject naive datetimes and normalize aware values to UTC. Generate `schedule:snapshot:<slot>` only for the current injected tick.

- [ ] **Step 3: Write failing transactional scheduler tests**

Cover repeat tick and concurrent processes deduplicating to one job, each heartbeat outcome, actor/commit/trace persistence, and database failure without direct execution fallback.

- [ ] **Step 4: Implement repository scheduler and CLI**

The scheduler imports contracts/store only, never worker/process modules. Exit nonzero after sanitized `FAILED` handling.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest -q tests/jobs/test_scheduler_slots.py tests/jobs/test_scheduler_repository.py`

Expected: PASS.

```bash
git add services/job_scheduler tests/jobs
git commit -m "scheduler: add UTC half-hour snapshot enqueue"
```

### Task 11: Dashboard BFF and canonical job UI

**Files (isolated hardened dashboard worktree):**
- Create: `trading-agent/src/lib/trading/job-api.ts`
- Create: `trading-agent/src/app/api/trading/jobs/route.ts`
- Create: `trading-agent/src/app/api/trading/jobs/[id]/route.ts`
- Create: `trading-agent/src/app/api/trading/jobs/[id]/cancel/route.ts`
- Modify: `trading-agent/src/app/api/trading/run/route.ts`
- Modify: `trading-agent/src/app/api/trading/pipeline-status/route.ts`
- Modify: `trading-agent/src/components/trading/run-pipeline-button.tsx`
- Modify: `trading-agent/src/components/trading/pipeline-status.tsx`
- Test: existing security test location plus new `trading-agent/tests/trading-job-bff.test.ts`

**Interfaces:**
- Produces server-only `jobApiRequest`, authenticated same-origin list/detail/cancel routes, deprecated run enqueue wrapper, and bounded canonical-state UI.
- Uses existing operator-session/role guard; the Job API token is read only in server code.

- [ ] **Step 1: Read the bundled Next.js route-handler guidance and existing auth helpers**

Record the exact Next.js 16 APIs used. Do not introduce a parallel auth system.

- [ ] **Step 2: Write failing BFF security/behavior tests**

Assert missing session rejection, role enforcement, token added server-side but absent from bundles/responses/logs, fixed loopback origin, typed 503, trace/actor propagation, and list/detail/cancel behavior.

- [ ] **Step 3: Implement the server-only client and BFF routes**

Validate upstream schemas, bound timeouts/body sizes, and never fall back to files/processes. Feature flag off returns disabled/read-only.

- [ ] **Step 4: Write failing compatibility-route tests**

Spy on child-process and filesystem APIs; assert `/api/trading/run` enqueues typed jobs only and never reads/writes `run_status.json`. Capture the legacy file hash/mtime before/after.

- [ ] **Step 5: Replace run/status filesystem logic and update UI**

Map actions to fixed job payloads and show job ID, state, requested time, attempts, latest event/result/reason. Bound polling and distinguish queued from completed success.

- [ ] **Step 6: Verify dashboard and commit in its isolated branch**

Run: `npm test && npx tsc --noEmit && npm run lint && npm run build`

Expected: all pass; token absent from `.next/static`; `run_status.json` unchanged.

```bash
git add trading-agent/src trading-agent/tests
git commit -m "frontend: route dashboard research actions through job API"
```

### Task 12: Hardened systemd units and isolated service smoke

**Files:**
- Create: `ops/systemd/trading-job-api.service`
- Create: `ops/systemd/trading-job-worker.service`
- Create: `ops/systemd/trading-job-scheduler.service`
- Create: `ops/systemd/trading-job-scheduler.timer`
- Create: `ops/systemd/job-api.env.example`
- Create: `ops/systemd/job-worker.env.example`
- Create: `ops/systemd/README.md`
- Test: `tests/jobs/test_systemd_units.py`

**Interfaces:**
- Defines loopback Job API, single worker, oneshot scheduler, per-minute non-persistent timer, `UMask=0077`, least writable paths, and explicit environment files.

- [ ] **Step 1: Write failing static unit-policy tests**

Assert bind address, no shell scripts, timer calendar/`Persistent=false`, service ordering, no active-agent dependencies, hardening directives, restrictive umask, and minimal `ReadWritePaths`/address families.

- [ ] **Step 2: Implement unit definitions and protected-env instructions**

Examples contain names/placeholders only, never tokens. Document creation with mode `0600` and dedicated non-owner DB credentials.

- [ ] **Step 3: Verify unit syntax before installation**

Run: `systemd-analyze --user verify ops/systemd/trading-job-*.service ops/systemd/trading-job-scheduler.timer`

Expected: exit 0 without unknown directives.

- [ ] **Step 4: Apply migration and start Job API only after approval gates**

Capture a protected database backup/restore verification appropriate to schema change, apply Alembic 0004 using the documented role, create protected service env outside Git, start Job API, and verify loopback-only listener plus health/auth smoke. Do not start worker/timer on any failed gate.

- [ ] **Step 5: Start worker and run isolated fixture/no-op smoke**

Enqueue fixture-backed job, inspect claim/events/result metadata, idempotency, cancel, timeout, safety-block cases, and prove orders/trades/legacy outputs unchanged.
Only after the explicit deployment gate in Step 4, run an isolated Linux
capability smoke with a fixture/no-op child to verify WNOWAIT zombie retention,
leader pidfd acquisition, and bounded reap. Do not deliver signals to any
unrelated process. Record the command, kernel/Python capability result, child
identity, reap result, and absence of leftovers in
`docs/implementation/phase-4-runtime-evidence.md`. This smoke is operations
evidence, never a Task 9 unit test.

- [ ] **Step 6: Start scheduler service/timer only after all prior tests pass**

Observe outside-slot, injected/repeated same-slot dedup, and heartbeat separation. Do not run a real legacy snapshot without fresh explicit user approval.

- [ ] **Step 7: Commit**

```bash
git add ops/systemd tests/jobs/test_systemd_units.py
git commit -m "ops: add hardened phase 4 systemd units"
```

### Task 13: Contracts, full verification, evidence, and rollback drill

**Files:**
- Modify: `scripts/generate_contracts.py`
- Create: `docs/implementation/phase-4-schema.md`
- Create: `docs/implementation/phase-4-job-api.md`
- Create: `docs/implementation/phase-4-worker.md`
- Create: `docs/implementation/phase-4-scheduler.md`
- Create: `docs/implementation/phase-4-dashboard-integration.md`
- Create: `docs/implementation/phase-4-command-allowlist.md`
- Create: `docs/implementation/phase-4-lease-recovery.md`
- Create: `docs/implementation/phase-4-test-evidence.md`
- Create: `docs/implementation/phase-4-runtime-evidence.md`
- Create: `docs/implementation/phase-4-scheduler-evidence.md`
- Create: `docs/implementation/phase-4-rollback.md`
- Create: `docs/implementation/phase-4-known-limitations.md`

**Interfaces:**
- Produces generated/checkable internal Job API schemas, exact acceptance evidence, and a tested rollback that disables commands without restoring spawn/files.

- [ ] **Step 1: Add generated internal Job API contract checks**

Generate deterministic OpenAPI/JSON schema for the new internal API without changing the existing Control API contract. Run `uv run python scripts/generate_contracts.py --check` and contract drift tests.

- [ ] **Step 2: Run the complete migration/service suite**

Run: `uv run pytest -q && uv run python scripts/generate_contracts.py --check && uv run alembic current`

Expected: all tests pass and current revision is `0004_durable_research_jobs` in the isolated/runtime-approved database.

- [ ] **Step 3: Run backend and dashboard regressions**

Run the documented backend isolated 43-test integration target, Phase 1 safety target (85 pass, 2 intended connectivity skips), and dashboard `npm test`, typecheck, lint, build. Record exact fresh counts rather than copying expected numbers on drift.

- [ ] **Step 4: Run API/security/runtime smoke**

Verify live/ready, authenticated enqueue/list/detail/cancel, duplicate same ID, fingerprint 409, unauthenticated/invalid-token denial, localhost-only 8401, arbitrary command impossible, worker fencing/safety/cancel/timeout, scheduler slot/dedup, no GET writes, no exchange import/call, and bounded artifacts.

- [ ] **Step 5: Drill rollback**

Disable timer, stop scheduler/worker/API, set dashboard commands disabled, prove Control API and active port 3002 remain available, then restore Phase 4 local services only if continuing evidence requires it. Never restore spawn or `run_status.json`.

- [ ] **Step 6: Capture final invariants and hashes**

Record modes, gates, kill switch, orders/trades, PIDs, port 3002, Cloudflare, legacy output hashes, `run_status.json` hash/mtime, queue depth, service states, heartbeats, latest enqueued/completed job, and apply/feature guards.

- [ ] **Step 7: Write all evidence and limitations**

State single worker, PostgreSQL queue, snapshot-only schedule, manual other jobs, no catch-up, metric-export limitations, unchanged research semantics, and live NO-GO. Any missing acceptance evidence forces the prescribed Phase 4 NO-GO conclusion.

- [ ] **Step 8: Final docs commit**

```bash
git add scripts/generate_contracts.py generated docs/implementation
git commit -m "docs: add phase 4 runtime and rollback evidence"
```

- [ ] **Step 9: Final worktree and decision review**

Show status/commits for migration, isolated backend, isolated dashboard, and untouched runtime repositories. Conclude only `GO FOR PHASE 5 — DETERMINISTIC RISK AND EXECUTION SERVICE SEPARATION` or `NO-GO — PHASE 4 JOB DURABILITY, SAFETY, OR SCHEDULER BLOCKERS REMAIN`; never propose live trading.
