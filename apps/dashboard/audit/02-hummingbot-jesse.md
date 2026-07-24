# Hummingbot & Jesse: Deep-Dive Architectural Analysis

## 1. Hummingbot Strategy V2 — Controller / Executor Framework

Hummingbot's Strategy V2 separates *decision-making* (Controllers) from *execution* (Executors), creating a composition-friendly, event-driven architecture.

### Core Primitives

**ExecutorBase** (`hummingbot/strategy_v2/executors/executor_base.py`) is the abstract root. It manages a `RunnableStatus` lifecycle (NOT_STARTED → RUNNING → SHUTTING_DOWN → TERMINATED), registers event forwarders for all order-creation/fill/cancel/failure events against registered connectors, and delegates to subclasses via `control_task()`. It handles retry counting and max-retry evaluation, with a `CloseType` enum capturing the termination reason (TAKE_PROFIT, STOP_LOSS, TIME_LIMIT, TRAILING_STOP, EXPIRED, INSUFFICIENT_BALANCE, FAILED, EARLY_STOP, POSITION_HOLD, COMPLETED).

**PositionExecutor** implements the **Triple Barrier Method**: Stop Loss, Take Profit, and Time Limit, plus an optional Trailing Stop. Key design:
- Places an open order (market/limit/limit-maker) with activation-bounds checking (capital-efficient — skips placement if mid-price is outside configurable bounds).
- Once filled, monitors three simultaneous barriers in `control_barriers()`.
- Tracks three order slots: `_open_order`, `_close_order`, `_take_profit_limit_order`.
- Spot vs perpetual: uses `is_perpetual_connector()` to decide between `PerpetualOrderCandidate` (with leverage) and `OrderCandidate` for balance validation.
- Exposes PnL as `trade_pnl_pct`, `net_pnl_pct`, `cum_fees_quote`, with separate entry/close price tracking.

**ArbitrageExecutor** manages cross-exchange arbitrage: buys on one market, sells on another. It validates token interchangeability (ETH/WETH, BTC/WBTC, USDC/USDC.E, stablecoin group), computes profitability accounting for fees and gas (AMM connectors), uses the `RateOracle` for quote-asset conversion when markets settle in different quote currencies, and places simultaneous market orders when `min_profitability` threshold is met.

**ControllerBase** (`hummingbot/strategy_v2/controllers/controller_base.py`) is the strategic brain:
- `determine_executor_actions()` → returns `List[ExecutorAction]` (Create/Stop) pushed onto an `asyncio.Queue`.
- Built-in `buy()`/`sell()`/`cancel()`/`cancel_all()` convenience methods that create `PositionExecutorConfig` or `OrderExecutorConfig` objects.
- Comprehensive `ExecutorFilter` system for querying executors by ID, connector, pair, type, status, side, PnL range, and timestamp — all with AND/OR logic.
- Holds `positions_held: List[PositionSummary]` and `executors_info: List[ExecutorInfo]` for full state introspection.

**MarketMakingControllerBase** extends ControllerBase for two-sided order-book liquidity:
- Configurable `buy_spreads[]` and `sell_spreads[]` with corresponding `buy_amounts_pct[]`/`sell_amounts_pct[]`.
- `executor_refresh_time` replaces stale non-trading executors; `cooldown_time` prevents immediate re-entry after stop-loss.
- Automatic position rebalancing for spot: compares required base asset (derived from sell-quote amounts) vs current position, places a market `OrderExecutor` when the difference exceeds `position_rebalance_threshold_pct`.
- Each level creates a `PositionExecutorConfig` with the `TripleBarrierConfig`, giving every MM level its own TP/SL/TTL/trailing-stop.

### Controller ↔ Executor Split

Controllers produce actions; a parent `StrategyV2Base` script consumes them, instantiates executors, and relays `executor_info` back to controllers. This allows multiple controllers to run in one process (via `v2_with_controllers.py`), each managing independent executors on different pairs/connectors.

## 2. Hummingbot ConnectorBase Exchange Abstraction

**ConnectorBase** (`hummingbot/connector/connector_base.py`) is the abstract interface that all spot (`Exchange`) and perpetual (`Derivative`) connectors inherit. Key responsibilities:
- Price queries: `get_price_by_type(pair, PriceType)`, `get_quote_price()`, `get_order_book()`.
- Order placement: `buy()`/`sell()` with position-action semantics.
- Balance: `get_balance()`, `get_available_balance()`.
- Fee retrieval: `get_fee()` returns a `TradeFeeBase` (either `AddedToCostTradeFee` or `DeductedFromReturnsTradeFee`).
- `BudgetChecker` validates orders against balances and fee impact.

### Spot vs Perpetual Connectors

