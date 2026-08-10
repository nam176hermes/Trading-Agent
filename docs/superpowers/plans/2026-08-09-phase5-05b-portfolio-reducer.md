# Phase 5 05B Portfolio Reducer and Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic one-account portfolio reducer that consumes only registered canonical ledger envelopes and produces an exact AccountPortfolioSnapshot plus a hash-bound replay snapshot supporting strict tail replay.

**Architecture:** Keep packages.event_ledger as the canonical envelope codec and generic ledger. Add a closed portfolio-entry event family and a pure packages.portfolio_reducer projection: it persists nothing, derives immutable account state, and carries an active execution-effect index solely for correction/bust reversal.

**Tech Stack:** Python 3.11, Pydantic v2 strict frozen models, existing Money/Price/Quantity/Currency primitives, SHA-256 canonical JSON, pytest, contract generator.

## Global Constraints

- Use existing EventEnvelope registration and canonical serialization; add no second ledger, persistence adapter, API route, database migration, provider, order approval, or live authority.
- Do not modify FillEvent, legacy PortfolioSnapshot, order/risk contracts, or existing event registrations; add only the portfolio entry family.
- AccountPositionSnapshot.average_entry_price is required for non-zero quantity, absent for zero quantity, and uses settlement currency.
- A replay accepts one account and one stream. Incoming canonical order is (stream_id.bytes, sequence, event_id.bytes); all state tuples have explicit lexical/UUID order.
- Use exact arithmetic only; reject non-representable Money values. Never use float, ambient Decimal rounding, wall clock, filesystem, network, database, or provider input.
- PortfolioReplayError is fail-closed. No degraded mode or partial result exists.
- Full history and verified snapshot-plus-tail must produce identical result objects, canonical JSON, and state SHA-256.
- Generate schemas only with uv run python scripts/generate_contracts.py; do not hand-edit generated output or lockfiles.
- Preserve user-owned worktree files. Do not push or take production actions.

---

## File Structure

- packages/domain/portfolio.py — 05A position cost-basis invariant.
- packages/domain/portfolio_events.py — strict public portfolio payload models, no accounting.
- packages/domain/events.py — additive registration of those payloads.
- packages/domain/__init__.py — public domain exports.
- packages/portfolio_reducer/models.py — immutable state, execution effects, valuation rates, result, and record models.
- packages/portfolio_reducer/reducer.py — exact event application and AccountPortfolioSnapshot derivation.
- packages/portfolio_reducer/replay.py — strict canonical replay, state JSON/hash, and snapshot-tail validation.
- packages/portfolio_reducer/__init__.py — public reducer boundary.
- scripts/generate_contracts.py — public model/schema inventory.
- tests/domain/test_portfolio_events.py — cost-basis, payload, registration tests.
- tests/portfolio_reducer/test_execution_accounting.py — fill/correction/bust accounting tests.
- tests/portfolio_reducer/test_adjustments.py — mark, funding, conversion, valuation, reconciliation tests.
- tests/portfolio_reducer/test_portfolio_replay.py — stream, canonical ordering, snapshot-tail, tamper tests.
- tests/domain/test_contract_generation.py — generated schemas and legacy compatibility.

### Task 1: Public Cost-Basis and Portfolio Ledger Entry Contracts

**Files:**
- Modify: packages/domain/portfolio.py
- Create: packages/domain/portfolio_events.py
- Modify: packages/domain/events.py
- Modify: packages/domain/__init__.py
- Modify: scripts/generate_contracts.py
- Create: tests/domain/test_portfolio_events.py
- Modify: tests/domain/test_contract_generation.py

**Interfaces:**
- Consumes: AccountPortfolioSnapshot, AccountPositionSnapshot, PositionMark, CurrencyConversion, FillEvent, and EventEnvelope.
- Produces: PortfolioOpeningEntry, PortfolioFillEntry, PortfolioMarkEntry, PortfolioFundingEntry, PortfolioConversionEntry, PortfolioValuationRateEntry, PortfolioReconciliationEntry, and PortfolioReconciliationSource.
- Produces: AccountPositionSnapshot.average_entry_price: Price | None.

- [ ] **Step 1: Write failing position and payload contract tests**

