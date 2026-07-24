# Phase 2 Contract-First Control API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a localhost-only, read-only FastAPI boundary over legacy trading data and make the candidate dashboard consume generated, runtime-validated contracts instead of scanning the backend filesystem.

**Architecture:** The migration repository owns strict Pydantic contracts, legacy read repositories, the FastAPI application, and deterministic OpenAPI generation. The candidate dashboard owns only a generated client/schema layer, same-origin proxy routes, UI-facing mappers, and an explicitly isolated Phase 1 fallback selected by `CONTROL_API_ENABLED=false`.

**Tech Stack:** Python 3.11, Pydantic 2, FastAPI, pytest, HTTPX, OpenAPI 3.1, TypeScript 5, Next.js 16, generated TypeScript types, generated Zod runtime schemas.

## Global Constraints

- Control API is GET/HEAD/OPTIONS only and binds to `127.0.0.1` when run.
- No subprocess, shell, exchange adapter, broker, credential loader, scheduler, PostgreSQL, Redis, Celery, or legacy write operation.
- `TRADING_DATA_ROOT` is the sole legacy-root configuration boundary.
- Expected stale/no-data/source-invalid conditions are typed data states, not fabricated health or generic HTTP 500 responses.
- Live gates, mode, kill-switch semantics, runtime services, port 3002, and Cloudflare routing remain unchanged.
- TDD is required for behavior changes; generated files are regenerated rather than hand-edited.

---

### Task 1: Python project and strict contracts

**Files:**
- Create: `pyproject.toml`
- Create: `apps/control_api/control_api/contracts.py`
- Create: `apps/control_api/control_api/config.py`
- Create: `tests/control_api/test_contracts.py`

**Interfaces:**
- Produces strict enums/models plus `ApiEnvelope[T]`, `ApiErrorEnvelope`, and `Settings.from_env()`.

- [ ] Write failing tests proving wrong scalar types, unknown enums, and out-of-range confidence are rejected while schema/envelope metadata is required.
- [ ] Run `pytest tests/control_api/test_contracts.py -q` and confirm import/model failures.
- [ ] Add pinned Python metadata and minimal strict Pydantic implementations.
- [ ] Re-run the focused test and confirm green.

### Task 2: Read-only legacy repositories

**Files:**
- Create: `apps/control_api/control_api/normalization.py`
- Create: `apps/control_api/control_api/repositories/market.py`
- Create: `apps/control_api/control_api/repositories/decisions.py`
- Create: `apps/control_api/control_api/repositories/status.py`
- Create: `apps/control_api/control_api/repositories/capabilities.py`
- Create: `apps/control_api/control_api/repositories/costs.py`
- Create: `tests/control_api/test_market_repository.py`
- Create: `tests/control_api/test_decision_repository.py`
- Create: `tests/control_api/test_status_repositories.py`

**Interfaces:**
- Consumes strict contract constructors.
- Produces `MarketRepository.latest()`, `DecisionRepository.list()/get()`, `StatusRepository.get()`, `CapabilityRepository.list()`, and `CostRepository.get()` with diagnostics.

- [ ] Write fixtures/tests for mixed reports, invalid JSON/JSONL, semantic timestamps, stable decision IDs, filters, true totals, freshness, separated liveness, and non-exact cost evidence.
- [ ] Run focused tests and confirm the missing repositories fail.
- [ ] Implement streaming/read-only normalization; derive decision IDs from stable source content and line position without rewriting JSONL.
- [ ] Re-run focused tests and confirm green.

### Task 3: Read-only FastAPI application

**Files:**
- Create: `apps/control_api/control_api/app.py`
- Create: `apps/control_api/control_api/errors.py`
- Create: `apps/control_api/control_api/middleware.py`
- Create: `apps/control_api/control_api/main.py`
- Create: `tests/control_api/test_api.py`
- Create: `tests/control_api/test_side_effects.py`

**Interfaces:**
- Produces `create_app(settings)` and the ten required GET endpoints with versioned envelopes, trace IDs, security headers, typed errors, CORS allowlist, and 405 for mutations.

- [ ] Write failing API tests for endpoints, filters, 404/422/500 envelopes, response headers, mutation rejection, liveness/readiness separation, and no-write snapshots.
- [ ] Run the focused API suite and confirm red.
- [ ] Implement the minimal app/middleware/error handlers without importing backend execution code.
- [ ] Re-run tests and confirm green.

### Task 4: Deterministic OpenAPI, JSON Schema, and TypeScript/Zod generation

**Files:**
- Create: `scripts/generate_contracts.py`
- Create: `scripts/check_generated.py`
- Create: `generated/openapi/openapi.json`
- Create: `generated/json-schema/*.json`
- Modify: `/home/thenam176/projects/trading-dashboard/package.json`
- Modify: `/home/thenam176/projects/trading-dashboard/package-lock.json`
- Create: `/home/thenam176/projects/trading-dashboard/src/generated/api-types.ts`
- Create: `/home/thenam176/projects/trading-dashboard/src/generated/api-schemas.ts`
- Create: `tests/control_api/test_generation.py`

