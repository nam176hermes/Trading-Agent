# Trading Agent V2 Implementation Roadmap

> **For Hermes:** Use the `subagent-driven-development` skill to implement this plan task by task.

**Goal:** Move the current paper-only Trading Agent from a recovered control plane and durable research job foundation to a deterministic, replayable paper trading platform with point-in-time data, target portfolios, a risk gateway, an OMS, reconciliation, and constrained AI research.

**Architecture:** Preserve the canonical monorepo and strangler boundary. Keep the legacy research backend isolated. Add a modular trading domain inside the root Python component, with PostgreSQL as durable operational truth and the existing dashboard as a projection and control surface. One trading node owns risk, order state, paper execution, and reconciliation. Research and LLM processes can only produce versioned proposals.

**Tech Stack:** Python 3.11, Pydantic 2, PostgreSQL 16, Alembic, psycopg 3, FastAPI, pytest, Next.js 16, TypeScript, existing immutable release tooling, Parquet-compatible dataset manifests, paper simulator first.

**Safety posture:** PAPER only. Keep `LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false`. No broker, exchange, account, balance, position, order, or credential call is authorized by this roadmap.

**SwarmBrief per task:**

```text
GOAL: end state | SCOPE: included and excluded work | DELIVERABLES: exact paths | PROOF: exact command
```

**Checkpoint contract:** Every worker returns `STATE`, `FILES_CHANGED`, `COMMANDS_RUN`, `RESULT`, `BLOCKER`, and `NEXT_ACTION`. A checkpoint must include test output or an exact artifact identity.

---

## 1. Current-state decision

### What already exists

| Capability | Current evidence | Decision |
|---|---|---|
| Canonical monorepo | Root `README.md`, `AGENTS.md`, commit `e2aca4b` | Preserve |
| Paper-only safety | `services/job_worker/safety.py`, production baseline | Preserve and extend |
| Read-only Control API | `apps/control_api/control_api/` | Reuse for projections |
| PostgreSQL operational schema | Alembic `0001` to `0005` on canonical branch | Extend after authority branch decision |
| Durable jobs | `packages/job_contracts/`, `services/job_*` | Reuse for research and batch jobs |
| Source and release authority | `packages/runtime_release/`, job-plane branches | Close blockers before runtime activation |
| Legacy research backend | `legacy/research-backend/` | Keep isolated behind contracts |
| Legacy risk, backtest, paper and execution code | `risk_engine.py`, `backtest_engine.py`, `paper_trader.py`, `exchange/` | Treat as untrusted reference, not canonical authority |
| Dashboard | `apps/dashboard/` | Reuse as projection; never make UI the risk authority |

### Gaps that block the target architecture

1. The canonical branch has no `SignalProposal`, `TargetPortfolio`, canonical `RiskDecision`, `SignedOrderPlan`, `OrderIntent`, `OrderEvent`, or `ExecutionReport` domain types.
2. There is no one canonical event envelope or deterministic trading replay.
3. Existing legacy decisions use floats and prose-heavy structures. They are not safe execution contracts.
4. Portfolio construction, risk, order lifecycle, fill accounting, and reconciliation remain coupled or legacy-only.
5. Job-plane authority work is split across branches and worktrees. Runtime PostgreSQL and hermetic release blockers remain open.
6. No paper execution path proves that backtest, replay, and paper share the same order state machine.
7. Model and LLM output are not yet governed by a production promotion registry.

### Required contract chain

```text
ResearchPacket
  -> SignalProposal
  -> TargetPortfolio
  -> RiskDecision
  -> SignedOrderPlan
  -> OrderIntent
  -> OrderEvent and FillEvent
  -> ExecutionReport
  -> PortfolioSnapshot
```

`SignedOrderPlan` is the short-lived authorization envelope over one or more `OrderIntent` records. It preserves the prior upgrade-plan security requirement without allowing an LLM or strategy to become order authority.

---

## 2. Program gates

| Gate | Meaning | Promotion rule |
|---|---|---|
| AUTH0 | Canonical source and migration authority are unambiguous | Required before new schema work |
| AUTH1 | Hermetic paper release can be built and verified | Required before runtime activation |
| G0 | Code, contracts, locks, and secret scans pass | Required for every phase |
| G1 | Point-in-time data and lineage pass | Required before strategy evaluation |
| G2 | Research utility beats predefined baselines after costs | Required before model promotion |
| G3 | Replay, stress, lookahead, and failure tests pass | Required before paper production |
| G4 | Paper operation has no unresolved policy or reconciliation breach | Required before any future live ADR |