~~~
def test_nonzero_position_requires_settlement_cost_basis(position_factory):
    with pytest.raises(ValidationError, match="non-zero position requires an average entry price"):
        position_factory(quantity=Quantity(value=Decimal("1")), average_entry_price=None)


def test_zero_position_rejects_mark_and_cost_basis(position_factory):
    with pytest.raises(ValidationError, match="zero position must not retain a mark or average entry price"):
        position_factory(quantity=Quantity(value=Decimal("0")), average_entry_price=Price(...))


def test_portfolio_fill_entry_is_registered(portfolio_fill_entry, envelope_factory):
    envelope = envelope_factory(payload=portfolio_fill_entry)
    assert envelope.event_type == "PortfolioFillEntry"
    assert deserialize_event(serialize_event(envelope)) == envelope
~~~

Add invalid constructor cases for unordered opening balances, incomplete funding keys, non-positive/same-currency valuation rates, non-UTC times, empty provenance/source/revision IDs, and a reconciliation snapshot for another account.

- [ ] **Step 2: Run the focused contract tests and confirm RED**

Run: uv run pytest -q tests/domain/test_portfolio_events.py

Expected: FAIL because cost basis and portfolio-entry payloads are absent/unregistered.

- [ ] **Step 3: Implement the minimal strict models and registrations**

In AccountPositionSnapshot, add and validate the new field:

~~~
average_entry_price: Price | None

if self.quantity.value != 0:
    if self.average_entry_price is None:
        raise ValueError("non-zero position requires an average entry price")
    if self.average_entry_price.currency is not self.settlement_currency:
        raise ValueError("average entry price currency must match settlement currency")
elif self.mark is not None or self.average_entry_price is not None:
    raise ValueError("zero position must not retain a mark or average entry price")
~~~

Create portfolio_events.py with frozen DomainModel subclasses. Each carries account_id, effective_at, and schema_version; use CanonicalPortfolioIdentifier, NonEmptyText, existing primitives, and require_utc. Require both funding position-key fields or neither:

~~~
if (self.strategy_id is None) != (self.instrument is None):
    raise ValueError("funding position key must provide strategy_id and instrument together")
~~~

Require an exact FillEvent in a fill entry, marked_at == mark.marked_at, sorted opening balances, a positive valuation rate with different source/target currency and UTC quoted_at, and reconciliation snapshot.account_id == account_id with snapshot.observed_at <= effective_at. Register all seven types in EVENT_TYPE_BY_PAYLOAD; export them and append direct payloads plus all seven typed envelopes to DOMAIN_SCHEMA_MODELS.

- [ ] **Step 4: Generate schemas and prove additive compatibility**

Add assertions that the previous registry names and schema filenames remain present while seven payload and seven typed-envelope schemas are added.

Run: uv run python scripts/generate_contracts.py && uv run pytest -q tests/domain/test_portfolio_events.py tests/domain/test_contract_generation.py

Expected: PASS; all generated changes originate from the generator.

- [ ] **Step 5: Run scoped gates and commit**

Run: uv run pytest -q tests/domain/test_portfolio_events.py tests/domain/test_contract_generation.py tests/domain/test_events.py && git diff --check

Expected: PASS.

~~~
git add packages/domain/portfolio.py packages/domain/portfolio_events.py packages/domain/events.py packages/domain/__init__.py scripts/generate_contracts.py schemas tests/domain
git commit -m "feat: add portfolio ledger entry contracts"
~~~

### Task 2: Exact Execution Accounting Reducer

**Files:**
- Create: packages/portfolio_reducer/models.py
- Create: packages/portfolio_reducer/reducer.py
- Create: packages/portfolio_reducer/__init__.py
- Create: tests/portfolio_reducer/test_execution_accounting.py

**Interfaces:**
- Consumes: typed opening/fill envelopes, Money/Price/Quantity, and registered payloads from Task 1.
- Produces: PortfolioExecutionEffect, PortfolioReplayState, PortfolioReplayError, apply_portfolio_event(state, event) -> PortfolioReplayState, and reduce_portfolio_events(events) -> PortfolioReplayState.
- Produces: active normal effects keyed by original execution ID and retaining exact economic deltas needed for correction/bust reversal.

