# Trading Agent — Handoff Checklist

## Daily Verification (5 min)

- [ ] Dashboard loads: `http://localhost:3002` — all cards render
- [ ] API routes healthy: `curl localhost:3002/api/trading/status` → 200
- [ ] Pipeline running: check `~/hermes/logs/snapshot_*.log` for latest run
- [ ] Signal files fresh: `ls -la signals/predscope_signals.json` (modified < 1h ago)
- [ ] Cron jobs healthy: check `cronjob action=list` — all trading jobs `last_status=ok`
- [ ] Kill switch ON: `cat crypto-research/.mode` should say `paper`
- [ ] No budget alerts: `cronjob action=list` → Daily Budget Report → `last_status=ok`

## Weekly Verification (15 min)

- [ ] Backtest update: `python backtest_runner.py --mode walk-forward --all`
- [ ] Check gate status: `python backtest_gate.py --symbol BTC` — should show BLOCK/ALLOW
- [ ] Portfolio review: `cat memory/paper/portfolio.json` — check PnL, positions
- [ ] Alert log clean: `tail -20 memory/alerts.jsonl` — no unexpected alerts
- [ ] ML model retrain: `python ml_predictor.py --train --all` (if >1 week since last train)
- [ ] Dashboard rebuild: `cd ~/trading-agent && npm run build` — should succeed

## Before Going Live (mandatory)

- [ ] Paper trading profitable for 30+ consecutive days (Sharpe > 0.5)
- [ ] All LightGBM models have positive Sharpe in walk-forward
- [ ] PredScope + Adanos signals verified against actual outcomes (7-day backtest)
- [ ] Dryrun mode tested for 1 week on Kraken sandbox (no errors)
- [ ] Position sizing verified: max 5% per trade, max 5 concurrent positions
- [ ] Telegram alerts working: test buy/sell notification received
- [ ] Emergency stop procedure documented: `echo "paper" > .mode` + `fuser -k 3002/tcp`
- [ ] API keys encrypted: `.keys.enc` exists, no plaintext keys in git
- [ ] `LIVE_EXECUTION_ENABLED` verified False before any live run

## Emergency Procedures

**Stop all trading immediately:**
```bash
echo "paper" > ~/.hermes/crypto-research/.mode
# Circuit breaker triggers at 15% drawdown automatically
```

**Restart dashboard:**
```bash
fuser -k 3002/tcp
cd ~/.hermes/trading-agent && npm run dev
```

**Force pipeline run:**
```bash
bash ~/.hermes/scripts/trading-pipeline.sh
```

**Check why trades blocked:**
```bash
cd ~/.hermes/crypto-research
python backtest_gate.py --symbol BTC
python ml_regime.py  # check current regime
```

## Known Issues (May 19, 2026)

| Issue | Impact | Workaround | Status |
|-------|--------|-----------|--------|
| BTC LightGBM Sharpe -0.41 | BTC trades blocked by gate | Use PredScope signals for BTC (SELL 0.91) | Permanent |
| LSTM overfits (zero signals) | No deep learning signals | Use LightGBM (ETH Sharpe 0.43) | Permanent |
| Coinbase sandbox 401 | Can't test Coinbase dryrun | Use Kraken sandbox (verified) | URL fix applied |
| Portfolio PnL was -1530% | Test position skewing | Reset paper portfolio | ✅ Fixed |
| backtest route was 500 | Wrong directory | Reads memory/backtest/ now | ✅ Fixed |
| freecrypto.py archived | Silent nohup failure | Removed from pipeline | ✅ Fixed |

## Git Repos

- **crypto-research**: `~/.hermes/crypto-research` (Python pipeline)
- **trading-agent**: `~/.hermes/trading-agent` (Next.js dashboard)
- **scripts**: `~/.hermes/scripts/` (pipeline + start scripts)

All committed as of 2026-05-19. No uncommitted changes in critical files.
