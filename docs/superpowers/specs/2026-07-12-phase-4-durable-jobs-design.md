# Phase 4 Durable Jobs, Scheduler, and Command Boundary Design

**Status:** Approved in conversation on 2026-07-12

**Goal:** Replace dashboard-owned process spawning and `run_status.json` state
with an authenticated, PostgreSQL-backed research-job boundary whose single
worker can run only fixed, paper-safe commands and whose scheduler can enqueue
only half-hourly UTC snapshots.

## Scope and non-negotiable safety boundary

Phase 4 adds a localhost Job Command API, durable job repository, one worker,
one minute scheduler tick, dashboard BFF routes, protected result artifacts,
systemd unit definitions, tests, and audit evidence. It does not alter strategy,
models, prompts, signals, risk semantics, execution authority, the read-only
Control API, active port 3002, Cloudflare, or the active legacy services.

The requested and effective modes remain `paper`; both live gates remain
`false`; the kill switch remains canonical and fail-closed. A Phase 4 child
must neither import nor initialize broker/exchange execution paths, submit or
cancel orders, nor receive trading credentials. PostgreSQL or service failure
has no filesystem or direct-process fallback.

## Delivery gates

1. Capture the pre-change Git, process, network, database, scheduler, legacy
   command, output, and safety checkpoint. Stop on invariant drift.
2. Approve this design and the six Phase 4 ADRs before schema or code changes.
3. Add schema, contracts, state machine, and repository test-first.
4. Add the loopback Job API and prove authentication, size limits,
   idempotency, and token non-disclosure.
5. Add a research-only legacy CLI seam before any real command can be
   allowlisted. Prove it skips stop checks and all paper/broker/live execution
   and does not import exchange modules.
6. Add the worker with mocked commands; prove safety preflight, heartbeat-time
   safety, leases, fencing, cancellation, timeout, output bounds, result
   validation, and recovery.
7. Add UTC scheduler logic and systemd definitions. Do not enable the timer
   until enqueue, lease, allowlist, and worker-safety suites pass.
8. Integrate dashboard BFF routes in the isolated hardened-dashboard branch.
   Do not restart or cut over the active dashboard in Phase 4 verification.
9. Run isolated fixture integration. A real-data snapshot is optional and may
   run only after explicit in-session approval and all safety gates pass.
10. Smoke local services, observe scheduler outcomes, drill rollback, and
    recapture all runtime invariants.

## Components and trust boundaries

### Read-only Control API

The existing Control API remains GET-only and owns market, status, signals,
decisions, capabilities, and costs. It receives no job POST endpoint, does not
write the queue, and never starts a subprocess.

### Job Command API

`apps/job_api` is a separate FastAPI process bound explicitly to
`127.0.0.1:8401`. Authenticated endpoints enqueue, list, inspect, and request
cancellation of durable jobs. `/health/live` reports process liveness;
`/health/ready` additionally requires PostgreSQL, revision support, repository
initialization, and configured service authentication. It does not require a
worker heartbeat.

All job endpoints require a separate bearer token compared in constant time.
Missing configuration makes authenticated operations fail closed. Request
bodies are limited to 16 KiB and canonical payload JSON to 8 KiB. Headers,
tokens, and raw payloads are excluded from structured logs and error bodies.

### Dashboard BFF

The browser calls same-origin server routes only. The dashboard server first
verifies an operator session and authorization, constructs the actor and trace
identity, then calls the loopback Job API with a server-only token. The token
is never exposed to client code, responses, PostgreSQL, or logs.

The compatibility `/api/trading/run` route becomes a deprecated enqueue
wrapper. It does not inspect or modify `run_status.json`, run Python, invoke a
shell, or spawn a child. Job list/detail/cancel routes return canonical states
and typed unavailability errors. UI wording distinguishes queued, claimed,
running, succeeded, blocked, failed, timed out, cancel requested, and
cancelled; enqueue is never presented as successful research completion.

### Worker

`services/job_worker` is the only component allowed to spawn a process.
Production concurrency is one. It claims with `FOR UPDATE SKIP LOCKED`, closes
the claim transaction before child execution, uses a lease token on every
heartbeat and finalization, and records an attempt and event for every state
change. A stale worker cannot renew or finalize.

