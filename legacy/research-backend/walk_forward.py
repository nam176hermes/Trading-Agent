"""
walk_forward.py — Walk-forward validation harness for the TA-voting strategy.

Runs the agent's signal logic (assembly.py) against historical data in rolling windows.
Train: 60 days → Test: 7 days → Roll forward 7 days → repeat.

For each window: generate daily signals using TA-only voting, simulate trades, track P&L.
Output: memory/backtest/walk_forward_<date>.json

Usage:
  python3 walk_forward.py --symbols BTC,ETH,SOL
  python3 walk_forward.py --symbols BTC,ETH,SOL --train-days 60 --test-days 7
  python3 walk_forward.py --symbols BTC --download  # fetch data via yfinance first
"""

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
from runtime_paths import data_root

log = logging.getLogger("walk_forward")

# ── Config ────────────────────────────────────────────────────────────────────

BACKTEST_DIR = data_root() / "memory" / "backtest"
HISTORICAL_DIR = BACKTEST_DIR / "historical"
RESULTS_DIR = BACKTEST_DIR

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL"]
DEFAULT_TRAIN_DAYS = 60
DEFAULT_TEST_DAYS = 7
MIN_CANDLES = 200  # minimum 1h candles for SMA-200

# ── Helpers ────────────────────────────────────────────────────────────────────


def load_historical(symbol: str) -> pd.DataFrame | None:
    """Load cached 1h OHLCV data for a symbol."""
    path = HISTORICAL_DIR / f"{symbol}_1h.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    df.sort_index(inplace=True)
    return df


def save_historical(symbol: str, df: pd.DataFrame):
    """Cache 1h OHLCV data to disk."""
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(HISTORICAL_DIR / f"{symbol}_1h.csv")


def download_historical(symbol: str, days: int = 365) -> pd.DataFrame | None:
    """Download 1h candles via yfinance Python library."""
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed. Install with: pip install yfinance")
        return None

    # Map symbol to yfinance ticker
    ticker_map = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
        "SOL": "SOL-USD",
        "DOGE": "DOGE-USD",
    }
    ticker = ticker_map.get(symbol.upper(), f"{symbol.upper()}-USD")

    end = datetime.now()
    start = end - timedelta(days=days + 30)  # extra for warm-up

    log.info("[%s] Downloading %dd of 1h candles via yfinance...", symbol, days)
    try:
        data = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1h",
            progress=False,
        )
    except Exception as e:
        log.error("[%s] yfinance download failed: %s", symbol, e)
        return None

    if data.empty:
        log.error("[%s] No data returned from yfinance", symbol)
        return None

    # Flatten multi-level columns if present
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    # Standardize column names
    col_map = {
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    }
    data.rename(columns=col_map, inplace=True)
    data.index.name = "timestamp"

    log.info("[%s] Downloaded %d candles", symbol, len(data))
    return data


