# Phase 4 Known Limitations

## Designed limitations

- Production concurrency is one worker; multi-worker deployment is out of
  scope even though claim/fencing concurrency is tested.
- PostgreSQL is the only durable queue; Redis/Celery is not introduced.
- Only `SNAPSHOT` is schedulable, at UTC minute `00` and `30`.
- `DEBATE`, `REPLAY` and `BACKTEST` remain manual operator requests.
- `Persistent=false` means no production historical catch-up policy.
- Structured counters and heartbeat records exist, but a full Prometheus and
  deep operational-metrics surface is deferred.
- Research strategies, models, prompts and signal semantics are unchanged.
- Live execution, orders and broker commands remain out of scope and NO-GO.

## Current deployment blockers

- Root-owned application/backend releases and external manifests are absent;
  this session has no sudo authority to provision them.
- The backend command manifest digest is intentionally `None`, so attestation
  blocks every real spawn.
- The reviewed backend is pinned to `51de1cf06b3d595a336e19390230d0c09b608585`,
  but its external semantic-input manifest and code-pinned digest are not
  provisioned. Missing authority fails closed before research semantics load.
- No reviewed boundary yet exposes canonical dynamic `.mode` and
  `.kill_switch` evidence to the hardened worker without also exposing active
  legacy credentials/code. Copying evidence is stale; binding the whole tree
  with a finite denylist is not accepted.
- `systemd-analyze verify` reports the fixed interpreters missing until the
  immutable releases are provisioned; it must be rerun after provisioning.

Consequently Job API, worker, scheduler and timer are not running, queue depth
is zero, and no runtime heartbeat or completed Phase 4 job exists. The six
Phase 4 tables are empty. Dashboard integration remains isolated and has not
changed active port 3002 or Cloudflare.

One earlier dashboard test invocation appended 11 preserved authentication
audit events (nine `jobs.create`, two `jobs.cancel`) to the active append-only
audit file. No `run_status.json`, order or trade write accompanied them. This
is disclosed evidence, not a reason to rewrite the audit log.

Phase 4 cannot receive a runtime GO decision while these blockers remain.
Stopping here does not weaken the read-only Control API or the paper/live
safety gates.

References: [worker](phase-4-worker.md), [allowlist](phase-4-command-allowlist.md),
[dashboard integration](phase-4-dashboard-integration.md), and
[rollback](phase-4-rollback.md).
