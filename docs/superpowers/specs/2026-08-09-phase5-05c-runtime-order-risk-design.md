# Phase 5 05C Runtime Order Risk Design

## Status

Approved design for the Phase 5 runtime order-risk packet. This packet is
source-only and paper-only. It grants no deployment, broker, exchange, order
submission, production database, or live-trading authority.

## Objective

Build a deterministic runtime order-risk boundary that evaluates every
`OrderIntent` against an immutable account portfolio, market and operating
observation, and an explicit versioned policy. An approved decision becomes
usable authority only after its exact event has been durably appended to the
existing event ledger and can be read back unchanged.

05C does not execute an order. It produces a durable approval reference that a
later submit boundary can verify. Phase 5 05D will add global halt state,
circuit-breaker transitions, authority rotation, and submit-time
re-attestation against state changes.

## Existing Boundaries Preserved

- `RiskDecision` remains the target-portfolio policy-risk decision. It is not
  expanded into an order-level runtime decision.
- `OrderIntent` remains an immutable instruction whose identity fields confer
  no authority.
- `AccountPortfolioSnapshot` remains the portfolio input produced by the 05B
  reducer. 05C does not create another portfolio writer or ledger.
- The append-only event ledger remains the only durable audit authority.
- The Nautilus execution provider, runtime closures, paper harness, Job API,
  dashboard, PostgreSQL runtime, and all live routes remain unchanged.

## Considered Approaches

### Selected: separate pure evaluator plus existing-ledger approval

Introduce dedicated runtime order-risk contracts, a pure evaluator, and a
small persistence service that appends the resulting event to the existing
ledger. This keeps target policy, order risk, persistence, and execution as
separate authority boundaries and makes every decision reproducible.

### Rejected: extend the existing `RiskDecision`

The current contract evaluates `TargetPortfolio` and supports target
modification. Reusing it for an individual order would mix two stages of risk,
make its invariants ambiguous, and weaken the meaning of
`OrderIntent.risk_decision_id`.

### Rejected: implement risk inside the Nautilus execution adapter

This would couple 05C to runtime closure publication and execution activation,
and would make durable audit success harder to prove before submission. Adapter
integration belongs to Phase 6 after the source-only risk authority is closed.

## Architecture

```text
target-level RiskDecision
           +
OrderIntent + AccountPortfolioSnapshot
           +
instrument/market/venue observation + RuntimeRiskPolicy
           |
           v
pure evaluate_runtime_order_risk(...)
           |
           v
RuntimeOrderRiskDecision
           |
           v
existing EventLedgerRepository.append + exact read-back
           |
           v
DurableOrderApprovalRef (approved decisions only)
```

The evaluator does not read a clock, environment variable, database, cache, or
network. All time, price, account, policy, command-rate, and venue-health facts
are explicit immutable inputs.

## Public Contracts

The contracts live in a focused runtime-risk domain module and use the
repository's strict, frozen Pydantic conventions. All decimal values use
`Decimal`-backed domain primitives; floats and implicit currency conversion are
forbidden.

### `RuntimeInstrumentRiskSpec`

Defines the exact instrument and venue facts needed by risk evaluation:

- instrument and venue identities;
- settlement currency;
- price increment and quantity increment;
- minimum and maximum quantity;
- minimum and maximum order notional in reporting currency;
- an exact initial-margin rate from zero through one.

The increments and bounds are positive and mutually consistent. The spec must
match the order instrument and the observation venue exactly.

### `RuntimeRiskMarketSnapshot`

Contains bid, ask, last price, observation time, and a bounded provenance
identity. Prices use one currency, `bid <= ask`, and no timestamp may be after
the enclosing observation. The evaluator derives its own conservative risk
price; callers cannot inject a precomputed notional.

For market orders, the side-aware executable quote is used. For price-bearing
orders, the conservative magnitude is the maximum of the applicable executable
quote, limit price, and trigger price. This rule is deterministic and prevents
an optimistic caller-selected price from reducing risk.

### `RuntimeRiskPolicy`

The policy has a stable policy identity and version plus exact limits for:

- maximum market-data age;
- maximum portfolio and position-mark age;
- maximum pending, gross, and absolute net exposure;
- maximum strategy and venue exposure;
- minimum available cash or margin buffer;
- maximum daily loss and drawdown;
- maximum commands in the closed command window.
- exact closed command-window duration.

All monetary limits use the account reporting currency. The policy itself does
not carry a self-asserted digest. A canonical policy digest is derived by the
runtime-risk module and recorded by the decision.

