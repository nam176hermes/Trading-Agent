# Track C P10 Canonical Market-Data Pipeline Implementation Plan

> **For Hermes:** Use Codex as the single implementation worker. Hermes owns scope, safety, caller impact, RED verification, diff review, canonical gates, independent review, and commit.

**Goal:** Build a deterministic, provenance-bound, paper-only market-data pipeline that converts provider observations into canonical candle snapshots, persists them in PostgreSQL, and exposes read-only Control API data without account or execution authority.

**Architecture:** Extend the existing canonical domain, PostgreSQL operational store, snapshot job plane, and read-only Control API. Do not create a parallel market-report stack and do not import the legacy backend into the core process. P10 is split into candidate-bound vertical slices so domain semantics are proven before persistence, job, or API wiring.

**Tech Stack:** Python 3.11, Pydantic 2 strict frozen models, Decimal wire policy, PostgreSQL via psycopg/SQLAlchemy/Alembic, existing Job API contracts, FastAPI Control API, pytest, generated JSON Schema/OpenAPI.

**Pre-build assessment:** Potential 9/10, fit 10/10, existing overlap 55%. Build as a salvage extension. No new production dependency. No new UI. No runtime database mutation during source development. No exchange account, balance, position, broker, or order endpoint calls.

**Safety boundary:** Public market-data fixtures only. Keep `live_execution_approved=false` and `live_trading_approved=false`. Do not start persistent services, run migrations against a live database, change systemd/schedulers, push, deploy, or modify production configuration.

**SwarmBrief per task:**
```text
GOAL: deterministic P10 slice | SCOPE: exact listed files | DELIVERABLES: source + tests + generated contracts | PROOF: exact command
```

**Checkpoint contract:** The worker returns `STATE`, `FILES_CHANGED`, `COMMANDS_RUN`, `RESULT`, `BLOCKER`, and `NEXT_ACTION`. The worker must not commit, push, publish, deploy, restart services, mutate PostgreSQL, or invoke another agent CLI.

---

## Acceptance matrix

| ID | Requirement | Proof |
|---|---|---|
| P10-D01 | Strict canonical candle and snapshot contracts | `tests/domain/test_market_data.py` |
| P10-D02 | Safe symbol and timeframe normalization | alias, unsafe symbol, unsupported timeframe tests |
| P10-D03 | Duplicate and missing interval detection | deterministic duplicate/gap tests |
| P10-D04 | Source provenance and raw evidence digest | digest and provenance tamper tests |
| P10-D05 | Same normalized input produces same canonical bytes and digest | permutation/replay equality tests |
| P10-D06 | Strict numeric and temporal domains | bool, NaN, infinity, subnormal, negative volume, OHLC ordering, non-UTC tests |
| P10-C01 | Generated JSON Schema is deterministic and current | `make check-contracts` |
| P10-P01 | Additive PostgreSQL snapshot/candle schema with idempotency | migration contract tests and disposable PostgreSQL proof |
| P10-P02 | Repository atomically persists one snapshot and candles | repository rollback/idempotency tests |
| P10-J01 | Market-data job request is explicit and backward compatible | job contract and scheduler authority tests |
| P10-A01 | Control API is read-only and never scans legacy files for canonical candles | API repository and route tests |
| P10-S01 | No account, balance, position, broker, order, or live authority | static authority tests and paper-only gate |

## Slice 1: deterministic domain boundary

### Task 1: Add RED market-data contract tests

**Objective:** Specify strict candle, provenance, snapshot, normalization, continuity, and canonical identity behavior before implementation.

**SwarmBrief:**
```text
GOAL: New market-data tests fail only because P10 contracts do not exist.
SCOPE: tests/domain/test_market_data.py only.
DELIVERABLES: deterministic fixtures and adversarial tests for P10-D01 through P10-D06.
PROOF: uv run --frozen pytest -q tests/domain/test_market_data.py
```

**Files:**
- Create: `tests/domain/test_market_data.py`

