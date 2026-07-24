# Codex prompt - Phase 2 contract-first Control API

Use only after Phase 1 acceptance.

Create strict Pydantic contracts for Asset, MarketReport, Signal,
DecisionRecord, SystemStatus, CapabilityEvidence, and JobRecord. Generate JSON
Schema/OpenAPI and frontend TypeScript types, while retaining runtime validation
of network responses.

Build an initially read-only FastAPI Control API:

```text
GET /v1/meta
GET /v1/system/status
GET /v1/market/latest
GET /v1/signals
GET /v1/decisions
GET /v1/decisions/{id}
GET /v1/capabilities
GET /v1/costs
```

Responses include schema version, trace ID, generation time, and applicable
freshness metadata. Put legacy JSON/JSONL access behind backend adapters; the
dashboard must not scan the legacy filesystem. Add correct count and pagination.

Switch the dashboard through a rollback feature flag. Keep command APIs,
PostgreSQL, queues, and execution outside this phase. Add contract, API
integration, frontend contract, rollback, and migration-note evidence.
