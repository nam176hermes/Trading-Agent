# ADR: Phase 4 Job State Machine

## Decision

Use `QUEUED`, `CLAIMED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `BLOCKED`,
`TIMED_OUT`, `CANCEL_REQUESTED`, and `CANCELLED`. A single transition policy
permits:

- `QUEUED -> CLAIMED` and `QUEUED -> CANCELLED`;
- `CLAIMED -> RUNNING`, `CLAIMED -> CANCEL_REQUESTED`, or `CLAIMED -> BLOCKED`;
- `RUNNING -> SUCCEEDED`, `FAILED`, `TIMED_OUT`, `CANCEL_REQUESTED`, or
  `BLOCKED`;
- `CANCEL_REQUESTED -> CANCELLED` or `BLOCKED` when safe termination cannot be
  proven;
- `FAILED/TIMED_OUT -> QUEUED` only through the fixed retry policy after child
  absence is proven and attempts remain.

Terminal-state cancel is an idempotent no-op returning the existing job.
Every transition validates the current state, includes reason/trace identity,
updates the job, and appends an event in one transaction. Database constraints
validate states and application-role triggers reject normal event update or
delete.

Enqueue identity is `(job_type, idempotency_key)`. The same canonical payload
fingerprint returns the existing job; a different fingerprint returns HTTP 409
`IDEMPOTENCY_CONFLICT` without changing it.

## Alternatives

- Free-form status strings were rejected because invalid transitions become
  silent and unauditable.
- Treating enqueue as running or successful was rejected because it conflates
  durable intent, ownership, execution, and validated result.
- Introducing `RECOVERY_REQUIRED` was rejected to keep the approved enum;
  unresolved recovery uses `BLOCKED` with a specific reason code.
- Unlimited automatic retry was rejected because it can duplicate research
  work and costs.

## Safety impact

Transactional events make every mutation attributable. Central policy prevents
API, worker, and recovery code from inventing transitions. Retry never applies
to safety blocks, invalid payload/result, cancellation, or ambiguous child
ownership.

## Failure behavior

Invalid or stale transitions fail without partial event/state writes. Snapshot
has at most two attempts; manual types initially have one. Finalization failure
after a possibly valid artifact blocks reconciliation rather than spawning a
duplicate.

## Rollback

Stop mutating services and retain jobs, attempts, and append-only events for
audit. No state is rewritten or deleted. Dashboard commands become disabled.