- [ ] **Step 1: Write failing normal/correction/bust accounting tests**

~~~
def test_long_partial_close_keeps_basis_and_realizes_exact_pnl(opened_state, fill_event):
    state = apply_portfolio_event(opened_state, fill_event(side=OrderSide.BUY, quantity="3", price="100"))
    state = apply_portfolio_event(state, fill_event(side=OrderSide.SELL, quantity="1", price="110"))
    position = state.snapshot.positions[0]
    assert position.quantity.value == Decimal("2")
    assert position.average_entry_price.amount == Decimal("100")
    assert position.realized_pnl.amount == Decimal("10")


def test_correction_reverses_normal_effect_then_applies_replacement(events):
    result = reduce_portfolio_events(events.normal_and_correction)
    assert result.state.active_execution_ids == (events.correction.payload.fill.execution_id,)


def test_bust_rejects_repeated_or_cross_strategy_reference(events):
    with pytest.raises(PortfolioReplayError, match="active normal execution"):
        reduce_portfolio_events(events.repeated_bust)
~~~

Cover short cash flow, cross-zero reversal, multiple weighted entries, different fee currency, exact duplicate economics, conflicting duplicate, missing/self/cross-account/cross-strategy reference, and correction after correction/bust.

- [ ] **Step 2: Run tests and confirm RED**

Run: uv run pytest -q tests/portfolio_reducer/test_execution_accounting.py

Expected: FAIL because the portfolio reducer API does not exist.

- [ ] **Step 3: Implement immutable state and exact fill effects**

Make every state tuple sorted and unique: cursor, applied (event_id, digest), balances, positions, valuation rates, and active effects. Reconstruct Money after every product/sum so currency precision is validated. Apply close-first economics:

~~~
closed = min(abs(current_quantity), abs(signed_fill_quantity))
if current_quantity > 0 and signed_fill_quantity < 0:
    realized += (fill_price - average_price) * closed * multiplier
elif current_quantity < 0 and signed_fill_quantity > 0:
    realized += (average_price - fill_price) * closed * multiplier

residual = current_quantity + signed_fill_quantity
if residual == 0:
    next_average = None
elif current_quantity == 0 or current_quantity * residual < 0:
    next_average = fill_price
elif current_quantity * signed_fill_quantity > 0:
    next_average = weighted_average(current_quantity, average_price, signed_fill_quantity, fill_price)
else:
    next_average = average_price
~~~

Buy debits settlement cash, sell credits it, and commission debits only commission currency. Update position fees only when fee currency equals settlement currency. Record a normal effect before applying it; reverse by subtracting that recorded cash/position/fee/PnL effect, never by recomputing from later state. A DUPLICATE checks equal known economics without mutation; correction/bust consumes one active normal effect.

- [ ] **Step 4: Run exact accounting tests and confirm GREEN**

Run: uv run pytest -q tests/portfolio_reducer/test_execution_accounting.py

Expected: PASS for long/short, close/reversal, duplicate/correction/bust, and fee-currency cases.

- [ ] **Step 5: Commit the execution reducer**

Run: uv run pytest -q tests/portfolio_reducer/test_execution_accounting.py && git diff --check

Expected: PASS.

~~~
git add packages/portfolio_reducer tests/portfolio_reducer/test_execution_accounting.py
git commit -m "feat: reduce portfolio fill accounting"
~~~

### Task 3: Adjustments, Valuation, Reconciliation, and Derived Snapshot

**Files:**
- Modify: packages/portfolio_reducer/models.py
- Modify: packages/portfolio_reducer/reducer.py
- Create: tests/portfolio_reducer/test_adjustments.py

**Interfaces:**
- Consumes: Task 1 adjustment payloads and Task 2 state/application API.
- Produces: derive_account_snapshot(state, observed_at) -> AccountPortfolioSnapshot and adjustment support in apply_portfolio_event.
- Produces: reporting-currency total/instrument/strategy/venue ExposureSnapshot partitions with pending exposure exactly zero.

- [ ] **Step 1: Write failing adjustment and derived-exposure tests**

~~~
def test_mark_updates_all_positions_for_instrument_and_unrealized_pnl(two_strategy_state, mark_entry):
    state = apply_portfolio_event(two_strategy_state, mark_entry(price="105"))
    assert [position.unrealized_pnl.amount for position in state.snapshot.positions] == [Decimal("5"), Decimal("5")]


