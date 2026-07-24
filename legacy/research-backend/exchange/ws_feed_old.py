"""
Real-time price feed via Binance WebSocket.
Replaces REST polling with live ticker stream.
Falls back to REST if WebSocket disconnects.
"""

import json
import threading
import time
import logging
from typing import Dict, Optional, Callable, Set
from dataclasses import dataclass, field

from websocket import WebSocketApp

log = logging.getLogger(__name__)


@dataclass
class PriceTick:
    symbol: str
    price: float
    change_pct_24h: float
    volume_24h: float
    high_24h: float
    low_24h: float
    timestamp: float = field(default_factory=time.time)


class PriceFeed:
    """
    Binance WebSocket price feed with in-memory cache.
    Thread-safe: prices can be read from any thread.
    """

    RECONNECT_DELAY = 3.0
    WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(self, symbols: Optional[Set[str]] = None):
        self._symbols: Set[str] = set(symbols) if symbols else set()
        self._prices: Dict[str, PriceTick] = {}
        self._lock = threading.Lock()
        self._ws: Optional[WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._on_tick: Optional[Callable] = None
        self._fallback_fn: Optional[Callable] = None  # REST fallback for get_price

    # ── Public API ─────────────────────────────────────────────

    def start(self):
        """Start WebSocket connection in background thread."""
        if not self._symbols:
            log.warning("PriceFeed: no symbols configured, not starting")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="price-feed")
        self._thread.start()
        log.info("PriceFeed started for %d symbols", len(self._symbols))

    def stop(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            self._ws.close()

    @property
    def connected(self) -> bool:
        return self._connected

    def set_symbols(self, symbols: Set[str]):
        """Update tracked symbols (triggers reconnect)."""
        with self._lock:
            self._symbols = set(symbols)
        if self._connected:
            self._reconnect()

    def on_tick(self, callback: Callable):
        """Register callback for every price update: callback(PriceTick)."""
        self._on_tick = callback

    def set_fallback(self, fn: Callable):
        """Set REST fallback for get_price() when WebSocket is down."""
        self._fallback_fn = fn

    def get_price(self, symbol: str) -> Optional[PriceTick]:
        """Get latest price for a symbol. Falls back to REST if WebSocket down."""
        with self._lock:
            tick = self._prices.get(symbol)
        if tick:
            return tick

        # Fallback to REST
        if self._fallback_fn:
            try:
                return self._fallback_fn(symbol)
            except Exception as e:
                log.warning("PriceFeed fallback failed for %s: %s", symbol, e)
        return None

    def get_all_prices(self) -> Dict[str, PriceTick]:
        """Get all cached prices."""
        with self._lock:
            return dict(self._prices)

    # ── Internal ───────────────────────────────────────────────

    def _run(self):
        """Main WebSocket loop with auto-reconnect."""
        while self._running:
            try:
                self._connect()
                self._ws.run_forever()
            except Exception as e:
                log.error("PriceFeed error: %s, reconnecting in %.1fs", e, self.RECONNECT_DELAY)
                self._connected = False
                time.sleep(self.RECONNECT_DELAY)

    def _connect(self):
        """Build combined ticker stream URL and connect."""
        streams = [f"{s.lower()}usdt@ticker" for s in sorted(self._symbols)]
        url = f"{self.WS_URL}/{'/'.join(streams)}"

        self._ws = WebSocketApp(
            url,
            on_message=self._on_message,
            on_open=self._on_open,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        log.info("PriceFeed connecting to %d streams", len(streams))

    def _reconnect(self):
        """Force reconnect (e.g., after symbol change)."""
        if self._ws:
            self._ws.close()

    def _on_open(self, ws):
        self._connected = True
        log.info("PriceFeed connected")

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        log.info("PriceFeed disconnected: %s", close_msg)

    def _on_error(self, ws, error):
        log.error("PriceFeed WebSocket error: %s", error)

    def _on_message(self, ws, message):
        """Parse ticker message and update cache."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        symbol = data.get("s")
        if not symbol:
            return

        # Strip quote asset (USDT) for consistent lookup
        base = symbol.replace("USDT", "").replace("USDC", "").replace("BUSD", "")

        tick = PriceTick(
            symbol=base,
            price=float(data.get("c", 0)),
            change_pct_24h=float(data.get("P", 0)),
            volume_24h=float(data.get("v", 0)),
            high_24h=float(data.get("h", 0)),
            low_24h=float(data.get("l", 0)),
        )

        with self._lock:
            self._prices[base] = tick

        # Log first tick for debugging
        if len(self._prices) <= 2:
            log.info("PriceFeed tick: %s $%.2f (%.2f%%)", base, tick.price, tick.change_pct_24h)
