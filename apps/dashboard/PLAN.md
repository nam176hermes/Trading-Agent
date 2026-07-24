# Trading Agent — Production Readiness Plan

## Audit Summary

**What's REAL (working):**
- Data collection: CoinGecko + Binance APIs ✓
- Technical analysis: RSI, MACD, SMA, Bollinger, ATR via pandas-ta ✓
- 4 LLM analysts: Technical, Sentiment (live), On-chain (STUB), Macro (stale files)
- Bull/Bear debate: multi-round adversarial with deepseek-reasoner ✓
- 3 Risk personas: Aggressive, Conservative, Neutral ✓
- Portfolio manager: ratify/modify/reject with conviction thresholds ✓
- Paper trader: $100k virtual, 5% per position, 3% daily loss limit, 15% max drawdown ✓
- Walk-forward backtesting: 2022-2025 Binance data, zero lookahead ✓
- Kill switch: file-based emergency stop ✓
- Broker: Alpaca paper, live disabled ✓
- Dashboard: Next.js 16 with live API triggers ✓

**What's MISSING / BROKEN:**
- On-chain analyst: returns hardcoded neutral — no real data
- Sentiment data: reads stale local JSON, no live CryptoPanic/NewsAPI
- Macro data: reads stale local JSON, no live FRED/Yahoo
- Fundamentals: reads stale FMP JSON files
- No automated scheduling — manual triggers only
- No live trade execution — paper only
- No trailing stops / position monitoring
- No portfolio rebalancing
- No Telegram trade alerts
- No multi-timeframe confirmation

---

## Phase 1: Live Data Pipeline (Days 1-3)

### 1.1 Fix On-Chain Analyst
**Current:** `OnchainAnalyst.analyze()` returns hardcoded neutral
**Target:** Real whale movements, exchange flows, active addresses

Data sources (free tiers):
- CoinGecko `/coins/{id}` — market cap, volume, ath, atl (already have)
- CoinGecko `/coins/{id}/market_chart` — price/volume/market cap history
- CryptoQuant free API — exchange inflows/outflows (needs signup)
- Glassnode free tier — limited but useful

**Alternative (no API key needed):**
- Whale Alert Twitter scraper
- Derive from Binance order book depth + large trades
- Use volume spikes vs. price divergence as whale proxy

**Implementation:**
```python
# onchain_collector.py — rewrite from stub to real
# 1. Fetch exchange netflows from Binance large trades
# 2. Track top holder concentration via CoinGecko
# 3. Calculate exchange reserve changes
# 4. Feed into OnchainAnalyst for LLM interpretation
```

### 1.2 Live Sentiment Pipeline
**Current:** reads `reports/sentiment_report_*.json` (stale)
**Target:** Live CryptoPanic + NewsAPI + social volume

Data sources:
- CryptoPanic free API — crypto news with sentiment scores
- Alternative: Fear & Greed Index API — free, no key needed
- Alternative: LunarCrush free tier — social metrics

**Implementation:**
```python
# sentiment_collector.py — rewrite for live data
# 1. Fetch CryptoPanic news (filtered by asset)
# 2. Fetch Fear & Greed Index
# 3. Optional: Reddit/Twitter volume via LunarCrush
# 4. Output structured dict → SentimentAnalyst.analyze()
```

### 1.3 Live Macro Pipeline
**Current:** reads `reports/macro_report_*.json` (stale)
**Target:** Live FRED + Yahoo Finance macro indicators

Data sources:
- FRED API (free, needs key) — GDP, CPI, Fed rate, VIX, DXY, US10Y
- Yahoo Finance (already have yfinance) — SPY, QQQ, VIX, DXY
- Alternative: TradingEconomics free tier

**Implementation:**
```python
# macro.py — rewrite for live data via yfinance + FRED
# 1. Fetch VIX, DXY, SP500, US10Y from yfinance (works now)
# 2. Fetch Fed rate, CPI, GDP from FRED API
# 3. Regime detection: risk-on vs risk-off
# 4. Feed into MacroAnalyst for LLM interpretation
```

