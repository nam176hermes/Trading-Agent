# ADR: Phase 4 Command API Boundary

## Decision

Keep the existing Control API read-only. Add a separate FastAPI Job Command
API bound explicitly to `127.0.0.1:8401`. Browsers reach it only through
same-origin dashboard server routes after operator-session authorization; the
dashboard server supplies a distinct bearer token. Missing token configuration
fails authenticated operations and readiness closed.

The Job API owns enqueue, list/detail, and cancellation requests only. It does
not run a child, read exchange credentials, initialize a broker, or expose its
port through Cloudflare. Request bodies are capped at 16 KiB and authenticated
with a constant-time token comparison. Tokens and headers are excluded from
logs, responses, payloads, and database records.

The first database deployment uses a dedicated non-owner `trading_jobs` role
with no DDL, ownership, or DELETE. A later role split may narrow Job API,
worker, and scheduler grants further.

## Alternatives

- Adding POST endpoints to the Control API was rejected because it collapses
  the proven read boundary into a process-control trust domain.
- Direct browser access to port 8401 was rejected because it exposes the
  service credential and bypasses operator identity.
- Dashboard-owned database writes were rejected because they duplicate job
  invariants and transaction logic.
- Redis/Celery was rejected because PostgreSQL already supplies the required
  durable queue and transaction boundary.

## Safety impact

Compromise of browser code does not disclose the service token. Job API
failure cannot fall back to filesystem state or direct spawn. The Control API
retains no mutation or subprocess capability. The new service has no trading
credential or exchange dependency.

## Failure behavior

PostgreSQL failure makes readiness and enqueue fail with a typed unavailable
response. Missing/invalid authentication is rejected without logging token
material. Dashboard BFF failure produces an unavailable/unauthorized UI state,
never a fake success or alternate execution path.

## Rollback

Disable dashboard command controls, stop Job API/worker/scheduler, and preserve
all job data. The Control API continues unchanged. Rollback never restores
dashboard spawning or `run_status.json` operational state.
