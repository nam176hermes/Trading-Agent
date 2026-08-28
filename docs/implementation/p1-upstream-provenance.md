# P1 Nautilus upstream provenance

## P1-UPSTREAM-001 — catalog-driven currency pair

- Task: P1-08
- Engine: `nautilus_trader` `v1.231.0`
- Upstream commit: `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`
- Upstream sources:
  - `nautilus_trader/model/instruments/currency_pair.pyx`, `CurrencyPair.__init__`
  - `nautilus_trader/model/objects.pyx`, `Currency.__init__`, `Price.from_str`, `Quantity.from_str`, `Money.__init__`
  - `crates/model/src/types/price.rs`, `PRICE_MAX`
  - `crates/model/src/types/quantity.rs`, `QUANTITY_MAX`
- Mode: pattern-only adaptation; no upstream implementation bytes copied.
- Local divergence: values come only from the schema-v1 hash-bound catalog plus the fixed BTC/USDT metadata required by the accepted P1 slice. Exact high-precision G1 `PRICE_MAX` and `QUANTITY_MAX` bounds reject values before native construction.
- Test provider: `TestInstrumentProvider` is not used by `runtime_v1`.
- Schema note: catalog v1 contains no maximum fields, so a client-controlled min/max inconsistency is not representable; native maximums remain absent rather than invented.
- Authority: P1-only, offline backtest; no network, broker, exchange, live or production authority.
- Tests: `tests/p1_nautilus/test_instrument_factory_source.py`, `tests/p1_nautilus/test_instrument_factory_native.py`.