Passing G4 does not authorize live trading.

---

## 3. Phase R0: Close source authority and release blockers

**Priority:** P0
**Outcome:** One reviewed canonical source line and a buildable paper-only release foundation.

### Task R0.1: Classify and transfer the job-plane authority branch

**Objective:** Decide which commits from `codex/job-plane-authority-v4` become canonical without importing stale or runtime-specific evidence blindly.

**SwarmBrief:**

```text
GOAL: produce one reviewed transfer set from job-plane-authority-v4
SCOPE: source, tests, migrations, contracts, docs; no runtime or PostgreSQL mutation
DELIVERABLES: docs/implementation/job-plane-v4-transfer-review.md and reviewed commits
PROOF: make audit && make check-contracts && make test-all
```

**Files:**
- Review: `docs/implementation/job-plane-v4-transfer-review.md`
- Review: `docs/implementation/job-plane-v4-transfer-manifest.csv`
- Review: `alembic/versions/0006_job_transition_database_authority.py`
- Review: `alembic/versions/0007_job_event_chain_authority.py`
- Test: `tests/jobs/test_job_transition_authority.py`
- Test: `tests/jobs/test_job_event_chain_authority.py`

**Steps:**
1. Freeze both branch heads and generate a path-level transfer inventory.
2. Reject generated evidence whose identities bind a different worktree or commit.
3. Transfer source and tests in dependency order: database authority, verifier, repository, worker, dashboard, release tooling.
4. Run focused RED/GREEN tests after every transfer group.
5. Do not merge, push, migrate, or activate runtime without separate approval.

### Task R0.2: Close the hermetic Python release blocker

**Objective:** Produce a relocatable Python 3.11 paper release without host-path escape.

**Files:**
- Modify: `ops/release-v2/build-stage.sh`
- Modify: `ops/release-v2/verify-stage.py`
- Modify: `packages/runtime_release/v2.py`
- Modify: `tests/runtime_release/test_v2.py`
- Modify: `tests/runtime_release/test_v2_provisioning.py`
- Create: `docs/adr/ADR-hermetic-python-runtime-authority.md`

**Required design:**
- Pin a complete CPython 3.11 runtime archive and checksum.
- Pin a wheel-only dependency and build-tool closure.
- Reject `.pth`, shebang, `pyvenv.cfg`, symlink, hardlink, RPATH, or `sys.path` escape.
- Build twice with network disabled and compare complete manifests.
- Relocate the artifact and rerun import and startup smoke checks.

**Proof:**

```bash
uv run pytest -q tests/runtime_release
make audit-release
```

**Exit criteria:** Two independent builds are byte-identical where required, both pass external verification, and neither references the operator-owned Python installation.

### Task R0.3: Keep runtime recovery and migration approval separate

**Objective:** Preserve the existing PostgreSQL recovery gate instead of hiding it inside feature work.

**Files:**
- Review: `docs/production/runbooks/postgresql-preserve-recover.md`
- Review: `docs/production/runbooks/job-plane-role-split-rollout.md`
- Review: `ops/postgres/postgres-recovery-approval-record.schema.json`

**Gate:** Any start, stop, backup, restore, migration, role change, service change, or production database write requires a new Greenlight approval with exact rollback and evidence. Source development can continue without executing this gate.

---

## 4. Phase D0: Canonical domain and deterministic replay

**Priority:** P0
**Outcome:** Stable types and replay semantics shared by backtest, paper, and future adapters.

### Task D0.1: Add fixed-precision primitives and identifiers

**Files:**
- Create: `packages/domain/primitives.py`
- Create: `packages/domain/instruments.py`
- Create: `packages/domain/clock.py`
- Create: `packages/domain/__init__.py`
- Test: `tests/domain/test_primitives.py`
- Test: `tests/domain/test_instruments.py`
- Test: `tests/domain/test_clock.py`

**Types:**

```python
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    precision: int

@dataclass(frozen=True, slots=True)
class InstrumentId:
    symbol: str
    product_type: str
    venue: str
```

