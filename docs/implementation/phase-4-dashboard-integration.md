# Phase 4 Dashboard BFF Integration

## Isolated candidate implementation

The isolated dashboard worktree implements same-origin routes for job list,
enqueue, detail and cancel plus a compatibility `/api/trading/run` wrapper.
Each route requires the existing fail-closed operator session before the
server-side adapter calls `http://127.0.0.1:8401`. The Job API token is read
only by server code and is not returned, serialized into job payloads or
included in client bundles.

The compatibility run route no longer reads or writes `run_status.json`, runs
Python, invokes a shell or spawns a child. It returns the canonical durable job
record and preserves operator/trace attribution. Job API unavailability is a
typed unavailable result with no filesystem or process fallback. UI state
distinguishes queued, claimed, running, succeeded, blocked, failed, timed out
and cancelled rather than describing enqueue as successful execution.

The integration is present only in the isolated dashboard branch through
commit `843d449`; it has not been merged, deployed or used to cut over the
active dashboard on port 3002. The active service, port and Cloudflare route
remain unchanged, and commands must stay feature-disabled until the local Job
API boundary is provisioned and accepted.

## Preserved audit side effect

An earlier dashboard test invocation accidentally appended 11 authentication
audit events to the active append-only dashboard audit file: nine
`jobs.create` and two `jobs.cancel` events. They are preserved rather than
deleted or rewritten. Follow-up tests isolate their audit target and confirmed
that the active audit file no longer changes. The invocation did **not** write
`run_status.json`, orders or trades.

Rollback disables the dashboard command feature. It never restores detached
process spawn, Python execution or `run_status.json` as operational truth.

Final boundary hardening removes every remaining `child_process`, shell and
generic Python bridge from dashboard source. The legacy close-position,
service-control, execution, performance, performance-export and key routes
authenticate and then return typed `503 PROCESS_EXECUTION_DISABLED`; their UI
controls are disabled/read-only. No HTTP route owns a process boundary.

References: [command-boundary ADR](../adr/ADR-phase-4-command-api-boundary.md),
[pre-change checkpoint](phase-4-prechange-checkpoint.md), and
[rollback](phase-4-rollback.md).
