# Phase 2 Test Evidence

Evidence date: 2026-07-11 America/Toronto. Tests use fixture data or read-only real-data access and no production credential.

| Repo | Command | Result |
|---|---|---|
| Migration | `uv run pytest -q` | PASS; 22 contract, adapter, API, generation, and no-side-effect tests |
| Migration | `uv run python scripts/generate_contracts.py --check` | PASS; deterministic OpenAPI/JSON Schema/TypeScript/Zod artifacts |
| Candidate | `npm test` | PASS; 3 client tests plus Control API and rollback E2E smoke |
| Candidate | `npx tsc --noEmit` | PASS |
| Candidate | `npm run lint` | PASS; zero errors |
| Candidate | `npm run build` | PASS; Next.js 16.2.6 |
| Real-data smoke | ten required GETs, known decision detail, POST/PUT checks | PASS; GET 200, mutations 405 |
| Backend | `.venv/bin/python tests/test_integration.py` | PASS; 43/43 standalone integration checks |
| Backend | `.venv/bin/python -m pytest -q -s tests/test_broker.py tests/test_paper_trader.py tests/test_phase1_safety.py tests/test_live_execution_policy.py tests/test_asset_registry.py` | PASS; 85 passed, 2 connectivity tests intentionally skipped |
| Backend | `.venv/bin/python scripts/verify_phase1_environment.py` | PASS; Python 3.11.15 and 16 direct imports |
| Candidate | `npm audit --omit=dev` | KNOWN; two moderate Next.js/PostCSS advisories remain, force fix proposes an invalid breaking downgrade |
| Candidate | `npm audit` | KNOWN; the same production findings plus one low dev-tool Babel advisory |

Initial TDD evidence included missing-contract imports, missing repositories, missing FastAPI app, generated drift, missing client, invalid effective-capability semantics, and fresh-health mapping failures. Each was observed failing before its minimal implementation passed.

The two skipped tests require real Alpaca connectivity and remain outside the paper-only Phase 2 boundary. The FastAPI suite emits one upstream TestClient deprecation warning; it is tracked in known limitations.