**TDD requirements:** Reject floats, invalid precision, unknown currencies, malformed venue-qualified instruments, and non-UTC timestamps. Add boundary tests for tick, lot, minimum notional, zero, and negative values.

### Task D0.2: Define the event envelope and contracts

**Files:**
- Create: `packages/domain/events.py`
- Create: `packages/domain/signals.py`
- Create: `packages/domain/portfolio.py`
- Create: `packages/domain/risk.py`
- Create: `packages/domain/orders.py`
- Modify: `scripts/generate_contracts.py`
- Create: `generated/domain/json-schema/`
- Test: `tests/domain/test_event_contracts.py`
- Test: `tests/domain/test_contract_generation.py`

**Invariants:**
- `event_id` is unique.
- `sequence` is monotonic per stream.
- The envelope carries `event_id`, `event_type`, `schema_version`, `source`, `stream_id`, `sequence`, `observed_at`, `ingested_at`, `produced_at`, `effective_at`, `expires_at`, `correlation_id`, `causation_id`, `trace_id`, and a typed payload.
- Event, ingest, production, effective, and expiry timestamps have distinct semantics.
- Models are strict and forbid unknown fields.
- Every signal has cutoff, expiry, evidence, model, strategy, and schema versions.
- No signal contains credentials, account routing, order type, or arbitrary execution text.
- Every risk decision records original target, approved target, reason codes, policy version, and state snapshot.

### Task D0.3: Build an append-only event ledger and replay reducer

**Files:**
- Create: `packages/event_ledger/models.py`
- Create: `packages/event_ledger/reducer.py`
- Create: `packages/event_ledger/repository.py`
- Create: `packages/event_ledger/replay.py`
- Test: `tests/event_ledger/test_reducer.py`
- Test: `tests/event_ledger/test_replay.py`
- Create after AUTH0: `alembic/versions/0008_trading_domain_ledger.py`

**Schema:**
- `domain_events`
- `event_outbox`
- `consumer_inbox`
- `stream_snapshots`

**Proof:** Replaying the same immutable event set produces the same state hash. Duplicates are ignored by identity. Out-of-order and sequence-gap cases fail closed or enter a typed degraded state.

**Phase exit:** Contract generation, property tests, and one end-to-end deterministic replay scenario pass.

---

## 5. Phase D1: Point-in-time data and baseline research

**Priority:** P0
**Outcome:** One reproducible, non-LLM baseline from immutable data through cost-aware backtest.

### Task D1.1: Normalize canonical market events

**Files:**
- Create: `packages/market_data/events.py`
- Create: `packages/market_data/normalizer.py`
- Create: `packages/market_data/quality.py`
- Create: `packages/market_data/bitemporal.py`
- Test: `tests/market_data/test_events.py`
- Test: `tests/market_data/test_normalizer.py`
- Test: `tests/market_data/test_quality.py`
- Test: `tests/market_data/test_bitemporal.py`

**Required semantics:** Preserve source payload hash, event time, ingest time, receive time, sequence, venue, instrument, source, schema version, and quality flags. Quarantine malformed, duplicate, stale, crossed, impossible, and out-of-order events. Never silently rewrite source history.

### Task D1.2: Define dataset and run manifests

**Files:**
- Create: `packages/data_catalog/manifests.py`
- Create: `packages/data_catalog/checksums.py`
- Create: `packages/data_catalog/point_in_time.py`
- Test: `tests/data_catalog/test_manifests.py`
- Test: `tests/data_catalog/test_point_in_time.py`
- Create: `docs/adr/ADR-point-in-time-data-contract.md`

**Required fields:** Dataset ID, cutoff, universe rule and version, source partitions and checksums, transform versions, missing count, duplicate count, out-of-order count, schema version, code commit, config, seed, and environment identity.

### Task D1.3: Add one feature definition path with offline and online parity

**Files:**
- Create: `packages/features/definitions.py`
- Create: `packages/features/offline.py`
- Create: `packages/features/online.py`
- Create: `packages/features/lineage.py`
- Test: `tests/features/test_offline_online_parity.py`
- Test: `tests/features/test_cutoff_enforcement.py`
- Test: `tests/features/test_lineage.py`

**Invariant:** One feature definition produces equivalent offline and online values from the same as-of inputs. Every feature vector carries definition version, data cutoff, dataset lineage, and freshness.

