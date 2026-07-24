"""
SQLite repository — transactional data access for trading agent.
All writes go through this layer. All reads are queryable.
"""

import sqlite3
import json
import os
import threading
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from .schema import SCHEMA, QUERIES, run_migrations
from runtime_paths import data_root

DB_PATH = str(data_root() / "memory" / "trading.db")

_local = threading.local()


def get_db() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.executescript(SCHEMA)
        run_migrations(DB_PATH)
    return _local.conn


@contextmanager
def transaction():
    """Context manager for atomic transactions."""
    db = get_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


# ── Orders ────────────────────────────────────────────────────

def insert_order(
    client_order_id: str,
    exchange: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    idempotency_key: Optional[str] = None,
    strategy: Optional[str] = None,
    signal_id: Optional[str] = None,
    reduce_only: bool = False,
) -> int:
    """Insert a new order. Returns order ID. Raises if idempotency key exists."""
    with transaction() as db:
        if idempotency_key:
            existing = db.execute(
                "SELECT id FROM orders WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                raise ValueError(f"Duplicate order: idempotency_key={idempotency_key} already exists as order #{existing['id']}")

        cur = db.execute(
            """INSERT INTO orders (client_order_id, exchange, symbol, side, order_type,
               quantity, price, stop_price, idempotency_key, strategy, signal_id, reduce_only)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_order_id, exchange, symbol, side, order_type,
             quantity, price, stop_price, idempotency_key, strategy, signal_id, int(reduce_only))
        )
        return cur.lastrowid


def update_order_status(
    order_id: int,
    status: str,
    exchange_order_id: Optional[str] = None,
    filled_quantity: Optional[float] = None,
    avg_fill_price: Optional[float] = None,
    commission: Optional[float] = None,
    commission_asset: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_json: Optional[Dict] = None,
):
    """Update order after exchange response."""
    with transaction() as db:
        db.execute(
            """UPDATE orders SET
               status = ?, exchange_order_id = COALESCE(?, exchange_order_id),
               filled_quantity = COALESCE(?, filled_quantity),
               avg_fill_price = COALESCE(?, avg_fill_price),
               commission = COALESCE(?, commission),
               commission_asset = COALESCE(?, commission_asset),
               error_message = COALESCE(?, error_message),
               raw_json = COALESCE(?, raw_json),
               updated_at = datetime('now')
               WHERE id = ?""",
            (status, exchange_order_id, filled_quantity, avg_fill_price,
             commission, commission_asset, error_message,
             json.dumps(raw_json) if raw_json else None, order_id)
        )


def get_order_by_client_id(client_order_id: str) -> Optional[Dict]:
    """Find order by client_order_id."""
    row = get_db().execute(
        "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
    ).fetchone()
    return dict(row) if row else None


def get_order_by_idempotency_key(key: str) -> Optional[Dict]:
    """Check if an idempotency key was already used."""
    row = get_db().execute(
        "SELECT * FROM orders WHERE idempotency_key = ?", (key,)
    ).fetchone()
    return dict(row) if row else None


def get_open_orders(symbol: Optional[str] = None) -> List[Dict]:
    """Get all open/pending orders."""
    if symbol:
        rows = get_db().execute(
            "SELECT * FROM orders WHERE status IN ('pending','open','partially_filled') AND symbol = ? ORDER BY created_at DESC",
            (symbol,)
        ).fetchall()
    else:
        rows = get_db().execute(
            "SELECT * FROM orders WHERE status IN ('pending','open','partially_filled') ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_orders(limit: int = 50, symbol: Optional[str] = None) -> List[Dict]:
    """Get recent orders, optionally filtered by symbol."""
    if symbol:
        rows = get_db().execute(
            "SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit)
        ).fetchall()
    else:
        rows = get_db().execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Trades (fills) ────────────────────────────────────────────

def insert_trade(
    order_id: int,
    exchange_order_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    commission: Optional[float] = None,
    commission_asset: Optional[str] = None,
    trade_timestamp: Optional[str] = None,
) -> int:
    """Record an individual fill."""
    with transaction() as db:
        cur = db.execute(
            """INSERT INTO trades (order_id, exchange_order_id, symbol, side,
               quantity, price, commission, commission_asset, trade_timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, exchange_order_id, symbol, side, quantity, price,
             commission, commission_asset, trade_timestamp or "datetime('now')")
        )
        return cur.lastrowid


def get_recent_trades(limit: int = 20) -> List[Dict]:
    """Get recent fills with order context."""
    rows = get_db().execute(
        """SELECT o.symbol, o.side, o.order_type, t.quantity, t.price,
                  t.commission, t.trade_timestamp
           FROM trades t
           JOIN orders o ON t.order_id = o.id
           ORDER BY t.trade_timestamp DESC
           LIMIT ?""",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Positions ─────────────────────────────────────────────────

def upsert_position(
    exchange: str,
    symbol: str,
    quantity: float,
    avg_entry_price: Optional[float] = None,
    current_price: Optional[float] = None,
    unrealized_pnl: float = 0,
    realized_pnl: float = 0,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    trailing_stop: int = 0,
    trailing_distance_pct: Optional[float] = None,
    highest_price: Optional[float] = None,
):
    """Insert or update a position."""
    with transaction() as db:
        db.execute(
            QUERIES["upsert_position"],
            (exchange, symbol, quantity, avg_entry_price, current_price,
             unrealized_pnl, realized_pnl,
             stop_loss, take_profit, trailing_stop, trailing_distance_pct,
             highest_price)
        )


def get_positions() -> List[Dict]:
    """Get all non-zero positions."""
    rows = get_db().execute(
        "SELECT * FROM positions WHERE quantity != 0 ORDER BY symbol"
    ).fetchall()
    return [dict(r) for r in rows]


def get_position(symbol: str) -> Optional[Dict]:
    """Get a specific position."""
    row = get_db().execute(
        "SELECT * FROM positions WHERE symbol = ?", (symbol,)
    ).fetchone()
    return dict(row) if row else None


def get_pnl_summary() -> Dict:
    """Get aggregate P&L."""
    row = get_db().execute(QUERIES["get_pnl_summary"]).fetchone()
    return dict(row) if row else {}


# ── Signals ───────────────────────────────────────────────────

def insert_signal(
    symbol: str,
    direction: str,
    confidence: Optional[float] = None,
    strategy: Optional[str] = None,
    source: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> int:
    """Record a signal from the research pipeline."""
    with transaction() as db:
        cur = db.execute(
            """INSERT INTO signals (symbol, direction, confidence, strategy, source, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, direction, confidence, strategy, source,
             json.dumps(metadata) if metadata else None)
        )
        return cur.lastrowid


def get_recent_signals(limit: int = 50) -> List[Dict]:
    """Get recent unprocessed signals."""
    rows = get_db().execute(
        "SELECT * FROM signals WHERE processed = 0 ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_signal_processed(signal_id: int):
    """Mark a signal as processed."""
    with transaction() as db:
        db.execute("UPDATE signals SET processed = 1 WHERE id = ?", (signal_id,))


# ── Equity Snapshots ──────────────────────────────────────────

def insert_equity_snapshot(
    total_equity: float,
    cash: float,
    positions_value: float,
    unrealized_pnl: float,
    realized_pnl: float,
    daily_pnl: Optional[float] = None,
    drawdown_pct: Optional[float] = None,
):
    """Record periodic equity snapshot."""
    with transaction() as db:
        db.execute(
            """INSERT INTO equity_snapshots
               (total_equity, cash, positions_value, unrealized_pnl, realized_pnl, daily_pnl, drawdown_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (total_equity, cash, positions_value, unrealized_pnl, realized_pnl, daily_pnl, drawdown_pct)
        )


def get_equity_history(limit: int = 200) -> List[Dict]:
    """Get equity curve data."""
    rows = get_db().execute(
        "SELECT * FROM equity_snapshots ORDER BY timestamp ASC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Alerts ────────────────────────────────────────────────────

def insert_alert(
    alert_type: str,
    message: str,
    severity: str = "info",
    metadata: Optional[Dict] = None,
) -> int:
    """Create an alert."""
    with transaction() as db:
        cur = db.execute(
            """INSERT INTO alerts (type, severity, message, metadata_json)
               VALUES (?, ?, ?, ?)""",
            (alert_type, severity, message, json.dumps(metadata) if metadata else None)
        )
        return cur.lastrowid


def get_unacknowledged_alerts() -> List[Dict]:
    """Get alerts that haven't been acknowledged."""
    rows = get_db().execute(
        "SELECT * FROM alerts WHERE acknowledged = 0 ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def acknowledge_alert(alert_id: int):
    """Mark an alert as acknowledged."""
    with transaction() as db:
        db.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))


# ── Agent State ───────────────────────────────────────────────

def get_state(key: str) -> Optional[str]:
    """Get a persistent state value."""
    row = get_db().execute(
        "SELECT value FROM agent_state WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_state(key: str, value: str):
    """Set a persistent state value."""
    with transaction() as db:
        db.execute(
            """INSERT INTO agent_state (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value)
        )

# ── Slippage ──────────────────────────────────────────────────

def get_slippage_history(limit: int = 50) -> List[Dict]:
    """Get recent orders with computed slippage."""
    rows = get_db().execute(
        """SELECT symbol, side, order_type, quantity,
                  price as expected_price,
                  avg_fill_price as fill_price,
                  CASE WHEN price > 0 AND avg_fill_price > 0
                       THEN ROUND((avg_fill_price - price) / price * 100, 4)
                       ELSE 0 END as slippage_pct,
                  created_at
           FROM orders
           WHERE status = 'filled' AND price > 0 AND avg_fill_price > 0
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_average_slippage() -> Optional[float]:
    """Get average slippage across all filled orders."""
    row = get_db().execute(
        """SELECT AVG((avg_fill_price - price) / price * 100) as avg_slippage
           FROM orders
           WHERE status = 'filled' AND price > 0 AND avg_fill_price > 0
             AND created_at >= datetime('now', '-7 days')"""
    ).fetchone()
    return round(row["avg_slippage"], 4) if row and row["avg_slippage"] else None
