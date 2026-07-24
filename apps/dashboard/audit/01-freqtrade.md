# Freqtrade Architecture Study — Patterns for a Next.js + Python Crypto Trading Agent

## 1. Top-Level Module Map

| Module | Responsibility |
|---|---|
| `freqtrade/` | Top-level orchestration: `FreqtradeBot` main loop, entry/exit execution, wallet/order management |
| `freqtrade/strategy/` | `IStrategy` abstract base class — contract all user strategies must implement |
| `freqtrade/exchange/` | Exchange abstraction via ccxt; `exchange.py` base class, `binance.py`/`bybit.py` subclasses override per-exchange logic |
| `freqtrade/optimize/` | Hyperopt, backtesting engine, edge analysis — optimization of strategy parameters |
| `freqtrade/persistence/` | SQLAlchemy ORM models for `Trade`, `Order`, `PairLock`; database migrations |
| `freqtrade/wallets/` | Wallet balance tracking, stake amount calculation, starting-balance management |
| `freqtrade/rpc/` | RPC message dispatch (Telegram, Webhooks, FastAPI REST + WebSocket); `RPC` helper class with all query logic |
| `freqtrade/plugins/protections/` | Plug-in risk guards: stop loss lookback, cooldown periods, max drawdown, low-profit pair locking |

---

## 2. FreqtradeBot Orchestration — The Main Loop

The `FreqtradeBot.process()` method is the heartbeat, called once per throttle interval (e.g., every 5 minutes):

```
process():
  1. exchange.reload_markets()
  2. update_trades_without_assigned_fees()
  3. refresh active whitelist from PairListManager
  4. dataprovider.refresh() — download latest OHLCV candles
  5. strategy.bot_loop_start()     ← strategy lifecycle hook
  6. strategy.analyze(whitelist)   ← runs populate_indicators + signal generation on all pairs
  7. manage_open_orders()          ← check timeouts, replace orders on new candle
  8. exit_positions(trades)        ← per-trade: check stoploss, ROI, exit signal, custom_exit
  9. process_open_trade_positions() ← DCA / position adjustment (if enabled)
 10. enter_positions()             ← iterate whitelist: check entry signal, protections, place order
 11. _schedule.run_pending()       ← scheduled tasks (funding fees, wallet snapshots)
 12. rpc.process_msg_queue()       ← flush WebSocket/dataframe messages
```

**Ordering is critical**: exits happen *before* entries. Within `exit_positions()`, the evaluation order is:
1. Stoploss on exchange check
2. Strategy `should_exit()` which checks: (a) exit signal + custom_exit, (b) stoploss, (c) ROI, (d) trailing stoploss

Protections are checked *after* trade close (`handle_protections()` in `order_close_notify`) and again *during* entry (`is_pair_locked()` in `create_trade()`).

---

## 3. IStrategy — The Strategy Contract

`IStrategy` is an ABC with `HyperStrategyMixin` that defines the full user-strategy contract:

### Required class attributes (config declarations):
- `timeframe: str` — e.g., `"5m"`, `"1h"`
- `stoploss: float` — e.g., `-0.10` for -10%
- `minimal_roi: dict` — e.g., `{"0": 0.01, "60": 0.005, "120": 0}`
- `max_open_trades: IntOrInf`

### Core abstract/overridable methods:
| Method | What it does |
|---|---|
| `populate_indicators(df, metadata)` | **Abstract**. Add all TA indicators to the DataFrame |
| `populate_entry_trend(df, metadata)` | Set `enter_long` / `enter_short` columns to 1 where entry is desired |
| `populate_exit_trend(df, metadata)` | Set `exit_long` / `exit_short` columns to 1 where exit is desired |
| `custom_stoploss(pair, trade, ...)` | Dynamic stoploss; returns negative ratio relative to current rate |
| `custom_exit(pair, trade, ...)` | Custom exit logic beyond candle signals; returns string reason or True |
| `confirm_trade_entry(...)` | Final gate before placing entry order (return False to veto) |
| `confirm_trade_exit(...)` | Final gate before placing exit order |
| `custom_entry_price(...)` | Override the calculated entry price |
| `custom_exit_price(...)` | Override the calculated exit price |
| `custom_stake_amount(...)` | Dynamic position sizing |
| `adjust_trade_position(...)` | DCA / position adjustment (positive=add, negative=reduce) |
| `informative_pairs()` | Declare additional pairs+timeframes to cache for cross-pair analysis |
| `bot_start()` / `bot_loop_start(...)` | Lifecycle hooks |

