# Trading Dashboard UX Pattern Guide

A synthesis of leading trading dashboard UI patterns distilled from FreqUI, Hummingbot Dashboard, Hyperliquid, Drift, and GMX — to inform the design of a Next.js hub-and-spoke dashboard.

---

## 1. FreqUI (freqtrade/frequi)

**Tech stack:** Vue.js + PrimeVue + vue-grid-layout + Pinia stores. FreqUI connects to the Freqtrade REST API and renders a fully drag-and-drop resizable dashboard.

### Trade Table (TradeList.vue)
A dense data table with columns: ID | Pair | Amount | Stake Amount | Open Rate | Current/Close Rate | Profit % | Open Date | [Actions]. Active trades show "Current profit %" and "Current rate"; closed trades show "Profit %", "Close date", and "Close Reason." Each row supports right-click or popover actions: Force Exit, Force Exit (partial), Cancel Open Order, Delete Trade, Reload Trade, Force Entry. Multi-bot mode prepends a "Bot" column and clicking a row navigates to that bot's trading view.

**Pattern:** Row-click = drilldown into trade detail. Destructive actions gated behind confirmation modals via `useConfirmBox()`.

### Daily P&L Heatmap (PeriodBreakdown.vue)
A bar-chart widget showing profit/loss aggregated by day/week/month with green/red coloring. In multi-bot mode, it overlays all bots. The DashboardView fetches `allGetDaily({ timescale: 30 })` on mount. Provides the "at a glance" daily performance summary.

### Per-Pair Drilldown (PairSummary.vue)
A table of all whitelisted pairs with columns for current profit, open trades count, and lock status. Each pair is clickable to drill into that pair's chart and trade history. Paired with a PairLockList for managing locked pairs.

### Strategy Selector (StrategySelect.vue)
A dropdown populated from the bot's available strategies. Changing the strategy typically requires a ReloadConfig (gated behind a confirm dialog). This component sits in the settings or multi-pane area.

### Dry-Run / Live Toggle
FreqUI distinguishes modes by the bot's `isTrading` state (derived from API). The UI disables start/stop controls when not in trading mode. There is no persistent mode banner — this is a gap. The bot's state (`running` vs `stopped`) is shown in BotStatus but the dry-run/live distinction is not prominently surfaced.

### Structure: Drag-Resize Grid Dashboard
Both `DashboardView` and `TradingView` use `GridLayout` (vue-grid-layout) with 12-column responsive breakpoints. Each widget is a `GridItem` inside a `DraggableContainer` with a header bar. Users can lock/unlock the layout. The TradingView splits into: Multi Pane (tabs), Open Trades, Closed Trades, Trade Detail, Chart — each independently resizable.

---

## 2. Hummingbot Dashboard (hummingbot/dashboard)

**Tech stack:** Streamlit (Python) — migrating to a Condor-based browser dashboard. Current version uses Streamlit's sidebar navigation and widget system.

### Strategy Controller Cards
The Config page displays each strategy as a card with a name (e.g., "PMM Simple", "Bollinger V1", "XEMM Controller"), description, and expandable parameter form. Parameters are grouped into logical sections (General, Risk Management, Spread Configuration). Each card is self-contained — users select a strategy card, fill parameters, then save the configuration.

### Executor Monitor (Instances Page)
Active instances display as a metrics card row at the top: Net PNL (Quote), Net PNL (%), Volume Traded, Liquidity Placed, Unrealized PNL, Imbalance. Below, an "Active Controllers" table lists: ID | Controller | Connector | Trading Pair | Realized PNL ($) | Unrealized PNL ($) | Net PNL ($) | Volume ($). Each row is a live executor instance.

### Performance Pane
The Portfolio page features a sunburst chart for allocation visualization, plus line graphs for portfolio evolution over time and per-token value evolution. The backtest page shows equity curves and trade breakdowns.

### Stop Mechanism
Two-tier stop: (1) STOP button next to "Active Controller" — gracefully closes positions, allows restart; (2) small square stop icon at top right — force-stops Docker container, cancels all orders. This dual-stop pattern is an excellent safety model.

### Navigation
Sidebar pages: Credentials → Portfolio → Config → Backtest → Deploy → Instances. Linear workflow mirrors the bot lifecycle.

---

## 3. Hyperliquid / Drift / GMX — Live DEX Trading UIs

