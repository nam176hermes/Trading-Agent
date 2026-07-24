"""
paper_trader.py — Simulated brokerage with $100k virtual balance.

State machine, not a full exchange. Persists portfolio to disk after every trade.
Position sizing: max 20% per asset, max 60% total allocation. 0.1% slippage.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from runtime_paths import data_root, reports_dir as runtime_reports_dir

log = logging.getLogger("paper_trader")

PAPER_DIR = data_root() / "memory" / "paper"
PORTFOLIO_FILE = PAPER_DIR / "portfolio.json"
ORDERS_FILE = PAPER_DIR / "orders.jsonl"
JOURNAL_FILE = data_root() / "memory" / "trade_journal.jsonl"

INITIAL_CASH = 100_000.0
MAX_POSITION_PCT = 0.05
MAX_TOTAL_ALLOCATION = 0.50
SLIPPAGE = 0.001

MAX_DRAWDOWN_PCT = 0.15  # 15% max drawdown from peak equity
DAILY_LOSS_LIMIT = 0.03  # 3% max daily loss

STOP_LOSS_DEFAULT_PCT = 0.05   # 5% default stop-loss if signal doesn't provide one
TRAIL_TRIGGER_PCT    = 0.10    # activate trailing stop at +10% unrealised gain
TRAIL_PCT            = 0.05    # trail 5% below highest price


def _init_portfolio() -> dict:
    return {
        "cash": INITIAL_CASH,
        "positions": {},
        "orders": [],
        "pnl": 0.0,
        "peak_equity": INITIAL_CASH,
        "daily_pnl": 0.0,
        "daily_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def load_portfolio() -> dict:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            log.warning("Corrupt portfolio.json, reinitializing")
    pf = _init_portfolio()
    save_portfolio(pf)
    return pf


def save_portfolio(pf: dict):
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_FILE.write_text(json.dumps(pf, indent=2, default=str))


def _log_order(order: dict):
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    with open(ORDERS_FILE, "a") as f:
        f.write(json.dumps(order, default=str) + "\n")


def _log_journal(entry: dict):
    Path(JOURNAL_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_report_prices() -> dict[str, float]:
    """Load latest prices from reports for mark-to-market of all positions."""
    import os as _os
    reports_dir = runtime_reports_dir()
    try:
        files = sorted(
            [f for f in _os.listdir(reports_dir) if f.startswith("report_") and f.endswith(".json")],
            reverse=True,
        )
        if not files:
            return {}
        report = json.loads((reports_dir / files[0]).read_text())
        prices = {}
        for a in report.get("assets", []):
            sym = a.get("symbol", "").upper()
            price = a.get("current_price") or a.get("price")
            if sym and price:
                prices[sym] = float(price)
        return prices
    except Exception:
        return {}


def get_portfolio_value(pf: dict, prices: dict) -> float:
    # Merge report prices as fallback so all positions are marked to market
    all_prices = {**_load_report_prices(), **prices}
    positions_value = 0.0
    for sym, pos in pf.get("positions", {}).items():
        price = all_prices.get(sym, pos.get("avg_cost", 0))
        positions_value += pos["shares"] * price
    return pf["cash"] + positions_value


def _check_risk_limits(pf: dict, prices: dict) -> dict | None:
    """Return rejection dict if a risk limit is breached, else None."""
    equity = get_portfolio_value(pf, prices)

    # Update peak equity
    if equity > pf.get("peak_equity", INITIAL_CASH):
        pf["peak_equity"] = equity

    peak = pf.get("peak_equity", INITIAL_CASH)
    drawdown = (peak - equity) / peak if peak > 0 else 0

    if drawdown >= MAX_DRAWDOWN_PCT:
        return {
            "status": "rejected",
            "reason": f"Max drawdown limit: {drawdown:.1%} >= {MAX_DRAWDOWN_PCT:.0%} (peak=${peak:,.0f} equity=${equity:,.0f})",
        }

    # Reset daily P&L if date changed
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if pf.get("daily_date") != today:
        pf["daily_pnl"] = 0.0
        pf["daily_date"] = today

    daily_pnl = pf.get("daily_pnl", 0.0)
    daily_loss_pct = abs(daily_pnl) / INITIAL_CASH if daily_pnl < 0 else 0
    if daily_loss_pct >= DAILY_LOSS_LIMIT:
        return {
            "status": "rejected",
            "reason": f"Daily loss limit: {daily_loss_pct:.1%} >= {DAILY_LOSS_LIMIT:.0%} (daily P&L=${daily_pnl:,.0f})",
        }

    return None


def execute_signal(signal: dict, prices: dict) -> dict:
    """
    Execute BUY or SELL from a signal dict.
    BUY: Kelly sizing, max 20% per position, max 60% total.
    SELL: Close full position.
    Returns confirmation with fill details.
    """
    pf = load_portfolio()

    symbol = signal.get("asset", signal.get("symbol", "???"))
    action = str(signal.get("action", "")).upper()
    current_price = prices.get(symbol)

    if current_price is None or current_price <= 0:
        log.warning("[paper] No price for %s, cannot execute %s", symbol, action)
        return {"status": "skipped", "reason": f"No price for {symbol}"}

    # Risk kill switches — only block BUY (SELL always allowed to reduce risk)
    if action == "BUY":
        risk_reject = _check_risk_limits(pf, prices)
        if risk_reject:
            save_portfolio(pf)  # Persist updated peak/daily fields
            log.warning("[paper] %s BUY blocked: %s", symbol, risk_reject["reason"])
            return risk_reject

    fill_price = current_price * (1 + SLIPPAGE) if action == "BUY" else current_price * (1 - SLIPPAGE)

    if action == "BUY":
        return _execute_buy(pf, symbol, fill_price, signal)
    elif action == "SELL":
        return _execute_sell(pf, symbol, fill_price, signal)
    else:
        return {"status": "skipped", "reason": f"Non-executable action: {action}"}


def _execute_buy(pf: dict, symbol: str, fill_price: float, signal: dict) -> dict:
    # Calculate total allocation from existing positions
    positions_value = 0.0
    for sym, pos in pf.get("positions", {}).items():
        positions_value += pos["shares"] * fill_price  # approximate

    total_equity = pf["cash"] + positions_value
    current_allocation = positions_value / total_equity if total_equity > 0 else 0

    if current_allocation >= MAX_TOTAL_ALLOCATION:
        log.info("[paper] %s BUY blocked: total allocation %.0f%% >= %.0f%%",
                 symbol, current_allocation * 100, MAX_TOTAL_ALLOCATION * 100)
        return {"status": "rejected", "reason": f"Total allocation {current_allocation:.0%} >= {MAX_TOTAL_ALLOCATION:.0%}"}

    # Kelly-style sizing: confidence * remaining allocation headroom
    confidence = float(signal.get("confidence", 0.5))
    allocation_headroom = MAX_TOTAL_ALLOCATION - current_allocation
    position_pct = min(confidence * allocation_headroom, MAX_POSITION_PCT)
    position_pct = max(position_pct, 0.05)  # min 5% if we're buying

    # Apply backtest-gate position modifier if present
    modifier = float(signal.get("position_modifier", 1.0))
    if modifier < 1.0:
        position_pct *= modifier

    cash_to_use = pf["cash"] * position_pct
    shares = cash_to_use / fill_price
    cost = shares * fill_price

    if cost > pf["cash"]:
        shares = pf["cash"] / fill_price
        cost = pf["cash"]

    if cost <= 0 or shares <= 0:
        return {"status": "rejected", "reason": "Insufficient cash"}

    pf["cash"] -= cost

    if symbol not in pf["positions"]:
        pf["positions"][symbol] = {"shares": 0.0, "avg_cost": 0.0}

    pos = pf["positions"][symbol]
    total_shares = pos["shares"] + shares
    pos["avg_cost"] = ((pos["shares"] * pos["avg_cost"]) + cost) / total_shares if total_shares > 0 else fill_price
    pos["shares"] = total_shares
    
    # Stop-loss — use signal value or default 5% below fill price
    if signal.get("stop_loss"):
        pos["stop_loss"] = float(signal["stop_loss"])
    elif not pos.get("stop_loss"):
        pos["stop_loss"] = round(fill_price * (1 - STOP_LOSS_DEFAULT_PCT), 6)
    if signal.get("take_profit"):
        pos["take_profit"] = float(signal["take_profit"])

    # Trailing stop tracking state (only initialise for new positions)
    pos.setdefault("highest_price", fill_price)
    pos.setdefault("trail_active", False)
    pos.setdefault("trail_stop", None)
    pos.setdefault("opened_at", datetime.now(timezone.utc).isoformat())

    order = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": "BUY",
        "shares": round(shares, 8),
        "fill_price": round(fill_price, 4),
        "cost": round(cost, 2),
        "slippage": SLIPPAGE,
        "portfolio_cash_after": round(pf["cash"], 2),
    }
    pf["orders"].append(order)
    save_portfolio(pf)
    _log_order(order)

    journal_entry = {
        **order,
        "reasoning": signal.get("reasoning", "")[:300],
        "confidence": signal.get("confidence"),
    }
    _log_journal(journal_entry)

    log.info("[paper] BUY %s: %.6f shares @ $%.2f = $%.2f | cash=$%.2f",
             symbol, shares, fill_price, cost, pf["cash"])

    return {"status": "filled", **order}


def _execute_sell(pf: dict, symbol: str, fill_price: float, signal: dict) -> dict:
    if symbol not in pf.get("positions", {}):
        return {"status": "rejected", "reason": f"No position in {symbol}"}

    pos = pf["positions"][symbol]
    shares = pos["shares"]
    proceeds = shares * fill_price

    pf["cash"] += proceeds
    pnl = proceeds - (shares * pos["avg_cost"])
    pf["pnl"] += pnl
    pf["daily_pnl"] = pf.get("daily_pnl", 0.0) + pnl
    del pf["positions"][symbol]

    order = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": "SELL",
        "shares": round(shares, 8),
        "fill_price": round(fill_price, 4),
        "proceeds": round(proceeds, 2),
        "pnl": round(pnl, 2),
        "slippage": SLIPPAGE,
        "portfolio_cash_after": round(pf["cash"], 2),
    }
    pf["orders"].append(order)
    save_portfolio(pf)
    _log_order(order)

    journal_entry = {
        **order,
        "reasoning": signal.get("reasoning", "")[:300],
        "confidence": signal.get("confidence"),
    }
    _log_journal(journal_entry)

    log.info("[paper] SELL %s: %.6f shares @ $%.2f = $%.2f | pnl=$%.2f | cash=$%.2f",
             symbol, shares, fill_price, proceeds, pnl, pf["cash"])

    return {"status": "filled", **order}


def check_stops(prices: dict) -> list[dict]:
    """Sweep all open positions for stop-loss and trailing-stop hits.

    Called every pipeline tick. Returns list of closed-position order dicts.
    Stop-loss: fixed price stored on position (defaults to 5% below avg_cost).
    Trailing stop: activates at +10% gain, then trails 5% below the highest seen price.
    """
    pf = load_portfolio()
    closed = []

    for symbol in list(pf["positions"].keys()):
        pos = pf["positions"][symbol]
        price = prices.get(symbol)
        if price is None:
            continue

        avg_cost = pos.get("avg_cost", 0)
        if avg_cost <= 0:
            continue

        # Ratchet highest price
        high = max(pos.get("highest_price", avg_cost), price)
        pos["highest_price"] = high

        # Activate trailing stop at +10% gain
        gain_pct = (price - avg_cost) / avg_cost
        if gain_pct >= TRAIL_TRIGGER_PCT and not pos.get("trail_active"):
            pos["trail_active"] = True
            log.info("[%s] Trailing stop activated — gain=%.1f%%", symbol, gain_pct * 100)

        # Update trailing stop level (ratchets up, never down)
        if pos.get("trail_active"):
            new_trail = round(high * (1 - TRAIL_PCT), 6)
            existing = pos.get("trail_stop")
            if existing is None or new_trail > existing:
                pos["trail_stop"] = new_trail

        # Determine whether a stop is hit
        stop_loss = pos.get("stop_loss") or round(avg_cost * (1 - STOP_LOSS_DEFAULT_PCT), 6)
        trail_stop = pos.get("trail_stop")

        stop_hit  = price <= stop_loss
        trail_hit = bool(pos.get("trail_active") and trail_stop and price <= trail_stop)

        if not (stop_hit or trail_hit):
            continue

        exit_reason = "trailing-stop" if trail_hit else "stop-loss"
        fill = round(price * (1 - SLIPPAGE), 4)
        shares = pos["shares"]
        proceeds = round(shares * fill, 2)
        pnl = round(proceeds - shares * avg_cost, 2)

        pf["cash"] += proceeds
        pf["pnl"] = round(pf.get("pnl", 0.0) + pnl, 2)
        pf["daily_pnl"] = round(pf.get("daily_pnl", 0.0) + pnl, 2)
        del pf["positions"][symbol]

        order = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": "SELL",
            "shares": round(shares, 8),
            "fill_price": fill,
            "proceeds": proceeds,
            "pnl": pnl,
            "slippage": SLIPPAGE,
            "exit_reason": exit_reason,
            "portfolio_cash_after": round(pf["cash"], 2),
        }
        _log_order(order)
        _log_journal({**order, "reasoning": f"auto: {exit_reason}"})
        closed.append(order)
        log.info("[paper] %s %s: %.6f @ $%.2f pnl=$%.2f",
                 exit_reason.upper(), symbol, shares, fill, pnl)

    save_portfolio(pf)
    return closed


def execute_batch(signals: list[dict], price_map: dict) -> dict:
    """Execute multiple signals with per-signal rollback on failure.
    Each signal succeeds or fails independently — a failed BUY doesn't block other signals."""
    pf = load_portfolio()
    results = {"executed": [], "skipped": [], "portfolio": get_summary()}

    for signal in signals:
        sym = signal.get("asset", signal.get("symbol"))
        action = signal.get("action", "BUY")
        price = price_map.get(sym)

        if not price:
            results["skipped"].append({"symbol": sym, "reason": "no_price"})
            continue

        try:
            result = execute_signal(signal, {sym: price})
            if result.get("status") == "filled":
                results["executed"].append({"symbol": sym, "action": action, "result": result})
            else:
                results["skipped"].append({"symbol": sym, "reason": result.get("reason", "unknown")})
        except Exception as e:
            results["skipped"].append({"symbol": sym, "reason": str(e)[:100]})
            continue

    results["portfolio"] = get_summary()
    return results