### How config is wired:
Strategy attributes (`timeframe`, `stoploss`, `minimal_roi`) are declared as class fields on the strategy, read by the bot at init. `order_types`, `order_time_in_force`, `trailing_stop`, `use_custom_stoploss`, `can_short`, `position_adjustment_enable` — all are strategy class-level flags the bot reads.

---

## 4. Exchange Abstraction

`freqtrade/exchange/exchange.py` (~4500 lines) is the monolithic base wrapping **ccxt**. Key structure:

```
Exchange (base, wraps ccxt)
├── Binance (binance.py)    — overrides timeframes, funding fee logic
├── Bybit (bybit.py)        — overrides pair parsing, order types
├── Kraken, OKX, etc.
└── ExchangeResolver.load_exchange(config) → instantiates the right subclass
```

**What subclasses override:**
- `_default_timeframes` — list of valid candle intervals
- `get_funding_fees()`, `get_max_pair_stake_amount()` — exchange-specific fee/stake limits
- `additional_exchange_init()` — exchange-specific websocket setup
- `validate_order_types()`, `validate_timeframes()` — exchange-specific constraints

**Key methods on the base:**
- `get_rate(pair, side, ...)` — fetch current bid/ask
- `create_order(pair, ordertype, side, amount, rate, ...)` — place order, handles dry_run internally
- `create_stoploss(pair, amount, stop_price, ...)` — stop-loss order
- `get_markets()`, `get_pair_quote_currency()`, `get_min_pair_stake_amount()` — market metadata
- `refresh_latest_ohlcv(pair_list)` — download candles, cache on disk

**Pattern**: The base class uses `self.dry_run` flag to simulate order fills vs. actually calling ccxt. The same `create_order()` method works in both modes.

---

## 5. Protections Plugin System

Located in `freqtrade/plugins/protections/`. All inherit from `IProtection(ABC)`:

| Protection | Scope | Signature / Config |
|---|---|---|
| `StoplossGuard` | Global + Local | `trade_limit` (default 10), `lookback_period`, `stop_duration`, `required_profit` (0.0), `only_per_pair`, `only_per_side` |
| `CooldownPeriod` | Local only | `lookback_period`, `stop_duration`, `stop_duration_candles` |
| `MaxDrawdown` | Global only | `max_allowed_drawdown`, `lookback_period`, `trade_limit`, `stop_duration`, `calculation_mode` ("ratios" or "equity") |
| `LowProfitPairs` | Local only | `trade_limit` (1), `required_profit` (0.0), `lookback_period`, `only_per_side` |

**Interface contract** (`IProtection`):
```
global_stop(date_now, side, starting_balance) → ProtectionReturn | None
stop_per_pair(pair, date_now, side, starting_balance) → ProtectionReturn | None
```
`ProtectionReturn` = `{lock: bool, until: datetime, reason: str, lock_side: "*"|"long"|"short"}`

**How they're wired**: Strategies declare `protections = [{"method": "StoplossGuard", "lookback_period": 60, ...}]`. `ProtectionManager` instantiates them. On every trade close, `handle_protections()` calls `global_stop()` and `stop_per_pair()` on all protections; results translate to `PairLocks` entries that block future entries.

---

## 6. Dry-Run vs. Backtest vs. Live

Freqtrade uses **a single `RunMode` enum** to switch behavior:

| Mode | How it works |
|---|---|
| `RunMode.LIVE` | Real exchange orders; wallet balance from exchange API; real fees; real slippage |
| `RunMode.DRY_RUN` | Same `process()` loop; orders go through `exchange.create_order()` but fill is simulated; wallets track virtual balances; same strategy code path |
| `RunMode.BACKTEST` | Separate `Backtesting` class; replays historical candle data; no live exchange calls; simulates fills at OHLCV prices; same strategy's `populate_*` methods used |

**Key insight**: The same `IStrategy` subclass works in all three modes without modification. The bot reads `self.config['dry_run']` and `self.config['runmode']` to toggle behavior. In dry-run, order fills are simulated but the bot processes them through the same `update_trade_state()` pipeline as live.

---

## 7. RPC / API Layer

The RPC stack has **three layers**:

### RPC (`freqtrade/rpc/rpc.py`)
The `RPC` class holds a reference to `FreqtradeBot` and exposes all query methods:
- `_rpc_trade_status()`, `_rpc_balance()`, `_rpc_trade_statistics()`, `_rpc_performance()`
- `_rpc_force_entry()`, `_rpc_force_exit()`, `_rpc_start()`, `_rpc_stop()`
- `_rpc_analysed_dataframe()` — returns OHLCV + indicators as JSON

