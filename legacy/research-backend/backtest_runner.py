#!/usr/bin/env python3
"""
backtest_runner.py — CLI + pipeline integration for the event-driven backtesting engine.

Usage:
  python backtest_runner.py --symbol BTC/USDT --start 2025-01-01 --end 2026-05-01
  python backtest_runner.py --all --start 2024-06-01
  python backtest_runner.py --mode walk-forward --symbol BTC/USDT --windows 5
  python backtest_runner.py --mode compare --symbol BTC/USDT

Pipeline:
  from backtest_runner import run_walk_forward
  result = run_walk_forward("BTC/USDT", "2025-01-01", "2026-05-01")
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from backtest_engine import (
    BacktestConfig, BacktestEngine, DataHandler,
    MLStrategy, BaselineStrategy, Portfolio, Broker,
    compute_aggregate_metrics, compute_sharpe, compute_sortino,
    compute_max_drawdown_pct, compute_calmar, compute_profit_factor,
    compute_equity_returns, _annual_factor,
    BACKTEST_DIR, MODELS_DIR, CACHE_DIR, PROJECT_ROOT,
)
from risk_engine import validate_walk_forward

log = logging.getLogger("backtest_runner")

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
STOCK_SYMBOLS = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]


# ── Walk-Forward Training ──────────────────────────────────────────────────────

def _train_model_on_window(df: pd.DataFrame, symbol: str):
    """Train a LightGBM classifier on a training DataFrame. Returns Booster + platt params."""
    from ml_predictor import generate_features, _make_lgbm

    feats = generate_features(df).dropna()
    feature_cols = [c for c in feats.columns
                    if c not in ("target_1h_return", "target_up")
                    and feats[c].dtype in ("float64", "float32", "int64", "int32")]

    y = feats["target_up"]
    X = feats[feature_cols]

    if len(X) < 200:
        log.warning("Only %d training rows for %s — skipping window", len(X), symbol)
        return None, None, None, None

    n_pos = max(int(y.sum()), 1)
    n_neg = max(len(y) - n_pos, 1)
    spw = n_neg / n_pos if n_pos > 0 else 1.0

    model = _make_lgbm(scale_pos_weight=spw)
    model.fit(X.values, y.values)

    # Platt scaling on last 20% of training data
    split = int(len(X) * 0.8)
    if split > 10 and len(X) - split > 10:
        from sklearn.linear_model import LogisticRegression
        X_cal = X.iloc[split:].values
        y_cal = y.iloc[split:].values
        raw_proba = model.predict_proba(X_cal)[:, 1]
        eps = 1e-12
        logit = np.log((raw_proba + eps) / (1 - raw_proba + eps))
        platt = LogisticRegression(C=np.inf, solver="lbfgs")
        platt.fit(logit.reshape(-1, 1), y_cal)
        platt_a, platt_b = float(platt.coef_[0, 0]), float(platt.intercept_[0])
    else:
        platt_a, platt_b = None, None

    booster = model.booster_
    return booster, feature_cols, platt_a, platt_b


def _run_window_backtest(df: pd.DataFrame, symbol: str, config: BacktestConfig,
                         model, platt_a, platt_b, feature_cols, timeframe: str) -> dict:
    """Run a backtest over a test window with a pre-trained model."""
    if df.empty or len(df) < 50:
        return _empty_window_result()

    strategy = MLStrategy(config, symbol=symbol, model=model)
    strategy._precompute_features(df)
    if platt_a is not None:
        strategy._platt_a = platt_a
        strategy._platt_b = platt_b

    portfolio = Portfolio(config)
    broker = Broker(config)

    bars = DataHandler._to_bars_static(df)
    if not bars:
        return _empty_window_result()

    for i, bar in enumerate(bars):
        exit_fills = portfolio.check_exits(bar, i)
        entry_fills = broker.process_orders(bar, i, portfolio, symbol)
        signal = strategy.next(i, bar, portfolio.positions)
        if signal.action in ("BUY", "SELL"):
            broker.place_order(signal, bar, i, portfolio, symbol)
        portfolio.record_snapshot(bar.timestamp, bar)

    if portfolio.positions and bars:
        portfolio.close_all(bars[-1], len(bars) - 1)

    metrics = compute_aggregate_metrics(portfolio.equity_curve, portfolio.trade_log, config, timeframe)
    eq_values = [e[1] for e in portfolio.equity_curve]
    return {
        "metrics": metrics,
        "trades": portfolio.trade_log,
        "equity_curve": portfolio.equity_curve,
        "sharpe": metrics["sharpe"],
        "total_pnl": metrics["total_pnl"],
        "total_pnl_pct": metrics["total_pnl_pct"],
        "win_rate": metrics["avg_win_rate"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "trade_count": metrics["total_trades"],
        "final_equity": eq_values[-1] if eq_values else config.initial_capital,
    }


def _empty_window_result() -> dict:
    return {
        "metrics": {},
        "trades": [],
        "equity_curve": [],
        "sharpe": 0.0, "total_pnl": 0.0, "total_pnl_pct": 0.0,
        "win_rate": 0.0, "max_drawdown_pct": 0.0,
        "trade_count": 0, "final_equity": 0.0,
    }


# Static helper for walk-forward (avoids creating DataHandler instances)
def _df_to_bars(df: pd.DataFrame) -> list:
    bars = []
    for idx, row in df.iterrows():
        try:
            o, h, l, c, v = (float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"]),
                              float(row["volume"]))
            if not all(np.isfinite(x) for x in (o, h, l, c, v)):
                continue
            from backtest_engine import Bar
            bars.append(Bar(timestamp=idx, open=o, high=h, low=l, close=c, volume=v))
        except (ValueError, TypeError):
            continue
    return bars


DataHandler._to_bars_static = staticmethod(_df_to_bars)


# ── Walk-Forward Orchestration ──────────────────────────────────────────────────

def run_walk_forward(symbol: str, start: str, end: str,
                     n_windows: int = 5, timeframe: str = "1h",
                     config: BacktestConfig = None) -> dict:
    """
    Run walk-forward backtest for a symbol.

    Splits data into n_windows expanding windows. For each window:
      - Train LightGBM on all data before the test window
      - Run backtest on the test window
    Produces aggregate metrics across all windows.

    Returns dict compatible with backtest_gate.py.
    """
    config = config or BacktestConfig()
    dh = DataHandler(symbol, start, end, timeframe)
    df = dh.load()
    if df is None or df.empty:
        log.error("No data for %s (%s → %s)", symbol, start, end)
        return _empty_walk_forward_result(symbol)

    if len(df) < 200:
        log.error("Insufficient data for %s: %d candles (need >= 200)", symbol, len(df))
        return _empty_walk_forward_result(symbol)

    # Determine walk-forward windows
    total_bars = len(df)
    min_train = max(200, total_bars // (n_windows + 2))
    remaining = total_bars - min_train
    test_size = max(24, remaining // n_windows)  # at least 1 day of 1h bars

    log.info("Walk-forward: %s, %d bars, %d windows, ~%d bars/test",
             symbol, total_bars, n_windows, test_size)

    all_trades = []
    all_equity = []
    windows = []
    window_metrics_agg = {
        "sharpe": [], "total_pnl": [], "win_rate": [], "max_drawdown_pct": [],
        "trade_count": [], "buy_count": [], "sell_count": [],
    }
    sortino_vals = []  # per-window sortino for validation gate
    ann_factor = _annual_factor(timeframe)  # pre-compute for per-window sortino

    for w in range(n_windows):
        train_end_idx = min_train + w * test_size
        test_start_idx = train_end_idx
        test_end_idx = min(test_start_idx + test_size, total_bars)

        if test_end_idx - test_start_idx < 24:
            log.warning("Window %d: too few test bars (%d), skipping",
                        w, test_end_idx - test_start_idx)
            continue

        train_df = df.iloc[:train_end_idx]
        test_df = df.iloc[test_start_idx:test_end_idx]

        train_start_str = str(df.index[0])[:10]
        train_end_str = str(df.index[train_end_idx - 1])[:10] if train_end_idx > 0 else start
        test_start_str = str(df.index[test_start_idx])[:10]
        test_end_str = str(df.index[test_end_idx - 1])[:10]

        log.info("Window %d/%d: train=%s→%s, test=%s→%s",
                 w + 1, n_windows, train_end_str, train_end_str, test_start_str, test_end_str)

        # Train model
        model, feature_cols, platt_a, platt_b = _train_model_on_window(train_df, symbol)
        if model is None:
            windows.append({
                "window": w,
                "train_end": train_end_str,
                "window_start": test_start_str,
                "window_end": test_end_str,
                "total_pnl": 0.0, "total_pnl_pct": 0.0, "win_rate": 0.0,
                "max_drawdown_pct": 0.0, "trade_count": 0,
                "buy_count": 0, "sell_count": 0,
                "final_equity": config.initial_capital,
                "sharpe": 0.0, "regime": "unknown",
            })
            continue

        # Run backtest on test window
        result = _run_window_backtest(
            test_df, symbol, config, model, platt_a, platt_b, feature_cols, timeframe,
        )

        all_trades.extend(result["trades"])
        all_equity.extend(result["equity_curve"])

        buy_count = sum(1 for t in result["trades"] if t.get("side") == "BUY")
        sell_count = len(result["trades"]) - buy_count

        windows.append({
            "window": w,
            "train_end": train_end_str,
            "window_start": test_start_str,
            "window_end": test_end_str,
            "total_pnl": result["total_pnl"],
            "total_pnl_pct": result["total_pnl_pct"],
            "win_rate": result["win_rate"],
            "max_drawdown_pct": result["max_drawdown_pct"],
            "trade_count": result["trade_count"],
            "buy_count": buy_count,
            "sell_count": sell_count,
            "final_equity": result["final_equity"],
            "sharpe": result["sharpe"],
            "regime": "unknown",
        })

        window_metrics_agg["sharpe"].append(result["sharpe"])
        window_metrics_agg["total_pnl"].append(result["total_pnl"])
        window_metrics_agg["win_rate"].append(result["win_rate"])
        window_metrics_agg["max_drawdown_pct"].append(result["max_drawdown_pct"])
        window_metrics_agg["trade_count"].append(result["trade_count"])
        window_metrics_agg["buy_count"].append(buy_count)
        window_metrics_agg["sell_count"].append(sell_count)

        # Compute sortino for this window (for validation gate)
        window_rets = compute_equity_returns(result["equity_curve"]) if result["equity_curve"] else []
        window_sortino = compute_sortino(window_rets, ann_factor) if window_rets else 0.0
        sortino_vals.append(window_sortino)

    if not windows:
        return _empty_walk_forward_result(symbol)

    # Aggregate across all windows
    all_window_trades = [t for t in all_trades]
    total_pnl = sum(t["pnl"] for t in all_window_trades)
    total_pnl_pct = (total_pnl / config.initial_capital) * 100
    win_count = sum(1 for t in all_window_trades if t["pnl"] > 0)
    avg_win_rate = win_count / len(all_window_trades) if all_window_trades else 0.0

    sharpe_vals = window_metrics_agg["sharpe"]
    profitable = sum(1 for s in sharpe_vals if s > 0)
    consistency = profitable / len(sharpe_vals) if sharpe_vals else 0.0

    # Full equity curve across all windows
    eq_rets = compute_equity_returns(all_equity) if all_equity else []
    eq_values = [e[1] for e in all_equity]
    max_dd = compute_max_drawdown_pct(eq_values) if eq_values else 0.0

    ann_factor = _annual_factor(timeframe)
    sharpe = compute_sharpe(eq_rets, ann_factor) if eq_rets else 0.0
    sortino = compute_sortino(eq_rets, ann_factor) if eq_rets else 0.0
    calmar = compute_calmar(total_pnl_pct, max_dd) if max_dd > 0 else 0.0
    profit_factor = compute_profit_factor(all_window_trades)

    # Avg trade duration
    avg_duration = 0.0
    if all_window_trades:
        durations = []
        for t in all_window_trades:
            try:
                entry = datetime.fromisoformat(t["entry_time"])
                exit_t = datetime.fromisoformat(t["exit_time"])
                durations.append((exit_t - entry).total_seconds() / 3600)
            except (ValueError, KeyError):
                pass
        avg_duration = float(np.mean(durations)) if durations else 0.0

    aggregate = {
        "symbol": symbol,
        "total_windows": len(windows),
        "total_trades": len(all_window_trades),
        "total_pnl": round(total_pnl, 4),
        "total_pnl_pct": round(total_pnl_pct, 4),
        "avg_win_rate": round(avg_win_rate, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "calmar": round(calmar, 4),
        "profit_factor": round(profit_factor, 4),
        "consistency": round(consistency, 4),
        "profitable_windows": profitable,
        "avg_trade_duration_hours": round(avg_duration, 2),
        "regime_breakdown": {},
    }

    # Walk-forward validation gate
    gate = validate_walk_forward(
        sharpe_per_window=sharpe_vals,
        sortino_per_window=sortino_vals,
        pnl_per_window=window_metrics_agg["total_pnl"],
        max_drawdown=max_dd,
    )
    aggregate["gate"] = {
        "passed": gate.passed,
        "consistency": gate.consistency,
        "rejection_reasons": gate.rejection_reasons,
    }

    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "train_days": None,
            "test_days": None,
            "total_bars": len(df),
            "n_windows": n_windows,
        },
        "aggregate": aggregate,
        "windows": windows,
        "equity_curve": [(ts.isoformat(), round(eq, 4)) for ts, eq in all_equity],
        "trades": all_window_trades,
    }


def _empty_walk_forward_result(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {},
        "aggregate": {
            "symbol": symbol, "total_windows": 0, "total_trades": 0,
            "total_pnl": 0.0, "total_pnl_pct": 0.0, "avg_win_rate": 0.0,
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0,
            "calmar": 0.0, "profit_factor": 0.0, "consistency": 0.0,
            "profitable_windows": 0, "avg_trade_duration_hours": 0.0,
            "regime_breakdown": {},
        },
        "windows": [],
        "equity_curve": [],
        "trades": [],
    }


# ── Single Backtest ─────────────────────────────────────────────────────────────

def run_single_backtest(symbol: str, start: str, end: str,
                        timeframe: str = "1h",
                        config: BacktestConfig = None) -> dict:
    """Run a single backtest with MLStrategy over the full date range."""
    config = config or BacktestConfig()
    dh = DataHandler(symbol, start, end, timeframe)
    strategy = MLStrategy(config, symbol=symbol)
    strategy.init()

    engine = BacktestEngine(symbol, strategy, config, dh, timeframe)
    result = engine.run()
    return result


def run_compare_backtest(symbol: str, start: str, end: str,
                         timeframe: str = "1h",
                         config: BacktestConfig = None) -> dict:
    """Run both MLStrategy and BaselineStrategy for comparison."""
    config = config or BacktestConfig()
    dh = DataHandler(symbol, start, end, timeframe)

    # ML Strategy
    ml_strategy = MLStrategy(config, symbol=symbol)
    ml_strategy.init()
    ml_engine = BacktestEngine(symbol, ml_strategy, config, dh, timeframe)
    ml_result = ml_engine.run()

    # Baseline (buy & hold)
    dh2 = DataHandler(symbol, start, end, timeframe)
    bl_strategy = BaselineStrategy(config)
    bl_engine = BacktestEngine(symbol, bl_strategy, config, dh2, timeframe)
    bl_result = bl_engine.run()

    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config.to_dict(),
        "ml_strategy": ml_result.get("aggregate", {}),
        "baseline": bl_result.get("aggregate", {}),
        "ml_equity_curve": ml_result.get("equity_curve", []),
        "baseline_equity_curve": bl_result.get("equity_curve", []),
        "ml_trades": ml_result.get("trades", []),
        "baseline_trades": bl_result.get("trades", []),
    }


# ── Output ──────────────────────────────────────────────────────────────────────

def save_result(result: dict, prefix: str = "walk_forward"):
    """Save backtest result to memory/backtest/ with timestamp."""
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    symbol = result.get("symbol", "unknown").replace("/", "_")
    filename = f"{prefix}_{symbol}_{ts}.json"
    path = BACKTEST_DIR / filename
    path.write_text(json.dumps(result, indent=2, default=str))
    log.info("Saved → %s", path)
    return path


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Event-driven backtesting engine — CLI + pipeline integration",
    )
    parser.add_argument("--symbol", type=str, default=None,
                        help="Symbol to backtest (e.g. BTC/USDT, AAPL)")
    parser.add_argument("--all", action="store_true",
                        help="Run all crypto symbols (BTC, ETH, SOL)")
    parser.add_argument("--stocks", action="store_true",
                        help="Run stock symbols instead of crypto")
    parser.add_argument("--start", type=str, default="2025-01-01",
                        help="Start date YYYY-MM-DD (default: 2025-01-01)")
    parser.add_argument("--end", type=str, default="2026-05-01",
                        help="End date YYYY-MM-DD (default: 2026-05-01)")
    parser.add_argument("--mode", type=str, default="walk-forward",
                        choices=["walk-forward", "single", "compare"],
                        help="Backtest mode (default: walk-forward)")
    parser.add_argument("--windows", type=int, default=5,
                        help="Number of walk-forward windows (default: 5)")
    parser.add_argument("--timeframe", type=str, default="1h",
                        choices=["1h", "4h", "1d"],
                        help="Timeframe (default: 1h)")
    parser.add_argument("--capital", type=float, default=10000.0,
                        help="Initial capital (default: 10000)")
    parser.add_argument("--position-size", type=float, default=0.2,
                        help="Position size as fraction of equity (default: 0.2)")
    parser.add_argument("--max-positions", type=int, default=5,
                        help="Max concurrent positions (default: 5)")
    parser.add_argument("--commission", type=float, default=0.001,
                        help="Commission rate (default: 0.001 = 0.1%%)")
    parser.add_argument("--slippage", type=float, default=0.0005,
                        help="Slippage rate (default: 0.0005 = 0.05%%)")
    parser.add_argument("--stop-loss", type=float, default=0.05,
                        help="Stop loss pct (default: 0.05 = 5%%)")
    parser.add_argument("--take-profit", type=float, default=0.10,
                        help="Take profit pct (default: 0.10 = 10%%)")
    parser.add_argument("--prob-threshold", type=float, default=0.55,
                        help="ML probability threshold for BUY (default: 0.55)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if walk-forward validation gate rejects (CI/pipeline)")

    args = parser.parse_args()

    config = BacktestConfig(
        initial_capital=args.capital,
        commission_pct=args.commission,
        slippage_pct=args.slippage,
        position_size_pct=args.position_size,
        max_positions=args.max_positions,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
    )

    if args.all:
        symbols = STOCK_SYMBOLS if args.stocks else DEFAULT_SYMBOLS
    elif args.symbol:
        symbols = [args.symbol]
    else:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    for symbol in symbols:
        # Check if model exists for this symbol
        model_sym = symbol.split("/")[0].lower() if "/" in symbol else symbol.lower()
        model_path = MODELS_DIR / f"{model_sym}_lightgbm_latest.txt"
        if not model_path.exists() and args.mode != "compare":
            log.warning("No model for %s — skipping (run ml_predictor.py --train %s first)",
                        symbol, model_sym.upper())
            continue

        print(f"\n{'=' * 60}")
        print(f"  {args.mode.upper()} | {symbol} | {args.start} → {args.end}")
        print(f"{'=' * 60}")

        try:
            if args.mode == "walk-forward":
                result = run_walk_forward(
                    symbol, args.start, args.end,
                    n_windows=args.windows, timeframe=args.timeframe,
                    config=config,
                )
            elif args.mode == "single":
                result = run_single_backtest(
                    symbol, args.start, args.end,
                    timeframe=args.timeframe, config=config,
                )
            elif args.mode == "compare":
                result = run_compare_backtest(
                    symbol, args.start, args.end,
                    timeframe=args.timeframe, config=config,
                )
            else:
                log.error("Unknown mode: %s", args.mode)
                continue

            path = save_result(result, prefix=args.mode.replace("-", "_"))

            # Print summary
            agg = result.get("aggregate", {})
            print(f"\n── Results: {symbol} ──")
            if args.mode == "compare":
                ml = result.get("ml_strategy", {})
                bl = result.get("baseline", {})
                print(f"  ML Strategy:   Sharpe={ml.get('sharpe', 0):.4f}  PnL=${ml.get('total_pnl', 0):,.2f}  "
                      f"WinRate={ml.get('avg_win_rate', 0):.1%}  Trades={ml.get('total_trades', 0)}")
                print(f"  Buy & Hold:    Sharpe={bl.get('sharpe', 0):.4f}  PnL=${bl.get('total_pnl', 0):,.2f}  "
                      f"Trades={bl.get('total_trades', 0)}")
            else:
                print(f"  Sharpe:        {agg.get('sharpe', 0):.4f}")
                print(f"  Sortino:       {agg.get('sortino', 0):.4f}")
                print(f"  Total PnL:     ${agg.get('total_pnl', 0):,.2f} ({agg.get('total_pnl_pct', 0):.2f}%)")
                print(f"  Win Rate:      {agg.get('avg_win_rate', 0):.1%}")
                print(f"  Max Drawdown:  {agg.get('max_drawdown_pct', 0):.2f}%")
                print(f"  Calmar:        {agg.get('calmar', 0):.4f}")
                print(f"  Profit Factor: {agg.get('profit_factor', 0):.2f}")
                print(f"  Total Trades:  {agg.get('total_trades', 0)}")
                if "consistency" in agg:
                    print(f"  Consistency:   {agg.get('consistency', 0):.1%}")
            print(f"  Output:        {path}")

            # Walk-forward validation gate enforcement
            if args.mode == "walk-forward" and "gate" in agg:
                gate = agg["gate"]
                if not gate["passed"]:
                    reasons = "; ".join(gate["rejection_reasons"])
                    print(f"\n  VALIDATION GATE: REJECTED — {reasons}")
                    if args.strict:
                        log.error("Strict mode: walk-forward validation gate rejected — exiting")
                        sys.exit(1)
                else:
                    print(f"  Validation Gate: PASSED")

        except Exception as e:
            log.error("[%s] Backtest failed: %s", symbol, e, exc_info=True)

    print("\nDone.")


if __name__ == "__main__":
    main()
