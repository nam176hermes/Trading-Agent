"""
allocation_engine.py — Portfolio Allocation Engine

Bridges portfolio_optimizer into live allocation decisions.
Compares mean-variance optimal weights against current portfolio weights
and generates rebalance suggestions.

Usage:
    python allocation_engine.py              # Full run: optimise + report
    python allocation_engine.py --current    # Show current vs optimal only
    python allocation_engine.py --json       # Output as JSON to stdout

Output: reports/allocation_<timestamp>.json
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from portfolio_optimizer import optimize_portfolio
from runtime_paths import data_root, reports_dir

log = logging.getLogger("allocation_engine")

# ── Paths ─────────────────────────────────────────────────────────────────
REPORTS_DIR = reports_dir()
PORTFOLIO_PATH = data_root() / "memory" / "paper" / "portfolio.json"
LIVE_PRICES_PATH = data_root() / "live_prices.json"

# ── Constraints ───────────────────────────────────────────────────────────
DEFAULT_MAX_WEIGHT = 0.40      # Max 40% in any single asset
DEFAULT_MIN_WEIGHT = 0.0       # Min 0%
RISK_FREE_RATE = 0.02          # 2% annual risk-free rate
LOOKBACK_DAYS = 90             # Days of returns to use for optimisation


# ── Portfolio loading ─────────────────────────────────────────────────────

def load_current_positions() -> dict:
    """Load current portfolio positions from paper portfolio file.

    Returns {symbol: {shares, avg_cost, notional_value}, ...} or empty dict.
    """
    if not PORTFOLIO_PATH.exists():
        log.warning("No portfolio file at %s — assuming empty", PORTFOLIO_PATH)
        return {}

    try:
        pf = json.loads(PORTFOLIO_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read portfolio: %s", e)
        return {}

    positions = pf.get("positions", {})
    if not positions:
        return {}

    prices = _load_live_prices()

    result = {}
    for sym, pos in positions.items():
        sym_up = sym.upper()
        shares = float(pos.get("shares", 0))
        avg_cost = float(pos.get("avg_cost", 0))
        current_price = prices.get(sym_up, avg_cost)
        notional = shares * current_price
        result[sym_up] = {
            "shares": shares,
            "avg_cost": avg_cost,
            "current_price": current_price,
            "notional": notional,
        }
    return result


def _load_live_prices() -> dict[str, float]:
    """Load latest prices from live_prices.json."""
    if not LIVE_PRICES_PATH.exists():
        return {}
    try:
        data = json.loads(LIVE_PRICES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    prices = {}
    for sym, info in data.items():
        if sym.startswith("_"):
            continue
        if isinstance(info, dict):
            p = info.get("price")
            if p is not None:
                prices[sym.upper()] = float(p)
    return prices


# ── Returns retrieval ─────────────────────────────────────────────────────

def fetch_returns(symbols: list[str], lookback_days: int = LOOKBACK_DAYS) -> dict[str, np.ndarray]:
    """Fetch daily returns for a list of symbols.

    Tries backtest cache CSVs first, then falls back to yfinance.
    Returns {SYMBOL: np.array of daily returns}.
    """
    returns_dict = {}

    for sym in symbols:
        rets = _from_backtest_cache(sym, lookback_days)
        if rets is not None and len(rets) >= 10:
            returns_dict[sym] = rets
        else:
            rets = _from_yfinance(sym, lookback_days)
            if rets is not None and len(rets) >= 10:
                returns_dict[sym] = rets
            else:
                log.warning("No returns data for %s — skipping in optimisation", sym)

    return returns_dict


def _from_backtest_cache(symbol: str, lookback_days: int) -> Optional[np.ndarray]:
    """Try loading returns from backtest cache CSV files."""
    cache_dir = data_root() / "memory" / "backtest" / "cache"
    if not cache_dir.exists():
        return None

    # Find most recent CSV for this symbol
    candidates = sorted(
        cache_dir.glob(f"{symbol.upper()}_USDT_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        candidates = sorted(
            cache_dir.glob(f"{symbol.upper()}_USD_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    for csv_path in candidates:
        try:
            import pandas as pd
            df = pd.read_csv(csv_path, parse_dates=["timestamp"], index_col="timestamp")
            if df.empty or "close" not in df.columns:
                continue
            closes = df["close"]
            # Filter to last N days
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=lookback_days)
            closes = closes[closes.index >= cutoff]
            if len(closes) < 10:
                continue
            daily_closes = closes.resample("1D").last().dropna()
            rets = daily_closes.pct_change().dropna().values
            if len(rets) >= 10:
                return rets.astype(float)
        except Exception as e:
            log.debug("Cache read failed for %s from %s: %s", symbol, csv_path.name, e)
            continue

    return None


def _from_yfinance(symbol: str, lookback_days: int) -> Optional[np.ndarray]:
    """Fetch daily returns from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return None

    # Map to yfinance ticker
    CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC"}
    if symbol in CRYPTO_SYMBOLS:
        ticker = f"{symbol}-USD"
    else:
        ticker = symbol

    try:
        data = yf.download(
            ticker, period=f"{lookback_days}d", interval="1d",
            progress=False, auto_adjust=True,
        )
    except Exception:
        return None

    if data is None or data.empty:
        return None

    # Extract close prices
    if isinstance(data.columns, pd.MultiIndex):
        closes = data["Close"] if "Close" in data.columns else None
    else:
        closes = data["Close"] if "Close" in data.columns else data

    if closes is None or closes.empty:
        return None

    rets = closes.pct_change().dropna().values
    if len(rets) < 10:
        return None

    return rets.astype(float).ravel()


