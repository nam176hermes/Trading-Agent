# Phase 5 05A Account and Portfolio Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add strict immutable account, position-mark, exposure, and aggregate portfolio contracts that WS-05B can reduce into the one authoritative paper portfolio.

**Architecture:** Keep the Phase 3/4 `PortfolioSnapshot` and `RiskStateSnapshot` wire contracts untouched. Add a separately named `AccountPortfolioSnapshot` family in `packages.domain.portfolio`, using only existing exact financial primitives, and publish its schemas through the deterministic contract generator. This packet has no reducer, persistence, transport, provider, dashboard, or execution behavior.

**Tech Stack:** Python 3.11, Pydantic v2 strict/frozen models, existing `Money`/`Currency`/`Price`/`Quantity` primitives, pytest, generated JSON Schema.

## Global Constraints

- Work only on branch `codex/nt-ws05-portfolio-risk`, never directly on `main`.
- Use `UV_OFFLINE=1 uv run --frozen` for root Python tests; add no dependency and never hand-edit `uv.lock`.
- Every financial field uses an existing exact `Money`, `Price`, or `Quantity`; float, implicit conversion, and provider lookup are forbidden.
- Preserve the existing `PortfolioSnapshot`, `PositionSnapshot`, `RiskStateSnapshot`, `RiskDecision`, order/event contracts, and their generated schema bytes.
- New models are strict/frozen Pydantic models with `extra="forbid"`, UTC validation, canonical identifiers, and fail-closed validators.
- Generate artifacts only with `UV_OFFLINE=1 uv run --frozen python scripts/generate_contracts.py`.
- Do not add a reducer, database migration, API route, dashboard change, engine/provider call, order submission, reconciliation path, runtime materialization, or live authority.
- Preserve user-owned untracked files and never stage them.

---

## File Structure

- `packages/domain/portfolio.py` — account, mark, exposure, and aggregate snapshot models; existing target and observed portfolio models remain intact.
- `packages/domain/__init__.py` — public exports for the new contracts.
- `scripts/generate_contracts.py` — stable registration in `DOMAIN_SCHEMA_MODELS`.
- `generated/domain/json-schema/*.json` — generated schemas for public contracts.
- `tests/domain/test_account_portfolio_contracts.py` — focused positive and adversarial model tests.
- `tests/domain/test_contract_generation.py` — generated inventory and legacy-schema compatibility tests.

### Task 1: Account Balance, Mark, and Position Contracts

**Files:**

- Modify: `packages/domain/portfolio.py`
- Create: `tests/domain/test_account_portfolio_contracts.py`

**Interfaces:**

- Produces `AccountBalanceSnapshot`, `PositionMark`, and `AccountPositionSnapshot` in `packages.domain.portfolio`.
- `AccountBalanceSnapshot(account_id, currency, cash, locked_funds, margin_used, realized_pnl, unrealized_pnl, fees, funding, observed_at, schema_version)` requires every `Money.currency is currency`; `locked_funds` and `margin_used` must not be negative.
- `PositionMark(price, marked_at, provenance_id)` requires a UTC timestamp and an identifier compatible with `OrderIntent.account_id`.
- `AccountPositionSnapshot(account_id, strategy_id, instrument, settlement_currency, quantity, mark, realized_pnl, unrealized_pnl, fees, funding, observed_at, schema_version)` requires matching monetary/mark currency, mark time not after observation, and a mark when `quantity.value` is non-zero.

- [ ] **Step 1: Write failing account and position contract tests**

Create the focused test file with UTC, currency, instrument, money, balance, mark, and position factories. Include these exact behaviors:

```python
def test_account_balance_requires_one_currency_and_nonnegative_locked_margin() -> None:
    balance = account_balance()
    assert balance.locked_funds.amount == Decimal("3")
    with pytest.raises(ValidationError, match="currency"):
        AccountBalanceSnapshot(**{**balance.model_dump(), "fees": Money(Decimal("1"), Currency.USDT)})
    with pytest.raises(ValidationError, match="locked_funds"):
        AccountBalanceSnapshot(**{**balance.model_dump(), "locked_funds": Money(Decimal("-1"), Currency.USD)})


def test_nonzero_position_requires_current_provenance_mark() -> None:
    position = account_position()
    with pytest.raises(ValidationError, match="non-zero position"):
        AccountPositionSnapshot(**{**position.model_dump(), "mark": None})
    with pytest.raises(ValidationError, match="mark timestamp"):
        AccountPositionSnapshot(**{**position.model_dump(), "mark": future_mark()})
```

Also cover `extra_forbidden`, frozen instances, naive timestamps, invalid identifier, wrong settlement currency, and zero quantity without a mark.

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
UV_OFFLINE=1 uv run --frozen pytest -q tests/domain/test_account_portfolio_contracts.py
```

Expected: collection fails because Task 1 public models do not exist.

- [ ] **Step 3: Add the minimum strict models and validators**

In `packages/domain/portfolio.py`, define a local canonical identifier alias using the same pattern and bounds as `OrderIntent.account_id`. Add a shared money-currency check and the three models after `PositionSnapshot`.

```python
CanonicalPortfolioIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"),
]


