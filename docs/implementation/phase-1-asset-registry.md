# Phase 1 Asset Registry

`asset_registry.py` is the canonical deny-by-default routing boundary.

Each route declares asset ID, symbol, class, instrument type, currencies, data mapping, execution adapter/venue/symbol/market ID, enabled modes, and status. Unknown, disabled, incomplete, or mode-incompatible routes raise stable reason codes.

BTC, ETH, SOL, TON, DOGE, ADA, AVAX, DOT, LINK, and MATIC are all classified exclusively as crypto and can never fall through to Alpaca. Their direct venue routes are explicitly `DISABLED` because venue market IDs were not verified without exchange calls. Paper research behavior remains available; direct execution remains denied.

Equities retain explicit Alpaca paper routes. There is no default adapter.