# ── Allocation computation ────────────────────────────────────────────────

def compute_current_weights(positions: dict) -> dict[str, float]:
    """Compute current portfolio weights from positions dict.

    Returns {SYMBOL: weight (0-1)}.
    """
    total_value = sum(p["notional"] for p in positions.values())
    if total_value <= 0:
        return {}
    return {sym: p["notional"] / total_value for sym, p in positions.items()}


def compute_target_allocation(
    returns_dict: dict[str, np.ndarray],
    constraints: Optional[dict] = None,
) -> dict:
    """Run portfolio optimisation and return target weights.

    Args:
        returns_dict: {SYMBOL: np.array of daily returns}.
        constraints: Optional dict with max_weight, min_weight, per_asset.

    Returns:
        {
            'weights': {SYMBOL: target_weight},
            'ret': float,
            'vol': float,
            'sharpe': float,
            'symbols': list of symbols used,
            'timestamp': ISO 8601 string,
        }
    """
    if constraints is None:
        constraints = {
            "max_weight": DEFAULT_MAX_WEIGHT,
            "min_weight": DEFAULT_MIN_WEIGHT,
        }

    result = optimize_portfolio(
        returns_dict,
        method="max_sharpe",
        constraints=constraints,
        risk_free_rate=RISK_FREE_RATE,
    )

    result["symbols"] = list(returns_dict.keys())
    result["timestamp"] = datetime.now(timezone.utc).isoformat()

    return result


def compute_rebalance_suggestions(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    drift_threshold: float = 0.02,
) -> list[dict]:
    """Compare current vs target weights and generate rebalance suggestions.

    Args:
        current_weights: {SYMBOL: current_weight}.
        target_weights: {SYMBOL: target_weight}.
        drift_threshold: Minimum absolute delta (%) to suggest a trade.

    Returns:
        List of {symbol, current_pct, target_pct, delta_pct, action}.
        action is 'BUY', 'SELL', or 'HOLD'.
    """
    suggestions = []

    all_symbols = set(current_weights.keys()) | set(target_weights.keys())

    for sym in sorted(all_symbols):
        current = current_weights.get(sym, 0.0)
        target = target_weights.get(sym, 0.0)
        delta = target - current

        if abs(delta) < drift_threshold:
            action = "HOLD"
        elif delta > 0:
            action = "BUY"
        else:
            action = "SELL"

        suggestions.append({
            "symbol": sym,
            "current_pct": round(current * 100, 1),
            "target_pct": round(target * 100, 1),
            "delta_pct": round(delta * 100, 1),
            "action": action,
        })

    return suggestions


# ── Output ────────────────────────────────────────────────────────────────

