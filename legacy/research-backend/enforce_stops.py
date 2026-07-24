"""
enforce_stops.py — Stop-Loss / Take-Profit enforcement for paper trading.

Reads latest report + open positions. For each open position:
- If current price <= stop_loss_suggestion: SELL at market
- If current price >= target_suggestion: SELL at market
Logs all checks to memory/stop_checks.jsonl.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from paper_trader import execute_batch, load_portfolio
from alert_manager import send_telegram_text
from runtime_paths import data_root, reports_dir

log = logging.getLogger("enforce_stops")
REPORTS_DIR = reports_dir()
MEMORY_DIR = data_root() / "memory"
STOP_CHECKS_FILE = MEMORY_DIR / "stop_checks.jsonl"


def latest_report() -> dict | None:
    """Return the latest report that has stop_loss_suggestion fields (debate reports).
    Falls back to any report if no debate report is found."""
    files = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.startswith("report_") and f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return None

    # Prefer debate reports (have stop_loss_suggestion / target_suggestion)
    for f in files:
        report = json.loads((REPORTS_DIR / f).read_text())
        assets = report.get("assets", [])
        if assets and any(
            a.get("stop_loss_suggestion") is not None for a in assets
        ):
            return report

    # Fallback: return latest report even without stop fields
    return json.loads((REPORTS_DIR / files[0]).read_text())


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from kill_switch import is_kill_switch_active
    if is_kill_switch_active():
        log.warning("Kill switch active — enforce_stops aborted")
        return

    report = latest_report()
    if not report:
        log.info("No report found — nothing to enforce")
        return

    pf = load_portfolio()
    positions = pf.get("positions", {})
    if not positions:
        log.info("No open positions — nothing to enforce")
        return

    # Build price map from report assets
    price_map: dict[str, float] = {}
    target_map: dict[str, float] = {}
    stop_map: dict[str, float] = {}

    for a in report.get("assets", []):
        sym = a.get("symbol", "").upper()
        price = a.get("current_price")
        target = a.get("target_suggestion")
        stop_loss = a.get("stop_loss_suggestion")

        if price:
            price_map[sym] = float(price)
        if target is not None:
            target_map[sym] = float(target)
        if stop_loss is not None:
            stop_map[sym] = float(stop_loss)

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    # First pass: collect all triggers
    signals = []
    checks = []

    # Trailing stop — activate at +10% profit, trail 5% below current price
    trailing_file = MEMORY_DIR / "trailing_stops.json"
    trailing_stops = {}
    if trailing_file.exists():
        try:
            trailing_stops = json.loads(trailing_file.read_text())
        except (json.JSONDecodeError, KeyError):
            pass

    for sym, pos in positions.items():
        current_price = price_map.get(sym)
        entry_price = pos.get("avg_cost", 0)
        if not current_price or not entry_price:
            continue

        pnl_pct = (current_price - entry_price) / entry_price
        prev_trailing = trailing_stops.get(sym, {}).get("stop")
        highest = trailing_stops.get(sym, {}).get("highest_price", current_price)

        if pnl_pct >= 0.10:
            if current_price > highest:
                highest = current_price
            new_stop = round(highest * 0.95, 4)
            if prev_trailing is None or new_stop > prev_trailing:
                trailing_stops[sym] = {
                    "stop": new_stop,
                    "activated_at": datetime.now(timezone.utc).isoformat(),
                    "highest_price": highest,
                }
                log.info("[%s] Trailing stop activated: $%.4f (up %.1f%%, high=$%.2f)",
                         sym, new_stop, pnl_pct * 100, highest)

        if prev_trailing and current_price <= prev_trailing:
            log.info("[%s] Trailing stop HIT: $%.2f <= $%.2f", sym, current_price, prev_trailing)
            signals.append({
                "asset": sym, "action": "SELL", "confidence": 1.0,
                "reasoning": f"Trailing stop hit: ${current_price:.2f} <= ${prev_trailing:.2f}",
            })
            trailing_stops.pop(sym, None)

    trailing_file.write_text(json.dumps(trailing_stops, indent=2))

    for sym, pos in positions.items():
        current_price = price_map.get(sym)
        if not current_price:
            log.info("[%s] No current price — skipping", sym)
            continue

        stop_loss = stop_map.get(sym)
        target = target_map.get(sym)
        reason = None

        if stop_loss and current_price <= stop_loss:
            reason = f"Stop-loss hit: ${current_price:.2f} <= ${stop_loss:.2f}"
        elif target and current_price >= target:
            reason = f"Take-profit hit: ${current_price:.2f} >= ${target:.2f}"

        check_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": sym,
            "current_price": current_price,
            "stop_loss": stop_loss,
            "target": target,
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0),
            "action": "SELL" if reason else "HOLD",
            "reason": reason,
        }

        if reason:
            log.info("[%s] Triggered: %s", sym, reason)
            signals.append({
                "asset": sym, "action": "SELL", "confidence": 1.0, "reasoning": reason,
            })
        else:
            log.info("[%s] No trigger — price=$%.2f stop=$%s target=$%s",
                     sym, current_price, stop_loss, target)

        checks.append(check_entry)

    # Execute all triggered stops as a batch
    if signals:
        result = execute_batch(signals, price_map)
        executed = [e["symbol"] for e in result["executed"]]

        # Update check entries with results
        for c in checks:
            if c["action"] == "SELL":
                c["result"] = "filled" if c["symbol"] in executed else "skipped"
    else:
        executed = []

    # Persist check log
    for c in checks:
        with open(STOP_CHECKS_FILE, "a") as f:
            f.write(json.dumps(c, default=str) + "\n")

    if executed:
        log.info("Executed %d stop/target sell(s): %s", len(executed), executed)
        for e in result["executed"]:
            sym = e["symbol"]
            r = e["result"]
            try:
                pnl = r.get("pnl", 0)
                send_telegram_text(
                    f"🛑 *Stop/Target Hit*\n{sym} — SELL {r.get('shares', 0):.4f} @ ${r.get('fill_price', 0):,.2f}\n"
                    f"PnL: ${pnl:,.2f} | Cash: ${r.get('portfolio_cash_after', 0):,.0f}"
                )
            except Exception:
                pass
    else:
        log.info("No stop-loss or take-profit triggers fired")


if __name__ == "__main__":
    main()
