"""
ws_stream.py — WebSocket real-time streaming for Kraken via CCXT Pro.

Provides a KrakenWebSocket class that wraps CCXT Pro's async watch_*
methods for real-time ticker and OHLCV data. Falls back gracefully
when ccxt.pro is not installed.

Usage:
    stream = KrakenWebSocket()
    await stream.start_stream(
        symbols=["BTC/USDT", "ETH/USDT"],
        callback=lambda tick: print(tick),
        timeframe="1m",
    )
    # ... run until ...
    await stream.stop_stream()
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from runtime_paths import data_root

log = logging.getLogger(__name__)

# ── Optional ccxt.pro import ──────────────────────────────────────

try:
    import ccxt.pro as ccxtpro

    HAS_CCXT_PRO = True
except ImportError:
    HAS_CCXT_PRO = False
    ccxtpro = None  # type: ignore[assignment]

# ── Constants ─────────────────────────────────────────────────────

EXCHANGE_ID = "kraken"
RECONNECT_DELAY = 5.0  # seconds between reconnect attempts


class KrakenWebSocket:
    """Async WebSocket streaming client for Kraken via CCXT Pro.

    Wraps ccxt.pro.kraken's watch_ticker and watch_ohlcv for
    real-time market data.  All methods are async — use inside an
    asyncio event loop.
    """

    def __init__(self):
        self._exchange: Optional[ccxtpro.Exchange] = None
        self._tasks: list[asyncio.Task] = []
        self._running = False

    # ── Connection management ──────────────────────────────────

    async def connect(self) -> bool:
        """Create and initialise the CCXT Pro Kraken exchange instance.

        Returns:
            True if connected successfully, False otherwise.
        """
        if not HAS_CCXT_PRO:
            log.error("ccxt.pro is not installed — install with: pip install ccxt[ccxtpro]")
            return False

        try:
            self._exchange = getattr(ccxtpro, "kraken")({
                "enableRateLimit": True,
                "newUpdates": True,
            })
            # Quick connectivity check — load markets to verify API reachability
            await self._exchange.load_markets()
            log.info("KrakenWebSocket connected — %d markets loaded",
                     len(self._exchange.markets))
            return True
        except Exception as e:
            log.error("KrakenWebSocket connect failed: %s", e)
            self._exchange = None
            return False

    async def close(self):
        """Close the underlying exchange connection and cancel all tasks."""
        self._running = False

        # Cancel all stream tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

        # Close exchange
        if self._exchange is not None:
            try:
                await self._exchange.close()
                log.info("KrakenWebSocket closed")
            except Exception as e:
                log.warning("Error closing exchange: %s", e)
            finally:
                self._exchange = None

    # ── Public API ─────────────────────────────────────────────

    async def start_stream(
        self,
        symbols: list[str],
        callback: Callable[[dict], None],
        *,
        timeframe: str = "1m",
        stream_type: str = "ticker",
    ):
        """Start a real-time WebSocket stream for the given symbols.

        Args:
            symbols: List of CCXT market symbols, e.g. ["BTC/USDT", "ETH/USDT"].
            callback: Called with each tick/candle dict as it arrives.
            timeframe: OHLCV bar interval (only used when stream_type="ohlcv").
            stream_type: "ticker" for watch_ticker, "ohlcv" for watch_ohlcv.
        """
        if not self._running:
            if not await self.connect():
                log.error("Cannot start stream — connection failed")
                return
            self._running = True

        if stream_type == "ohlcv":
            task = asyncio.create_task(
                self._stream_ohlcv(symbols, callback, timeframe),
                name="ws_ohlcv",
            )
        else:
            task = asyncio.create_task(
                self._stream_tickers(symbols, callback),
                name="ws_ticker",
            )

        self._tasks.append(task)
        log.info("Started %s stream for %s", stream_type, symbols)

    async def stop_stream(self):
        """Gracefully stop all streams and close the connection."""
        await self.close()

    # ── Internal stream loops ──────────────────────────────────

    async def _stream_tickers(self, symbols: list[str], callback: Callable[[dict], None]):
        """Watch ticker updates for all symbols, calling callback on each."""
        ex = self._exchange
        if ex is None:
            return

        while self._running:
            try:
                for symbol in symbols:
                    ticker = await ex.watch_ticker(symbol)
                    callback(ticker)
            except asyncio.CancelledError:
                log.debug("Ticker stream cancelled")
                break
            except Exception as e:
                log.warning("Ticker stream error: %s — reconnecting in %.0fs", e, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _stream_ohlcv(
        self,
        symbols: list[str],
        callback: Callable[[dict], None],
        timeframe: str,
    ):
        """Watch OHLCV candle updates for all symbols, calling callback on each."""
        ex = self._exchange
        if ex is None:
            return

        while self._running:
            try:
                for symbol in symbols:
                    candles = await ex.watch_ohlcv(symbol, timeframe)
                    if candles and len(candles) > 0:
                        # candles is a list of [timestamp, open, high, low, close, volume]
                        latest = candles[-1]
                        candle_dict = {
                            "symbol": symbol,
                            "timestamp": datetime.fromtimestamp(
                                latest[0] / 1000, tz=timezone.utc
                            ).isoformat(),
                            "open": latest[1],
                            "high": latest[2],
                            "low": latest[3],
                            "close": latest[4],
                            "volume": latest[5],
                        }
                        callback(candle_dict)
            except asyncio.CancelledError:
                log.debug("OHLCV stream cancelled")
                break
            except Exception as e:
                log.warning("OHLCV stream error: %s — reconnecting in %.0fs", e, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    # ── Properties ─────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._exchange is not None and self._running

    @property
    def is_available(self) -> bool:
        return HAS_CCXT_PRO


# ── Standalone runner ─────────────────────────────────────────────



# ── BinanceWebSocket ─────────────────────────────────────────────

class BinanceWebSocket:
    """Real-time price feed for Binance via CCXT Pro.

    Streams ticker prices and writes them to live_prices.json so that
    paper_trader.check_stops() and execute_live.py can act on live prices
    instead of last-candle closes.

    Usage:
        feed = BinanceWebSocket()
        await feed.start_stream(["BTC/USDT", "ETH/USDT"], on_tick)
        # ... later ...
        await feed.stop_stream()
    """

    LIVE_PRICES_PATH = data_root() / "live_prices.json"

    def __init__(self):
        self._exchange = None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._prices: dict[str, float] = {}

    async def connect(self) -> bool:
        if not HAS_CCXT_PRO:
            log.error("ccxt.pro not installed — pip install ccxt")
            return False
        try:
            self._exchange = getattr(ccxtpro, "binance")({
                "enableRateLimit": True,
                "newUpdates": True,
            })
            await self._exchange.load_markets()
            log.info("BinanceWebSocket connected — %d markets", len(self._exchange.markets))
            return True
        except Exception as e:
            log.error("BinanceWebSocket connect failed: %s", e)
            self._exchange = None
            return False

    async def close(self):
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception as e:
                log.warning("Error closing Binance exchange: %s", e)
            finally:
                self._exchange = None

    async def start_stream(
        self,
        symbols: list[str],
        callback: Callable[[dict], None] | None = None,
    ):
        """Start real-time ticker stream for symbols.

        Prices are written to live_prices.json on every tick so the rest of
        the pipeline can read current prices without WebSocket knowledge.

        Args:
            symbols: CCXT market symbols, e.g. ["BTC/USDT", "ETH/USDT"].
            callback: Optional callback called with each ticker dict.
        """
        if not self._running:
            if not await self.connect():
                log.error("BinanceWebSocket: cannot start — connection failed")
                return
            self._running = True

        task = asyncio.create_task(
            self._stream_tickers(symbols, callback),
            name="binance_ws_ticker",
        )
        self._tasks.append(task)
        log.info("BinanceWebSocket: streaming %s", symbols)

    async def stop_stream(self):
        await self.close()

    async def _stream_tickers(
        self,
        symbols: list[str],
        callback: Callable[[dict], None] | None,
    ):
        ex = self._exchange
        if ex is None:
            return
        while self._running:
            try:
                for symbol in symbols:
                    ticker = await ex.watch_ticker(symbol)
                    price = ticker.get("last") or ticker.get("close")
                    if price:
                        sym_key = symbol.replace("/USDT", "").replace("/USD", "")
                        self._prices[sym_key] = float(price)
                    self._flush_prices()
                    if callback:
                        callback(ticker)
            except asyncio.CancelledError:
                log.debug("BinanceWebSocket ticker stream cancelled")
                break
            except Exception as e:
                log.warning("BinanceWebSocket stream error: %s — retry in %.0fs", e, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    def _flush_prices(self):
        """Write current prices to live_prices.json (atomic via temp file)."""
        import json as _json
        import time as _time
        try:
            existing: dict = {}
            if self.LIVE_PRICES_PATH.exists():
                try:
                    existing = _json.loads(self.LIVE_PRICES_PATH.read_text())
                except Exception:
                    pass
            updated = {**existing}
            ts = datetime.now(timezone.utc).isoformat()
            for sym, price in self._prices.items():
                updated[sym] = {"price": price, "source": "binance_ws", "updated_at": ts}
            tmp = self.LIVE_PRICES_PATH.with_suffix(".json.tmp")
            tmp.write_text(_json.dumps(updated, indent=2))
            tmp.replace(self.LIVE_PRICES_PATH)
        except Exception as e:
            log.debug("live_prices.json flush error: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._exchange is not None and self._running

    @property
    def prices(self) -> dict[str, float]:
        """Latest prices dict keyed by base symbol (BTC, ETH, ...)."""
        return dict(self._prices)



async def demo() -> None:
    """Quick demo: stream BTC/USDT tickers for 30 seconds and print them."""
    stream = KrakenWebSocket()

    def print_tick(tick: dict):
        symbol = tick.get("symbol", "?")
        bid = tick.get("bid", "N/A")
        ask = tick.get("ask", "N/A")
        ts = tick.get("timestamp") or tick.get("datetime", "")
        log.info("[%s] bid=%.2f ask=%.2f  ts=%s", symbol, bid, ask, ts)

    await stream.start_stream(["BTC/USDT"], print_tick, stream_type="ticker")
    log.info("Streaming for 30 seconds...")
    await asyncio.sleep(30)
    await stream.stop_stream()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    if not HAS_CCXT_PRO:
        print(
            "ccxt.pro is not installed.\n\n"
            "Install with:  pip install 'ccxt[ccxtpro]'\n"
            "Or:            pip install ccxtpro\n"
        )
        raise SystemExit(1)

    asyncio.run(demo())
