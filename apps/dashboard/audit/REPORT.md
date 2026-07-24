# Trading Agent — Deep Architecture & UX Audit
**Date:** 2026-05-19
**Scope:** `~/.hermes/trading-agent/` (Next.js 16 + React 19 dashboard) + `~/.hermes/crypto-research/` (Python pipeline)
**Inputs:** Reference research on Freqtrade, Hummingbot, Jesse, Lean, Lumibot, FreqUI, Hyperliquid, Drift, GMX (see `audit/01-04*.md`)

---

## Executive Summary (10 wins)

**Architecture & code (the biggest gaps)**

1. **No strategy interface.** All trading logic is inlined in `trading_agent.py`. Steal Freqtrade's `IStrategy` ABC + Jesse's `before()` / `after()` / `on_open_position()` lifecycle. One file per strategy, hot-swappable.
2. **No exchange abstraction.** Direct CCXT calls scatter across the Python layer. Introduce `ExchangeBase` + `BinanceExchange`/`BybitExchange` subclasses with a `dry_run: bool` flag — same code path serves paper, dry-run, and live.
3. **No protections registry.** Freqtrade's `IProtection` (StoplossGuard, CooldownPeriod, MaxDrawdown, LowProfitPairs) is the gold-standard pattern for capital protection. Config-driven, declarative, audit-friendly. Port it directly.
4. **No `OrderTicket`.** Orders are dicts passed around as snapshots. Lean's `OrderTicket` (stateful handle with `status`, `update()`, `cancel()`) makes partial-fill, timeout, and replacement logic safe.
5. **`process()` loop ordering is wrong.** Currently entries are evaluated before exits. Freqtrade's battle-tested order: **exits first, then entries** — stoploss/ROI/signal evaluate before new capital is committed. Single-line refactor with massive risk reduction.

**Trading logic / risk**

6. **No unfilled-order timeout.** Limit orders that don't fill have no `manage_open_orders()` cleanup. Port Freqtrade's pattern: every tick, check open orders → cancel if timed out → optionally replace via `adjust_order_price()`.
7. **No PairLock equivalent.** After a stop-loss, nothing prevents the next tick from re-entering the same symbol. Add `PairLocks` (in-memory + persisted): lock_pair(symbol, until, reason).
8. **Equity curve was inflated** (already fixed in May 18 audit) — but the deeper fix is **per-symbol historical price snapshots** at fill time, not the global price map. The fix landed; document it as architecture.

**UX (operator clarity)**

9. **No persistent mode banner.** Operator can't tell at a glance whether the bot is in paper / dryrun / live. This is the single highest-leverage UX fix in the project. Even FreqUI and Hummingbot lack this — original synthesis from operator-safety patterns.
10. **Hub is verb-light, status-heavy.** Borrow Hummingbot's two-tier stop (graceful + force-stop icon), FreqUI's confirmation modals with phrase input for destructive actions, and Hyperliquid's terminal layout (order book L | chart C | positions/orders R) for the Execution page.

---

## Phase 1 — Current Architecture Map

### Stack
```
Frontend: Next.js 16.2.6, React 19.2.4, TypeScript 5, Tailwind 4, Turbopack
Backend:  Python 3.11, CCXT, sqlite (in ~/.hermes/crypto-research/memory/)
Tunnel:   Tailscale Funnel (thenampc-1.tail699983.ts.net) + topgoal.it.com redirect
Ports:    3002 (Next.js dev), proxy at 3099
```

### Component diagram

```
Operator → topgoal.it.com → Tailscale Funnel → localhost:3002 (Next.js)
                                                     │
                            ┌────────────────────────┼────────────────────────┐
                            │                        │                        │
                       /dashboard/*            /api/trading/*           SSE prices-stream
                       (8 pages,               (49 routes,              (reads live_prices.json
                        62 components)          force-dynamic)           from Python writer)
                                                     │
                                                     ▼
                                       execAsync → ~/.hermes/crypto-research/
                                                     │
                                       ┌─────────────┼─────────────┐
                                       ▼             ▼             ▼
                              trading_agent.py  research/   memory/trading.db
                              (orchestrator)    pipelines    (decisions, orders,
                                                              alerts, trades)
                                       │
                                       ▼
                              CCXT → Binance / Bybit / Kraken testnets
```

