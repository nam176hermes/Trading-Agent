# Phase 2 Read-only Control API

Endpoints:

```text
GET /health/live
GET /health/ready
GET /v1/meta
GET /v1/system/status
GET /v1/market/latest
GET /v1/signals
GET /v1/decisions
GET /v1/decisions/{decision_id}
GET /v1/capabilities
GET /v1/costs
```

There are no POST, PUT, PATCH, or DELETE route handlers. Mutation smoke returns 405. Liveness does not touch legacy data. Readiness checks root existence/read access but does not require fresh research and never probes an exchange.

Each response has schema/version/trace time metadata. Middleware adds `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and `X-Trace-Id`, and emits structured request logs without request headers or secrets. CORS is allowlisted. Production error responses exclude stack traces.

The run command binds `127.0.0.1`; no systemd unit, Cloudflare route, port 3002 deployment, or runtime restart was created in Phase 2.
