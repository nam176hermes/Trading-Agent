# Trading Agent — Deep Audit & Improvement Plan
**Date:** 2026-05-20  
**Goal:** Make the agent actually earn money

---

## 1. CURRENT PERFORMANCE (Brutal Truth)

| Metric | Value | Verdict |
|--------|-------|---------|
| Win rate | **7.7%** (1/13 trades) | ❌ Catastrophic |
| Total PnL | **-23.13%** | ❌ Capital destruction |
| Avg hold duration | **0 days** | ❌ No trades held |
| Best trade | +1.06% DOGE | ✅ |
| Worst trade | -5.15% LINK | ❌ |
| Active equity | $100,016 / $100,000 | ~ Flat |

A coin flip would beat this (50% win rate). The agent is systematically losing.

---

## 2. CRITICAL BUGS (Fix These First — They're Losing Money Now)

### BUG 1: Duplicate Signal Processing — Race Condition
**Severity:** CRITICAL  
**Evidence:** Database shows 5 TON sell orders executed within 14 seconds at the same timestamp (2026-05-17 02:24:30 to 02:24:43), all at identical prices. This wiped out the entire TON position with slippage 5x.

**Root cause:** `trading_agent.py` calls `_get_signals()` which runs `sync_new()` (pipeline_to_db bridge), then reads `db.get_recent_signals(limit=20)`. Multiple signals for the same asset arrive in the same batch. The duplicate guard (`db.get_position(symbol)`) only checks for existing positions, but after the first SELL the position qty becomes 0, so subsequent SELLs from the same batch also execute.

**Fix:** Add a per-cycle `already_processed` set in `_tick()`:
```python
# In _tick(), before the signal loop:
executed_symbols: set[str] = set()

# In _process_signal():
if symbol in executed_symbols:
    db.mark_signal_processed(signal["id"])
    return
executed_symbols.add(symbol)
```

Also add a DB-level deduplication: before executing any trade, check if an order for this symbol was created in the last 60 seconds.

---

### BUG 2: Wrong-Direction Signals — Selling Oversold Assets
**Severity:** CRITICAL  
**Evidence:** ADA signal (2026-05-20): RSI=43.7, StochRSI=oversold, CCI=-81.7 → `suggestion: SELL`. AVAX: RSI=46.1, StochRSI=oversold → `suggestion: SELL`. These assets are in oversold territory — the agent is selling exactly when it should hold or buy.

**Root cause:** The LLM (DeepSeek) receives TA data and is making poor signal quality decisions because:
1. No trend filter: `price_vs_sma200 = "below"` → the agent should require being ABOVE SMA200 to BUY, but doesn't require being above SMA200 to SELL short (it has no short positions)
2. No RSI floor for sells: selling with RSI < 50 in paper mode (where we can only sell what we own, not short) is always wrong — if we're in a long, RSI 43 with StochRSI oversold means DON'T SELL, wait for recovery
3. The signal filter pipeline (`signal_filters.py` `apply_signal_filters`) exists with multi-timeframe checks but is **never called** in the main execution path

**Fix:** Add hard TA gates in `assembly.py` before the signal is written to DB:
```python
# For LONG-only paper mode, these gates must pass for SELL signals:
# - RSI < 35 (truly oversold exit for defensive stop) OR
# - Stop-loss hit OR
# - Take-profit hit
# NEVER sell on RSI 43-50 — that's the middle of the range

def apply_long_only_gates(signal: str, ta: dict) -> str:
    """In long-only paper mode, prevent premature exits."""
    if signal != "SELL":
        return signal
    rsi = ta.get("rsi_14", 50)
    # Don't sell if we're in oversold or neutral territory (likely to bounce)
    if rsi < 50 and ta.get("stochrsi_signal") == "oversold":
        return "HOLD"
    return signal
```

---

### BUG 3: Equity Tracker Ignores Realized PnL
**Severity:** HIGH  
**Evidence:** `total_equity` shows $100,016 (unrealized from ETH), but positions table shows:
- TON realized: -$605
- LINK realized: -$592  
- SOL realized: -$0.68
- BTC realized: +$3
Total realized: **-$1,194** which is invisible to equity tracking.

