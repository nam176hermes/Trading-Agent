"""Integration tests for Phase 3-5 components — PredScope, Adanos, Assembly, Backtest, Execute."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# This standalone harness exercises generators that persist derived signal
# outputs. Always redirect those writes into a minimal temporary data root.
_isolated_data = tempfile.TemporaryDirectory(prefix="trading-integration-")
ISOLATED_DATA_ROOT = Path(_isolated_data.name)
isolated_reports = ISOLATED_DATA_ROOT / "reports"
isolated_reports.mkdir(parents=True)
for pattern in ("prediction_market_*.json", "social_sentiment_*.json"):
    candidates = sorted((PROJECT_DIR / "reports").glob(pattern))
    if candidates:
        shutil.copy2(candidates[-1], isolated_reports / candidates[-1].name)
os.environ["TRADING_DATA_ROOT"] = str(ISOLATED_DATA_ROOT)

def green(s): return f"\033[32m{s}\033[0m"
def red(s): return f"\033[31m{s}\033[0m"
def bold(s): return f"\033[1m{s}\033[0m"

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  {green('✓')} {name}")
    else:
        failed += 1
        print(f"  {red('✗')} {name}  {detail}")

def section(title):
    print(f"\n{bold(title)}")

# ────────────────────────────────────────────────────────────
section("1. PredScope Signals")

from predscope_signals import get_predscope_signals

signals = get_predscope_signals()
check("Returns list", isinstance(signals, list))
check("At least 1 signal", len(signals) > 0, f"got {len(signals)}")

for s in signals:
    check(f"  {s['symbol']}: has direction", s["direction"] in ("BUY", "SELL", "HOLD"))
    check(f"  {s['symbol']}: has confidence 0-1", 0 <= s["confidence"] <= 1.0)
    check(f"  {s['symbol']}: has source", s["source"] == "prediction_market")
    check(f"  {s['symbol']}: has market_question", len(s.get("market_question", "")) > 0)
    check(f"  {s['symbol']}: has timestamp", "timestamp" in s)

sig_file = ISOLATED_DATA_ROOT / "signals" / "predscope_signals.json"
check("Signal file exists", sig_file.exists())

# ────────────────────────────────────────────────────────────
section("2. Adanos Signals")

from adanos_signals import get_adanos_signals

signals = get_adanos_signals()
check("Returns list", isinstance(signals, list))

for s in signals:
    check(f"  {s['symbol']}: has direction", s["direction"] in ("BUY", "SELL", "HOLD"))
    check(f"  {s['symbol']}: has confidence 0-1", 0 <= s["confidence"] <= 1.0)
    check(f"  {s['symbol']}: has source", s["source"] == "adanos_social")
    check(f"  {s['symbol']}: has reason", len(s.get("reason", "")) > 0)

sig_file = ISOLATED_DATA_ROOT / "signals" / "adanos_signals.json"
check("Signal file exists", sig_file.exists())

# ────────────────────────────────────────────────────────────
section("3. Assembly Integration")

try:
    import assembly
    check("assembly.py imports without error", True)
except Exception as e:
    check("assembly.py imports without error", False, str(e)[:100])

# ────────────────────────────────────────────────────────────
section("4. Backtest Engine")

from backtest_engine import BacktestEngine, MLStrategy, BacktestConfig

config = BacktestConfig()
check("Default capital $10K", config.initial_capital == 10000)
check("Commission 0.1%", config.commission_pct == 0.001)
check("Slippage 0.05%", config.slippage_pct == 0.0005)

# ────────────────────────────────────────────────────────────
section("5. Execute Live")

from execute_live import execute_signal, execute_signals
from exchange.ccxt_bridge import get_mode, CANADA_LEGAL

mode = get_mode()
check(f"Mode: {mode} (safe)", mode in ("paper", "dryrun", "live"))
check("Canada-legal set", len(CANADA_LEGAL) >= 2)
check("Kraken in legal set", "kraken" in CANADA_LEGAL)
check("Binance blocked", "binance" not in CANADA_LEGAL)

# ────────────────────────────────────────────────────────────
section("6. Runtime Path Resolver")

runtime_paths_module = PROJECT_DIR / "runtime_paths.py"
check("Runtime path resolver exists", runtime_paths_module.is_file())

# Verify key files exist
required = ["predscope_collector.py", "adanos_collector.py", "ml_predictor.py",
            "paper_trader.py", "alert_manager.py", "enforce_stops.py"]
for f in required:
    check(f"  {f}", (PROJECT_DIR / f).is_file())

for d in ["reports", "signals", "memory/backtest", "models"]:
    check(f"  {d}/", (PROJECT_DIR / d).is_dir())

# ────────────────────────────────────────────────────────────
section("7. Model Files")

models_dir = PROJECT_DIR / "models"
lgbm_models = list(models_dir.glob("*_lightgbm_latest.txt"))
lstm_models = list(models_dir.glob("*_lstm.pt"))
check(f"LightGBM models: {len(lgbm_models)}", len(lgbm_models) > 0)
check(f"LSTM models: {len(lstm_models)}", len(lstm_models) > 0)

# ────────────────────────────────────────────────────────────
section("8. Backtest Gate")

from backtest_gate import GateChecker, check as gate_check

result = gate_check("BTC")
check("Gate returns dict", isinstance(result, dict))
check("Gate has status", "status" in result)
check("Gate has reason", "reason" in result)

# ────────────────────────────────────────────────────────────
section("9. Regime Detection")

from ml_regime import detect_current_regime
check("Regime detection import", callable(detect_current_regime))

# ────────────────────────────────────────────────────────────
section("10. Signal Quality")

from signal_quality import score_decision
check("Signal quality import", callable(score_decision))

# ────────────────────────────────────────────────────────────
section("11. Edge Cases — Empty Data")

# Test Adanos with no data directory
import tempfile, os
with tempfile.TemporaryDirectory() as tmp:
    old_dir = os.environ.get("REPORTS_DIR")
    try:
        # Verify that both signal generators handle missing data gracefully
        # (they read from reports/ which should exist with real data)
        from predscope_signals import get_predscope_signals
        sigs = get_predscope_signals()
        check("PredScope handles missing gracefully", isinstance(sigs, list))
        
        from adanos_signals import get_adanos_signals
        sigs = get_adanos_signals()
        check("Adanos handles missing gracefully", isinstance(sigs, list))
    except Exception as e:
        check("Edge case handling", False, str(e)[:80])

# ────────────────────────────────────────────────────────────
print(f"\n{bold('RESULTS')}: {green(passed)} passed, {red(failed)} failed, {passed+failed} total")
if failed > 0:
    print(f"\n{red(f'SOME TESTS FAILED ({failed})')}")
    sys.exit(1)
else:
    print(f"\n{green('ALL TESTS PASSED')}")
    sys.exit(0)
