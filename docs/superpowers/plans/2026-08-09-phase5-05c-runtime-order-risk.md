# Phase 5 05C Runtime Order Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic order-level runtime-risk evaluator and issue a verifiable approval reference only after the exact approved decision event is durably appended to and read back from the existing event ledger.

**Architecture:** New strict domain contracts describe policy, observations, decisions, and durable references. A pure `packages.runtime_risk` evaluator derives conservative price, exact projections, and a fixed-order reason tuple without ambient I/O; a separate approval module appends and verifies typed decision events through `EventLedgerRepository`. The existing target-level `RiskDecision`, 05B portfolio authority, execution providers, runtime closures, and live boundaries remain unchanged.

**Tech Stack:** Python 3.11, Pydantic 2.13.4 strict frozen models, exact `Decimal` domain primitives, existing event ledger, pytest 9.0.3, generated JSON Schema.

## Global Constraints

- This packet is source-only and paper-only. It grants no deployment, broker, exchange, order submission, production database, or live-trading authority.
- `RiskDecision` remains target-portfolio policy risk; `RuntimeOrderRiskDecision` is a separate order-level approve/reject contract and never modifies an order.
- `AccountPortfolioSnapshot` is the only portfolio input. Do not create another portfolio writer, portfolio ledger, persistence table, or migration.
- Use only strict frozen Pydantic contracts and exact `Decimal`-backed primitives. Floats, ambient clocks, implicit currency conversion, and caller-supplied precomputed notional are forbidden.
- Evaluation is pure and runs the exact reason order defined by the approved spec without short-circuiting.
- A `DurableOrderApprovalRef` has authority only when every binding verifies against an exact approved event read from the trusted existing repository.
- Append, sequence, conflict, and read-back failure return no approval and fail with a bounded error.
- 05D owns durable global halt, circuit-breaker transitions, rotation, and submit-time race re-attestation. Do not implement them here.
- Do not change execution providers, runtime closure policies, Job API, dashboard, dependency manifests, lockfiles, PostgreSQL runtime, or live routes.
- Use TDD for every behavioral task. Preserve existing tests and generate contracts only through `scripts/generate_contracts.py`.

---

## File Structure

- `packages/domain/runtime_risk.py`: strict public runtime-risk models and closed reason taxonomy only.
- `packages/runtime_risk/canonical.py`: canonical model serialization and SHA-256 helpers.
- `packages/runtime_risk/projections.py`: exact price, conversion, position and exposure projection helpers.
- `packages/runtime_risk/evaluator.py`: fixed-order pure decision evaluator.
- `packages/runtime_risk/approval.py`: existing-ledger append/read-back and approval-reference verification.
- `packages/runtime_risk/__init__.py`: narrow public runtime-risk API.
- `tests/domain/test_runtime_risk_contracts.py`: model, invariant and generated-schema coverage.
- `tests/runtime_risk/test_projections.py`: conservative price and exact projection coverage.
- `tests/runtime_risk/test_evaluator.py`: fixed-order policy checks and adversarial order semantics.
- `tests/runtime_risk/test_approval.py`: persistence, idempotency and forged-reference coverage.

---

### Task 1: Runtime-risk contracts and canonical identity

**Files:**
- Create: `packages/domain/runtime_risk.py`
- Create: `packages/runtime_risk/__init__.py`
- Create: `packages/runtime_risk/canonical.py`
- Modify: `packages/domain/__init__.py`
- Modify: `scripts/generate_contracts.py`
- Generate: `generated/domain/json-schema/RuntimeInstrumentRiskSpec.json`
- Generate: `generated/domain/json-schema/RuntimeRiskMarketSnapshot.json`
- Generate: `generated/domain/json-schema/RuntimeRiskConversionRate.json`
- Generate: `generated/domain/json-schema/RuntimeVenueHealthRecord.json`
- Generate: `generated/domain/json-schema/PriorRuntimeCommandIdentity.json`
- Generate: `generated/domain/json-schema/RuntimeRiskPolicy.json`
- Generate: `generated/domain/json-schema/RuntimeRiskObservation.json`
- Generate: `generated/domain/json-schema/RuntimeOrderRiskDecision.json`
- Generate: `generated/domain/json-schema/DurableOrderApprovalRef.json`
- Test: `tests/domain/test_runtime_risk_contracts.py`
- Modify: `tests/domain/test_contract_generation.py`