def test_cross_currency_exposure_requires_explicit_valuation_rate(eur_position_state):
    with pytest.raises(PortfolioReplayError, match="valuation rate"):
        derive_account_snapshot(eur_position_state, observed_at=NOW)


def test_reconciliation_replaces_state_and_invalidates_old_execution(events):
    state = reduce_portfolio_events(events.normal_then_reconciliation)
    with pytest.raises(PortfolioReplayError, match="active normal execution"):
        apply_portfolio_event(state, events.old_execution_bust)
~~~

Add backward-mark/currency mismatch, account and position funding, incomplete/unknown funding key, insufficient conversion cash, valuation provenance/time, deterministic partition sum, and reconciliation revision/observation-time tests.

- [ ] **Step 2: Run tests and confirm RED**

Run: uv run pytest -q tests/portfolio_reducer/test_adjustments.py

Expected: FAIL because adjustment handlers and reporting-currency exposure derivation are absent.

- [ ] **Step 3: Implement each explicit adjustment boundary**

For a mark, update every matching non-zero (strategy_id, instrument) only when its new mark time is not older; calculate unrealized PnL from retained signed quantity and average price. For funding, mutate only its declared money currency; positional funding needs a complete existing key and matching settlement currency. For conversion, debit the supplied source amount and credit exactly the supplied target amount; never infer a rate. For valuation, retain only the latest explicit positive source/target rate with provenance. A reconciliation replaces the snapshot, retains its source/revision event, and clears active effects; it does not reinterpret old fills.

Derive marked non-zero exposure as abs(quantity) * mark.price * multiplier; require a matching explicit valuation rate if settlement differs from reporting currency. Fold by instrument, strategy, and venue in deterministic order, and create all pending Money values as exact reporting-currency zero.

- [ ] **Step 4: Run adjustment plus execution regressions**

Run: uv run pytest -q tests/portfolio_reducer/test_execution_accounting.py tests/portfolio_reducer/test_adjustments.py

Expected: PASS; zero positions retain neither cost basis nor mark.

- [ ] **Step 5: Commit deterministic adjustments**

Run: uv run pytest -q tests/portfolio_reducer/test_adjustments.py && git diff --check

Expected: PASS.

~~~
git add packages/portfolio_reducer tests/portfolio_reducer/test_adjustments.py
git commit -m "feat: derive portfolio adjustments and exposure"
~~~

### Task 4: Hash-Bound Replay, Snapshot Tail, and Generated Contract Integration

**Files:**
- Create: packages/portfolio_reducer/replay.py
- Modify: packages/portfolio_reducer/models.py
- Modify: packages/portfolio_reducer/__init__.py
- Modify: scripts/generate_contracts.py
- Create: tests/portfolio_reducer/test_portfolio_replay.py
- Modify: tests/domain/test_contract_generation.py
- Modify: docs/implementation/foundation-handler-inventory.md (only when its mandated check requires regeneration)

**Interfaces:**
- Consumes: Task 1 registered envelopes, Task 2/3 state, and existing ledger canonical codec/hash helpers.
- Produces: PortfolioReplayResult, PortfolioSnapshotRecord, replay_portfolio(events, *, snapshot=None), snapshot_from_portfolio_result(result), and PortfolioReplayError.

- [ ] **Step 1: Write failing full-history, tail-equality, and tamper tests**

~~~
def test_snapshot_tail_is_identical_to_full_replay(portfolio_events):
    full = replay_portfolio(portfolio_events)
    record = snapshot_from_portfolio_result(replay_portfolio(portfolio_events[:3]))
    tail = replay_portfolio(portfolio_events[3:], snapshot=record)
    assert tail == full
    assert tail.canonical_state_json == full.canonical_state_json
    assert tail.state_hash == full.state_hash


def test_tampered_record_hash_fails_closed(portfolio_events):
    record = snapshot_from_portfolio_result(replay_portfolio(portfolio_events[:2]))
    with pytest.raises(PortfolioReplayError, match="state hash"):
        replay_portfolio(portfolio_events[2:], snapshot=record.model_copy(update={"state_hash": "0" * 64}))