### Order Book Layout (Hyperliquid)
Professional CEX-style terminal layout:
- **Left panel:** Order book (bid/ask ladder) + recent trades tape
- **Center:** Candlestick chart (TradingView-powered) with drawing tools
- **Right panel:** Order entry form (Market/Limit/TP/SL) above positions table
- **Bottom:** Open orders + order history + trade history tabs

Hyperliquid's on-chain CLOB supports limit, market, TWAP orders with reduce-only toggle and TP/SL triggers directly in the entry form.

### Positions Table Density (Drift)
Drift's positions table is compact and scan-optimized:
Market | Side | Size | Entry Price | Mark Price | PnL | Liq. Price | Leverage | Actions
Each row uses color coding (green = profit, red = loss) and supports quick-actions: Close, Add collateral, Edit TP/SL.

### Alert Toasts (GMX)
GMX uses real-time notification toasts for:
- Trade executed (amount, price, side)
- TP/SL triggered
- Liquidation warning (with remaining margin %)
- Governance announcements
Toasts appear top-right, auto-dismiss after 5-8 seconds, with a bell icon to view history.

### Leverage & Risk Controls (Hyperliquid)
- Leverage slider (1x to 50x) with color gradient (green→yellow→red)
- Cross vs Isolated margin toggle with tooltip explanation
- Liquidation price displayed prominently near the entry form
- "Reduce Only" checkbox for risk-limiting orders

---

## 4. Operator-Grade Safety Patterns

### Confirmation Modals for Live Orders
**FreqUI pattern:** Every destructive action (force exit, delete trade, cancel order, stop bot, reload config) triggers `useConfirmBox()` — a modal with title, description, message, and confirm/cancel buttons. The `confirmText` defaults to "Confirm." Force exit dialogs show trade ID, pair, and order type.

**Recommended implementation:** Use a two-step confirm for high-stakes actions (e.g., "Force Exit ALL trades" should require typing a confirmation phrase like "EXIT ALL").

### Kill-Switch Placement
**Best pattern (synthesized from Hummingbot + FreqUI):**
- **Primary kill-switch:** Prominent red "EMERGENCY STOP" button fixed in the top navigation bar — always visible, never behind a menu. Clicking it immediately cancels all open orders and force-closes all positions after a single confirmation.
- **Secondary stop:** Per-instance/controller stop button in the execution monitor for granular control.
- **Circuit breaker indicators:** Visual status of any tripped circuit breakers (price bands, max drawdown, position limits) shown as a banner below the nav.

### Mode Banner (Paper / Dry-Run / Live)
Neither FreqUI nor Hummingbot currently implements a persistent mode banner well. This is a critical missing pattern.

**Recommended:** A fixed banner at the top of every page (below nav, above content) that shows:
- **PAPER TRADING** — gray banner, subtle text: "Paper Mode — no real funds at risk"
- **DRY-RUN** — blue banner: "Dry Run — simulated trading only"
- **LIVE** — red banner with pulsing dot icon: "LIVE TRADING — real funds at risk"

The banner should persist across all pages and only be dismissible temporarily (reappears on page reload). The LIVE banner should be deliberately anxiety-inducing, similar to production database banners in admin tools.

### Hot-Reload Safety
**FreqUI's ReloadControl.vue** gates config reload behind a confirm modal that warns: "Reload configuration (including strategy)?" This prevents accidental strategy changes during live trading. 

**Recommended extension:** When in LIVE mode, hot-reload should require: (1) confirm modal, (2) 5-second countdown button, (3) optional confirmation phrase. During the countdown, a warning toast shows which parameters will change.

---

## 5. Information Hierarchy: Hub vs Detail Page

### Hub View (Dashboard / Landing)
What belongs here:
- **Aggregated P&L** — daily, weekly, monthly (PeriodBreakdown pattern from FreqUI)
- **Active position count + total exposure** (card row at top)
- **Bot/strategy comparison** — side-by-side P&L cards (BotComparisonList)
- **Recent trade log** — last 20 trades across all bots/strategies
- **Alerts summary** — recent toasts consolidated into a widget
- **Quick actions** — Start All / Stop All buttons (no drilldown needed)
- **Portfolio allocation** — sunburst or treemap (Hummingbot pattern)
- **Mode banner** — most prominent here (landing page)