**Interfaces:**
- Consumes: `InstrumentId`, `Currency`, `Money`, `Price`, `Quantity`, `FiniteDecimal`, `AccountPortfolioSnapshot`, `OrderIntent`, and `RiskDecision` from `packages.domain`.
- Produces: the public models and enums listed below plus `canonical_model_json(value: BaseModel) -> str` and `canonical_model_digest(value: BaseModel) -> str`.

- [ ] **Step 1: Write strict-contract RED tests**

Create fixtures with one USD account, BTC-USD instrument, venue `SIM`, and fixed UTC timestamps. Assert strict construction, frozen behavior, canonical ordering, currency alignment, positive increments/bounds, `0 <= initial_margin_rate <= 1`, bid/ask ordering, conversion direction/rate, non-negative command counts, unique prior intent/client IDs, inclusive policy limits, and decision semantics.

The reason enum must have this exact order:

```python
EXPECTED_REASON_ORDER = (
    "POLICY_RISK_NOT_APPROVED",
    "ENGINE_NOT_READY",
    "INSTRUMENT_UNKNOWN",
    "MARKET_DATA_STALE",
    "VALUATION_AUTHORITY_MISSING",
    "PORTFOLIO_STATE_INVALID",
    "PRICE_PRECISION_INVALID",
    "QUANTITY_PRECISION_INVALID",
    "QUANTITY_OUT_OF_BOUNDS",
    "ORDER_NOTIONAL_LIMIT",
    "BALANCE_MARGIN_LIMIT",
    "PENDING_EXPOSURE_LIMIT",
    "GROSS_EXPOSURE_LIMIT",
    "NET_EXPOSURE_LIMIT",
    "STRATEGY_EXPOSURE_LIMIT",
    "VENUE_EXPOSURE_LIMIT",
    "DAILY_LOSS_LIMIT",
    "DRAWDOWN_LIMIT",
    "REDUCE_ONLY_VIOLATION",
    "COMMAND_RATE_LIMIT",
    "VENUE_UNHEALTHY",
    "DUPLICATE_COMMAND",
    "WITHIN_LIMITS",
)
```

Assert these decision invariants:

```python
with pytest.raises(ValueError):
    approved.model_copy(update={"reason_codes": (RuntimeRiskReasonCode.ENGINE_NOT_READY,)})
with pytest.raises(ValueError):
    rejected.model_copy(update={"reason_codes": (RuntimeRiskReasonCode.WITHIN_LIMITS,)})
with pytest.raises(ValueError):
    approved.model_copy(update={"risk_price": None})
with pytest.raises(ValueError):
    approved_ref.model_copy(update={"decision_outcome": RuntimeRiskOutcome.REJECTED})
```

- [ ] **Step 2: Run the contract tests to verify RED**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q tests/domain/test_runtime_risk_contracts.py tests/domain/test_contract_generation.py -k "runtime_risk or RuntimeRisk"
```

Expected: collection/import failure because `packages.domain.runtime_risk` and its public models do not exist.

- [ ] **Step 3: Implement the strict models and canonical helpers**

Define these exact public names in `packages/domain/runtime_risk.py`:

```python
class RuntimeRiskOutcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class RuntimeVenueHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"

