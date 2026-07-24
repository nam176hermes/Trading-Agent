"""
event_bus.py — Event-Driven Architecture for Trading Pipeline
Replaces polling loops with asyncio event bus + optional Redis pub/sub.

Pattern: Event-driven with publish/subscribe. Components react to market
data events instead of polling. This is the consensus architecture from
19 trading books (HFT Systems, DMA, Python for Algo Trading).

Books: All agree event-driven > polling. Python asyncio + Redis pub/sub
is sufficient for crypto (100ms latency budget vs μs for equities).
"""
import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, Optional, Set
from dataclasses import dataclass, field

log = logging.getLogger("event_bus")

# ── Core Event Types ─────────────────────────────────────────────────────────

@dataclass
class MarketEvent:
    """A market data event (price tick, OHLCV update, signal)."""
    type: str              # 'tick', 'ohlcv', 'signal', 'order', 'alert'
    symbol: str
    data: dict
    timestamp: float = field(default_factory=time.time)
    exchange: str = ""


# ── Local Event Bus (asyncio) ───────────────────────────────────────────────

class LocalEventBus:
    """
    In-process event bus using asyncio.Queue.
    Zero external dependencies. Sufficient for single-process crypto trading.

    Usage:
        bus = LocalEventBus()
        bus.subscribe('signal', handle_signal)
        await bus.publish(event)
        await bus.run()  # starts processing loop (blocking)
    """

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers: Dict[str, Set[Callable]] = {}
        self._running = False
        self._tasks: list = []

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Register a handler for a specific event type. Handler must be async."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = set()
        self._subscribers[event_type].add(handler)
        log.info("EventBus: subscribed to '%s' → %s", event_type, handler.__name__)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Remove a handler subscription."""
        if event_type in self._subscribers:
            self._subscribers[event_type].discard(handler)

    async def publish(self, event: MarketEvent) -> None:
        """Publish an event to the bus. Non-blocking put."""
        await self._queue.put(event)

    async def _process_events(self) -> None:
        """Main event loop: dequeues events and dispatches to subscribers."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                handlers = self._subscribers.get(event.type, set())
                tasks = []
                for handler in handlers:
                    tasks.append(asyncio.create_task(handler(event)))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error("EventBus dispatch error: %s", e)

    async def run(self) -> None:
        """Start the event processing loop. Blocks until stop() is called."""
        self._running = True
        log.info("EventBus: started processing loop")
        await self._process_events()

    async def stop(self) -> None:
        """Stop the event processing loop."""
        self._running = False
        log.info("EventBus: stopped")


# ── Redis Pub/Sub Bridge (optional) ──────────────────────────────────────────

class RedisEventBridge:
    """
    Cross-process event bus via Redis pub/sub.
    Enables multiple pipeline processes to share events (e.g., collector → analyst → executor).

    Requires: pip install redis[hiredis]
    Falls back gracefully if redis unavailable.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379", channel_prefix: str = "trading"):
        self._redis_url = redis_url
        self._channel = f"{channel_prefix}:events"
        self._redis = None
        self._pubsub = None
        self._available = False
        self._task = None

    async def connect(self) -> bool:
        """Connect to Redis. Returns True if successful."""
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url)
            await self._redis.ping()
            self._pubsub = self._redis.pubsub()
            self._available = True
            log.info("Redis bridge: connected to %s", self._redis_url)
            return True
        except ImportError:
            log.info("Redis bridge: redis not installed — using local bus only")
            return False
        except Exception as e:
            log.warning("Redis bridge: connection failed (%s) — using local bus only", e)
            return False

    async def publish(self, event: MarketEvent) -> None:
        """Publish event to Redis channel."""
        if not self._available or self._redis is None:
            return
        try:
            payload = json.dumps({
                "type": event.type,
                "symbol": event.symbol,
                "data": event.data,
                "timestamp": event.timestamp,
                "exchange": event.exchange,
            })
            await self._redis.publish(self._channel, payload)
        except Exception as e:
            log.error("Redis publish error: %s", e)

    async def subscribe(self, local_bus: LocalEventBus) -> None:
        """Subscribe to Redis events and forward to local bus."""
        if not self._available or self._pubsub is None:
            return

        await self._pubsub.subscribe(self._channel)
        log.info("Redis bridge: subscribed to channel '%s'", self._channel)

        async def _listen():
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    event = MarketEvent(
                        type=data["type"],
                        symbol=data["symbol"],
                        data=data["data"],
                        timestamp=data["timestamp"],
                        exchange=data.get("exchange", ""),
                    )
                    await local_bus.publish(event)
                except Exception as e:
                    log.error("Redis bridge: parse error — %s", e)

        self._task = asyncio.create_task(_listen())

    async def close(self) -> None:
        """Close Redis connections."""
        if self._task:
            self._task.cancel()
        if self._pubsub:
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()
        self._available = False


# ── Pipeline Integration Helpers ─────────────────────────────────────────────

async def data_collector_producer(bus: LocalEventBus, symbols: list, interval_seconds: int = 60):
    """Producer: periodically fetches market data and publishes to bus."""
    from data_collector import collect_all  # lazy import to avoid circular deps
    while True:
        try:
            data = await collect_all(symbols)
            for symbol, asset in data.get("assets", {}).items():
                event = MarketEvent(
                    type="ohlcv",
                    symbol=symbol,
                    data=asset,
                )
                await bus.publish(event)
        except Exception as e:
            log.error("Collector producer error: %s", e)
        await asyncio.sleep(interval_seconds)


async def signal_consumer(bus: LocalEventBus):
    """Consumer: reacts to OHLCV events, computes TA, generates signals."""
    from ta_engine import calculate_indicators
    bus.subscribe("ohlcv", _handle_ohlcv)


async def _handle_ohlcv(event: MarketEvent):
    """Internal: compute TA indicators from OHLCV event."""
    try:
        ohlcv = event.data.get("ohlcv", [])
        if not ohlcv:
            return
        result = calculate_indicators(ohlcv, event.symbol)
        if result:
            log.info("[%s] TA computed from event: RSI=%.1f", event.symbol, result.get("rsi_14", 0))
    except Exception as e:
        log.error("Signal consumer error [%s]: %s", event.symbol, e)
