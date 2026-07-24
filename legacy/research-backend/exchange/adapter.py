"""
Unified exchange adapter wrapping CCXT.
Supports: Binance, Coinbase, Bybit, Kraken.
Handles: rate limiting, auto-retry, standardized responses.
"""

import ccxt
import time
import logging
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)


class ExchangeID(str, Enum):
    BINANCE = "binance"
    COINBASE = "coinbase"
    BYBIT = "bybit"
    KRAKEN = "kraken"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    STOP_LOSS_LIMIT = "stop_loss_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"


@dataclass
class OrderRequest:
    exchange: ExchangeID
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None         # Required for limit orders
    stop_price: Optional[float] = None    # Required for stop orders
    client_order_id: Optional[str] = None # Idempotency key
    idempotency_key: Optional[str] = None # DB dedup key (same as client_order_id)
    reduce_only: bool = False


@dataclass
class OrderResult:
    exchange: ExchangeID
    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    quantity: float
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    price: Optional[float] = None         # Limit price
    stop_price: Optional[float] = None
    commission: Optional[float] = None
    commission_asset: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Balance:
    exchange: ExchangeID
    free: Dict[str, float] = field(default_factory=dict)
    used: Dict[str, float] = field(default_factory=dict)
    total: Dict[str, float] = field(default_factory=dict)

    def get(self, asset: str) -> Tuple[float, float]:
        return (self.free.get(asset, 0.0), self.total.get(asset, 0.0))


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    high_24h: float
    low_24h: float
    volume_24h: float
    change_pct_24h: float
    timestamp: int


