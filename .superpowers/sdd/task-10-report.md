# Task 10 Report: UTC Scheduler and Heartbeat Outcomes

## Outcome

Implemented the enqueue-only UTC scheduler. Injected aware ticks are normalized
to UTC, only minutes `00` and `30` produce the current slot, and naive values are
rejected. Scheduled work is always `SNAPSHOT` with the exact key
`schedule:snapshot:<YYYY-MM-DDTHH:MMZ>`; there is no historical catch-up.

The shared job repository now enqueues the snapshot job and appends its
`ENQUEUED` or `DEDUPLICATED` scheduler heartbeat in one PostgreSQL transaction.
Non-slot ticks append `SKIPPED_NOT_SLOT`. Enqueue/database errors attempt a
separate sanitized `FAILED` heartbeat and the CLI returns nonzero. Heartbeats
retain scheduler, commit, actor, trace, tick, slot, outcome, and canonical job
identity.

No worker, process runner, command registry, legacy research module, filesystem
fallback, timer, service, or runtime database is imported or invoked. All new
database tests provision an isolated disposable PostgreSQL cluster.

## TDD and verification

RED was observed first for the missing pure slot module and then for the missing
transactional scheduler composition boundary. Green verification covered exact
slots/keys, timezone normalization, naive rejection, no catch-up, every
heartbeat outcome, repeat and concurrent idempotency, identity persistence,
database failure, import isolation, and sanitized CLI failure.

Fresh verification before commit:

```text
uv run pytest -q tests/jobs/test_scheduler_slots.py tests/jobs/test_scheduler_repository.py
12 passed

uv run pytest -q tests/jobs
420 passed, 1 pre-existing Starlette deprecation warning

uv run python -m compileall -q services/job_scheduler services/job_store/repository.py \
  tests/jobs/test_scheduler_slots.py tests/jobs/test_scheduler_repository.py
exit 0

git diff --check
exit 0
```

## Operational boundary

Task 10 creates code and tests only. It does not install, enable, start, or
modify a systemd timer/service, and it does not run a scheduler tick against a
runtime database.
