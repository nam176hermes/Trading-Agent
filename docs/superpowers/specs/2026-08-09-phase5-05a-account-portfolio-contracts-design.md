# Phase 5 05A Account and Portfolio Contracts Design

## Status

Approved design for the first WS-05 packet. This packet establishes immutable
contracts only; it does not create a reducer, a risk evaluator, an execution
path, or a persistent runtime authority.

## Goal

Define the strict, deterministic account and portfolio data contracts that
WS-05B will reduce into the sole authoritative paper portfolio and that
WS-05C will consume when evaluating an order.

## Context

The current domain already has canonical `Money`, `Currency`, `Quantity`,
`Price`, `InstrumentId`, `PortfolioSnapshot`, and `RiskStateSnapshot` types.
The current `PortfolioSnapshot` is a position-only observed projection used by
existing Phase 3 and Phase 4 payloads. Replacing it now would change existing
event and generated-schema contracts before a replacement reducer exists.

The Phase 5 program requires richer account state: multi-currency balances,
cash and locked funds, margin, PnL, fees, funding, pending exposure, exposure
by instrument/strategy/venue, and marks with provenance. All authoritative
financial values must continue to use exact canonical `Decimal`-backed domain
primitives; floats remain forbidden.

## Chosen Approach

05A adds a separate, explicitly named account-and-portfolio contract family.
It leaves the existing `PortfolioSnapshot` and `RiskStateSnapshot` wire
contracts unchanged. 05B will make the new snapshot family authoritative by
introducing its reducer and a single persistence/projection route. Until that
point, the new objects are validated data only and cannot trigger execution.

This is preferred over extending the existing snapshot in place because an
in-place change would break Phase 3/4 event history and make a partial
contract rollout look like a completed authority migration. It is also
preferred over building the reducer and runtime risk engine in the same packet
because each changes a distinct authority boundary and needs its own review.

## Contract Family

The implementation will add public immutable Pydantic domain models in the
portfolio domain module and export them from `packages.domain`.

### Account identity and balances

`AccountBalanceSnapshot` represents one account/currency balance at an exact
observation time. It contains:

- a canonical account identifier compatible with existing `OrderIntent.account_id`;
- one registered `Currency`;
- signed `cash`, `realized_pnl`, `unrealized_pnl`, `fees`, and `funding` as
  `Money` in that currency;
- non-negative `locked_funds` and `margin_used` as `Money` in that currency;
- a UTC `observed_at` timestamp and a non-empty schema version.

Every `Money` value in the balance must have the balance currency. `locked_funds`
and `margin_used` cannot be negative. The contract deliberately does not infer
available buying power, perform currency conversion, or book accounting
entries; those are reducer responsibilities in 05B.

### Position marks and positions

`PositionMark` records the exact positive `Price`, UTC mark time, and
provenance identifier used to value a position. The provenance identifier is a
bounded canonical string, never a provider credential, URL, or opaque payload.

`AccountPositionSnapshot` records an account, strategy, instrument, explicit
settlement `Currency`, signed `Quantity`, optional `PositionMark`, realized
PnL, unrealized PnL, fees, and funding in that settlement currency, plus UTC
observation time. A missing mark is permitted only for a zero quantity; a
non-zero position must carry a current provenance-bearing mark. Its mark and
every monetary amount must use the explicit settlement currency. The contract
checks identity and currency consistency but does not calculate PnL.

### Exposure snapshots

`ExposureSnapshot` carries exact non-negative `gross`, signed `net`, and
non-negative `pending` `Money` in a declared reporting currency. It is reused
for total exposure and for the three explicitly keyed partitions:

- `InstrumentExposureSnapshot` keyed by canonical `InstrumentId`;
- `StrategyExposureSnapshot` keyed by canonical strategy identifier;
- `VenueExposureSnapshot` keyed by canonical venue identifier.

The contract requires every nested money amount to use the reporting currency,
requires unique keys within each partition, and keeps partitions in canonical
lexicographic key order. It does not aggregate or reconcile those values in
05A.

