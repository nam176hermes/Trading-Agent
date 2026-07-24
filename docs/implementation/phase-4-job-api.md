# Phase 4 Job Command API

## Boundary

`apps/job_api` is a separate FastAPI service with a fixed runtime bind of
`127.0.0.1:8401`. It does not add mutation endpoints to the read-only Control
API, execute subprocesses, initialize an exchange or broker, or accept trading
credentials. It is not configured for Cloudflare or direct browser access.

Implemented endpoints are:

- `GET /health/live` for process liveness only;
- `GET /health/ready` for PostgreSQL, revision `0004`, repository and token
  configuration readiness;
- authenticated `POST /v1/jobs`;
- authenticated `GET /v1/jobs` and `GET /v1/jobs/{job_id}`;
- authenticated `POST /v1/jobs/{job_id}/cancel`.

Authenticated requests use a server-side bearer token with constant-time
comparison. Each token must be configured together with an exact server-side
principal; middleware derives request attribution from that principal.
Enqueue and cancel bodies cannot supply or override an actor. Missing token or
principal configuration fails closed, request bodies are capped at 16 KiB,
payload models forbid extra fields, and request logging is metadata-only.
Tokens, raw headers and raw unbounded output are excluded from responses and
PostgreSQL.

Enqueue canonicalizes the typed payload and computes a deterministic SHA-256
fingerprint. A first request creates `QUEUED` plus its event in one transaction.
The same type, idempotency key and fingerprint returns the existing job; a
different fingerprint returns `409 IDEMPOTENCY_CONFLICT`. Cancel changes
`QUEUED` directly to `CANCELLED`, requests cooperative cancellation for
claimed/running work, and treats terminal jobs as idempotent no-ops.

## Deployment status

The database revision and empty tables exist, but the Job API has not been
installed or started. No listener has been authorized on port 8401 and no
runtime API smoke result is claimed here. The protected environment file and
immutable application release cannot be provisioned in this session because
root access is unavailable. `systemd-analyze verify` must therefore be rerun
after the fixed release interpreter exists.

References: [command API ADR](../adr/ADR-phase-4-command-api-boundary.md),
[schema](phase-4-schema.md), and [dashboard integration](phase-4-dashboard-integration.md).
