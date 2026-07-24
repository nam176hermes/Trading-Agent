# Trading Agent upgrade plan

## Strategy

Use a strangler migration. Do not rewrite the roughly 39,000-line Python backend
or discard its models, paper state, scratchpads, and historical records.

```text
Freeze live and inventory runtime
  -> wrap legacy data with contracts
  -> recover a canonical paper dashboard
  -> add a read-only Control API
  -> move operational truth to PostgreSQL
  -> introduce durable jobs
  -> separate deterministic risk and execution
  -> govern models and LLM research
  -> gather paper evidence
  -> consider live-limited through a separate ADR
```

## Phase 0 - safety and runtime inventory

- Identify every process, working directory, repo commit, port, scheduler, data
  root, and read/write path.
- Inventory all possible order-submission code paths without calling them.
- Confirm mode and every live-enablement flag while redacting secrets.
- Establish canonical repo, dashboard, URL, data root, and rollback approach.
- Produce `docs/audits/phase-0-runtime-inventory.md`.

Exit: the active deployment and report producer are unambiguous, live is proven
disabled, and consolidation has an explicit go/no-go decision.

## Phase 1 - paper dashboard recovery and route safety

- Add deployment identity and visible paper-mode status.
- Catalog only valid market reports; validate at runtime and sort semantically.
- Return typed `VALID`, `STALE`, or `NO_DATA`; malformed legacy files do not
  cause HTTP 500.
- Correct decision counts, timestamps, confidence scaling, assets, signals, and
  UI states.
- Replace hard-coded capability success with evidence states.
- Create a deny-by-default asset registry and routing tests for all ten crypto
  assets plus unknown-asset rejection.
- Remove duplicate circuit-breaker implementation through call-site analysis
  and regression tests.
- Pin dependencies and establish CI.

Exit: one canonical paper dashboard shows correct freshness and data, all ten
crypto assets route to the crypto adapter, unknown assets fail closed, and live
remains disabled.

## Phase 2 - contract-first read-only Control API

- Create strict Pydantic models and publish JSON Schema/OpenAPI.
- Generate TypeScript types; validate API payloads at runtime.
- Expose read-only `/v1/meta`, system status, market, signals, decisions,
  capabilities, and costs endpoints.
- Put legacy filesystem scanning behind the backend adapter.
- Switch the dashboard through a rollback feature flag.

Exit: the dashboard no longer scans `~/.hermes/crypto-research` directly and
contract drift fails tests.

## Phase 3 - operational store

Add PostgreSQL for assets, snapshots, reports, packets, signals, decisions, risk
decisions, intents, plans, orders, fills, positions, jobs, audit events, strategy
and policy versions, model versions, capabilities, costs, and reconciliation.
Legacy import is idempotent, hash-deduplicated, quarantines invalid data, and
labels estimated provenance.

## Phase 4 - durable commands and scheduling

Add audited, idempotent jobs for snapshot, debate, replay, and backtest using a
queue and workers. Persist canonical job state and heartbeats in PostgreSQL.

## Phase 5 - deterministic risk and execution separation

Create `TradeIntent`, `RiskDecision`, and `SignedOrderPlan` boundaries; implement
order state, idempotency, preflight, one account route, and reconciliation.

## Phase 6 - model and research governance

Inventory legacy models as `LEGACY_UNVERIFIED`, register artifacts and metrics,
introduce ResearchPacket, and run LLM factors in shadow with zero weight.

## Phase 7 - productize the paper dashboard

Connect Command Center, Signals, Risk, History, Plan, jobs, evidence, cost, and
deployment identity end-to-end without exposing order credentials.

## Phase 8 - paper evidence

Collect G0-G4 evidence. Passing G4 does not enable live trading. Live-limited
requires a separate ADR, dedicated subaccount, manual approval, no leverage,
and credentials without withdrawal permission.