def resample_to_daily(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h candles to daily OHLCV."""
    df = df_1h.copy()
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return daily


def df_to_ohlcv_list(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame to list-of-dicts format expected by ta_engine."""
    candles = []
    for idx, row in df.iterrows():
        candles.append({
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "date": str(idx)[:10],  # needed for date matching in simulate_window
        })
    return candles


# ── Signal generation (reuses assembly.py logic) ───────────────────────────────


def generate_signal_for_day(
    candles_up_to: list[dict],
    symbol: str,
) -> dict | None:
    """
    Run the TA-voting pipeline on historical data up to a given day.
    Returns a signal dict with suggestion, confidence, and entry_price.
    Uses the same weighted scoring as assembly.py's resolve_signal_and_confidence().
    """
    from ta_engine import calculate_indicators, interpret_rsi, interpret_macd
    from assembly import resolve_signal_and_confidence, evaluate_alerts

    ta = calculate_indicators(candles_up_to, symbol)
    if ta is None:
        return None

    rsi_sig = interpret_rsi(ta.get("rsi_14"))
    macd_sig = interpret_macd(ta.get("macd_line"), ta.get("macd_signal_line"))
    price_vs_sma = ta.get("price_vs_sma200")
    alerts = evaluate_alerts(ta)

    suggestion, confidence_str, warning, conflict = resolve_signal_and_confidence(
        rsi_signal=rsi_sig,
        macd_sig=macd_sig,
        price_vs_sma=price_vs_sma,
        alerts=alerts,
        sentiment=None,       # not available historically
        onchain_risk=None,     # not available historically
        derivatives_signal=None,
        ta=ta,
    )

    confidence_num = float(confidence_str)
    entry_price = ta.get("close")

    # Convert suggestion to actionable signal
    actionable = None
    if suggestion in ("BUY", "STRONG BUY"):
        actionable = "BUY"
    elif suggestion in ("SELL", "STRONG SELL"):
        actionable = "SELL"

    return {
        "suggestion": suggestion,
        "actionable": actionable,
        "confidence": confidence_num,
        "entry_price": entry_price,
        "conflict": conflict,
        "ta": {
            "rsi_14": ta.get("rsi_14"),
            "rsi_signal": rsi_sig,
            "macd_signal": macd_sig,
            "price_vs_sma200": price_vs_sma,
            "volume_trend_ratio": ta.get("volume_trend_ratio"),
        },
    }


# ── Trade simulation ───────────────────────────────────────────────────────────


def simulate_window(
    daily_df: pd.DataFrame,
    symbol: str,
    candles: list[dict],
    window_start_idx: int,
    window_end_idx: int,
    initial_capital: float = 100_000.0,
    position_pct: float = 0.05,
) -> dict:
    """
    Simulate trading within a test window.
    Walk forward day by day, generating signals and executing against next day's open.
    """
    dates = daily_df.index.tolist()
    close_prices = daily_df["close"].values
    open_prices = daily_df["open"].values

    capital = initial_capital
    position_shares = 0.0
    position_cost = 0.0
    trades = []
    daily_equity = []

    for i in range(window_start_idx, window_end_idx + 1):
        date = dates[i]

        # Generate signal using only data available up to this day
        # We need the candle index in the full list
        candle_idx = None
        for j, c in enumerate(candles):
            if isinstance(c, dict):
                c_date = c.get("date") or (
                    c.get("timestamp").strftime("%Y-%m-%d")
                    if hasattr(c.get("timestamp"), "strftime")
                    else str(c.get("timestamp", ""))[:10]
                )
            if str(date)[:10] == str(c_date)[:10]:
                candle_idx = j
                break

        if candle_idx is None or candle_idx < MIN_CANDLES:
            continue

        signal = generate_signal_for_day(candles[:candle_idx + 1], symbol)
        if signal is None:
            continue

        entry_price = signal["entry_price"]
        if entry_price is None or entry_price <= 0:
            continue

        # Execute trades at next day's open (no lookahead)
        next_idx = i + 1
        if next_idx >= len(dates):
            continue
        exec_price = float(open_prices[next_idx])

        # BUY logic
        if signal["actionable"] == "BUY" and signal["confidence"] >= 0.65 and position_shares == 0:
            cost = capital * position_pct
            shares = cost / exec_price
            if shares > 0 and cost <= capital:
                capital -= cost
                position_shares = shares
                position_cost = cost
                trades.append({
                    "date": str(dates[next_idx])[:10],
                    "action": "BUY",
                    "price": round(exec_price, 6),
                    "shares": round(shares, 8),
                    "cost": round(cost, 2),
                    "signal_confidence": signal["confidence"],
                })

        # SELL logic
        elif signal["actionable"] == "SELL" and position_shares > 0:
            proceeds = position_shares * exec_price
            pnl = proceeds - position_cost
            capital += proceeds
            trades.append({
                "date": str(dates[next_idx])[:10],
                "action": "SELL",
                "price": round(exec_price, 6),
                "shares": round(position_shares, 8),
                "proceeds": round(proceeds, 2),
                "pnl": round(pnl, 2),
                "signal_confidence": signal["confidence"],
            })
            position_shares = 0.0
            position_cost = 0.0

        # Mark to market
        equity = capital + (position_shares * float(close_prices[i]))
        daily_equity.append({"date": str(date)[:10], "equity": round(equity, 2)})

    # Close any open position at end of window
    if position_shares > 0 and window_end_idx < len(close_prices):
        final_price = float(close_prices[window_end_idx])
        proceeds = position_shares * final_price
        pnl = proceeds - position_cost
        capital += proceeds
        trades.append({
            "date": str(dates[window_end_idx])[:10],
            "action": "CLOSE",
            "price": round(final_price, 6),
            "shares": round(position_shares, 8),
            "proceeds": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "signal_confidence": 0.0,
            "note": "end-of-window close",
        })

    final_equity = capital
    total_pnl = final_equity - initial_capital
    total_pnl_pct = (total_pnl / initial_capital) * 100

    # Win rate
    sell_trades = [t for t in trades if t["action"] in ("SELL", "CLOSE")]
    wins = sum(1 for t in sell_trades if t.get("pnl", 0) > 0)
    win_rate = wins / len(sell_trades) if sell_trades else 0.0

    # Max drawdown
    peak = initial_capital
    max_dd = 0.0
    for de in daily_equity:
        eq = de["equity"]
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "window_start": str(dates[window_start_idx])[:10],
        "window_end": str(dates[window_end_idx])[:10],
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "win_rate": round(win_rate, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "trade_count": len(trades),
        "buy_count": len([t for t in trades if t["action"] == "BUY"]),
        "sell_count": len([t for t in trades if t["action"] in ("SELL", "CLOSE")]),
        "final_equity": round(final_equity, 2),
        "trades": trades,
        "daily_equity": daily_equity,
    }


# ── Sharpe ratio ───────────────────────────────────────────────────────────────


def compute_sharpe(daily_returns: list[float]) -> float:
    """Compute Sharpe ratio from daily returns (no risk-free rate for crypto)."""
    if len(daily_returns) < 3:
        return 0.0
    arr = np.array(daily_returns)
    mean = arr.mean()
    std = arr.std(ddof=1)
    if std == 0:
        return 0.0
    # Annualize: crypto trades 365 days
    return (mean / std) * math.sqrt(365)


def compute_daily_returns(equity_curve: list[dict]) -> list[float]:
    """Compute daily returns from equity curve."""
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]["equity"]
        curr = equity_curve[i]["equity"]
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