### Task D1.4: Wrap the legacy backtest behind a canonical simulator port

**Files:**
- Create: `packages/execution/ports.py`
- Create: `packages/execution/simulator.py`
- Create: `legacy/research-backend/canonical_backtest_adapter.py`
- Test: `tests/execution/test_simulator_contract.py`
- Test: `legacy/research-backend/tests/test_canonical_backtest_adapter.py`

**Boundary rule:** Root core does not import the flat legacy backend in process. Integration uses a versioned artifact, subprocess, or durable job contract.

**Reality models:** Fees, spread, slippage, latency, partial fills, rejected orders, min quantity, min notional, funding, and restart during partial fill.

### Task D1.5: Establish predefined baselines and leakage tests

**Files:**
- Create: `tests/research/test_no_lookahead.py`
- Create: `tests/research/test_cost_sensitivity.py`
- Create: `tests/research/test_baselines.py`
- Create: `docs/research/baseline-protocol.md`

**Baselines:** Cash, buy-and-hold where applicable, equal weight, and one deterministic rule-based strategy.

**Phase exit:** A run can be reproduced from commit plus manifests, feature parity passes, no future-known data is used, and sensitivity to costs and fill assumptions is reported.

---

## 6. Phase P0: Portfolio construction V0

**Priority:** P0
**Outcome:** Signals become bounded target weights, never direct orders.

### Task P0.1: Normalize and calibrate proposals

**Files:**
- Create: `packages/portfolio/calibration.py`
- Create: `packages/portfolio/fusion.py`
- Test: `tests/portfolio/test_calibration.py`
- Test: `tests/portfolio/test_fusion.py`

**Contract output:** Expected return, expected risk, calibrated confidence, horizon, capacity, expiry, evidence family, and model lineage.

**Rules:**
- Self-reported LLM confidence is not a probability.
- Correlated signals are grouped before weighting.
- Different horizons are not averaged blindly.
- Missing calibration shrinks a signal toward zero.

### Task P0.2: Implement rule-based target sizing

**Files:**
- Create: `packages/portfolio/constructor.py`
- Create: `packages/portfolio/policies.py`
- Test: `tests/portfolio/test_constructor.py`
- Test: `tests/portfolio/test_properties.py`

**V0 policy:** Volatility-scaled sizing, max weight, gross and net bounds, turnover cap, cash convention, no-trade band, minimum trade notional, and uncertainty penalty.

**Property tests:** Weights satisfy constraints, output is deterministic, infeasible inputs fail safely, missing cost or covariance never silently becomes zero, and small input changes do not create unreasonable discontinuities.

**Phase exit:** A `TargetPortfolio` has numerical attribution to signal families, risk penalties, costs, and constraints.

---

## 7. Phase R1: Deterministic Risk Gateway

**Priority:** P0
**Outcome:** A fail-closed firewall between target portfolios and execution.

### Task R1.1: Define versioned risk policy and state

**Files:**
- Create: `packages/risk_gateway/policy.py`
- Create: `packages/risk_gateway/snapshot.py`
- Create: `packages/risk_gateway/state_machine.py`
- Test: `tests/risk_gateway/test_policy.py`
- Test: `tests/risk_gateway/test_state_machine.py`
- Create after AUTH0: `alembic/versions/0009_risk_policy_and_reservations.py`

**States:** `STARTING`, `ACTIVE`, `REDUCING`, `HALTED`.

**Invariant:** Cancel and reconciliation remain available while halted. Increasing exposure is denied outside `ACTIVE`.

**Risk snapshot:** Positions, balances, active orders, open reservations, mark and FX prices, data age, venue health, realized and unrealized P&L, drawdown, and loss counters must be bound to one versioned decision snapshot.

### Task R1.2: Implement ordered checks and reason codes

**Files:**
- Create: `packages/risk_gateway/checks.py`
- Create: `packages/risk_gateway/engine.py`
- Create: `packages/risk_gateway/reason_codes.py`
- Test: `tests/risk_gateway/test_checks.py`
- Test: `tests/risk_gateway/test_engine.py`
- Test: `tests/risk_gateway/test_reason_codes.py`

**Check order:** Schema and authorization, data freshness, instrument capability, reconciliation health, order limits, position limits, portfolio limits, loss limits, liquidity limits, operational health, then approve, clip, reject, or halt.

