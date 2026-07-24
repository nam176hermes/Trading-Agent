# PLAN — From Research Tool to Real Trading Agent

**Date:** 2026-05-19 | **Input:** Deep research on 31 libraries/APIs (4 discovery + 1 adversarial agents, 75+ sources)
**Current state:** Paper trader, 20-asset pipeline, negative alpha (Sharpe BTC -0.34, ETH -0.92, SOL -0.85), Coinbase+Kraken only

---

## North Star

A trading agent that:
1. **Generates positive alpha** (ML-driven signals, not TA voting)
2. **Executes on real exchanges** (CCXT → Coinbase/Kraken/Crypto.com, Canada-legal)
3. **Has data advantages** (prediction markets, social sentiment, order flow)
4. **Self-improves** (proper backtesting, walk-forward model retraining, post-trade reflection)

---

## Phase 1 — Strategy Alpha (Week 1-2) 🔴 CRITICAL PATH

*No other phase matters until strategy has positive expected value.*

### Task 1.1: Clone ML4T + Set Up Environment
```
cd ~/.hermes/crypto-research
git clone https://github.com/stefan-jansen/machine-learning-for-trading reference/ml4t
pip install scikit-learn xgboost lightgbm alphalens pyfolio-reloaded pandas numpy matplotlib
```

### Task 1.2: Build Alpha Factor Research Notebook
- Extract Ch4 (alpha factor research) patterns
- Apply to our crypto data (BTC, ETH, SOL 1h candles)
- Use Alphalens to evaluate which features predict returns
- Output: list of features with Information Coefficient (IC) rankings

### Task 1.3: Build LightGBM Return Predictor
- Replace assembly.py's RSI/MACD/SMA voting with ML-predicted return probabilities
- Walk-forward cross-validation (train 60d, test 7d, advance 7d)
- Output: `ml_predictor.py` — produces buy/sell/hold signals with calibrated probabilities

### Task 1.4: Wire ML Predictor Into Pipeline
- `ml_predictor.py` → assembly.py → portfolio_manager.py
- Replace binary confidence (low/medium/high) with calibrated probability scores
- Backtest against existing walk_forward.py results
- Target: Sharpe > 0 (break even to positive)

### Task 1.5: Regime Detection (Phase 1 Stretch)
- Ch13: PCA + clustering on 20-asset returns
- Identify market regimes (trending, ranging, volatile, calm)
- Apply different strategy parameters per regime

---

## Phase 2 — Data Moats (Week 1-2, Parallel) 🟡

### Task 2.1: PredScope Collector
- New file: `~/.hermes/crypto-research/predscope_collector.py`
- GET https://predscope.com/api/markets.json every 5 min (100 req/hr allowed)
- Filter: crypto-category markets + Fed/election/geopolitical macro markets
- Output: `reports/prediction_market_<timestamp>.json`
- Assemble: aggregate crypto probabilities, macro event odds
- Dashboard: new PredictionMarketCard component

### Task 2.2: Adanos Sentiment Collector
- Sign up at adanos.org (free tier: 250 req/mo)
- New file: `~/.hermes/crypto-research/adanos_collector.py`
- Daily batch: fetch Reddit crypto sentiment, X/Twitter cashtag signals, Polymarket conviction
- Output: `reports/social_sentiment_<timestamp>.json`
- Assemble: weighted composite buzz + sentiment + bullish_pct
- Cross-reference against Marketaux news sentiment for divergence detection
- Dashboard: new SocialSentimentCard component

---

## Phase 3 — Real Execution (Week 3-4) 🔴 CRITICAL PATH

### Task 3.1: CCXT Integration
```
pip install coincurve==20.0.0  # workaround for CCXT install bug
pip install ccxt
```
- New file: `~/.hermes/crypto-research/exchange/ccxt_bridge.py`
- Initialize: Coinbase, Kraken, Crypto.com (Canada-legal exchanges only)
- Fetch balances, ticker prices, order books
- Replace Binance public API calls in data_collector.py with CCXT unified interface

### Task 3.2: Real Order Execution
- Wire CCXT → `execute_live.py` (counterpart to `execute_paper.py`)
- Reuse existing encrypted key storage (`exchange/secrets.py`)
- Reuse existing position sizing (5% max, 50% total, ATR-based stops)
- Add pre-flight checks: balance verification, minimum order size, rate limits
- Mode: dryrun first (CCXT test orders on exchange testnet), then live