# ── Signal quality ─────────────────────────────────────────────────────────────


def signal_quality_score(signals: list[dict], daily_df: pd.DataFrame) -> float:
    """
    How often did BUY signals lead to profits?
    For each BUY signal, check forward return over next 24h (or next candle).
    """
    if not signals:
        return 0.0
    profitable = 0
    total = 0
    close_prices = daily_df["close"].values
    dates = [str(d)[:10] for d in daily_df.index]

    for sig in signals:
        if sig.get("actionable") != "BUY":
            continue
        sig_date = sig.get("date", "")
        if sig_date not in dates:
            continue
        idx = dates.index(sig_date)
        if idx + 24 >= len(close_prices):  # 24h forward (1h candles * 24)
            continue
        entry = sig.get("entry_price", 0)
        exit_price = float(close_prices[min(idx + 24, len(close_prices) - 1)])
        if entry > 0:
            total += 1
            if exit_price > entry:
                profitable += 1

    return profitable / total if total > 0 else 0.0


# ── Regime detection ───────────────────────────────────────────────────────────


def classify_regime(df: pd.DataFrame, window_start: int, window_end: int) -> str:
    """Classify market regime as trending or sideways based on ADX-like metric."""
    closes = df["close"].values[window_start:window_end + 1]
    if len(closes) < 20:
        return "unknown"

    # Simple trend strength: ratio of total move to sum of absolute daily moves
    total_move = abs(closes[-1] - closes[0])
    sum_moves = np.sum(np.abs(np.diff(closes)))
    if sum_moves == 0:
        return "sideways"
    efficiency = total_move / sum_moves

    if efficiency > 0.35:
        return "trending_up" if closes[-1] > closes[0] else "trending_down"
    return "sideways"


# ── Main walk-forward orchestrator ─────────────────────────────────────────────


