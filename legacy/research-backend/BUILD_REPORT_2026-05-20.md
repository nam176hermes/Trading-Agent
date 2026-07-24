# Trading Agent — Build Report & Gap Analysis

**Date:** 2026-05-20
**Project:** `~/.hermes/crypto-research/`
**Status:** Research-grade autonomous trading system. Not yet production-live.

---

## 1. Executive Summary

The trading-agent has grown from a 7-day sprint prototype (6,000 lines) into a **36,527-line research trading platform** with 103 Python modules across 14 functional domains. Two intensive research-build sessions — 19 trading books and 8 university-level courses — added 17 new modules (5,113 lines) that fill critical gaps.

**Reality check:** The system can research, backtest, generate signals, and simulate execution. It cannot yet run unsupervised with real money. The gap is not in code volume — it's in the **last-mile hardening** needed to trust a machine with capital.

---

## 2. What Was Built — The Full Architecture

### 2.1 Layer Map

```
┌─────────────────────────────────────────────────┐
│              DASHBOARD (Next.js 16)              │
│   13 API routes · Live cards · Backtest viewer   │
├─────────────────────────────────────────────────┤
│                 EXECUTION LAYER                  │
│  CCXT bridge · Canada gate · Paper/Dryrun/Live  │
│  WebSocket streaming · Order executor            │
├─────────────────────────────────────────────────┤
│                 SIGNAL ASSEMBLY                  │
│  Alpha priority chain: PredScope > Adanos+ML     │
│  > LightGBM > Ensemble ML > TA (fallback)        │
├──────────────┬──────────────┬───────────────────┤
│  ML MODELS   │  ALPHA DATA  │   RISK ENGINE      │
│  LightGBM    │  PredScope   │   Monte Carlo VaR  │
│  XGBoost+RF  │  Adanos      │   GARCH vol        │
│  LSTM/GRU    │  Polymarket  │   Regime detect    │
│  Ensemble    │  Social buzz │   Circuit breaker  │
├──────────────┴──────────────┴───────────────────┤
│              BACKTESTING ENGINE                  │
│  Event-driven · Walk-forward CV · No lookahead  │
│  Slippage model · Commission · Stop-loss/take    │
├─────────────────────────────────────────────────┤
│              DATA PIPELINE                       │
│  yfinance · CoinGecko · FRED · Fear & Greed     │
│  Cron-scheduled · SQLite persistence             │
└─────────────────────────────────────────────────┘
```

### 2.2 Module Inventory (81 active modules)

**Core Pipeline (Sprint Days 1-7)**
| Module | Lines | Purpose |
|--------|-------|---------|
| `main.py` | 1,418 | CLI entry, mode routing, pipeline orchestration |
| `trading_agent.py` | 964 | Agent loop, decision dispatch |
| `assembly.py` | 822 | Multi-source signal assembly, priority chain |
| `portfolio_manager.py` | 728 | Position tracking, PnL, allocation |
| `data_collector.py` | 479 | OHLCV + fundamentals ingestion |
| `safety_engine.py` | 377 | Stop-loss enforcement, 5-min checks |
| `paper_trader.py` | 493 | Simulated execution with stop/target persistence |

**ML & Quantitative (Phase 2-5)**
| Module | Lines | Purpose |
|--------|-------|---------|
| `ml_predictor.py` | 464 | LightGBM binary classifier, walk-forward training |
| `ml_regime.py` | 626 | PCA + K-Means regime detection (4 regimes) |
| `regime_detector.py` | 538 | Market regime classification + filtering |
| `bayesian_weighting.py` | 169 | Beta-Binomial signal confidence tracking |
| `dl_predictor.py` | 713 | LSTM/GRU infrastructure (research-grade) |

**Exchange Connectivity (Phase 3)**
| Module | Lines | Purpose |
|--------|-------|---------|
| `execute_live.py` | 498 | Order routing: paper → dryrun → live |
| `exchange/ccxt_bridge.py` | 213 | Unified CCXT interface, Canada-legal gate |
| `exchange/adapter.py` | 333 | Underlying CCXT wrapper with sandbox support |
| `exchange/executor.py` | 328 | Order lifecycle management |
| `exchange/ws_feed.py` | 179 | Kraken WebSocket real-time streaming |
| `exchange/secrets.py` | 203 | Encrypted credential management |

**Backtesting (Phase 4)**
| Module | Lines | Purpose |
|--------|-------|---------|
| `backtest_engine.py` | 862 | Event-driven loop: DataHandler→Strategy→Broker→Portfolio |
| `backtest_runner.py` | 599 | CLI: single, walk-forward, compare modes |
| `backtest_analyzer.py` | 555 | Performance reports, equity curves |
| `backtest_gate.py` | 276 | Regression gate for pipeline integration |