class RuntimeRiskReasonCode(str, Enum):
    POLICY_RISK_NOT_APPROVED = "POLICY_RISK_NOT_APPROVED"
    ENGINE_NOT_READY = "ENGINE_NOT_READY"
    INSTRUMENT_UNKNOWN = "INSTRUMENT_UNKNOWN"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    VALUATION_AUTHORITY_MISSING = "VALUATION_AUTHORITY_MISSING"
    PORTFOLIO_STATE_INVALID = "PORTFOLIO_STATE_INVALID"
    PRICE_PRECISION_INVALID = "PRICE_PRECISION_INVALID"
    QUANTITY_PRECISION_INVALID = "QUANTITY_PRECISION_INVALID"
    QUANTITY_OUT_OF_BOUNDS = "QUANTITY_OUT_OF_BOUNDS"
    ORDER_NOTIONAL_LIMIT = "ORDER_NOTIONAL_LIMIT"
    BALANCE_MARGIN_LIMIT = "BALANCE_MARGIN_LIMIT"
    PENDING_EXPOSURE_LIMIT = "PENDING_EXPOSURE_LIMIT"
    GROSS_EXPOSURE_LIMIT = "GROSS_EXPOSURE_LIMIT"
    NET_EXPOSURE_LIMIT = "NET_EXPOSURE_LIMIT"
    STRATEGY_EXPOSURE_LIMIT = "STRATEGY_EXPOSURE_LIMIT"
    VENUE_EXPOSURE_LIMIT = "VENUE_EXPOSURE_LIMIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    REDUCE_ONLY_VIOLATION = "REDUCE_ONLY_VIOLATION"
    COMMAND_RATE_LIMIT = "COMMAND_RATE_LIMIT"
    VENUE_UNHEALTHY = "VENUE_UNHEALTHY"
    DUPLICATE_COMMAND = "DUPLICATE_COMMAND"
    WITHIN_LIMITS = "WITHIN_LIMITS"
```

Add strict frozen models with these stable names:

```python
RuntimeInstrumentRiskSpec
RuntimeRiskMarketSnapshot
RuntimeRiskConversionRate
RuntimeVenueHealthRecord
PriorRuntimeCommandIdentity
RuntimeRiskPolicy
RuntimeRiskObservation
RuntimeOrderRiskDecision
DurableOrderApprovalRef
```

Use these exact public field shapes; validators add the invariants from the
spec without renaming fields:

```python
class RuntimeInstrumentRiskSpec(DomainModel):
    instrument: InstrumentId
    venue_id: CanonicalRiskIdentifier
    settlement_currency: Currency
    price_increment: Price
    quantity_increment: OrderQuantity
    min_quantity: OrderQuantity
    max_quantity: OrderQuantity
    min_order_notional: Money
    max_order_notional: Money
    initial_margin_rate: FiniteDecimal

class RuntimeRiskMarketSnapshot(DomainModel):
    instrument: InstrumentId
    bid: Price
    ask: Price
    last: Price
    observed_at: datetime
    provenance_id: CanonicalRiskIdentifier

class RuntimeRiskConversionRate(DomainModel):
    source_currency: Currency
    target_currency: Currency
    rate: FiniteDecimal
    observed_at: datetime
    provenance_id: CanonicalRiskIdentifier

class RuntimeVenueHealthRecord(DomainModel):
    venue_id: CanonicalRiskIdentifier
    health: RuntimeVenueHealth
    observed_at: datetime

class PriorRuntimeCommandIdentity(DomainModel):
    intent_id: UUID
    client_order_id: CanonicalRiskIdentifier

class RuntimeRiskPolicy(DomainModel):
    policy_id: UUID
    policy_version: CanonicalRiskIdentifier
    account_id: CanonicalRiskIdentifier
    market_data_max_age_seconds: StrictInt
    portfolio_max_age_seconds: StrictInt
    max_pending_exposure: Money
    max_gross_exposure: Money
    max_abs_net_exposure: Money
    max_strategy_exposure: Money
    max_venue_exposure: Money
    min_available_funds: Money
    max_daily_loss: Money
    max_drawdown: Money
    command_window_seconds: StrictInt
    max_commands_per_window: StrictInt
    schema_version: Literal["runtime-risk-policy-v1"]

class RuntimeRiskObservation(DomainModel):
    observation_id: UUID
    state_version: StrictInt
    portfolio: AccountPortfolioSnapshot
    instrument_specs: tuple[RuntimeInstrumentRiskSpec, ...]
    market_snapshots: tuple[RuntimeRiskMarketSnapshot, ...]
    conversion_rates: tuple[RuntimeRiskConversionRate, ...]
    venue_health: tuple[RuntimeVenueHealthRecord, ...]
    engine_ready: bool
    daily_pnl: Money
    current_equity: Money
    peak_equity: Money
    command_window_started_at: datetime
    commands_in_window: StrictInt
    prior_commands: tuple[PriorRuntimeCommandIdentity, ...]
    observed_at: datetime
    schema_version: Literal["runtime-risk-observation-v1"]