### 1.4 Live Fundamentals Pipeline
**Current:** reads `reports/fundamentals_report_*.json` (stale FMP dump)
**Target:** Live fundamentals from yfinance + FMP

**Implementation:**
```python
# fundamentals_collector.py — new module
# 1. Fetch P/E, P/B, EPS, revenue from yfinance (free)
# 2. Compare to sector averages
# 3. Earnings date detection
# 4. Feed into debate prompt context
```

---

## Phase 2: Risk & Safety Hardening (Days 4-5)

### 2.1 Trailing Stop Automation
**Current:** Static stop losses only
**Target:** ATR-based trailing stops, updated every snapshot

```python
# trailing_stops.py — new module
# 1. Read open positions from paper_trader
# 2. Calculate ATR(14) for each position
# 3. If price moved favorably, trail stop by ATR multiples
# 4. Check every 30min snapshot
# 5. Auto-close if stop hit
```

### 2.2 Position Monitor & Alerts
**Current:** No monitoring between pipeline runs
**Target:** Real-time position P&L, circuit breaker checks, Telegram alerts

```python
# position_monitor.py — new module
# 1. Poll current prices every 5min
# 2. Check each position: P&L, stop distance, target distance
# 3. Alert if: stop within 5%, target within 10%, drawdown > 10%
# 4. Telegram: send via @Trading_page_agent_bot
```

### 2.3 Multi-Timeframe Confirmation
**Current:** Single timeframe analysis (daily candles)
**Target:** 1h + 4h + 1d alignment check

```python
# timeframe_confirmation.py — new module
# 1. Fetch 1h, 4h, 1d OHLCV per asset
# 2. Run TA indicators on each timeframe
# 3. Score alignment (all bullish = high conviction, mixed = reduce size)
# 4. Feed into PortfolioManager for position size adjustment
```

### 2.4 Correlation Risk Check
**Current:** No correlation analysis
**Target:** Don't over-concentrate in correlated assets

```python
# correlation_check.py — new module  
# 1. Calculate 30d correlation matrix for all watchlist assets
# 2. If adding new position, check correlation to existing
# 3. Reduce position size if >0.7 correlation with existing holding
```

---

## Phase 3: Automation & Scheduling (Days 6-7)

### 3.1 Cron Pipeline Automation
**Current:** Manual trigger only
**Target:** Fully autonomous schedule

```
Every 30min:  Snapshot pipeline (prices + TA + sentiment)
Every 4h:     Full debate pipeline (snapshot + debate + risk personas)
Daily 9AM:    Morning brief for watchlist
Daily 4PM:    Daily P&L report + position review
Weekly Sun:   Weekly recap + portfolio rebalance check + reflection loop
```

**Implementation:** Python scheduler or crontab entries
```bash
*/30 * * * * cd ~/.hermes/crypto-research && python3 pipeline.py snapshot
0 */4 * * * cd ~/.hermes/crypto-research && python3 pipeline.py debate
0 9 * * *  cd ~/.hermes/crypto-research && python3 main.py --mode brief
0 16 * * * cd ~/.hermes/crypto-research && python3 main.py --mode daily-report
0 18 * * 0 cd ~/.hermes/crypto-research && python3 main.py --mode weekly
```

### 3.2 Auto-Execution with Safeguards
**Current:** Paper execution after debate, but must verify it actually runs
**Target:** Pipeline → Debate → Portfolio Manager → Auto-execute (paper)

Gate checklist before execution:
1. ✓ Kill switch inactive
2. ✓ Circuit breaker not triggered
3. ✓ Daily loss < 3%
4. ✓ Total drawdown < 15%
5. ✓ Portfolio Manager conviction > 0.35
6. ✓ At least 1/3 risk personas accept
7. ✓ Correlation check passed
8. ✓ Timeframe alignment ≥ 2/3

