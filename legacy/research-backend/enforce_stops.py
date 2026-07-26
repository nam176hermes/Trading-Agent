"""
enforce_stops.py — Stop-Loss / Take-Profit enforcement for paper trading.

Reads latest report + open positions. For each open position:
- If current price <= stop_loss_suggestion: SELL at market
- If current price >= target_suggestion: SELL at market
Logs all checks to memory/stop_checks.jsonl.
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from paper_trader import PortfolioStateError, execute_batch, load_portfolio
from alert_manager import send_telegram_text
from runtime_paths import data_root, reports_dir

log = logging.getLogger("enforce_stops")
REPORTS_DIR = reports_dir()
MEMORY_DIR = data_root() / "memory"
STOP_CHECKS_FILE = MEMORY_DIR / "stop_checks.jsonl"


def _result(
    *,
    status: str,
    reason_code: str | None,
    trace_id: str,
    checked_symbols: list[str] | None = None,
    missing_price_symbols: list[str] | None = None,
    executed_symbols: list[str] | None = None,
    batch_status: str = "NOT_RUN",
    batch_reason_code: str | None = None,
    check_log_status: str = "NOT_RUN",
    check_log_reason_code: str | None = None,
) -> dict:
    """Build the stable public status schema for every enforcement exit."""
    return {
        "status": status,
        "reason_code": reason_code,
        "trace_id": trace_id,
        "checked_symbols": sorted(checked_symbols or []),
        "missing_price_symbols": sorted(set(missing_price_symbols or [])),
        "executed_symbols": sorted(executed_symbols or []),
        "batch_status": batch_status,
        "batch_reason_code": batch_reason_code,
        "check_log_status": check_log_status,
        "check_log_reason_code": check_log_reason_code,
    }


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


def main() -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    trace_id = uuid4().hex[:16]

    from kill_switch import is_kill_switch_active
    if is_kill_switch_active():
        log.warning("Kill switch active — enforce_stops aborted")
        return _result(
            status="SKIPPED",
            reason_code="KILL_SWITCH_ACTIVE",
            trace_id=trace_id,
        )

    try:
        pf = load_portfolio()
    except PortfolioStateError as exc:
        log.error(
            "event=stop_enforcement_unavailable trace_id=%s "
            "reason_code=%s error_type=%s",
            exc.trace_id,
            exc.reason_code,
            type(exc).__name__,
        )
        return _result(
            status="UNAVAILABLE",
            reason_code=exc.reason_code,
            trace_id=exc.trace_id,
        )
    positions = pf.get("positions", {})
    if not positions:
        log.info("No open positions — nothing to enforce")
        return _result(status="COMPLETED", reason_code=None, trace_id=trace_id)

    try:
        report = latest_report()
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        log.error(
            "event=stop_enforcement_unavailable trace_id=%s "
            "reason_code=PRICE_REPORT_INVALID error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _result(
            status="UNAVAILABLE",
            reason_code="PRICE_REPORT_INVALID",
            trace_id=trace_id,
            missing_price_symbols=list(positions),
        )
    if not report:
        log.error(
            "event=stop_enforcement_unavailable trace_id=%s "
            "reason_code=PRICE_REPORT_MISSING",
            trace_id,
        )
        return _result(
            status="UNAVAILABLE",
            reason_code="PRICE_REPORT_MISSING",
            trace_id=trace_id,
            missing_price_symbols=list(positions),
        )

    # Build price map from report assets
    price_map: dict[str, float] = {}
    target_map: dict[str, float] = {}
    stop_map: dict[str, float] = {}

    try:
        if not isinstance(report, dict):
            raise TypeError("price report must be an object")
        assets = report.get("assets")
        if not isinstance(assets, list):
            raise TypeError("price report assets must be a list")
        for asset in assets:
            if not isinstance(asset, dict):
                raise TypeError("price report asset must be an object")
            symbol_value = asset.get("symbol")
            if not isinstance(symbol_value, str) or not symbol_value.strip():
                raise TypeError("price report symbol must be a non-empty string")
            sym = symbol_value.upper()
            price = float(asset.get("current_price"))
            if not math.isfinite(price) or price <= 0:
                raise ValueError("current price must be finite and positive")
            price_map[sym] = price
            for field, destination in (
                ("target_suggestion", target_map),
                ("stop_loss_suggestion", stop_map),
            ):
                value = asset.get(field)
                if value is None:
                    continue
                threshold = float(value)
                if not math.isfinite(threshold) or threshold <= 0:
                    raise ValueError(f"{field} must be finite and positive")
                destination[sym] = threshold
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        log.error(
            "event=stop_enforcement_unavailable trace_id=%s "
            "reason_code=PRICE_REPORT_INVALID error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _result(
            status="UNAVAILABLE",
            reason_code="PRICE_REPORT_INVALID",
            trace_id=trace_id,
            missing_price_symbols=list(positions),
        )

    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error(
            "event=stop_enforcement_unavailable trace_id=%s "
            "reason_code=STOP_MEMORY_DIR_UNAVAILABLE error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _result(
            status="UNAVAILABLE",
            reason_code="STOP_MEMORY_DIR_UNAVAILABLE",
            trace_id=trace_id,
        )

    # First pass: collect all triggers
    signals = []
    checks = []

    # Trailing stop — activate at +10% profit, trail 5% below current price
    trailing_file = MEMORY_DIR / "trailing_stops.json"
    trailing_stops = {}
    if trailing_file.exists():
        try:
            trailing_stops = json.loads(trailing_file.read_text())
            if not isinstance(trailing_stops, dict):
                raise TypeError("trailing stop state must be an object")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            log.error(
                "event=stop_enforcement_unavailable trace_id=%s "
                "reason_code=TRAILING_STOP_STATE_INVALID error_type=%s",
                trace_id,
                type(exc).__name__,
            )
            return _result(
                status="UNAVAILABLE",
                reason_code="TRAILING_STOP_STATE_INVALID",
                trace_id=trace_id,
            )

    missing_price_symbols = []
    for sym, pos in positions.items():
        current_price = price_map.get(sym)
        entry_price = pos.get("avg_cost", 0)
        if not current_price:
            missing_price_symbols.append(sym)
            continue
        if not entry_price:
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

    try:
        trailing_file.write_text(json.dumps(trailing_stops, indent=2))
    except (OSError, UnicodeError) as exc:
        log.error(
            "event=stop_enforcement_unavailable trace_id=%s "
            "reason_code=TRAILING_STOP_STATE_WRITE_FAILED error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _result(
            status="UNAVAILABLE",
            reason_code="TRAILING_STOP_STATE_WRITE_FAILED",
            trace_id=trace_id,
            checked_symbols=list(set(positions) - set(missing_price_symbols)),
            missing_price_symbols=missing_price_symbols,
        )

    for sym, pos in positions.items():
        current_price = price_map.get(sym)
        if not current_price:
            log.error(
                "event=stop_position_unchecked trace_id=%s "
                "reason_code=POSITION_PRICE_MISSING symbol=%s",
                trace_id,
                sym,
            )
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
    batch_result = {
        "status": "COMPLETED",
        "reason_code": None,
        "trace_id": trace_id,
        "executed": [],
        "unavailable": [],
    }
    if signals:
        try:
            batch_result = execute_batch(signals, price_map)
        except Exception as exc:
            log.error(
                "event=stop_enforcement_unavailable trace_id=%s "
                "reason_code=PAPER_BATCH_EXECUTION_FAILED error_type=%s",
                trace_id,
                type(exc).__name__,
            )
            return _result(
                status="UNAVAILABLE",
                reason_code="PAPER_BATCH_EXECUTION_FAILED",
                trace_id=trace_id,
                checked_symbols=list(set(positions) - set(missing_price_symbols)),
                missing_price_symbols=missing_price_symbols,
                batch_status="UNAVAILABLE",
                batch_reason_code="PAPER_BATCH_EXECUTION_FAILED",
            )
        try:
            if not isinstance(batch_result, dict):
                raise TypeError("paper batch result must be an object")
            batch_status = batch_result.get("status")
            if batch_status not in {"COMPLETED", "PARTIAL", "UNAVAILABLE"}:
                raise ValueError("paper batch result status is invalid")
            executed_entries = batch_result.get("executed")
            unavailable_entries = batch_result.get("unavailable")
            if not isinstance(executed_entries, list) or not isinstance(
                unavailable_entries, list
            ):
                raise TypeError("paper batch result lists are invalid")
            executed = []
            for entry in executed_entries:
                if not isinstance(entry, dict):
                    raise TypeError("paper batch executed entry must be an object")
                symbol = entry.get("symbol")
                result = entry.get("result")
                if not isinstance(symbol, str) or not symbol:
                    raise TypeError("paper batch executed symbol is invalid")
                if not isinstance(result, dict):
                    raise TypeError("paper batch executed result is invalid")
                executed.append(symbol)
        except (TypeError, ValueError) as exc:
            log.error(
                "event=stop_enforcement_unavailable trace_id=%s "
                "reason_code=PAPER_BATCH_RESULT_INVALID error_type=%s",
                trace_id,
                type(exc).__name__,
            )
            return _result(
                status="UNAVAILABLE",
                reason_code="PAPER_BATCH_RESULT_INVALID",
                trace_id=trace_id,
                checked_symbols=list(set(positions) - set(missing_price_symbols)),
                missing_price_symbols=missing_price_symbols,
                batch_status="UNAVAILABLE",
                batch_reason_code="PAPER_BATCH_RESULT_INVALID",
            )

        # Update check entries with results
        for c in checks:
            if c["action"] == "SELL":
                c["result"] = (
                    "filled" if c["symbol"] in executed else "unavailable"
                )
    else:
        executed = []

    check_log_status = "COMPLETED"
    check_log_reason_code = None
    try:
        for c in checks:
            with open(STOP_CHECKS_FILE, "a") as f:
                f.write(json.dumps(c, default=str) + "\n")
    except (OSError, UnicodeError) as exc:
        check_log_status = "UNAVAILABLE"
        check_log_reason_code = "STOP_CHECK_LOG_WRITE_FAILED"
        log.error(
            "event=stop_check_log_unavailable trace_id=%s "
            "reason_code=%s error_type=%s executed_count=%d",
            trace_id,
            check_log_reason_code,
            type(exc).__name__,
            len(executed),
        )

    if executed:
        log.info("Executed %d stop/target sell(s): %s", len(executed), executed)
        for e in batch_result["executed"]:
            sym = e["symbol"]
            r = e["result"]
            try:
                pnl = r.get("pnl", 0)
                send_telegram_text(
                    f"🛑 *Stop/Target Hit*\n{sym} — SELL {r.get('shares', 0):.4f} @ ${r.get('fill_price', 0):,.2f}\n"
                    f"PnL: ${pnl:,.2f} | Cash: ${r.get('portfolio_cash_after', 0):,.0f}"
                )
            except Exception as exc:
                log.warning(
                    "event=stop_alert_delivery_failed trace_id=%s "
                    "symbol=%s error_type=%s",
                    trace_id,
                    sym,
                    type(exc).__name__,
                )
    elif not signals and not missing_price_symbols:
        log.info("No stop-loss or take-profit triggers fired")

    checked_symbols = sorted(set(positions) - set(missing_price_symbols))
    status = "COMPLETED"
    reason_code = None
    if batch_result["status"] != "COMPLETED":
        status = batch_result["status"]
        reason_code = batch_result.get("reason_code") or "PAPER_BATCH_INCOMPLETE"
    if missing_price_symbols:
        if status == "COMPLETED":
            status = "PARTIAL" if checked_symbols else "UNAVAILABLE"
            reason_code = "POSITION_PRICE_MISSING"
        elif status == "PARTIAL":
            reason_code = "STOP_ENFORCEMENT_MULTIPLE_FAILURES"
    if check_log_status != "COMPLETED" and status == "COMPLETED":
        status = "PARTIAL"
        reason_code = check_log_reason_code

    if status != "COMPLETED":
        log.error(
            "event=stop_enforcement_incomplete trace_id=%s status=%s "
            "reason_code=%s missing_symbols=%s",
            trace_id,
            status,
            reason_code,
            ",".join(sorted(set(missing_price_symbols))[:16]),
        )
    return _result(
        status=status,
        reason_code=reason_code,
        trace_id=trace_id,
        checked_symbols=checked_symbols,
        missing_price_symbols=missing_price_symbols,
        executed_symbols=executed,
        batch_status=batch_result["status"],
        batch_reason_code=batch_result.get("reason_code"),
        check_log_status=check_log_status,
        check_log_reason_code=check_log_reason_code,
    )


if __name__ == "__main__":
    result = main()
    if result["status"] not in {"COMPLETED", "SKIPPED"}:
        raise SystemExit(1)