**Required tests:**
- Normalize `btc/usdt`, `BTC-USDT`, and canonical `BTCUSDT` only through an explicit adapter-supplied alias map, never heuristic ambiguity or a duplicated global asset registry.
- Prove the same normalizer supports additional crypto and equity aliases when an adapter explicitly supplies them; unknown safe aliases remain denied.
- Reject empty, Unicode-confusable, control-character, path-like, account-like, and order-like symbols.
- Normalize closed timeframe aliases such as `1m`, `60s`, and `1h` only at the explicit adapter utility; canonical candle, continuity, and snapshot wire models accept only the six canonical enum spellings and reject alias or whitespace normalization.
- Reject bool, float, NaN, infinity, positive subnormal, negative prices/volume, non-UTC timestamps, misaligned timestamps, `high < max(open, close, low)`, and `low > min(open, close, high)`.
- Reject duplicate candle identities.
- Detect exact missing intervals without fabricating candles.
- Require bounded provider identity, observed/fetched timestamps, raw evidence SHA-256, schema version, and normalization version.
- Prove input permutation normalizes to identical canonical bytes and digest.
- Prove any candle/provenance change changes the digest.

**RED command:**
```bash
UV_CACHE_DIR=/tmp/trading-agent-track-c-p10-root-uv-cache \
  uv run --frozen pytest -q tests/domain/test_market_data.py
```

**Expected RED:** collection/import failure for not-yet-created market-data contracts.

### Task 2: Implement strict market-data contracts

**Objective:** Make Task 1 green with the smallest immutable domain implementation.

**SwarmBrief:**
```text
GOAL: Strict deterministic market-data domain boundary passes all RED tests.
SCOPE: packages/domain/market_data.py and packages/domain/__init__.py.
DELIVERABLES: closed enums, strict frozen models, normalization, continuity report, canonical bytes/digest.
PROOF: uv run --frozen pytest -q tests/domain/test_market_data.py tests/domain/test_instruments.py
```

**Files:**
- Create: `packages/domain/market_data.py`
- Modify: `packages/domain/__init__.py`
- Test: `tests/domain/test_market_data.py`

**Design:**
- Reuse `DomainModel`, `InstrumentId`, `ProductType`, `FiniteDecimal`, `CANONICAL_DECIMAL_POLICY_VERSION`, and `require_utc` patterns.
- Use a closed `MarketTimeframe` enum with exact interval seconds.
- Model `MarketCandle` with canonical string decimals and UTC-aligned open time.
- Model `MarketDataProvenance` without credentials, account routing, or free-form execution text.
- Model `MarketSnapshot` with ordered unique candles, explicit `known_at`, schema/normalization versions, raw evidence digest, canonical payload bytes, and SHA-256 identity.
- Return a typed continuity result containing duplicate identities and missing interval timestamps. Never fabricate data and never silently coerce malformed values.

**GREEN command:**
```bash
UV_CACHE_DIR=/tmp/trading-agent-track-c-p10-root-uv-cache \
  uv run --frozen pytest -q \
  tests/domain/test_market_data.py \
  tests/domain/test_instruments.py \
  tests/domain/test_clock.py
```

### Task 3: Generate and govern public schemas

**Objective:** Publish deterministic strict JSON Schema for the P10 public contracts.

**SwarmBrief:**
```text
GOAL: Generated domain schema includes market-data contracts and drift check passes.
SCOPE: generator registration, generated files, contract-generation tests.
DELIVERABLES: MarketCandle, MarketDataProvenance, MarketSnapshot, MarketContinuity schemas.
PROOF: make generate-contracts && make check-contracts
```

**Files:**
- Modify: `scripts/generate_contracts.py`
- Modify: `tests/domain/test_contract_generation.py`
- Generate: `generated/domain/json-schema/MarketCandle.json`
- Generate: `generated/domain/json-schema/MarketDataProvenance.json`
- Generate: `generated/domain/json-schema/MarketSnapshot.json`
- Generate: `generated/domain/json-schema/MarketContinuity.json`

**Verification:**
```bash
make generate-contracts
make check-contracts
UV_CACHE_DIR=/tmp/trading-agent-track-c-p10-root-uv-cache \
  uv run --frozen pytest -q tests/domain/test_contract_generation.py
```

## Slice 2: additive PostgreSQL persistence

### Task 4: Add RED migration and repository contracts

**Objective:** Specify additive market snapshot/candle persistence without touching a live database.

**Files:**
- Create: `alembic/versions/0009_canonical_market_data.py`
- Create: `services/market_data/repository.py`
- Create: `services/market_data/__init__.py`
- Create: `tests/market_data/test_migration_contract.py`
- Create: `tests/market_data/test_repository.py`
- Create: `tests/market_data/test_postgres_runtime.py`

