# Contract catalog

Python strict Pydantic models are the intended source of truth. Generate JSON
Schema 2020-12, OpenAPI 3.1, and TypeScript types; still perform runtime response
validation in the frontend.

| Contract | Purpose | Required lineage or safety fields |
|---|---|---|
| `Asset` | Canonical instrument and route | asset ID, class, provider symbol, adapter, venue, execution symbol, modes |
| `MarketSnapshot` | Point-in-time input | snapshot ID, event time, known at, ingested at, source |
| `MarketReport` | Validated market view | schema version, status, as-of, generated-at, age, assets |
| `ResearchPacket` | Sole LLM/research output | packet ID, as-of, horizon, claims, evidence, uncertainty, prompt hash, trace |
| `Signal` | Model output | model/version, score, uncertainty, valid-until, snapshot, feature hash |
| `DecisionRecord` | Chosen action | contributing signals, strategy/model/code/policy versions, actor, trace |
| `TradeIntent` | Portfolio target change | current and target weights, reason, expiry |
| `RiskDecision` | Deterministic authority | action, reason codes, original/resized target, policy version |
| `SignedOrderPlan` | Authorized execution plan | signature, TTL, idempotency key, account, strategy/policy versions |
| `OrderEvent` | Order state transition | client order ID, venue order ID, previous/new state, timestamp |
| `FillEvent` | Venue fill | order IDs, quantity, price, fees, venue timestamp |
| `PositionSnapshot` | Account position truth | account, asset, quantity, cost, observed-at, source |
| `PortfolioSnapshot` | Portfolio truth | NAV, cash, exposure, positions, observed-at |
| `JobRecord` | Durable work state | job type/state, idempotency, actor, heartbeats, attempts, commit, trace |
| `SystemStatus` | Liveness/readiness/business health | mode, deployment, workers, freshness, risk/audit status |
| `CapabilityEvidence` | Non-fabricated capability | PASS/FAIL/STALE/UNKNOWN, run, validity, metric, threshold, evidence |
| `CostEvent` | Measured resource cost | provider/model, tokens, currency, amount, job and trace |
| `StrategySpec` | Allowlisted strategy DSL | version, universe, signals, rebalance, risk profile, lifecycle state |

LLM contracts deliberately exclude quantity, order type, broker instruction,
credentials, policy changes, and submission actions.