### 3.3 Telegram Alert Integration
Wire alerts to Nam's trading bot (`@Trading_page_agent_bot`, ID: 8561691098)

Alert types:
- Trade executed (entry price, size, stop, target)
- Stop hit / target hit
- Daily P&L summary
- Circuit breaker triggered
- Kill switch activated
- Error alerts

---

## Phase 4: Production Hardening (Days 8-10)

### 4.1 Environment & Config
```
~/.hermes/.env:
  # LLM
  DEEPSEEK_API_KEY=sk-...
  
  # Data (optional — many work without keys)
  COINGECKO_API_KEY=...    # pro tier, optional
  CRYPTOPANIC_API_KEY=...  # free tier
  FRED_API_KEY=...         # free
  
  # Broker (paper only for now)
  ALPACA_API_KEY=...
  ALPACA_SECRET_KEY=...
  ALPACA_PAPER=true
  
  # Alerts
  TELEGRAM_BOT_TOKEN=...   # @Trading_page_agent_bot
  TELEGRAM_CHAT_ID=...
```

### 4.2 Error Recovery
```python
# pipeline.py additions:
# - Retry with exponential backoff on API failures
# - Fallback data sources (CoinGecko → Binance → yfinance)
# - Graceful degradation: if sentiment API fails, use price-action proxy
# - Pipeline health check endpoint
```

### 4.3 Data Quality Checks
```python
# data_quality.py — new module
# - Staleness check: price data < 5min old
# - Completeness: all watchlist assets have data
# - Sanity: no $0 prices, no NaN indicators
# - Report quality: LLM response length > minimum
```