**Required reason codes:** `DATA_STALE`, `SIGNAL_EXPIRED`, `MODEL_NOT_APPROVED`, `PRICE_OUTSIDE_COLLAR`, `ORDER_NOTIONAL_LIMIT`, `GROSS_EXPOSURE_LIMIT`, `MARGIN_BUFFER_LIMIT`, `DAILY_LOSS_LIMIT`, `VENUE_DEGRADED`, `DUPLICATE_COMMAND`, and `GLOBAL_HALT`.

### Task R1.3: Add atomic risk reservations

**Files:**
- Create: `packages/risk_gateway/reservations.py`
- Modify: `packages/event_ledger/repository.py`
- Test: `tests/risk_gateway/test_reservations.py`
- Test: `tests/risk_gateway/test_concurrency.py`

**Flow:** Check, reserve exposure or buying power, persist decision and outbox atomically, issue short-lived signed approval, consume once, then release or adjust on reject, cancel, expiry, or fill.

### Task R1.4: Implement hierarchical kill switches

**Files:**
- Create: `packages/risk_gateway/kill_switches.py`
- Extend: `packages/safety_evidence.py`
- Test: `tests/risk_gateway/test_kill_switches.py`
- Test: `tests/risk_gateway/test_kill_switch_drills.py`

**Scopes:** Strategy, instrument, venue, account, asset class, global.

**Phase exit:** Boundary, concurrency, stale-data, drawdown, reservation, override, and kill-switch drill tests pass. The engine has no LLM dependency.

---

## 8. Phase O0: Paper OMS and reconciliation

**Priority:** P0
**Outcome:** One order authority with restart-safe paper order accounting.

### Task O0.1: Implement the order aggregate and state reducer

**Files:**
- Create: `packages/oms/aggregate.py`
- Create: `packages/oms/state_machine.py`
- Create: `packages/oms/commands.py`
- Test: `tests/oms/test_state_machine.py`
- Test: `tests/oms/test_aggregate.py`

**States:** `INITIALIZED`, `SUBMITTED`, `ACCEPTED`, `PARTIALLY_FILLED`, `PENDING_UPDATE`, `PENDING_CANCEL`, `FILLED`, `CANCELED`, `REJECTED`, `EXPIRED`, and `UNKNOWN`.

**Hierarchy:** A parent order intent stores the portfolio objective. Child orders store execution tactics. Changing slicing or timing must not rewrite the parent objective or alpha lineage.

**Tests:** Fill before ACK, fill during cancel, timeout then late ACK, duplicate report, partial-fill sums, invalid transition, and restart at every state.

### Task O0.2: Add idempotent commands, outbox, and inbox

**Files:**
- Create: `packages/oms/service.py`
- Create: `packages/oms/repository.py`
- Test: `tests/oms/test_idempotency.py`
- Test: `tests/oms/test_outbox_inbox.py`
- Create after AUTH0: `alembic/versions/0010_oms_ledger.py`

**Schema:** Orders, order events, fills, execution reports, client ID reservations, raw adapter reports, outbox, inbox, and reconciliation findings.

**Invariant:** A retry of the same economic intent reuses the same client order ID. A network timeout never causes blind resubmission.

### Task O0.3: Implement the paper adapter and execution simulator

**Files:**
- Create: `packages/execution/paper_adapter.py`
- Create: `packages/execution/fill_models.py`
- Create: `packages/execution/error_taxonomy.py`
- Test: `tests/execution/test_paper_adapter.py`
- Test: `tests/execution/test_fill_models.py`
- Test: `tests/execution/test_error_taxonomy.py`

**Canonical errors:** Authentication, permission, rate limit, timeout, disconnect, invalid instrument, invalid price, invalid quantity, insufficient balance, order not found, duplicate client ID, halt, venue error, and unknown outcome.

### Task O0.4: Implement reconciliation and ledger projections

**Files:**
- Create: `services/reconciliation/service.py`
- Create: `services/reconciliation/policies.py`
- Create: `packages/ledger/projections.py`
- Create: `packages/ledger/accounting.py`
- Test: `tests/reconciliation/test_startup.py`
- Test: `tests/reconciliation/test_continuous.py`
- Test: `tests/ledger/test_accounting.py`

