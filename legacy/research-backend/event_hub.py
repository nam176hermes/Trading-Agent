"""
event_hub.py — Signal Pipeline Event Hub
=========================================
Wires event_bus.LocalEventBus into the signal pipeline.
Provides a singleton bus, helper functions for publishing/subscribing,
and a file-logging subscriber for signal events.

Usage (sync):
  from event_hub import get_bus, emit_signal, subscribe

  bus = get_bus()
  subscribe("signal", my_handler)

  emit_signal("BTC", "BUY", 0.85)

Usage (async):
  from event_hub import get_bus, publish_async

  await publish_async(event)

Cron-friendly:
  python event_hub.py --replay signals_events.jsonl  # replay past events
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from event_bus import LocalEventBus, MarketEvent
from runtime_paths import data_root

log = logging.getLogger("event_hub")

# ── Singleton ────────────────────────────────────────────────────────────────

_bus: Optional[LocalEventBus] = None
_lock = threading.Lock()


def get_bus() -> LocalEventBus:
    """Get or create the singleton LocalEventBus instance.

    Thread-safe. Returns the same bus across all callers.
    """
    global _bus
    if _bus is not None:
        return _bus
    with _lock:
        if _bus is None:
            _bus = LocalEventBus(maxsize=1000)
            log.info("EventHub: singleton LocalEventBus created (maxsize=1000)")
        return _bus


# ── Async publish helper ────────────────────────────────────────────────────

async def publish_async(event: MarketEvent) -> None:
    """Publish an event to the event bus asynchronously."""
    bus = get_bus()
    await bus.publish(event)


# ── Sync publish (fire-and-forget via background event loop) ─────────────────

def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Get running event loop or create a new one in a background thread."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass

    # Create a background event loop in a daemon thread
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop


# Store a reference to the background loop
_background_loop: Optional[asyncio.AbstractEventLoop] = None


def publish_sync(event: MarketEvent) -> None:
    """Publish an event from synchronous code.

    Uses an existing running loop if available, otherwise schedules on
    a background event loop thread. Non-blocking.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(publish_async(event))
    except RuntimeError:
        # No running loop — use background loop
        global _background_loop
        if _background_loop is None or _background_loop.is_closed():
            _background_loop = _get_or_create_loop()
        asyncio.run_coroutine_threadsafe(publish_async(event), _background_loop)


# ── Subscribe helper ─────────────────────────────────────────────────────────

def subscribe(event_type: str, handler: Callable) -> None:
    """Register an async handler for an event type.

    Args:
        event_type: Event type string (e.g. 'signal', 'tick', 'alert').
        handler: Async callable that accepts a MarketEvent.
    """
    bus = get_bus()
    bus.subscribe(event_type, handler)


# ── Signal-specific helpers ──────────────────────────────────────────────────

SIGNALS_LOG = data_root() / "logs" / "signals_events.jsonl"


def emit_signal(
    symbol: str,
    action: str,
    confidence: float,
    exchange: str = "",
    data: Optional[dict] = None,
) -> None:
    """Emit a trading signal to the event bus and log it to file.

    Args:
        symbol: Asset symbol (e.g. 'BTC', 'ETH').
        action: Trading action ('BUY', 'SELL', 'HOLD', 'WATCH').
        confidence: Signal confidence (0.0 to 1.0).
        exchange: Exchange identifier (optional).
        data: Additional signal data dict (optional).
    """
    event = MarketEvent(
        type="signal",
        symbol=symbol,
        data={
            "action": action.upper(),
            "confidence": confidence,
            **(data or {}),
        },
        exchange=exchange,
    )

    # Log to JSONL file (synchronous, fire-and-forget-safe)
    _log_signal_to_file(event)

    # Publish to event bus
    try:
        publish_sync(event)
    except Exception as e:
        log.error("emit_signal publish failed: %s", e)


def _log_signal_to_file(event: MarketEvent) -> None:
    """Append a signal event to signals_events.jsonl."""
    SIGNALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event.type,
            "symbol": event.symbol,
            "action": event.data.get("action", ""),
            "confidence": event.data.get("confidence", 0.0),
            "data": event.data,
            "exchange": event.exchange,
        }
        with open(SIGNALS_LOG, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log.error("Signal file logger error: %s", e)


# ── Built-in file-logging subscriber ─────────────────────────────────────────


async def _log_signal_handler(event: MarketEvent) -> None:
    """Async handler that logs signal events to the events file.
    
    This is the primary subscriber wired to the 'signal' event type.
    When any component calls emit_signal(), this handler fires and
    appends to signals_events.jsonl.
    """
    _log_signal_to_file(event)


def wire_default_subscribers() -> None:
    """Wire built-in subscribers. Call once at startup."""
    subscribe("signal", _log_signal_handler)
    log.info("EventHub: default subscribers wired (signal → file logger)")


# ── Startup: wire defaults ───────────────────────────────────────────────────

wire_default_subscribers()


# ── CLI / replay tools ───────────────────────────────────────────────────────


def replay_signals(log_path: Optional[str] = None) -> None:
    """Replay signals from a JSONL log file through the event bus.

    Useful for testing subscribers or backfilling event state.
    """
    path = Path(log_path or SIGNALS_LOG)
    if not path.exists():
        print(f"No signal log found at {path}")
        return

    bus = get_bus()

    async def _replay():
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event = MarketEvent(
                    type=entry.get("event_type", "signal"),
                    symbol=entry.get("symbol", "?"),
                    data=entry.get("data", {}),
                    exchange=entry.get("exchange", ""),
                    timestamp=entry.get("timestamp", time.time()),
                )
                await bus.publish(event)
                print(f"  Replayed: {entry.get('symbol', '?')} {entry.get('action', '?')}")

    asyncio.run(_replay())
    print(f"Replay complete — {path}")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Event Hub — signal pipeline event bus")
    parser.add_argument("--replay", type=str, nargs="?", const="auto",
                        help="Replay signals from JSONL log (default: logs/signals_events.jsonl)")
    parser.add_argument("--emit", type=str, nargs=3, metavar=("SYMBOL", "ACTION", "CONFIDENCE"),
                        help="Emit a test signal (e.g. BTC BUY 0.85)")
    args = parser.parse_args()

    if args.replay:
        path = None if args.replay == "auto" else args.replay
        replay_signals(path)

    if args.emit:
        symbol, action, confidence = args.emit
        emit_signal(symbol.upper(), action.upper(), float(confidence))
        print(f"Emitted signal: {symbol.upper()} {action.upper()} conf={confidence}")
        time.sleep(0.5)
