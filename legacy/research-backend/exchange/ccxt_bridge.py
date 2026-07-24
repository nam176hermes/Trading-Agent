"""
ccxt_bridge.py — Unified CCXT interface (Canada-legal exchanges only).

Public function API wrapping exchange.adapter.ExchangeAdapter.
Supports: Coinbase, Kraken (+ Crypto.com placeholder for future).
"""

import logging
import os
from typing import Optional

from .adapter import ExchangeAdapter, ExchangeID
from .secrets import get_exchange_credentials
from runtime_paths import mode_file

log = logging.getLogger("ccxt_bridge")

# ── Canada-legal exchanges only ──────────────────────────────────

CANADA_LEGAL = {"coinbase", "kraken", "crypto.com"}

# Exchange-specific symbol maps (our internal BTC/USDT → exchange native)
# Coinbase Advanced Trade uses USD pairs and hyphen notation
# Kraken uses XBT for Bitcoin and its own naming
_SYMBOL_OVERRIDES: dict[str, dict[str, str]] = {
    "coinbase": {
        "BTC/USDT": "BTC/USD",
        "ETH/USDT": "ETH/USD",
        "SOL/USDT": "SOL/USD",
        "TON/USDT": "TON/USD",
        "DOGE/USDT": "DOGE/USD",
    },
    "kraken": {
        "BTC/USDT": "XBT/USDT",
        "BTC/USD":  "XBT/USD",
    },
}

LIVE_EXECUTION_ENABLED = os.getenv("LIVE_EXECUTION_ENABLED", "false").lower() == "true"
MIN_ORDER_USD = 10.0
MAX_SLIPPAGE_PCT = 0.01

def get_mode() -> str:
    """Read trading mode: 'paper', 'dryrun', or 'live'."""
    target = mode_file()
    if target.exists():
        mode = target.read_text().strip().lower()
        if mode in ("paper", "dryrun", "live"):
            return mode
    return "paper"


def set_mode(mode: str) -> None:
    if mode not in ("paper", "dryrun", "live"):
        raise ValueError(f"Invalid mode: {mode}. Must be 'paper', 'dryrun', or 'live'.")
    target = mode_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mode + "\n")


def normalize_symbol(exchange_id: str, symbol: str) -> str:
    """
    Map a generic CCXT symbol (e.g. "BTC/USDT") to the exchange-native form.
    Coinbase Advanced Trade uses USD pairs; Kraken uses XBT.
    Falls back to the original symbol if no override is defined.
    """
    overrides = _SYMBOL_OVERRIDES.get(exchange_id.lower(), {})
    return overrides.get(symbol, symbol)


def _validate_exchange(exchange_id: str) -> None:
    if exchange_id.lower() not in CANADA_LEGAL:
        legal = ", ".join(sorted(CANADA_LEGAL))
        raise ValueError(f"Exchange '{exchange_id}' is not Canada-legal. Supported: {legal}")


def _sandbox_flag() -> bool:
    """Sandbox=true for dryrun mode, false for live, n/a for paper."""
    return get_mode() == "dryrun"


def _make_adapter(exchange_id: str) -> ExchangeAdapter:
    """Create an ExchangeAdapter from stored credentials.

    Raises:
        ValueError if exchange is not Canada-legal.
        RuntimeError if credentials are not configured.
    """
    _validate_exchange(exchange_id)

    creds = get_exchange_credentials(exchange_id)
    if not creds:
        raise RuntimeError(
            f"No API keys for {exchange_id}. "
            f"Add them with: python -m exchange.secrets add {exchange_id}"
        )

    return ExchangeAdapter(
        exchange_id=ExchangeID(exchange_id.lower()),
        api_key=creds["api_key"],
        secret=creds["secret"],
        password=creds.get("password", ""),
        sandbox=_sandbox_flag(),
    )


# ── Public API ──────────────────────────────────────────────────

def get_exchange(exchange_id: str) -> ExchangeAdapter:
    """Return a configured CCXT exchange instance.

    Raises ValueError if exchange is not Canada-legal.
    """
    return _make_adapter(exchange_id)


def fetch_balances(exchange_id: str) -> Optional[dict]:
    """Fetch account balances. Returns {BTC: {free, used, total}, ...} or None on failure."""
    try:
        adapter = _make_adapter(exchange_id)
        balance = adapter.fetch_balance()
        result = {}
        for asset in balance.total:
            if balance.total.get(asset, 0) > 0:
                result[asset] = {
                    "free": balance.free.get(asset, 0),
                    "used": balance.used.get(asset, 0),
                    "total": balance.total.get(asset, 0),
                }
        return result
    except Exception as e:
        log.error("[%s] fetch_balances failed: %s", exchange_id, e)
        return None


def fetch_ticker(exchange_id: str, symbol: str) -> Optional[dict]:
    """Fetch current ticker. Returns {bid, ask, last, volume, ...} or None on failure."""
    try:
        adapter = _make_adapter(exchange_id)
        ticker = adapter.fetch_ticker(normalize_symbol(exchange_id, symbol))
        return {
            "bid": ticker.bid,
            "ask": ticker.ask,
            "last": ticker.last,
            "high_24h": ticker.high_24h,
            "low_24h": ticker.low_24h,
            "volume_24h": ticker.volume_24h,
            "change_pct_24h": ticker.change_pct_24h,
        }
    except Exception as e:
        log.error("[%s] fetch_ticker failed for %s: %s", exchange_id, symbol, e)
        return None


