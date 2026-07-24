# ADR: Phase 2 Control API Boundary

Status: accepted on 2026-07-11.

## Decision

Place the read-only FastAPI application in the migration repository and configure the legacy root only through `TRADING_DATA_ROOT`. Keep JSON/JSONL/SQLite in place and wrap them with repository interfaces. The candidate dashboard consumes localhost HTTP through generated OpenAPI types and Zod validation. No public cutover occurs in Phase 2.

## Alternatives considered

1. Continue Next.js filesystem reads. Rejected because it duplicates parsing/schema rules and gives the UI knowledge of the backend layout.
2. Move data to PostgreSQL now. Rejected because storage migration, count reconciliation, and rollback are Phase 3 and would make Phase 2 too broad.
3. Import the legacy Python backend directly. Rejected because imports can initialize broker/exchange/config side effects and blur the read/execution authority boundary.

## Consequences

- Python contracts become authoritative and generated drift is testable.
- Legacy invalid records can be skipped with diagnostics without taking down the API.
- Research freshness and runtime liveness remain separate facts.
- Reads are slower than an indexed operational store because report/JSONL adapters scan files; Phase 3 addresses this.
- `CONTROL_API_ENABLED=false` remains a bounded rollback seam until PostgreSQL cutover acceptance.
