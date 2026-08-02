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
import math
from numbers import Real
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, cast
from uuid import uuid4
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

_MAX_SYMBOL_LENGTH = 32
_MAX_ORDER_ID_LENGTH = 128
_MAX_RETURN_SAMPLES = 3660
_MAX_RETURN_MAGNITUDE = 10.0
_MAX_CVAR_NOTIONAL = 1_000_000_000.0
_MAX_CVAR_QUANTITY = 1_000_000_000_000.0
_MIN_NORMAL_FLOAT = float.fromhex("0x1.0000000000000p-1022")


def new_trace_id() -> str:
    """Return a non-sensitive identifier for correlating outcomes and logs."""
    return uuid4().hex[:16]


class ExecutionBoundaryError(RuntimeError):
    """Base typed failure crossing the legacy execution module boundary."""

    default_status = "UNAVAILABLE"
    default_reason_code = "EXECUTION_BOUNDARY_FAILED"

    def __init__(
        self,
        *,
        operation: str,
        symbol: str | None = None,
        cause: BaseException | None = None,
        status: str | None = None,
        reason_code: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.status = status or self.default_status
        self.reason_code = reason_code or self.default_reason_code
        self.trace_id = trace_id or new_trace_id()
        self.operation = operation
        self.symbol = symbol
        self.error_type = type(cause).__name__ if cause is not None else None
        super().__init__(f"{self.reason_code} trace_id={self.trace_id}")

    def to_dict(self) -> dict:
        """Serialize the stable public envelope without uncontrolled details."""
        result = {
            "status": self.status,
            "reason_code": self.reason_code,
            "trace_id": self.trace_id,
            "operation": self.operation,
        }
        if self.symbol is not None:
            result["symbol"] = self.symbol
        if self.error_type is not None:
            result["error_type"] = self.error_type
        return result


class ExecutionDependencyUnavailable(ExecutionBoundaryError):
    default_reason_code = "EXECUTION_DEPENDENCY_UNAVAILABLE"


class ExecutionSubmissionFailed(ExecutionBoundaryError):
    default_reason_code = "ORDER_SUBMISSION_FAILED"


class ExecutionStatePersistenceFailed(ExecutionBoundaryError):
    default_status = "PARTIAL"
    default_reason_code = "EXECUTION_STATE_PERSISTENCE_FAILED"


class ExecutionObservabilityFailed(ExecutionBoundaryError):
    default_status = "PARTIAL"
    default_reason_code = "EXECUTION_OBSERVABILITY_FAILED"


class ExecutionInputInvalid(ExecutionBoundaryError):
    """Typed rejection for malformed dryrun/live execution input."""

    default_status = "REJECTED"
    default_reason_code = "EXECUTION_INPUT_INVALID"


def _log_typed_failure(failure: ExecutionBoundaryError) -> None:
    log.error(
        "event=execution_boundary_failure trace_id=%s status=%s reason_code=%s "
        "operation=%s symbol=%s error_type=%s",
        failure.trace_id,
        failure.status,
        failure.reason_code,
        failure.operation,
        failure.symbol or "none",
        failure.error_type or "none",
    )


def _call_execution_dependency(
    operation: str, symbol: str, dependency: Callable, *args, **kwargs
):
    """Call one uncontrolled read dependency and immediately type failures."""
    try:
        return dependency(*args, **kwargs)
    except Exception as exc:
        failure = ExecutionDependencyUnavailable(operation=operation, symbol=symbol, cause=exc)
        _log_typed_failure(failure)
        raise failure from exc


def _call_order_submission(
    operation: str, symbol: str, submission: Callable, *args, **kwargs
):
    """Call one uncontrolled order boundary and immediately type failures."""
    try:
        return submission(*args, **kwargs)
    except Exception as exc:
        failure = ExecutionSubmissionFailed(operation=operation, symbol=symbol, cause=exc)
        _log_typed_failure(failure)
        raise failure from exc


def _call_observability_boundary(
    operation: str, bounded_symbol: str, observer: Callable, *args, **kwargs
) -> ExecutionObservabilityFailed | None:
    """Return a typed warning when optional telemetry cannot be recorded."""
    try:
        observed = observer(*args, **kwargs)
    except Exception as exc:
        failure = ExecutionObservabilityFailed(
            operation=operation, symbol=bounded_symbol, cause=exc
        )
        _log_typed_failure(failure)
        return failure
    if observed is False:
        failure = ExecutionObservabilityFailed(operation=operation, symbol=bounded_symbol)
        _log_typed_failure(failure)
        return failure
    return None


def _invalid_boundary_result(
    *, operation: str, symbol: str, reason_code: str
) -> ExecutionBoundaryError:
    failure = ExecutionSubmissionFailed(
        operation=operation,
        symbol=symbol,
        cause=TypeError(),
        reason_code=reason_code,
    )
    _log_typed_failure(failure)
    return failure


def _is_finite_real(value, *, minimum=None, maximum=None, positive=False) -> bool:
    """Accept only finite non-bool real scalars in an optional safe range."""
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    if not math.isfinite(normalized):
        return False
    if normalized != 0.0 and abs(normalized) < _MIN_NORMAL_FLOAT:
        return False
    if positive and normalized <= 0:
        return False
    if minimum is not None and normalized < minimum:
        return False
    return maximum is None or normalized <= maximum


def _bounded_safe_text(value, *, maximum: int, allow_empty: bool = False) -> str | None:
    """Return a bounded printable string without coercing uncontrolled objects."""
    if not isinstance(value, str) or len(value) > maximum:
        return None
    if not allow_empty and not value.strip():
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


def _validated_symbol(value) -> str | None:
    symbol = _bounded_safe_text(value, maximum=_MAX_SYMBOL_LENGTH)
    if symbol is None:
        return None
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
    return symbol if all(character in allowed for character in symbol) else None


def _normalized_order_id(value) -> str | None:
    """Normalize a CCXT-style scalar identifier without exposing malformed values."""
    if isinstance(value, str):
        return _bounded_safe_text(value, maximum=_MAX_ORDER_ID_LENGTH)
    if not _is_finite_real(value) or not float(value).is_integer():
        return None
    normalized = int(value)
    if normalized < 0 or normalized > 10**30:
        return None
    return str(normalized)


def _invalid_execution_input(*, symbol: str | None = None) -> ExecutionInputInvalid:
    failure = ExecutionInputInvalid(operation="validate_execution_input", symbol=symbol, cause=TypeError())
    _log_typed_failure(failure)
    return failure


def _validated_execution_input(asset, *, single_signal: bool = False) -> dict:
    """Normalize the bounded dryrun/live input contract before any sizing call."""
    if not isinstance(asset, dict):
        raise _invalid_execution_input()
    symbol_value = asset.get("asset", asset.get("symbol")) if single_signal else asset.get("symbol")
    symbol = _validated_symbol(symbol_value)
    if symbol is None:
        raise _invalid_execution_input()
    action_value = asset.get("action") if single_signal else asset.get("suggestion")
    if not isinstance(action_value, str) or action_value not in {"BUY", "SELL"}:
        raise _invalid_execution_input(symbol=symbol)
    price = asset.get("price", asset.get("current_price")) if not single_signal else None
    if price is not None and not _is_finite_real(price, positive=True):
        raise _invalid_execution_input(symbol=symbol)
    confidence = asset.get("confidence", 0.5)
    modifier = asset.get("position_modifier", asset.get("_gate_modifier", 1.0))
    metadata_field = "reasoning" if single_signal else "rationale"
    reasoning = _bounded_safe_text(
        asset.get(metadata_field, ""), maximum=300, allow_empty=True
    )
    if not _is_finite_real(confidence, minimum=0, maximum=1) or not _is_finite_real(
        modifier, minimum=0, maximum=1
    ) or reasoning is None:
        raise _invalid_execution_input(symbol=symbol)
    return {
        "symbol": symbol,
        "action": action_value,
        "price": float(price) if price is not None else None,
        "confidence": float(confidence),
        "position_modifier": float(modifier),
        "reasoning": reasoning,
    }


def _validated_order_result(order_result, *, symbol: str) -> dict:
    """Require provider evidence before any result can be treated as a fill."""
    if not isinstance(order_result, dict):
        raise _invalid_boundary_result(
            operation="place_order", symbol=symbol, reason_code="ORDER_RESULT_INVALID"
        )

    status = order_result.get("status")
    if status == "blocked":
        return order_result
    order_id = _normalized_order_id(order_result.get("id"))
    if (
        status not in {"closed", "filled"}
        or order_id is None
        or not _is_finite_real(
            order_result.get("filled"),
            positive=True,
            maximum=_MAX_CVAR_QUANTITY,
        )
        or not _is_finite_real(
            order_result.get("avg_fill_price"),
            positive=True,
            maximum=_MAX_CVAR_NOTIONAL,
        )
    ):
        raise _invalid_boundary_result(
            operation="place_order", symbol=symbol, reason_code="ORDER_RESULT_INVALID"
        )
    # Only the normalized scalar is eligible for public output or persistence.
    return {**order_result, "id": order_id}


def _invalid_dependency_result(
    *, operation: str, symbol: str
) -> ExecutionDependencyUnavailable:
    """Return the safe envelope for a malformed read-dependency result."""
    failure = ExecutionDependencyUnavailable(
        operation=operation,
        symbol=symbol,
        cause=TypeError(),
        reason_code="EXECUTION_DEPENDENCY_RESULT_INVALID",
    )
    _log_typed_failure(failure)
    return failure


def _read_execution_mode(*, symbol: str = "execution") -> str:
    """Read and validate mode through the typed dependency boundary."""
    mode = _call_execution_dependency("get_mode", symbol, get_mode)
    if mode not in {"paper", "dryrun", "live"}:
        raise _invalid_dependency_result(operation="get_mode", symbol=symbol)
    return mode


def _validated_returns(values, *, operation: str, symbol: str) -> list[float]:
    """Bound and normalize return observations before handing them to NumPy."""
    if not isinstance(values, (list, tuple)) or len(values) > _MAX_RETURN_SAMPLES:
        raise _invalid_dependency_result(operation=operation, symbol=symbol)
    normalized = []
    for value in values:
        if not _is_finite_real(
            value, minimum=-1.0, maximum=_MAX_RETURN_MAGNITUDE
        ):
            raise _invalid_dependency_result(operation=operation, symbol=symbol)
        normalized.append(float(value))
    return normalized


def _validated_cvar_sizing(sizing, *, symbol: str, price: float, capital: float):
    """Reject malformed risk-engine output before it can influence an order."""
    if sizing is None:
        return None
    fields = ("quantity", "notional", "capital_used_pct", "risk_amount", "cvar_95")
    try:
        values = _call_execution_dependency(
            "cvar_position_size",
            symbol,
            lambda: tuple(getattr(sizing, field) for field in fields),
        )
    except ExecutionDependencyUnavailable as failure:
        raise _invalid_dependency_result(
            operation="cvar_position_size", symbol=symbol
        ) from failure
    quantity, notional, capital_used_pct, risk_amount, cvar_95 = values
    if not (
        _is_finite_real(quantity, positive=True, maximum=_MAX_CVAR_QUANTITY)
        and _is_finite_real(notional, positive=True)
        and _is_finite_real(capital_used_pct, positive=True, maximum=100)
        and _is_finite_real(risk_amount, positive=True)
        and _is_finite_real(cvar_95, positive=True, maximum=1)
    ):
        raise _invalid_dependency_result(operation="cvar_position_size", symbol=symbol)
    quantity = float(quantity)
    notional = float(notional)
    capital_used_pct = float(capital_used_pct)
    risk_amount = float(risk_amount)
    cvar_95 = float(cvar_95)
    if (
        notional > min(capital, _MAX_CVAR_NOTIONAL)
        or not math.isclose(quantity * price, notional, rel_tol=0.02, abs_tol=0.02)
        or not math.isclose(notional / capital * 100, capital_used_pct, rel_tol=0.05, abs_tol=0.05)
        or risk_amount > notional
    ):
        raise _invalid_dependency_result(operation="cvar_position_size", symbol=symbol)
    return {
        "quantity": quantity,
        "notional": notional,
        "capital_used_pct": capital_used_pct,
        "risk_amount": risk_amount,
        "cvar_95": cvar_95,
    }


def _validated_order_sizing(
    sizing, *, symbol: str, price: float, action: str
) -> dict:
    """Reject non-finite, unbounded, or internally inconsistent order sizing."""
    if not isinstance(sizing, dict):
        raise _invalid_dependency_result(
            operation="validate_order_sizing", symbol=symbol
        )
    shares = sizing.get("shares")
    cost = sizing.get("cost")
    position_pct = sizing.get("position_pct")
    cash_to_use = sizing.get("cash_to_use")
    quote_balance = sizing.get("quote_balance")
    if not (
        _is_finite_real(shares, positive=True, maximum=_MAX_CVAR_QUANTITY)
        and _is_finite_real(
            cost, minimum=MIN_ORDER_USD, maximum=_MAX_CVAR_NOTIONAL
        )
        and _is_finite_real(position_pct, minimum=0, maximum=1)
        and _is_finite_real(cash_to_use, minimum=0, maximum=_MAX_CVAR_NOTIONAL)
        and _is_finite_real(quote_balance, minimum=0)
        and _is_finite_real(price, positive=True)
    ):
        raise _invalid_dependency_result(
            operation="validate_order_sizing", symbol=symbol
        )
    shares = float(cast(Real, shares))
    cost = float(cast(Real, cost))
    quote_balance = float(cast(Real, quote_balance))
    try:
        computed_cost = shares * float(price)
    except (ArithmeticError, TypeError, ValueError) as exc:
        failure = ExecutionDependencyUnavailable(
            operation="validate_order_sizing",
            symbol=symbol,
            cause=exc,
            reason_code="EXECUTION_DEPENDENCY_RESULT_INVALID",
        )
        _log_typed_failure(failure)
        raise failure from exc
    if (
        not math.isfinite(computed_cost)
        or not math.isclose(computed_cost, cost, rel_tol=0.02, abs_tol=0.02)
        or (action == "BUY" and cost > quote_balance)
    ):
        raise _invalid_dependency_result(
            operation="validate_order_sizing", symbol=symbol
        )
    sizing.update(
        {
            "shares": shares,
            "cost": cost,
            "position_pct": float(cast(Real, position_pct)),
            "cash_to_use": float(cast(Real, cash_to_use)),
            "quote_balance": quote_balance,
        }
    )
    return sizing


def _validated_order_quantity(quantity, *, symbol: str) -> float:
    """Return a bounded finite order quantity or a typed invalid result."""
    if not _is_finite_real(
        quantity, positive=True, maximum=_MAX_CVAR_QUANTITY
    ):
        raise _invalid_dependency_result(
            operation="validate_order_quantity", symbol=symbol
        )
    return float(cast(Real, quantity))


def _validated_live_positions(data: dict) -> dict:
    """Validate durable position/tier state without discarding compatible extras."""
    if not isinstance(data, dict) or not isinstance(data.get("positions"), dict):
        raise ValueError("invalid live positions schema")
    for symbol, position in data["positions"].items():
        if _validated_symbol(symbol) is None or not isinstance(position, dict):
            raise ValueError("invalid live position")
        if not _is_finite_real(
            position.get("shares"), minimum=0, maximum=_MAX_CVAR_QUANTITY
        ) or not _is_finite_real(
            position.get("avg_cost"), minimum=0, maximum=_MAX_CVAR_NOTIONAL
        ):
            raise ValueError("invalid live position value")
    tiered_exits = data.get("tiered_exits")
    if tiered_exits is not None:
        if not isinstance(tiered_exits, dict):
            raise ValueError("invalid tiered exits")
        for symbol, tier_state in tiered_exits.items():
            if _validated_symbol(symbol) is None or not isinstance(tier_state, dict):
                raise ValueError("invalid tier state")
            cumulative_closed = tier_state.get("cumulative_closed", 0)
            if not _is_finite_real(cumulative_closed, minimum=0, maximum=1) or float(cumulative_closed) >= 1:
                raise ValueError("invalid cumulative closed")
            high_watermark = tier_state.get("high_watermark")
            if high_watermark is not None and not _is_finite_real(high_watermark, positive=True):
                raise ValueError("invalid high watermark")
    return data


def _validated_tiered_exit_result(result, *, symbol: str) -> dict:
    """Accept only bounded tiered-exit decisions before a sell calculation."""
    if not isinstance(result, dict):
        raise _invalid_dependency_result(operation="tiered_profit_exit", symbol=symbol)
    action = result.get("action")
    close_pct = result.get("close_pct")
    reason = _bounded_safe_text(result.get("reason"), maximum=300, allow_empty=True)
    if action not in {"hold", "partial_close", "full_close"} or reason is None or not _is_finite_real(
        close_pct, minimum=0, maximum=1
    ):
        raise _invalid_dependency_result(operation="tiered_profit_exit", symbol=symbol)
    close_pct = float(close_pct)
    if (
        (action == "hold" and close_pct != 0)
        or (action == "partial_close" and not 0 < close_pct < 1)
        or (action == "full_close" and close_pct != 1)
    ):
        raise _invalid_dependency_result(operation="tiered_profit_exit", symbol=symbol)
    if "trailing_stop" in result and not _is_finite_real(result["trailing_stop"], positive=True):
        raise _invalid_dependency_result(operation="tiered_profit_exit", symbol=symbol)
    return {"action": action, "close_pct": close_pct, "reason": reason}


def _validated_forecast_volatility(volatility) -> float:
    """Normalize a usable GARCH volatility forecast before sizing arithmetic."""
    if not _is_finite_real(volatility, minimum=0):
        raise TypeError("volatility forecast must be a finite non-negative number")
    return float(cast(Real, volatility))


def _validated_ticker_price(ticker, *, symbol: str) -> float:
    """Normalize the selected ticker price before exit-monitor arithmetic."""
    if not isinstance(ticker, dict):
        raise _invalid_dependency_result(operation="fetch_ticker", symbol=symbol)

    price = ticker.get("last", ticker.get("close", 0))
    if not _is_finite_real(
        price, positive=True, maximum=_MAX_CVAR_NOTIONAL
    ):
        raise _invalid_dependency_result(operation="fetch_ticker", symbol=symbol)
    return float(cast(Real, price))


def _validated_balance_values(
    balances, *, symbol: str, currencies: tuple[str, ...], fields: tuple[str, ...]
) -> dict[str, dict[str, float]]:
    """Normalize usable balance values while rejecting malformed provider data."""
    if not isinstance(balances, dict):
        raise _invalid_dependency_result(operation="fetch_balances", symbol=symbol)

    normalized = {}
    for currency in currencies:
        if currency not in balances:
            normalized[currency] = {field: 0.0 for field in fields}
            continue

        balance = balances[currency]
        if not isinstance(balance, dict):
            raise _invalid_dependency_result(operation="fetch_balances", symbol=symbol)

        normalized[currency] = {}
        for field in fields:
            value = balance.get(field, 0)
            if value is None:
                normalized[currency][field] = 0.0
            elif _is_finite_real(
                value, minimum=0, maximum=_MAX_CVAR_QUANTITY
            ):
                normalized[currency][field] = float(cast(Real, value))
            else:
                raise _invalid_dependency_result(operation="fetch_balances", symbol=symbol)
    return normalized


def _validated_target_weight(target_weight) -> float | None:
    """Require an allocation target to be a finite fraction before use."""
    if target_weight is None:
        return None
    if not _is_finite_real(target_weight, minimum=0, maximum=1):
        raise TypeError("target weight must be a finite fraction")
    return float(cast(Real, target_weight))


def _persist_order_evidence(order_log: dict, *, symbol: str) -> None:
    try:
        LIVE_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LIVE_ORDERS_FILE, "a") as file_handle:
            file_handle.write(json.dumps(order_log, default=str) + "\n")
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        failure = ExecutionStatePersistenceFailed(
            operation="write_live_order_log", symbol=symbol, cause=exc
        )
        _log_typed_failure(failure)
        raise failure from exc


