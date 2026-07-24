"""Exchange module — CCXT adapter, order execution, WebSocket feed, API key management."""

from .adapter import (
    ExchangeAdapter, ExchangeID, OrderSide, OrderType,
    OrderStatus, OrderRequest, OrderResult, Balance, Ticker,
)
from .executor import OrderExecutor, ExecutionReport
from .secrets import load_keys, get_exchange_credentials, add_key, remove_key, load_secrets_into_env, set_env_secrets

# WebSocket feed — ws_stream.py is the canonical WebSocket client (Kraken via CCXT Pro).
# The old Binance-only ws_feed.py has been archived as ws_feed_old.py.
import sys
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from ws_stream import KrakenWebSocket, HAS_CCXT_PRO
from .ws_feed_old import PriceFeed

__all__ = [
    "ExchangeAdapter", "ExchangeID", "OrderSide", "OrderType",
    "OrderStatus", "OrderRequest", "OrderResult", "Balance", "Ticker",
    "OrderExecutor", "ExecutionReport",
    "KrakenWebSocket", "HAS_CCXT_PRO",
    "PriceFeed",
    "load_keys", "get_exchange_credentials", "add_key", "remove_key",
    "load_secrets_into_env", "set_env_secrets",
]