def get_summary() -> dict:
    """Cash, equity, P&L, positions, daily change."""
    pf = load_portfolio()
    positions = pf.get("positions", {})
    return {
        "cash": round(pf["cash"], 2),
        "positions": positions,
        "total_positions": len(positions),
        "pnl": round(pf.get("pnl", 0), 2),
        "orders_count": len(pf.get("orders", [])),
        "created_at": pf.get("created_at"),
        "slippage_stats": get_slippage_stats(),
    }


def get_slippage_stats() -> dict:
    """Compute adaptive slippage statistics from order history.

    Reads the `slippage` rate recorded on each order (the fractional rate
    applied at fill time, e.g. 0.001 = 0.1%).  Returns avg/p50/p95 across
    all orders and a recommended rate (avg * 1.5, floored at 0.001).
    """
    if not ORDERS_FILE.exists():
        return {"avg": SLIPPAGE, "p50": SLIPPAGE, "p95": SLIPPAGE,
                "recommended": SLIPPAGE, "samples": 0}

    slippages: list[float] = []
    try:
        for line in ORDERS_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
                rate = o.get("slippage")
                if rate is not None:
                    slippages.append(float(rate))
            except (ValueError, KeyError, TypeError):
                continue
    except Exception:
        return {"avg": SLIPPAGE, "p50": SLIPPAGE, "p95": SLIPPAGE,
                "recommended": SLIPPAGE, "samples": 0}

    if not slippages:
        return {"avg": SLIPPAGE, "p50": SLIPPAGE, "p95": SLIPPAGE,
                "recommended": SLIPPAGE, "samples": 0}

    slippages.sort()
    n = len(slippages)
    avg = sum(slippages) / n
    p50 = slippages[n // 2]
    p95 = slippages[int(n * 0.95)]
    recommended = max(0.001, round(avg * 1.5, 5))

    return {
        "avg":         round(avg, 6),
        "p50":         round(p50, 6),
        "p95":         round(p95, 6),
        "recommended": recommended,
        "samples":     n,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # Smoke test
    pf = load_portfolio()
    print(f"Cash: ${pf['cash']:,.2f}")
    print(f"Positions: {pf['positions']}")
    print(f"Total P&L: ${pf.get('pnl', 0):,.2f}")

    # Test buy
    result = execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.7, "reasoning": "Strong signal"},
        {"BTC": 97000.0},
    )
    print(f"Buy result: {json.dumps(result, indent=2)}")

    # Test summary
    print(f"Summary: {json.dumps(get_summary(), indent=2)}")
