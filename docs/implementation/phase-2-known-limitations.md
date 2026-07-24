# Phase 2 Known Limitations

- Research scheduling remains absent; valid research data is stale since 2026-06-25.
- JSON/JSONL/SQLite remain legacy sources; PostgreSQL and idempotent import are Phase 3.
- Report scans and JSONL totals are not indexed and can be slower than an operational store.
- Memory/replay are not part of the Phase 2 Control API and return typed unavailable state in normal mode.
- Capability benchmark evidence remains UNKNOWN; cost is UNKNOWN or explicitly ESTIMATED.
- Prometheus/OpenTelemetry export is deferred; request logs already carry trace/route/status/duration/schema fields.
- FastAPI 0.139 emits an upstream TestClient deprecation warning recommending its future `httpx2` path; tests pass and runtime behavior is unaffected.
- The Zod generator broadens some nullable static types; the dashboard uses the stricter generated OpenAPI types and Zod only as the runtime gate.
- Candidate production dependencies retain two moderate Next.js/PostCSS audit findings already present in the Phase 1 baseline; npm proposes an unsafe breaking downgrade. The generation toolchain adds one low dev-only Babel advisory. No broad or breaking dependency change was authorized in Phase 2.
- Models remain LEGACY_UNVERIFIED; venue metadata/direct crypto execution remain disabled.
- Live trading remains NO-GO.