~~~

Cover missing/non-first opening, foreign account/stream, gap/regression, old tail event, duplicate ID with distinct canonical bytes, tampered applied digest/effect index, caller reordering, and no persistence/provider call.

- [ ] **Step 2: Run replay tests and confirm RED**

Run: uv run pytest -q tests/portfolio_reducer/test_portfolio_replay.py

Expected: FAIL because no strict portfolio record/replay boundary exists.

- [ ] **Step 3: Implement strict record validation and replay**

Define:

~~~
PORTFOLIO_REPLAY_SCHEMA_VERSION = "portfolio-replay-v1"
PORTFOLIO_REDUCER_VERSION = "portfolio-reducer-v1"
~~~

Canonicalize every incoming envelope through existing serialize_event/deserialize_event; permit only Task 1 payload classes, then require exact opening and next sequence. Validate records by rebuilding strict models, recomputing canonical JSON/SHA-256, validating ordered cursor/applied/effect tuples, and requiring every tail event to be newer than the cursor. Use the same reducer path for full and tail input.

The canonical state document contains exactly schema_version, reducer_version, state, canonical_snapshot, and cursor, encoded through existing canonical JSON/hash helpers. Every invalid envelope, state, or record raises PortfolioReplayError before any result is returned. Export models and append them to DOMAIN_SCHEMA_MODELS, then run the generator.

- [ ] **Step 4: Run the complete 05B focused suite and static gates**

Run: uv run pytest -q tests/domain/test_portfolio_events.py tests/portfolio_reducer/test_execution_accounting.py tests/portfolio_reducer/test_adjustments.py tests/portfolio_reducer/test_portfolio_replay.py tests/domain/test_contract_generation.py

Expected: PASS.

Run: make check-broad-handler-inventory && make check-secrets && git diff --check

Expected: PASS. Regenerate only the inventory if that exact command reports required source-line drift.

- [ ] **Step 5: Commit the replay boundary and generated artifacts**

~~~
git add packages/portfolio_reducer scripts/generate_contracts.py schemas tests/portfolio_reducer tests/domain/test_contract_generation.py docs/implementation/foundation-handler-inventory.md
git commit -m "feat: replay portfolio snapshots deterministically"
~~~

### Task 5: Independent Integration Review and Clean-Clone Gate

**Files:**
- Create: .superpowers/sdd/2026-08-09-phase5-05b-portfolio-reducer/task-5-review.md (ignored receipt)
- Modify: no tracked source unless a review finding opens a new TDD task.

**Interfaces:**
- Consumes: all Task 1–4 commits, approved 05B design, and generated artifacts.
- Produces: a zero-finding SPEC PASS / QUALITY PASS receipt authorizing only local fast-forward integration; no reviewer code, provider, or runtime action.

- [ ] **Step 1: Independently audit the contract and authority boundary**

Verify registry changes are additive, FillEvent and old schemas are byte-compatible, one account/stream is enforced, and no storage/API/provider/execution/risk/live path was introduced. Recompute a full-history versus tail hash without using reducer-private state.

- [ ] **Step 2: Independently run adversarial accounting and replay tests**

Run: uv run pytest -q tests/portfolio_reducer tests/domain/test_portfolio_events.py tests/domain/test_contract_generation.py

Expected: PASS. Confirm duplicate conflict, correction/bust after reconciliation, missing valuation rate, backward mark, and snapshot tamper all fail closed.

- [ ] **Step 3: Run a clean-clone release-proportional gate**

Create a detached clean clone at the candidate commit, run UV_OFFLINE=1 uv sync --frozen, then:

~~~
make audit-release
make check-contracts
make check-broad-handler-inventory
make check-secrets
uv run pytest -q tests/domain/test_portfolio_events.py tests/portfolio_reducer tests/domain/test_contract_generation.py
git diff --check
git status --short
~~~

Expected: every check passes and status is empty. Keep outputs outside Git; remove only task-owned temporary artifacts recoverably after writing the receipt.

- [ ] **Step 4: Record the independent verdict**

Write commit range, commands, source/schema compatibility evidence, and C/I/M findings to the ignored receipt. Only a zero-finding PASS authorizes a local fast-forward into main; do not push.
