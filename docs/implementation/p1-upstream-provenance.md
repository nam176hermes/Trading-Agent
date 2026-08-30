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

## P1-UPSTREAM-002 — sealed backtest engine and models

- Tasks: P1-09 through P1-14
- Engine: `nautilus_trader` `v1.231.0`, upstream commit `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`
- Public primitives: `BacktestEngine`, `FeeModel`, `FillModel`, `LoggingConfig`, `BacktestEngineConfig`, `AccountType`, `OmsType`, `Venue`, `CurrencyPair`, and `Money`.
- Local owners: `engines/nautilus/runtime_v1/session.py`, `backtest_runner.py`.
- Mode: direct public API use from the sealed wheel; no upstream implementation bytes copied.
- Boundary: deterministic offline backtest only; the closure excludes adapters, network clients, and live engines.

## P1-UPSTREAM-003 — market data and identifiers

- Tasks: P1-07 through P1-10
- Public primitives: `Bar`, `BarType`, `QuoteTick`, `InstrumentId`, `CurrencyPair`, `Price`, and `Quantity`.
- Local owners: `engines/nautilus/runtime_v1/market_data_loader.py`, `instrument_factory.py`.
- Mode: direct public API use with schema-8 inputs validated before native construction.
- Boundary: exact decimal strings and hash-bound artifacts remain locally authoritative.

## P1-UPSTREAM-004 — target strategy lifecycle

- Tasks: P1-11 through P1-14
- Public primitives: `Strategy`, `StrategyConfig`, `OrderSide`, `OrderFilled`, and `OrderRejected`.
- Local owner: `engines/nautilus/runtime_v1/target_strategy.py`.
- Mode: public lifecycle/callback reuse. The target-portfolio pattern is a local extraction from `engines/nautilus/launcher/target_portfolio_strategy.py` (source SHA-256 `6cc129ac9d0c6a09718500eb96d76398bd2925c8fa4f996ac85f37962bc38384`), not a copied upstream snippet. Local event projection and deterministic accounting are not delegated upstream.
- Boundary: no compatibility fallback, synthesized callback, live client, leverage, shorting, or closure mutation.
- Tests: `tests/p1_nautilus/test_p1_target_strategy_source.py`, `tests/p1_nautilus/test_target_strategy_native.py`, and `tests/p1_nautilus/test_event_stream_native.py`.

All reuse is pinned by the schema-8 runtime inventory and candidate lineage
report. Further public 1.231 primitives may be used only when they preserve the
sealed candidate, engine-neutral contracts, deterministic accounting, custody,
recovery, and live-safety boundaries.
