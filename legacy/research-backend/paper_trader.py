"""
paper_trader.py — Simulated brokerage with $100k virtual balance.

State machine, not a full exchange. Persists portfolio to disk after every trade.
Position sizing: max 20% per asset, max 60% total allocation. 0.1% slippage.
"""

import json
import logging
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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


class PaperPortfolioError(RuntimeError):
    """Base typed error for paper-state and valuation failures."""

    def __init__(self, reason_code: str, trace_id: str):
        self.reason_code = reason_code
        self.trace_id = trace_id
        super().__init__(f"{reason_code} trace_id={trace_id}")


class PortfolioStateError(PaperPortfolioError):
    """Portfolio persistence is unavailable or corrupt."""


class PortfolioValuationUnavailable(PaperPortfolioError):
    """At least one open position lacks a valid market price."""


class PriceSnapshot(Mapping[str, float]):
    """Mapping-compatible report prices with explicit availability metadata."""

    def __init__(
        self,
        prices: dict[str, float],
        *,
        status: str,
        reason_code: str | None,
        trace_id: str,
    ) -> None:
        self._prices = prices
        self.status = status
        self.reason_code = reason_code
        self.trace_id = trace_id

    def __getitem__(self, key: str) -> float:
        return self._prices[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._prices)

    def __len__(self) -> int:
        return len(self._prices)