class RuntimeOrderRiskDecision(DomainModel):
    decision_id: UUID
    intent_id: UUID
    risk_decision_id: UUID
    intent_digest: Sha256
    policy_risk_decision_digest: Sha256
    portfolio_snapshot_id: UUID
    portfolio_digest: Sha256
    observation_id: UUID
    observation_version: StrictInt
    observation_digest: Sha256
    policy_id: UUID
    policy_version: CanonicalRiskIdentifier
    policy_digest: Sha256
    risk_price: Price | None
    order_notional: Money | None
    projected_position_quantity: Quantity | None
    projected_pending: Money | None
    projected_gross: Money | None
    projected_net: Money | None
    projected_strategy_gross: Money | None
    projected_venue_gross: Money | None
    projected_instrument_gross: Money | None
    projected_margin_used: Money | None
    projected_available_funds: Money | None
    outcome: RuntimeRiskOutcome
    reason_codes: tuple[RuntimeRiskReasonCode, ...]
    decided_at: datetime
    schema_version: Literal["runtime-order-risk-decision-v1"]

class DurableOrderApprovalRef(DomainModel):
    decision_outcome: Literal[RuntimeRiskOutcome.APPROVED]
    event_id: UUID
    stream_id: UUID
    sequence: StrictInt
    event_digest: Sha256
    decision_id: UUID
    decision_digest: Sha256
    intent_id: UUID
    intent_digest: Sha256
    risk_decision_id: UUID
    policy_risk_decision_digest: Sha256
    portfolio_snapshot_id: UUID
    portfolio_digest: Sha256
    observation_id: UUID
    observation_version: StrictInt
    observation_digest: Sha256
    policy_id: UUID
    policy_version: CanonicalRiskIdentifier
    policy_digest: Sha256
    schema_version: Literal["durable-order-approval-v1"]
```

Use `StrictInt` for observation version, age/window seconds, command count, and
ledger sequence. Use a lowercase SHA-256 annotated string for all digest
fields.
`RuntimeOrderRiskDecision` carries `policy_risk_decision_digest` in addition to
its decision ID and projection bindings. Its projection fields are
`Price | Money | None`; approval requires every projection field and only
`WITHIN_LIMITS`, while rejection forbids `WITHIN_LIMITS`.
`DurableOrderApprovalRef` contains exact IDs, sequence, digests, and
`decision_outcome: Literal[RuntimeRiskOutcome.APPROVED]`; it has no boolean
authority flag.

Implement canonical identity without using Pydantic's unsorted JSON output:

```python
def canonical_model_json(value: BaseModel) -> str:
    if not isinstance(value, BaseModel):
        raise ValueError("value must be a Pydantic model")
    try:
        fields = {name: getattr(value, name) for name in type(value).model_fields}
        canonical = type(value).model_validate(fields)
        document = canonical.model_dump(mode="json")
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("model cannot be canonically represented") from exc
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )

def canonical_model_digest(value: BaseModel) -> str:
    return sha256(canonical_model_json(value).encode("utf-8")).hexdigest()
```

Add regressions for invalid `model_copy(update=...)` and incomplete
`model_construct(...)` instances so neither can acquire a canonical digest.

Re-export every public contract from `packages/domain/__init__.py` and the digest helpers from `packages/runtime_risk/__init__.py`.

- [ ] **Step 4: Register and generate schemas**

Add all nine contracts to `DOMAIN_SCHEMA_MODELS` in `scripts/generate_contracts.py`, then run:

```bash
UV_OFFLINE=1 uv run python scripts/generate_contracts.py
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
```

Update `tests/domain/test_contract_generation.py` to assert every exact filename and strict schema presence. Do not edit generated JSON manually.

- [ ] **Step 5: Run GREEN and compatibility tests**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/domain/test_runtime_risk_contracts.py \
  tests/domain/test_contract_generation.py \
  tests/domain/test_event_contracts.py \
  tests/domain/test_orders.py \
  tests/domain/test_account_portfolio_contracts.py
git diff --check
```

Expected: all tests pass and generated-contract check reports no drift.

- [ ] **Step 6: Commit Task 1**

```bash
git add packages/domain/runtime_risk.py packages/domain/__init__.py \
  packages/runtime_risk/__init__.py packages/runtime_risk/canonical.py \
  scripts/generate_contracts.py generated/domain/json-schema \
  tests/domain/test_runtime_risk_contracts.py tests/domain/test_contract_generation.py
git commit -m "feat: define runtime order risk contracts"
```