**Interfaces:**
- Produces deterministic checked-in artifacts from `create_app()` only; dashboard code imports generated types/schemas but never edits them.

- [ ] Write a failing deterministic-generation test and generated-drift check.
- [ ] Add the minimal generator toolchain and generation scripts.
- [ ] Generate artifacts twice and compare hashes/working-tree output.
- [ ] Confirm generated TypeScript compiles and Zod schemas parse known API fixtures.

### Task 5: Candidate Control API client and isolated rollback source

**Files:**
- Move/modify: `/home/thenam176/projects/trading-dashboard/src/lib/data.ts`
- Create: `/home/thenam176/projects/trading-dashboard/src/lib/legacy-data.ts`
- Create: `/home/thenam176/projects/trading-dashboard/src/lib/control-api/client.ts`
- Create: `/home/thenam176/projects/trading-dashboard/src/lib/control-api/errors.ts`
- Create: `/home/thenam176/projects/trading-dashboard/src/lib/data-source.ts`
- Create: `/home/thenam176/projects/trading-dashboard/tests/control-api-client.test.mjs`

**Interfaces:**
- Produces one dashboard data-source interface. `CONTROL_API_ENABLED=true` uses HTTP and generated schemas; false uses the frozen Phase 1 adapter.

- [ ] Write failing client tests for valid, invalid-contract, unavailable, stale, and no-data responses plus explicit rollback selection.
- [ ] Run focused Node tests and confirm missing client/source failures.
- [ ] Implement no-store fetch, trace-preserving typed errors, schema parsing, and explicit feature-flag selection without silent fallback.
- [ ] Re-run focused tests and confirm green.

### Task 6: Same-origin routes and dashboard integration

**Files:**
- Modify: `/home/thenam176/projects/trading-dashboard/src/app/api/market/route.ts`
- Modify: `/home/thenam176/projects/trading-dashboard/src/app/api/signals/route.ts`
- Modify: `/home/thenam176/projects/trading-dashboard/src/app/api/decisions/route.ts`
- Modify: `/home/thenam176/projects/trading-dashboard/src/app/api/capability/route.ts`
- Modify: `/home/thenam176/projects/trading-dashboard/src/app/api/costs/route.ts`
- Modify: `/home/thenam176/projects/trading-dashboard/src/app/api/meta/route.ts`
- Modify relevant page/components only where the Control API envelope changes presentation mapping.
- Modify: `/home/thenam176/projects/trading-dashboard/tests/dashboard-data.integration.sh`

**Interfaces:**
- Routes proxy typed Control API responses in normal mode and delegate to isolated legacy functions only when rollback is explicitly disabled.

- [ ] Extend integration tests to start a fixture Control API, prove route payload validation, preserve STALE/UNKNOWN/count/confidence behavior, and prove normal mode does not touch the fixture filesystem.
- [ ] Run the integration test and confirm red before route changes.
- [ ] Implement minimal proxy/mapping changes, keeping the UI design unchanged.
- [ ] Re-run test, typecheck, lint, and build until green.

### Task 7: Documentation, operational run command, and rollback

**Files:**
- Create all Phase 2 deliverables under `docs/implementation`, `docs/contracts`, and `docs/adr` listed in the user specification.
- Modify: `README.md`

**Interfaces:**
- Documents exact localhost startup, environment, feature flag, contract generation, schema versioning, no-write proof, limitations, and rollback commands.

- [ ] Record architecture and decisions from implemented behavior only.
- [ ] Add exact run command binding `127.0.0.1` and candidate flag configuration without creating/restarting a service.
- [ ] Add rollback steps using `CONTROL_API_ENABLED=false`; do not use git reset on legacy repositories.
- [ ] Scan documentation for secrets, placeholders, and claims lacking evidence.

### Task 8: Full verification and completion gate

**Files:**
- Update: `docs/implementation/phase-2-test-evidence.md`
- Update: `docs/implementation/phase-2-runtime-evidence.md`
- Update: `docs/implementation/phase-2-known-limitations.md`

**Interfaces:**
- Produces the evidence-backed Phase 2 GO/NO-GO decision.

- [ ] Run all Control API tests, generated-drift checks, dashboard test/typecheck/lint/build, and Phase 1 backend safety regressions.
- [ ] Smoke all ten GET endpoints on localhost plus mutation 405 checks.
- [ ] Compare legacy file/database metadata and order/trade counts before/after GET smoke.
- [ ] Re-check service identity, paper mode, false gates, inactive kill switch, no exchange calls, and no public cutover.
- [ ] Mark GO only if every acceptance criterion has fresh evidence; otherwise record NO-GO blockers.