**Root cause:** `_snapshot_equity()` in `trading_agent.py` calls `db.get_pnl_summary()` but the positions table's `realized_pnl` column is not included in that summary. The equity appears flat but the agent has actually lost $1,194.

**Fix:** Fix `_snapshot_equity()` to read realized PnL directly from positions:
```python
def _snapshot_equity(self):
    positions = db.get_positions()
    # Sum realized PnL from positions table directly
    realized = sum(p.get("realized_pnl", 0) or 0 for p in positions)
    unrealized = sum(
        (p.get("quantity", 0) or 0) * ((p.get("current_price", 0) or 0) - (p.get("avg_entry_price", 0) or 0))
        for p in positions if (p.get("quantity", 0) or 0) > 0
    )
    total_equity = self.capital + realized + unrealized
    ...
```

---

### BUG 4: price_at_decision Always 0.0
**Severity:** HIGH  
**Evidence:** Every decision in `decisions.jsonl` has `"price_at_decision": 0.0`.

**Root cause:** The field is being set before the price is fetched, or the price dict key format doesn't match (e.g., the signal uses "BTC" but price dict has "BTC/USDT").

**Fix:** In `main.py` where decisions are stored, explicitly pass the current market price:
```python
store_decision({
    ...
    "price_at_decision": market_data.get("price", 0) or ta_data.get("close", 0),
})
```

---

### BUG 5: Reflection Loop Never Runs
**Severity:** MEDIUM  
**Evidence:** All 13 decisions have `"reflected": false`. The agent is not learning from its mistakes.

**Root cause:** `main.py --mode reflect` must be called separately. The continuous `trading_agent.py` loop never triggers reflection. There's no cron job or scheduled reflection.

**Fix:** Add reflection to the cron schedule OR add a reflection trigger after every N trades in `trading_agent.py`.

---

## 3. LOGIC FLAWS (Signal Quality Issues)

### FLAW 1: No Trend Confirmation Gate
**Problem:** Signals are generated without requiring trend alignment. Buying assets that are in a downtrend (below SMA200) leads to fighting the dominant trend.

**Required gates for BUY:**
- Price must be above SMA-50 (medium trend)
- OR price bouncing from SMA-200 with RSI < 35 (deep oversold bounce)
- ADX > 20 (trending market, not ranging)
- MACD line > MACD signal (momentum aligned)

**Required gates for SELL (in long-only mode = close position):**
- RSI > 65 (overbought exit) OR
- Stop-loss triggered OR  
- Take-profit triggered OR
- MACD bearish crossover + RSI < 50 (momentum reversal)

### FLAW 2: No Multi-Timeframe Confirmation
**Problem:** `signal_filters.py` has excellent multi-timeframe confirmation code (4H/1D/1W) but it's **never called** in production. All signals are from a single timeframe snapshot.

**Evidence:** `_HAS_SIGNAL_FILTERS = True` in assembly.py, but the `apply_signal_filters` call requires `ta_4h`, `ta_1d`, `ta_1w` dicts that aren't being collected.

**Fix:** Collect 3 timeframes in `data_collector.py` and gate every entry signal through `multi_timeframe_confirm()`.

### FLAW 3: Position Sizing Is Static (Not Risk-Adjusted)
**Problem:** `MAX_PER_TRADE_PCT = 0.05` (5% fixed) regardless of:
- Volatility (ATR)
- Signal confidence
- Current drawdown
- Market regime

A 5% position in LINK (high volatility) has the same size as 5% in BTC — this is wrong.

**Fix:** Use ATR-based Kelly Criterion (which already exists in `atr_stops.py`!):
```python
# Volatility-adjusted position size
atr_pct = ta.get("atr_pct", 2.0)  # % ATR
risk_per_trade = 0.01  # risk 1% of capital per trade
stop_distance = atr_pct * 2  # stop 2x ATR from entry
position_size = risk_per_trade / (stop_distance / 100)
position_size = min(position_size, MAX_PER_TRADE_PCT)
```