| Aspect | Spot | Perpetual |
|--------|------|-----------|
| **Directory** | `hummingbot/connector/exchange/` | `hummingbot/connector/derivative/` |
| **Base class** | `Exchange` (→ `ConnectorBase`) | `Derivative` (→ `ExchangeBase` + `PerpetualTrading`) |
| **Position model** | One-directional (buy→sell closes) | HEDGE or ONEWAY mode; `PositionAction.OPEN`/`CLOSE` |
| **Leverage** | Not applicable | Configurable via `PerpetualTrading` mixin |
| **Funding rate** | N/A | Tracked by `OrderBookTrackerDataSource` on perp connectors |
| **Balance check** | `OrderCandidate` | `PerpetualOrderCandidate` (adds leverage, position-side) |
| **OrderBook source** | `*_order_book_data_source.py` | Same, but also tracks funding info |

Each connector contains: `OrderBookTracker` (maintains real-time order book per pair), `UserStreamTracker` (user account state, open orders, positions), `ClientOrderTracker` (tracks `InFlightOrder` objects), and an optional `Auth` class for HMAC-signing REST/WS requests.

## 3. Hummingbot RateOracle + Smart Order Routing

**RateOracle** (`hummingbot/core/rate_oracle/rate_oracle.py`) is a singleton providing conversion rates for any token pair. Architecture:

- **Configurable source**: Defaults to `BinanceRateSource`, with 18+ supported sources (CoinGecko, CoinCap, KuCoin, Gate.io, Coinbase, Hyperliquid, MEXC, and perp-specific sources like Aevo, Evedex, Pacifica, Decibel).
- **Connector fallback**: `register_connector()` adds live connector order books as secondary price sources. `_get_rate_from_connectors()` iterates sorted connectors and reads mid-price (or its inverse for reverse pairs).
- **Multi-hop rate resolution**: `get_pair_rate(pair)` tries: (1) cached source rate on direct pair, (2) connector live order books (direct and reverse), (3) cached source rate on reverse pair inverted.
- **Asynchronous price loop**: `_fetch_price_loop()` polls the source and updates `_prices` dict; sets `_ready_event` once populated.
- **Usage in ArbitrageExecutor**: `get_quote_asset_conversion_rate()` calls `rate_oracle.get_pair_rate()` to normalize sell prices when buying and selling markets use different quote assets (e.g., M3M3/USDT vs M3M3/SOL → fetches SOL/USDT).

The architecture enables cross-exchange and cross-chain arbitrage without hard-coded pairs.

## 4. Jesse's Strategy Class API

Jesse's `Strategy` class (`jesse/strategies/Strategy.py`) is an ABC with a remarkably clean surface:

### Entry Logic
- `should_long()` → bool (abstract, must override)
- `should_short()` → bool (optional)
- `go_long()` → set `self.buy` = `(qty, price)` or `[(qty, price), ...]` for multiple entries
- `go_short()` → set `self.sell` similarly
- `should_cancel_entry()` → bool (default True on new candle)

### Built-in Helpers
- `self.buy` / `self.sell` — entry order tuples; framework auto-detects market/limit/stop based on price vs current price
- `self.stop_loss` / `self.take_profit` — exit orders; set as tuples
- `self.liquidate()` — immediately close position with a MARKET order (sets stop_loss or take_profit to `(position.qty, self.price)`)
- `self.log(msg)` — unified logging

### Lifecycle Hooks
- `before()` — called before strategy logic each bar
- `after()` — called after strategy logic each bar
- `on_open_position(order)` — fired when entry fills
- `on_close_position(order, closed_trade)` — fired when position closes, receives `ClosedTrade` object
- `on_increased_position(order)` / `on_reduced_position(order)` — partial-fill hooks
- `on_cancel()` — after all orders cancelled
- `update_position()` — called on each bar while position is open (for dynamic TP/SL)
- `terminate()` — cleanup hook

### Multi-route Awareness
- `on_route_open_position(strategy)`, `on_route_close_position(strategy)`, etc. — cross-route event broadcasting
- `self.routes` — list of all `Route` objects; `self.all_positions` — dict of symbol→Position

### Filters
- `filters()` → list of callables; all must return True for entry to proceed. A clean, composable pre-trade check system.

### ML Integration
- `ml_features()` → dict — define features once; used by both data-gathering and inference modes
- `ml_predict()` / `ml_predict_proba()` — lazy-loads model/scaler, runs inference
- `record_features()`, `record_label()`, `export_ml_data()` — training data pipeline

