#!/usr/bin/env python3
"""
alpha_backtest.py — Backtest PredScope + Adanos signals vs ML and B&H baselines.

Usage:
  python alpha_backtest.py --mode single --symbol BTC/USDT
  python alpha_backtest.py --mode compare --symbol BTC/USDT
  python alpha_backtest.py --mode walk-forward --symbol BTC/USDT --windows 3

Architecture:
  - Loads all historical signal reports from reports/prediction_market_*.json
    and reports/social_sentiment_*.json
  - Creates a time-indexed signal stream keyed by (timestamp, symbol)
  - AlphaStrategy subclasses match the most recent signal to each bar
  - Uses existing BacktestEngine for event-driven simulation
  - Compares PredScope, Adanos, ML (LightGBM), and Buy & Hold
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
from runtime_paths import data_root, signal_output_dir

from backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    BaselineStrategy,
    DataHandler,
    MLStrategy,
    Portfolio,
    Broker,
    Signal,
    Bar,
    Strategy,
    Position,
    compute_aggregate_metrics,
    _annual_factor,
    MODELS_DIR,
)
from predscope_signals import (
    _find_latest_report,
    _build_keyword_patterns,
    _match_symbol,
    _best_probability,
    REPORTS_DIR as PREDSCOPE_REPORTS_DIR,
)
from adanos_signals import (
    _find_latest_report as _find_latest_social_report,
    _compute_buzz_change,
    REPORTS_DIR as ADANOS_REPORTS_DIR,
)

log = logging.getLogger("alpha_backtest")

PROJECT_ROOT = Path(__file__).parent
BACKTEST_DIR = data_root() / "memory" / "backtest"
SIGNALS_DIR = signal_output_dir()

# ── Signal Loading ──────────────────────────────────────────────────────────────

def _base(symbol: str) -> str:
    """Extract base symbol from pair notation (e.g. 'BTC/USDT' → 'BTC')."""
    return symbol.split("/")[0].upper()


def _parse_isotime(ts: str) -> Optional[pd.Timestamp]:
    """Parse ISO timestamp to pandas Timestamp (UTC). Returns None if invalid/NaT."""
    try:
        t = pd.Timestamp(ts).tz_convert("UTC")
        if pd.isna(t):
            return None
        return t
    except Exception:
        return None


def load_predscope_signal_history(symbol: str) -> list[tuple[pd.Timestamp, str, float]]:
    """Load all historical PredScope signals for a given symbol.

    Scans all prediction_market_*.json files, extracts signals for the symbol,
    and returns a time-sorted list of (timestamp, direction, confidence).

    Signal logic (as specified for alpha backtest):
      - prob > 0.55 → BUY
      - prob < 0.45 → SELL
      - else skip (no signal)
    """
    base = _base(symbol)
    files = sorted(PREDSCOPE_REPORTS_DIR.glob("prediction_market_*.json"))
    if not files:
        log.warning("No prediction_market_*.json files found in %s", PREDSCOPE_REPORTS_DIR)
        return []

    patterns = _build_keyword_patterns()
    signals = []

    for fpath in files:
        try:
            report = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        ts_str = report.get("collected_at", "")
        ts = _parse_isotime(ts_str)
        if ts is None:
            continue

        markets = report.get("markets", [])
        for m in markets:
            title = m.get("title", "")
            matched = _match_symbol(title, patterns)
            if matched != base:
                continue

            prob = _best_probability(m.get("outcomes", []))
            if prob is None:
                continue

            # Alpha backtest thresholds: 0.55/0.45 (per task spec)
            if prob > 0.55:
                direction = "BUY"
                confidence = min(prob, 1.0)
            elif prob < 0.45:
                direction = "SELL"
                confidence = min(1.0 - prob, 1.0)
            else:
                continue  # neutral zone

            signals.append((ts, direction, confidence))
            break  # one signal per symbol per report

    signals.sort(key=lambda x: x[0])
    log.info("Loaded %d PredScope signals for %s (%s → %s)",
             len(signals), symbol,
             signals[0][0] if signals else "N/A",
             signals[-1][0] if signals else "N/A")
    return signals


def load_adanos_signal_history(symbol: str) -> list[tuple[pd.Timestamp, str, float]]:
    """Load all historical Adanos social sentiment signals for a given symbol.

    Scans all social_sentiment_*.json files and returns time-sorted
    (timestamp, direction, confidence) tuples.

    Signal logic (as specified for alpha backtest):
      - buzz_score > 70 AND buzz_change_24h > 0.30 (momentum) → BUY
      - buzz_score > 80 AND sentiment_score < -0.30 (contrarian) → SELL
    """
    base = _base(symbol)
    files = sorted(ADANOS_REPORTS_DIR.glob("social_sentiment_*.json"))
    if not files:
        log.warning("No social_sentiment_*.json files found in %s", ADANOS_REPORTS_DIR)
        return []

    signals = []

    for fpath in files:
        try:
            report = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        ts_str = report.get("collected_at", "")
        ts = _parse_isotime(ts_str)
        if ts is None:
            continue

        reddit_crypto = report.get("data", {}).get("reddit_crypto", [])
        for entry in reddit_crypto:
            sym = entry.get("symbol", "").upper()
            if sym != base:
                continue

            buzz_score = entry.get("buzz_score", 0.0)
            buzz_change = _compute_buzz_change(entry.get("trend_history", []))
            sentiment = entry.get("sentiment_score", 0.0)

            direction = None
            confidence = 0.0

            # Momentum BUY: buzz > 70 AND buzz_change > 0.30
            if buzz_score > 70 and buzz_change > 0.30:
                direction = "BUY"
                confidence = min(buzz_score / 100.0, 1.0)

            # Contrarian SELL: buzz > 80 AND negative sentiment
            if direction is None and buzz_score > 80 and sentiment < -0.30:
                direction = "SELL"
                confidence = min(abs(sentiment), 1.0)

            # Fallthrough contrarian: extreme sentiment
            if direction is None:
                if sentiment > 0.80:
                    direction = "SELL"
                    confidence = min(sentiment, 1.0)
                elif sentiment < -0.60:
                    direction = "BUY"
                    confidence = min(abs(sentiment), 1.0)

            if direction is None:
                continue

            signals.append((ts, direction, confidence))
            break  # one signal per symbol per report

    signals.sort(key=lambda x: x[0])
    log.info("Loaded %d Adanos signals for %s (%s → %s)",
             len(signals), symbol,
             signals[0][0] if signals else "N/A",
             signals[-1][0] if signals else "N/A")
    return signals


# ── Alpha Strategies ────────────────────────────────────────────────────────────

class AlphaSignalStrategy(Strategy):
    """Base strategy that maps pre-loaded signal stream to bar timestamps.

    For each bar, finds the most recent signal whose timestamp is <= bar's timestamp.
    If the signal is BUY and no position is held, enters long.
    If the signal is SELL and a position is held, exits.
    """

    def __init__(self, config: BacktestConfig, signal_stream: list[tuple[pd.Timestamp, str, float]],
                 name: str = "alpha"):
        super().__init__(config)
        self.signal_stream = signal_stream  # sorted (ts, direction, confidence)
        self.name = name
        self._signal_idx = 0
        self._last_signal: Optional[tuple] = None

    def _lookup_signal(self, bar_ts: pd.Timestamp) -> Optional[tuple]:
        """Return the most recent signal at or before bar_ts."""
        # Advance pointer to catch up to bar_ts
        while (self._signal_idx < len(self.signal_stream)
               and self.signal_stream[self._signal_idx][0] <= bar_ts):
            self._last_signal = self.signal_stream[self._signal_idx]
            self._signal_idx += 1
        # If we haven't consumed our first signal, return None
        if self._last_signal is None or self._last_signal[0] > bar_ts:
            return None
        return self._last_signal

    def next(self, i: int, bar: Bar, positions: list[Position]) -> Signal:
        ts = pd.Timestamp(bar.timestamp).tz_convert("UTC")
        sig = self._lookup_signal(ts)
        if sig is None:
            return Signal(action="HOLD", confidence=0.0,
                          stop_loss_pct=0.0, take_profit_pct=0.0)

        _, direction, confidence = sig

        # Already in position → hold (exits handled via stop-loss/take-profit)
        if positions:
            return Signal(action="HOLD", confidence=confidence,
                          stop_loss_pct=0.0, take_profit_pct=0.0)

        if direction == "BUY":
            return Signal(
                action="BUY",
                confidence=round(confidence, 4),
                stop_loss_pct=self.config.stop_loss_pct,
                take_profit_pct=self.config.take_profit_pct,
            )

        # SELL signal with no position → HOLD (no shorting in this test)
        return Signal(action="HOLD", confidence=round(confidence, 4),
                      stop_loss_pct=0.0, take_profit_pct=0.0)


class PredScopeStrategy(AlphaSignalStrategy):
    """Prediction market (Polymarket) based strategy."""

    def __init__(self, config: BacktestConfig, symbol: str):
        stream = load_predscope_signal_history(symbol)
        super().__init__(config, stream, name="predscope")


class AdanosStrategy(AlphaSignalStrategy):
    """Social sentiment (Reddit crypto buzz) based strategy."""

    def __init__(self, config: BacktestConfig, symbol: str):
        stream = load_adanos_signal_history(symbol)
        super().__init__(config, stream, name="adanos")


# ── Backtest Helpers ────────────────────────────────────────────────────────────

def _to_bars_static(df: pd.DataFrame) -> list:
    """Convert DataFrame rows to Bar objects (static helper)."""
    bars = []
    for idx, row in df.iterrows():
        try:
            o, h, l, c, v = (float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"]),
                              float(row["volume"]))
            if not all(np.isfinite(x) for x in (o, h, l, c, v)):
                continue
            bars.append(Bar(timestamp=idx, open=o, high=h, low=l, close=c, volume=v))
        except (ValueError, TypeError):
            continue
    return bars


def _run_alpha_backtest(symbol: str, strategy: AlphaSignalStrategy,
                        start: str, end: str, timeframe: str = "1h",
                        config: BacktestConfig = None) -> dict:
    """Run a single backtest for an alpha strategy over the full date range."""
    config = config or BacktestConfig()
    dh = DataHandler(symbol, start, end, timeframe)
    df = dh.load()
    if df is None or df.empty:
        log.error("No data for %s (%s → %s)", symbol, start, end)
        return _empty_alpha_result(symbol, strategy.name)

    if len(df) < 50:
        log.warning("Only %d bars for %s — insufficient", len(df), symbol)
        return _empty_alpha_result(symbol, strategy.name)

    strategy.set_data(df)
    portfolio = Portfolio(config)
    broker = Broker(config)

    bars = _to_bars_static(df)
    if not bars:
        return _empty_alpha_result(symbol, strategy.name)

    for i, bar in enumerate(bars):
        exit_fills = portfolio.check_exits(bar, i)
        entry_fills = broker.process_orders(bar, i, portfolio, symbol)
        signal = strategy.next(i, bar, portfolio.positions)
        if signal.action in ("BUY", "SELL"):
            broker.place_order(signal, bar, i, portfolio, symbol)
        portfolio.record_snapshot(bar.timestamp, bar)

    if portfolio.positions and bars:
        portfolio.close_all(bars[-1], len(bars) - 1)

    metrics = compute_aggregate_metrics(
        portfolio.equity_curve, portfolio.trade_log, config,
        portfolio.total_funding_paid, timeframe,
    )
    return {
        "symbol": symbol,
        "strategy": strategy.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config.to_dict(),
        "aggregate": metrics,
        "equity_curve": [(ts.isoformat(), round(eq, 4)) for ts, eq in portfolio.equity_curve],
        "trades": portfolio.trade_log,
    }


def _empty_alpha_result(symbol: str, strategy_name: str, config: BacktestConfig = None) -> dict:
    config = config or BacktestConfig()
    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config.to_dict(),
        "aggregate": {
            "sharpe": 0.0, "sortino": 0.0, "avg_win_rate": 0.0,
            "total_pnl": 0.0, "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0,
            "calmar": 0.0, "profit_factor": 0.0, "total_trades": 0,
            "avg_trade_duration_hours": 0.0,
            "final_equity": config.initial_capital, "total_funding_paid": 0.0,
        },
        "equity_curve": [],
        "trades": [],
    }


# ── Compare Mode ───────────────────────────────────────────────────────────────

def run_alpha_compare(symbol: str, start: str, end: str,
                      timeframe: str = "1h",
                      config: BacktestConfig = None) -> dict:
    """Run all 4 strategies (PredScope, Adanos, ML, B&H) and compare results."""
    config = config or BacktestConfig()

    results = {}

    # 1. PredScope
    log.info("── PredScope Strategy ──")
    ps_strategy = PredScopeStrategy(config, symbol)
    results["predscope"] = _run_alpha_backtest(symbol, ps_strategy, start, end, timeframe, config)

    # 2. Adanos
    log.info("── Adanos Strategy ──")
    ad_strategy = AdanosStrategy(config, symbol)
    results["adanos"] = _run_alpha_backtest(symbol, ad_strategy, start, end, timeframe, config)

    # 3. ML Strategy (LightGBM)
    log.info("── ML Strategy ──")
    try:
        dh_ml = DataHandler(symbol, start, end, timeframe)
        ml_strategy = MLStrategy(config, symbol=symbol)
        ml_strategy.init()
        ml_engine = BacktestEngine(symbol, ml_strategy, config, dh_ml, timeframe)
        results["ml"] = ml_engine.run()
    except Exception as e:
        log.warning("ML strategy failed: %s", e)
        results["ml"] = _empty_alpha_result(symbol, "ml", config)

    # 4. Buy & Hold
    log.info("── Buy & Hold Baseline ──")
    try:
        dh_bh = DataHandler(symbol, start, end, timeframe)
        bh_strategy = BaselineStrategy(config)
        bh_engine = BacktestEngine(symbol, bh_strategy, config, dh_bh, timeframe)
        results["baseline"] = bh_engine.run()
    except Exception as e:
        log.warning("Baseline strategy failed: %s", e)
        results["baseline"] = _empty_alpha_result(symbol, "baseline", config)

    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config.to_dict(),
        "date_range": {"start": start, "end": end},
        "results": results,
    }


# ── Single Mode ────────────────────────────────────────────────────────────────

def run_alpha_single(symbol: str, start: str, end: str,
                     strategy_name: str = "predscope",
                     timeframe: str = "1h",
                     config: BacktestConfig = None) -> dict:
    """Run a single alpha strategy backtest."""
    config = config or BacktestConfig()

    if strategy_name == "predscope":
        strategy = PredScopeStrategy(config, symbol)
    elif strategy_name == "adanos":
        strategy = AdanosStrategy(config, symbol)
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

    return _run_alpha_backtest(symbol, strategy, start, end, timeframe, config)


# ── Walk-Forward Mode ──────────────────────────────────────────────────────────

def run_alpha_walk_forward(symbol: str, start: str, end: str,
                           strategy_name: str = "predscope",
                           n_windows: int = 3, timeframe: str = "1h",
                           config: BacktestConfig = None) -> dict:
    """Run walk-forward backtest for an alpha strategy.

    Since alpha strategies don't train, walk-forward just partitions the data
    and runs each window independently. This validates signal consistency.
    """
    config = config or BacktestConfig()
    dh = DataHandler(symbol, start, end, timeframe)
    df = dh.load()
    if df is None or df.empty:
        log.error("No data for %s (%s → %s)", symbol, start, end)
        return _empty_wf_result(symbol)

    if len(df) < 100:
        log.error("Insufficient data for %s: %d bars", symbol, len(df))
        return _empty_wf_result(symbol)

    total_bars = len(df)
    test_size = max(50, total_bars // (n_windows + 1))
    if test_size * n_windows > total_bars:
        test_size = max(24, total_bars // n_windows)

    log.info("Walk-forward: %s, %d bars, %d windows, ~%d bars/test",
             symbol, total_bars, n_windows, test_size)

    all_trades = []
    all_equity = []
    windows = []
    ann_factor = _annual_factor(timeframe)

    for w in range(n_windows):
        train_end_idx = min((w + 1) * test_size, total_bars)
        test_start_idx = w * test_size
        test_end_idx = min((w + 1) * test_size, total_bars)

        if test_end_idx - test_start_idx < 24:
            log.warning("Window %d: too few bars (%d), skipping",
                        w, test_end_idx - test_start_idx)
            continue

        test_df = df.iloc[test_start_idx:test_end_idx]
        test_start_str = str(df.index[test_start_idx])[:10]
        test_end_str = str(df.index[test_end_idx - 1])[:10]

        log.info("Window %d/%d: %s → %s", w + 1, n_windows, test_start_str, test_end_str)

        # Create a fresh strategy and backtest for each window
        if strategy_name == "predscope":
            strategy = PredScopeStrategy(config, symbol)
        else:
            strategy = AdanosStrategy(config, symbol)

        portfolio = Portfolio(config)
        broker = Broker(config)
        strategy.set_data(test_df)

        bars = _to_bars_static(test_df)
        if not bars:
            windows.append(_empty_window_dict(w, test_start_str, test_end_str, config))
            continue

        for i, bar in enumerate(bars):
            exit_fills = portfolio.check_exits(bar, i)
            entry_fills = broker.process_orders(bar, i, portfolio, symbol)
            signal = strategy.next(i, bar, portfolio.positions)
            if signal.action in ("BUY", "SELL"):
                broker.place_order(signal, bar, i, portfolio, symbol)
            portfolio.record_snapshot(bar.timestamp, bar)

        if portfolio.positions and bars:
            portfolio.close_all(bars[-1], len(bars) - 1)

        window_trades = portfolio.trade_log
        all_trades.extend(window_trades)
        all_equity.extend(portfolio.equity_curve)

        pnl = sum(t["pnl"] for t in window_trades)
        pnl_pct = (pnl / config.initial_capital) * 100
        win_count = sum(1 for t in window_trades if t["pnl"] > 0)
        win_rate = win_count / len(window_trades) if window_trades else 0.0

        eq_values = [e[1] for e in portfolio.equity_curve]
        final_eq = eq_values[-1] if eq_values else config.initial_capital

        windows.append({
            "window": w,
            "window_start": test_start_str,
            "window_end": test_end_str,
            "total_pnl": round(pnl, 4),
            "total_pnl_pct": round(pnl_pct, 4),
            "win_rate": round(win_rate, 4),
            "max_drawdown_pct": round(portfolio.max_drawdown_pct * 100, 4),
            "trade_count": len(window_trades),
            "final_equity": round(final_eq, 4),
        })

    if not windows:
        return _empty_wf_result(symbol)

    # Aggregate
    total_pnl = sum(t["pnl"] for t in all_trades)
    total_pnl_pct = (total_pnl / config.initial_capital) * 100
    win_count = sum(1 for t in all_trades if t["pnl"] > 0)
    avg_win_rate = win_count / len(all_trades) if all_trades else 0.0

    eq_values = [e[1] for e in all_equity]
    from backtest_engine import compute_max_drawdown_pct, compute_sharpe, compute_sortino
    eq_rets = [(all_equity[i][1] - all_equity[i-1][1]) / all_equity[i-1][1]
               for i in range(1, len(all_equity)) if all_equity[i-1][1] > 0]

    max_dd = compute_max_drawdown_pct(eq_values) if eq_values else 0.0
    sharpe = compute_sharpe(eq_rets, ann_factor) if eq_rets else 0.0
    sortino = compute_sortino(eq_rets, ann_factor) if eq_rets else 0.0

    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"n_windows": n_windows, "total_bars": len(df)},
        "aggregate": {
            "symbol": symbol,
            "total_windows": len(windows),
            "total_trades": len(all_trades),
            "total_pnl": round(total_pnl, 4),
            "total_pnl_pct": round(total_pnl_pct, 4),
            "avg_win_rate": round(avg_win_rate, 4),
            "sharpe": round(sharpe, 4),
            "sortino": round(sortino, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
        },
        "windows": windows,
        "equity_curve": [(ts.isoformat(), round(eq, 4)) for ts, eq in all_equity],
        "trades": all_trades,
    }


def _empty_window_dict(w: int, start: str, end: str, config: BacktestConfig) -> dict:
    return {
        "window": w,
        "window_start": start,
        "window_end": end,
        "total_pnl": 0.0, "total_pnl_pct": 0.0, "win_rate": 0.0,
        "max_drawdown_pct": 0.0, "trade_count": 0,
        "final_equity": config.initial_capital,
    }


def _empty_wf_result(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {},
        "aggregate": {
            "symbol": symbol, "total_windows": 0, "total_trades": 0,
            "total_pnl": 0.0, "total_pnl_pct": 0.0, "avg_win_rate": 0.0,
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0,
        },
        "windows": [],
        "equity_curve": [],
        "trades": [],
    }


# ── Output ──────────────────────────────────────────────────────────────────────

def _print_metrics(name: str, agg: dict):
    """Print a single strategy's metrics nicely."""
    print(f"\n  [{name}]")
    print(f"    Sharpe:        {agg.get('sharpe', 0):.4f}")
    print(f"    Sortino:       {agg.get('sortino', 0):.4f}")
    print(f"    Total PnL:     ${agg.get('total_pnl', 0):,.2f} ({agg.get('total_pnl_pct', 0):.2f}%)")
    print(f"    Win Rate:      {agg.get('avg_win_rate', 0):.1%}")
    print(f"    Max Drawdown:  {agg.get('max_drawdown_pct', 0):.2f}%")
    print(f"    Total Trades:  {agg.get('total_trades', 0)}")
    if "calmar" in agg:
        print(f"    Calmar:        {agg.get('calmar', 0):.4f}")
    if "profit_factor" in agg:
        print(f"    Profit Factor: {agg.get('profit_factor', 0):.2f}")


