"""Canonical deny-by-default asset metadata and execution routing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetRoute:
    asset_id: str
    symbol: str
    asset_class: str
    instrument_type: str
    base_currency: str
    quote_currency: str
    data_provider: str
    data_symbol: str
    execution_adapter: str | None
    execution_venue: str | None
    execution_symbol: str | None
    market_id: str | None
    enabled_modes: tuple[str, ...]
    status: str

    @property
    def adapter(self) -> str | None:  # compatibility during strangler migration
        return self.execution_adapter


class AssetRoutingError(ValueError):
    def __init__(self, reason_code: str, symbol: str):
        super().__init__(f"{reason_code}: {symbol}")
        self.reason_code = reason_code


CRYPTO_SYMBOLS = ("BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC")
STOCK_SYMBOLS = ("AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "SPY", "QQQ")

# Venue market IDs for the ten cryptos have not been verified in Phase 1.
# They remain explicitly disabled for direct venue execution, while their
# adapter classification is retained so they can never fall through to Alpaca.
ASSET_REGISTRY = {
    **{
        symbol: AssetRoute(
            asset_id=f"crypto:spot:{symbol}/USDT", symbol=symbol,
            asset_class="crypto", instrument_type="spot",
            base_currency=symbol, quote_currency="USDT",
            data_provider="legacy_crypto_collectors", data_symbol=symbol,
            execution_adapter="crypto", execution_venue=None,
            execution_symbol=f"{symbol}/USDT", market_id=None,
            enabled_modes=("paper",), status="DISABLED",
        ) for symbol in CRYPTO_SYMBOLS
    },
    **{
        symbol: AssetRoute(
            asset_id=f"equity:alpaca:{symbol}", symbol=symbol,
            asset_class="equity", instrument_type="stock",
            base_currency=symbol, quote_currency="USD",
            data_provider="alpaca", data_symbol=symbol,
            execution_adapter="alpaca", execution_venue="alpaca",
            execution_symbol=symbol, market_id=symbol,
            enabled_modes=("paper",), status="ACTIVE",
        ) for symbol in STOCK_SYMBOLS
    },
}


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().split("/")[0]


def resolve_asset(symbol: str) -> AssetRoute | None:
    return ASSET_REGISTRY.get(normalize_symbol(symbol))


def require_execution_route(symbol: str, mode: str) -> AssetRoute:
    route = resolve_asset(symbol)
    if route is None:
        raise AssetRoutingError("REJECT_UNKNOWN_ASSET", symbol)
    if route.status != "ACTIVE":
        raise AssetRoutingError("REJECT_DISABLED_ASSET", symbol)
    if not route.execution_adapter or not route.execution_venue or not route.market_id:
        raise AssetRoutingError("REJECT_ROUTE_UNAVAILABLE", symbol)
    if mode not in route.enabled_modes:
        raise AssetRoutingError("REJECT_MODE_NOT_ALLOWED", symbol)
    return route