### 4.4 Performance Monitoring
Track over time:
- Win rate per analyst (which analyst's signals are most accurate?)
- Debate quality (how often does debate change the outcome?)
- PM override rate (how often does portfolio manager reject?)
- Signal-to-noise ratio
- Rolling Sharpe ratio

---

## Phase 5: Dashboard → Real Agent Integration (Days 11-12)

### 5.1 Live Agent Output in Dashboard
**Current:** Static agent descriptions ("RSI, MACD, SMA-200...")
**Target:** Show latest analyst reports, debate results, PM decisions

New API endpoints:
- `/api/trading/latest-reports` — most recent analyst reports
- `/api/trading/latest-debate` — most recent debate round
- `/api/trading/latest-decision` — most recent PM decision
- `/api/trading/pipeline-log` — streaming pipeline output (SSE)

### 5.2 Pipeline Control from Dashboard
**Current:** "Run Analysis" button works but feedback is binary (running/done)
**Target:** Live pipeline progress, agent-by-agent status

- Pipeline progress bar (step 1/7: Data Collection...)
- Per-agent status (Technical ✓, Sentiment ✓, On-chain ⏳, Macro ⏳)
- Live debate log (Round 1 complete, Round 2 in progress...)
- PM decision display with rationale

### 5.3 Portfolio View
**Current:** Static exposure gauge
**Target:** Live portfolio with position cards, P&L, risk metrics

- Open positions table with current P&L
- Equity curve chart (30d)
- Risk dashboard: Sharpe, max drawdown, win rate
- Trade journal with decision audit trail

---

## Phase 6: Strategy Evolution (Days 13-15)

### 6.1 Reflection & Learning Loop
**Current:** `reflection_engine.py` exists but needs verification
**Target:** Automated strategy improvement

```python
# After each closed trade:
# 1. Was the signal correct? (direction + magnitude)
# 2. Which analyst had the most accurate read?
# 3. Did the debate improve or degrade the decision?
# 4. Should position sizing be adjusted for this asset/regime?
# 5. Update per-analyst accuracy scores
# 6. Feed reflections into next debate prompts
```

### 6.2 Strategy Variants & A/B Testing
Run parallel strategies on paper:
- Strategy A: Current (debate consensus)
- Strategy B: Momentum-only (no debate, TA > 70 conviction)
- Strategy C: Contrarian (fade extreme sentiment)
- Compare performance, auto-select best

### 6.3 Regime-Aware Position Sizing
**Current:** Fixed 5% per position
**Target:** Dynamic sizing by market regime

| Regime | Max Position | Max Total | Stop Width |
|--------|-------------|-----------|------------|
| Strong Bull | 8% | 60% | 2x ATR |
| Weak Bull | 5% | 40% | 1.5x ATR |
| Range/Neutral | 3% | 25% | 1x ATR |
| Weak Bear | 1% | 10% | 0.5x ATR |
| Strong Bear | 0% | 0% | N/A |

---

## Phase 7: Live Trading Readiness (Day 16+)

### 7.1 Paper Trading Validation
**Requirement before live:** 30 days profitable paper trading
- Minimum 20 closed trades
- Win rate > 45%
- Sharpe > 0.5
- Max drawdown < 15%
- No kill switch activations from real emergencies

### 7.2 Live Trading Checklist
Before flipping `LIVE_TRADING_ENABLED = True`:
- [ ] 30 days paper trading with positive returns
- [ ] All data pipelines running without errors for 7 days
- [ ] Kill switch tested (activate → verify all positions frozen)
- [ ] Circuit breaker tested (3% daily loss → halt)
- [ ] Telegram alerts working
- [ ] Broker API keys configured with trading permissions
- [ ] Start with $500 max position size
- [ ] Manual review required for first 10 trades
- [ ] Emergency contact / escalation path defined

### 7.3 Gradual Capital Deployment
```
Week 1: $100 max per position, 1 position max
Week 2: $250 max per position, 2 positions max  
Week 3: $500 max per position, 3 positions max
Week 4+: Full allocation (capped at portfolio limits)
```

---

## Risk & Kill Switch Architecture

```
                    ┌─────────────┐
                    │ Kill Switch  │ ← Dashboard toggle / CLI
                    │ (file flag)  │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Circuit   │ │ Daily    │ │ Max      │
        │ Breaker   │ │ Loss 3%  │ │ Drawdown │
        │ Exposure  │ │          │ │ 15%      │
        │ >50% halt │ │          │ │          │
        └──────────┘ └──────────┘ └──────────┘
              │            │            │
              └────────────┼────────────┘
                           ▼
                    ┌─────────────┐
                    │ EXECUTION   │
                    │ GATE        │
                    │ (all must   │
                    │  pass)      │
                    └─────────────┘
```

---

## Implementation Order (What to Build First)

**Week 1: Data (highest leverage)**
1. Live macro collection (yfinance — works today, no API key needed)
2. Live sentiment (CryptoPanic free tier or Fear & Greed Index)
3. Fundamentals collection (yfinance)
4. On-chain proxy from Binance large trades

**Week 2: Safety**
5. Trailing stops
6. Position monitor + alerts
7. Multi-timeframe confirmation
8. Correlation check

**Week 3: Automation**
9. Cron scheduling
10. Auto-execution gates
11. Telegram alerts
12. Error recovery

**Week 4: Intelligence**
13. Live dashboard integration
14. Reflection loop
15. Regime-aware sizing
16. A/B strategy testing

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Data freshness | Hours/days (stale files) | < 5 minutes |
| On-chain analysis | Stub (neutral always) | Real whale/flows data |
| Pipeline autonomy | Manual trigger | Fully scheduled |
| Paper trade execution | Works but unverified | Verified end-to-end |
| Dashboard interactivity | SSR only | Real-time agent outputs |
| Risk controls | Basic (file-based) | Multi-layer with Telegram alerts |
| Backtesting coverage | 2022-2025 daily | Add 1h/4h timeframes |
| Win rate | Unknown | Tracked per-analyst |