---

### Task 2: Conservative price and exact portfolio projections

**Files:**
- Create: `packages/runtime_risk/projections.py`
- Modify: `packages/runtime_risk/__init__.py`
- Test: `tests/runtime_risk/test_projections.py`

**Interfaces:**
- Consumes: Task 1 contracts and `canonical_model_digest`.
- Produces: `ProjectionError`, `RuntimeRiskProjection`, and `project_runtime_order(intent: OrderIntent, observation: RuntimeRiskObservation, policy: RuntimeRiskPolicy, *, decided_at: datetime) -> RuntimeRiskProjection`.

- [ ] **Step 1: Write projection RED tests**

Cover all of these exact behaviors with table-driven public tests:

```python
@pytest.mark.parametrize(
    ("side", "order_type", "limit", "trigger", "expected"),
    [
        (OrderSide.BUY, OrderType.MARKET, None, None, Decimal("101")),
        (OrderSide.SELL, OrderType.MARKET, None, None, Decimal("99")),
        (OrderSide.BUY, OrderType.LIMIT, Decimal("103"), None, Decimal("103")),
        (OrderSide.SELL, OrderType.STOP_LIMIT, Decimal("98"), Decimal("97"), Decimal("99")),
    ],
)
def test_conservative_risk_price(
    runtime_case, side, order_type, limit, trigger, expected
):
    intent = runtime_case.make_intent(
        side=side,
        order_type=order_type,
        limit_amount=limit,
        trigger_amount=trigger,
    )
    projection = project_runtime_order(
        intent,
        runtime_case.observation,
        runtime_case.policy,
        decided_at=runtime_case.decided_at,
    )
    assert projection.risk_price.amount == expected
```

Also assert:

- same-currency rate is exactly one and rejects a redundant conflicting rate;
- cross-currency projection requires the exact source/target rate and rejects stale or reversed provenance;
- a new instrument/strategy/venue absent from partitions starts from canonical zero only when no matching non-zero position exists;
- an absent partition for an existing non-zero position raises `ProjectionError`;
- long increase, short increase, partial close, full close, and reversal derive exact position, gross, net, strategy, venue, instrument, pending, notional, margin-increase, and available-funds values;
- every product is invariant under a hostile ambient `decimal.localcontext(prec=3)`;
- market, order, quantity, and conversion inputs are never mutated.

- [ ] **Step 2: Run projection tests to verify RED**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q tests/runtime_risk/test_projections.py
```

Expected: import failure for `packages.runtime_risk.projections`.

- [ ] **Step 3: Implement deterministic lookup and pricing helpers**

Use exact helpers with these signatures: `ProjectionError(ValueError)`,
`RuntimeRiskProjection`, and
`project_runtime_order(intent: OrderIntent, observation: RuntimeRiskObservation,
policy: RuntimeRiskPolicy, *, decided_at: datetime) -> RuntimeRiskProjection`.

```python
class ProjectionError(ValueError):
    pass

class RuntimeRiskProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    risk_price: Price
    order_notional: Money
    projected_position_quantity: Quantity
    projected_pending: Money
    projected_gross: Money
    projected_net: Money
    projected_strategy_gross: Money
    projected_venue_gross: Money
    projected_instrument_gross: Money
    projected_margin_used: Money
    projected_available_funds: Money
```

Look up the instrument, market, venue, rate, reporting balance, and matching
position by exact canonical keys. Derive the side-aware conservative price in
the module. Use integer-scaled or exact local-context-independent arithmetic
following `packages/portfolio_reducer/reducer.py`; do not multiply under the
ambient Decimal context.

- [ ] **Step 4: Implement exact replacement projections**

Compute signed quantity delta (`BUY` positive, `SELL` negative), reject a
quantity currency/instrument mismatch, and replace the matching position's
marked contribution with the projected risk-price contribution. Aggregate:

```python
projected_pending = current_pending + abs(order_notional)
risk_increasing_notional = max(
    abs(projected_position_notional) - abs(current_position_notional),
    Decimal(0),
)
projected_margin_used = current_margin_used + risk_increasing_notional * initial_margin_rate
projected_available_funds = cash - locked_funds - projected_margin_used
```

For partitions absent from the snapshot, derive zero only after proving there
is no matching non-zero position. Verify all existing partition values against
recomputed position contributions before projecting. Raise `ProjectionError`
on missing authority or inconsistent state; do not return partial projections.

- [ ] **Step 5: Run GREEN and 05B regression tests**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk/test_projections.py \
  tests/portfolio_reducer \
  tests/domain/test_account_portfolio_contracts.py
git diff --check
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add packages/runtime_risk/projections.py packages/runtime_risk/__init__.py \
  tests/runtime_risk/test_projections.py
git commit -m "feat: project exact runtime order risk"
```