### `RuntimeRiskObservation`

The observation binds:

- a stable observation identity and monotonically interpreted version;
- the exact `AccountPortfolioSnapshot`;
- canonically ordered instrument specs and market snapshots;
- engine readiness and canonically ordered venue-health records;
- canonically ordered reporting-currency conversion rates with provenance;
- daily PnL, current equity, and peak equity;
- the closed command-window start and command count;
- canonically ordered prior command identities containing both intent ID and
  client-order ID for duplicate detection;
- observation time and schema version.

Currencies, account identity, timestamps, and ordering must be internally
consistent. Missing conversion authority, stale observations, unknown
instruments, or structurally inconsistent facts fail closed.

Portfolio observation time and every retained non-zero position mark must be
within the policy's portfolio-age bound at decision time. Venue-health records
must be current for the enclosing observation. The command-window start must
define the active policy-sized window containing the observation time; an
expired or future window is invalid state rather than an implicit counter reset.

An instrument or market entry is looked up by exact canonical identity. Its
absence is a deterministic rejection, not permission to manufacture a default
instrument or price. A conversion entry is omitted only when settlement and
reporting currencies are identical, in which case the exact rate is one.

### `RuntimeOrderRiskDecision`

The decision is either `APPROVED` or `REJECTED`; 05C never modifies an order.
It binds:

- decision and intent identities;
- the canonical `OrderIntent` digest;
- the target-level `RiskDecision` identity and canonical digest;
- portfolio snapshot identity and canonical digest;
- observation identity, version, and canonical digest;
- policy identity, version, and canonical digest;
- derived risk price, projected position quantity, and projected exposure
  values, which may be absent only on a rejected decision whose missing
  authority prevents derivation;
- outcome and canonically ordered reason codes;
- decision time and schema version.

Approval requires exactly `WITHIN_LIMITS`. Rejection contains one or more
unique reasons in the fixed check order and never contains `WITHIN_LIMITS`.

### `DurableOrderApprovalRef`

This is a narrow reference, not a bearer secret. It carries an exact
`APPROVED` outcome literal and binds the exact ledger event ID, stream ID,
sequence, event digest, decision digest, intent digest, target-policy decision
digest, portfolio digest, observation digest, and runtime-policy digest. Its
fields confer no authority without successful verification against the trusted
event-ledger repository.

## Deterministic Evaluation

`evaluate_runtime_order_risk(...)` consumes:

1. an explicit decision UUID;
2. the exact `OrderIntent`;
3. its referenced target-level `RiskDecision`;
4. one `RuntimeRiskObservation`;
5. one `RuntimeRiskPolicy`;
6. an explicit UTC decision time.

It verifies that the order references the supplied target decision and that the
target decision is approved or modified. It then evaluates and accumulates
reasons in this fixed order:

1. target policy risk is approved or modified;
2. engine readiness;
3. instrument existence and exact identity;
4. market-data freshness;
5. valuation authority completeness;
6. portfolio partition consistency;
7. price precision;
8. quantity precision;
9. quantity bounds;
10. order notional bounds;
11. balance or margin buffer;
12. pending exposure;
13. gross exposure;
14. absolute net exposure;
15. strategy exposure;
16. venue exposure;
17. daily loss;
18. drawdown;
19. reduce-only semantics;
20. command rate;
21. venue health;
22. duplicate command.

The check taxonomy is closed and serialized in exactly this order. Evaluation
does not short-circuit, so identical invalid inputs produce identical complete
reason tuples.

Projected exposure uses the side-signed order notional and the matching
instrument, strategy, and venue entries from the portfolio snapshot. Absence of
a partition is canonical zero only when the snapshot contains no matching
non-zero position for that instrument, strategy, or venue. A missing or
inconsistent partition for an existing non-zero position fails closed.

The evaluator replaces the matching position's current marked contribution
with its projected contribution at the conservative risk price, rather than
blindly adding order notional to gross exposure. It then adjusts the exact
instrument, strategy, venue, gross, and net partitions. Pending exposure adds
the absolute order notional. All maximum limits are inclusive; a projected
value is rejected only when it exceeds its maximum. Minimum quantity and
notional bounds are inclusive.

For balance or margin, the instrument spec supplies an exact initial-margin
rate from zero through one. The risk-increasing projected notional multiplied by
that rate is added to existing margin use. Available reporting-currency funds
are `cash - locked_funds - projected_margin_used`; absence of a reporting
currency balance or falling below the policy's minimum buffer fails closed.

