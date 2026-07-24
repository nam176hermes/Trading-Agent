#!/usr/bin/env python3
"""
CRA-Compliant Crypto Cost-Basis Tracker (CAD)

Canadian tax law:
- Every crypto-to-crypto swap is a taxable disposition at CAD FMV.
- No like-kind exchange.
- Active trading (>50 trades/year) = 100% business income inclusion.
- Otherwise: capital gains with 50% inclusion rate.

Trades are stored in memory/cra/trades.jsonl (one JSON object per line).
"""

import json
import os
from datetime import datetime, timezone
from runtime_paths import data_root
from typing import Optional

# --- Configuration ---
TRADES_FILE = data_root() / "memory" / "cra" / "trades.jsonl"
DEFAULT_USD_CAD_RATE = 1.35
DEFAULT_MARGINAL_RATE = 0.33
BUSINESS_INCOME_THRESHOLD = 50  # >50 trades flagged as business income


def _ensure_trades_file() -> None:
    """Create trades.jsonl if it doesn't exist."""
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not TRADES_FILE.exists():
        TRADES_FILE.touch()


def _fetch_usd_cad_rate() -> float:
    """
    Attempt to fetch live USD/CAD rate via ccxt (Binance or Kraken).
    Falls back to the hardcoded default on any failure.
    """
    try:
        import ccxt

        for exchange_id in ("kraken", "binance"):
            try:
                exchange = getattr(ccxt, exchange_id)()
                ticker = exchange.fetch_ticker("USD/CAD")
                rate = ticker.get("last") or ticker.get("close")
                if rate and rate > 0:
                    return float(rate)
            except Exception:
                continue
    except ImportError:
        pass
    except Exception:
        pass

    return DEFAULT_USD_CAD_RATE


def _read_trades() -> list[dict]:
    """Read all trades from the JSONL file."""
    _ensure_trades_file()
    trades = []
    with open(TRADES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return trades


def log_trade(
    symbol: str,
    side: str,
    quantity: float,
    price_usd: float,
    usd_cad_rate: Optional[float] = None,
) -> dict:
    """
    Log a crypto trade with CAD-equivalent FMV.

    Args:
        symbol: Ticker symbol (e.g., 'BTC', 'ETH', 'SOL')
        side: 'buy' or 'sell'
        quantity: Number of units traded
        price_usd: Price per unit in USD
        usd_cad_rate: USD/CAD exchange rate. If None, fetched from ccxt or uses 1.35.

    Returns:
        dict with keys: symbol, side, quantity, price_usd, cad_rate, value_cad, timestamp
    """
    side = side.lower().strip()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")

    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")

    if price_usd <= 0:
        raise ValueError(f"price_usd must be positive, got {price_usd}")

    # Resolve USD/CAD rate
    if usd_cad_rate is None:
        usd_cad_rate = _fetch_usd_cad_rate()
    elif usd_cad_rate <= 0:
        raise ValueError(f"usd_cad_rate must be positive, got {usd_cad_rate}")

    value_cad = round(quantity * price_usd * usd_cad_rate, 2)
    timestamp = datetime.now(timezone.utc).isoformat()

    trade = {
        "symbol": symbol.upper(),
        "side": side,
        "quantity": quantity,
        "price_usd": price_usd,
        "cad_rate": round(usd_cad_rate, 6),
        "value_cad": value_cad,
        "timestamp": timestamp,
    }

    # Append to JSONL
    _ensure_trades_file()
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(trade) + "\n")

    return trade


def _compute_acb_gains(trades: list[dict]) -> float:
    """
    Walk trades chronologically and compute realized gains using ACB
    (Adjusted Cost Basis) per CRA rules.

    Returns total realized gains in CAD.
    """
    # Per-symbol state: { symbol: {"units": float, "total_cost_cad": float} }
    positions: dict[str, dict[str, float]] = {}
    total_realized_gains = 0.0

    for trade in trades:
        symbol = trade["symbol"]
        side = trade["side"]
        quantity = trade["quantity"]
        value_cad = trade["value_cad"]

        if symbol not in positions:
            positions[symbol] = {"units": 0.0, "total_cost_cad": 0.0}

        pos = positions[symbol]

        if side == "buy":
            pos["units"] += quantity
            pos["total_cost_cad"] += value_cad
        else:
            # sell
            if pos["units"] <= 0:
                # No position — treat as pure gain (or could be a short)
                total_realized_gains += value_cad
                continue

            sell_quantity = min(quantity, pos["units"])
            acb_per_unit = pos["total_cost_cad"] / pos["units"] if pos["units"] > 0 else 0.0
            cost_of_sold = sell_quantity * acb_per_unit
            proceeds = value_cad * (sell_quantity / quantity)  # proportional if oversold

            realized_gain = proceeds - cost_of_sold
            total_realized_gains += realized_gain

            # Reduce position proportionally
            pos["units"] -= sell_quantity
            pos["total_cost_cad"] -= cost_of_sold

            if pos["units"] < 1e-10:
                pos["units"] = 0.0
                pos["total_cost_cad"] = 0.0

    return round(total_realized_gains, 2)


