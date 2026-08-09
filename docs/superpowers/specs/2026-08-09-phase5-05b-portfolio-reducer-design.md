# Phase 5 05B Portfolio Reducer and Snapshot Design

## Status

Approved design for WS-05 packet 05B. This packet creates the pure,
deterministic portfolio-accounting reducer. It does not create an API route,
database migration, provider call, execution path, runtime risk decision, or
live authority.

## Goal

Make the existing append-only `EventEnvelope` ledger the sole input authority
for one account's paper portfolio, and deterministically reduce its accounting
events into a self-contained `AccountPortfolioSnapshot`. Replaying a full
history or a verified snapshot plus its tail must produce identical canonical
state and hash.

## Context

05A added strict account, position, exposure, and aggregate snapshot
contracts. The existing generic event ledger already owns canonical envelope
serialization, event identity, duplicate/conflict detection, and stream
sequence semantics. Existing `FillEvent` provides fill/correction/bust facts,
but deliberately has no account or strategy identity. It cannot be changed
without breaking Phase 3/4 schemas and retained event history.

The current position snapshot also lacks cost basis. A snapshot without cost
basis cannot reproduce realized PnL after a partial close or reversal, so 05B
adds an explicit `average_entry_price` to `AccountPositionSnapshot`: it is
required for non-zero quantity, is absent for zero quantity, and uses the
position settlement currency.

## Chosen Architecture

05B registers a closed family of new portfolio payloads with the existing
`EventEnvelope` registry. The generic event ledger remains the only durable
event identity and ordering mechanism; 05B introduces no second ledger or
portfolio store. A pure reducer consumes one account's canonical portfolio
event stream and returns a `PortfolioReplayResult` containing the new
`AccountPortfolioSnapshot`, replay cursor, correction/bust index, canonical
state JSON, and SHA-256 state hash.

```text
EventEnvelope[Portfolio*Entry] ── canonical validation ──> portfolio replay
                                                               │
                                                               ├─ AccountPortfolioSnapshot
                                                               └─ PortfolioSnapshotRecord
                                                                      │
tail EventEnvelope[Portfolio*Entry] ──────────────────────────────────┘
```

The record is a deterministic cache of the same stream, not a competing
portfolio authority. Its digest-bound cursor and execution-effect index exist
solely so snapshot-plus-tail replay has exactly the same correction/bust and
duplicate semantics as a full replay.

## Public Event Contracts

All payloads are strict, frozen, UTC-only Pydantic models, carry a non-empty
schema version, and use canonical account/strategy/provenance identifiers.
Each event envelope's `stream_id` is the portfolio stream identity. A replay
accepts exactly one such stream and one account; all payloads must bind that
account. The opening entry establishes the account and reporting currency.

### `PortfolioOpeningEntry`

The first event in an account stream. It contains the account identifier,
reporting currency, canonically ordered opening balance snapshots, source and
revision identifiers, and effective UTC time. It creates zero positions and
zero exposure. It may occur once only and cannot appear in a tail after a
snapshot.

### `PortfolioFillEntry`

Wraps an immutable existing `FillEvent` with the account and strategy
identities needed for accounting. Normal fills apply exact signed quantity,
cash movement, fee debit, average entry price, realized PnL, and position
fees. `DUPLICATE` has no economic effect but must reference an already-known
execution with identical underlying economics. `CORRECTION` reverses its
referenced normal execution then applies corrected economics. `BUST` reverses
its referenced normal execution and leaves no replacement. Missing,
self-referential, cross-account, cross-strategy, or already-reversed
references fail closed.

The reducer uses the fill's exact instrument multiplier and fixed-precision
quantity/price primitives. Buy fills debit settlement cash; sell fills credit
settlement cash. Closing a long realizes `(exit - average) * closed quantity *
multiplier`; closing a short realizes `(average - exit) * closed quantity *
multiplier`. A sign-flipping fill closes the old position first and opens the
residual at its fill price. A fee debits its declared fee currency once,
including when that differs from the settlement currency.

### `PortfolioMarkEntry`

Carries account, instrument, `PositionMark`, and effective UTC time. It applies
the same mark to every non-zero account position for that instrument, requires
the mark currency to equal each position settlement currency, and refuses a
mark older than the currently retained mark. It recalculates unrealized PnL
from the explicit average entry price. Freshness duration is not guessed here;
05C evaluates staleness against an explicit risk policy.

### `PortfolioFundingEntry`

Carries a unique funding identity, account, optional complete
`(strategy_id, instrument)` position key, signed `Money`, provenance ID, and
effective UTC time. Account-level funding changes only the matching currency
balance. Position funding additionally updates that position's funding field.
Supplying only one component of the position key, an unknown position, or a
currency mismatch fails closed.

### `PortfolioConversionEntry`

Carries account, a current `CurrencyConversion`, conversion provenance ID, and
effective UTC time. It debits the exact source cash amount and credits the
exact target cash amount. No reducer arithmetic derives an exchange rate or
rounds a conversion. An insufficient source cash balance fails closed.

### `PortfolioReconciliationEntry`