**Reconciliation modes:** Startup full, periodic incremental, event-triggered after unknown state, end-of-session snapshot, and manual on-demand.

**Conflict policy:** Mark degraded, block new risk, obtain authoritative snapshots, emit adjustment events, and resume only after invariants pass. Never rewrite historical events.

**Phase exit:** Duplicate command, timeout, partial fill, cancel race, unknown remote order, position mismatch, crash recovery, and full replay tests pass.

---

## 9. Phase T0: One paper trading node and observability

**Priority:** P1
**Outcome:** Backtest, replay, and paper use the same domain and OMS path.

### Task T0.1: Add the paper trading composition root

**Files:**
- Create: `apps/trading_node/__init__.py`
- Create: `apps/trading_node/config.py`
- Create: `apps/trading_node/app.py`
- Create: `apps/trading_node/main.py`
- Test: `tests/trading_node/test_composition.py`
- Test: `tests/trading_node/test_paper_flow.py`

**Pipeline:** Market snapshot, approved inference, signal proposal, target portfolio, risk decision, signed plan, OMS, paper adapter, reconciliation, ledger, and audit.

**Boundary:** The trading node cannot import or execute arbitrary research code. It accepts only approved immutable artifacts and strict contracts.

### Task T0.2: Add telemetry and end-to-end trace identity

**Files:**
- Create: `packages/observability/events.py`
- Create: `packages/observability/metrics.py`
- Create: `packages/observability/tracing.py`
- Test: `tests/observability/test_trace_lineage.py`

**Metrics:** Data age, queue lag, signal expiry, risk clip and reject rate, order ACK latency, fill ratio, reject rate, reconciliation mismatch, exposure, turnover, P&L, and model or agent schema failure.

### Task T0.3: Extend Control API and dashboard projections

**Files:**
- Create: `apps/control_api/control_api/repositories/risk.py`
- Create: `apps/control_api/control_api/repositories/orders.py`
- Create: `apps/control_api/control_api/repositories/reconciliation.py`
- Modify: `apps/control_api/control_api/contracts.py`
- Modify: `apps/control_api/control_api/app.py`
- Create: `apps/dashboard/src/lib/trading/risk.ts`
- Create: `apps/dashboard/src/lib/trading/orders.ts`
- Modify: `apps/dashboard/src/app/dashboard/execution/page.tsx`
- Test: `tests/control_api/test_risk_api.py`
- Test: `tests/control_api/test_orders_api.py`
- Test: `apps/dashboard/tests/trading-risk.test.mjs`

**UI rule:** Show an explicit PAPER mode banner, freshness, risk state, limit utilization, active reservations, orders, fills, reconciliation, and kill-switch state. The dashboard is never enforcement authority.

### Task T0.4: Add SLOs, incident runbooks, and paper failure drills

**Files:**
- Create: `docs/production/trading-node-slos.md`
- Create: `docs/production/runbooks/trading-node-incident-response.md`
- Create: `docs/production/runbooks/trading-node-paper-recovery.md`
- Create: `tests/chaos/test_trading_node_failures.py`
- Create: `tests/security/test_trading_node_boundaries.py`

**Drills:** Stale market data, queue lag, database disconnect, adapter disconnect, dropped ACK, duplicate fill, process restart during partial fill, reconciliation mismatch, disk pressure, expired model, and global halt. Each drill must record detection, automated action, operator action, recovery evidence, and rollback.

**Security checks:** Secret scan, dependency and artifact inventory, no credentials in prompts or logs, read-only research permissions, signed approval replay rejection, immutable audit retention, and least-privilege database roles.

**Phase exit:** One deterministic scenario traces from market snapshot through paper fill and reconciled portfolio with stable correlation IDs. SLO and failure-drill evidence is current.

---

## 10. Phase M0: Model and experiment governance

**Priority:** P1
**Start condition:** G1 and paper domain path are stable.

### Task M0.1: Add experiment and model registries

**Files:**
- Create: `packages/model_registry/models.py`
- Create: `packages/model_registry/repository.py`
- Create: `packages/model_registry/promotion.py`
- Test: `tests/model_registry/test_lifecycle.py`
- Test: `tests/model_registry/test_promotion.py`
- Create after AUTH0: `alembic/versions/0011_model_registry.py`