class StopSweepResult(list[dict]):
    """List-compatible stop result with partial/unavailable state."""

    def __init__(
        self,
        orders: list[dict],
        *,
        status: str,
        reason_code: str | None,
        trace_id: str,
        unavailable_symbols: tuple[str, ...] = (),
    ) -> None:
        super().__init__(orders)
        self.status = status
        self.reason_code = reason_code
        self.trace_id = trace_id
        self.unavailable_symbols = unavailable_symbols


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
    trace_id = uuid4().hex[:16]
    try:
        PAPER_DIR.mkdir(parents=True, exist_ok=True)
        if not PORTFOLIO_FILE.exists():
            portfolio = _init_portfolio()
            save_portfolio(portfolio)
            return portfolio

        portfolio = json.loads(PORTFOLIO_FILE.read_text())
        if not isinstance(portfolio, dict):
            raise ValueError("portfolio must be an object")
        if not isinstance(portfolio.get("cash"), (int, float)):
            raise ValueError("portfolio cash is invalid")
        positions = portfolio.get("positions")
        if not isinstance(positions, dict):
            raise ValueError("portfolio positions are invalid")
        for symbol, position in positions.items():
            if not isinstance(symbol, str) or not isinstance(position, dict):
                raise ValueError("portfolio position schema is invalid")
            if not isinstance(position.get("shares"), (int, float)):
                raise ValueError("portfolio position shares are invalid")
            if not isinstance(position.get("avg_cost"), (int, float)):
                raise ValueError("portfolio position average cost is invalid")
        portfolio.setdefault("orders", [])
        portfolio.setdefault("pnl", 0.0)
        portfolio.setdefault("peak_equity", INITIAL_CASH)
        portfolio.setdefault("daily_pnl", 0.0)
        portfolio.setdefault(
            "daily_date",
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        return portfolio
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        log.error(
            "event=paper_portfolio_unavailable trace_id=%s "
            "reason_code=PORTFOLIO_STATE_INVALID error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        raise PortfolioStateError(
            "PORTFOLIO_STATE_INVALID",
            trace_id,
        ) from exc


def save_portfolio(pf: dict):
    trace_id = uuid4().hex[:16]
    try:
        if not isinstance(pf, dict) or not isinstance(pf.get("positions"), dict):
            raise ValueError("portfolio schema is invalid")
        PAPER_DIR.mkdir(parents=True, exist_ok=True)
        temporary = PORTFOLIO_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(pf, indent=2, default=str))
        temporary.replace(PORTFOLIO_FILE)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        log.error(
            "event=paper_portfolio_write_unavailable trace_id=%s "
            "reason_code=PORTFOLIO_WRITE_FAILED error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        raise PortfolioStateError("PORTFOLIO_WRITE_FAILED", trace_id) from exc


def _log_order(order: dict):
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    with open(ORDERS_FILE, "a") as f:
        f.write(json.dumps(order, default=str) + "\n")


def _log_journal(entry: dict):
    Path(JOURNAL_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _write_execution_audits(
    order: dict,
    journal_entry: dict,
    *,
    trace_id: str,
) -> list[str]:
    """Write secondary evidence after portfolio persistence and report failures."""
    failures: list[str] = []
    for name, writer, payload in (
        ("order", _log_order, order),
        ("journal", _log_journal, journal_entry),
    ):
        try:
            writer(payload)
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            failures.append(name)
            log.error(
                "event=paper_audit_write_failed trace_id=%s audit=%s "
                "reason_code=PAPER_AUDIT_WRITE_FAILED error_type=%s",
                trace_id,
                name,
                type(exc).__name__,
            )
    return failures


def _filled_result_with_audit(order: dict, journal_entry: dict) -> dict:
    """Preserve an authoritative fill while exposing secondary audit coverage."""
    trace_id = uuid4().hex[:16]
    failures = _write_execution_audits(
        order,
        journal_entry,
        trace_id=trace_id,
    )
    if failures:
        return {
            "status": "filled",
            **order,
            "audit_status": "PARTIAL",
            "audit_reason_code": "PAPER_AUDIT_WRITE_FAILED",
            "trace_id": trace_id,
            "audit_failures": failures,
        }
    return {
        "status": "filled",
        **order,
        "audit_status": "COMPLETED",
        "audit_reason_code": None,
        "audit_failures": [],
    }


def _load_report_prices() -> PriceSnapshot:
    """Load latest prices from reports for mark-to-market of all positions."""
    trace_id = uuid4().hex[:16]
    reports_dir = runtime_reports_dir()
    try:
        files = sorted(reports_dir.glob("report_*.json"), reverse=True)
        if not files:
            reason_code = "REPORT_PRICE_SNAPSHOT_MISSING"
            log.warning(
                "event=paper_prices_unavailable trace_id=%s reason_code=%s",
                trace_id,
                reason_code,
            )
            return PriceSnapshot(
                {},
                status="UNAVAILABLE",
                reason_code=reason_code,
                trace_id=trace_id,
            )
        report = json.loads(files[0].read_text())
        assets = report.get("assets")
        if not isinstance(assets, list):
            raise ValueError("report assets are invalid")
        prices: dict[str, float] = {}
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError("report asset is invalid")
            symbol = str(asset.get("symbol", "")).upper()
            raw_price = asset.get("current_price") or asset.get("price")
            if symbol and raw_price is not None and float(raw_price) > 0:
                prices[symbol] = float(raw_price)
        if not prices:
            raise ValueError("report contains no valid prices")
        return PriceSnapshot(
            prices,
            status="AVAILABLE",
            reason_code=None,
            trace_id=trace_id,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        reason_code = "PRICE_REPORT_INVALID"
        log.error(
            "event=paper_prices_unavailable trace_id=%s reason_code=%s "
            "error_type=%s",
            trace_id,
            reason_code,
            type(exc).__name__,
        )
        return PriceSnapshot(
            {},
            status="UNAVAILABLE",
            reason_code=reason_code,
            trace_id=trace_id,
        )


def get_portfolio_value(pf: dict, prices: dict) -> float:
    snapshot = _load_report_prices()
    supplied_prices = {
        str(symbol).upper(): float(price)
        for symbol, price in prices.items()
        if price is not None and float(price) > 0
    }
    all_prices = {**snapshot, **supplied_prices}
    positions_value = 0.0
    missing_symbols: list[str] = []
    for sym, pos in pf.get("positions", {}).items():
        price = all_prices.get(sym.upper())
        if price is None:
            missing_symbols.append(sym.upper())
            continue
        positions_value += pos["shares"] * price
    if missing_symbols:
        trace_id = snapshot.trace_id or uuid4().hex[:16]
        log.error(
            "event=paper_valuation_unavailable trace_id=%s "
            "reason_code=POSITION_PRICE_MISSING symbols=%s",
            trace_id,
            ",".join(sorted(missing_symbols)[:16]),
        )
        raise PortfolioValuationUnavailable(
            "POSITION_PRICE_MISSING",
            trace_id,
        )
    return pf["cash"] + positions_value


def _check_risk_limits(pf: dict, prices: dict) -> dict | None:
    """Return rejection dict if a risk limit is breached, else None."""
    try:
        equity = get_portfolio_value(pf, prices)
    except PortfolioValuationUnavailable as exc:
        return {
            "status": "unavailable",
            "reason": "Portfolio valuation unavailable; BUY blocked",
            "reason_code": exc.reason_code,
            "trace_id": exc.trace_id,
        }

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
    try:
        pf = load_portfolio()
    except PortfolioStateError as exc:
        return {
            "status": "unavailable",
            "reason": "Paper portfolio state unavailable",
            "reason_code": exc.reason_code,
            "trace_id": exc.trace_id,
        }

    symbol = str(signal.get("asset", signal.get("symbol", "???"))).upper()
    action = str(signal.get("action", "")).upper()
    normalized_prices = {
        str(key).upper(): value for key, value in prices.items()
    }
    current_price = normalized_prices.get(symbol)

    if current_price is None or current_price <= 0:
        trace_id = uuid4().hex[:16]
        log.warning(
            "event=paper_signal_unavailable trace_id=%s symbol=%s action=%s "
            "reason_code=SIGNAL_PRICE_UNAVAILABLE",
            trace_id,
            symbol,
            action,
        )
        return {
            "status": "unavailable",
            "reason": f"No price for {symbol}",
            "reason_code": "SIGNAL_PRICE_UNAVAILABLE",
            "trace_id": trace_id,
        }

    # Risk kill switches — only block BUY (SELL always allowed to reduce risk)
    if action == "BUY":
        risk_reject = _check_risk_limits(pf, normalized_prices)
        if risk_reject:
            save_portfolio(pf)  # Persist updated peak/daily fields
            log.warning("[paper] %s BUY blocked: %s", symbol, risk_reject["reason"])
            return risk_reject

    fill_price = current_price * (1 + SLIPPAGE) if action == "BUY" else current_price * (1 - SLIPPAGE)

    if action == "BUY":
        return _execute_buy(
            pf,
            symbol,
            fill_price,
            signal,
            normalized_prices,
        )
    elif action == "SELL":
        return _execute_sell(pf, symbol, fill_price, signal)
    else:
        return {"status": "skipped", "reason": f"Non-executable action: {action}"}


def _execute_buy(
    pf: dict,
    symbol: str,
    fill_price: float,
    signal: dict,
    market_prices: dict | None = None,
) -> dict:
    try:
        total_equity = get_portfolio_value(pf, market_prices or {})
    except PortfolioValuationUnavailable as exc:
        return {
            "status": "unavailable",
            "reason": "Existing position valuation unavailable; BUY blocked",
            "reason_code": exc.reason_code,
            "trace_id": exc.trace_id,
        }
    positions_value = total_equity - pf["cash"]
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

    journal_entry = {
        **order,
        "reasoning": signal.get("reasoning", "")[:300],
        "confidence": signal.get("confidence"),
    }

    log.info("[paper] BUY %s: %.6f shares @ $%.2f = $%.2f | cash=$%.2f",
             symbol, shares, fill_price, cost, pf["cash"])

    return _filled_result_with_audit(order, journal_entry)


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

    journal_entry = {
        **order,
        "reasoning": signal.get("reasoning", "")[:300],
        "confidence": signal.get("confidence"),
    }

    log.info("[paper] SELL %s: %.6f shares @ $%.2f = $%.2f | pnl=$%.2f | cash=$%.2f",
             symbol, shares, fill_price, proceeds, pnl, pf["cash"])

    return _filled_result_with_audit(order, journal_entry)


def check_stops(prices: dict) -> StopSweepResult:
    """Sweep open positions and expose missing-price coverage as PARTIAL."""
    trace_id = uuid4().hex[:16]
    pf = load_portfolio()
    closed: list[dict] = []
    audit_entries: list[tuple[dict, dict]] = []
    unavailable_symbols: list[str] = []
    normalized_prices = {
        str(symbol).upper(): value for symbol, value in prices.items()
    }

    for symbol in list(pf["positions"].keys()):
        pos = pf["positions"][symbol]
        price = normalized_prices.get(symbol.upper())
        if price is None or price <= 0:
            unavailable_symbols.append(symbol.upper())
            continue

        avg_cost = pos.get("avg_cost", 0)
        if not isinstance(avg_cost, (int, float)) or avg_cost <= 0:
            unavailable_symbols.append(symbol.upper())
            continue

        high = max(pos.get("highest_price", avg_cost), price)
        pos["highest_price"] = high

        gain_pct = (price - avg_cost) / avg_cost
        if gain_pct >= TRAIL_TRIGGER_PCT and not pos.get("trail_active"):
            pos["trail_active"] = True
            log.info(
                "event=paper_trailing_stop_activated symbol=%s gain_pct=%.6f",
                symbol,
                gain_pct,
            )

        if pos.get("trail_active"):
            new_trail = round(high * (1 - TRAIL_PCT), 6)
            existing = pos.get("trail_stop")
            if existing is None or new_trail > existing:
                pos["trail_stop"] = new_trail

        stop_loss = pos.get("stop_loss") or round(
            avg_cost * (1 - STOP_LOSS_DEFAULT_PCT),
            6,
        )
        trail_stop = pos.get("trail_stop")
        stop_hit = price <= stop_loss
        trail_hit = bool(
            pos.get("trail_active")
            and trail_stop
            and price <= trail_stop
        )
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
        closed.append(order)
        audit_entries.append(
            (order, {**order, "reasoning": f"auto: {exit_reason}"})
        )

    save_portfolio(pf)
    audit_failures: list[str] = []
    for order, journal_entry in audit_entries:
        audit_failures.extend(
            _write_execution_audits(
                order,
                journal_entry,
                trace_id=trace_id,
            )
        )
        log.info(
            "event=paper_stop_filled symbol=%s exit_reason=%s shares=%.8f "
            "fill_price=%.4f pnl=%.2f",
            order["symbol"],
            order["exit_reason"],
            order["shares"],
            order["fill_price"],
            order["pnl"],
        )
    if audit_failures:
        log.error(
            "event=paper_stop_audit_partial trace_id=%s "
            "reason_code=STOP_AUDIT_WRITE_FAILED failures=%s",
            trace_id,
            ",".join(audit_failures[:16]),
        )
        return StopSweepResult(
            closed,
            status="PARTIAL",
            reason_code="STOP_AUDIT_WRITE_FAILED",
            trace_id=trace_id,
            unavailable_symbols=tuple(sorted(set(unavailable_symbols))),
        )
    if unavailable_symbols:
        bounded = tuple(sorted(set(unavailable_symbols)))
        log.warning(
            "event=paper_stop_sweep_partial trace_id=%s "
            "reason_code=POSITION_PRICE_MISSING symbols=%s",
            trace_id,
            ",".join(bounded[:16]),
        )
        return StopSweepResult(
            closed,
            status="PARTIAL",
            reason_code="POSITION_PRICE_MISSING",
            trace_id=trace_id,
            unavailable_symbols=bounded,
        )
    return StopSweepResult(
        closed,
        status="COMPLETED",
        reason_code=None,
        trace_id=trace_id,
    )


def execute_batch(signals: list[dict], price_map: dict) -> dict:
    """Execute signals while keeping unavailable results separate from skips."""
    trace_id = uuid4().hex[:16]
    normalized_price_map = {
        str(symbol).upper(): price for symbol, price in price_map.items()
    }
    try:
        portfolio_summary = get_summary()
    except PortfolioStateError as exc:
        return {
            "status": "UNAVAILABLE",
            "reason_code": exc.reason_code,
            "trace_id": exc.trace_id,
            "executed": [],
            "skipped": [],
            "unavailable": [],
            "portfolio": None,
        }

    results = {
        "status": "COMPLETED",
        "reason_code": None,
        "trace_id": trace_id,
        "executed": [],
        "skipped": [],
        "unavailable": [],
        "portfolio": portfolio_summary,
    }

    for signal in signals:
        symbol = str(signal.get("asset", signal.get("symbol", ""))).upper()
        action = signal.get("action", "BUY")
        price = normalized_price_map.get(symbol)
        if not price:
            results["unavailable"].append(
                {
                    "symbol": symbol,
                    "reason_code": "SIGNAL_PRICE_UNAVAILABLE",
                }
            )
            continue

        try:
            result = execute_signal(signal, normalized_price_map)
            if result.get("status") == "filled":
                results["executed"].append(
                    {"symbol": symbol, "action": action, "result": result}
                )
                if result.get("audit_status") == "PARTIAL":
                    results["unavailable"].append(
                        {
                            "symbol": symbol,
                            "reason_code": result.get(
                                "audit_reason_code",
                                "PAPER_AUDIT_WRITE_FAILED",
                            ),
                            "trace_id": result.get("trace_id"),
                        }
                    )
            elif result.get("status") == "unavailable":
                results["unavailable"].append(
                    {
                        "symbol": symbol,
                        "reason_code": result.get(
                            "reason_code",
                            "PAPER_EXECUTION_UNAVAILABLE",
                        ),
                        "trace_id": result.get("trace_id"),
                    }
                )
            else:
                results["skipped"].append(
                    {
                        "symbol": symbol,
                        "reason": result.get("reason", "unknown"),
                    }
                )
        except (
            PaperPortfolioError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as exc:
            results["unavailable"].append(
                {
                    "symbol": symbol,
                    "reason_code": getattr(
                        exc,
                        "reason_code",
                        "PAPER_EXECUTION_FAILED",
                    ),
                    "trace_id": getattr(exc, "trace_id", trace_id),
                    "error_type": type(exc).__name__,
                }
            )

    try:
        results["portfolio"] = get_summary()
    except PortfolioStateError as exc:
        results["portfolio"] = None
        results["unavailable"].append(
            {
                "symbol": None,
                "reason_code": exc.reason_code,
                "trace_id": exc.trace_id,
            }
        )

    if results["unavailable"]:
        results["reason_code"] = "PAPER_BATCH_HAS_UNAVAILABLE_ITEMS"
        results["status"] = (
            "PARTIAL" if results["executed"] else "UNAVAILABLE"
        )
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
    except (OSError, UnicodeError):
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