def _persist_live_positions(data: dict, *, symbol: str) -> None:
    try:
        _validated_live_positions(data)
        _save_live_positions(data)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        failure = ExecutionStatePersistenceFailed(
            operation="save_live_positions", symbol=symbol, cause=exc
        )
        _log_typed_failure(failure)
        raise failure from exc


def _update_live_positions_after_fill(
    data: dict,
    *,
    symbol: str,
    action: str,
    sizing: dict,
    fill_price: float,
) -> None:
    """Revalidate aliased durable state after acknowledgement, then persist it."""
    try:
        live_pos = _validated_live_positions(data)
        if action == "BUY":
            if symbol not in live_pos["positions"]:
                live_pos["positions"][symbol] = {"shares": 0, "avg_cost": 0}
            pos = live_pos["positions"][symbol]
            total = pos["shares"] + sizing["shares"]
            pos["avg_cost"] = (
                ((pos["shares"] * pos["avg_cost"]) + sizing["cost"]) / total
                if total > 0
                else fill_price
            )
            pos["shares"] = total
        elif action == "SELL":
            live_pos["positions"].pop(symbol, None)
        _validated_live_positions(live_pos)
    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
        failure = ExecutionStatePersistenceFailed(
            operation="update_live_positions_after_fill", symbol=symbol, cause=exc
        )
        _log_typed_failure(failure)
        raise failure from exc
    _persist_live_positions(live_pos, symbol=symbol)


