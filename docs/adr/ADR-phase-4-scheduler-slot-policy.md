# ADR: Phase 4 Scheduler UTC Slot Policy

## Decision

Run a systemd oneshot scheduler tick every minute. Code uses injected UTC time
and enqueues `SNAPSHOT` only when minute equals `00` or `30`. Slot keys are
`YYYY-MM-DDTHH:00Z` and `YYYY-MM-DDTHH:30Z`; the idempotency key is
`schedule:snapshot:<slot>`. Repeated processes/ticks therefore resolve to one
canonical job.

Record each tick as `ENQUEUED`, `DEDUPLICATED`, `SKIPPED_NOT_SLOT`, or `FAILED`
in `scheduler_heartbeats`, along with actor/service/trace identity. The timer
uses `Persistent=false`; scheduler code accepts only the current UTC slot and
does not catch up historical slots. Debate, replay, and backtest are never
scheduled automatically.

The scheduler calls the shared enqueue repository under a dedicated scheduler
actor. It does not import worker command execution and does not treat enqueue
or heartbeat as research completion.

## Alternatives

- Cron and legacy shell tick scripts were rejected because they execute
  pipelines directly and lack durable idempotency/audit.
- `Persistent=true` historical catch-up was rejected because restart could
  enqueue stale work.
- Local-time scheduling was rejected because host timezone changes would alter
  slots.
- Automatic debate/replay/backtest was rejected because only snapshot has
  approved scheduled scope.

## Safety impact

The scheduler has enqueue authority only. Stable slot keys make duplicate
ticks harmless. UTC injection makes slot tests independent of host timezone.

## Failure behavior

Database failure records `FAILED` when possible and exits nonzero; there is no
filesystem or direct-process fallback. A missed slot is not caught up. Job
completion remains visible only through the job state and result metadata.

## Rollback

Disable the timer first, then stop the oneshot service. Existing queued/running
jobs remain governed by worker policy. No schedule or job row is deleted.
