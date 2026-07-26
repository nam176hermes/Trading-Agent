"""
safety_engine.py — Stop-loss and take-profit enforcement for paper portfolio.

Runs every 5 minutes via cron. Checks all open positions against
their stop-loss and take-profit levels. Triggers paper SELL when hit.

Usage: python3 safety_engine.py
"""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from paper_trader import (
    PaperPortfolioError,
    PortfolioStateError,
    PriceSnapshot,
    _load_report_prices,
    execute_signal,
    load_portfolio,
)
from runtime_paths import data_root, reports_dir as runtime_reports_dir

log = logging.getLogger("safety_engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

MEMORY_DIR = data_root() / "memory"
CHECKS_FILE = MEMORY_DIR / "stop_checks.jsonl"


def get_current_prices() -> PriceSnapshot:
    """Load prices through the paper trader's typed report boundary."""
    return _load_report_prices()


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




def check_circuit_breaker(
    panic_threshold_pct: float = -15.0,
    single_asset_threshold_pct: float = -30.0,
) -> dict:
    """Evaluate market movement with explicit data-availability status."""
    trace_id = uuid4().hex[:16]
    reports_dir = runtime_reports_dir()
    try:
        files = sorted(
            [
                path
                for path in reports_dir.iterdir()
                if path.name.startswith("report_") and path.suffix == ".json"
            ],
            reverse=True,
        )
        if not files:
            return {
                "status": "UNAVAILABLE",
                "reason_code": "PRICE_REPORT_MISSING",
                "trace_id": trace_id,
                "triggered": False,
                "detail": "No report files found",
            }
        report = json.loads(files[0].read_text())
        assets = report.get("assets", [])
        if not isinstance(assets, list) or not assets:
            return {
                "status": "UNAVAILABLE",
                "reason_code": "PRICE_REPORT_HAS_NO_ASSETS",
                "trace_id": trace_id,
                "triggered": False,
                "detail": "Report contains no asset data",
            }
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        log.error(
            "event=circuit_breaker_unavailable trace_id=%s "
            "reason_code=PRICE_REPORT_INVALID error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return {
            "status": "UNAVAILABLE",
            "reason_code": "PRICE_REPORT_INVALID",
            "trace_id": trace_id,
            "triggered": False,
            "detail": "Price report unavailable",
        }

    changes = []
    extreme_asset = None
    extreme_change = 0.0
    try:
        for asset in assets:
            symbol = asset.get("symbol", "???")
            change_pct = asset.get("price_change_24h_pct")
            if change_pct is not None:
                change_pct = float(change_pct)
                changes.append(change_pct)
                if change_pct < extreme_change:
                    extreme_change = change_pct
                    extreme_asset = symbol
    except (AttributeError, TypeError, ValueError) as exc:
        log.error(
            "event=circuit_breaker_unavailable trace_id=%s "
            "reason_code=PRICE_CHANGE_DATA_INVALID error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return {
            "status": "UNAVAILABLE",
            "reason_code": "PRICE_CHANGE_DATA_INVALID",
            "trace_id": trace_id,
            "triggered": False,
            "detail": "Price-change data invalid",
        }

    if not changes:
        return {
            "status": "UNAVAILABLE",
            "reason_code": "PRICE_CHANGE_DATA_MISSING",
            "trace_id": trace_id,
            "triggered": False,
            "detail": "No 24h change data in report",
        }

    avg_change = sum(changes) / len(changes)
    if extreme_change <= single_asset_threshold_pct:
        return {
            "status": "COMPLETED",
            "reason_code": None,
            "trace_id": trace_id,
            "triggered": True,
            "detail": (
                f"Single-asset circuit breaker: {extreme_asset} "
                f"{extreme_change:+.1f}% 24h (threshold "
                f"{single_asset_threshold_pct:+.0f}%). Portfolio average: "
                f"{avg_change:+.1f}% across {len(changes)} assets."
            ),
        }
    if avg_change <= panic_threshold_pct:
        return {
            "status": "COMPLETED",
            "reason_code": None,
            "trace_id": trace_id,
            "triggered": True,
            "detail": (
                f"Panic circuit breaker: average {avg_change:+.1f}% 24h "
                f"across {len(changes)} assets (threshold "
                f"{panic_threshold_pct:+.0f}%). Worst: {extreme_asset} "
                f"{extreme_change:+.1f}%."
            ),
        }
    return {
        "status": "COMPLETED",
        "reason_code": None,
        "trace_id": trace_id,
        "triggered": False,
        "detail": (
            f"Circuit OK: avg {avg_change:+.1f}% across {len(changes)} assets. "
            f"Worst: {extreme_asset} {extreme_change:+.1f}%."
        ),
    }


class PreviousChecksSnapshot(dict):
    """Mapping-compatible history state for extreme-move checks."""

    def __init__(
        self,
        values: dict[str, float],
        *,
        status: str,
        reason_code: str | None,
        trace_id: str,
    ) -> None:
        super().__init__(values)
        self.status = status
        self.reason_code = reason_code
        self.trace_id = trace_id


def _load_previous_checks() -> PreviousChecksSnapshot:
    """Load prior prices and expose incomplete or invalid history."""
    trace_id = uuid4().hex[:16]
    if not CHECKS_FILE.exists():
        return PreviousChecksSnapshot(
            {},
            status="PARTIAL",
            reason_code="PREVIOUS_CHECK_HISTORY_MISSING",
            trace_id=trace_id,
        )

    previous: dict[str, float] = {}
    invalid_lines = 0
    try:
        lines = CHECKS_FILE.read_text().splitlines()
        for line in reversed(lines[-100:]):
            try:
                entry = json.loads(line)
                symbol = str(entry.get("symbol", "")).upper()
                current_price = float(entry["current_price"])
                if symbol and symbol not in previous and current_price > 0:
                    previous[symbol] = current_price
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                invalid_lines += 1
    except (OSError, UnicodeError) as exc:
        log.error(
            "event=previous_checks_unavailable trace_id=%s "
            "reason_code=PREVIOUS_CHECK_HISTORY_UNAVAILABLE error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return PreviousChecksSnapshot(
            {},
            status="UNAVAILABLE",
            reason_code="PREVIOUS_CHECK_HISTORY_UNAVAILABLE",
            trace_id=trace_id,
        )

    if invalid_lines:
        log.warning(
            "event=previous_checks_partial trace_id=%s "
            "reason_code=PREVIOUS_CHECK_HISTORY_INVALID invalid_lines=%d",
            trace_id,
            invalid_lines,
        )
        return PreviousChecksSnapshot(
            previous,
            status="PARTIAL",
            reason_code="PREVIOUS_CHECK_HISTORY_INVALID",
            trace_id=trace_id,
        )
    return PreviousChecksSnapshot(
        previous,
        status="AVAILABLE",
        reason_code=None,
        trace_id=trace_id,
    )


def _safety_result(
    *,
    status: str,
    reason_code: str | None,
    trace_id: str,
    checked_symbols: list[str] | None = None,
    missing_price_symbols: list[str] | None = None,
    triggered_symbols: list[str] | None = None,
    executed_symbols: list[str] | None = None,
    execution_failures: list[dict] | None = None,
    previous_checks_status: str = "NOT_RUN",
    previous_checks_reason_code: str | None = None,
) -> dict:
    """Build the stable public result schema for every safety-check exit."""
    triggered = sorted(triggered_symbols or [])
    return {
        "status": status,
        "reason_code": reason_code,
        "trace_id": trace_id,
        "checked_symbols": sorted(checked_symbols or []),
        "missing_price_symbols": sorted(missing_price_symbols or []),
        "triggered_symbols": triggered,
        "executed_symbols": sorted(executed_symbols or []),
        "execution_failures": execution_failures or [],
        "previous_checks_status": previous_checks_status,
        "previous_checks_reason_code": previous_checks_reason_code,
        "safe": status == "COMPLETED" and not triggered,
    }


def run_safety_check() -> dict:
    """Check every paper position and return explicit coverage status."""
    trace_id = uuid4().hex[:16]
    try:
        portfolio = load_portfolio()
    except PortfolioStateError as exc:
        return _safety_result(
            status="UNAVAILABLE",
            reason_code=exc.reason_code,
            trace_id=exc.trace_id,
        )

    positions = portfolio.get("positions", {})
    if not positions:
        return _safety_result(
            status="COMPLETED",
            reason_code=None,
            trace_id=trace_id,
        )

    prices = get_current_prices()
    if prices.status != "AVAILABLE":
        log.error(
            "event=safety_check_unavailable trace_id=%s reason_code=%s",
            prices.trace_id,
            prices.reason_code,
        )
        return _safety_result(
            status="UNAVAILABLE",
            reason_code=prices.reason_code,
            trace_id=prices.trace_id,
            missing_price_symbols=list(positions),
        )

    previous_checks = _load_previous_checks()
    checked_symbols: list[str] = []
    missing_price_symbols: list[str] = []
    triggered: list[tuple[str, dict]] = []
    check_entries: list[dict] = []

    for symbol, position in list(positions.items()):
        current_price = prices.get(symbol)
        if not current_price:
            missing_price_symbols.append(symbol)
            log.error(
                "event=safety_position_unchecked trace_id=%s "
                "reason_code=POSITION_PRICE_MISSING symbol=%s",
                trace_id,
                symbol,
            )
            continue

        checked_symbols.append(symbol)
        check_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "current_price": current_price,
            "stop_loss": position.get("stop_loss"),
            "target": position.get("take_profit"),
            "shares": position.get("shares", 0),
            "avg_cost": position.get("avg_cost", 0),
        }
        signal = check_position(symbol, position, current_price)
        if signal:
            check_entry["action"] = "SELL"
            check_entry["reason"] = signal["reasoning"]
            triggered.append((symbol, signal))
            log.warning(
                "[SAFETY] %s %s; executing paper SELL",
                symbol,
                signal["reasoning"],
            )
        else:
            check_entry["action"] = "HOLD"
            check_entry["reason"] = None
        check_entries.append(check_entry)

    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        for check_entry in check_entries:
            with CHECKS_FILE.open("a") as handle:
                handle.write(json.dumps(check_entry, default=str) + "\n")
    except (OSError, UnicodeError, TypeError) as exc:
        log.error(
            "event=safety_check_unavailable trace_id=%s "
            "reason_code=SAFETY_CHECK_LOG_WRITE_FAILED error_type=%s "
            "checked_count=%d missing_count=%d triggered_count=%d "
            "previous_checks_status=%s",
            trace_id,
            type(exc).__name__,
            len(checked_symbols),
            len(missing_price_symbols),
            len(triggered),
            previous_checks.status,
        )
        return _safety_result(
            status="UNAVAILABLE",
            reason_code="SAFETY_CHECK_LOG_WRITE_FAILED",
            trace_id=trace_id,
            checked_symbols=checked_symbols,
            missing_price_symbols=missing_price_symbols,
            triggered_symbols=[symbol for symbol, _signal in triggered],
            previous_checks_status=previous_checks.status,
            previous_checks_reason_code=previous_checks.reason_code,
        )

    executed_symbols: list[str] = []
    execution_failures: list[dict] = []
    prices_for_execution = {
        symbol: prices[symbol]
        for symbol, _signal in triggered
        if symbol in prices
    }
    for symbol, signal in triggered:
        try:
            execution = execute_signal(signal, prices_for_execution)
        except (PaperPortfolioError, OSError, ValueError, TypeError, KeyError) as exc:
            execution_failures.append(
                {
                    "symbol": symbol,
                    "reason_code": getattr(
                        exc,
                        "reason_code",
                        "PAPER_STOP_EXECUTION_FAILED",
                    ),
                    "error_type": type(exc).__name__,
                }
            )
            continue
        if execution.get("status") == "filled":
            executed_symbols.append(symbol)
            audit_status = execution.get("audit_status")
            if audit_status != "COMPLETED":
                audit_reason = (
                    execution.get("audit_reason_code")
                    if audit_status == "PARTIAL"
                    else "PAPER_STOP_AUDIT_STATUS_INVALID"
                )
                execution_failures.append(
                    {
                        "symbol": symbol,
                        "reason_code": audit_reason
                        or "PAPER_STOP_AUDIT_INCOMPLETE",
                        "status": "filled",
                        "audit_status": audit_status,
                        "trace_id": execution.get("trace_id") or trace_id,
                    }
                )
        else:
            execution_failures.append(
                {
                    "symbol": symbol,
                    "reason_code": execution.get(
                        "reason_code",
                        "PAPER_STOP_EXECUTION_NOT_FILLED",
                    ),
                    "status": execution.get("status"),
                }
            )

    if triggered:
        try:
            from alert_manager import send_telegram_text

            lines = ["Safety Engine: stop triggered"]
            for symbol, signal in triggered:
                lines.append(f"{symbol}: SELL, {signal['reasoning']}")
            send_telegram_text("\n".join(lines))
        except Exception as exc:
            log.warning(
                "event=safety_alert_delivery_failed trace_id=%s error_type=%s",
                trace_id,
                type(exc).__name__,
            )

    extreme_moves = []
    for symbol in checked_symbols:
        current_price = prices[symbol]
        previous_price = previous_checks.get(symbol)
        if previous_price and previous_price > 0:
            pct_change = (current_price - previous_price) / previous_price
            if abs(pct_change) > 0.10:
                direction = "UP" if pct_change > 0 else "DOWN"
                extreme_moves.append(
                    (symbol, current_price, previous_price, pct_change, direction)
                )
                log.warning(
                    "[SAFETY] EXTREME MOVE: %s %s %.1f%% (%.4f to %.4f)",
                    symbol,
                    direction,
                    pct_change * 100,
                    previous_price,
                    current_price,
                )

    if extreme_moves:
        try:
            from alert_manager import send_telegram_text

            lines = ["Extreme price move detected"]
            for symbol, current, previous, pct, direction in extreme_moves:
                lines.append(
                    f"{symbol}: {direction} {pct * 100:.1f}%, "
                    f"{previous:.4f} to {current:.4f}"
                )
            send_telegram_text("\n".join(lines))
        except Exception as exc:
            log.warning(
                "event=safety_alert_delivery_failed trace_id=%s error_type=%s",
                trace_id,
                type(exc).__name__,
            )

    reason_code = None
    status = "COMPLETED"
    if execution_failures:
        status = "PARTIAL" if executed_symbols else "UNAVAILABLE"
        audit_only_failures = all(
            failure.get("status") == "filled"
            and "audit_status" in failure
            for failure in execution_failures
        )
        reason_code = (
            "PAPER_STOP_AUDIT_INCOMPLETE"
            if audit_only_failures
            else "PAPER_STOP_EXECUTION_INCOMPLETE"
        )
    elif missing_price_symbols:
        status = "PARTIAL" if checked_symbols else "UNAVAILABLE"
        reason_code = "POSITION_PRICE_MISSING"
    elif previous_checks.status != "AVAILABLE":
        status = "PARTIAL"
        reason_code = previous_checks.reason_code

    if status == "COMPLETED":
        log.info("[SAFETY] Checked all %d positions", len(positions))
    else:
        log.error(
            "event=safety_check_incomplete trace_id=%s status=%s "
            "reason_code=%s missing_symbols=%s execution_failures=%d",
            trace_id,
            status,
            reason_code,
            ",".join(sorted(missing_price_symbols)[:16]),
            len(execution_failures),
        )

    return _safety_result(
        status=status,
        reason_code=reason_code,
        trace_id=trace_id,
        checked_symbols=checked_symbols,
        missing_price_symbols=missing_price_symbols,
        triggered_symbols=[symbol for symbol, _signal in triggered],
        executed_symbols=executed_symbols,
        execution_failures=execution_failures,
        previous_checks_status=previous_checks.status,
        previous_checks_reason_code=previous_checks.reason_code,
    )


if __name__ == "__main__":
    result = run_safety_check()
    if result["status"] != "COMPLETED":
        raise SystemExit(1)