### FLAW 4: Minimum Confidence Too Low
**Problem:** `MIN_SIGNAL_CONFIDENCE = 0.65` lets in signals at 65% confidence. The signal data shows many trades executed at exactly 0.65 confidence (the boundary) with no stop-loss targets.

**Reality check:** In any trading system, 65% confidence LLM signals are essentially noise. You need 75%+ with supporting TA evidence.

**Fix:** Raise `MIN_SIGNAL_CONFIDENCE` to `0.72` and require that the signal also has a valid stop_loss set.

### FLAW 5: No Market Regime Filter
**Problem:** The system trades in all market conditions — risk-on, risk-off, ranging. In ranging markets (ADX < 20), RSI/MACD signals generate noise.

**Fix:** Gate all BUY signals through regime detector:
- `TRENDING` + `risk_on` → allow BUY signals
- `RANGING` → only allow mean-reversion plays (oversold bounce with RSI < 30)
- `risk_off` regime → reduce all position sizes by 50%, stop new BUY entries

---

## 4. ARCHITECTURE ISSUES

### ARCH 1: Pipeline Disconnect (Research vs Execution)
**Problem:** Two disconnected systems:
1. `main.py` runs the research pipeline → writes to JSONL files
2. `trading_agent.py` runs the executor → reads from SQLite via `pipeline_to_db.py` sync

This is fragile. The sync mechanism is a "safety net" that runs every tick. When the research pipeline is run manually, signals can arrive in large batches and trigger duplicate trades.

**Fix:** Refactor so `main.py` writes directly to the SQLite `signals` table via `db.insert_signal()`, not to JSONL. Remove the `pipeline_to_db.py` safety-net sync.

### ARCH 2: No Live Feedback Loop
**Problem:** The research pipeline (main.py) doesn't know about current positions when generating signals. It might generate a BUY for ETH when ETH is already 5% of the portfolio.

**Fix:** Pass current positions context to the LLM when generating signals:
```python
# In main.py signal generation:
positions = db.get_positions()
position_context = format_positions_for_llm(positions)
# Inject into analyst prompts
```

### ARCH 3: No Trade Journal Analysis
**Problem:** `memory/trade_journal.jsonl` exists but is never used for pattern analysis. The reflection engine can't learn what types of signals lead to wins vs losses.

**Fix:** After every 10 trades, run an analysis:
- Which TA conditions preceded wins?
- Which conditions preceded losses?
- Update `SOUL.md` with learned rules

---

## 5. IMPROVEMENT ROADMAP

### Phase 1: Stop the Bleeding (Week 1) 🚨

**Priority: Fix bugs that are losing money right now.**

| Task | File | Impact |
|------|------|--------|
| Fix duplicate signal execution | `trading_agent.py:_tick()` | Stops the 5x-sell bug |
| Fix wrong-direction sells (add RSI gate) | `assembly.py` | Stops selling oversold |
| Fix equity tracker (include realized PnL) | `trading_agent.py:_snapshot_equity()` | Accurate P&L tracking |
| Raise MIN_SIGNAL_CONFIDENCE to 0.72 | `trading_agent.py` | Fewer low-quality trades |
| Add 60-second dedup gate per symbol | `trading_agent.py:_process_signal()` | Belt+suspenders dedup |

**Expected outcome:** Win rate improves from 7.7% to ~35-45%, losses stop cascading.

---

### Phase 2: Signal Quality (Weeks 2-3) 📊

**Priority: Only enter trades with strong multi-factor confirmation.**

| Task | File | Impact |
|------|------|--------|
| Activate multi-timeframe filter | `assembly.py` + `data_collector.py` | Requires 4H+1D+1W alignment |
| Add trend gate (SMA-50/SMA-200) | `signal_filters.py` | No buying in downtrends |
| Add regime filter | `assembly.py` | No BUY in risk-off/ranging |
| Add ADX > 20 gate for trend entries | `signal_filters.py` | No trend trades in choppy market |
| Wire ATR-based position sizing | `trading_agent.py:_process_signal()` | Risk-adjusted sizing |
| Fix price_at_decision = 0.0 | `main.py` | Accurate performance tracking |