---

### Task 3: Fixed-order runtime-risk evaluator

**Files:**
- Create: `packages/runtime_risk/evaluator.py`
- Modify: `packages/runtime_risk/__init__.py`
- Test: `tests/runtime_risk/test_evaluator.py`

**Interfaces:**
- Consumes: Task 1 models/digests and Task 2 `project_runtime_order`.
- Produces: `evaluate_runtime_order_risk(*, decision_id: UUID, intent: OrderIntent, policy_decision: RiskDecision, observation: RuntimeRiskObservation, policy: RuntimeRiskPolicy, decided_at: datetime) -> RuntimeOrderRiskDecision`.

- [ ] **Step 1: Write evaluator RED tests for approval and complete reason order**

Create a fully valid approved fixture, then parameterize one mutation per reason.
Assert approval carries only `WITHIN_LIMITS`, every exact digest recomputes, and
all projection fields match `project_runtime_order`.

Create a combined-invalid fixture and assert the complete tuple exactly:

```python
assert decision.reason_codes == (
    RuntimeRiskReasonCode.POLICY_RISK_NOT_APPROVED,
    RuntimeRiskReasonCode.ENGINE_NOT_READY,
    RuntimeRiskReasonCode.MARKET_DATA_STALE,
    RuntimeRiskReasonCode.PRICE_PRECISION_INVALID,
    RuntimeRiskReasonCode.QUANTITY_PRECISION_INVALID,
    RuntimeRiskReasonCode.QUANTITY_OUT_OF_BOUNDS,
    RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT,
    RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,
    RuntimeRiskReasonCode.PENDING_EXPOSURE_LIMIT,
    RuntimeRiskReasonCode.GROSS_EXPOSURE_LIMIT,
    RuntimeRiskReasonCode.NET_EXPOSURE_LIMIT,
    RuntimeRiskReasonCode.STRATEGY_EXPOSURE_LIMIT,
    RuntimeRiskReasonCode.VENUE_EXPOSURE_LIMIT,
    RuntimeRiskReasonCode.DAILY_LOSS_LIMIT,
    RuntimeRiskReasonCode.DRAWDOWN_LIMIT,
    RuntimeRiskReasonCode.REDUCE_ONLY_VIOLATION,
    RuntimeRiskReasonCode.COMMAND_RATE_LIMIT,
    RuntimeRiskReasonCode.VENUE_UNHEALTHY,
    RuntimeRiskReasonCode.DUPLICATE_COMMAND,
)
```

Keep instrument, valuation, and portfolio projection authority valid in this
combined fixture so every projection-dependent check is meaningful. Separately
parameterize `INSTRUMENT_UNKNOWN`, `VALUATION_AUTHORITY_MISSING`, and
`PORTFOLIO_STATE_INVALID`; for each, combine the dependency failure with engine,
command-rate, venue-health, and duplicate failures and assert those independent
later checks are still present in canonical order. Together these tests prove
evaluation does not stop after the first reason without manufacturing
projections from missing authority.

- [ ] **Step 2: Write adversarial semantic RED tests**

Cover:

- wrong `risk_decision_id`, rejected target risk, and model-copy target-risk forgery;
- stale-by-one-second versus exact-age boundary;
- stale portfolio/position mark by one second versus exact portfolio-age
  boundary;
- price and quantity values one increment off;
- minimum/maximum quantity and notional equality versus one-unit breach;
- reporting balance missing, insufficient cash/margin, and different-currency money;
- pending/gross/net/strategy/venue equality versus breach;
- exact daily-loss/drawdown boundaries;
- reduce-only partial close and full close approval;
- reduce-only increase, cross-zero, and reversal rejection for long and short;
- command count `max - 1` approval and `max` rejection because the new command is counted;
- active command-window exact boundary and expired/future window rejection;
- degraded/unknown venue and duplicate by either intent ID or client-order ID;
- identical inputs evaluated twice produce byte-identical canonical decisions;
- hostile ambient Decimal context does not change outcome or digests.

