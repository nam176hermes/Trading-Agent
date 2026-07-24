# Task 8 Report: Leased Claims, Fencing, and Recovery

## Outcome

Implemented the PostgreSQL worker claim boundary, token-fenced lifecycle
updates, complete process identity recording, and conservative expired-lease
recovery. No worker, subprocess, signal, scheduler, service, exchange, broker,
order, or trade was started or changed.

## Concurrency and fencing

- Claims use `FOR UPDATE SKIP LOCKED` ordered by priority descending,
  `requested_at` ascending, and `job_id` ascending.
- The claim transaction performs `QUEUED -> CLAIMED`, attempt-count increment,
  random lease assignment, attempt insertion, and attributed event insertion
  atomically. It closes before returning the claim.
- A simultaneous two-transaction test proves a single queued job is assigned
  once. A separate held-row test proves a worker skips the locked highest
  priority job and claims the next eligible job without waiting.
- Start, heartbeat, and finalize predicates include job ID, expected state,
  worker ID, raw lease token, and unexpired lease. Matching attempt mutations
  additionally require attempt ID and the same fence. Wrong, stale, expired,
  or reassigned fences update zero rows and append no false event.
- Raw lease tokens are excluded from claim representations and event metadata;
  only SHA-256 token hashes enter event metadata.

## Recovery behavior

- `ProcessIdentity` records PID, process group, Linux process-start ticks, and
  lowercase SHA-256 command fingerprint. `ProcProcessInspector` only reads
  procfs; it never signals or kills a process. Tests use an injected fake.
- A matching live child becomes `BLOCKED` with
  `LEASE_EXPIRED_CHILD_STILL_RUNNING` and is never retried.
- Only positive child absence (an inspector `None` result) marks the attempt
  `INTERRUPTED`. An eligible snapshot records `RUNNING -> FAILED -> QUEUED`
  with fixed 30-second backoff; exhausted jobs remain `FAILED`. Any non-`None`
  identity mismatch or PID reuse blocks without retry.
- Existing possible-result metadata blocks with
  `RESULT_RECONCILIATION_REQUIRED`. Incomplete or unreadable identity evidence
  blocks rather than retrying. Expiry before a proven running result state also
  blocks conservatively.
- Every recovery mutation repeats the original state, worker, lease token, and
  expiry predicates so a concurrent heartbeat/finalization wins without a
  false recovery event.

## TDD and verification

RED was observed first: all three focused test modules failed collection
because `services.job_store.worker_repository` did not exist.

Fresh GREEN evidence before commit:

```text
uv run pytest -q tests/jobs/test_worker_claims.py \
  tests/jobs/test_worker_leases.py tests/jobs/test_worker_recovery.py
13 passed

uv run pytest -q tests/jobs
317 passed, 1 pre-existing Starlette deprecation warning

uv run python -m compileall -q services/job_store services/job_worker \
  tests/jobs/test_worker_claims.py tests/jobs/test_worker_leases.py \
  tests/jobs/test_worker_recovery.py
exit 0
```

`ruff` is not installed in the project environment, so that optional command
could not run. `git diff --check` is included in the final verification gate.

## Review-fix verification

- Heartbeat and finalize now require an explicit active attempt outcome,
  separately from the job state, and enforce the legal mapping (`CLAIMED` to
  `CLAIMED`, `RUNNING` to `RUNNING`, and `CANCEL_REQUESTED` to either active
  outcome). A cancelled claimed attempt remains `CLAIMED` and a cancelled
  started attempt remains `RUNNING` until the fenced
  `CANCEL_REQUESTED -> CANCELLED` finalization; no schema-invalid
  `CANCEL_REQUESTED` attempt outcome is written.
- Recovery treats only an inspector `None` result as positive child absence.
  Exact live identity, identity mismatch/PID reuse, incomplete identity, and
  inspector failure all block with distinct conservative reason codes.
- After process observation, recovery locks and re-reads the current fenced job
  and attempt, including current result and identity fields. Result evidence
  always wins and forces reconciliation; a changed fence or terminal attempt
  yields `LEASE_RECOVERY_STALE` without an event.
- Recovery attempt updates repeat the captured active outcome, worker, token,
  attempt, and job predicates. Any zero-row fenced mutation aborts the whole
  transaction before an event can commit.
- Candidate selection and locked re-read both enforce the same active mapping;
  terminal or inconsistent attempt rows are never captured as expected active
  outcomes and produce no recovery mutation or event.
- Deterministic PostgreSQL races cover a result appearing during process
  inspection and an attempt becoming terminal between candidate selection and
  recovery.

Fresh review-fix evidence:

```text
uv run pytest -q tests/jobs/test_worker_claims.py \
  tests/jobs/test_worker_leases.py tests/jobs/test_worker_recovery.py
21 passed

uv run pytest -q tests/jobs
325 passed, 1 pre-existing Starlette deprecation warning
```

## Integration seam for Task 9

The process identity command fingerprint must be reproducible after a worker
restart. `ProcProcessInspector` defines it as SHA-256 over the exact procfs
`cmdline` bytes. Task 7's capability fingerprint includes a monotonic issuance
nonce and cannot be reconstructed from a live child. Task 9 must therefore
capture the spawned child's procfs command fingerprint for `ProcessIdentity`;
using the nonce-derived capability fingerprint in this database field would
make a matching live child appear different during recovery.

## Rollback

Revert the Task 8 commit. This removes worker claim/recovery code and its tests
only. No database downgrade, process stop, lease reassignment, or data cleanup
is required because this task ran exclusively against disposable PostgreSQL
databases.
