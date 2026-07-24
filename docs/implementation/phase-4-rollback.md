# Phase 4 Rollback

Phase 4 rollback is a forward-safe service rollback, not a return to dashboard
process spawning.

## Procedure

1. Disable `trading-job-scheduler.timer` first.
2. Stop `trading-job-scheduler.service` if active.
3. Stop `trading-job-worker.service` and reconcile any current child/lease
   identity before a later restart.
4. Stop `trading-job-api.service`.
5. Set the dashboard Job Command feature flag off; controls become disabled or
   read-only.
6. Preserve `jobs`, attempts, append-only events, heartbeats and artifact
   references for audit.
7. Leave the read-only Control API and canonical PostgreSQL read path intact.
8. Confirm paper/paper mode, both false live gates, canonical kill-switch
   state, orders/trades, active agent/dashboard PIDs, port 3002 and Cloudflare.

Do not delete job rows, rewrite events, downgrade revision `0004`, remove
artifacts, reset dirty legacy repositories or modify legacy sources during
normal rollback. A schema downgrade requires a separate maintenance approval
and backup because it drops audit-bearing Phase 4 tables.

Rollback must never restore `/api/trading/run` process spawning, shell/Python
execution, filesystem fallback or `run_status.json` reads/writes. If the Job
API is unavailable, the correct dashboard state is unavailable/disabled.

At the current checkpoint no Phase 4 service or timer has started, so runtime
rollback is presently a no-op. The database remains at `0004` with six empty
job tables and the isolated dashboard remains undeployed.

References: [pre-change checkpoint](phase-4-prechange-checkpoint.md),
[command-boundary ADR](../adr/ADR-phase-4-command-api-boundary.md), and
[scheduler](phase-4-scheduler.md).