class ExchangeAdapter:
    """Unified interface to crypto exchanges via CCXT."""

    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # seconds, doubles each retry

    def __init__(self, exchange_id: ExchangeID, api_key: str, secret: str, password: str = "",
                 sandbox: bool = False):
        self.exchange_id = exchange_id
        self.sandbox = sandbox

        exchange_class = getattr(ccxt, exchange_id.value)
        config = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        }
        if password:
            config["password"] = password

        if sandbox:
            if exchange_id == ExchangeID.BINANCE:
                config["urls"] = {"api": ccxt.binance().urls["test"]}
            elif exchange_id == ExchangeID.COINBASE:
                # Coinbase uses nested urls['api']['rest'] — don't overwrite entire urls dict
                config["urls"] = {"api": {"rest": "https://api-public.sandbox.exchange.coinbase.com"}}
            elif exchange_id == ExchangeID.KRAKEN:
                config["urls"] = {"api": "https://api.demo.kraken.com"}

        self._client: ccxt.Exchange = exchange_class(config)
        self._client.load_markets()

        log.info("ExchangeAdapter initialized: %s (sandbox=%s) — %d markets loaded",
                 exchange_id.value, sandbox, len(self._client.markets))

    # ── Market Data ────────────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch current ticker for a symbol."""
        raw = self._retry(lambda: self._client.fetch_ticker(symbol))
        return Ticker(
            symbol=symbol,
            bid=raw.get("bid", 0),
            ask=raw.get("ask", 0),
            last=raw.get("last", 0),
            high_24h=raw.get("high", 0),
            low_24h=raw.get("low", 0),
            volume_24h=raw.get("baseVolume", 0) or raw.get("volume", 0),
            change_pct_24h=raw.get("percentage", 0) or 0,
            timestamp=raw.get("timestamp", 0),
        )

    def fetch_tickers(self, symbols: List[str]) -> Dict[str, Ticker]:
        """Fetch tickers for multiple symbols."""
        raw = self._retry(lambda: self._client.fetch_tickers(symbols))
        result = {}
        for sym, data in raw.items():
            result[sym] = Ticker(
                symbol=sym,
                bid=data.get("bid", 0),
                ask=data.get("ask", 0),
                last=data.get("last", 0),
                high_24h=data.get("high", 0),
                low_24h=data.get("low", 0),
                volume_24h=data.get("baseVolume", 0) or data.get("volume", 0),
                change_pct_24h=data.get("percentage", 0) or 0,
                timestamp=data.get("timestamp", 0),
            )
        return result

    # ── Balance ────────────────────────────────────────────────

    def fetch_balance(self) -> Balance:
        """Fetch account balance."""
        raw = self._retry(lambda: self._client.fetch_balance())
        return Balance(
            exchange=self.exchange_id,
            free=raw.get("free", {}),
            used=raw.get("used", {}),
            total=raw.get("total", {}),
        )

    # ── Order Management ───────────────────────────────────────

    def create_order(self, req: OrderRequest) -> OrderResult:
        """Place an order on the exchange."""
        if self.sandbox:
            raise PermissionError("DRYRUN_ORDER_SUBMISSION_DISABLED")
        from live_execution_policy import LiveExecutionPolicy
        decision = LiveExecutionPolicy().evaluate("live", adapter_initialized=True, credentials_available=True)
        if not decision.allowed:
            raise PermissionError(decision.reason_code)
        params = {}
        if req.client_order_id:
            params["clientOrderId"] = req.client_order_id
        if req.reduce_only:
            params["reduceOnly"] = True

        raw = self._retry(lambda: self._client.create_order(
            symbol=req.symbol,
            type=req.order_type.value,
            side=req.side.value,
            amount=req.quantity,
            price=req.price,
            params=params,
        ))

        return self._parse_order(raw)

    def create_oco_order(self, symbol: str, side: str, quantity: float,
                         price: float, stop_loss_price: float,
                         take_profit_price: float) -> tuple[OrderResult, OrderResult] | None:
        """
        Place a One-Cancels-Other (OCO) order pair.
        Returns (stop_loss_result, take_profit_result) or None if not supported.
        Binance supports this natively. Coinbase does not.
        """
        if self.sandbox:
            raise PermissionError("DRYRUN_ORDER_SUBMISSION_DISABLED")
        from live_execution_policy import LiveExecutionPolicy
        decision = LiveExecutionPolicy().evaluate("live", adapter_initialized=True, credentials_available=True)
        if not decision.allowed:
            raise PermissionError(decision.reason_code)
        if self.exchange_id != ExchangeID.BINANCE:
            log.warning("OCO orders only supported on Binance, skipping")
            return None

        try:
            # Binance OCO: market sell with stop-loss limit + take-profit limit
            raw = self._retry(lambda: self._client.create_order(
                symbol=symbol,
                type="OCO",
                side=side,
                amount=quantity,
                price=take_profit_price,
                stopLossPrice=stop_loss_price,
                stopLimitPrice=stop_loss_price * 0.995,  # Slightly below stop for fill
            ))
            log.info("OCO placed: %s %s %.4f | SL: %.2f | TP: %.2f",
                    symbol, side, quantity, stop_loss_price, take_profit_price)
            # CCXT returns a dict with 'orders' key containing both orders
            orders = raw.get("orders", [raw]) if isinstance(raw, dict) else []
            results = [self._parse_order(o) for o in orders]
            return (results[0], results[1]) if len(results) >= 2 else (results[0], results[0])
        except Exception as e:
            log.error("OCO order failed: %s", e)
            return None

    def cancel_order(self, order_id: str, symbol: str) -> OrderResult:
        """Cancel an open order."""
        raw = self._retry(lambda: self._client.cancel_order(order_id, symbol))
        return self._parse_order(raw)

    def fetch_order(self, order_id: str, symbol: str) -> OrderResult:
        """Fetch a specific order by ID."""
        raw = self._retry(lambda: self._client.fetch_order(order_id, symbol))
        return self._parse_order(raw)

    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """Fetch all open orders."""
        raw = self._retry(lambda: self._client.fetch_open_orders(symbol))
        return [self._parse_order(o) for o in raw]

    def fetch_closed_orders(self, symbol: Optional[str] = None, since: Optional[int] = None,
                            limit: int = 50) -> List[OrderResult]:
        """Fetch closed orders."""
        raw = self._retry(lambda: self._client.fetch_closed_orders(symbol, since, limit))
        return [self._parse_order(o) for o in raw]

    # ── Helpers ────────────────────────────────────────────────

    def _parse_order(self, raw: Dict) -> OrderResult:
        """Parse CCXT order dict into OrderResult."""
        status_map = {
            "open": OrderStatus.OPEN,
            "closed": OrderStatus.CLOSED,
            "canceled": OrderStatus.CANCELED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.REJECTED,
        }

        fee = raw.get("fee") or {}
        return OrderResult(
            exchange=self.exchange_id,
            order_id=raw.get("id", ""),
            client_order_id=raw.get("clientOrderId", ""),
            symbol=raw.get("symbol", ""),
            side=OrderSide(raw["side"]) if raw.get("side") else OrderSide.BUY,
            order_type=OrderType(raw.get("type", "market")),
            status=status_map.get(raw.get("status", ""), OrderStatus.OPEN),
            quantity=raw.get("amount", 0) or 0,
            filled_quantity=raw.get("filled", 0) or 0,
            avg_fill_price=raw.get("average"),
            price=raw.get("price"),
            stop_price=raw.get("stopPrice"),
            commission=fee.get("cost"),
            commission_asset=fee.get("currency"),
            created_at=raw.get("datetime"),
            updated_at=raw.get("lastTradeTimestamp"),
            raw=raw,
        )

    def _retry(self, fn, max_retries: int = 0):
        """Execute with exponential backoff retry."""
        if not max_retries:
            max_retries = self.MAX_RETRIES
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                return fn()
            except ccxt.RateLimitExceeded as e:
                wait = self.RETRY_DELAY * (2 ** attempt)
                log.warning("Rate limited (attempt %d/%d), waiting %.1fs", attempt + 1, max_retries, wait)
                time.sleep(wait)
                last_error = e
            except ccxt.NetworkError as e:
                wait = self.RETRY_DELAY * (2 ** attempt)
                log.warning("Network error (attempt %d/%d): %s, retrying in %.1fs",
                           attempt + 1, max_retries, e, wait)
                time.sleep(wait)
                last_error = e
            except ccxt.ExchangeError as e:
                # Exchange-level errors (insufficient balance, invalid symbol) — don't retry
                log.error("Exchange error: %s", e)
                raise
            except Exception as e:
                log.error("Unexpected error: %s", e)
                raise

        raise last_error or Exception("Max retries exceeded")

    def test_connection(self) -> bool:
        """Verify API keys work by fetching balance."""
        try:
            self.fetch_balance()
            return True
        except Exception as e:
            log.error("Connection test failed: %s", e)
            return False