- [ ] **Step 3: Run evaluator tests to verify RED**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q tests/runtime_risk/test_evaluator.py
```

Expected: import failure for `evaluate_runtime_order_risk`.

- [ ] **Step 4: Implement the fixed-order evaluator**

Use a single ordered tuple of `(reason_code, predicate)` checks. Never sort a
set after evaluation and never early-return. Projection failures set the
authority/portfolio reason that caused the failure and leave projection fields
`None`; independent checks that do not require a projection still run.

The evaluator must expose exactly
`evaluate_runtime_order_risk(*, decision_id: UUID, intent: OrderIntent,
policy_decision: RiskDecision, observation: RuntimeRiskObservation,
policy: RuntimeRiskPolicy, decided_at: datetime) -> RuntimeOrderRiskDecision`.

Derive all five canonical input digests internally, including the complete
target-policy `RiskDecision`. Validate UTC ordering and exact
account/instrument/strategy/venue/currency identity. Check precision with exact
integer-scaled divisibility. For approved output include every projection; for
rejection include available projections only when derivation was complete.

- [ ] **Step 5: Run GREEN and existing risk/order tests**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk/test_evaluator.py \
  tests/runtime_risk/test_projections.py \
  tests/domain/test_runtime_risk_contracts.py \
  tests/domain/test_event_contracts.py \
  tests/domain/test_orders.py \
  tests/portfolio_reducer
make check-broad-handler-inventory
git diff --check
```

Expected: all tests and static checks pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add packages/runtime_risk/evaluator.py packages/runtime_risk/__init__.py \
  tests/runtime_risk/test_evaluator.py
git commit -m "feat: evaluate runtime order risk"
```

---

### Task 4: Durable event-ledger approval and verification

**Files:**
- Create: `packages/runtime_risk/approval.py`
- Modify: `packages/runtime_risk/__init__.py`
- Modify: `packages/domain/events.py`
- Modify: `scripts/generate_contracts.py`
- Generate: `generated/domain/json-schema/EventEnvelope_RuntimeOrderRiskDecision_.json`
- Test: `tests/runtime_risk/test_approval.py`
- Modify: `tests/domain/test_event_contracts.py`
- Modify: `tests/domain/test_contract_generation.py`
- Modify: `tests/event_ledger/test_replay.py`
- Modify: `tests/event_ledger/test_repository.py`

**Interfaces:**
- Consumes: Tasks 1–3, `EventEnvelope[RuntimeOrderRiskDecision]`, `EventLedgerRepository`, `InMemoryEventLedger`, and ledger canonical serialization helpers.
- Produces: `DurableApprovalError`, `record_runtime_risk_decision(...) -> DurableOrderApprovalRef | None`, and `verify_durable_order_approval(...) -> RuntimeOrderRiskDecision`.

- [ ] **Step 1: Write event-registration and approval RED tests**

Registering the new payload must make this typed envelope valid:

```python
event = EventEnvelope[RuntimeOrderRiskDecision](
    event_id=event_id,
    event_type="RuntimeOrderRiskDecision",
    schema_version="runtime-order-risk-event-v1",
    source="runtime-risk",
    stream_id=stream_id,
    sequence=1,
    observed_at=decided_at,
    ingested_at=decided_at,
    produced_at=decided_at,
    effective_at=decided_at,
    expires_at=decided_at + timedelta(minutes=5),
    correlation_id=intent.intent_id,
    causation_id=intent.risk_decision_id,
    trace_id=trace_id,
    payload=approved_decision,
)
```

Assert `serialize_event`/`deserialize_event` round-trip retains the concrete
payload type and canonical bytes. Before implementation these tests must fail
because the payload is unregistered.

- [ ] **Step 2: Write durable-flow and adversarial RED tests**

Use `InMemoryEventLedger` and bounded doubles to cover:

```python
reference = record_runtime_risk_decision(repository=ledger, event=event)
verified = verify_durable_order_approval(
    repository=ledger,
    reference=reference,
    intent=intent,
    policy_decision=policy_decision,
    observation=observation,
    policy=policy,
)
assert verified == approved_decision
```

Also require:

- rejected decision append is auditable and returns `None`;
- append exception, sequence conflict, conflicting content, absent read-back,
  wrong event type, wrong payload type, and mutated read-back raise
  `DurableApprovalError` and return no reference;
- exact idempotent append returns the same reference;
- forged event/stream/sequence/event-digest/decision-digest/intent/
  target-policy/portfolio/observation/runtime-policy binding is rejected one
  field at a time;
- a reference to a rejected decision is rejected even when all hashes match;
- verifying against a different intent, target decision, observation, policy,
  or repository fails;
- publication topic is exactly `runtime-risk.decisions` and payload JSON equals
  `json.dumps({"decision_id": str(event.payload.decision_id)},
  sort_keys=True, separators=(",", ":"))`;
- persistence is invoked before the first reference can be observed.

- [ ] **Step 3: Run approval tests to verify RED**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk/test_approval.py \
  tests/domain/test_event_contracts.py -k "runtime_risk or RuntimeOrderRiskDecision"
```