### Routes Config
A `Route` is simply: `Route(exchange="Binance", symbol="BTC-USDT", timeframe="1h", strategy_name="MyStrategy", dna="...")`. Routes are defined in `routes.py` and can include multiple strategies running on multiple exchanges/symbols/timeframes simultaneously. The `config.py` sets exchange balances, fees, leverage, futures leverage mode, warmup candles, and optimization objectives.

## 5. Jesse's Research Mode (Backtest Harness)

Jesse's backtest engine (`jesse/services/metrics.py`) produces a comprehensive metrics dict from `ClosedTrade` list and `daily_balance` array:

**Core Performance Metrics:**
| Metric | Calculation |
|--------|-------------|
| **CAGR** | `(cum_return)^(1/years) - 1` annualized |
| **Sharpe Ratio** | `mean(daily_returns) / std(daily_returns) * sqrt(365)` |
| **Sortino Ratio** | `mean(daily_returns) / downside_deviation * sqrt(365)` |
| **Calmar Ratio** | `CAGR / max_drawdown` |
| **Omega Ratio** | Sum of positive excess returns / sum of negative excess returns |
| **Serenity Index** | `total_return / (Ulcer_Index * CVaR_penalty)` |
| **Max Drawdown** | Largest peak-to-trough decline in cumulative returns |
| **Max Underwater Period** | Longest consecutive days below previous equity peak |
| **Win Rate** | winning_trades / total_trades (also split by long/short) |

**Trade-Level Metrics:** Total trades, gross profit/loss, net profit (absolute and %), average win/loss, ratio avg win/loss, expectancy, expectancy %, expected net profit per 100 trades, average holding period (winning/losing/overall), largest winning/losing trade, winning/losing streak, current streak, longs/shorts count and percentage, total fees.

**Additional:** Hyperparameter DNA display, open-position PnL at backtest end, average trades per day/week/month.

The optimization mode uses Optuna with configurable objective function (sharpe, calmar, sortino, omega, serenity, smart-sharpe, smart-sortino).

---

## 5–7 Best Ideas to Port (Complementing Freqtrade)

1. **Triple Barrier Executor as a Position-Management Plugin** — Freqtrade's exit logic is primarily signal-based (sell-signal or ROI). Porting Hummingbot's PositionExecutor with independent TP/SL/TTL/trailing-stop barriers would give users a dramatic upgrade in risk management without changing entry logic. Each trade gets its own barrier tracker; useful for strategies that want fixed-risk-per-trade.

2. **RateOracle for Cross-Exchange Pricing** — Freqtrade currently has no built-in multi-source rate oracle. Porting Hummingbot's RateOracle pattern (singleton + configurable source + connector-fallback + reverse-pair inversion) would enable freqtrade to price assets on DEXs, handle wrapped-token equivalence, and support cross-exchange strategies. This is *additive*, not duplicative.

3. **Jesse-style `before()` / `after()` / `on_open_position()` Hooks** — Freqtrade's callback chain (`populate_indicators` → `populate_entry_signal` → `populate_exit_signal` → `custom_stoploss`) is rigid. Jesse's per-bar lifecycle with `before()`, `after()`, and granular position-state hooks (`on_open_position`, `on_increased_position`, `on_reduced_position`, `update_position`) would let users run stateful per-bar logic without monkey-patching. The pattern is purely additive to existing callbacks.

4. **Executor Orchestrator for Multi-Strategy Concurrency** — Hummingbot's `ExecutorOrchestrator` pattern (multiple controllers each managing their own executor pool, running in one process) would let freqtrade run independent strategies (e.g., trend-following on BTC + grid on ETH + arbitrage on SOL) within a single bot instance, each with isolated PnL tracking. Freqtrade currently runs one strategy at a time.

5. **Jesse's `self.liquidate()` + Dynamic TP/SL via `update_position()`** — Freqtrade's `custom_stoploss` is called per-candle but doesn't support dynamic take-profit or one-click liquidation. Porting Jesse's `self.liquidate()` and the `update_position()` pattern (where users can modify `self.stop_loss`/`self.take_profit` on each bar while in position) would be a low-effort, high-impact win.

6. **Comprehensive Research Metrics Dashboard (Calmar, Omega, Serenity, Underwater Period)** — Freqtrade's backtesting reports Sharpe, Sortino, max drawdown, and win rate, but lacks Calmar, Omega, Serenity Index, max underwater period, and streak analysis. Porting Jesse's full metrics suite (as an add-on to `freqtrade optimize` output) complements existing reporting without duplication.

7. **Configurable Activation Bounds for Entry Orders** — Hummingbot's activation-bounds pattern (only place a limit order when mid-price is within a configurable band of the target price) is capital-efficient. Porting this as a pre-entry filter in freqtrade would prevent capital from being locked in stale limit orders far from market — a common pain point in grid and DCA strategies.