### Detail Pages
What belongs in detail:
- **Signals page:** Per-pair signal list with entry reason, confidence, timeframe, strategy name. Expandable rows show indicator values that triggered the signal.
- **Execution page:** Live order book + chart + order entry (Hyperliquid pattern). Open orders table. Per-instance executor monitor (Hummingbot pattern).
- **Portfolio page:** Full position breakdown, allocation charts, P&L over time, per-asset performance.
- **Risk page:** Current drawdown, margin utilization, liquidation proximity, circuit breaker status, max position limits, current exposure by asset.
- **History page:** Full trade history with advanced filters (date range, pair, strategy, outcome), export to CSV, P&L calendar heatmap.
- **Plan page:** Strategy configuration forms (Hummingbot card pattern), backtest launcher, parameter optimization queue.
- **Settings page:** API keys, exchange connections, notification preferences, theme, layout presets.

---

## 6. Concrete UI Proposals (Hub-and-Spoke Dashboard)

The dashboard uses a persistent left sidebar navigation (7 pages + Hub) with a top navbar containing the emergency stop button and mode banner.

### Page 1: Hub (`/`)
**Borrows from:** FreqUI DashboardView + Hummingbot Portfolio

```
TOP BAR:      [EMERGENCY STOP] | MODE BANNER (LIVE/DRYRUN/PAPER) | [Notifications Bell]
LEFT SIDEBAR: Hub | Signals | Execution | Portfolio | Risk | History | Plan | Settings
CONTENT:
  ┌──────────────────────────────────────────────────────────────┐
  │ [Daily P&L: +$1,234.56 ▲2.3%] [Open Positions: 4] [Exposure: $45K] [Win Rate: 68%] │  ← MetricCards row
  ├───────────────────────┬──────────────────────────────────────┤
  │ Profit Over Time      │ Bot Comparison                       │
  │ (bar chart, daily)    │ (side-by-side cards: Bot A +3%,      │
  │                       │  Bot B -0.5%, Bot C +1.2%)           │
  ├───────────────────────┼──────────────────────────────────────┤
  │ Open Trades (table)   │ Cumulative P&L (line chart)          │
  │ Pair | Size | PnL |   │                                     │
  │ Entry | Current       │                                     │
  ├───────────────────────┴──────────────────────────────────────┤
  │ Recent Activity Feed (trade executions, signal triggers, alerts)  │
  └──────────────────────────────────────────────────────────────┘
```

### Page 2: Signals (`/signals`)
**Borrows from:** FreqUI PairSummary + StrategySelect

```
left col: Strategy Selector dropdown + Timeframe selector
center: Signal Table (Pair | Signal Type | Confidence | Price | Indicators | Actions)
right col: Selected signal detail — candle chart with entry/exit markers, indicator values
```

Component: `SignalTable` — sortable, filterable by pair/strategy/timeframe. Each row expands to show the full indicator snapshot that generated the signal.

### Page 3: Execution (`/execution`)
**Borrows from:** Hyperliquid order book layout + FreqUI TradingView + Hummingbot Instances

```
left col: Order Book Ladder (bids/asks with depth visualization)
center: Candle Chart (TradingView or lightweight-charts) with trade markers
right col:
  ┌─────────────────────┐
  │ Order Entry Card    │ ← Market/Limit/TP/SL, leverage slider, reduce-only
  ├─────────────────────┤
  │ Open Positions Table│ ← Pair | Side | Size | Entry | Mark | PnL | Liq. | Actions
  ├─────────────────────┤
  │ Open Orders Table   │ ← Order ID | Pair | Type | Price | Filled | Status
  └─────────────────────┘
bottom: Executor Monitor — per-controller cards with PnL, volume, status (Hummingbot pattern)
```

Component: `OrderEntryCard` with leverage slider (color-coded), cross/isolated toggle, TP/SL inline fields, reduce-only checkbox. `ExecutorCard` showing controller name, pair, realized/unrealized PnL, uptime.

### Page 4: Portfolio (`/portfolio`)
**Borrows from:** Hummingbot Portfolio sunburst + FreqUI WalletHistory

```
left col: Allocation Sunburst (account → exchange → token)
center: Portfolio Value Over Time (line chart) + Asset Breakdown Table
right col: Per-Token Performance (mini bar charts) + Unrealized PnL by asset
```