def save_result(result: dict, prefix: str = "alpha"):
    """Save result to memory/backtest/ directory."""
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
        description="Alpha backtest — PredScope + Adanos signals vs ML and B&H",
    )
    parser.add_argument("--symbol", type=str, default="BTC/USDT",
                        help="Symbol to backtest (default: BTC/USDT)")
    parser.add_argument("--start", type=str, default="2026-05-16",
                        help="Start date YYYY-MM-DD (default: 2026-05-16)")
    parser.add_argument("--end", type=str, default="2026-05-20",
                        help="End date YYYY-MM-DD (default: 2026-05-20)")
    parser.add_argument("--mode", type=str, default="compare",
                        choices=["single", "compare", "walk-forward"],
                        help="Backtest mode (default: compare)")
    parser.add_argument("--strategy", type=str, default="predscope",
                        choices=["predscope", "adanos"],
                        help="Alpha strategy for single/walk-forward mode")
    parser.add_argument("--windows", type=int, default=3,
                        help="Walk-forward windows (default: 3)")
    parser.add_argument("--timeframe", type=str, default="1h",
                        choices=["1h", "4h", "1d"],
                        help="Timeframe (default: 1h)")
    parser.add_argument("--capital", type=float, default=10000.0,
                        help="Initial capital (default: 10000)")
    parser.add_argument("--position-size", type=float, default=0.5,
                        help="Position size as fraction of equity (default: 0.5)")
    parser.add_argument("--max-positions", type=int, default=1,
                        help="Max concurrent positions (default: 1)")
    parser.add_argument("--commission", type=float, default=0.001)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--stop-loss", type=float, default=0.05)
    parser.add_argument("--take-profit", type=float, default=0.10)

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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print(f"\n{'=' * 70}")
    print(f"  ALPHA BACKTEST | {args.mode.upper()} | {args.symbol}")
    print(f"  {args.start} → {args.end} | timeframe={args.timeframe}")
    print(f"{'=' * 70}")

    try:
        if args.mode == "compare":
            result = run_alpha_compare(
                args.symbol, args.start, args.end,
                timeframe=args.timeframe, config=config,
            )
            path = save_result(result, prefix="alpha_compare")

            print(f"\n── Results: {args.symbol} ──")
            for key in ["predscope", "adanos", "ml", "baseline"]:
                if key in result.get("results", {}):
                    r = result["results"][key]
                    _print_metrics(key.upper(), r.get("aggregate", {}))

        elif args.mode == "single":
            result = run_alpha_single(
                args.symbol, args.start, args.end,
                strategy_name=args.strategy,
                timeframe=args.timeframe, config=config,
            )
            path = save_result(result, prefix=f"alpha_{args.strategy}")
            _print_metrics(args.strategy.upper(), result.get("aggregate", {}))

        elif args.mode == "walk-forward":
            result = run_alpha_walk_forward(
                args.symbol, args.start, args.end,
                strategy_name=args.strategy,
                n_windows=args.windows, timeframe=args.timeframe,
                config=config,
            )
            path = save_result(result, prefix=f"alpha_wf_{args.strategy}")

            agg = result.get("aggregate", {})
            print(f"\n── Results: {args.symbol} ({args.strategy}) ──")
            print(f"  Sharpe:        {agg.get('sharpe', 0):.4f}")
            print(f"  Sortino:       {agg.get('sortino', 0):.4f}")
            print(f"  Total PnL:     ${agg.get('total_pnl', 0):,.2f} ({agg.get('total_pnl_pct', 0):.2f}%)")
            print(f"  Win Rate:      {agg.get('avg_win_rate', 0):.1%}")
            print(f"  Max Drawdown:  {agg.get('max_drawdown_pct', 0):.2f}%")
            print(f"  Total Trades:  {agg.get('total_trades', 0)}")
            for w in result.get("windows", []):
                print(f"    Window {w['window']}: PnL=${w['total_pnl']:,.2f}  "
                      f"WR={w['win_rate']:.1%}  DD={w['max_drawdown_pct']:.2f}%")

        print(f"\n  Output: {path}")

    except Exception as e:
        log.error("Backtest failed: %s", e, exc_info=True)
        sys.exit(1)

    print("\nDone.")


if __name__ == "__main__":
    main()