def _persist_journal(entry: dict, *, symbol: str) -> None:
    try:
        _log_journal(entry)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        failure = ExecutionStatePersistenceFailed(
            operation="write_execution_journal", symbol=symbol, cause=exc
        )
        _log_typed_failure(failure)
        raise failure from exc


def _failure_entry(failure: ExecutionBoundaryError, *, action: str | None = None) -> dict:
    entry = failure.to_dict()
    if action is not None:
        entry["action"] = action
    return entry


def _record_summary_failure(
    summary: dict, failure: ExecutionBoundaryError, *, action: str | None = None
) -> None:
    summary["errors"].append(_failure_entry(failure, action=action))
    has_execution_evidence = bool(summary.get("executed") or summary.get("actions"))
    summary["status"] = (
        "PARTIAL" if failure.status == "PARTIAL" or has_execution_evidence else "UNAVAILABLE"
    )
    if summary.get("reason_code") is None:
        summary["reason_code"] = failure.reason_code
        summary["trace_id"] = failure.trace_id


def _record_observability_failure(summary: dict, failure: ExecutionObservabilityFailed) -> None:
    summary.setdefault("observability_failures", []).append(failure.to_dict())
    summary["status"] = "PARTIAL"
    if summary.get("reason_code") is None:
        summary["reason_code"] = failure.reason_code
        summary["trace_id"] = failure.trace_id


