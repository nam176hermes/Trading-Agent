"""
Order execution engine with state machine.
Handles: order placement → fill tracking → PnL update → DB recording.
"""

import uuid
import time
import logging
from typing import Optional, List, Dict
from dataclasses import dataclass
from datetime import datetime, timezone

from .adapter import (
    ExchangeAdapter, ExchangeID, OrderSide, OrderType,
    OrderStatus, OrderRequest, OrderResult
)
from db import repository as db

log = logging.getLogger(__name__)


@dataclass
class ExecutionReport:
    """Complete report after order execution."""
    order_id: int                    # DB order ID
    client_order_id: str
    exchange_order_id: str
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: float
    filled_quantity: float
    avg_fill_price: Optional[float]
    commission: Optional[float]
    slippage_pct: Optional[float]
    duration_ms: float
    error: Optional[str] = None


class OrderExecutor:
    """
    Executes orders on exchange with full lifecycle tracking.

    State machine:
        PENDING → PLACED → PARTIALLY_FILLED → FILLED
                        → REJECTED
                        → CANCELLED
    """

    POLL_INTERVAL = 1.0      # seconds between status checks
    POLL_TIMEOUT = 30.0      # max seconds to wait for fill
    IDEMPOTENCY_PREFIX = "ta"  # trading-agent

    def __init__(self, adapter: ExchangeAdapter):
        self.adapter = adapter
        self.exchange_id = adapter.exchange_id.value

    # ── Public API ─────────────────────────────────────────────

    def execute(self, req: OrderRequest) -> ExecutionReport:
        """
        Execute an order end-to-end:
        1. Generate idempotency key
        2. Check for duplicate
        3. Record PENDING in DB
        4. Place on exchange
        5. Poll until terminal state
        6. Update DB
        7. Update position
        8. Return ExecutionReport
        """
        t0 = time.time()

        # 1. Idempotency key
        if not req.client_order_id:
            req.client_order_id = self._make_client_id(req.symbol)

        req.idempotency_key = req.client_order_id  # Use same for idempotency

        # 2. Check duplicate
        existing = db.get_order_by_idempotency_key(req.idempotency_key)
        if existing:
            log.warning("Duplicate order blocked: %s (existing #%d)",
                       req.idempotency_key, existing["id"])
            return ExecutionReport(
                order_id=existing["id"],
                client_order_id=existing["client_order_id"],
                exchange_order_id=existing.get("exchange_order_id", ""),
                symbol=existing["symbol"],
                side=existing["side"],
                order_type=existing["order_type"],
                status=existing["status"],
                quantity=existing["quantity"],
                filled_quantity=existing.get("filled_quantity", 0),
                avg_fill_price=existing.get("avg_fill_price"),
                commission=existing.get("commission"),
                slippage_pct=None,
                duration_ms=(time.time() - t0) * 1000,
                error="Duplicate prevented by idempotency",
            )

        # 3. Record PENDING
        order_id = db.insert_order(
            client_order_id=req.client_order_id,
            exchange=self.exchange_id,
            symbol=req.symbol,
            side=req.side.value,
            order_type=req.order_type.value,
            quantity=req.quantity,
            price=req.price,
            stop_price=req.stop_price,
            idempotency_key=req.idempotency_key,
            reduce_only=req.reduce_only,
        )

        # 4. Place on exchange
        try:
            result = self.adapter.create_order(req)
        except Exception as e:
            db.update_order_status(order_id, "rejected", error_message=str(e))
            return ExecutionReport(
                order_id=order_id,
                client_order_id=req.client_order_id,
                exchange_order_id="",
                symbol=req.symbol,
                side=req.side.value,
                order_type=req.order_type.value,
                status="rejected",
                quantity=req.quantity,
                filled_quantity=0,
                avg_fill_price=None,
                commission=None,
                slippage_pct=None,
                duration_ms=(time.time() - t0) * 1000,
                error=str(e),
            )

        db.update_order_status(
            order_id, "open",
            exchange_order_id=result.order_id,
            raw_json=result.raw,
        )

        # 5. Poll until terminal
        result = self._poll_until_terminal(result.order_id, req.symbol)
        if not result:
            # Poll timeout — use last known state
            status = "open"
            return ExecutionReport(
                order_id=order_id, client_order_id=req.client_order_id,
                exchange_order_id="", symbol=req.symbol, side=req.side.value,
                order_type=req.order_type.value, status=status,
                quantity=req.quantity, filled_quantity=0, avg_fill_price=None,
                commission=None, slippage_pct=None,
                duration_ms=(time.time() - t0) * 1000,
                error="Order status poll timeout",
            )

        # 6. Update DB
        status = result.status.value
        db.update_order_status(
            order_id, status,
            filled_quantity=result.filled_quantity,
            avg_fill_price=result.avg_fill_price,
            commission=result.commission,
            commission_asset=result.commission_asset,
        )

        # 7. Record fills as trades
        if result.filled_quantity > 0 and result.avg_fill_price:
            db.insert_trade(
                order_id=order_id,
                exchange_order_id=result.order_id,
                symbol=req.symbol,
                side=req.side.value,
                quantity=result.filled_quantity,
                price=result.avg_fill_price,
                commission=result.commission,
                commission_asset=result.commission_asset,
            )

        # 8. Update position
        self._update_position(req, result)

        # Compute slippage
        slippage = self._calc_slippage(req, result)

        duration_ms = (time.time() - t0) * 1000
        log.info("Order executed: %s %s %s %.4f @ %s | fill=%.4f | slip=%.3f%% | %dms",
                 req.symbol, req.side.value, req.order_type.value,
                 req.quantity, result.avg_fill_price,
                 result.filled_quantity, slippage or 0, int(duration_ms))

        return ExecutionReport(
            order_id=order_id,
            client_order_id=req.client_order_id,
            exchange_order_id=result.order_id,
            symbol=req.symbol,
            side=req.side.value,
            order_type=req.order_type.value,
            status=status,
            quantity=req.quantity,
            filled_quantity=result.filled_quantity,
            avg_fill_price=result.avg_fill_price,
            commission=result.commission,
            slippage_pct=slippage,
            duration_ms=duration_ms,
        )

    def cancel_order(self, order_id: str, symbol: str) -> ExecutionReport:
        """Cancel an open order."""
        t0 = time.time()
        result = self.adapter.cancel_order(order_id, symbol)

        # Find DB record
        existing = db.get_order_by_client_id(order_id)
        db_id = existing["id"] if existing else 0

        if existing:
            db.update_order_status(db_id, "canceled",
                                  filled_quantity=result.filled_quantity)

        return ExecutionReport(
            order_id=db_id,
            client_order_id=order_id,
            exchange_order_id=result.order_id,
            symbol=symbol,
            side=result.side.value,
            order_type=result.order_type.value,
            status="canceled",
            quantity=result.quantity,
            filled_quantity=result.filled_quantity,
            avg_fill_price=result.avg_fill_price,
            commission=None,
            slippage_pct=None,
            duration_ms=(time.time() - t0) * 1000,
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open orders from DB."""
        return db.get_open_orders(symbol)

    # ── Internal ───────────────────────────────────────────────

    def _make_client_id(self, symbol: str) -> str:
        """Generate a unique client order ID from symbol, UTC time, and nonce."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        short = uuid.uuid4().hex[:8]
        return f"{self.IDEMPOTENCY_PREFIX}_{symbol}_{ts}_{short}"

    def _poll_until_terminal(self, exchange_order_id: str, symbol: str) -> OrderResult:
        """Poll order status until it reaches a terminal state."""
        terminal = {OrderStatus.CLOSED, OrderStatus.CANCELED,
                    OrderStatus.EXPIRED, OrderStatus.REJECTED}
        t0 = time.time()

        while (time.time() - t0) < self.POLL_TIMEOUT:
            result = self.adapter.fetch_order(exchange_order_id, symbol)
            if result.status in terminal:
                return result
            time.sleep(self.POLL_INTERVAL)

        # Timeout — return last known state
        log.warning("Order %s poll timeout after %.1fs, status=%s",
                   exchange_order_id, self.POLL_TIMEOUT, result.status)
        return result

    def _update_position(self, req: OrderRequest, result: OrderResult):
        """Update position in DB after fill."""
        if result.filled_quantity <= 0:
            return

        current = db.get_position(req.symbol)
        old_qty = current["quantity"] if current else 0
        old_entry = current["avg_entry_price"] if current else None
        old_realized = current["realized_pnl"] if current else 0

        if req.side == OrderSide.BUY:
            new_qty = old_qty + result.filled_quantity
            # Weighted average entry
            if old_entry and old_qty > 0:
                new_entry = ((old_qty * old_entry) +
                            (result.filled_quantity * (result.avg_fill_price or 0))) / new_qty
            else:
                new_entry = result.avg_fill_price
            realized = old_realized
        else:  # SELL
            new_qty = old_qty - result.filled_quantity
            new_entry = old_entry if new_qty > 0 else None
            # Realized PnL on sell
            if old_entry and old_qty > 0:
                sold_qty = min(result.filled_quantity, old_qty)
                realized = old_realized + sold_qty * ((result.avg_fill_price or 0) - old_entry)
            else:
                realized = old_realized

        db.upsert_position(
            exchange=self.exchange_id,
            symbol=req.symbol,
            quantity=new_qty,
            avg_entry_price=new_entry,
            current_price=result.avg_fill_price,
            unrealized_pnl=0,  # Will be recalculated on next price update
            realized_pnl=realized,
            stop_loss=(result.avg_fill_price or 0) * (1 - 0.05),       # 5% stop
            take_profit=(result.avg_fill_price or 0) * (1 + 0.10),     # 10% TP
            trailing_stop=1,
            trailing_distance_pct=0.05,
            highest_price=result.avg_fill_price,
        )

    def _calc_slippage(self, req: OrderRequest, result: OrderResult) -> Optional[float]:
        """Calculate slippage as % deviation from expected price."""
        if not result.avg_fill_price or result.filled_quantity <= 0:
            return None

        if req.order_type == OrderType.MARKET:
            # For market orders, compare to current price (use fill price as reference)
            # Slippage is minimal for market — just report 0
            return 0.0

        if req.price:
            expected = req.price
            actual = result.avg_fill_price
            return ((actual - expected) / expected) * 100

        return None