**Alpha Sources (Phase 5.5)**
| Module | Lines | Purpose |
|--------|-------|---------|
| `predscope_collector.py` | 82 | Polymarket data ingestion (47 markets) |
| `predscope_signals.py` | 162 | Odds → BUY/SELL signal conversion |
| `adanos_collector.py` | 103 | Social sentiment fetching (20 tokens) |
| `adanos_signals.py` | 127 | Buzz → momentum/contrarian signals |

### 2.3 Built From Books & Courses (Today's Focus)

**From 19 Trading Books → 8 Modules (2,378 lines)**
| Module | Lines | Book Concept |
|--------|-------|-------------|
| `risk_engine.py` | 331 | Professional risk mgmt (Natenberg, Sinclair) |
| `data_vendors.py` | 502 | Multi-source data abstraction (Chan, Jansen) |
| `ml_toolkit.py` | 575 | ML infrastructure (Lopez de Prado) |
| `exit_strategies.py` | 303 | Exit logic library (Faith, Minervini) |
| `signal_filters.py` | 276 | Regime/quality gating (Kaufman) |
| `event_bus.py` | 223 | Async event architecture |
| `exchange_health.py` | 255 | Exchange status monitoring |
| `cra_tracker.py` | 301 | CRA-compliant trade journal (Canadian tax) |

**From 8 University Courses → 7 Modules (2,735 lines)**
| Module | Lines | Course Concept |
|--------|-------|---------------|
| `pairs_trader.py` | 461 | Statistical arbitrage (Engle-Granger cointegration) |
| `garch_vol.py` | 265 | Volatility forecasting (GARCH 1,1 + EWMA fallback) |
| `portfolio_optimizer.py` | 519 | Efficient frontier, max Sharpe, risk parity (Markowitz/EDHEC) |
| `ensemble_ml.py` | 325 | LightGBM+XGBoost+RF stacking (Hagmann) |
| `ws_stream.py` | 246 | Kraken WebSocket via ccxt.pro, async reconnect |
| `candlestick_patterns.py` | 639 | 8 patterns in pure numpy: Doji, Hammer, Engulfing, Stars, Soldiers |
| `monte_carlo.py` | 280 | Bootstrap + parametric simulation, VaR/CVaR, strategy CIs |

---

## 3. What's Missing — The Gap Analysis

A trading-agent is "real" when it can run unsupervised with real money and produce positive expected value. Here's what stands between current state and that threshold.

### 3.1 CRITICAL — Strategy Edge (No Alpha Yet)

**The problem:** None of our strategies have demonstrated statistically significant positive Sharpe ratios in walk-forward backtests.

| Strategy | Symbol | Sharpe | Win Rate | Verdict |
|----------|--------|--------|----------|---------|
| LightGBM ML | BTC/USDT | -0.41 | — | Negative |
| LightGBM ML | ETH/USDT | +0.43 | — | Marginal |
| LightGBM ML (WF) | BTC/USDT | +0.23 | 36.8% | Near-zero |
| LSTM/GRU | All | ~0.00 | ~50% | Dead end |
| TA Voting | All | ~0.30 | — | HOLD-bias |

**Action needed:** PredScope odds-based signals showed BUY/SELL with high conviction (0.91-1.00) but have never been backtested. This is the most promising unexplored edge.

### 3.2 HIGH — Live Execution Hardening

**What exists:** CCXT bridge connects to Kraken demo sandbox. Kill switch prevents live orders.

**What's missing:**
- No order state machine (submitted → partially filled → filled → canceled)
- No heartbeat monitoring (detect exchange disconnection)
- No failover between Coinbase/Kraken
- No trade journal recording actual fills (only simulated paper trades)
- `LIVE_EXECUTION_ENABLED` has never been set to `True` — zero live orders placed

**Risk level:** Premature live execution would lose money to bugs, not edge.

### 3.3 HIGH — Position Sizing

**What exists:** Basic fixed-position sizing.

