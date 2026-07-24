# Trading Agent

Autonomous multi-signal crypto/stock trading agent with paper trading, live execution capability, and a real-time dashboard.

**Status:** Paper trading active. 3 models trained (LightGBM + LSTM). PredScope + Adanos alpha signals live.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      DATA COLLECTORS                        │
│  predscope_collector  adanos_collector  twelve_data.py      │
│  (Polymarket odds)    (social sentiment) (price data)       │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    SIGNAL GENERATION                        │
│  predscope_signals.py  adanos_signals.py                   │
│  ml_predictor.py (LightGBM)  dl_predictor.py (LSTM/GRU)    │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                      ASSEMBLY (assembly.py)                 │
│  Priority: PredScope > Adanos+ML > LightGBM > TA voting    │
│  Regime filter → backtest gate → position sizing → execute  │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌──────────────────────┴──────────────────────────────────────┐
│              EXECUTION                 SAFETY               │
│  paper_trader.py (paper)              enforce_stops.py     │
│  execute_live.py (dryrun/live)        alert_manager.py     │
│  exchange/ccxt_bridge.py (CCXT)       circuit breaker      │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    DASHBOARD (port 3002)                    │
│  Next.js 16 — 13 API routes — 10 trading cards             │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
uv sync --frozen --extra test
TRADING_DATA_ROOT="$HOME/.local/share/trading-agent" \
  uv run --frozen --extra test python main.py --mode snapshot --research-only
```

## Setup

### Prerequisites
- Python 3.11+ (venv at `.venv/`)
- Node.js 22+ (for dashboard)
- CCXT (`pip install ccxt`)
- LightGBM, PyTorch, scikit-learn, pandas, numpy

### Protected runtime configuration

Pass credentials through the service environment. The backend never searches
for environment files. `TRADING_ENV_FILE` may point to one explicitly
provisioned, protected environment file when the operator requires file-based
configuration.

```bash
ADANOS_API_KEY=your_key_here        # Social sentiment API
DEEPSEEK_API_KEY=your_key_here      # LLM debate/reflection
# Exchange keys (for live/dryrun mode):
KRAKEN_API_KEY=your_key
KRAKEN_SECRET=your_secret
COINBASE_API_KEY=your_key
COINBASE_SECRET=your_secret
```

### Install
```bash
uv sync --frozen --extra test
```

## Usage

### Pipeline Modes

| Command | What it does |
|---------|-------------|
| `python main.py --mode snapshot --research-only` | Attributed research snapshot; execution disabled |
| `python main.py --mode debate --research-only` | Attributed LLM adversarial debate; execution disabled |
| `python main.py --mode backtest --research-only` | Attributed research backtest; execution disabled |
| `python main.py --mode reflect` | Daily strategy reflection + optimization |

The Phase 4 research backend defaults every `allow_execution` argument to
`False`. Snapshot, debate, backtest, and replay reject CLI invocations that omit
`--research-only`; legacy execution is reachable only through an explicit
programmatic `allow_execution=True` call outside the Phase 4 command manifest.

### Trading Modes

The external mode file controls execution. Its path is
`TRADING_MODE_FILE`, or `$TRADING_DATA_ROOT/.mode` when unset:

```bash
uv run python set_mode.py paper     # Paper trading (default, safe)
uv run python set_mode.py dryrun    # CCXT sandbox
```

**Never set `LIVE_EXECUTION_ENABLED=True` without:**
1. Verified paper/dryrun profitability (Sharpe > 0.5 for 30+ days)
2. Small position sizes (max 5% per trade)
3. Manual monitoring

### Signal Sources

| Source | Alpha Type | Confidence | Status |
|--------|-----------|------------|--------|
| **PredScope** | Polymarket prediction odds | Market-aggregated | ✅ Live |
| **Adanos** | Social sentiment spikes | Lead indicator | ✅ Live |
| **LightGBM** | ML on 27 features | Model-based | ⚠️ ETH only (Sharpe 0.43) |
| **LSTM/GRU** | Deep learning sequences | Research-grade | ❌ Not production |
| **TA Voting** | Technical indicators | Legacy fallback | ⚠️ Low confidence |

### Backtesting

```bash
# Single symbol
python backtest_runner.py --mode compare --symbol ETH/USDT --start 2025-01-01

# Walk-forward (all crypto)
python backtest_runner.py --mode walk-forward --all --start 2024-01-01

