"""Database module — SQLite schema and repository."""

from .schema import SCHEMA
from .repository import (
    get_db, transaction,
    insert_order, update_order_status, get_order_by_client_id,
    get_order_by_idempotency_key, get_open_orders, get_recent_orders,
    insert_trade, get_recent_trades,
    upsert_position, get_positions, get_position, get_pnl_summary,
    insert_signal, get_recent_signals,
    insert_equity_snapshot, get_equity_history,
    insert_alert, get_unacknowledged_alerts, acknowledge_alert,
    get_state, set_state,
)

__all__ = [
    "SCHEMA", "get_db", "transaction",
    "insert_order", "update_order_status", "get_order_by_client_id",
    "get_order_by_idempotency_key", "get_open_orders", "get_recent_orders",
    "insert_trade", "get_recent_trades",
    "upsert_position", "get_positions", "get_position", "get_pnl_summary",
    "insert_signal", "get_recent_signals",
    "insert_equity_snapshot", "get_equity_history",
    "insert_alert", "get_unacknowledged_alerts", "acknowledge_alert",
    "get_state", "set_state",
]