def _record_sizing_observability_failures(summary: dict, sizing: dict) -> None:
    """Expose optional sizing degradation before execution continues."""
    for failure in sizing.pop("_observability_failures", []):
        _record_observability_failure(summary, failure)


def _record_execution_evidence(summary: dict) -> None:
    """A fill after an unavailable item makes the aggregate only partial."""
    if summary["status"] == "UNAVAILABLE":
        summary["status"] = "PARTIAL"


def preflight(exchange_id: str, symbol: str, side: str, amount: float) -> tuple[bool, Optional[str]]:
    """Run safety checks before placing a live/dryrun order.

    Returns (True, None) if OK, or (False, "reason") if blocked.
    """
    mode = _read_execution_mode(symbol=symbol)
    try:
        amount = _validated_order_quantity(
            amount, symbol=symbol.split("/")[0]
        )
    except ExecutionDependencyUnavailable:
        return False, "Invalid order amount"

    # 1. Kill switch — live mode requires explicit enable
    if mode == "live" and not LIVE_EXECUTION_ENABLED:
        return False, "LIVE_EXECUTION_ENABLED=False — kill switch active"

    # 1.5. Incubation gate: unavailable state is a hard live-execution block.
    if mode == "live":
        try:
            from incubation_tracker import incubation_status
        except ImportError as exc:
            return False, f"Incubation gate unavailable: IMPORT_FAILED ({type(exc).__name__})"

        status = incubation_status()
        if status["status"] != "AVAILABLE":
            return False, (
                "Incubation gate unavailable: "
                f"{status['reason_code']} trace_id={status['trace_id']}"
            )
        if not status["passed"]:
            return False, (
                "Incubation gate not passed: "
                f"{status['n_resolved']}/{status['n_required']} signals resolved, "
                f"win rate {status['win_rate']:.0%}"
            )

    # 2. Minimum order size
    if amount <= 0:
        return False, f"Invalid order amount: {amount}"

    # 3. Balance check — ensure enough free balance
    if side.lower() == "buy":
        balances = _call_execution_dependency(
            "fetch_balances", symbol.split("/")[0], fetch_balances, exchange_id
        )
        if balances is None:
            failure = ExecutionDependencyUnavailable(
                operation="fetch_balances", symbol=symbol.split("/")[0]
            )
            _log_typed_failure(failure)
            raise failure
        quote_balances = _validated_balance_values(
            balances,
            symbol=symbol.split("/")[0],
            currencies=("USD", "USDT", "USDC"),
            fields=("free",),
        )
        usd_balance = (
            quote_balances["USD"]["free"]
            or quote_balances["USDT"]["free"]
            or quote_balances["USDC"]["free"]
        )
        if usd_balance <= 0:
            return False, f"No USD/USDT/USDC balance on {exchange_id}"

    # 4. Market open check — crypto is 24/7, but exchanges do maintenance
    # CCXT will surface maintenance errors; we don't pre-check here.

    return True, None