def get_tax_summary(year: Optional[int] = None) -> dict:
    """
    Read all trade logs and produce a tax summary.

    Args:
        year: Filter trades to this tax year (UTC). If None, all trades.

    Returns:
        dict with keys:
            total_trades, total_volume_cad, realized_gains_cad, tax_year,
            income_type_hint, business_income_flag
    """
    all_trades = _read_trades()

    # Filter by year if specified
    if year is not None:
        trades = [
            t for t in all_trades
            if datetime.fromisoformat(t["timestamp"]).year == year
        ]
    else:
        trades = all_trades

    total_trades = len(trades)
    total_volume_cad = round(sum(t["value_cad"] for t in trades), 2)
    realized_gains_cad = _compute_acb_gains(trades)
    business_income_flag = total_trades > BUSINESS_INCOME_THRESHOLD
    income_type_hint = "business" if business_income_flag else "capital_gains"

    return {
        "total_trades": total_trades,
        "total_volume_cad": total_volume_cad,
        "realized_gains_cad": realized_gains_cad,
        "tax_year": year,
        "business_income_flag": business_income_flag,
        "income_type_hint": income_type_hint,
    }


def estimate_tax_liability(total_gains_cad: float, trade_count: int) -> dict:
    """
    Estimate tax liability based on CRA rules.

    - >50 trades/year: treated as business income (100% inclusion).
    - <=50 trades/year: treated as capital gains (50% inclusion).

    Args:
        total_gains_cad: Total realized gains in CAD.
        trade_count: Number of trades in the tax year.

    Returns:
        dict with keys:
            income_type, taxable_amount, estimated_tax, assumed_rate,
            inclusion_rate, trade_count
    """
    if trade_count > BUSINESS_INCOME_THRESHOLD:
        income_type = "business"
        inclusion_rate = 1.0
    else:
        income_type = "capital_gains"
        inclusion_rate = 0.5

    taxable_amount = round(total_gains_cad * inclusion_rate, 2)
    estimated_tax = round(taxable_amount * DEFAULT_MARGINAL_RATE, 2)

    return {
        "income_type": income_type,
        "taxable_amount": taxable_amount,
        "estimated_tax": estimated_tax,
        "assumed_rate": DEFAULT_MARGINAL_RATE,
        "inclusion_rate": inclusion_rate,
        "trade_count": trade_count,
    }


# ---------------------------------------------------------------------------
# Quick self-test / demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("CRA Crypto Tracker — Quick Demo")
    print("=" * 60)

    # Log some sample trades
    trades = [
        log_trade("BTC", "buy", 0.1, 67000.0),
        log_trade("ETH", "buy", 2.0, 3500.0),
        log_trade("BTC", "sell", 0.05, 72000.0),
        log_trade("SOL", "buy", 10.0, 145.0),
        log_trade("ETH", "sell", 1.0, 3800.0),
    ]

    for t in trades:
        print(f"  {t['side']:>4} {t['quantity']:>8} {t['symbol']:<5} "
              f"@ ${t['price_usd']:>10,.2f} USD  "
              f"({t['cad_rate']:.4f} CAD/USD)  "
              f"= ${t['value_cad']:>12,.2f} CAD")

    print()

    # Tax summary
    summary = get_tax_summary()
    print("Tax Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print()

    # Tax liability estimate
    liability = estimate_tax_liability(
        total_gains_cad=summary["realized_gains_cad"],
        trade_count=summary["total_trades"],
    )
    print("Estimated Tax Liability:")
    for k, v in liability.items():
        print(f"  {k}: {v}")

    print()
    print(f"Trades stored in: {TRADES_FILE}")
