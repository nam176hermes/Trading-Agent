# Lean & Lumibot Architecture Comparison: Portability Report

## 1. Lean's Engine Architecture

Lean (C#/.NET, also Python bindings) is a full-stack algorithmic trading engine powering QuantConnect. All user algorithms subclass **QCAlgorithm**, which is the single entry point coordinating four key subsystems:

- **Securities** — A dictionary of `Security` objects, one per subscribed asset. Each `Security` holds its price data, symbol resolution, and per-asset models (fill, fee, slippage, margin, buying-power). The security manager handles data subscription and universe selection.

- **Portfolio (SecurityPortfolioManager)** — Tracks every position as a `SecurityHolding` dictionary plus a **CashBook** (multi-currency cash ledger). It aggregates P&L, computes margin requirements, and provides total portfolio value. Cash is modeled as a transaction ledger across currencies (USD, BTC, etc.).

- **OrderTicket** — Returned on every order submission. It is a handle that allows the algorithm to track status (`OrderStatus`), receive `OrderEvent` notifications, update quantity/limit/stop prices, and cancel. Orders flow through `SecurityTransactionManager` which enforces validations and delegates to the brokerage handler.

- **BrokerageModel (IBrokerageModel)** — Defines how a brokerage handles fills, fees, slippage, settlement, and margin for each asset class. The default `DefaultBrokerageModel` can be overridden per algorithm. Each brokerage plugin (Interactive Brokers, etc.) ships its own model.

- **FillModel (IFillModel)** — Determines the actual fill price and quantity for orders during simulation. The brokerage model auto-sets the appropriate fill model per security type. Pre-built variants include `ImmediateFillModel`, `LatestPriceFillModel`, and equity-specific models that incorporate spread and market depth.

The engine runs a time-slice loop: data enters via the algorithm manager, your `QCAlgorithm` event handlers fire (`OnData`, etc.), orders are queued, and the transaction handler processes fills against the fill model and brokerage model.

## 2. Lean's Risk Management Primitives

Lean's **Algorithm Framework** provides a pluggable `IRiskManagementModel` interface:

`IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)`

It receives the current set of portfolio targets (from the alpha/portfolio-construction pipeline) and returns adjusted targets after evaluating risk constraints. Built-in models:

| Model | Behavior |
|---|---|
| `MaximumDrawdownPercentPortfolio` | Monitors total portfolio drawdown; liquidates all positions and cancels insights when threshold exceeded. |
| `MaximumDrawdownPercentPerSecurity` | Per-security unrealized drawdown guard; liquidates individual positions. |
| `MaximumUnrealizedProfitPercentPerSecurity` | Take-profit trigger when unrealized gain exceeds threshold. |
| `TrailingStopRiskManagementModel` | Trailing stop based on peak-to-trough drawdown of unrealized P&L per security. |
| `CompositeRiskManagementModel` | Combines multiple risk models; each transforms targets in sequence. |

Risk models run after portfolio construction and before execution. They have access to the full algorithm context (securities, portfolio, time) and can call `Liquidate()` or cancel insights.

## 3. Lumibot's Trader/Strategy/Broker Triad

Lumibot (pure Python) uses a clean three-component architecture:

- **Strategy** — Abstract base class with React-inspired lifecycle methods. Users override:
  - `initialize()` — Called once; set parameters, schedule, sleep time.
  - `on_trading_iteration()` — The core loop; called at each time step (minute, daily). All trading logic goes here.
  - `before_market_opens()`, `before_starting_trading()`, `on_filled_order()`, `on_canceled_order()`, etc. — Optional hooks.

- **Trader** — The orchestrator. It binds a Strategy to a Broker and a data source, then runs the main event loop. In backtest mode, it iterates through historical bars, calling lifecycle methods at each time slice. In live mode, it sleeps between iterations and polls for new data.

- **Broker** — Abstract interface (`Broker` base class). Concrete implementations map to real brokers: `Alpaca`, `InteractiveBrokers`, `Ccxt` (for crypto via CCXT). The broker handles order submission (`_submit_order`), position tracking, portfolio value, and connectivity. Strategy code never touches broker APIs directly.

**Event-driven loop:** Trader's `_run()` method advances through time. At each iteration: (1) fetch data for current timestamp, (2) call `on_trading_iteration()`, (3) process any order fills/cancellations, (4) update positions and cash, (5) advance clock. This is the same loop for both backtesting and live — only the data source and broker differ.

## 4. Multi-Asset Handling

**Lean** treats every instrument as a `Security` with a `Symbol` and a `SecurityType` enum (Equity, Option, Future, Crypto, Forex, Cfd, Index). The `CashBook` handles multi-currency settlement — buying European equities deducts EUR, crypto deducts BTC/USDT, etc. Margin models are per-security-type: equities use Reg-T, futures use SPAN-like models, options use portfolio margin. The brokerage model per algorithm or per security maps each asset type to the correct fill/fee/slippage behavior. One `QCAlgorithm` can simultaneously hold SPY shares, BTC perpetual swaps, SPX options, and ES futures — all with unified portfolio reporting.

**Lumibot** also supports multi-asset through an `Asset` type with an `asset_type` enum (`STOCK`, `OPTION`, `CRYPTO`, `FUTURE`, `FOREX`). The broker abstraction handles each asset class through the same `create_order()`/`submit_order()` interface. However, unlike Lean, Lumibot does not have a unified multi-currency cashbook or per-asset-class margin modeling — these are delegated to the individual broker implementations.

## 5. Best Ideas to Port (Python+TS Stack)

1. **Unified Security + Model-per-Asset pattern** — Lean's idea of a single `Security` object that carries its own fill model, fee model, slippage model, and margin model is elegant. A Python+TS stack can define a `SecurityConfig` interface and let each asset type plug in its own models via a registry.

2. **OrderTicket as a handle** — Returning an `OrderTicket` from every order submission (instead of raw order IDs) gives the strategy a stateful handle to track, update, and cancel. This is trivial to implement in both Python and TypeScript and dramatically simplifies order management.

3. **Composite risk model pipeline** — Lean's `CompositeRiskManagementModel` chains risk models sequentially. A Python+TS stack can implement the same: a `RiskPipeline` class that accepts an ordered list of `RiskModel` instances, each implementing `manage_risk(targets) -> targets`, and chains them before execution.

4. **Lifecycle method pattern from Lumibot** — Lumibot's React-inspired `initialize()` / `on_trading_iteration()` / lifecycle hooks are lightweight and easy to port. A TypeScript `Strategy` abstract class with the same hooks would feel native to both React and Python developers.

5. **Broker abstraction with same-loop backtest/live** — Both engines share this, but Lumibot's implementation is especially clean: the same `Trader._run()` loop drives both modes, swapping only the data source and broker. A TypeScript port can use dependency injection to achieve the same.

6. **CashBook for multi-currency** — Porting Lean's `CashBook` (a ledger of currency transactions) to Python/TS is straightforward and unlocks realistic multi-asset P&L tracking without per-currency hacks.
