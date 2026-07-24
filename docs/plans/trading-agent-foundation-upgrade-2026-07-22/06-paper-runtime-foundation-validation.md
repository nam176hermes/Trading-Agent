# Package 6 - Paper Runtime Foundation Validation

## Entry gate

Do not begin until:

```text
Package 1 = GO - HOST RELEASE PROOF CLOSED
Package 2 = GO - POSTGRESQL RUNTIME PARITY CLOSED
Package 4 = GO - CANONICAL PAPER RELEASE HAS NO LIVE AUTHORITY
SEC-002 = PASS - PAPER CHILD ENVIRONMENT EXCLUDES TRADING CREDENTIALS
separate Package 6 runtime Greenlight = APPROVED
```

This package validates the foundation runtime only. It does not validate alpha or authorize live trading.

## Goal

Prove one complete paper-only foundation chain from immutable artifact to durable database evidence and validated research output.

Target chain:

```text
immutable paper release
→ Job API or approved command boundary
→ single worker
→ paper-safe child environment
→ controlled SNAPSHOT
→ validated result artifact
→ append-only events
→ PostgreSQL read path
→ dashboard/read API visibility
```

## In scope

- Use approved immutable release.
- Use disposable/staging PostgreSQL proven by Package 2.
- Start a local-only Job API and one worker as tracked, bounded child processes after Greenlight.
- Use explicit ports, timeout, PID/process inventory and cleanup trap.
- Never use systemd, scheduler timers or persistent services.
- Leave scheduler timer disabled.
- Enqueue exactly one controlled SNAPSHOT with a predeclared idempotency key.
- Verify result sealing and event history.
- Verify queue returns idle.
- Verify no order/trade or live authority change.
- Exercise explicit rollback.

## Out of scope

- Automatic timer scheduling.
- Debate/replay/backtest runtime.
- Public cutover.
- Paper P&L claims.
- Model-alpha claims.
- Live trading.

## Predeclared operation

Example:

```text
idempotency_key = foundation:manual:snapshot:<approved-id>
actor = FOUNDATION_VALIDATION
job_type = SNAPSHOT
```

The operation must be bound in an approval record before process startup. Immediately before running the exact startup commands, present a Greenlight with ports, processes, paths, timeout, rollback and cleanup verification. Approval records and Greenlight serve different purposes and both are required.

## Safety preflight

Immediately before child spawn:

```text
requested_mode = paper
effective_mode = paper
all live gates = false
kill_switch = INACTIVE
release manifest valid
command manifest valid
semantic manifest fresh/valid
child environment allowlist valid
```

Any unknown value blocks the job without spawning.

## Runtime evidence

Required evidence:

- Job API listener is localhost-only.
- Worker heartbeat is current.
- One job row.
- One attempt unless a documented retry occurs.
- Append-only event chain is valid.
- Exact immutable interpreter/cwd/argv.
- Child environment excludes credentials.
- Exit code 0.
- New attributable report.
- Report schema valid.
- Result hash and sealed artifact.
- GET/read paths do not mutate.
- Queue depth returns to zero.
- No orphan child.

## Safety invariants after job

```text
mode remains paper
live gates remain false
kill switch remains INACTIVE
orders unchanged
trades unchanged
no exchange/broker/provider execution path invoked
```

Provider-free fixture SNAPSHOT is preferred. A real provider-backed research job requires separate credential and cost approval.

## Rollback

1. Stop the tracked worker after child reconciliation using the approved exact command.
2. Stop Job API.
3. Preserve jobs, events, attempts and artifact.
4. Keep timer disabled.
5. Do not restore dashboard process spawning.
6. Verify no listener and no orphan process.
7. Preserve PostgreSQL evidence.

## Acceptance

- Exactly one canonical job.
- Idempotent retry returns the same job.
- One sealed valid result.
- Complete event chain.
- Queue idle.
- Rollback pass.
- No live/trading mutation.
- Read APIs/dashboard show truthful paper/stale/fresh state.

## Stop conditions

Stop on duplicate job, unsafe child env, manifest mismatch, invalid result, orphan process, unexpected legacy write or non-paper state.

## Deliverables

```text
docs/implementation/foundation-paper-runtime-approval.md
docs/implementation/foundation-paper-job-runtime.md
docs/implementation/foundation-paper-snapshot-result.md
docs/implementation/foundation-paper-event-chain.md
docs/implementation/foundation-paper-runtime-rollback.md
docs/implementation/foundation-paper-runtime-final.md
```

## Final decision

```text
GO - PAPER FOUNDATION RUNTIME VERIFIED
```

or:

```text
NO-GO - PAPER FOUNDATION RUNTIME NOT TRUSTWORTHY
```