Daily loss is rejected when daily PnL is below the negative maximum-loss
magnitude. Drawdown is `max(peak_equity - current_equity, 0)` and is rejected
when it exceeds the maximum drawdown. Command rate evaluates the new command,
so `commands_in_window + 1` must not exceed the configured maximum. A command
is duplicate when either its intent ID or client-order ID already appears in
the prior-command set.

`reduce_only=True` is valid only when the order cannot cross through zero,
reverse the position, or increase absolute position exposure. An order merely
claiming reduce-only receives no exemption from freshness, precision,
notional, venue-health, duplicate, or durable-audit requirements.

05C evaluates every order, including exposure-reducing orders. Global halt is
not silently treated as false: it is absent from the 05C contract and will be a
mandatory additional 05D authority before submission.

## Durable Approval Flow

The persistence service receives a fully constructed decision event and an
`EventLedgerRepository` implementation.

1. Rejected decisions may be appended for audit but never produce a durable
   approval reference.
2. Approved decisions are appended with a canonical outbox intent.
3. Append failure, sequence conflict, content conflict, or read-back failure
   raises a bounded durable-approval error and returns no approval.
4. An idempotent append of byte-identical content is accepted.
5. After append, the service reloads the ledger event and compares the exact
   canonical event bytes and digest before constructing the reference.
6. `verify_durable_order_approval(...)` reloads the referenced event and
   verifies every reference binding. A forged, stale, rejected, missing, or
   mismatched event fails closed.

No new database table or migration is required: the existing event ledger
stores the new typed event envelope. Tests use the in-memory repository and
failing repository doubles only; no PostgreSQL instance is mutated.

The approval binds the observation used during evaluation. It does not claim
that state remains current forever. 05D must compare the current halt/risk
authority with these bindings immediately before submit and reject rotations or
state drift.

## Error Handling

- Policy violations are normal deterministic rejection results.
- Invalid contracts fail during strict model validation.
- Missing authority, inconsistent currencies, missing exposure facts, or
  unrepresentable projections fail closed rather than assuming zero.
- Persistence and verification faults use a bounded exception taxonomy and do
  not return partial authority.
- Error strings and event payloads contain identities and reason codes only;
  they do not contain credentials, environment data, or raw private runtime
  paths.

## Packet Decomposition

### 05C1 — contracts and pure evaluator

- Add runtime-risk contracts and canonical digest helpers.
- Implement fixed-order deterministic evaluation and projections.
- Generate and check domain JSON schemas.
- Cover long, short, increase, partial reduction, full close, reversal,
  stale/missing data, multi-currency conversion, precision, limits, rate,
  venue health, duplicate, and reduce-only adversarial cases.

### 05C2 — durable ledger approval

- Register the runtime decision as a typed event payload.
- Append and read back decisions through the existing ledger boundary.
- Produce and verify `DurableOrderApprovalRef` only for exact approved events.
- Cover append failure, read-back failure, idempotent replay, conflicting
  content, rejected decisions, forged references, and stale binding attempts.

Each task follows test-first RED to GREEN, receives an independent task review,
and the whole branch receives an adversarial final review before integration.

## Validation

At minimum the implementation plan must run:

- focused runtime-risk and durable-approval tests;
- existing risk, order, event, event-ledger, portfolio, and 05B reducer tests;
- generated-contract drift checks;
- broad-handler inventory and secret-hygiene checks;
- `git diff --check`;
- a fresh clean-candidate aggregate gate before local integration.

## Acceptance Criteria

1. Identical canonical inputs always yield the same outcome, reason order,
   projections, and digests.
2. Every missing, stale, inconsistent, or limit-breaching fact fails closed.
3. Reduce-only cannot increase or reverse exposure.
4. Approval binds the exact intent, target policy decision, portfolio,
   observation, runtime policy, projections, and decision event.
5. No approval reference is returned before exact ledger append and read-back.
6. Forged or mismatched references fail verification against the trusted
   repository.
7. Existing Phase 3–05B contracts and replay behavior remain compatible and
   generated schemas are current.
8. The diff contains no execution provider, runtime closure, persistence
   migration, service activation, network, broker, exchange, dashboard, paper,
   or live-trading change.
9. Independent task reviews and the final whole-branch review pass before
   integration.

## Explicitly Deferred to 05D

- durable global halt and circuit-breaker state;
- daily-loss/drawdown-to-halt transitions;
- halt authority rotation and recovery;
- submit-time re-attestation and evaluation/submit race protection;
- any execution-client or provider integration.