### Message channels (`freqtrade/rpc/telegram.py`, `webhook.py`)
`RPCHandler` subclasses receive `RPCSendMsg` dicts. `RPCManager` broadcasts messages (entry fills, exit fills, protection triggers) to all enabled channels.

### FastAPI server (`freqtrade/rpc/api_server/`)
- `api_v1.py` — ~30 endpoints on two routers: `router_public` (`/ping`) and `router` (authenticated: `/status`, `/profit`, `/balance`, `/forcebuy`, `/forcesell`, `/whitelist`, `/blacklist`, `/locks`, `/show_config`, `/version`, `/health`, `/logs`, etc.)
- WebSocket endpoint for real-time streaming of analyzed dataframes
- **FreqUI** (React dashboard) consumes these REST + WebSocket endpoints to render charts, trade tables, config, and bot controls

---

## 5–10 Best Ideas to Port into a Next.js + Python Trading Agent

### 1. Adopt IStrategy-Style Hook Surface
Define a Python `TradingStrategy` ABC with `populate_indicators(df, metadata)`, `populate_entry_signal(df)`, `populate_exit_signal(df)`, `custom_stoploss(trade, current_rate, profit)`, `confirm_entry(...)`, and lifecycle hooks `on_bot_start()`, `on_loop_start()`. This gives users a single-file "drop in a strategy" experience identical to Freqtrade's UX.

### 2. Use a Protections Registry
Port Freqtrade's pluggable `IProtection` system: `global_stop()` / `stop_per_pair()` returning `{lock: bool, until, reason}`. Built-in protections (StoplossGuard, CooldownPeriod, MaxDrawdown, LowProfitPairs) can be JSON-configurable. Strategies declare protections as a config list. This is one of Freqtrade's most under-appreciated features.

### 3. Separate Entry from Exit in the Main Loop
Freqtrade's `process()` order — exit first, then enter — is battle-tested. In your agent: check stoploss/ROI/signal for open positions *before* evaluating new entries. Use a `should_exit()` method that evaluates in priority order: exit_signal → stoploss → ROI → trailing_stop.

### 4. Exchange Abstraction via a Base Class
Implement an `ExchangeBase` wrapping ccxt, with `BinanceExchange`, `BybitExchange` subclasses. The base handles `create_order()`, `get_rate()`, `fetch_ohlcv()` and accepts a `dry_run: bool` flag so the same code path works in paper-trading mode.

### 5. Single Strategy, Three Run Modes
Adopt Freqtrade's `RunMode` pattern (LIVE / DRY_RUN / BACKTEST) as an enum. The same strategy object works in all three. In the Next.js frontend, expose a mode toggle. Paper-trading uses the same `process()` loop but with simulated wallets and order fills.

### 6. RPC/WebSocket Architecture for the Dashboard
Freqtrade's `rpc/` layer is a great pattern: a Python `RPC` class holds the bot reference and exposes all query/mutation methods. Wrap this in FastAPI with REST endpoints for CRUD + WebSocket for real-time price/signal streaming. Your Next.js dashboard (like FreqUI) consumes these directly.

### 7. DataFrame-Centric Signal Pipeline
Freqtrade's `populate_indicators()` → `populate_entry_trend()` → `populate_exit_trend()` DataFrame pipeline is elegant. Every strategy returns a DataFrame with `enter_long`, `exit_long` columns. The bot just reads the last row. Simple, debuggable, backtest-friendly.

### 8. PairLock / Capital Protection Mechanism
`PairLocks` (database table + in-memory cache) that block specific pairs for a duration after a losing trade. The `lock_pair(pair, until, reason, side)` API from `IStrategy` lets strategies self-lock. The protections system auto-creates locks. This prevents revenge-trading cycles.

### 9. Position Adjustment / DCA Support
Freqtrade's `adjust_trade_position()` callback lets strategies return a positive or negative stake amount to add to or reduce a position. Combined with `max_entry_position_adjustment`, this enables grid-like or DCA strategies natively in the strategy layer.

### 10. Unfilled-Order Timeout + Replacement
Freqtrade's `manage_open_orders()` checks open limit orders every iteration: if timed out → cancel; if new candle → call `adjust_order_price()` on strategy to optionally replace at a new price. This is critical for limit-order strategies that must adapt to fast markets.