def place_order(exchange_id: str, symbol: str, side: str, amount: float,
                price: Optional[float] = None) -> Optional[dict]:
    """Place a market or limit order. Returns CCXT order dict or None on failure."""
    if get_mode() == "live":
        from live_execution_policy import LiveExecutionPolicy
        decision = LiveExecutionPolicy().evaluate("live")
        if not decision.allowed:
            log.critical("Live execution blocked — reason=%s", decision.reason_code)
            return {"status": "blocked", "reason": decision.reason_code}

    if not LIVE_EXECUTION_ENABLED and get_mode() not in ("paper",):
        log.info("Live execution disabled — dryrun orders go to sandbox")

    try:
        from .adapter import OrderRequest, OrderSide, OrderType

        adapter = _make_adapter(exchange_id)
        order_type = OrderType.LIMIT if price else OrderType.MARKET
        native_symbol = normalize_symbol(exchange_id, symbol)

        result = adapter.create_order(OrderRequest(
            exchange=ExchangeID(exchange_id.lower()),
            symbol=native_symbol,
            side=OrderSide(side.lower()),
            order_type=order_type,
            quantity=amount,
            price=price,
        ))

        return {
            "id": result.order_id,
            "symbol": result.symbol,
            "side": result.side.value,
            "type": order_type.value,
            "amount": result.quantity,
            "filled": result.filled_quantity,
            "avg_fill_price": result.avg_fill_price,
            "status": result.status.value,
            "fee": result.commission,
            "fee_asset": result.commission_asset,
        }
    except Exception as e:
        log.error("[%s] place_order failed: %s %s %.6f — %s", exchange_id, side, symbol, amount, e)
        return None


def check_order(exchange_id: str, order_id: str, symbol: str) -> Optional[dict]:
    """Fetch order status by ID. Returns order dict or None on failure."""
    try:
        adapter = _make_adapter(exchange_id)
        result = adapter.fetch_order(order_id, symbol)
        return {
            "id": result.order_id,
            "symbol": result.symbol,
            "side": result.side.value,
            "amount": result.quantity,
            "filled": result.filled_quantity,
            "avg_fill_price": result.avg_fill_price,
            "status": result.status.value,
        }
    except Exception as e:
        log.error("[%s] check_order failed: %s — %s", exchange_id, order_id, e)
        return None


def cancel_order(exchange_id: str, order_id: str, symbol: str) -> Optional[dict]:
    """Cancel an open order. Returns order dict or None on failure."""
    try:
        adapter = _make_adapter(exchange_id)
        result = adapter.cancel_order(order_id, symbol)
        return {
            "id": result.order_id,
            "symbol": result.symbol,
            "side": result.side.value,
            "status": result.status.value,
        }
    except Exception as e:
        log.error("[%s] cancel_order failed: %s — %s", exchange_id, order_id, e)
        return None


def test_connection(exchange_id: str) -> bool:
    """Test API connectivity by fetching balance. Returns True/False."""
    try:
        adapter = _make_adapter(exchange_id)
        return adapter.test_connection()
    except Exception as e:
        log.error("[%s] Connection test failed: %s", exchange_id, e)
        return False


def get_best_quote(symbol: str, preferred_exchanges: Optional[list[str]] = None) -> Optional[dict]:
    """
    Query all configured Canada-legal exchanges for a symbol and return the
    best bid/ask (tightest spread from the exchange with credentials).

    Args:
        symbol: Internal symbol e.g. "BTC/USDT"
        preferred_exchanges: If given, try these first (must still be Canada-legal).

    Returns:
        {exchange, bid, ask, spread_pct, native_symbol} or None if all fail.
    """
    candidates = preferred_exchanges or sorted(CANADA_LEGAL - {"crypto.com"})
    best: Optional[dict] = None

    for ex_id in candidates:
        if ex_id not in CANADA_LEGAL:
            continue
        native = normalize_symbol(ex_id, symbol)
        try:
            adapter = _make_adapter(ex_id)
            ticker  = adapter.fetch_ticker(native)
            bid, ask = ticker.bid, ticker.ask
            if bid <= 0 or ask <= 0:
                continue
            spread_pct = (ask - bid) / ask * 100 if ask > 0 else 999.0
            if best is None or spread_pct < best["spread_pct"]:
                best = {
                    "exchange":      ex_id,
                    "native_symbol": native,
                    "bid":           bid,
                    "ask":           ask,
                    "last":          ticker.last,
                    "spread_pct":    round(spread_pct, 4),
                }
        except Exception as e:
            log.debug("[%s] get_best_quote(%s) skipped: %s", ex_id, symbol, e)

    if best:
        log.info("Best quote for %s: %s bid=%.4f ask=%.4f spread=%.4f%%",
                 symbol, best["exchange"], best["bid"], best["ask"], best["spread_pct"])
    return best


def multi_exchange_balance() -> dict[str, dict]:
    """
    Aggregate balances from all Canada-legal exchanges that have credentials.
    Returns {exchange_id: {asset: {free, used, total}}} for exchanges with keys.
    Silently skips exchanges without credentials.
    """
    results: dict[str, dict] = {}
    for ex_id in sorted(CANADA_LEGAL - {"crypto.com"}):
        try:
            balances = fetch_balances(ex_id)
            if balances:
                results[ex_id] = balances
        except RuntimeError:
            pass  # no credentials
        except Exception as e:
            log.debug("[%s] balance fetch skipped: %s", ex_id, e)
    return results
