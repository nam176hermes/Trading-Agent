# Phase 4 Scheduler Evidence

## Verified code policy

The scheduler implementation is enqueue-only. An injected timezone-aware
instant is normalized to UTC; minute `00` or `30` yields exactly one
`SNAPSHOT` identity:

```text
slot key:       YYYY-MM-DDTHH:00Z or YYYY-MM-DDTHH:30Z
idempotency:    schedule:snapshot:<slot-key>
actor:          dedicated scheduler identity
automatic jobs: SNAPSHOT only
catch-up:       none
```

All other minutes produce `SKIPPED_NOT_SLOT`. Repeating or concurrently
processing the same slot returns the same canonical job and records
`DEDUPLICATED`; it does not create a second job. Heartbeat outcomes distinguish
`ENQUEUED`, `DEDUPLICATED`, `SKIPPED_NOT_SLOT` and `FAILED`. None of those
outcomes represents research completion.

## Test-only evidence

At commit `26754a7`, the focused scheduler suite passed 12 tests. Coverage used
an injected clock and disposable PostgreSQL databases and included:

- exact `12:00` and `12:30` UTC slots;
- non-slot minutes and naive-time rejection;
- host-timezone independence;
- repeat and concurrent same-slot deduplication;
- SNAPSHOT-only automatic enqueue;
- all heartbeat outcomes and persisted scheduler/commit/trace identity;
- sanitized database-failure behavior and nonzero CLI exit;
- an import boundary excluding worker/process/legacy research modules.

The systemd unit tests cover a per-minute, non-persistent timer and a oneshot
scheduler service. Those are unit/injected checks only. No scheduler tick was
run against the runtime database.

## Runtime evidence

```text
trading-job-scheduler.service: not installed, inactive/dead
trading-job-scheduler.timer:   not installed, inactive/dead, not enabled
scheduler_heartbeats rows:     0
jobs rows:                     0
latest scheduled enqueue:      none
latest successful snapshot:    none
```

No outside-slot runtime tick, same-slot runtime deduplication, controlled slot
enqueue or worker completion has been observed. The dashboard must not report
the scheduler or research pipeline as healthy based on the unit tests.

`systemd-analyze --user verify` currently exits `1` for the Phase 4 units
because the fixed interpreter
`/opt/trading-agent-phase4/releases/phase4-0001/.venv/bin/python3.11` has not
been provisioned. The timer must remain disabled until the immutable release,
manifest, worker safety gates, final test chain and Job API/worker runtime smoke
all pass.

Runtime scheduler acceptance: **PENDING / NOT RUN**.