def _load_live_positions() -> dict:
    """Load live positions without replacing unreadable durable state."""
    if LIVE_POSITIONS_FILE.exists():
        try:
            data = json.loads(LIVE_POSITIONS_FILE.read_text())
            return _validated_live_positions(data)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            failure = ExecutionStatePersistenceFailed(
                operation="load_live_positions",
                cause=exc,
                status="UNAVAILABLE",
                reason_code="EXECUTION_STATE_UNAVAILABLE",
            )
            _log_typed_failure(failure)
            raise failure from exc
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
    mode = _read_execution_mode(symbol=symbol)
    if mode == "paper":
        return None

    balances = _call_execution_dependency(
        "fetch_balances", symbol, fetch_balances, exchange_id
    )
    if balances is None:
        failure = ExecutionDependencyUnavailable(
            operation="fetch_balances", symbol=symbol
        )
        _log_typed_failure(failure)
        raise failure
    base_asset = symbol.split("/")[0] if "/" in symbol else symbol

    if side.upper() == "SELL":
        # Close full position: use actual base asset balance
        base_balances = _validated_balance_values(
            balances,
            symbol=symbol,
            currencies=(base_asset,),
            fields=("free", "total"),
        )
        base_balance = (
            base_balances[base_asset]["free"]
            or base_balances[base_asset]["total"]
        )
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
        import numpy as np
        import json as _json
        returns_dir = data_root() / "memory" / "backtest" / "returns"
        returns_file = returns_dir / f"{symbol.replace('/', '_')}_returns.json"
        if returns_file.exists():
            data = _json.loads(returns_file.read_text())
            if not isinstance(data, dict):
                raise _invalid_dependency_result(
                    operation="load_symbol_returns", symbol=symbol
                )
            daily_returns = _validated_returns(
                data.get("daily_returns", []),
                operation="load_symbol_returns",
                symbol=symbol,
            )
            rets = daily_returns[-30:]
            if len(rets) >= MIN_RETURNS_FOR_CVAR:
                returns_30d = np.array(rets)
    except (ImportError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        failure = ExecutionDependencyUnavailable(
            operation="load_symbol_returns", symbol=symbol, cause=exc
        )
        _log_typed_failure(failure)
        raise failure from exc

    quote_balances = _validated_balance_values(
        balances,
        symbol=symbol,
        currencies=("USD", "USDT", "USDC"),
        fields=("free",),
    )
    quote_balance = (
            quote_balances["USD"]["free"]
            or quote_balances["USDT"]["free"]
            or quote_balances["USDC"]["free"]
    )
    if quote_balance <= 0:
        log.warning("[%s] No quote balance available on %s", symbol, exchange_id)
        return None

    if returns_30d is not None and len(returns_30d) >= MIN_RETURNS_FOR_CVAR:
        # CVaR-based sizing (preferred)
        sizing = _call_execution_dependency(
            "cvar_position_size", symbol, cvar_position_size,
            symbol, price, "BUY", quote_balance, returns_30d,
        )
        sizing = _validated_cvar_sizing(
            sizing, symbol=symbol, price=price, capital=quote_balance
        )
        if sizing is None:
            log.warning("[%s] CVaR sizing failed — falling back to Kelly", symbol)
        else:
            cost = sizing["notional"]
            shares = sizing["quantity"]
            position_pct = sizing["capital_used_pct"] / 100
            cash_to_use = cost
            log.info("[%s] CVaR sizing: %s shares at $%.2f (risk=$%.2f, CVaR95=%.4f%%)",
                     symbol, shares, price, sizing["risk_amount"], sizing["cvar_95"] * 100)
    else:
        # Fallback 1: try equity curve returns from performance_tracker (live trade history)
        _pt_returns = None
        try:
            from performance_tracker import _equity_returns
        except ImportError:
            _equity_returns = None
        if _equity_returns is not None:
            try:
                _pt_rets = _call_execution_dependency(
                    "load_equity_returns", symbol, _equity_returns
                )
                _pt_rets = _validated_returns(
                    _pt_rets, operation="load_equity_returns", symbol=symbol
                )
                if len(_pt_rets) >= MIN_RETURNS_FOR_CVAR:
                    import numpy as _np
                    _pt_returns = _np.array(_pt_rets[-30:])
            except (OSError, UnicodeError, TypeError, ValueError, KeyError) as exc:
                failure = ExecutionDependencyUnavailable(
                    operation="load_equity_returns", symbol=symbol, cause=exc
                )
                _log_typed_failure(failure)
                raise failure from exc

        if _pt_returns is not None and len(_pt_returns) >= MIN_RETURNS_FOR_CVAR:
            # CVaR from live equity curve (adapts to actual account performance)
            sizing = _call_execution_dependency(
                "cvar_position_size", symbol, cvar_position_size,
                symbol, price, "BUY", quote_balance, _pt_returns,
            )
            sizing = _validated_cvar_sizing(
                sizing, symbol=symbol, price=price, capital=quote_balance
            )
            if sizing is not None:
                cost = sizing["notional"]
                shares = sizing["quantity"]
                position_pct = sizing["capital_used_pct"] / 100
                cash_to_use = cost
                log.info("[%s] CVaR sizing (equity returns fallback): %.0f%% (CVaR95=%.4f%%)",
                         symbol, position_pct * 100, sizing["cvar_95"] * 100)
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
    sizing_observability_failures = []
    if _HAS_GARCH and returns_30d is not None and len(returns_30d) >= 10:
        observed_forecast = []
        failure = _call_observability_boundary(
            "forecast_volatility",
            symbol,
            lambda: observed_forecast.append(
                _validated_forecast_volatility(forecast_volatility(returns_30d))
            ) or True,
        )
        if failure is not None:
            sizing_observability_failures.append(failure)
        else:
            try:
                import numpy as np
                vol_forecast = observed_forecast[0]
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
            except (ArithmeticError, IndexError, TypeError, ValueError) as exc:
                failure = ExecutionObservabilityFailed(
                    operation="forecast_volatility", symbol=symbol, cause=exc
                )
                _log_typed_failure(failure)
                sizing_observability_failures.append(failure)

    if vol_adjustment != 1.0:
        position_pct = min(position_pct * vol_adjustment, MAX_POSITION_PCT)
        cash_to_use *= vol_adjustment
        shares *= vol_adjustment
        cost *= vol_adjustment

    if cost < MIN_ORDER_USD:
        log.info("[%s] Order too small ($%.2f < $%.2f minimum) — skipping", symbol, cost, MIN_ORDER_USD)
        return None

    sizing_result = {
        "position_pct": round(position_pct, 4),
        "cash_to_use": round(cash_to_use, 2),
        "shares": round(shares, 8),
        "cost": round(cost, 2),
        "quote_balance": round(quote_balance, 2),
    }
    if sizing_observability_failures:
        sizing_result["_observability_failures"] = sizing_observability_failures
    return _validated_order_sizing(
        sizing_result, symbol=symbol, price=price, action=side.upper()
    )


def _check_allocation_alignment(symbol: str, sizing: dict) -> tuple[dict, str]:
    """Check if a BUY trade moves toward or away from optimal allocation.

    If moving AWAY from target weight: reduce size by 50% and log warning.

    Returns (sizing_dict, log_message).
    """
    if not _HAS_ALLOCATION_ENGINE or get_target_weight_for_symbol is None:
        return sizing, ""

    if sizing is None:
        return sizing, ""

    observed_target_weight = []
    failure = _call_observability_boundary(
        "allocation_alignment",
        symbol,
        lambda: observed_target_weight.append(
            _validated_target_weight(get_target_weight_for_symbol(symbol))
        ) or True,
    )
    if failure is not None:
        sizing.setdefault("_observability_failures", []).append(failure)
        return sizing, ""

    target_weight = observed_target_weight[0]

    if target_weight is None:
        return sizing, ""

    # Approximate current weight and post-trade weight
    # Current weight ≈ sizing.position_pct (this is the capital share being deployed)
    # This is a coarse check: if target is < current_pos_pct, adding more
    # moves us further away from target
    current_pct = sizing.get("position_pct", 0)
    # The target weight is a validated fraction (0-1).
    target_pct = target_weight

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
    results = {
        "executed": [],
        "skipped": [],
        "errors": [],
        "mode": None,
        "status": "COMPLETED",
        "reason_code": None,
        "trace_id": new_trace_id(),
        "observability_failures": [],
    }
    try:
        mode = _read_execution_mode()
    except ExecutionDependencyUnavailable as failure:
        _record_summary_failure(results, failure)
        return results
    results["mode"] = mode

    if not isinstance(report, dict) or not isinstance(report.get("assets", []), list):
        if mode != "paper":
            failure = _invalid_execution_input()
            _record_summary_failure(results, failure)
        return results
    assets = report.get("assets", [])
    if not assets:
        log.info("[execute_live] No assets in report — nothing to do")
        return results

    for asset in assets:
        if mode != "paper":
            try:
                validated = _validated_execution_input(asset)
            except ExecutionInputInvalid as failure:
                _record_summary_failure(results, failure)
                continue
            sym = validated["symbol"]
            action = validated["action"]
            price = validated["price"]
            confidence = validated["confidence"]
            modifier = validated["position_modifier"]
            reasoning = validated["reasoning"]
            if price is None:
                failure = _invalid_execution_input(symbol=sym)
                _record_summary_failure(results, failure, action=action)
                continue
        else:
            # Paper execution keeps the legacy report compatibility contract.
            if not isinstance(asset, dict):
                continue
            sym = asset.get("symbol", "???")
            action = str(asset.get("suggestion", "")).upper()
            if action not in ("BUY", "SELL"):
                continue
            price = float(asset.get("price") or asset.get("current_price") or 0)
            if not price:
                results["skipped"].append({"symbol": sym, "reason": "no_price"})
                continue
            confidence = float(asset.get("confidence", 0.5))
            modifier = asset.get("_gate_modifier", 1.0)
            reasoning = asset.get("rationale", "")[:300]

        signal = {
            "asset": sym,
            "action": action,
            "confidence": confidence,
            "reasoning": reasoning,
            "entry_price": price,
            "stop_loss": asset.get("stop_loss_suggestion"),
            "take_profit": asset.get("target_suggestion"),
            "position_modifier": modifier,
        }

        if mode == "paper":
            confirmation = paper_execute(signal, {sym: price})
            results["executed"].append({
                "symbol": sym, "action": action, "mode": "paper",
                "result": confirmation,
            })
            continue

        if modifier == 0:
            results["skipped"].append({
                "symbol": sym,
                "action": action,
                "reason": "position_modifier_zero",
                "reason_code": "POSITION_MODIFIER_ZERO",
            })
            continue

        exchange_id = DEFAULT_EXCHANGE
        try:
            # Validate durable state before sizing or submission; reuse it after ack.
            durable_positions = _load_live_positions()
            sizing = _compute_position_size(sym, price, confidence, action, exchange_id)
            if sizing is None:
                results["skipped"].append({
                    "symbol": sym, "action": action,
                    "reason": "insufficient_balance_or_below_minimum",
                })
                continue
            if modifier < 1.0:
                sizing["shares"] *= modifier
                sizing["cost"] *= modifier
                sizing["position_pct"] *= modifier
            if action == "BUY":
                sizing, _alloc_msg = _check_allocation_alignment(sym, sizing)
                if sizing.get("_allocation_trimmed"):
                    signal["_allocation_trimmed"] = True
            sizing = _validated_order_sizing(
                sizing, symbol=sym, price=price, action=action
            )
            _record_sizing_observability_failures(results, sizing)
            ok, reason = preflight(
                exchange_id, f"{sym}/USD", action.lower(), sizing["shares"]
            )
        except ExecutionBoundaryError as failure:
            _record_summary_failure(results, failure, action=action)
            continue

        if not ok:
            log.warning("[%s] Preflight failed", sym)
            results["skipped"].append({
                "symbol": sym, "action": action, "reason": f"preflight: {reason}",
            })
            if mode == "live":
                try:
                    from alert_manager import send_telegram_text
                except ImportError as exc:
                    alert_failure = ExecutionObservabilityFailed(
                        operation="send_telegram_text", symbol=sym, cause=exc
                    )
                    _log_typed_failure(alert_failure)
                else:
                    alert_failure = _call_observability_boundary(
                        "send_telegram_text",
                        sym,
                        send_telegram_text,
                        f"BLOCKED [{mode.upper()}] {action} {sym}: {reason}",
                    )
                if alert_failure is not None:
                    _record_observability_failure(results, alert_failure)
            continue

        try:
            order_result = _call_order_submission(
                "place_order",
                sym,
                place_order,
                exchange_id,
                f"{sym}/USD",
                action.lower(),
                sizing["shares"],
                price=None,
            )
            order_result = _validated_order_result(order_result, symbol=sym)
        except ExecutionBoundaryError as failure:
            _record_summary_failure(results, failure, action=action)
            continue

        if order_result.get("status") == "blocked":
            results["skipped"].append({
                "symbol": sym,
                "action": action,
                "reason": "order_blocked",
                "reason_code": "ORDER_BLOCKED",
            })
            continue

        fill_price = float(order_result["avg_fill_price"])
        order_log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": sym,
            "side": action,
            "shares": sizing["shares"],
            "fill_price": round(fill_price, 4),
            "cost": sizing["cost"],
            "exchange": exchange_id,
            "mode": mode,
            "order_id": order_result["id"],
            "slippage": abs(fill_price - price) / price if price else 0,
        }

        persistence_failures = []
        try:
            _persist_order_evidence(order_log, symbol=sym)
        except ExecutionStatePersistenceFailed as failure:
            persistence_failures.append(failure)

        cra_failure = _call_observability_boundary(
            "cra_log_trade",
            sym,
            cra_tracker.log_trade,
            symbol=sym,
            side=action.lower(),
            quantity=sizing["shares"],
            price_usd=fill_price,
        )
        if cra_failure is not None:
            _record_observability_failure(results, cra_failure)

        try:
            _update_live_positions_after_fill(
                durable_positions,
                symbol=sym,
                action=action,
                sizing=sizing,
                fill_price=fill_price,
            )
        except ExecutionStatePersistenceFailed as failure:
            persistence_failures.append(failure)

        results["executed"].append({
            "symbol": sym,
            "action": action,
            "mode": mode,
            "exchange": exchange_id,
            "status": "PARTIAL" if persistence_failures else "COMPLETED",
            "result": order_log,
        })
        _record_execution_evidence(results)
        for failure in persistence_failures:
            _record_summary_failure(results, failure, action=action)

        try:
            from alert_manager import send_telegram_text
        except ImportError as exc:
            alert_failure = ExecutionObservabilityFailed(
                operation="send_telegram_text", symbol=sym, cause=exc
            )
            _log_typed_failure(alert_failure)
        else:
            tag = "LIVE" if mode == "live" else "DRYRUN"
            alert_failure = _call_observability_boundary(
                "send_telegram_text",
                sym,
                send_telegram_text,
                f"[{tag}] {action} {sym}: {sizing['shares']:.4f} shares "
                f"@ ${fill_price:,.2f} | "
                f"Cost ${sizing['cost']:,.2f} ({sizing['position_pct']:.1%})",
            )
        if alert_failure is not None:
            _record_observability_failure(results, alert_failure)

        log.info("[%s] %s %s: %.6f shares @ $%.2f = $%.2f",
                 mode.upper(), action, sym,
                 sizing["shares"], fill_price, sizing["cost"])

    log.info("[execute_live] Mode=%s — %d executed, %d skipped, %d errors",
             mode, len(results["executed"]), len(results["skipped"]), len(results["errors"]))

    return results


