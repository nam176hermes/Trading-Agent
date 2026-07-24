# Phase 4 UTC Snapshot Scheduler

`services/job_scheduler` implements a pure, injected-time UTC slot decision.
Only minute `00` and `30` are slots. The slot format is
`YYYY-MM-DDTHH:00Z` or `YYYY-MM-DDTHH:30Z`, and the durable idempotency key is
`schedule:snapshot:<slot>`.

The scheduler can enqueue only this fixed payload:

```json
{"scope":"default","requested_as_of":null}
```

It records one of `ENQUEUED`, `DEDUPLICATED`, `SKIPPED_NOT_SLOT`, or `FAILED`
in `scheduler_heartbeats`. That row proves only a tick/enqueue outcome; it does
not prove the research job started or succeeded. `DEBATE`, `REPLAY` and
`BACKTEST` have no automatic scheduling path.

The supplied systemd timer ticks every minute with `Persistent=false`, so it
does not request historical catch-up after downtime. The oneshot scheduler
uses the PostgreSQL repository and never imports the worker process runner or
executes a legacy shell script.

The scheduler service and timer have not been installed, started or enabled.
There is no runtime scheduler heartbeat yet. They must remain off until the
immutable releases exist and enqueue, lease, allowlist, worker-safety,
cancellation, timeout and result-validation gates pass. In particular, an
operational Job API alone does not authorize the timer while the worker safety
boundary is unresolved.

References: [slot-policy ADR](../adr/ADR-phase-4-scheduler-slot-policy.md),
[schema](phase-4-schema.md), and [rollback](phase-4-rollback.md).
