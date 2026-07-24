# Task 9 Report: Bounded Runner, Validation, and Worker Lifecycle

## Outcome

Implemented protected bounded output capture, fixed result validators, hardened
process-group lifecycle handling, fenced worker orchestration, atomic result and
artifact persistence, fixed retry, and worker heartbeat persistence. All child
process and signal behavior in Task 9 tests is mocked. No approved release,
legacy backend, network service, exchange, broker, order, or deployment was
started or changed.

## Safety and concurrency behavior

- Artifact roots/directories are forced to `0700` and files to `0600` independent
  of umask. Each stream retains at most 1 MiB while continuously draining and
  hashing all observed bytes. PostgreSQL receives only relative refs, hashes,
  sizes, media types, truncation flags, and validator metadata.
- Report success requires exit zero and exactly one fresh, attempt-attributed,
  schema-valid report. Replay additionally requires a non-empty artifact for the
  exact requested session. Ambiguous output blocks reconciliation.
- The direct spawn boundary re-runs safety preflight, consumes Task 7's opaque
  `PreparedSpawn` once, rebuilds/revalidates the child environment, and invokes
  an argv list with `shell=False`, fixed cwd, and `start_new_session=True`.
- Process identity is captured from procfs after spawn. Cancellation, timeout,
  safety drift, and stale fences stop heartbeat renewal and target only the
  exact process group with SIGTERM then SIGKILL after grace. Identity drift
  prevents signaling; an initially unprovable spawned identity is killed and
  cannot enter the worker lifecycle.
- Claim/start/heartbeat/finalize operations retain the raw lease fence. Result
  metadata/artifact insertion and final state are one transaction. Eligible
  retry records `RUNNING -> FAILED -> QUEUED` atomically with fixed backoff;
  a stale lease cannot finalize.

## TDD and verification

RED was observed for each new module/API and for the last-moment preflight race.
Fresh final evidence:

```text
uv run pytest -q tests/jobs/test_artifacts.py tests/jobs/test_result_validation.py \
  tests/jobs/test_process_runner.py tests/jobs/test_worker_lifecycle.py
35 passed

uv run pytest -q tests/jobs
360 passed, 1 pre-existing Starlette deprecation warning

uv run python -m compileall -q services/job_worker services/job_store \
  tests/jobs/test_artifacts.py tests/jobs/test_result_validation.py \
  tests/jobs/test_process_runner.py tests/jobs/test_worker_lifecycle.py
exit 0

git diff --check
exit 0
```

## Remaining deployment gate

Task 7's immutable release and external approved manifest remain intentionally
unprovisioned. The worker composition root exists, but Task 9 did not start it
or install any service/timer. Production execution therefore remains blocked.