### Aggregate snapshot

`AccountPortfolioSnapshot` is the sole aggregate produced by this contract
family. It contains its UUID snapshot identity, canonical account identifier,
the reporting currency, tuple-valued balances, positions, total exposure, the
three exposure partitions, UTC `observed_at`, and a non-empty schema version.

It validates that all nested records belong to the same account; every
reporting/exposure amount uses the declared reporting currency; each balance
currency, position `(strategy, instrument)` pair, and partition key is unique;
and all nested timestamps are no later than the aggregate observation time.
Balances are ordered by currency code, positions by `(strategy_id,
instrument.canonical)`, and exposure partitions by their keys. Tuple order is
therefore canonical and round-trips deterministically through the generated
JSON schema. The snapshot has no mutable map, implicit currency conversion,
provider query, or execution capability.

## Authority and Compatibility Boundaries

- `AccountPortfolioSnapshot` is the only new aggregate candidate for the
  eventual paper-runtime portfolio. 05A does not mark any existing data as
  authoritative and does not permit an alternative portfolio source.
- Existing `PortfolioSnapshot`, `PositionSnapshot`, `RiskStateSnapshot`,
  `RiskDecision`, event envelopes, and their schemas stay byte-compatible in
  this packet.
- No database migration, API route, dashboard mutation, engine/provider call,
  order submission, reconciliation, or runtime materialization is in scope.
- `TradeIntent`, order-time risk decisions, signed order plans, the reducer,
  deduplication, and circuit breakers are deferred respectively to the later
  05B–05D packets. Existing target-level `RiskDecision` is unchanged.
- Values use the existing registered-currency and canonical-decimal policy;
  unknown currencies, naive timestamps, non-canonical identifiers, duplicate
  keys, cross-currency money, and non-finite/float values fail closed.

## Data Flow

```text
validated fill/reconciliation facts ──(05B, not 05A)──> AccountPortfolioSnapshot
                                                           │
                                                           ├──> runtime risk state (05C)
                                                           └──> read-only projection/API (later packet)
```

05A implements only the boxed snapshot contract at the center of this flow.
It accepts already-normalized data and has no side effects.

## Error Handling

Model construction must fail with `ValueError`/Pydantic validation errors for
invalid invariants. It must not coerce strings to numeric values, repair sort
order, silently convert currencies, invent mark provenance, or derive values
from prices. Callers must supply complete canonical input.

## Testing and Generated Contracts

The packet will use TDD and add direct tests for:

- canonical JSON round trips and immutable/extra-field rejection;
- UTC and parent/child timestamp ordering;
- currency identity consistency for every monetary field;
- negative locked/margin/exposure rejection;
- duplicate and non-canonical account, balance, position, instrument,
  strategy, and venue keys;
- zero/non-zero quantity mark rules and provenance validation;
- canonical partition ordering and deterministic JSON schemas;
- unchanged legacy portfolio/risk schemas and event contracts.

The generated domain JSON-schema inventory will be updated through
`scripts/generate_contracts.py`; the deterministic `--check` gate must pass.
No generated file is edited by hand.

## Acceptance Criteria for 05A

1. The new account/portfolio models are strict, frozen, public domain
   contracts with only exact numeric primitives.
2. Their invariants fail closed and are covered by focused adversarial tests.
3. Existing Phase 3/4 portfolio, risk, event, and order contracts remain
   compatible and their generated schemas remain current.
4. The diff has no persistence, transport, provider, execution, dashboard, or
   live-trading behavior.
5. An independent task review and whole-branch review pass before integration.

## Out of Scope Follow-on Packets

- **05B:** event-driven portfolio/accounting reducer, snapshot-plus-tail
  replay, conversion booking, fills/corrections, and reconciliation.
- **05C:** deterministic runtime order-risk evaluation and durable authority
  checks.
- **05D:** circuit breaker, halt state, rotation, and submit-time race checks.
