"""
safety_engine.py — Stop-loss and take-profit enforcement for paper portfolio.

Runs every 5 minutes via cron. Checks all open positions against
their stop-loss and take-profit levels. Triggers paper SELL when hit.

Usage: python3 safety_engine.py
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from paper_trader import load_portfolio, save_portfolio, execute_signal
from runtime_paths import data_root, reports_dir as runtime_reports_dir

log = logging.getLogger("safety_engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

MEMORY_DIR = data_root() / "memory"
CHECKS_FILE = MEMORY_DIR / "stop_checks.jsonl"


def get_current_prices() -> dict[str, float]:
    """Load latest prices from the most recent report."""
    reports_dir = runtime_reports_dir()
    if not reports_dir.exists():
        return {}

    files = sorted(
        [f for f in os.listdir(reports_dir)
         if f.startswith("report_") and f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return {}

    with open(reports_dir / files[0]) as f:
        report = json.load(f)

    prices = {}
    for asset in report.get("assets", []):
        symbol = asset.get("symbol", "").upper()
        price = asset.get("current_price") or asset.get("price")
        if symbol and price:
            prices[symbol] = float(price)
    return prices


def check_position(symbol: str, pos: dict, current_price: float) -> dict | None:
    """
    Check if stop-loss or take-profit is triggered.
    Returns a SELL signal if triggered, None otherwise.
    """
    stop_loss = pos.get("stop_loss")
    take_profit = pos.get("take_profit")
    avg_cost = pos.get("avg_cost", 0)
    shares = pos.get("shares", 0)

    reason = None

    # Check stop-loss (only if set and price moved against us)
    if stop_loss and current_price <= stop_loss:
        loss_pct = (current_price - avg_cost) / avg_cost * 100
        reason = f"Stop-loss triggered: ${current_price:.4f} ≤ ${stop_loss:.4f} ({loss_pct:+.1f}%)"

    # Check take-profit
    elif take_profit and current_price >= take_profit:
        gain_pct = (current_price - avg_cost) / avg_cost * 100
        reason = f"Take-profit triggered: ${current_price:.4f} ≥ ${take_profit:.4f} ({gain_pct:+.1f}%)"

    if reason:
        return {
            "asset": symbol,
            "action": "SELL",
            "confidence": 1.0,
            "reasoning": reason,
            "entry_price": current_price,
        }
    return None




def check_circuit_breaker(panic_threshold_pct: float = -15.0, single_asset_threshold_pct: float = -30.0) -> dict:
    """
    Check if market conditions warrant a circuit breaker halt.

    Triggers when:
    - Average 24h change across tracked assets drops below panic_threshold_pct (default -15%)
    - Any single asset drops below single_asset_threshold_pct (default -30%)

    Returns {"triggered": bool, "detail": str}
    """
    reports_dir = runtime_reports_dir()
    if not reports_dir.exists():
        return {"triggered": False, "detail": "No reports directory — skipping circuit breaker check"}

    files = sorted(
        [f for f in os.listdir(reports_dir)
         if f.startswith("report_") and f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return {"triggered": False, "detail": "No report files found"}

    with open(reports_dir / files[0]) as f:
        report = json.load(f)

    assets = report.get("assets", [])
    if not assets:
        return {"triggered": False, "detail": "Report contains no asset data"}

    changes = []
    extreme_asset = None
    extreme_change = 0.0

    for asset in assets:
        symbol = asset.get("symbol", "???")
        change_pct = asset.get("price_change_24h_pct")
        if change_pct is not None:
            change_pct = float(change_pct)
            changes.append(change_pct)
            if change_pct < extreme_change:
                extreme_change = change_pct
                extreme_asset = symbol

    if not changes:
        return {"triggered": False, "detail": "No 24h change data in report"}

    avg_change = sum(changes) / len(changes)

    # Check single-asset extreme move first (more specific alarm)
    if extreme_change <= single_asset_threshold_pct:
        return {
            "triggered": True,
            "detail": (
                f"Single-asset circuit breaker: {extreme_asset} {extreme_change:+.1f}% 24h "
                f"(threshold {single_asset_threshold_pct:+.0f}%). "
                f"Portfolio average: {avg_change:+.1f}% across {len(changes)} assets."
            ),
        }

    # Check portfolio-wide panic
    if avg_change <= panic_threshold_pct:
        return {
            "triggered": True,
            "detail": (
                f"Panic circuit breaker: average {avg_change:+.1f}% 24h across {len(changes)} assets "
                f"(threshold {panic_threshold_pct:+.0f}%). "
                f"Worst: {extreme_asset} {extreme_change:+.1f}%."
            ),
        }

    return {
        "triggered": False,
        "detail": (
            f"Circuit OK: avg {avg_change:+.1f}% across {len(changes)} assets. "
            f"Worst: {extreme_asset} {extreme_change:+.1f}%."
        ),
    }


def _load_previous_checks() -> dict[str, float]:
    """Load last known price for each symbol from previous safety checks."""
    prev = {}
    if not CHECKS_FILE.exists():
        return prev
    try:
        with open(CHECKS_FILE) as f:
            lines = f.readlines()
        # Read last 20 checks to find most recent price per symbol
        for line in reversed(lines[-100:]):
            try:
                entry = json.loads(line)
                sym = entry.get("symbol", "")
                if sym and sym not in prev:
                    prev[sym] = entry.get("current_price", 0)
            except (json.JSONDecodeError, KeyError):
                continue
    except Exception:
        pass
    return prev


def run_safety_check():
    """Main safety check — runs through all positions."""
    pf = load_portfolio()
    prices = get_current_prices()

    if not prices:
        log.warning("No prices available — skipping safety check")
        return

    positions = pf.get("positions", {})
    if not positions:
        return  # No positions to check

    triggered = []
    for symbol, pos in list(positions.items()):
        current_price = prices.get(symbol)
        if not current_price:
            continue

        # Log the check
        check_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "current_price": current_price,
            "stop_loss": pos.get("stop_loss"),
            "target": pos.get("take_profit"),
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0),
        }

        signal = check_position(symbol, pos, current_price)
        if signal:
            check_entry["action"] = "SELL"
            check_entry["reason"] = signal["reasoning"]
            triggered.append((symbol, signal))
            log.warning("[SAFETY] %s %s — executing SELL", symbol, signal["reasoning"])
        else:
            check_entry["action"] = "HOLD"
            check_entry["reason"] = None

        # Persist check
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(CHECKS_FILE, "a") as f:
            f.write(json.dumps(check_entry, default=str) + "\n")

    # Execute triggered stops
    if triggered:
        prices_for_exec = {sym: prices.get(sym, 0) for sym, _ in triggered}
        for symbol, signal in triggered:
            try:
                result = execute_signal(signal, prices_for_exec)
                log.info("[SAFETY] %s SELL result: %s", symbol, result.get("status", "?"))
            except Exception as e:
                log.error("[SAFETY] Failed to execute SELL for %s: %s", symbol, e)

        # Telegram alert for stops
        try:
            from alert_manager import send_telegram_text
            lines = ["🛑 *Safety Engine — Stop Triggered*\n"]
            for symbol, signal in triggered:
                lines.append(f"🔴 *{symbol}* → SELL")
                lines.append(f"  {signal['reasoning']}")
            send_telegram_text("\n".join(lines))
        except Exception:
            pass

    # ── Circuit breaker: extreme price moves ──
    # Check if any position moved >10% since last check (flash crash protection)
    extreme_moves = []
    prev_checks = _load_previous_checks()
    for symbol, pos in positions.items():
        current_price = prices.get(symbol)
        if not current_price:
            continue
        prev_price = prev_checks.get(symbol)
        if prev_price and prev_price > 0:
            pct_change = (current_price - prev_price) / prev_price
            if abs(pct_change) > 0.10:  # >10% move
                direction = "UP" if pct_change > 0 else "DOWN"
                extreme_moves.append((symbol, current_price, prev_price, pct_change, direction))
                log.warning("[SAFETY] EXTREME MOVE: %s %s %.1f%% (%.4f→%.4f)",
                           symbol, direction, pct_change * 100, prev_price, current_price)

    if extreme_moves:
        try:
            from alert_manager import send_telegram_text
            lines = ["⚠️ *Extreme Price Move Detected*\n"]
            for sym, cur, prev, pct, direction in extreme_moves:
                lines.append(f"🔴 *{sym}*: {direction} {pct*100:.1f}%")
                lines.append(f"  {prev:.4f} → {cur:.4f}")
                lines.append(f"  Stop: ${positions[sym].get('stop_loss', 'N/A')}")
            send_telegram_text("\n".join(lines))
        except Exception:
            pass

    triggered_count = len(triggered)
    if triggered_count:
        log.warning("[SAFETY] %d stops triggered", triggered_count)
    else:
        log.info("[SAFETY] All %d positions OK", len(positions))


if __name__ == "__main__":
    run_safety_check()
