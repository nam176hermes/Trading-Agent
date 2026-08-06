# WS-04B Target Strategy Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, paper-only bridge that converts approved canonical target portfolios into typed engine targets without exposing provider dictionaries or execution authority.

**Architecture:** `packages/strategy_bridge` is a pure domain package. It accepts only `TargetPortfolio` and produces the existing strict `EngineTargetPortfolio` contract with canonical ordering and no side effects. Quantity sizing, risk decision, order construction, Nautilus runtime adapter, and backtest wiring remain 04C/05B.

**Tech Stack:** Python 3.11, Pydantic v2, Decimal, existing `packages.domain` contracts, pytest.

## Global Constraints

- Paper-only: do not activate services, brokers, exchanges, databases, or runtime engine commands.
- Strategies never receive provider dictionaries; accepted inputs are strict canonical models only.
- No new production dependency without operator approval.
- Preserve UTC, canonical decimal precision, deterministic ordering, and stable output digest/IDs.
- 04B does not implement Nautilus `Strategy`, DataEngine/Cache ingestion, or a backtest runner; those belong to 04C.

---

### Task 1: Define target-to-engine bridge contract

**Files:**
- Create: `packages/strategy_bridge/__init__.py`
- Create: `packages/strategy_bridge/target.py`
- Test: `tests/strategy_bridge/test_target_bridge.py`

**Interfaces:**
- Consumes: `packages.domain.TargetPortfolio`.
- Produces: `bridge_target_portfolio(value: TargetPortfolio) -> EngineTargetPortfolio` and `TargetStrategyBridgeError`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_bridge_maps_a_canonical_target_without_creating_an_order() -> None:
    result = bridge_target_portfolio(target())
    assert result.target_id == target().target_id
    assert "order" not in type(result).__name__.casefold()
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest -q tests/strategy_bridge/test_target_bridge.py`

Expected: FAIL because `packages.strategy_bridge` does not exist.

- [ ] **Step 3: Implement the strict models**

Map each canonical instrument field into `EngineInstrumentId`, preserve target metadata, and sort positions by `InstrumentId.canonical` plus source signal UUIDs by their canonical string representation. Reject non-`TargetPortfolio` input. Do not construct an order or inspect price/equity.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest -q tests/strategy_bridge/test_target_bridge.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/strategy_bridge tests/strategy_bridge
git commit -m "feat(strategy): bridge canonical targets to engine contracts"
```

### Task 2: Lock source-only and paper-safe boundaries

**Files:**
- Modify: `tests/strategy_bridge/test_target_bridge.py`

**Interfaces:**
- Verifies the public bridge surface is pure and 04C is the only future Nautilus runtime integration point.

- [ ] **Step 1: Write failing behavior tests**

```python
def test_strategy_bridge_has_no_provider_runtime_or_execution_imports() -> None:
    assert forbidden_imports(Path("packages/strategy_bridge")) == []
```

Forbid `nautilus_trader`, `services`, `requests`, `httpx`, `socket`, `subprocess`, `sqlalchemy`, `psycopg`, and broker/exchange modules.

- [ ] **Step 2: Run RED**

Run: `uv run pytest -q tests/strategy_bridge/test_target_bridge.py::test_strategy_bridge_has_no_provider_runtime_or_execution_imports`

Expected: FAIL until the AST check is added.

- [ ] **Step 3: Implement pure translation**

Keep the production bridge dependency-free and document the 04C handoff: Nautilus consumes the `EngineTargetPortfolio` only after explicit typed runtime integration.

- [ ] **Step 4: Run focused and boundary tests**

Run: `uv run pytest -q tests/strategy_bridge tests/engine_contracts/test_commands.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/strategy_bridge tests/strategy_bridge docs/superpowers/plans/2026-08-06-ws04-target-strategy-bridge.md
git commit -m "test(strategy): lock target bridge boundaries"
```

## Gate sequence

1. Independent adversarial Codex review; resolve all blockers with regressions.
2. Independent final Codex gate on the exact candidate.
3. Run `env TMPDIR=/tmp TEMP=/tmp TMP=/tmp make ci` on the gated SHA.
4. Fast-forward local `main` only after all gates pass; do not push remote.

## Self-review

- Coverage: target portfolio bridge, typed signal provenance through `source_signal_ids`, deterministic intent generation, strict input rejection, paper/source-only boundaries, and 04C separation are covered.
- No placeholders: every task names files, interfaces, tests, commands, and commit boundaries.
- Type consistency: Task 1 defines the exact mapping Task 2 constrains; the bridge returns `EngineTargetPortfolio` for the 04C adapter handoff.