Carries a unique reconciliation identity, account, closed reconciliation
source, source revision, a complete `AccountPortfolioSnapshot`, and effective
UTC time. The supplied snapshot must bind the same account and have an
observation time no later than its effective time. It becomes the complete
post-reconciliation portfolio state and clears the active execution-effect
index, so a correction/bust referencing an execution before reconciliation is
rejected rather than silently changing reconciled state. Any resulting
position change is thereby explicitly explained by an immutable reconciliation
event.

## Reducer State and Snapshot-plus-Tail

`PortfolioReplayState` contains exactly the self-contained current portfolio
snapshot, canonical stream cursor, applied event ID/digest set, and active
normal-execution effects required for correction/bust reversal. It is strict,
ordered, and never mutable after construction.

`PortfolioSnapshotRecord` contains `PortfolioReplayState`, reducer/schema
versions, canonical JSON, and its SHA-256 hash. `snapshot_from_portfolio_result`
creates it only after full validation. `replay_portfolio(events, snapshot=...)`
revalidates the record and then accepts only the strict tail: no old event,
duplicate ID with different canonical bytes, sequence gap/regression, foreign
stream, or event before the cursor is accepted. Full replay and snapshot-plus-
tail must return exactly equal result objects, canonical JSON, and hashes.

Canonical ordering is `(stream_id.bytes, sequence, event_id.bytes)` for
incoming envelopes and explicit lexical/UUID ordering for every state tuple.
The reducer does not repair caller order, silently skip invalid state, or run
in a degraded mode.

## Aggregate State Derivation

The reducer derives balances and positions directly from entries. Position
keys are `(strategy_id, instrument.canonical)`.

- A non-zero position retains exact average entry price and the latest valid
  provenance-bearing mark; a zero position has no average entry price or mark.
- Realized PnL is carried forward after closure and cannot be rewritten except
  by a correction/bust of a still-indexed execution or a full reconciliation.
- Gross/net/pending exposure is in the reporting currency. For a position
  whose settlement currency differs from reporting currency, a current,
  provenance-bearing `PortfolioValuationRateEntry` is required. That entry
  carries an exact positive source/target rate, UTC mark time, and provenance;
  it is never inferred from a cash conversion.
- Instrument, strategy, and venue partitions are exact deterministic sums of
  marked positions. Pending exposure remains zero in 05B because no runtime
  order authority has been introduced; 05C will own its updates.

All Decimal products and sums use exact primitive helpers or integer-scaled
arithmetic. The reducer uses no float, ambient Decimal context rounding,
clock, network, filesystem, database, or provider.

## Compatibility and Authority Boundaries

- Existing `FillEvent`, legacy `PortfolioSnapshot`, `RiskStateSnapshot`,
  `RiskDecision`, order contracts, and old event schemas remain byte-compatible.
- New portfolio entry payloads are registered as additional `EventEnvelope`
  types and get generated schemas. Existing envelope registrations remain
  unchanged.
- `AccountPositionSnapshot.average_entry_price` is an additive field in the
  new 05A contract family and is regenerated through the contract generator.
- The new reducer has no persistence adapter. Phase 3's ledger repository may
  store the registered envelopes but is not changed in this packet.
- There is no risk evaluation, durable order approval, circuit breaker,
  provider transport, API route, dashboard mutation, materialized runtime, or
  live execution authority.

## Error Handling

The reducer raises a typed `PortfolioReplayError` for invalid event material,
unknown account/stream, missing opening, duplicate/conflicting identity,
sequence violation, invalid correction/bust lineage, invalid conversion,
backward mark, and tampered snapshot. It never returns partial portfolio state
on such input.

## Test Strategy

TDD must cover:

- long/short opening, partial close, reversal, multiple fills, exact cost
  basis, realized/unrealized PnL, correct short cash flow, and fee currency;
- funding at account and position scope; explicit conversion and valuation
  rate; missing/cross-currency conversion or valuation failure;
- mark provenance, backward mark rejection, and zero/non-zero average/mark
  invariants;
- exact duplicate, conflicting duplicate, correction, bust, repeated
  correction/bust, and correction after reconciliation;
- reconciliation replacing state with immutable source/revision provenance;
- one-account/one-stream constraint, sequence gap/regression, foreign tail,
  full replay versus snapshot-plus-tail, and tampered canonical snapshot;
- JSON schema generation, unchanged legacy schemas/envelope inventory, and no
  provider/persistence calls.

## Acceptance Criteria

1. The append-only event ledger is the only input authority for a portfolio
   stream; no second portfolio ledger or store is created.
2. Accounting state is deterministic and self-contained enough for exact
   snapshot-plus-tail replay, including correction/bust behavior.
3. Every position change is explained by a fill or reconciliation entry; fees,
   funding, conversions, and marks have typed identity and provenance.
4. Multi-currency balances and reporting-currency exposures use only explicit
   exact conversion/valuation inputs.
5. Existing Phase 3/4 public contracts remain compatible; all generated
   artifacts are current.
6. Independent task reviews and a whole-branch review pass before integration.

## Follow-on Boundaries

- **05C:** consumes `AccountPortfolioSnapshot` plus explicit policy limits to
  perform deterministic runtime order-risk evaluation and durable approval.
- **05D:** adds global halt/circuit-breaker state and submit-time race
  protection. It does not amend accounting history.