**Lifecycle:** `REGISTERED`, `VALIDATED`, `APPROVED`, `SHADOW`, `CANARY`, `ACTIVE`, `REJECTED`, `RETIRED`.

**Registry fields:** Artifact hash, code and data lineage, metrics, universe, horizon, feature schema, calibration, expiry, owner, approval, and rollback target.

### Task M0.2: Add walk-forward, purging, Monte Carlo, drift, and expiry gates

**Files:**
- Create: `packages/research_validation/walk_forward.py`
- Create: `packages/research_validation/stress.py`
- Create: `packages/research_validation/drift.py`
- Test: `tests/research_validation/test_walk_forward.py`
- Test: `tests/research_validation/test_stress.py`
- Test: `tests/research_validation/test_drift.py`

**Phase exit:** The model is reproducible, beats a predefined baseline after costs, can roll back, and stops producing proposals after expiry or critical drift.

---

## 11. Phase AI0: Constrained AI research

**Priority:** P2
**Start condition:** Non-agent baseline, schema contracts, and model governance are working.

### Task AI0.1: Convert legacy research output into `ResearchPacket`

**Files:**
- Create: `legacy/research-backend/research_packet_export.py`
- Create: `packages/research_contracts/packets.py`
- Test: `legacy/research-backend/tests/test_research_packet_export.py`
- Test: `tests/research_contracts/test_packets.py`

**Packet fields:** Cutoff, horizon, claims, evidence references, uncertainty, prompt hash, model and provider identity, trace, expiry, and prompt-injection flags.

### Task AI0.2: Add deterministic data verification and constrained agent roles

**Files:**
- Create: `packages/agent_research/data_verifier.py`
- Create: `packages/agent_research/schemas.py`
- Create: `packages/agent_research/evaluation.py`
- Test: `tests/agent_research/test_data_verifier.py`
- Test: `tests/agent_research/test_prompt_injection.py`
- Test: `tests/agent_research/test_evaluation.py`

**Permissions:** Historical and sanitized read-only data only. No trading credentials. No submit, cancel, promotion, policy mutation, or raw account tools.

**Promotion rule:** Start with zero portfolio weight. Increase influence only after a predefined point-in-time benchmark shows incremental value over the non-agent baseline within cost, latency, and reliability budgets.

**Phase exit:** Every output passes schema or is rejected, every claim has evidence and cutoff, and the agent path has no route to the OMS.

---

## 12. Phase S0: Shadow or testnet connector

**Priority:** P2
**Start condition:** G0 through G4 paper gates pass.
**Approval:** Separate operator approval required before any external venue or testnet interaction.

### Deliverables

- `packages/adapters/capabilities.py`
- `packages/adapters/rate_limits.py`
- One production-shaped adapter selected after a build-vs-adopt ADR
- Startup and continuous reconciliation
- WebSocket recovery and snapshot resync
- Shadow mode with zero venue submission
- Full adapter contract suite

### Build-vs-adopt ADR

Create `docs/adr/ADR-core-and-adapter-adoption.md` and score:

- NautilusTrader for multi-asset event-driven core and parity.
- LEAN for equities, options, and C# reality models.
- Hummingbot for market making and connector-heavy execution.
- A thin internal adapter only when the existing frameworks fail explicit contract or license criteria.

Do not let two frameworks submit to the same account. Integrate through targets and intents.

### Exit criteria

- No unexplained state divergence across multiple sessions.
- Exact order and fill mapping.
- Unknown-state recovery passes.
- Reconnect and rate-limit tests pass.
- Full audit trail exists.

---

## 13. Phase C0: Future canary live

This phase is not authorized by this roadmap. It requires a separate ADR and Greenlight approval after a complete paper observation window.

Minimum prerequisites:

- Dedicated subaccount and no-withdrawal key.
- Small capital and symbol allowlist.
- Tighter loss, turnover, and venue limits.
- On-call owner, tested rollback, and incident runbooks.
- Stable reconciliation and paper-shadow comparison.
- Global kill switch drill.
- No unresolved critical incidents.

---

## 14. Recommended implementation order