def run_walk_forward(
    symbol: str,
    train_days: int = DEFAULT_TRAIN_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
    download: bool = False,
) -> dict | None:
    """
    Run walk-forward validation for a single symbol.

    Returns dict with per-window metrics and aggregate summary.
    """
    # Load or download data
    df_1h = load_historical(symbol)
    if df_1h is None or download:
        df_1h = download_historical(symbol, days=max(train_days + 200, 365))
        if df_1h is not None:
            save_historical(symbol, df_1h)

    if df_1h is None or df_1h.empty:
        log.error("[%s] No historical data available", symbol)
        return None

    # Resample to daily for walk-forward (signal generation is daily)
    daily_df = resample_to_daily(df_1h)
    candles = df_to_ohlcv_list(daily_df)

    n_days = len(daily_df)
    if n_days < train_days + test_days:
        log.error("[%s] Not enough data: %d days, need %d",
                  symbol, n_days, train_days + test_days)
        return None

    windows = []
    all_daily_returns = []
    regime_results = {"trending_up": [], "trending_down": [], "sideways": []}

    # Slide windows
    step = test_days
    window_num = 0
    train_end = train_days - 1

    while train_end + test_days < n_days:
        test_start = train_end + 1
        test_end = min(train_end + test_days, n_days - 1)

        log.info("[%s] Window %d: train=0..%d test=%d..%d",
                 symbol, window_num, train_end, test_start, test_end)

        result = simulate_window(
            daily_df=daily_df,
            symbol=symbol,
            candles=candles,
            window_start_idx=test_start,
            window_end_idx=test_end,
        )

        # Compute Sharpe for this window
        returns = compute_daily_returns(result["daily_equity"])
        sharpe = compute_sharpe(returns)

        # Classify regime
        regime = classify_regime(daily_df, test_start, test_end)

        window_metrics = {
            "window": window_num,
            "train_end": str(daily_df.index[train_end])[:10],
            **{k: v for k, v in result.items()
               if k not in ("trades", "daily_equity")},
            "sharpe": round(sharpe, 4),
            "regime": regime,
        }
        windows.append(window_metrics)

        all_daily_returns.extend(returns)

        if regime in regime_results:
            regime_results[regime].append(result["total_pnl_pct"])

        # Slide forward
        train_end += step
        window_num += 1

    if not windows:
        log.error("[%s] No windows produced", symbol)
        return None

    # ── Aggregate metrics ──
    total_pnl = sum(w["total_pnl"] for w in windows)
    total_pnl_pct = sum(w["total_pnl_pct"] for w in windows)
    avg_win_rate = np.mean([w["win_rate"] for w in windows])
    avg_sharpe = compute_sharpe(all_daily_returns)
    max_dd = max(w["max_drawdown_pct"] for w in windows)
    total_trades = sum(w["trade_count"] for w in windows)
    profitable_windows = sum(1 for w in windows if w["total_pnl"] > 0)
    consistency = profitable_windows / len(windows) if windows else 0

    # Regime breakdown
    regime_avg = {}
    for regime_name, pnls in regime_results.items():
        if pnls:
            regime_avg[regime_name] = {
                "avg_pnl_pct": round(np.mean(pnls), 2),
                "count": len(pnls),
            }

    aggregate = {
        "symbol": symbol,
        "total_windows": len(windows),
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "avg_win_rate": round(avg_win_rate, 4),
        "sharpe": round(avg_sharpe, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "consistency": round(consistency, 4),
        "profitable_windows": profitable_windows,
        "regime_breakdown": regime_avg,
    }

    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "train_days": train_days,
            "test_days": test_days,
            "total_days": n_days,
        },
        "aggregate": aggregate,
        "windows": windows,
    }


def save_results(symbol: str, results: dict):
    """Save walk-forward results to JSON."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"walk_forward_{symbol}_{date_str}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log.info("[%s] Results saved → %s", symbol, path)
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Walk-forward validation")
    parser.add_argument("--symbols", type=str, default="BTC,ETH,SOL",
                        help="Comma-separated symbols (default: BTC,ETH,SOL)")
    parser.add_argument("--train-days", type=int, default=DEFAULT_TRAIN_DAYS,
                        help=f"Training window in days (default: {DEFAULT_TRAIN_DAYS})")
    parser.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS,
                        help=f"Test window in days (default: {DEFAULT_TEST_DAYS})")
    parser.add_argument("--download", action="store_true",
                        help="Force re-download data via yfinance")
    parser.add_argument("--output", type=str, default=None,
                        help="Output path for combined results JSON")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    all_results = {}

    for symbol in symbols:
        log.info("═══ Walk-forward: %s ═══", symbol)
        results = run_walk_forward(
            symbol=symbol,
            train_days=args.train_days,
            test_days=args.test_days,
            download=args.download,
        )
        if results:
            path = save_results(symbol, results)
            all_results[symbol] = {
                "path": str(path),
                "aggregate": results["aggregate"],
            }

    # Print summary
    if all_results:
        print("\n" + "=" * 60)
        print("WALK-FORWARD SUMMARY")
        print("=" * 60)
        for symbol, data in all_results.items():
            agg = data["aggregate"]
            status = "✅" if agg["sharpe"] >= 0.5 and agg["avg_win_rate"] >= 0.45 else "❌"
            print(f"  {symbol}: {status}  Sharpe={agg['sharpe']:.2f}  "
                  f"WinRate={agg['avg_win_rate']:.1%}  "
                  f"P&L=${agg['total_pnl']:,.0f}  "
                  f"Consistency={agg['consistency']:.0%}")
        print("=" * 60)

        # Write combined output
        if args.output or len(symbols) > 1:
            out_path = args.output or str(RESULTS_DIR / f"walk_forward_combined_{datetime.now().strftime('%Y%m%d')}.json")
            combined = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "results": all_results,
            }
            with open(out_path, "w") as f:
                json.dump(combined, f, indent=2, default=str)
            print(f"\nCombined results → {out_path}")
    else:
        print("No results generated. Check that data is available.")
        sys.exit(1)


if __name__ == "__main__":
    main()