Immediately before spawn and on every heartbeat the worker checks requested
mode, effective mode, both live gates, and kill-switch state. Unknown is
unsafe. Cancellation or safety drift terminates the exact process group with
SIGTERM followed by bounded grace and SIGKILL, then records the appropriate
terminal state and reason.

### Scheduler

`services/job_scheduler` runs as a systemd oneshot every minute. It uses UTC,
accepts only current ticks at minute `00` or `30`, and enqueues only `SNAPSHOT`
with key `schedule:snapshot:<YYYY-MM-DDTHH:MMZ>`. Outside slots are recorded as
`SKIPPED_NOT_SLOT`; repeat slots are `DEDUPLICATED`; errors are `FAILED`.
`Persistent=false` and code-side current-slot validation prevent historical
catch-up. A scheduler heartbeat is enqueue evidence, never completion
evidence.

The scheduler writes through the job repository using a dedicated scheduler
actor so a database/API failure can still be represented consistently within
the same database transaction boundary. It does not import worker execution
code or run research commands.

## Database design

Alembic `0004_durable_research_jobs` adds `jobs`, `job_attempts`, `job_events`,
`scheduler_heartbeats`, `job_artifacts`, and `worker_heartbeats` without
rewriting Phase 3/3B migration history.

`jobs` owns the canonical state, strict typed payload, SHA-256 fingerprint,
actor, priority, timestamps, attempts, lease, cancellation, result, and
sanitized error fields. `(job_type, idempotency_key)` is unique. The same key
and fingerprint returns the existing row; a different fingerprint returns
`IDEMPOTENCY_CONFLICT` without mutation.

`job_attempts` records claim/run identity, lease, child PID/process group,
Linux process-start ticks, command fingerprint, exit/termination metadata,
and bounded artifact references/hashes. Raw stdout/stderr are not database
columns. `job_events` is append-only; database triggers reject update/delete
for the application role. Each transition update and event insert occur in
one transaction. Heartbeat tables keep service instance/commit/timestamp and
do not imply job success.

The initial deployment uses one dedicated non-owner `trading_jobs` role with
no DDL, ownership, or DELETE privilege. Database-enforced append-only events
and application capability checks limit the shared role. Splitting enqueue,
worker, and scheduler roles is a documented hardening follow-up, not a reason
to use `trading_owner`.

## State machine, retry, and recovery

Canonical states are `QUEUED`, `CLAIMED`, `RUNNING`, `SUCCEEDED`, `FAILED`,
`BLOCKED`, `TIMED_OUT`, `CANCEL_REQUESTED`, and `CANCELLED`. A centralized
policy validates only the transitions defined in the state-machine ADR. Each
transition requires a reason code and trace ID.

Retries are not general state rewrites. Only an allowlisted transient failure
or an expired lease whose child is proven absent may schedule
`FAILED/TIMED_OUT -> QUEUED`; attempts must remain and a fixed backoff applies.
Safety, validation, cancellation, result ambiguity, and live-child recovery
are never automatically retried. Snapshot permits at most two attempts;
manual job types permit one initially.

The worker stores PID, process group, `/proc/<pid>/stat` start ticks, and
command fingerprint. After lease expiry, a matching living child causes the
job to become `BLOCKED` with `LEASE_EXPIRED_CHILD_STILL_RUNNING`; it is never
duplicated. Only a positively absent or identity-mismatched process can be
marked interrupted and considered for retry. A valid-looking result with
failed database finalization is blocked for reconciliation.

## Strict job contracts and command registry

Pydantic models use `extra="forbid"`; canonical JSON has sorted keys and
compact separators. Clients cannot supply executable, module, command, argv,
shell, cwd, environment, output path, or timeout.

- `SNAPSHOT`: `scope` must equal `default`; `requested_as_of` must be null.
- `DEBATE`: `asset` must resolve through the existing registry; `horizon` is
  fixed to `1d` because the legacy CLI exposes no horizon control.
- `REPLAY`: uses a validated `session_id`, not an arbitrary path. The current
  legacy records do not prove a deterministic `decision_id -> session`
  relationship, so `decision_id` is rejected rather than guessed.