Component: `AllocationSunburst` (interactive, click to drilldown). `PortfolioTimeline` with configurable date range.

### Page 5: Risk (`/risk`)
**Borrows from:** Hyperliquid liquidation display + operator safety patterns

```
left col: Risk Summary Cards (Current Drawdown | Margin Utilization % | Distance to Liquidation | Max Drawdown Limit)
center: Exposure Heatmap (by asset, by strategy) + Circuit Breaker Status Panel
right col: Position Risk Breakdown (table: Position | Leverage | Liq. Price | Current Price | Distance % | Risk Score)
```

Component: `DrawdownGauge` — semi-circular gauge showing current drawdown vs max allowed (green/yellow/red zones). `CircuitBreakerPanel` — list of breakers with tripped/active status, threshold, current value.

### Page 6: History (`/history`)
**Borrows from:** FreqUI TradeList + PeriodBreakdown

```
top: Filter Bar (Date Range Picker | Pair Selector | Strategy Selector | Outcome: Win/Loss/All)
left col: Trade History Table (all columns from FreqUI + export button)
right col: P&L Calendar Heatmap (day cells colored by P&L, like GitHub contribution graph)
bottom: Performance Stats (Total Trades | Win Rate | Avg Win | Avg Loss | Profit Factor | Sharpe)
```

Component: `PnLCalendarHeatmap` — rows = months, columns = days, color intensity = profit/loss magnitude. Green for profit, red for loss. `TradeExportButton` for CSV download.

### Page 7: Plan (`/plan`)
**Borrows from:** Hummingbot Config strategy cards + FreqUI BacktestRun

```
left col: Strategy Card Grid — each card shows strategy name, description, parameters summary, last backtest result
center: Parameter Configuration Form (when a card is selected) — grouped parameter sections
right col: Backtest Launcher — Time Range Select | Starting Capital | Run Button | Backtest Results (equity curve, stats table)
```

Component: `StrategyCard` with expand/collapse, "Deploy" button, "Backtest" button. `BacktestResultSummary` showing equity curve, total return, max drawdown, Sharpe ratio, win rate, total trades.

### Page 8: Settings (`/settings`)
**Borrows from:** FreqUI SettingsView + Hummingbot Credentials

```
single-column form layout:
  - Exchange Connections (API key management with masked keys)
  - Notification Preferences (Telegram, Email, Webhook)
  - Theme & Display (Dark/Light, chart defaults, layout presets)
  - Risk Limits (Max position size, max drawdown %, daily loss limit)
  - Mode Switch (PAPER / DRY-RUN / LIVE) with confirm dialog
  - Layout Lock/Unlock toggle
```

Component: `ModeSwitch` — segmented control with confirmation modal when switching to LIVE. Requires confirmation phrase input when leaving LIVE mode.

---

## Summary of Borrowed Patterns

| Proposal Component | Primary Pattern Source |
|---|---|
| Drag-resize grid dashboard | FreqUI GridLayout |
| MetricCards row (Hub top) | FreqUI DashboardView / Hummingbot Instances |
| Trade table with row-click drilldown | FreqUI TradeList |
| P&L Calendar Heatmap | FreqUI PeriodBreakdown (conceptual extension) |
| Bot Comparison cards | FreqUI BotComparisonList |
| Strategy Controller Cards | Hummingbot Config page |
| Order Book + Chart + Positions split | Hyperliquid terminal layout |
| Order Entry Card with leverage slider | Hyperliquid / Drift |
| Executor Monitor table | Hummingbot Instances page |
| Sunburst allocation chart | Hummingbot Portfolio page |
| Drawdown Gauge + Circuit Breaker Panel | Original synthesis from operator safety patterns |
| Mode Banner (persistent) | Original — fills gap in both FreqUI and Hummingbot |
| Two-tier stop (graceful + emergency) | Hummingbot Instances stop pattern |
| Confirmation modals with phrase input | FreqUI useConfirmBox (extended) |
| Alert toast system | GMX real-time notifications |
| Strategy Selector dropdown | FreqUI StrategySelect |
| Backtest launcher + results | FreqUI BacktestRun / BacktestResultAnalysis |

All patterns are implementable in Next.js with shadcn/ui components (Table, Card, Dialog, Toast, Select, Tabs, Slider, Switch, etc.) and a charting library like lightweight-charts or recharts.
