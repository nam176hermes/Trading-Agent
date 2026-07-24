"""
execute_live.py — Real order execution via CCXT bridge.

Reuses paper_trader position sizing logic but routes to CCXT instead
of simulation. Entry point: execute_signals(report) called from main.py.

Flow:
  1. Check mode: paper→paper_trader, dryrun→CCXT sandbox, live→CCXT real
  2. For each BUY/SELL signal:
     a. Pre-flight checks (kill switch, balance, min order, market open)
     b. Compute position size via paper_trader sizing logic
     c. Place order via ccxt_bridge.place_order()
     d. Log to orders.jsonl + send Telegram notification
  3. Track positions in live_positions.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

from exchange.ccxt_bridge import (
    get_mode,
    LIVE_EXECUTION_ENABLED,
    MIN_ORDER_USD,
    MAX_SLIPPAGE_PCT,
    fetch_balances,
    place_order,
    get_exchange,
)
from paper_trader import (
    execute_signal as paper_execute,
    load_portfolio,
    _log_order,
    _log_journal,
    MAX_POSITION_PCT,
)
from risk_engine import (
    cvar_position_size,
    check_circuit_breaker,
    estimate_funding_cost,
    MIN_RETURNS_FOR_CVAR,
)

# ── Allocation awareness integration ──
try:
    from allocation_engine import get_target_weight_for_symbol
    _HAS_ALLOCATION_ENGINE = True
except ImportError:
    _HAS_ALLOCATION_ENGINE = False
    get_target_weight_for_symbol = None  # type: ignore[assignment]

# ── Exit strategies integration ──
try:
    from exit_strategies import tiered_profit_exit, time_based_exit, trailing_stop
    _HAS_EXIT_STRATEGIES = True
except ImportError:
    _HAS_EXIT_STRATEGIES = False
    tiered_profit_exit = None
    time_based_exit = None
    trailing_stop = None

log = logging.getLogger("execute_live")

# ── GARCH volatility integration ──
try:
    from garch_vol import forecast_volatility, fit_garch
    _HAS_GARCH = True
except ImportError:
    _HAS_GARCH = False
    forecast_volatility = None
    fit_garch = None

# ── CRA tax tracker integration ──
import cra_tracker

LIVE_POSITIONS_FILE = data_root() / "memory" / "live_positions.json"
LIVE_ORDERS_FILE = data_root() / "memory" / "paper" / "live_orders.jsonl"

# Default exchange for crypto execution
DEFAULT_EXCHANGE = "coinbase"

# Symbols that route to crypto exchanges (vs stocks to Alpaca)
CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC"}


def preflight(exchange_id: str, symbol: str, side: str, amount: float) -> tuple[bool, Optional[str]]:
    """Run safety checks before placing a live/dryrun order.

    Returns (True, None) if OK, or (False, "reason") if blocked.
    """
    mode = get_mode()

    # 1. Kill switch — live mode requires explicit enable
    if mode == "live" and not LIVE_EXECUTION_ENABLED:
        return False, "LIVE_EXECUTION_ENABLED=False — kill switch active"

    # 1.5. Incubation gate — live requires validated paper-trade history (Kevin Davey)
    if mode == "live":
        try:
            from incubation_tracker import is_incubation_passed, incubation_status
            if not is_incubation_passed():
                status = incubation_status()
                return False, (
                    f"Incubation gate not passed: "
                    f"{status['n_resolved']}/{status['n_required']} signals resolved, "
                    f"win rate {status['win_rate']:.0%}"
                )
        except ImportError:
            pass

    # 2. Minimum order size
    if amount <= 0:
        return False, f"Invalid order amount: {amount}"

    # 3. Balance check — ensure enough free balance
    if side.lower() == "buy":
        balances = fetch_balances(exchange_id)
        if balances is None:
            return False, "Cannot fetch balances — API unavailable"
        usd_balance = balances.get("USD", {}).get("free", 0) or balances.get("USDT", {}).get("free", 0) or balances.get("USDC", {}).get("free", 0)
        if usd_balance <= 0:
            return False, f"No USD/USDT/USDC balance on {exchange_id}"

    # 4. Market open check — crypto is 24/7, but exchanges do maintenance
    # CCXT will surface maintenance errors; we don't pre-check here.

    return True, None


def _load_live_positions() -> dict:
    """Load or initialize live positions tracking."""
    if LIVE_POSITIONS_FILE.exists():
        try:
            data = json.loads(LIVE_POSITIONS_FILE.read_text())
            return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {"positions": {}, "updated_at": None}


def _save_live_positions(data: dict) -> None:
    LIVE_POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    LIVE_POSITIONS_FILE.write_text(json.dumps(data, indent=2, default=str))


def _compute_position_size(symbol: str, price: float, confidence: float,
                           side: str = "BUY", exchange_id: str = DEFAULT_EXCHANGE) -> Optional[dict]:
    """Compute position size for real/dry-run execution.

    BUY: Kelly-style sizing against exchange quote balance.
    SELL: Full position close using exchange base balance.
    Returns dict with shares, cost, position_pct or None if insufficient funds.
    """
    mode = get_mode()
    if mode == "paper":
        return None

    balances = fetch_balances(exchange_id)
    if balances is None:
        log.warning("[%s] Cannot fetch balances — aborting position sizing", symbol)
        return None

    base_asset = symbol.split("/")[0] if "/" in symbol else symbol

    if side.upper() == "SELL":
        # Close full position: use actual base asset balance
        base_balance = balances.get(base_asset, {}).get("free", 0) or balances.get(base_asset, {}).get("total", 0)
        if base_balance <= 0:
            log.info("[%s] No %s balance to sell on %s", symbol, base_asset, exchange_id)
            return None
        cost = base_balance * price
        if cost < MIN_ORDER_USD:
            log.info("[%s] SELL too small ($%.2f < $%.2f minimum) — skipping", symbol, cost, MIN_ORDER_USD)
            return None
        return {
            "position_pct": 1.0,
            "cash_to_use": 0,
            "shares": round(base_balance, 8),
            "cost": round(cost, 2),
            "quote_balance": 0,
        }

    # BUY: CVaR-based position sizing (replaces Kelly)
    # CVaR is robust to crypto's fat tails (kurtosis 15+) unlike Kelly
    # Source: risk_engine.py + 19-book adversarial audit

    # Try loading recent returns for CVaR calculation
    returns_30d = None
    try:
        from pathlib import Path
        import numpy as np
        import json as _json
        returns_dir = data_root() / "memory" / "backtest" / "returns"
        returns_file = returns_dir / f"{symbol.replace('/', '_')}_returns.json"
        if returns_file.exists():
            data = _json.loads(returns_file.read_text())
            rets = data.get("daily_returns", [])[-30:]
            if len(rets) >= MIN_RETURNS_FOR_CVAR:
                returns_30d = np.array(rets)
    except Exception:
        pass

    quote_balance = (
            balances.get("USD", {}).get("free", 0)
            or balances.get("USDT", {}).get("free", 0)
            or balances.get("USDC", {}).get("free", 0)
            or 0
    )
    if quote_balance <= 0:
        log.warning("[%s] No quote balance available on %s", symbol, exchange_id)
        return None

    if returns_30d is not None and len(returns_30d) >= MIN_RETURNS_FOR_CVAR:
        # CVaR-based sizing (preferred)
        sizing = cvar_position_size(symbol, price, "BUY", quote_balance, returns_30d)
        if sizing is None:
            log.warning("[%s] CVaR sizing failed — falling back to Kelly", symbol)
        else:
            cost = sizing.notional
            shares = sizing.quantity
            position_pct = sizing.capital_used_pct / 100
            cash_to_use = cost
            log.info("[%s] CVaR sizing: %s shares at $%.2f (risk=$%.2f, CVaR95=%.4f%%)",
                     symbol, shares, price, sizing.risk_amount, sizing.cvar_95 * 100)
    else:
        # Fallback 1: try equity curve returns from performance_tracker (live trade history)
        _pt_returns = None
        try:
            from performance_tracker import _equity_returns
            _pt_rets = _equity_returns()
            if len(_pt_rets) >= MIN_RETURNS_FOR_CVAR:
                import numpy as _np
                _pt_returns = _np.array(_pt_rets[-30:])
        except Exception:
            pass

        if _pt_returns is not None and len(_pt_returns) >= MIN_RETURNS_FOR_CVAR:
            # CVaR from live equity curve (adapts to actual account performance)
            sizing = cvar_position_size(symbol, price, "BUY", quote_balance, _pt_returns)
            if sizing is not None:
                cost = sizing.notional
                shares = sizing.quantity
                position_pct = sizing.capital_used_pct / 100
                cash_to_use = cost
                log.info("[%s] CVaR sizing (equity returns fallback): %.0f%% (CVaR95=%.4f%%)",
                         symbol, position_pct * 100, sizing.cvar_95 * 100)
            else:
                # Fallback 2: confidence-based (last resort)
                position_pct = min(confidence * 0.50, MAX_POSITION_PCT)
                position_pct = max(position_pct, 0.01)
                cash_to_use = quote_balance * position_pct
                shares = cash_to_use / price
                cost = shares * price
                log.info("[%s] CVaR failed — confidence-based sizing: %.0f%%", symbol, position_pct * 100)
        else:
            # Fallback 2: confidence-based (no returns available anywhere)
            position_pct = min(confidence * 0.50, MAX_POSITION_PCT)
            position_pct = max(position_pct, 0.01)
            cash_to_use = quote_balance * position_pct
            shares = cash_to_use / price
            cost = shares * price
            log.info("[%s] No returns data — confidence-based sizing: %.0f%%", symbol, position_pct * 100)

    # ── GARCH volatility-based position size adjustment ──
    vol_adjustment = 1.0
    if _HAS_GARCH and returns_30d is not None and len(returns_30d) >= 10:
        try:
            import numpy as np
            vol_forecast = forecast_volatility(returns_30d)
            if vol_forecast and vol_forecast > 0:
                annualized = vol_forecast * np.sqrt(252)
                # Risk-adjusted sizing: tighten in high vol, expand in low vol
                if annualized < 0.60:
                    vol_adjustment = 1.10
                elif annualized > 1.00:
                    vol_adjustment = 0.75
                else:
                    vol_adjustment = 1.0
                log.info("[%s] GARCH vol: %.1f%% annualized → size adj: %.2f",
                         symbol, annualized * 100, vol_adjustment)
        except Exception as e:
            log.debug("[%s] GARCH vol adjustment failed: %s", symbol, e)

    if vol_adjustment != 1.0:
        position_pct = min(position_pct * vol_adjustment, MAX_POSITION_PCT)
        cash_to_use *= vol_adjustment
        shares *= vol_adjustment
        cost *= vol_adjustment

    if cost < MIN_ORDER_USD:
        log.info("[%s] Order too small ($%.2f < $%.2f minimum) — skipping", symbol, cost, MIN_ORDER_USD)
        return None

    return {
        "position_pct": round(position_pct, 4),
        "cash_to_use": round(cash_to_use, 2),
        "shares": round(shares, 8),
        "cost": round(cost, 2),
        "quote_balance": round(quote_balance, 2),
    }


def _check_allocation_alignment(symbol: str, sizing: dict) -> tuple[dict, str]:
    """Check if a BUY trade moves toward or away from optimal allocation.

    If moving AWAY from target weight: reduce size by 50% and log warning.

    Returns (sizing_dict, log_message).
    """
    if not _HAS_ALLOCATION_ENGINE or get_target_weight_for_symbol is None:
        return sizing, ""

    if sizing is None:
        return sizing, ""

    try:
        target_weight = get_target_weight_for_symbol(symbol)
    except Exception as e:
        log.debug("[%s] Allocation check skipped — engine error: %s", symbol, e)
        return sizing, ""

    if target_weight is None:
        return sizing, ""

    # Approximate current weight and post-trade weight
    # Current weight ≈ sizing.position_pct (this is the capital share being deployed)
    # This is a coarse check: if target is < current_pos_pct, adding more
    # moves us further away from target
    current_pct = sizing.get("position_pct", 0)
    # The target weight is a fraction (0-1)
    target_pct = float(target_weight)

    # If the position we're building is already above target, adding more
    # would increase the allocation gap — we're moving AWAY from optimal
    if current_pct > target_pct and target_pct > 0:
        # Reduce size by 50%
        reduction = 0.50
        sizing["shares"] *= (1 - reduction)
        sizing["cost"] *= (1 - reduction)
        sizing["position_pct"] *= (1 - reduction)
        sizing["cash_to_use"] *= (1 - reduction)
        sizing["_allocation_trimmed"] = True
        msg = (
            "[{}] ALLOCATION AWARE: position would be {:.1f}% vs target {:.1f}% "
            "→ reducing size 50% (from {:.1f}% to {:.1f}%)".format(
                symbol,
                current_pct * 100, target_pct * 100,
                current_pct * 100, current_pct * 50,
            )
        )
        log.warning(msg)
        return sizing, msg
    else:
        sizing["_allocation_trimmed"] = False
        msg = (
            "[{}] ALLOCATION OK: current {:.1f}% <= target {:.1f}% — "
            "moving toward optimal".format(
                symbol, current_pct * 100, target_pct * 100,
            )
        )
        log.info(msg)
        return sizing, msg


def execute_signals(report: dict) -> dict:
    """Execute trading signals from a pipeline report.

    Routes to paper_trader or ccxt_bridge depending on mode.
    Returns summary of executions.
    """
    mode = get_mode()
    results = {"executed": [], "skipped": [], "errors": [], "mode": mode}

    assets = report.get("assets", [])
    if not assets:
        log.info("[execute_live] No assets in report — nothing to do")
        return results

    for asset in assets:
        sym = asset.get("symbol", "???")
        action = str(asset.get("suggestion", "")).upper()
        execution = asset.get("execution")

        if action not in ("BUY", "SELL"):
            continue

        # Build signal dict for paper_trader compatibility
        price = float(asset.get("price") or asset.get("current_price") or 0)
        if not price:
            results["skipped"].append({"symbol": sym, "reason": "no_price"})
            continue

        confidence = float(asset.get("confidence", 0.5))
        signal = {
            "asset": sym,
            "action": action,
            "confidence": confidence,
            "reasoning": asset.get("rationale", "")[:300],
            "entry_price": price,
            "stop_loss": asset.get("stop_loss_suggestion"),
            "take_profit": asset.get("target_suggestion"),
            "position_modifier": asset.get("_gate_modifier", 1.0),
        }

        try:
            if mode == "paper":
                # Delegate to paper_trader (simulation only)
                confirmation = paper_execute(signal, {sym: price})
                results["executed"].append({
                    "symbol": sym, "action": action, "mode": "paper",
                    "result": confirmation,
                })
            else:
                # dryrun or live: real CCXT order execution
                exchange_id = DEFAULT_EXCHANGE

                # Compute position size against real balance
                sizing = _compute_position_size(sym, price, confidence, action, exchange_id)
                if sizing is None:
                    results["skipped"].append({
                        "symbol": sym, "action": action,
                        "reason": "insufficient_balance_or_below_minimum",
                    })
                    continue

                # Allocation awareness: check if BUY aligns with optimal weights
                if action == "BUY":
                    sizing, _alloc_msg = _check_allocation_alignment(sym, sizing)
                    if sizing.get("_allocation_trimmed"):
                        signal["_allocation_trimmed"] = True

                # Pre-flight checks
                ok, reason = preflight(exchange_id, f"{sym}/USD", action.lower(), sizing["shares"])
                if not ok:
                    log.warning("[%s] Preflight failed: %s", sym, reason)
                    results["skipped"].append({
                        "symbol": sym, "action": action, "reason": f"preflight: {reason}",
                    })
                    if mode == "live":
                        from alert_manager import send_telegram_text
                        send_telegram_text(
                            f"BLOCKED [{mode.upper()}] {action} {sym}: {reason}"
                        )
                    continue

                # Place the order
                order_result = place_order(
                    exchange_id,
                    f"{sym}/USD",
                    action.lower(),
                    sizing["shares"],
                    price=None,  # Market order
                )

                if order_result is None or order_result.get("status") == "blocked":
                    block_reason = (order_result or {}).get("reason", "API failure")
                    results["skipped"].append({
                        "symbol": sym, "action": action,
                        "reason": f"order_failed: {block_reason}",
                    })
                    continue

                # Log the fill
                fill_price = float(order_result.get("avg_fill_price") or price)
                order_log = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": sym,
                    "side": action,
                    "shares": sizing["shares"],
                    "fill_price": round(fill_price, 4),
                    "cost": sizing["cost"],
                    "exchange": exchange_id,
                    "mode": mode,
                    "order_id": order_result.get("id"),
                    "slippage": abs(fill_price - price) / price if price else 0,
                }

                LIVE_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(LIVE_ORDERS_FILE, "a") as f:
                    f.write(json.dumps(order_log, default=str) + "\n")

                # Record trade for Canadian tax (CRA)
                try:
                    cra_tracker.log_trade(
                        symbol=sym,
                        side=action.lower(),
                        quantity=sizing["shares"],
                        price_usd=fill_price,
                    )
                except Exception as _cra_e:
                    log.debug("[%s] CRA trade logging failed: %s", sym, _cra_e)

                # Update live positions
                live_pos = _load_live_positions()
                if action == "BUY":
                    if sym not in live_pos["positions"]:
                        live_pos["positions"][sym] = {"shares": 0, "avg_cost": 0}
                    pos = live_pos["positions"][sym]
                    total = pos["shares"] + sizing["shares"]
                    pos["avg_cost"] = ((pos["shares"] * pos["avg_cost"]) + sizing["cost"]) / total if total > 0 else fill_price
                    pos["shares"] = total
                elif action == "SELL":
                    live_pos["positions"].pop(sym, None)
                _save_live_positions(live_pos)

                results["executed"].append({
                    "symbol": sym, "action": action, "mode": mode,
                    "exchange": exchange_id,
                    "result": order_log,
                })

                # Telegram notification
                try:
                    from alert_manager import send_telegram_text
                    tag = "LIVE" if mode == "live" else "DRYRUN"
                    send_telegram_text(
                        f"[{tag}] {action} {sym}: {sizing['shares']:.4f} shares "
                        f"@ ${fill_price:,.2f} | "
                        f"Cost ${sizing['cost']:,.2f} ({sizing['position_pct']:.1%})"
                    )
                except Exception:
                    pass

                log.info("[%s] %s %s: %.6f shares @ $%.2f = $%.2f",
                         mode.upper(), action, sym,
                         sizing["shares"], fill_price, sizing["cost"])

        except Exception as e:
            log.error("[%s] Execution error: %s", sym, e)
            results["errors"].append({"symbol": sym, "action": action, "error": str(e)[:200]})

    log.info("[execute_live] Mode=%s — %d executed, %d skipped, %d errors",
             mode, len(results["executed"]), len(results["skipped"]), len(results["errors"]))

    return results


def get_live_summary() -> dict:
    """Return summary of live positions for dashboard display."""
    mode = get_mode()
    live_pos = _load_live_positions()

    # Enrich with current prices via CCXT
    positions = {}
    for sym, pos in live_pos.get("positions", {}).items():
        try:
            ticker = fetch_balances.__self__ if hasattr(fetch_balances, "__self__") else None
        except Exception:
            pass
        positions[sym] = {
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0),
        }

    return {
        "mode": mode,
        "live_execution_enabled": LIVE_EXECUTION_ENABLED,
        "positions": positions,
        "updated_at": live_pos.get("updated_at"),
    }


def monitor_positions_for_exits() -> dict:
    """Monitor open positions and execute tiered profit-taking exits.

    Applies Kevin Davey-style tiered exits:
      - 1R profit → close 50% of position
      - 2R profit → close 30% of remaining
      - Keep 20% runner with ATR trailing stop

    Only operates in dryrun or live mode (skips paper mode silently).
    Tracks executed tier fractions in live_positions.json to avoid
    double-closing on repeated calls.

    Returns summary dict with actions taken.
    """
    mode = get_mode()
    if mode == "paper":
        return {"status": "skipped", "reason": "paper_mode"}

    if not _HAS_EXIT_STRATEGIES:
        log.debug("Exit strategies not available — skipping position monitoring")
        return {"status": "skipped", "reason": "exit_strategies_unavailable"}

    live_pos = _load_live_positions()
    positions = live_pos.get("positions", {})
    if not positions:
        return {"status": "no_positions"}

    # Initialize tier tracking per symbol
    if "tiered_exits" not in live_pos:
        live_pos["tiered_exits"] = {}

    exchange_id = DEFAULT_EXCHANGE
    summary = {"actions": [], "errors": []}

    for sym, pos in list(positions.items()):
        shares_held = pos.get("shares", 0)
        avg_cost = pos.get("avg_cost", 0)
        if shares_held <= 0 or avg_cost <= 0:
            continue

        # Fetch current price
        try:
            from exchange.ccxt_bridge import fetch_ticker
            ticker = fetch_ticker(exchange_id, f"{sym}/USD")
            if ticker is None:
                continue
            current_price = float(ticker.get("last", ticker.get("close", 0)))
            if current_price <= 0:
                continue
        except Exception as e:
            log.warning("[%s] Cannot fetch price for exit monitoring: %s", sym, e)
            continue

        # Get ATR for the symbol (2% of price proxy for crypto)
        atr_value = current_price * 0.02

        # Track already-closed fraction for this symbol
        sym_tiers = live_pos["tiered_exits"].get(sym, {
            "cumulative_closed": 0.0,
            "high_watermark": current_price,
        })
        cum_closed = sym_tiers.get("cumulative_closed", 0.0)
        high_watermark = max(sym_tiers.get("high_watermark", current_price), current_price)

        # Apply tiered profit exit
        result = tiered_profit_exit(
            entry_price=avg_cost,
            current_price=current_price,
            atr=atr_value,
            direction="long",
            high_watermark=high_watermark,
        )

        sym_tiers["high_watermark"] = high_watermark

        action = result.get("action", "hold")
        close_pct = result.get("close_pct", 0.0)
        reason = result.get("reason", "")

        if action == "hold" or close_pct <= cum_closed:
            log.debug("[%s] Exit monitor: HOLD (cum_closed=%.0f%%, close_pct=%.0f%%)",
                      sym, cum_closed * 100, close_pct * 100)
            live_pos["tiered_exits"][sym] = sym_tiers
            continue

        # Only close the delta (fraction not yet closed)
        delta_close = close_pct - cum_closed
        if delta_close <= 0:
            continue

        shares_to_sell = round(shares_held * delta_close / (1.0 - cum_closed), 8)
        if shares_to_sell <= 0:
            continue

        log.info("[%s] TIERED EXIT: closing %.0f%% (delta=%.0f%%) — %s shares @ $%.2f — %s",
                 sym, delta_close * 100, shares_to_sell, current_price, reason)

        # Place sell order for the tier fraction
        try:
            order_result = place_order(
                exchange_id,
                f"{sym}/USD",
                "sell",
                shares_to_sell,
                price=None,  # Market order
            )

            if order_result and order_result.get("status") != "blocked":
                fill_price = float(order_result.get("avg_fill_price", current_price))
                sym_tiers["cumulative_closed"] = close_pct
                live_pos["tiered_exits"][sym] = sym_tiers

                # Update remaining position
                if close_pct >= 1.0:
                    live_pos["positions"].pop(sym, None)
                    live_pos["tiered_exits"].pop(sym, None)
                else:
                    pos["shares"] = max(0, shares_held - shares_to_sell)

                # Log order
                order_log = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": sym,
                    "side": "SELL",
                    "shares": shares_to_sell,
                    "fill_price": round(fill_price, 4),
                    "cost": round(shares_to_sell * fill_price, 2),
                    "exchange": exchange_id,
                    "mode": mode,
                    "order_id": order_result.get("id"),
                    "exit_type": "tiered_profit",
                    "close_pct": round(delta_close, 4),
                    "reason": reason,
                }
                LIVE_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(LIVE_ORDERS_FILE, "a") as f:
                    f.write(json.dumps(order_log, default=str) + "\n")

                summary["actions"].append({
                    "symbol": sym, "action": "partial_sell",
                    "shares": shares_to_sell, "close_pct": round(delta_close, 4),
                    "price": round(fill_price, 2), "reason": reason,
                })

                # Telegram notification
                try:
                    from alert_manager import send_telegram_text
                    tag = "LIVE" if mode == "live" else "DRYRUN"
                    send_telegram_text(
                        f"[{tag}] TIERED EXIT {sym}: {shares_to_sell:.4f} shares "
                        f"(≈{delta_close*100:.0f}%) @ ${fill_price:,.2f} — {reason}"
                    )
                except Exception:
                    pass

                log.info("[%s] Tiered exit: sold %.6f shares @ $%.2f (= %.0f%% of position)",
                         sym, shares_to_sell, fill_price, delta_close * 100)

            else:
                summary["errors"].append({
                    "symbol": sym, "reason": "order_blocked",
                    "detail": (order_result or {}).get("reason", "unknown"),
                })
        except Exception as e:
            log.error("[%s] Tiered exit order failed: %s", sym, e)
            summary["errors"].append({"symbol": sym, "reason": f"order_error: {e}"})

    _save_live_positions(live_pos)

    action_count = len(summary["actions"])
    error_count = len(summary["errors"])
    log.info("[exit_monitor] %d exit(s) executed, %d error(s)", action_count, error_count)

    return summary


def execute_signal(signal: dict, prices: dict) -> dict:
    """Execute a single signal. Drop-in replacement for paper_trader.execute_signal().

    Routes to paper_trader for 'paper' mode, CCXT sandbox for 'dryrun',
    and CCXT real for 'live' (blocked by LIVE_EXECUTION_ENABLED).
    """
    mode = get_mode()
    symbol = signal.get("asset", signal.get("symbol", "???"))
    action = str(signal.get("action", "")).upper()
    price = prices.get(symbol)

    if price is None or price <= 0:
        return {"status": "skipped", "reason": f"No price for {symbol}"}

    if mode == "paper":
        return paper_execute(signal, prices)

    # dryrun or live: real CCXT execution
    exchange_id = DEFAULT_EXCHANGE
    confidence = float(signal.get("confidence", 0.5))
    modifier = float(signal.get("position_modifier", 1.0))

    # Compute position size against real balance
    sizing = _compute_position_size(symbol, price, confidence, action, exchange_id)
    if sizing and modifier < 1.0:
        sizing["shares"] *= modifier
        sizing["cost"] *= modifier
        sizing["position_pct"] *= modifier
    if sizing is None:
        return {"status": "rejected", "reason": "insufficient_balance_or_below_minimum"}

    # Allocation awareness: check if BUY aligns with optimal weights
    if action == "BUY":
        sizing, _alloc_msg = _check_allocation_alignment(symbol, sizing)

    # Pre-flight checks
    ok, reason = preflight(exchange_id, f"{symbol}/USD", action.lower(), sizing["shares"])
    if not ok:
        log.warning("[%s] Preflight failed: %s", symbol, reason)
        return {"status": "rejected", "reason": f"preflight: {reason}"}

    # Place the order
    order_result = place_order(
        exchange_id,
        f"{symbol}/USD",
        action.lower(),
        sizing["shares"],
        price=None,  # Market order
    )

    if order_result is None:
        return {"status": "rejected", "reason": "CCXT API call returned None"}
    if order_result.get("status") == "blocked":
        return {"status": "rejected", "reason": order_result.get("reason", "kill-switch")}

    fill_price = float(order_result.get("avg_fill_price") or price)
    cost = sizing["cost"]

    order_log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": action,
        "shares": sizing["shares"],
        "fill_price": round(fill_price, 4),
        "cost": cost,
        "exchange": exchange_id,
        "mode": mode,
        "order_id": order_result.get("id"),
        "slippage": abs(fill_price - price) / price if price else 0,
    }

    # Persist to live orders
    LIVE_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LIVE_ORDERS_FILE, "a") as f:
        f.write(json.dumps(order_log, default=str) + "\n")

    # Journal
    _log_journal({**order_log, "reasoning": signal.get("reasoning", "")[:300]})

    # Update live positions
    live_pos = _load_live_positions()
    if action == "BUY":
        if symbol not in live_pos["positions"]:
            live_pos["positions"][symbol] = {"shares": 0, "avg_cost": 0}
        pos = live_pos["positions"][symbol]
        total = pos["shares"] + sizing["shares"]
        pos["avg_cost"] = ((pos["shares"] * pos["avg_cost"]) + cost) / total if total > 0 else fill_price
        pos["shares"] = total
    elif action == "SELL":
        live_pos["positions"].pop(symbol, None)
    _save_live_positions(live_pos)

    # Telegram notification
    try:
        from alert_manager import send_telegram_text
        tag = "LIVE" if mode == "live" else "DRYRUN"
        send_telegram_text(
            f"[{tag}] {action} {symbol}: {sizing['shares']:.4f} shares "
            f"@ ${fill_price:,.2f} | "
            f"Cost ${cost:,.2f} ({sizing['position_pct']:.1%})"
        )
    except Exception:
        pass

    log.info("[%s] %s %s: %.6f shares @ $%.2f = $%.2f",
             mode.upper(), action, symbol, sizing["shares"], fill_price, cost)

    return {"status": "filled", **order_log}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print(f"Mode: {get_mode()}")
    print(f"Live execution enabled: {LIVE_EXECUTION_ENABLED}")
    summary = get_live_summary()
    print(json.dumps(summary, indent=2))