**Required schema:**
- `market_data_snapshots`: identity, instrument, timeframe, range, known/fetched timestamps, provenance, raw evidence digest, canonical digest, versions, immutable created timestamp.
- `market_data_candles`: snapshot FK, aligned open time, exact OHLCV decimals, source sequence.
- Unique snapshot digest and unique `(snapshot_id, open_time)`.
- Check constraints for supported timeframe, nonnegative volume, and temporal ordering.
- Atomic insert, same-digest idempotency, conflicting identity rejection, rollback on any invalid candle.

**Verification:**
```bash
UV_CACHE_DIR=/tmp/trading-agent-track-c-p10-root-uv-cache \
  uv run --frozen pytest -q tests/market_data/test_migration_contract.py tests/market_data/test_repository.py
```

Runtime PostgreSQL proof remains `PENDING_APPROVAL` until a separately approved disposable instance is used.

**Slice 2 source-head boundary:** revision `0009_canonical_market_data` is the
additive Alembic source head after this slice. This does not activate it for
production: runtime release authority, deployment authority, and the Job API
expected revision remain intentionally pinned at
`0008_trading_domain_ledger` pending a separately reviewed activation. No role
grants are introduced by Slice 2.

## Slice 3: job and ingestion authority

### Task 5: Extend snapshot job contracts additively

**Objective:** Add explicit canonical market-data acquisition intent while preserving existing scheduler and worker authority.

**Files:**
- Modify: `packages/job_contracts/payloads.py`
- Modify: `packages/job_contracts/api.py`
- Modify: `packages/job_contracts/fingerprint.py` only if canonical serialization requires it
- Modify: `packages/job_contracts/__init__.py`
- Modify: `services/job_store/repository.py`
- Modify: `services/job_store/worker_repository.py`
- Test: existing `tests/jobs/*` plus new `tests/market_data/test_job_contract.py`

**Rules:**
- Existing `SnapshotPayload(scope="default", requested_as_of=None)` remains valid.
- New fields are closed, strict, bounded, and paper-only.
- Scheduler idempotency remains UTC-slot bound.
- Worker still accepts only `JobType.SNAPSHOT`.
- Provider retry metadata is declarative and bounded; no credentials in payloads.
- No account, balance, position, broker, or order endpoint authority.

## Slice 4: read-only Control API

### Task 6: Expose canonical snapshot read model

**Objective:** Add read-only canonical snapshot metadata/candles without legacy filesystem reads.

**Files:**
- Modify: `apps/control_api/control_api/contracts.py`
- Modify: `apps/control_api/control_api/app.py`
- Create: `apps/control_api/control_api/repositories/market_data.py`
- Create: `tests/control_api/test_market_data_repository.py`
- Modify: `tests/control_api/test_api.py`
- Generate: OpenAPI and dashboard types through `make generate-contracts`

**Endpoint:**
```text
GET /v1/market-data/latest?instrument=<canonical>&timeframe=<closed-enum>
```

**Rules:**
- Read-only PostgreSQL connection.
- Exact freshness and continuity status.
- Bounded page/window size.
- No legacy file scanning.
- No provider calls from request handlers.
- No API key or credential fields.

## Slice 5: closure

### Task 7: P10 candidate-bound verification

**Objective:** Close source P10 without claiming runtime PostgreSQL or production authority.

**Focused proof:**
```bash
UV_CACHE_DIR=/tmp/trading-agent-track-c-p10-root-uv-cache \
  uv run --frozen pytest -q tests/domain/test_market_data.py tests/market_data tests/control_api/test_market_data_repository.py
make check-contracts
```

**Broad proof:**
```bash
umask 0002
make audit
make check-contracts
make test-all
make build-dashboard
make ci
```

**Closure requirements:**
- Exact candidate inventory and dual identity seal.
- Independent read-only review for public contract, PostgreSQL, scheduling, and trading-safety impact.
- Explicit `source_status=PASS` only if review returns PASS with zero material findings.
- `runtime_postgres_status=PENDING_APPROVAL` until disposable runtime proof runs.
- `live_execution_approved=false`.
- `live_trading_approved=false`.
- Commit only exact reviewed paths after final identity check.

## First execution target

Implement **Slice 2 only** as the next candidate. It is source-only additive
PostgreSQL persistence with no external I/O, scheduler change, runtime mutation,
or UI. Its public persistence contract builds on Slice 1 and remains inactive
until separate Runtime Authority approval.