| Sequence | Phase | Why now | Dependency |
|---:|---|---|---|
| 1 | R0 | Removes branch, migration, and release ambiguity | None |
| 2 | D0 | Creates the stable language for all later work | AUTH0 source decision |
| 3 | D1 | Makes research claims reproducible | D0 |
| 4 | P0 | Converts forecasts into bounded portfolio targets | D0 and D1 |
| 5 | R1 | Blocks unsafe targets before order work | P0 |
| 6 | O0 | Adds durable paper order authority and reconciliation | R1 |
| 7 | T0 | Proves one shared paper path and exposes operations | O0 |
| 8 | M0 | Governs models after deterministic baseline exists | D1 and T0 |
| 9 | AI0 | Adds LLM value without granting authority | M0 |
| 10 | S0 | Validates a production-shaped adapter in shadow | G4 and approval |
| 11 | C0 | Future bounded canary only | Separate ADR and approval |

### Stop conditions

Stop promotion immediately if any phase introduces:

- direct LLM-to-broker or strategy-to-broker calls;
- float money or quantity in the canonical domain;
- paper and backtest order state machines that differ;
- retry logic without client ID reconciliation;
- no `UNKNOWN` order outcome;
- mutable activated risk policy;
- model activation without lineage and expiry;
- multiple order authorities for one account;
- runtime mutation without explicit approval;
- unsupported evidence claims or secret exposure.

---

## 15. Verification matrix

| Scope | Command | Required result |
|---|---|---|
| Repository shape | `make audit` | PASS |
| Generated contracts | `make check-contracts` | No drift |
| Root Python | `make test-core` | PASS |
| Legacy backend | `make test-backend` | PASS |
| Dashboard tests | `make test-dashboard` | PASS |
| Dashboard types | `make typecheck-dashboard` | PASS |
| Dashboard lint | `make lint-dashboard` | PASS |
| Dashboard build | `make build-dashboard` | PASS |
| Release candidate | `make audit-release` | Clean tree and PASS |
| Domain replay | `uv run pytest -q tests/domain tests/event_ledger` | PASS |
| Risk | `uv run pytest -q tests/risk_gateway` | PASS |
| OMS | `uv run pytest -q tests/oms tests/reconciliation tests/ledger` | PASS |
| Paper flow | `uv run pytest -q tests/trading_node/test_paper_flow.py` | PASS |
| Research | `uv run pytest -q tests/data_catalog tests/research tests/research_validation` | PASS |
| Agent | `uv run pytest -q tests/agent_research tests/research_contracts` | PASS |

Tests must use disposable PostgreSQL only through the approved hermetic harness. They must not call an exchange, broker, account, balance, position, order, or active mutation endpoint.

---

## 16. Definition of paper-production ready

The platform is ready for a separately approved paper-production deployment only when:

- Contracts and replay are deterministic.
- Raw data and datasets have checksums and point-in-time lineage.
- Baselines and cost sensitivity are reported.
- Target portfolios satisfy documented constraints.
- Risk is deterministic, versioned, default deny, and independent of LLMs.
- OMS submission is idempotent and preserves unknown outcomes.
- Startup and continuous reconciliation pass.
- Restart during partial fill recovers correctly.
- Kill-switch drills pass.
- Dashboard exposes paper mode, health, risk, orders, fills, and reconciliation.
- Release build and rollback artifacts are reproducible and verified.
- G0 through G4 evidence is current.

Only after that should the operator decide whether to authorize a paper deployment. Live remains a separate future decision.

---

## 17. Source map

- Research V2 target planes and state ownership: lines 1218 to 1322.
- Research V2 domain contracts and order states: lines 1326 to 1495.
- Research V2 risk gateway: lines 1499 to 1621 and 3427 to 3884.
- Research V2 OMS and reconciliation: lines 1625 to 1731 and 3888 to 4360.
- Research V2 data and model governance: lines 1735 to 1849.
- Research V2 agent permissions: lines 1853 to 1947.
- Research V2 validation and live path: lines 1965 to 2090.
- Research V2 operations and security: lines 2094 to 2325.
- Research V2 phase gates and acceptance criteria: lines 2407 to 2692.
- Research V2 signal fusion and portfolio construction: lines 3002 to 3421.
- Existing repository plan: `docs/upgrade-plan/UPGRADE-PLAN.md`.
- Existing acceptance gates: `docs/upgrade-plan/acceptance-gates.md`.
- Existing production blockers: `docs/production/production-readiness-baseline.md`.
- Current job-plane blockers: `docs/implementation/job-plane-residual-risks-v2.md`.