### Frontend route map
```
src/app/
├── page.tsx                          → /
├── dashboard/
│   ├── page.tsx                      → /dashboard         (Hub)
│   ├── signals/page.tsx              → /dashboard/signals
│   ├── execution/page.tsx            → /dashboard/execution
│   ├── portfolio/page.tsx            → /dashboard/portfolio
│   ├── risk/page.tsx                 → /dashboard/risk
│   ├── history/page.tsx              → /dashboard/history
│   ├── plan/page.tsx                 → /dashboard/plan
│   └── settings/page.tsx             → /dashboard/settings
└── api/trading/                       49 route handlers
```

### Data flow
```
ResearchPipeline.run(symbols)
      → writes reports/report_<ts>.json (signals, fundamentals, sentiment)
      → writes decisions/*.json
      → writes memory/trading.db (orders, positions, alerts)
      → writes live_prices.json (priceFeed tick)
                  │
                  ▼
  data.ts (getLatestReport / getDecisions / getDataStats)
                  │
                  ▼
  Page components (server-rendered) or fetch('/api/trading/...')
                  │
                  ▼
  SSE /api/trading/prices-stream → PriceTicker live updates
```

### What's right today
- Clean hub-and-spoke route topology
- Good component organization (`src/components/trading/`)
- TypeScript-strict, build passes clean
- After May 18 audit: atomic writes, force-dynamic on every route, no shell injection, no path traversal, no stderr→stdout contamination, `paths.ts` + `auth.ts` helpers, dead-code purged

### What's wrong
- **No strategy interface.** `trading_agent.py` mixes orchestration with strategy logic with risk checks with exchange I/O. ~1500 lines of god-class.
- **No exchange abstraction.** CCXT calls happen in 8+ files. Live/paper/dryrun branching is duplicated.
- **No backtest harness.** A full Jesse-style backtest with Sharpe/Sortino/Calmar metrics doesn't exist — only manual replay via `/api/trading/replay`.
- **Order lifecycle is implicit.** No `OrderTicket` class; orders are dicts in JSON files. Partial fills, cancellations, and replacements have no first-class state.
- **Protections are inline.** Drawdown checks, kill-switch logic live in `trading_agent.py` and `circuit-breaker/route.ts`. Not pluggable.
- **49 API routes is too many.** Many overlap (`status` vs `pipeline-status` vs `health`; `decisions` vs `decisions/typed`). Needs consolidation behind an `RPC` class.
- **No WebSocket layer.** SSE works for prices but not for trade-fills, alerts, or signal updates. Frontend polls.

---

## Phase 2 — Extracted Patterns from Reference Projects

(Full reports in `audit/01-freqtrade.md`, `audit/02-hummingbot-jesse.md`, `audit/03-lean-lumibot.md`, `audit/04-ux-patterns.md`. Synthesized highlights:)

### From Freqtrade — adopt verbatim
- **`FreqtradeBot.process()` loop:** check pending orders → check exits → check entries → update positions → handle protections → notify. **Exit-first is the rule.**
- **`IStrategy` ABC:** `populate_indicators(df)` → `populate_entry_signal(df)` → `populate_exit_signal(df)` → `custom_stoploss(trade, rate, profit)` + lifecycle (`on_bot_start`, `on_loop_start`, `confirm_entry`, `confirm_exit`).
- **DataFrame-centric pipeline:** every strategy returns a pandas DataFrame with `enter_long` / `exit_long` boolean columns. The bot reads the last row. Trivially backtestable.
- **`IProtection` plugin system:** `global_stop(date_now, side, balance) → ProtectionReturn | None`. Built-in: StoplossGuard, CooldownPeriod, MaxDrawdown, LowProfitPairs. Drives `PairLocks` table.
- **`RunMode` enum:** `LIVE | DRY_RUN | BACKTEST`. Same strategy class works in all three. The bot reads `config['runmode']` and toggles wallet/exchange/fill behavior.

### From Hummingbot — adopt selectively
- **`PositionExecutor`:** Triple Barrier (TP / SL / TTL / trailing stop) as an independent per-trade state machine. Decouples entry signal from exit management.
- **`RateOracle` singleton:** 18+ source priority, cached rates, reverse-pair inversion. Lets your agent price wrapped/synthetic tokens correctly.
- **`ConnectorBase` split:** spot connectors (`Exchange`) vs perpetual (`Derivative` + `PerpetualTrading` mixin). Funding-rate logic lives in the perpetual layer only.