**What's missing:**
- Kelly criterion (optimal fraction based on edge estimate)
- Volatility-adjusted sizing (smaller positions in high-vol regimes)
- Correlation-aware allocation (don't double-down on correlated pairs)
- The `portfolio_optimizer.py` module exists but is not wired into live execution

### 3.4 MEDIUM — Model Lifecycle

**What exists:** One-time trained LightGBM models saved as `.txt` files.

**What's missing:**
- Scheduled retraining (models decay after weeks in crypto)
- Feature store (consistent feature computation)
- A/B testing framework (compare strategies live)
- Model registry with performance tracking
- The `ensemble_ml.py` module is built but never trained or evaluated

### 3.5 MEDIUM — Monitoring & Alerting

**What exists:** Cron jobs run hourly/daily. Telegram alerts exist for trade signals.

**What's missing:**
- System health dashboard (are all collectors running?)
- P&L attribution (which signals made/lost money?)
- Anomaly detection (unexpected data gaps, API failures)
- The `exchange_health.py` and `event_bus.py` modules exist but are not wired

### 3.6 MEDIUM — WebSocket Live Market Data

**What exists:** `ws_stream.py` for Kraken WebSocket streaming (built, tested).

**What's missing:**
- Not integrated into the signal pipeline (signals still use REST-polled data)
- No order book depth analysis
- No real-time spread monitoring
- `ws_feed.py` in exchange/ exists but is separate from `ws_stream.py` — duplication

### 3.7 LOW — Pairs Trading Pipeline

**What exists:** `pairs_trader.py` with Engle-Granger cointegration testing.

**What's missing:**
- No scheduled pair scanning
- No signal integration into assembly.py
- No backtest of pair strategies
- This is a whole new strategy class, not a missing piece

### 3.8 LOW — Tax & Compliance

**What exists:** `cra_tracker.py` for Canadian tax journaling.

**What's missing:**
- Not tested with real trade data
- No capital gains/loss calculation
- No T5008-style reporting
- The CRA module is placeholder infrastructure

---

## 4. What Courses Taught Us That Books Missed

| Insight | Source | Status |
|---------|--------|--------|
| Ensemble ML > single model | Hagmann, Jansen | `ensemble_ml.py` built, not trained |
| Cloud deployment is standard | MIT, EDHEC courses | Dockerfile needed |
| WebSocket streaming is table stakes | 4/8 courses teach it | Built, not wired |
| Portfolio optimization from day 1 | EDHEC, Michigan | Built, not wired |
| Statistical arbitrage works in crypto | Quantopian-style courses | Pairs trader built, not backtested |
| Volatility regime drives position sizing | All quant courses | GARCH built, not integrated |

---

## 5. Priority Roadmap — What To Build Next

### Phase A: Prove the Edge (1-2 sessions)
Backtest PredScope signals on the event-driven engine. This is the highest-ROI task because it either validates our alpha or tells us we need a new approach.

1. Wire `predscope_signals.py` → `backtest_engine.py`
2. Run walk-forward on BTC, ETH, SOL with Polymarket-based signals
3. If Sharpe > 0.5: proceed to execution hardening
4. If Sharpe < 0.5: explore ensemble ML + pairs trading as alternative edge

### Phase B: Execution Hardening (2-3 sessions)
Only after Phase A succeeds.

1. Build order state machine in `exchange/executor.py`
2. Add heartbeat monitoring to `ws_stream.py`
3. Wire `risk_engine.py` position sizing into live execution
4. Run dry-run mode for 1 week with real market data
5. Verify: P&L matches simulation within slippage tolerance

### Phase C: Unsupervised Operation (2-3 sessions)
1. Scheduled model retraining
2. System health monitoring dashboard
3. Full alerting pipeline (collector failures, execution anomalies)
4. Trade journal → P&L attribution

### Phase D: Alpha Diversification (ongoing)
1. Train ensemble ML on expanded feature set
2. Backtest pairs trading strategies
3. Wire portfolio optimization into live allocation

---

## 6. Current System Health

| Component | Status | Details |
|-----------|--------|---------|
| Data collectors | ✅ Running | 4 cron jobs, all last_status=ok |
| ML models | ✅ Trained | LightGBM BTC/ETH/SOL, walk-forward validated |
| Backtest engine | ✅ Verified | Event-driven, no lookahead, proper slippage |
| Exchange bridge | ✅ Sandbox | Kraken demo connects; kill switch active |
| Dashboard | ✅ Live | 13/13 API routes 200 |
| Alpha signals | ✅ Active | PredScope + Adanos generating real signals |
| Live execution | ❌ Disabled | `LIVE_EXECUTION_ENABLED=False` |
| Order state machine | ❌ Missing | No fill/cancel/partial handling |
| Position sizing | ❌ Basic | Fixed-size only, no Kelly/vol-adjustment |
| Model retraining | ❌ Missing | One-time trained, no schedule |
| P&L attribution | ❌ Missing | Can't tell which signals make money |
| Pairs trading | ❌ Untested | Module built, never backtested |
| Ensemble ML | ❌ Untrained | Code exists, models never fitted |

---

## 7. Bottom Line

**36,527 lines of Python.** 103 modules. 17 modules added in the last two sessions from books and courses. The system is architecturally complete — every domain has at least one module. But architecture ≠ alpha.

The single most important truth: **we don't yet know if we have an edge.** Everything else — execution hardening, monitoring, position sizing — is scaffolding that assumes an edge exists. Phase A (backtesting PredScope signals) is the gating item. If PredScope signals show a positive Sharpe in walk-forward backtest, we have a clear path to live. If not, we need to pivot the alpha generation approach before building more infrastructure.
