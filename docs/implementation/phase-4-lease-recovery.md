# Phase 4 Lease, Fencing and Recovery

The production design has one worker, while database fencing remains correct
under concurrent claim tests. Claim uses `FOR UPDATE SKIP LOCKED`, ordered by
priority descending, request time ascending and job ID ascending. The claim
transaction changes `QUEUED` to `CLAIMED`, increments the attempt count,
creates the attempt, assigns a random lease owner/token/expiry and appends the
event before commit. No database transaction remains open while a child runs.

Start, heartbeat and finalization predicates require the exact current lease
token. The attempt stores PID, process group, process-start ticks and command
fingerprint. A stale worker cannot renew or finalize after losing the fence.
Cancellation is observed before spawn and throughout execution.

The runner anchors the child session, uses Linux process identity plus pidfds,
and drains bounded nonblocking pipes while renewing the lease. It does not use
bare PID/PGID signaling when identity is uncertain. Timeout, cancellation and
safety drift require conservative TERM/KILL cleanup; unproven cleanup becomes
`BLOCKED`, not success.

At worker startup, `recover_expired_leases` executes before any new claim:

- matching live child -> `BLOCKED`, no retry;
- identity mismatch or unverifiable identity -> `BLOCKED`, no blind retry;
- possible persisted result -> `BLOCKED/RESULT_RECONCILIATION_REQUIRED`;
- positively absent running child -> interrupted attempt and fixed retry only
  if attempts remain.

Recovery errors escape and stop the worker. These semantics are implemented
and tested in isolation, but no production worker or child exists and no live
lease recovery has been performed. The unresolved immutable release and
dynamic safety-evidence boundaries must be closed before the worker starts.

References: [lease ADR](../adr/ADR-phase-4-lease-and-recovery.md),
[state-machine ADR](../adr/ADR-phase-4-job-state-machine.md), and
[worker](phase-4-worker.md).