### From Jesse — adopt for ergonomics
- **`before()` / `after()` / `on_open_position(order)` / `on_close_position()` lifecycle hooks** — additive to Freqtrade's callbacks.
- **`self.liquidate()` one-liner** + dynamic TP/SL via `update_position()` per-bar.
- **Backtest metrics suite:** Sharpe, Sortino, Calmar, Omega, Serenity, max DD, max underwater period, win/loss streaks, expectancy, profit factor, holding period.

### From Lean — adopt the contract shape
- **`OrderTicket`:** stateful handle (`status`, `quantity`, `filled`, `update()`, `cancel()`). Replaces dict-passing.
- **`RiskManagementModel.ManageRisk()` pipeline** + `CompositeRiskManagementModel` to chain protections.
- **`BrokerageModel` / `FillModel`** — per-asset-class behavior (crypto vs equity vs option).

### From FreqUI + Hummingbot + Hyperliquid — UX
- **Drag-resize grid layout** (FreqUI uses `vue-grid-layout`; in React use `react-grid-layout`).
- **Persistent mode banner** at top (`PAPER` / `DRY-RUN` / `LIVE` — color-coded, dismissible only with phrase confirmation).
- **Two-tier stop:** graceful STOP button + emergency force-stop icon. Hummingbot pattern.
- **Confirmation modal with phrase input** (FreqUI `useConfirmBox`): "Type DELETE-LIVE to switch out of paper mode."
- **Hyperliquid terminal layout** for Execution page: order book left | chart center | positions+orders right.
- **P&L Calendar Heatmap** (GitHub-contribution-style) for History page.

---

## Phase 3 — Gap Analysis (Prioritized)

| # | Gap | Risk | Effort | Tier |
|---|-----|------|--------|------|
| 1 | `process()` loop evaluates entries before exits | **HIGH** — can over-commit capital before stoploss fires | S | **P0** |
| 2 | No `IStrategy` interface; logic in one 1500-line god class | HIGH — hard to test, hard to add strategies | L | P1 |
| 3 | No `ExchangeBase` abstraction; CCXT scattered | HIGH — paper/live drift, double-implementation | M | **P0** |
| 4 | No `OrderTicket`; orders are JSON dicts | MED — partial-fill state is implicit | M | P1 |
| 5 | No `IProtection` registry; risk checks inline | HIGH — adding a new protection requires editing god class | M | **P0** |
| 6 | No `manage_open_orders()` timeout/replacement | MED — limit orders can dangle | S | **P0** |
| 7 | No `PairLocks` — can re-enter immediately after stop | HIGH — revenge-trading on volatile pairs | S | **P0** |
| 8 | No backtest harness with Sharpe/Sortino/Calmar | MED — can't validate strategy changes | L | P2 |
| 9 | 49 API routes; many duplicates and inconsistencies | LOW — works but maintenance burden | M | P2 |
| 10 | No persistent mode banner in UI | HIGH — operator can confuse paper for live | S | **P0** |
| 11 | No graceful + emergency stop split | MED — single button is risky | S | P1 |
| 12 | No confirmation phrase for live-mode actions | HIGH — single click can trigger real order | S | **P0** |
| 13 | Hub mixes status + actions; no information hierarchy | MED — operator decision speed | M | P1 |
| 14 | History page lacks calendar heatmap + per-strategy breakdown | LOW | M | P2 |
| 15 | No WebSocket for fills/alerts; frontend polls | LOW | M | P2 |
| 16 | No `RateOracle`; prices read from one source | MED — vulnerable to stale price | M | P2 |

---

## Phase 4 — Code-Level Proposals

### P0.1 — Reorder `process()` to exit-first (1-day change)

**File:** `~/.hermes/crypto-research/trading_agent.py`

**Before (current — pseudo):**
```python
def process(self):
    self._check_halt_conditions()
    self._evaluate_signals()      # generates new entry candidates
    self._enter_positions()        # places entries
    self._manage_positions()       # checks exits/stops on existing
    self._update_state()
```