**Expected outcome:** Win rate ~50-55%, average win > average loss.

---

### Phase 3: Learning Loop (Weeks 3-4) 🧠

**Priority: Make the agent smarter over time.**

| Task | File | Impact |
|------|------|--------|
| Fix reflection loop (run after every 5 trades) | `trading_agent.py` | Agent learns from mistakes |
| Trade journal analysis (win/loss pattern) | new: `journal_analyzer.py` | Identify what works |
| Update SOUL.md with learned rules | `memory.py` | Rules reinforcement |
| Add backtesting gate (new signals must pass 30d backtest) | `backtest_gate.py` | Filter bad strategies |
| Walk-forward validation before live deployment | `walk_forward.py` | Out-of-sample validation |

---

### Phase 4: Alpha Generation (Month 2) 💰

**Priority: Find genuine edge.**

| Task | Description | Impact |
|------|-------------|--------|
| Funding rate arbitrage | When funding rate > 0.1%/8h, short perps, long spot | ~4-8% monthly if executed well |
| On-chain accumulation signal | Large wallet accumulation → lead indicator for BTC | 60%+ accuracy historically |
| Sentiment divergence plays | Price down + sentiment improving = buy signal | Mean-reversion edge |
| Earnings/event-driven plays | Position 2-3 days before known catalysts | 1-2× ATR moves expected |
| Mean reversion: Z-score > 2.5 | Pairs trading (BTC/ETH ratio) | Market-neutral returns |

---

### Phase 5: Execution Quality (Month 2-3) ⚙️

| Task | Description |
|------|-------------|
| Limit orders instead of market | Reduce slippage from 0.05-0.30% to near 0 |
| TWAP for large orders | Break large orders into time-weighted chunks |
| OCO orders on all entries | Simultaneous stop-loss + take-profit via exchange |
| Dynamic trailing stop | Tighten from 5% to 2% once position is +5% |
| Portfolio rebalancing | Weekly mean-variance optimization via `allocation_engine.py` |

---

## 6. KEY METRICS TO TRACK

Track these weekly to measure improvement:

| Metric | Current | Phase 1 Target | Phase 2 Target |
|--------|---------|----------------|----------------|
| Win rate | 7.7% | 35%+ | 50%+ |
| Profit factor | < 1.0 | > 1.2 | > 1.5 |
| Max drawdown | ~23% | < 10% | < 5% |
| Sharpe ratio | Negative | > 0.5 | > 1.0 |
| Avg hold time | 0 days | 2-5 days | 3-7 days |
| Signal quality score | 0.5 avg | 0.65+ | 0.75+ |

---

## 7. WHAT ACTUALLY WORKS (Keep These)

- ✅ Price feed via WebSocket (Binance, low latency)
- ✅ TA engine (RSI, MACD, BB, ATR, ADX — accurate calculations)
- ✅ Stop-loss / take-profit / trailing stop mechanism (the code is correct)
- ✅ Kill switch + daily loss limit + max drawdown halt
- ✅ Telegram notifications
- ✅ Mode hot-switching (paper → dryrun → live)
- ✅ Correlation group tracking (prevent over-concentration)
- ✅ DB schema and order tracking
- ✅ Bull/Bear debate architecture (catches confirmation bias)
- ✅ 3-way risk persona system (aggressive/conservative/neutral)
- ✅ Kelly Criterion calculator (just not wired to execution)
- ✅ Multi-timeframe signal filter code (just not activated)

---

## 8. IMMEDIATE ACTION ITEMS (Do Today)

```
1. [ ] Fix duplicate signal execution race condition
2. [ ] Add RSI floor gate for SELL signals (RSI < 50 in long-only = HOLD, not SELL)
3. [ ] Fix equity tracker to include realized PnL from positions table
4. [ ] Raise MIN_SIGNAL_CONFIDENCE from 0.65 to 0.72
5. [ ] Add 60-second deduplication per symbol per cycle
```

These 5 fixes will stop the current losing pattern without changing the strategy architecture.