- `BACKTEST`: `asset` must resolve through the registry and `strategy_id` must
  equal `legacy-binary-report-v1`. `date_from` and `date_to` must be null until
  the legacy CLI gains deterministic range support.

The code-owned registry uses the fixed backend virtualenv Python executable,
fixed backend working directory, fixed module/script and argv prefixes, fixed
timeouts, retry limits, child-environment keys, and result validator. Payload
values can populate only validated symbol/session positions. Shell metacharacter
and newline inputs fail contract validation even though `shell=False` is also
mandatory.

### Research-only seam

Current legacy `main.py` snapshot/debate/backtest paths call stop checks and
paper/broker execution and import execution modules at module load. They are
not safe allowlist entries as-is. Phase 4 therefore adds `--research-only`
and passes `allow_execution=False` through these modes. In research-only mode:

- stop checks and the complete execution block are skipped;
- paper trader, broker, live execution, and exchange bridge modules are not
  imported or initialized;
- the shared `~/.hermes/.env` and backend `.env` are not loaded;
- only an explicitly provided, least-privilege research provider environment
  may be used;
- generated research artifacts keep their legacy schemas and strategy/model
  semantics.

The registry always includes `--research-only`; there is no client override.
Tests fail if execution modules are imported or order/trade functions called.

## Child execution and artifacts

The worker constructs a new environment from a small key allowlist and always
sets `TRADING_MODE=paper`, `LIVE_EXECUTION_ENABLED=false`, and
`LIVE_TRADING_APPROVED=false`. It never copies the entire service environment
and excludes the Job API token, dashboard credentials, database-owner
credentials, and all exchange/broker credentials. Dedicated research provider
credentials, if required, come from a protected service environment file and
have no trading authority.

Subprocess execution uses an argv list, `shell=False`, fixed cwd,
`start_new_session=True`, a process-group timeout, and no user-controlled
paths. Stdout/stderr stream to a Phase 4 artifact directory with mode `0700`;
files are created `0600`. Each stream is capped at 1 MiB, hashes the observed
bytes, marks truncation, and stores only relative references/hash metadata in
PostgreSQL.

Fixed initial timeouts are snapshot 15 minutes, debate 20 minutes, replay 2
minutes, and backtest 15 minutes. Result validators require exit code zero and
an attributable, fresh, schema-valid artifact. Snapshot/debate/backtest require
a new valid report produced after attempt start; replay requires a non-empty
protected replay artifact tied to the validated session. Missing or invalid
results produce `RESULT_VALIDATION_FAILED`.

## Observability and API contract

Structured logs contain trace, job, attempt, type, transition, worker, hashed
lease token, duration, exit, reason, and result hash—never raw tokens, leases,
headers, or unbounded payload/output. Repository counters implement the named
Phase 4 metrics interface; exporting a public Prometheus endpoint is deferred.

Existing public Control API contracts remain unchanged. Job API schemas are a
new internal contract and generated/checkable independently. Dashboard types
mirror them without exposing lineage, credentials, or raw output.

## Deployment and rollback

Units are installed and verified before start. Job API starts first, then the
worker, then isolated enqueue/cancel smoke. The scheduler timer may be started
only after all safety/lease/allowlist tests pass. No Phase 4 action restarts the
active agent or dashboard or alters Cloudflare.

Rollback disables and stops the timer/scheduler, worker, and Job API, then
disables dashboard command controls with a feature flag. It preserves all
jobs/events/artifacts and leaves the Control API and active port 3002 intact.
It never restores dashboard spawning or `run_status.json` operational truth.

## Acceptance decision

Phase 4 is `GO FOR PHASE 5 — DETERMINISTIC RISK AND EXECUTION SERVICE
SEPARATION` only after fresh schema, contract, API, worker, scheduler,
dashboard, systemd, rollback, and runtime evidence satisfies the approved
acceptance criteria. Otherwise it is `NO-GO — PHASE 4 JOB DURABILITY, SAFETY,
OR SCHEDULER BLOCKERS REMAIN`. Live trading remains out of scope and NO-GO.