def save_allocation_report(report: dict) -> Path:
    """Save allocation report to reports/allocation_<timestamp>.json."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"allocation_{ts}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    log.info("Allocation report saved to %s", path)
    return path


# ── Main entry point ──────────────────────────────────────────────────────

def run(show_only: bool = False, output_json: bool = False) -> dict:
    """Main allocation engine run.

    Args:
        show_only: If True, print current vs optimal and return.
        output_json: If True, print JSON to stdout instead of saving file.

    Returns the full report dict.
    """
    # 1. Load current positions
    positions = load_current_positions()
    current_weights = compute_current_weights(positions)
    symbols_in_portfolio = list(positions.keys())

    log.info("Current portfolio: %d positions — %s",
             len(positions), ", ".join(symbols_in_portfolio) if symbols_in_portfolio else "empty")

    # 2. Fetch returns for all portfolio symbols
    if not symbols_in_portfolio:
        log.warning("No positions — cannot optimise")
        empty_report = {
            "status": "empty_portfolio",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_weights": {},
            "target_weights": {},
            "suggestions": [],
        }
        if output_json:
            print(json.dumps(empty_report, indent=2))
        elif not show_only:
            save_allocation_report(empty_report)
        return empty_report

    returns_dict = fetch_returns(symbols_in_portfolio)

    if len(returns_dict) < 2:
        log.warning("Insufficient returns data (%d symbols) — need >= 2 for optimisation",
                    len(returns_dict))
        report = {
            "status": "insufficient_data",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_weights": current_weights,
            "target_weights": {},
            "suggestions": [],
            "symbols_with_data": list(returns_dict.keys()),
        }
        if output_json:
            print(json.dumps(report, indent=2))
        elif not show_only:
            save_allocation_report(report)
        return report

    # 3. Run optimisation
    opt_result = compute_target_allocation(returns_dict)
    target_weights = opt_result.get("weights", {})

    # 4. Generate rebalance suggestions
    suggestions = compute_rebalance_suggestions(current_weights, target_weights)

    # 5. Build report
    report = {
        "status": "ok",
        "timestamp": opt_result.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "current_weights": {sym: round(w * 100, 1) for sym, w in current_weights.items()},
        "target_weights": {sym: round(w * 100, 1) for sym, w in target_weights.items()},
        "portfolio_metrics": {
            "expected_return_annual": round(opt_result.get("ret", 0) * 100, 2),
            "expected_vol_annual": round(opt_result.get("vol", 0) * 100, 2),
            "sharpe_ratio": round(opt_result.get("sharpe", 0), 4),
        },
        "suggestions": suggestions,
        "symbols_used": opt_result.get("symbols", []),
        "cash_residual_pct": round(
            100 - sum(current_weights.values()) * 100, 1
        ),
    }

    # 6. Output
    if show_only:
        _print_comparison(report)
    elif output_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        save_allocation_report(report)
        _print_comparison(report)

    return report


def _print_comparison(report: dict) -> None:
    """Pretty-print current vs optimal allocation."""
    print("\n" + "=" * 65)
    print("  PORTFOLIO ALLOCATION — Current vs Optimal (Max Sharpe)")
    print("=" * 65)

    current = report.get("current_weights", {})
    target = report.get("target_weights", {})

    if not target:
        print(f"  Status: {report.get('status', 'unknown')}")
        if report.get("symbols_with_data"):
            print(f"  Symbols with data: {report['symbols_with_data']}")
        return

    fmt = "  {:<8} {:>10} {:>10} {:>10}   {:>6}"
    print(fmt.format("SYMBOL", "CURRENT %", "TARGET %", "DELTA %", "ACTION"))
    print("  " + "-" * 57)

    for s in report.get("suggestions", []):
        delta_str = f"{s['delta_pct']:+.1f}"
        print(fmt.format(
            s["symbol"],
            f"{s['current_pct']:.1f}%",
            f"{s['target_pct']:.1f}%",
            f"{delta_str}%",
            s["action"],
        ))

    # Cash residual
    print(fmt.format(
        "CASH",
        f"{report.get('cash_residual_pct', 0):.1f}%",
        "—",
        "—",
        "—",
    ))

    metrics = report.get("portfolio_metrics", {})
    print("\n  Portfolio Metrics (Max Sharpe):")
    print(f"    Expected Return (annual): {metrics.get('expected_return_annual', 0):.2f}%")
    print(f"    Expected Vol (annual):    {metrics.get('expected_vol_annual', 0):.2f}%")
    print(f"    Sharpe Ratio:            {metrics.get('sharpe_ratio', 0):.4f}")
    print("=" * 65 + "\n")


def get_target_weight_for_symbol(symbol: str) -> Optional[float]:
    """Fast query: get optimal target weight for a single symbol.

    Used by execute_live.py to check allocation alignment.
    Returns target weight (0-1) or None if unavailable.
    """
    positions = load_current_positions()
    symbols = list(positions.keys())
    if not symbols:
        # If no positions, there's no target allocation to compare against
        return None

    # Include the new symbol if not already tracked
    all_symbols = set(symbols) | {symbol.upper()}
    symbols_to_fetch = sorted(all_symbols)

    returns_dict = fetch_returns(symbols_to_fetch)
    if len(returns_dict) < 2:
        return None

    try:
        opt_result = compute_target_allocation(returns_dict)
    except Exception:
        return None

    weights = opt_result.get("weights", {})
    return weights.get(symbol.upper())


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    show_only = "--current" in sys.argv
    output_json = "--json" in sys.argv

    report = run(show_only=show_only, output_json=output_json)
