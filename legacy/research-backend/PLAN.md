# 7-Day Trading Agent Sprint Plan ✅ 6/7 Complete + Extras

**Start:** 2026-05-15 | **Deadline:** 2026-05-22 (API discount expires)
**Goal:** Autonomous trading agent usable in real life.
**Status:** Core systems built. Alpha sources wired. Remaining: testing, hardening, deploy.

## Day 1 ✅ — Live Data + Safety + Automation
- [x] Live macro data (yfinance + World Bank)
- [x] Live fundamentals (yfinance P/E, P/B, ROE, beta)
- [x] Live sentiment (Fear & Greed Index API)
- [x] Live on-chain (Binance whale large trades)
- [x] Safety system (trailing stops, position monitor, correlation check, circuit breaker)
- [x] Automation (3 cron jobs: snapshot 30min, debate 4h, safety 5min)
- [x] Reflection engine price fetch (yfinance + CoinGecko)

## Day 2 ✅ — Backtest Marathon + Decision Quality
- [x] Walk-forward backtest on 15 symbols (8 crypto + 7 stocks), 2023-2025
- [x] Backtest analyzer — performance report
- [x] Fix decision quality (entry_price, stop_loss, take_profit)
- [x] Wire decisions → typed_decisions for reflection engine
- [x] Strategy optimization based on backtest results
- [x] Regime-aware signal filter (reject unclear, SHORT-only-in-trending_down)
- [x] Telegram alerts for new decisions

## Day 3 ✅ — Reflection + Strategy Tuning
- [x] Full reflection cycle — 7 reflections generated (LLM)
- [x] Strategy optimizer (statistical + LLM-ready)
- [x] Optimized rules: LONG only, trending_up, >=45% win rate
- [x] Reflection horizon bug fix (0 or 7 = 7 in Python)

## Day 4 ✅ — Stock Pipeline + Dashboard
- [x] Stock OHLCV via yfinance (replaced broken Twelve Data)
- [x] 7-stock backtest (AAPL, NVDA, MSFT, GOOGL, AMZN, META, TSLA)
- [x] 15-symbol unified analysis (NVDA 65.3% top performer)
- [x] Dashboard 46 API routes confirmed (200 OK)
- [x] Stock pipeline auto-included during US market hours

## Day 5 ✅ — Paper Trading + Risk
- [x] Regime filter applied BEFORE paper execution
- [x] Paper trader persists stop_loss + take_profit on BUY
- [x] Safety engine (Python): checks stops every 5min, triggers SELL
- [x] Backfilled stops for 4 existing positions (5% stop, 10% target)
- [x] Telegram alerts for filled/rejected trades
- [x] Circuit breaker: 15% max drawdown, 3% daily loss limit

## Day 6 ✅ — Testing + Hardening
- [x] Full integration tests (47/47 across 11 sections)
- [x] Edge case handling (empty data, missing collectors, API failures)
- [x] Dashboard health check (13/13 API routes verified 200)
- [x] Pipeline end-to-end verified (collectors → signals → assembly)
- [x] One-click start script (start-trading.sh)
- [x] Pipeline fix (removed archived freecrypto.py reference)

## Day 7 ✅ — Polish + Deploy
- [x] Documentation (README.md — architecture, setup, usage, troubleshooting)
- [x] Handoff checklist (HANDOFF.md — daily/weekly/before-live verification)
- [x] Final paper trade validation (portfolio active, stops set, mode=safe)
- [x] All cron jobs verified (4 trading jobs, all last_status=ok)
- [x] Dashboard: backtest route fixed (500→200), 13/13 routes 200
- [x] Pipeline: freecrypto.py removed, predscope+adanos collectors active

---

## REAL TRADING Upgrades (beyond sprint)

### Phase 3 ✅ — Exchange Connectivity (CCXT)
- [x] CCXT bridge (Canada-legal: Coinbase/Kraken/Crypto.com)
- [x] Execute_live.py (paper/dryrun/live mode routing)
- [x] Binance/Bybit/OKX blocked (Canada-non-legal)
- [x] Kraken demo sandbox verified
- [x] Kill switch: LIVE_EXECUTION_ENABLED=False

### Phase 4 ✅ — Backtesting Engine
- [x] Event-driven engine (DataHandler → Strategy → Broker → Portfolio)
- [x] MLStrategy (LightGBM) + BaselineStrategy (buy & hold)
- [x] Walk-forward CV with expanding windows
- [x] Output format compatible with backtest_gate.py
- [x] ETH ML beats buy-and-hold: +$495 vs -$104 (Sharpe 0.43)

### Phase 5.2 ✅ — LSTM/GRU Predictor (research-grade)
- [x] dl_predictor.py: 2-layer LSTM/GRU on 60-bar sequences
- [x] Chronological split, class-weighted BCE, early stopping
- [x] Overfits on 9K-bar dataset — zero signals in backtest
- [x] Documented as infrastructure; not production-ready

### Phase 5.5 ✅ — Alpha Sources → Signals
- [x] predscope_signals.py: Polymarket odds → BUY/SELL
- [x] adanos_signals.py: Social sentiment → momentum/contrarian
- [x] assembly.py: Alpha priority chain (PredScope > Adanos+ML > LightGBM > TA)
- [x] Dashboard: 3 new cards + 3 API routes (all verified 200)
- [x] Live signals: BTC SELL 0.91 (PredScope), ETH BUY 1.00 (PredScope), UNI BUY 0.52 (Adanos)

---

## Key Metrics (as of Day 5)

### Backtest
- 15 symbols, 11,780+ records, 2023-2025
- Top: NVDA 65.3%, AVAX 54.4%, ADA 50.6%
- trending_up regime: 48.9% win rate (890 signals)

### Paper Portfolio
- Equity: $98,487 (drawdown 3.3%)
- Cash: $27,277
- 4 positions: LINK, ADA, AVAX, DOGE
- All stops set (5% stop, 10% target)

### Pipeline
- 15 commits across 5 days
- 3 cron jobs running 24/7
- Dashboard: 46 API routes operational

## Fast Trade (Step 6 evaluation — 2026-05-20)

**Decision: DO NOT INTEGRATE**

fast-trade (https://github.com/jrmeier/fast-trade) is licensed under AGPL-3.0.
Using it in this trading agent would require open-sourcing the entire codebase.
The project's existing backtest_engine.py + walk_forward.py cover the same use case
without license constraints.

**Alternative**: Continue using the in-house backtest_engine.py + backtest_runner.py.
For rapid strategy prototyping, use backtest_runner.py with --prob-threshold tuning.