def get_live_summary() -> dict:
    """Return summary of live positions for dashboard display."""
    try:
        mode = _read_execution_mode()
    except ExecutionDependencyUnavailable as failure:
        return {
            "mode": None,
            "live_execution_enabled": LIVE_EXECUTION_ENABLED,
            "positions": {},
            "updated_at": None,
            **failure.to_dict(),
        }
    try:
        live_pos = _load_live_positions()
    except ExecutionStatePersistenceFailed as failure:
        return {
            "mode": mode,
            "live_execution_enabled": LIVE_EXECUTION_ENABLED,
            "positions": {},
            "updated_at": None,
            "status": "UNAVAILABLE",
            "reason_code": failure.reason_code,
            "trace_id": failure.trace_id,
            "errors": [failure.to_dict()],
        }

    positions = {}
    for sym, pos in live_pos.get("positions", {}).items():
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
    try:
        mode = _read_execution_mode()
    except ExecutionDependencyUnavailable as failure:
        return {
            "actions": [],
            "errors": [failure.to_dict()],
            "unavailable_symbols": [],
            **failure.to_dict(),
        }
    if mode == "paper":
        return {"status": "skipped", "reason": "paper_mode"}

    if not _HAS_EXIT_STRATEGIES:
        log.debug("Exit strategies not available — skipping position monitoring")
        return {"status": "skipped", "reason": "exit_strategies_unavailable"}

    try:
        live_pos = _validated_live_positions(_load_live_positions())
    except (TypeError, ValueError) as exc:
        failure = ExecutionStatePersistenceFailed(
            operation="load_live_positions",
            cause=exc,
            status="UNAVAILABLE",
            reason_code="EXECUTION_STATE_UNAVAILABLE",
        )
        _log_typed_failure(failure)
        return {
            "status": "UNAVAILABLE",
            "reason_code": failure.reason_code,
            "trace_id": failure.trace_id,
            "actions": [],
            "errors": [failure.to_dict()],
            "unavailable_symbols": [],
        }
    except ExecutionStatePersistenceFailed as failure:
        return {
            "status": "UNAVAILABLE",
            "reason_code": failure.reason_code,
            "trace_id": failure.trace_id,
            "actions": [],
            "errors": [failure.to_dict()],
            "unavailable_symbols": [],
        }
    positions = live_pos.get("positions", {})
    if not positions:
        return {"status": "no_positions"}

    exchange_id = DEFAULT_EXCHANGE
    summary = {
        "actions": [],
        "errors": [],
        "status": "COMPLETED",
        "reason_code": None,
        "trace_id": new_trace_id(),
        "unavailable_symbols": [],
        "observability_failures": [],
    }
    priced_symbols = 0
    state_dirty = False

    for sym, pos in list(positions.items()):
        shares_held = pos.get("shares", 0)
        avg_cost = pos.get("avg_cost", 0)
        if shares_held <= 0 or avg_cost <= 0:
            continue

        # Fetch current price at the uncontrolled adapter boundary.
        try:
            from exchange.ccxt_bridge import fetch_ticker
            ticker = _call_execution_dependency(
                "fetch_ticker", sym, fetch_ticker, exchange_id, f"{sym}/USD"
            )
            current_price = _validated_ticker_price(ticker, symbol=sym)
        except ExecutionDependencyUnavailable as failure:
            _log_typed_failure(failure)
            _record_summary_failure(summary, failure)
            summary["unavailable_symbols"].append(sym)
            continue
        priced_symbols += 1

        # Get ATR for the symbol (2% of price proxy for crypto)
        atr_value = current_price * 0.02

        # Track already-closed fraction for this symbol
        tiered_exits = live_pos.get("tiered_exits", {})
        sym_tiers = tiered_exits.get(sym, {
            "cumulative_closed": 0.0,
            "high_watermark": current_price,
        })
        cum_closed = sym_tiers.get("cumulative_closed", 0.0)
        high_watermark = max(sym_tiers.get("high_watermark", current_price), current_price)

        # Validate the uncontrolled strategy result before mutating tier state.
        try:
            result = _call_execution_dependency(
                "tiered_profit_exit",
                sym,
                tiered_profit_exit,
                entry_price=avg_cost,
                current_price=current_price,
                atr=atr_value,
                direction="long",
                high_watermark=high_watermark,
            )
            result = _validated_tiered_exit_result(result, symbol=sym)
        except ExecutionDependencyUnavailable as failure:
            _record_summary_failure(summary, failure)
            summary["unavailable_symbols"].append(sym)
            continue

        sym_tiers["high_watermark"] = high_watermark

        action = result["action"]
        close_pct = result["close_pct"]
        reason = result["reason"]

        if action == "hold" or close_pct <= cum_closed:
            log.debug("[%s] Exit monitor: HOLD (cum_closed=%.0f%%, close_pct=%.0f%%)",
                      sym, cum_closed * 100, close_pct * 100)
            live_pos.setdefault("tiered_exits", {})[sym] = sym_tiers
            state_dirty = True
            continue

        # Only close the delta (fraction not yet closed)
        delta_close = close_pct - cum_closed
        if delta_close <= 0:
            continue

        try:
            shares_to_sell = _validated_order_quantity(
                round(
                    shares_held * delta_close / (1.0 - cum_closed), 8
                ),
                symbol=sym,
            )
        except ArithmeticError:
            failure = _invalid_dependency_result(
                operation="validate_order_quantity", symbol=sym
            )
            _record_summary_failure(summary, failure)
            summary["unavailable_symbols"].append(sym)
            continue
        except ExecutionDependencyUnavailable as failure:
            _record_summary_failure(summary, failure)
            summary["unavailable_symbols"].append(sym)
            continue

        order_notional = shares_to_sell * current_price
        if not _is_finite_real(
            order_notional,
            minimum=MIN_ORDER_USD,
            maximum=_MAX_CVAR_NOTIONAL,
        ):
            failure = _invalid_dependency_result(
                operation="validate_order_notional", symbol=sym
            )
            _record_summary_failure(summary, failure)
            summary["unavailable_symbols"].append(sym)
            continue

        log.info("[%s] TIERED EXIT: closing %.0f%% (delta=%.0f%%) — %s shares @ $%.2f — %s",
                 sym, delta_close * 100, shares_to_sell, current_price, reason)

        # Place sell order for the tier fraction.
        try:
            order_result = _call_order_submission(
                "place_order",
                sym,
                place_order,
                exchange_id,
                f"{sym}/USD",
                "sell",
                shares_to_sell,
                price=None,
            )
            order_result = _validated_order_result(order_result, symbol=sym)
        except ExecutionBoundaryError as failure:
            _record_summary_failure(summary, failure)
            continue

        if order_result.get("status") == "blocked":
            failure = ExecutionSubmissionFailed(
                operation="place_order", symbol=sym, reason_code="ORDER_BLOCKED"
            )
            _log_typed_failure(failure)
            _record_summary_failure(summary, failure)
            continue

        fill_price = float(order_result["avg_fill_price"])
        sym_tiers["cumulative_closed"] = close_pct
        live_pos.setdefault("tiered_exits", {})[sym] = sym_tiers
        state_dirty = True

        if close_pct >= 1.0:
            live_pos["positions"].pop(sym, None)
            live_pos["tiered_exits"].pop(sym, None)
        else:
            pos["shares"] = max(0, shares_held - shares_to_sell)

        order_log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": sym,
            "side": "SELL",
            "shares": shares_to_sell,
            "fill_price": round(fill_price, 4),
            "cost": round(shares_to_sell * fill_price, 2),
            "exchange": exchange_id,
            "mode": mode,
            "order_id": order_result["id"],
            "exit_type": "tiered_profit",
            "close_pct": round(delta_close, 4),
            "reason": reason,
        }
        persistence_failures = []
        try:
            _persist_order_evidence(order_log, symbol=sym)
        except ExecutionStatePersistenceFailed as failure:
            persistence_failures.append(failure)

        action_entry = {
            "symbol": sym,
            "action": "partial_sell",
            "shares": shares_to_sell,
            "close_pct": round(delta_close, 4),
            "price": round(fill_price, 2),
            "reason": reason,
            "status": "PARTIAL" if persistence_failures else "COMPLETED",
            "order_id": order_result["id"],
        }
        summary["actions"].append(action_entry)
        for failure in persistence_failures:
            _record_summary_failure(summary, failure)

        try:
            from alert_manager import send_telegram_text
        except ImportError as exc:
            alert_failure = ExecutionObservabilityFailed(
                operation="send_telegram_text", symbol=sym, cause=exc
            )
            _log_typed_failure(alert_failure)
        else:
            tag = "LIVE" if mode == "live" else "DRYRUN"
            alert_failure = _call_observability_boundary(
                "send_telegram_text",
                sym,
                send_telegram_text,
                f"[{tag}] TIERED EXIT {sym}: {shares_to_sell:.4f} shares "
                f"(≈{delta_close*100:.0f}%) @ ${fill_price:,.2f} — {reason}",
            )
        if alert_failure is not None:
            _record_observability_failure(summary, alert_failure)

        log.info("[%s] Tiered exit: sold %.6f shares @ $%.2f (= %.0f%% of position)",
                 sym, shares_to_sell, fill_price, delta_close * 100)

    if state_dirty:
        try:
            _persist_live_positions(live_pos, symbol="exit_monitor")
        except ExecutionStatePersistenceFailed as failure:
            for action_entry in summary["actions"]:
                action_entry["status"] = "PARTIAL"
            _record_summary_failure(summary, failure)

    if summary["unavailable_symbols"] and priced_symbols:
        summary["status"] = "PARTIAL"

    action_count = len(summary["actions"])
    error_count = len(summary["errors"])
    log.info("[exit_monitor] %d exit(s) executed, %d error(s)", action_count, error_count)

    return summary