### Task 3.3: Exchange Settings UI
- Extend `/dashboard/settings` to support multiple exchange keys
- Add per-exchange test connection button
- Show exchange-specific balances
- Mode flow: paper → dryrun (testnet) → live (small size)

---

## Phase 4 — Proper Backtesting (Week 3-4)

### Task 4.1: Zipline-Reloaded Setup
```
pip install zipline-reloaded alphalens pyfolio-reloaded
```
- New file: `~/.hermes/crypto-research/backtest/zipline_runner.py`
- Crypto data bundle: ingest yfinance data as custom zipline bundle
- Replicate current RSI/MACD/SMA strategy as zipline strategy class

### Task 4.2: Factor Analysis Pipeline
- Run Alphalens on engineered features (from Phase 1.2)
- Generate factor tearsheet: IC analysis, quantile returns, turnover
- Output: `reports/factor_analysis_<timestamp>.json`

### Task 4.3: Backtest Dashboard Integration
- New API route: `/api/trading/factor-analysis`
- New dashboard component: FactorAnalysisCard (IC chart, quantile returns)
- Compare: old TA voting vs. ML-driven strategy side-by-side

---

## Phase 5 — Advanced ML (Month 2+)

### Task 5.1: Bayesian Signal Weighting (Ch 10)
- Replace binary voting with PyMC Bayesian-updated confidence
- Dynamic Sharpe estimation: weight signals by recent performance
- Output: `bayesian_weighting.py`

### Task 5.2: RNN/LSTM Forecasting (Ch 19)
- Multivariate time series model for next-period return prediction
- Compare against LightGBM baseline
- GPU training if available, CPU otherwise

### Task 5.3: RL Trading Agent (Ch 22)
- OpenAI Gym environment for crypto trading
- Deep Q-Learning agent: learns optimal entry/exit from market state
- Compare against supervised ML approach

---

## What We're NOT Doing (From Research)

| Library | Reason |
|---------|--------|
| Binance/Bybit/OKX/KuCoin SDKs | All blocked in Canada |
| backtrader | GPLv3 copyleft + abandoned since July 2020 |
| Catalyst | Archived, Python 2.7, cannot install |
| python-binance | Deprecated, WebSocket broken, Binance blocked |
| gs-quant | Requires Goldman Sachs credentials, pandas 2.x crash |
| RustQuant | Wrong language (Rust), v0.0.18, solo project |
| hftbacktest | Silent data corruption bugs, overkill for daily strategy |
| Block Atlas | Does not exist as open-source library |
| pykalshi | Abandoned since Oct 2023 |
| fast-trade | LGPLv3 license risk (evaluate later if needed) |
| Orderflow | Heavy infra (TimescaleDB), evaluate post-Phase 1 |

---

## Success Metrics

| Phase | Metric | Current | Target |
|-------|--------|---------|--------|
| Phase 1 | Sharpe ratio (BTC) | -0.34 | > 0.0 |
| Phase 1 | Win rate | ~20% | > 45% |
| Phase 1 | Max drawdown | TBD | < 15% |
| Phase 2 | Data freshness (new signals) | News only | +Prediction markets +Social sentiment |
| Phase 3 | Exchange coverage | Paper only | Coinbase + Kraken + Crypto.com (live) |
| Phase 4 | Backtest coverage | Walk-forward only | Factor analysis + regime-aware |
| Phase 5 | Model ensemble | TA voting only | LightGBM + LSTM + RL |

---

## Execution Strategy

- **Phase 1 is the critical path.** No other work until Sharpe > 0.
- **Phase 2 runs in parallel** (PredScope + Adanos are trivial ~30-line collectors).
- **Phase 3 only after Phase 1 succeeds.** Real execution with negative alpha = guaranteed loss.
- **Phase 4 validates Phase 1's ML approach** with proper factor analysis.
- **Phase 5 is aspirational** — only if Phase 1-4 are stable.
- **Subagent-driven development:** Delegate each Phase via Claude Code / DeepSeek subagents with structured SwarmBriefs.
- **Commit between phases.** Human checkpoint at Phase 1 completion before proceeding to Phase 3.
