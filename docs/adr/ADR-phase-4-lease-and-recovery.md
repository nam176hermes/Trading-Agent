# ADR: Phase 4 Lease, Fencing, and Recovery

## Decision

Run one production worker. Claim one eligible job with `FOR UPDATE SKIP LOCKED`
ordered by priority descending, requested time ascending, then job ID. In the
claim transaction, move to `CLAIMED`, increment attempts, create an attempt,
assign a random lease token/owner/expiry, and append the event; close the
transaction before spawning.

Heartbeat and finalization predicates require the current lease token. The
worker records child PID, process group, Linux process-start ticks, and command
fingerprint. A fenced pre-spawn control check observes cancellation while the
attempt is still `CLAIMED`. Heartbeats renew both job and attempt and inspect
cancellation and safety through execution and bounded output drain.

The runner drains nonblocking pipes in the heartbeat loop. It observes leader
exit with Linux `waitid(..., WNOWAIT)` and deliberately retains the zombie as a
PID/session allocation anchor. Pipe EOF is not containment evidence; an
injectable Linux `/proc` inspector snapshots every live non-zombie member of
the anchored session, including members that moved to alternate process
groups. Each member snapshot includes PID, PGID, session ID, and process-start
ticks. The runner opens a pidfd, revalidates that evidence after acquisition,
and signals only the exact pidfd; bare PGID signaling is forbidden. Cleanup
requires consecutive empty session snapshots. Only after that proof and
bounded pipe drain does the runner wait and reap the leader.

The leader pidfd is opened and revalidated immediately after post-Popen
identity validation, before selector setup, the first repository heartbeat, or
`start_attempt`. It remains in the shared cleanup state independently of later
session scans. A failed scan or omission cannot hide that retained member or
advance empty proof; normal, exception, and final cleanup still drive its exact
pidfd through TERM/KILL and then reap the leader.

Leader revalidation accepts a matching zombie because WNOWAIT intentionally
retains that state as the allocation anchor; general session scans still omit
zombies. A close-on-exec descriptor reserved before spawn is released for the
leader pidfd acquisition. If acquisition fails, no PID-based signal fallback
is safe: the worker performs only bounded reap observation and blocks with
cleanup unproven. If acquisition succeeds but identity revalidation fails, it
signals only the exact opened pidfd, closes it, performs bounded reap
observation, and still blocks with cleanup unproven.

An expired lease is never blindly requeued. Recovery compares the stored
identity with `/proc`. A matching live child produces `BLOCKED` with
`LEASE_EXPIRED_CHILD_STILL_RUNNING`. A positively absent or identity-mismatched
child marks the attempt interrupted and may apply the fixed retry policy. If
result creation may have completed but persistence did not, use `BLOCKED` with
`RESULT_RECONCILIATION_REQUIRED`.

## Alternatives

- Holding a database transaction while the child runs was rejected because it
  creates long locks and fragile recovery.
- PID-only recovery was rejected because PID reuse can misidentify a process.
- Immediate lease-expiry requeue was rejected because it can launch duplicate
  research children.
- Multi-worker production was deferred; two-transaction claim tests remain
  required to prove fencing.

## Safety impact

Token fencing prevents stale workers from completing reassigned jobs. Process
identity checks and conservative blocking prevent duplicate execution.
Heartbeat-time mode, live-gate, and kill-switch checks terminate work when the
runtime becomes unsafe.

## Failure behavior

Wrong-token renew/finalize updates zero rows and records no false success. An
unverifiable child remains blocked for operator review. Cancellation sends
SIGTERM to the exact process group, waits a fixed grace period, then SIGKILL.
Cancellation before start finalizes the `CLAIMED` attempt without spawning;
cancellation after leader exit remains observable during drain. Unproven
identity, group cleanup, or reaping cannot become `CANCELLED` success.
Creating a new session with `setsid` is outside the allowed command contract.
An observable session escape or any containment ambiguity is cleanup-unproven
and blocks finalization; Phase 4 does not claim containment beyond its original
spawned session.

## Rollback

Stop the worker after disabling the timer. Do not reassign leases during
rollback. Preserve attempts/events and reconcile any live child identity
before future restart.