def execute_signal(signal: dict, prices: dict) -> dict:
    """Execute a single signal. Drop-in replacement for paper_trader.execute_signal().

    Routes to paper_trader for 'paper' mode, CCXT sandbox for 'dryrun',
    and CCXT real for 'live' (blocked by LIVE_EXECUTION_ENABLED).
    """
    try:
        mode = _read_execution_mode()
    except ExecutionDependencyUnavailable as failure:
        return failure.to_dict()
    if mode == "paper":
        return paper_execute(signal, prices)

    try:
        validated = _validated_execution_input(signal, single_signal=True)
    except ExecutionInputInvalid as failure:
        return failure.to_dict()
    symbol = validated["symbol"]
    action = validated["action"]
    if not isinstance(prices, dict):
        return _invalid_execution_input(symbol=symbol).to_dict()
    price = prices.get(symbol)
    if not _is_finite_real(price, positive=True):
        return _invalid_execution_input(symbol=symbol).to_dict()
    price = float(price)
    confidence = validated["confidence"]
    modifier = validated["position_modifier"]
    reasoning = validated["reasoning"]
    if modifier == 0:
        return {
            "status": "REJECTED",
            "reason_code": "POSITION_MODIFIER_ZERO",
            "trace_id": new_trace_id(),
            "operation": "validate_execution_input",
            "symbol": symbol,
        }

    # dryrun or live: real CCXT execution
    exchange_id = DEFAULT_EXCHANGE

    try:
        # Validate durable state before any sizing or submission boundary.
        durable_positions = _load_live_positions()
        sizing = _compute_position_size(symbol, price, confidence, action, exchange_id)
        if sizing and modifier < 1.0:
            sizing["shares"] *= modifier
            sizing["cost"] *= modifier
            sizing["position_pct"] *= modifier
        if sizing is None:
            return {"status": "rejected", "reason": "insufficient_balance_or_below_minimum"}
        if action == "BUY":
            sizing, _alloc_msg = _check_allocation_alignment(symbol, sizing)
        sizing = _validated_order_sizing(
            sizing, symbol=symbol, price=price, action=action
        )
        sizing_observability_failures = sizing.pop("_observability_failures", [])
        ok, reason = preflight(
            exchange_id, f"{symbol}/USD", action.lower(), sizing["shares"]
        )
    except ExecutionBoundaryError as failure:
        return failure.to_dict()

    if not ok:
        log.warning("[%s] Preflight failed", symbol)
        return {"status": "rejected", "reason": f"preflight: {reason}"}

    try:
        order_result = _call_order_submission(
            "place_order",
            symbol,
            place_order,
            exchange_id,
            f"{symbol}/USD",
            action.lower(),
            sizing["shares"],
            price=None,
        )
        order_result = _validated_order_result(order_result, symbol=symbol)
    except ExecutionBoundaryError as failure:
        return failure.to_dict()

    if order_result.get("status") == "blocked":
        return {
            "status": "rejected",
            "reason": "order_blocked",
            "reason_code": "ORDER_BLOCKED",
        }

    fill_price = float(order_result["avg_fill_price"])
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
        "order_id": order_result["id"],
        "slippage": abs(fill_price - price) / price if price else 0,
    }

    persistence_failures = []
    try:
        _persist_order_evidence(order_log, symbol=symbol)
    except ExecutionStatePersistenceFailed as failure:
        persistence_failures.append(failure)

    try:
        _persist_journal(
            {**order_log, "reasoning": reasoning},
            symbol=symbol,
        )
    except ExecutionStatePersistenceFailed as failure:
        persistence_failures.append(failure)

    try:
        _update_live_positions_after_fill(
            durable_positions,
            symbol=symbol,
            action=action,
            sizing=sizing,
            fill_price=fill_price,
        )
    except ExecutionStatePersistenceFailed as failure:
        persistence_failures.append(failure)

    if persistence_failures:
        primary_failure = persistence_failures[0]
        return {
            "status": "PARTIAL",
            "reason_code": "EXECUTION_STATE_PERSISTENCE_FAILED",
            "trace_id": primary_failure.trace_id,
            "operation": primary_failure.operation,
            "symbol": symbol,
            "execution_evidence": order_log,
            "persistence_failures": [
                failure.to_dict() for failure in persistence_failures
            ],
        }

    try:
        from alert_manager import send_telegram_text
    except ImportError as exc:
        alert_failure = ExecutionObservabilityFailed(
            operation="send_telegram_text", symbol=symbol, cause=exc
        )
        _log_typed_failure(alert_failure)
    else:
        tag = "LIVE" if mode == "live" else "DRYRUN"
        alert_failure = _call_observability_boundary(
            "send_telegram_text",
            symbol,
            send_telegram_text,
            f"[{tag}] {action} {symbol}: {sizing['shares']:.4f} shares "
            f"@ ${fill_price:,.2f} | "
            f"Cost ${cost:,.2f} ({sizing['position_pct']:.1%})",
        )

    log.info("[%s] %s %s: %.6f shares @ $%.2f = $%.2f",
             mode.upper(), action, symbol, sizing["shares"], fill_price, cost)

    result = {
        "status": "PARTIAL" if sizing_observability_failures else "filled",
        "aggregate_status": (
            "PARTIAL" if sizing_observability_failures else "COMPLETED"
        ),
        "execution_evidence": order_log,
        "observability_failures": [
            failure.to_dict() for failure in sizing_observability_failures
        ],
        **order_log,
    }
    if alert_failure is not None:
        result["status"] = "PARTIAL"
        result["aggregate_status"] = "PARTIAL"
        result["observability_failures"].append(alert_failure.to_dict())
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    summary = get_live_summary()
    print(f"Mode: {summary.get('mode')}")
    print(f"Live execution enabled: {LIVE_EXECUTION_ENABLED}")
    print(json.dumps(summary, indent=2))