# Train ML models
python ml_predictor.py --train --all
python dl_predictor.py --train --all  # LSTM (research only)
```

### Collectors

Run independently:
```bash
python predscope_collector.py    # Fetches Polymarket odds
python adanos_collector.py       # Fetches social sentiment
```

Output goes to `reports/` directory. Signals extracted to `signals/`.

## Dashboard

**URL:** http://localhost:3002

**Cards:**
- Backtest Results — walk-forward metrics per symbol
- Exchange Status — mode, connectivity, kill switch state
- Live Positions — paper/dryrun positions with PnL
- Prediction Market — Polymarket signals
- Social Sentiment — Adanos buzz/spikes
- Portfolio — equity curve, allocation
- Signals — active trading signals
- Performance — PnL tracking

**API Routes (13 total, all verified 200):**
```
/api/trading/backtest          /api/trading/live-positions
/api/trading/backtest-results  /api/trading/mode
/api/trading/exchange-status   /api/trading/performance
/api/trading/portfolio         /api/trading/pnl
/api/trading/signals           /api/trading/prediction
/api/trading/sentiment         /api/trading/status
/api/trading/summary
```

## Safety Systems

| System | What it does | Trigger |
|--------|-------------|---------|
| **Circuit Breaker** | Blocks all trades | 15% drawdown or 3% daily loss |
| **Kill Switch** | `LIVE_EXECUTION_ENABLED=False` | Manual only |
| **Stop Loss** | Auto-sell position | 5% below entry |
| **Take Profit** | Auto-sell position | 10% above entry |
| **Backtest Gate** | Blocks symbol | Sharpe < 0.5 or win rate < 40% |
| **Regime Filter** | Reduces size / blocks | Choppy/low-vol regimes |
| **Position Sizing** | Kelly-style | Configurable max positions |
| **Mode File** | Routes execution | `.mode` file (paper/dryrun/live) |

## Directory Structure

```
legacy/research-backend/  # Source (never runtime authority)
├── assembly.py              # Signal aggregation + decision
├── main.py                  # Pipeline orchestrator
├── ml_predictor.py          # LightGBM models
├── dl_predictor.py          # LSTM/GRU (research)
├── predscope_signals.py     # Polymarket → signals
├── adanos_signals.py        # Social → signals
├── predscope_collector.py   # Polymarket data fetch
├── adanos_collector.py      # Social data fetch
├── backtest_engine.py       # Event-driven backtester
├── backtest_runner.py       # CLI + walk-forward
├── backtest_gate.py         # Gate checker
├── execute_live.py          # CCXT order execution
├── paper_trader.py          # Paper trading simulation
├── enforce_stops.py         # Stop-loss/take-profit
├── alert_manager.py         # Telegram notifications
├── exchange/                # CCXT bridge
│   ├── adapter.py           # Exchange wrapper
│   ├── ccxt_bridge.py       # Public API
│   ├── secrets.py           # Credential mgmt
│   └── ws_feed.py           # WebSocket feed
├── tests/
│   └── test_integration.py  # 47 integration tests
└── PLAN.md                  # Development plan

$TRADING_DATA_ROOT/        # External runtime state
├── reports/
├── signals/
├── memory/
├── .mode
└── .kill_switch
```

## Exchange Support

| Exchange | Canada-Legal | Paper | Dryrun | Live |
|----------|-------------|-------|--------|------|
| Kraken | ✅ | ✅ | ✅ | ⚠️ Blocked |
| Coinbase | ✅ | ✅ | ⚠️ Sandbox WIP | ⚠️ Blocked |
| Crypto.com | ✅ | ✅ | ❌ Not configured | ⚠️ Blocked |
| Binance | ❌ Blocked | — | — | — |
| Bybit | ❌ Blocked | — | — | — |
| OKX | ❌ Blocked | — | — | — |

## Testing

```bash
uv run --frozen --extra test pytest -q
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Dashboard won't start | `fuser -k 3002/tcp` then restart |
| No PredScope signals | Run `python predscope_collector.py` manually |
| Backtest gate blocking | Check `memory/backtest/` for walk_forward_*.json |
| Pipeline not running | Check crons: `cronjob action=list` |
| CCXT import error | `pip install ccxt` in project venv |
| LSTM model won't load | Models are research-grade, use LightGBM instead |

## Known Limitations

- **BTC model**: Negative Sharpe (-0.41) — blocked by backtest gate
- **LSTM/GRU**: Overfits on 9K-bar dataset — zero tradeable signals
- **Coinbase sandbox**: CCXT URL config issue — Kraken works
- **Stock pipeline**: Uses yfinance (delayed data, US market hours only)