class PositionMark(DomainModel):
    price: Price
    marked_at: datetime
    provenance_id: CanonicalPortfolioIdentifier

    @field_validator("marked_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)
```

Use `@model_validator(mode="after")` to reject cross-currency money, negative locked/margin money, a mark after observation, and a missing mark for non-zero quantity. Reuse `Money`, `Currency`, `Price`, and `Quantity`; add no arithmetic or coercion helper.

- [ ] **Step 4: Run focused contract tests to verify GREEN**

Run:

```bash
UV_OFFLINE=1 uv run --frozen pytest -q tests/domain/test_account_portfolio_contracts.py
```

Expected: every Task 1 positive and adversarial case passes.

- [ ] **Step 5: Commit Task 1**

```bash
git add packages/domain/portfolio.py tests/domain/test_account_portfolio_contracts.py
git commit -m "feat: add account position contracts"
```

### Task 2: Exposure and Aggregate Account-Portfolio Snapshot

**Files:**

- Modify: `packages/domain/portfolio.py`
- Modify: `tests/domain/test_account_portfolio_contracts.py`

**Interfaces:**

- Consumes the Task 1 account and position models without changing them.
- Produces `ExposureSnapshot`, `InstrumentExposureSnapshot`, `StrategyExposureSnapshot`, `VenueExposureSnapshot`, and `AccountPortfolioSnapshot`.
- `ExposureSnapshot(currency, gross, net, pending)` requires money in `currency`, non-negative gross/pending, and `gross.amount >= abs(net.amount)`.
- `AccountPortfolioSnapshot` has `snapshot_id`, `account_id`, `reporting_currency`, `balances`, `positions`, `total_exposure`, three ordered exposure partitions, `observed_at`, and `schema_version`.

- [ ] **Step 1: Write failing aggregate and partition tests**

Extend the same focused file with an aggregate factory and these exact cases:

```python
def test_account_portfolio_requires_canonical_unique_ordered_members() -> None:
    snapshot = account_portfolio()
    assert AccountPortfolioSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    with pytest.raises(ValidationError, match="balances must be ordered"):
        AccountPortfolioSnapshot(**{**snapshot.model_dump(), "balances": tuple(reversed(snapshot.balances))})
    with pytest.raises(ValidationError, match="duplicate position"):
        AccountPortfolioSnapshot(**{**snapshot.model_dump(), "positions": snapshot.positions * 2})


def test_exposure_rejects_cross_currency_negative_and_impossible_net() -> None:
    with pytest.raises(ValidationError, match="pending"):
        ExposureSnapshot(currency=Currency.USD, gross=Money(Decimal("2"), Currency.USD), net=Money(Decimal("1"), Currency.USD), pending=Money(Decimal("-1"), Currency.USD))
    with pytest.raises(ValidationError, match="gross"):
        ExposureSnapshot(currency=Currency.USD, gross=Money(Decimal("1"), Currency.USD), net=Money(Decimal("2"), Currency.USD), pending=Money(Decimal("0"), Currency.USD))
```

Also test duplicate account balance currency, duplicate `(strategy_id, instrument)` position, duplicate partition keys, noncanonical partition order, child timestamps after aggregate time, cross-account member, and reporting-currency mismatch.

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
UV_OFFLINE=1 uv run --frozen pytest -q tests/domain/test_account_portfolio_contracts.py
```

Expected: imports or factories fail because Task 2 models do not exist.

- [ ] **Step 3: Add exposure and aggregate models with ordering validators**

Add the five models to `packages/domain/portfolio.py`, accepting only tuples. Compare supplied tuple values with the canonical sorted tuple and reject rather than sort input silently.

```python
class ExposureSnapshot(DomainModel):
    currency: Currency
    gross: Money
    net: Money
    pending: Money

    @model_validator(mode="after")
    def _valid_exposure(self) -> "ExposureSnapshot":
        if any(value.currency is not self.currency for value in (self.gross, self.net, self.pending)):
            raise ValueError("exposure money currency must match exposure currency")
        if self.gross.amount < 0 or self.pending.amount < 0:
            raise ValueError("gross and pending exposure must be non-negative")
        if self.gross.amount < abs(self.net.amount):
            raise ValueError("gross exposure must cover absolute net exposure")
        return self
```

`AccountPortfolioSnapshot` must verify one account across balances/positions, reporting-currency consistency for total and partition exposures, canonical ordering/unique identity of every sequence, and child timestamps at or before `observed_at`. It must not total exposures, convert balances, or calculate PnL.

- [ ] **Step 4: Run focused contract tests to verify GREEN**

Run:

```bash
UV_OFFLINE=1 uv run --frozen pytest -q tests/domain/test_account_portfolio_contracts.py
```

Expected: positive, strictness, JSON round-trip, ordering, duplicate, timestamp, currency, and impossible-exposure cases pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add packages/domain/portfolio.py tests/domain/test_account_portfolio_contracts.py
git commit -m "feat: add aggregate portfolio contracts"
```

### Task 3: Public Exports and Deterministic Contract Publication

**Files:**

- Modify: `packages/domain/__init__.py`
- Modify: `scripts/generate_contracts.py`
- Modify: `tests/domain/test_contract_generation.py`
- Modify: `tests/domain/test_account_portfolio_contracts.py`
- Create: `generated/domain/json-schema/AccountBalanceSnapshot.json`
- Create: `generated/domain/json-schema/PositionMark.json`
- Create: `generated/domain/json-schema/AccountPositionSnapshot.json`
- Create: `generated/domain/json-schema/ExposureSnapshot.json`
- Create: `generated/domain/json-schema/InstrumentExposureSnapshot.json`
- Create: `generated/domain/json-schema/StrategyExposureSnapshot.json`
- Create: `generated/domain/json-schema/VenueExposureSnapshot.json`
- Create: `generated/domain/json-schema/AccountPortfolioSnapshot.json`

**Interfaces:**

- Consumes all eight Task 1/2 models unchanged.
- Publishes exactly eight new `packages.domain` symbols and eight generated schemas.
- Leaves every pre-existing generated schema byte-identical.

- [ ] **Step 1: Write failing export and schema inventory tests**

Add the eight filenames to `EXPECTED` in `tests/domain/test_contract_generation.py`, then add:

```python
def test_account_portfolio_schemas_publish_strict_exact_contracts() -> None:
    snapshot = json.loads((SCHEMA_ROOT / "AccountPortfolioSnapshot.json").read_text(encoding="utf-8"))
    exposure = json.loads((SCHEMA_ROOT / "ExposureSnapshot.json").read_text(encoding="utf-8"))
    assert snapshot["additionalProperties"] is False
    assert {"account_id", "reporting_currency", "balances", "positions", "total_exposure"} <= set(snapshot["required"])
    assert {"gross", "net", "pending"} <= set(exposure["required"])
```

In `test_account_portfolio_contracts.py`, import every model from `packages.domain` and assert each equals its module-level class.

- [ ] **Step 2: Run contract-generation tests to verify RED**

Run:

```bash
UV_OFFLINE=1 uv run --frozen pytest -q tests/domain/test_contract_generation.py tests/domain/test_account_portfolio_contracts.py
```

Expected: the generated inventory fails until models are exported, registered, and rendered.

- [ ] **Step 3: Publish models and generate schemas**

Add the eight models to the portfolio import and `__all__` in `packages/domain/__init__.py`. Register them in `DOMAIN_SCHEMA_MODELS` immediately after `PortfolioSnapshot`, in this order:

```python
AccountBalanceSnapshot,
PositionMark,
AccountPositionSnapshot,
ExposureSnapshot,
InstrumentExposureSnapshot,
StrategyExposureSnapshot,
VenueExposureSnapshot,
AccountPortfolioSnapshot,
```

Then run:

```bash
UV_OFFLINE=1 uv run --frozen python scripts/generate_contracts.py
```

Do not add a new `EventEnvelope` payload or modify old schemas.

- [ ] **Step 4: Verify schemas, legacy compatibility, and focused contracts**

Run:

```bash
UV_OFFLINE=1 uv run --frozen python scripts/generate_contracts.py --check
UV_OFFLINE=1 uv run --frozen pytest -q tests/domain/test_contract_generation.py tests/domain/test_event_contracts.py tests/domain/test_account_portfolio_contracts.py
git diff --check
```

Expected: generated schemas are current and both old/new contracts pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add packages/domain/__init__.py scripts/generate_contracts.py tests/domain/test_contract_generation.py tests/domain/test_account_portfolio_contracts.py generated/domain/json-schema
git commit -m "feat: publish account portfolio contracts"
```

## Plan Self-Review

### Spec coverage

- Tasks 1 and 2 cover exact balances, marks, positions, exposures, aggregate identity, strictness, UTC, currency, ordering, and deterministic serialization.
- Task 3 covers public exports, deterministic JSON Schema, and existing portfolio/risk/event compatibility.
- The no-reducer/no-runtime/no-provider/no-live boundary is binding in Global Constraints and each task scope.

### Placeholder scan

Every task contains exact files, interfaces, RED/GREEN commands, test examples, production-model requirements, and a commit command. No implementation choice is deferred inside this packet.

### Type consistency

Task 1 defines `AccountBalanceSnapshot`, `PositionMark`, and `AccountPositionSnapshot`. Task 2 consumes those exact names and defines `ExposureSnapshot` plus all four aggregate/partition names. Task 3 exports and generates exactly those eight model names.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-09-phase5-05a-account-portfolio-contracts.md`.

Execution uses Subagent-Driven Development as requested: one fresh implementer and independent task reviewer per task, followed by an independent whole-branch review.