Expected: event registration and approval imports fail.

- [ ] **Step 4: Register the typed event and implement durable approval**

Add `RuntimeOrderRiskDecision: "RuntimeOrderRiskDecision"` to
`EVENT_TYPE_BY_PAYLOAD`. Implement exact public signatures
`record_runtime_risk_decision(*, repository: EventLedgerRepository,
event: EventEnvelope[RuntimeOrderRiskDecision]) -> DurableOrderApprovalRef | None`
and `verify_durable_order_approval(*, repository: EventLedgerRepository,
reference: DurableOrderApprovalRef, intent: OrderIntent,
policy_decision: RiskDecision, observation: RuntimeRiskObservation,
policy: RuntimeRiskPolicy) -> RuntimeOrderRiskDecision`.

```python
class DurableApprovalError(RuntimeError):
    pass
```

Build the outbox inside the service. Catch repository and canonicalization
errors only at the narrow append/read-back boundary and chain them into
`DurableApprovalError`; do not add a broad-handler suppression. Reload events,
select exactly one matching event ID, compare canonical event text and digest,
then construct the reference for approved decisions. Verification recomputes
the expected input digests and every reference/event binding before returning
the concrete approved payload.

- [ ] **Step 5: Generate the typed event schema and run GREEN**

Register `EventEnvelope[RuntimeOrderRiskDecision]` in `DOMAIN_SCHEMA_MODELS`,
regenerate, and run:

```bash
UV_OFFLINE=1 uv run python scripts/generate_contracts.py
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk \
  tests/domain/test_runtime_risk_contracts.py \
  tests/domain/test_event_contracts.py \
  tests/domain/test_contract_generation.py \
  tests/event_ledger \
  tests/portfolio_reducer
make check-broad-handler-inventory
make check-secrets
git diff --check
```

Expected: all tests and gates pass without a database, provider, runtime, or
network action.

- [ ] **Step 6: Commit Task 4**

```bash
git add packages/runtime_risk/approval.py packages/runtime_risk/__init__.py \
  packages/domain/events.py scripts/generate_contracts.py \
  generated/domain/json-schema/EventEnvelope_RuntimeOrderRiskDecision_.json \
  tests/runtime_risk/test_approval.py tests/domain/test_event_contracts.py \
  tests/domain/test_contract_generation.py tests/event_ledger/test_replay.py \
  tests/event_ledger/test_repository.py
git commit -m "feat: persist runtime order approvals"
```

---

## Whole-branch completion gate

After all four task reviews pass:

1. Generate one whole-branch review package from the merge base through HEAD.
2. Dispatch an independent broad reviewer for SPEC and QUALITY verdicts.
3. Resolve all Critical and Important findings through the bounded SDD fix
   loop; record Minor findings in the plan ledger for final triage.
4. In a fresh clean clone at the exact reviewed candidate, run root frozen
   offline sync, focused Phase 5 tests, generated-contract checks, broad-handler
   inventory, secret hygiene, `git diff --check`, and `make test-all`.
5. Only a clean independent review plus clean-candidate gates may authorize a
   local fast-forward integration. Do not push or activate runtime behavior.
