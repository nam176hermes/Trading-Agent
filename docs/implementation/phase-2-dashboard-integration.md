# Phase 2 Candidate Dashboard Integration

Normal mode is `CONTROL_API_ENABLED=true`. Same-origin Next.js routes proxy typed Control API responses and do no business normalization. Server pages use the same generated, runtime-validated client. Direct filesystem code was moved to `legacy-data.ts` and is dynamically selected only when the flag is explicitly `false`.

The dashboard now uses generated OpenAPI types for market assets, decisions, freshness, and deployment identity. Zod schemas generated from the same OpenAPI validate every network success payload; invalid payloads produce `CONTRACT_VALIDATION_ERROR` with a trace ID. Connection failures produce `API_UNAVAILABLE` and never silently fall back.

Market, signals, decisions, capability, cost, and meta routes use the Control API. Memory and replay return typed `SOURCE_UNAVAILABLE` in normal Phase 2 mode because those contracts are intentionally out of scope; their old readers are available only in rollback mode. UI layout was not redesigned.

Integration coverage starts a fixture FastAPI server and candidate Next.js server, verifies STALE/UNKNOWN/total/confidence/action behavior, then stops the API and verifies `CONTROL_API_ENABLED=false` rollback against the isolated Phase 1 adapter.