**After (Freqtrade pattern):**
```python
def process(self):
    self._check_halt_conditions()
    self._manage_open_orders()     # NEW — timeout/replace unfilled
    self._exit_positions()         # exits BEFORE new capital commits
    self._handle_protections()     # NEW — IProtection pipeline
    if not self.is_locked():
        self._enter_positions()
    self._update_state()
    self._notify()
```

**Acceptance:** in unit test, when a position hits stoploss AND a new signal fires in same tick, stoploss executes first.

---

### P0.2 — `ExchangeBase` abstraction (2–3 days)

**New files:**
```
crypto-research/exchanges/
├── __init__.py
├── base.py              ← ExchangeBase
├── binance.py           ← BinanceExchange(ExchangeBase)
├── bybit.py             ← BybitExchange(ExchangeBase)
└── paper.py             ← PaperExchange(ExchangeBase) — simulates fills
```

**`base.py` skeleton:**
```python
from abc import ABC, abstractmethod
from typing import Optional
import ccxt

class ExchangeBase(ABC):
    name: str
    supports_perpetual: bool = False

    def __init__(self, api_key: str, secret: str, dry_run: bool = False):
        self.dry_run = dry_run
        self._client = self._build_client(api_key, secret)

    @abstractmethod
    def _build_client(self, api_key: str, secret: str) -> ccxt.Exchange: ...

    def create_order(self, symbol: str, side: str, amount: float,
                     price: Optional[float], order_type: str = "market") -> "OrderTicket":
        if self.dry_run:
            return self._simulate_fill(symbol, side, amount, price, order_type)
        raw = self._client.create_order(symbol, order_type, side, amount, price)
        return OrderTicket.from_ccxt(raw, exchange=self.name)

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200): ...

    @abstractmethod
    def fetch_ticker(self, symbol: str): ...

    def _simulate_fill(self, *args, **kwargs) -> "OrderTicket":
        # paper fill at last ticker price + configurable slippage
        ...
```

**Migration:** replace every direct `ccxt.binance()` call in pipelines with `self.exchange.create_order(...)`. The bot instantiates `ExchangeBase` once at startup based on `config['exchange']`.

---

### P0.3 — `IProtection` registry (3 days)

**New files:**
```
crypto-research/protections/
├── __init__.py
├── iprotection.py       ← ABC
├── stoploss_guard.py
├── cooldown_period.py
├── max_drawdown.py
└── low_profit_pairs.py
```

**`iprotection.py`:**
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class ProtectionReturn:
    lock: bool
    until: datetime
    reason: str
    lock_side: str = "*"   # "*", "long", "short"

class IProtection(ABC):
    has_global_stop = False
    has_local_stop = False

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def global_stop(self, date_now, side, starting_balance) -> Optional[ProtectionReturn]: ...

    @abstractmethod
    def stop_per_pair(self, pair, date_now, side, starting_balance) -> Optional[ProtectionReturn]: ...
```

**Config in `strategies/my_strategy.py`:**
```python
protections = [
    {"method": "StoplossGuard", "lookback_period": 60, "trade_limit": 4, "stop_duration": 60},
    {"method": "CooldownPeriod", "stop_duration": 30},
    {"method": "MaxDrawdown", "max_allowed_drawdown": 0.10, "lookback_period": 480},
]
```

**Wiring:** `ProtectionManager` reads strategy config → instantiates protections → bot's `_handle_protections()` calls each and writes results to `PairLocks` table.

---

### P0.4 — `IStrategy` interface (1 week, can ship incrementally)

**New file:** `crypto-research/strategy/interface.py`

```python
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd

class IStrategy(ABC):
    # Class-level config (Freqtrade pattern)
    timeframe: str = "1h"
    stoploss: float = -0.10      # -10%
    minimal_roi: dict = {"0": 0.05}
    process_only_new_candles: bool = True

    # --- mandatory ---
    @abstractmethod
    def populate_indicators(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame: ...

    @abstractmethod
    def populate_entry_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        # must set df['enter_long'] / df['enter_short'] boolean columns
        ...

    @abstractmethod
    def populate_exit_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        # must set df['exit_long'] / df['exit_short']
        ...

    # --- optional callbacks ---
    def custom_stoploss(self, trade, current_rate, current_profit) -> Optional[float]:
        return None

    def confirm_entry(self, pair, order_type, amount, rate, time_in_force, current_time) -> bool:
        return True

    def confirm_exit(self, pair, trade, order_type, amount, rate, time_in_force, exit_reason) -> bool:
        return True

    # --- lifecycle (Jesse pattern) ---
    def on_bot_start(self): pass
    def on_loop_start(self): pass
    def on_open_position(self, trade): pass
    def on_close_position(self, trade): pass
```

**Migration path:** wrap current logic in a `LegacyAggregateStrategy(IStrategy)` first. Then split BTC, ETH, SOL strategies into separate classes. Zero behavior change in step 1.

---

### P1.1 — `OrderTicket` (2 days)

**New file:** `crypto-research/orders/ticket.py`

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List
import uuid

class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"

@dataclass
class OrderTicket:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    exchange: str = ""
    symbol: str = ""
    side: str = ""              # "buy" | "sell"
    type: str = "market"        # "market" | "limit"
    amount: float = 0.0
    price: Optional[float] = None
    filled: float = 0.0
    avg_fill_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    exchange_order_id: Optional[str] = None
    fees: List[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def update(self, **kwargs): ...
    def cancel(self, exchange): exchange.cancel_order(self.id, self.symbol)
    @classmethod
    def from_ccxt(cls, raw: dict, exchange: str) -> "OrderTicket": ...
```

Persist to `memory/trading.db` `orders` table with one row per ticket. Replace all dict-passing in API routes (`execution`, `close-position`, `orders`) with `OrderTicket.from_db(id)` / `.to_dict()`.

---

### P0.5 — `manage_open_orders()` + `PairLocks` (2 days)

**`manage_open_orders` skeleton (in trading_agent.py):**
```python
def _manage_open_orders(self):
    for ticket in OrderTicket.list_open():
        age = (datetime.utcnow() - ticket.created_at).total_seconds()
        timeout = self.config.get("unfilledtimeout", {}).get(ticket.side, 300)
        if age > timeout and ticket.status == OrderStatus.OPEN:
            self.exchange.cancel_order(ticket)
            new_price = self.strategy.adjust_order_price(ticket)
            if new_price:
                self.exchange.create_order(
                    symbol=ticket.symbol, side=ticket.side,
                    amount=ticket.amount - ticket.filled,
                    price=new_price, order_type="limit"
                )
```

**`PairLocks` table:**
```sql
CREATE TABLE pair_locks (
  id INTEGER PRIMARY KEY,
  pair TEXT NOT NULL,
  side TEXT NOT NULL,         -- '*' | 'long' | 'short'
  reason TEXT,
  locked_until DATETIME NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

`is_pair_locked(pair, side)` → boolean. Called at the top of `_enter_positions()`.

---

## Phase 5 — UI/UX Proposals (Port 3002)

### Top priority: persistent mode banner (P0)

**Component:** `<ModeBanner />` rendered in `src/app/dashboard/layout.tsx` (above sidebar nav).

```tsx
// src/components/trading/mode-banner.tsx
const colorMap = {
  paper:  "bg-slate-700 text-slate-100",
  dryrun: "bg-amber-600 text-amber-50",
  live:   "bg-red-600 text-white animate-pulse",
};
const labelMap = {
  paper:  "PAPER TRADING — no real orders",
  dryrun: "DRY RUN — simulated fills on live prices",
  live:   "LIVE TRADING — REAL MONEY",
};

export function ModeBanner({ mode }: { mode: "paper"|"dryrun"|"live" }) {
  return (
    <div className={`px-4 py-2 text-sm font-mono text-center ${colorMap[mode]}`}>
      {labelMap[mode]}
    </div>
  );
}
```

Wire to existing `/api/trading/mode` GET. Renders on every dashboard page.

---

### Page-by-page wireframes (text)

**Hub (`/dashboard`)** — declutter current implementation
```
+-----------------------------------------------------------+
| MODE BANNER (color-coded)                                 |
+-----------+-----------------------------------------------+
| Sidebar   | KPI strip: Daily P&L | Open Positions | Exposure | Win Rate
|           +-----------------------------------------------+
| Hub       | LEFT col:  Equity curve (last 30d)           |
| Signals   | RIGHT col: Bot status card (uptime, mode,    |
| Execution |            last decision, next tick)          |
| Portfolio +-----------------------------------------------+
| Risk      | Open Trades table (≤8 rows, click→drilldown) |
| History   +-----------------------------------------------+
| Plan      | Recent Activity Feed (fills, signals, alerts)|
| Settings  +-----------------------------------------------+
+-----------+ Bottom row: 4 group cards (Signals | Execution | Risk | History)
```

**Execution (`/dashboard/execution`)** — Hyperliquid terminal layout
```
+----------------------+----------------------+--------------------+
| Order Book Ladder    | Candle Chart         | Order Entry Card   |
| (bids/asks +         | (lightweight-charts) | (mkt/lmt/TP/SL)    |
|  depth)              | + trade markers      | confirmation modal |
|                      |                      +--------------------+
|                      |                      | Positions Table    |
|                      |                      | (Symbol|Side|Size| |
|                      |                      |  Entry|Mark|PnL|   |
|                      |                      |  Liq.|Actions)     |
|                      |                      +--------------------+
|                      |                      | Open Orders Table  |
+----------------------+----------------------+--------------------+
| Executor Monitor: per-strategy cards (PnL, volume, status)       |
+------------------------------------------------------------------+
```

**Risk (`/dashboard/risk`)** — operator-grade safety
```
+-------------------+-------------------+-------------------+
| Drawdown Gauge    | Margin Utiliza.  | Distance to Liq.  |
| (semicircle,      | (%, color zones) | (closest position)|
|  green/yellow/red)|                  |                   |
+-------------------+-------------------+-------------------+
| Circuit Breaker Panel: list of breakers (tripped/active) |
+----------------------------------------------------------+
| Position Risk Breakdown: sortable table                  |
| Position | Lev | Liq. Px | Mark | Distance % | Risk Score|
+----------------------------------------------------------+
| Active Protections (from IProtection registry)           |
| StoplossGuard | CooldownPeriod | MaxDrawdown | LowProfit |
+----------------------------------------------------------+
| Active PairLocks (with countdown until expiry)           |
+----------------------------------------------------------+
```

**History (`/dashboard/history`)** — calendar heatmap
```
+----------------------+----------------------+
| Filter Bar: date range | pair | strategy   |
+----------------------+----------------------+
| Trade History Table  | P&L Calendar        |
| (paginated, exports  |  Heatmap            |
|  CSV)                | (rows=months,        |
|                      |  cols=days)         |
+----------------------+----------------------+
| Stats row: Total | Win% | Avg W | Avg L | PF | Sharpe | Sortino |
+--------------------------------------------------------------+
```

**Plan (`/dashboard/plan`)** — strategy + backtest launcher
```
+----------------------+----------------------+----------------------+
| Strategy Card Grid   | Parameter Form       | Backtest Launcher    |
| (one per strategy,   | (when card selected) | Date range, capital, |
|  Deploy/Backtest btns| grouped sections     | run button           |
+----------------------+----------------------+----------------------+
| Backtest Results: equity curve | metrics table | trade list        |
+----------------------------------------------------------------+
```

---

### Safety patterns (apply globally)

1. **Confirmation modal with phrase input** for: switching to LIVE, force-close all positions, kill switch, deleting strategies. Type `DELETE-LIVE` or `CONFIRM-CLOSE-ALL` to proceed.
2. **Two-tier stop:** `<StopButton>` in header for graceful (finishes current tick); `<EmergencyStop>` icon button for immediate. Different colors, different confirmations.
3. **Alert toast system:** SSE feed of fills, protections triggered, halt conditions. Auto-dismiss for info; sticky for warnings/errors. Already have `lib/toast.tsx`; wire to a new `/api/trading/alerts-stream` SSE.
4. **Inline validation everywhere:** order size > balance → red border + tooltip "Exceeds available balance ($X)". Leverage > max_leverage → similar.

---

## Phase 6 — Prioritized Roadmap

### Tier 1 — Low-hanging fruit (1–2 weeks)
- [ ] **P0.1** Reorder `process()` loop — exit-first (1 day)
- [ ] **P0.5a** Add `manage_open_orders()` for unfilled-limit cleanup (1 day)
- [ ] **P0.5b** Add `PairLocks` table + `is_pair_locked()` check (2 days)
- [ ] **UI** Persistent `<ModeBanner />` (4 hours)
- [ ] **UI** Confirmation modal with phrase input for LIVE-mode actions (1 day)
- [ ] **UI** Two-tier stop button in header (4 hours)
- [ ] **UI** Drawdown gauge component on Risk page (1 day)

### Tier 2 — Medium (2–4 weeks)
- [ ] **P0.2** `ExchangeBase` + `BinanceExchange`/`BybitExchange`/`PaperExchange` (1 week)
- [ ] **P0.3** `IProtection` registry with 4 built-in protections (1 week)
- [ ] **P1.1** `OrderTicket` first-class class + DB table + migration (3 days)
- [ ] **UI** Execution page Hyperliquid layout refactor (1 week)
- [ ] **UI** P&L Calendar Heatmap on History page (2 days)

### Tier 3 — Deep refactor (4–8 weeks)
- [ ] **P0.4** `IStrategy` ABC + extract current logic into `LegacyAggregateStrategy` → split into `BtcTrendStrategy`, `EthTrendStrategy`, `SolTrendStrategy` (2 weeks)
- [ ] **P2** Full Jesse-style backtest harness with Sharpe/Sortino/Calmar/Omega/Serenity metrics (2 weeks)
- [ ] **P2** Consolidate 49 API routes behind an `RPC` class (1 week)
- [ ] **P2** WebSocket layer for fills/alerts (replacing polling) (1 week)
- [ ] **P2** `RateOracle` singleton with 3+ sources (3 days)

---

## Follow-up Tickets

```
T1. fix(orchestrator): reorder process() loop — exit before entry
T2. feat(orders): add unfilled-order timeout + replacement via manage_open_orders()
T3. feat(risk): add PairLocks table and check on every entry
T4. feat(ui): persistent ModeBanner above dashboard layout
T5. feat(ui): phrase-confirmation modal for live-mode actions
T6. feat(ui): two-tier stop (graceful + emergency) in dashboard header
T7. feat(ui): drawdown gauge component on /dashboard/risk
T8. refactor(exchange): introduce ExchangeBase + Binance/Bybit/Paper subclasses
T9. feat(protections): IProtection registry with StoplossGuard, CooldownPeriod, MaxDrawdown, LowProfitPairs
T10. refactor(orders): OrderTicket class + DB migration + replace dict-passing
T11. refactor(strategy): IStrategy ABC + LegacyAggregateStrategy wrapper
T12. feat(strategy): split LegacyAggregateStrategy into per-asset strategies
T13. feat(ui): Execution page — Hyperliquid terminal layout (orderbook L | chart C | positions/orders R)
T14. feat(ui): P&L Calendar Heatmap on /dashboard/history
T15. feat(backtest): Jesse-style metrics suite (Sharpe, Sortino, Calmar, Omega, Serenity, max DD, streaks)
T16. refactor(api): consolidate 49 routes behind RPC class with typed handlers
T17. feat(rt): WebSocket layer for fills + alerts; deprecate polling
T18. feat(market-data): RateOracle singleton with multi-source price resolution
```

---

## Assumptions to validate before starting

- **Exchange list:** confirm only Binance / Bybit / Kraken testnets matter. If you plan to add DEXs (Hyperliquid, Drift, GMX), `ExchangeBase` needs to support on-chain order flow too.
- **Asset class:** spot only? Or perpetuals? Hummingbot's `PerpetualTrading` mixin matters if the latter.
- **Strategy count:** today everything looks like one aggregate strategy across BTC/ETH/SOL. Should they be 3 strategies or 1 multi-asset strategy? (Reference: Freqtrade favors 1 strategy × many pairs; Jesse favors 1 strategy × 1 route per route.)
- **Backtest data source:** CCXT OHLCV download is fine for testnet validation. For production-grade backtests, need a tick or 1m-OHLCV archive (CryptoLake, Tardis, or local CCXT-pulled archive).
- **Auth model:** currently `TRADING_MASTER_KEY` header check. If you plan to expose this beyond Tailscale (e.g., topgoal.it.com without ACLs), you need session auth + audit log + 2FA on live-mode actions.

---

## Appendix

- Full reference reports: `audit/01-freqtrade.md`, `audit/02-hummingbot-jesse.md`, `audit/03-lean-lumibot.md`, `audit/04-ux-patterns.md`
- Current-code samples: `audit/00-current-code-sample.txt`
- Architecture snapshot: `/tmp/audit-bundle/architecture.txt`
- Previous bug-fix plan (May 18, completed): `BUG_FIX_PLAN.md`
