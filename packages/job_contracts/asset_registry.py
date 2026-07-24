"""Code-owned canonical assets approved for Phase 4 job contracts.

This immutable registry preserves the Phase 1 identity and classification
semantics without importing the legacy runtime or reading venue state.  SPY and
QQQ were configured Phase 1 routes, but were not approved among the 17
canonical assets established by the Phase 3 migration boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping


PHASE1_CRYPTO_SYMBOLS: Final = (
    "BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC",
)
PHASE1_CANONICAL_EQUITY_SYMBOLS: Final = (
    "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
)


@dataclass(frozen=True)
class ContractAsset:
    asset_id: str
    symbol: str
    asset_class: Literal["crypto", "equity"]


_ASSETS = {
    **{
        symbol: ContractAsset(
            asset_id=f"crypto:spot:{symbol}/USDT",
            symbol=symbol,
            asset_class="crypto",
        )
        for symbol in PHASE1_CRYPTO_SYMBOLS
    },
    **{
        symbol: ContractAsset(
            asset_id=f"equity:alpaca:{symbol}",
            symbol=symbol,
            asset_class="equity",
        )
        for symbol in PHASE1_CANONICAL_EQUITY_SYMBOLS
    },
}
CANONICAL_ASSET_REGISTRY: Final[Mapping[str, ContractAsset]] = MappingProxyType(
    _ASSETS
)
APPROVED_ASSET_SYMBOLS: Final[frozenset[str]] = frozenset(
    CANONICAL_ASSET_REGISTRY
)
