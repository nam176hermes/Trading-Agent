# P1 Nautilus 1.231 API map

Authority is the closed 33-surface/153-invocation direct API contract and the
U05 qualification receipt. P1 does not perform a second upstream discovery.

| Decision | Surface |
|---|---|
| USE | `BacktestEngine`, `BacktestEngineConfig`, `FillModel`, `FeeModel` |
| USE | `Currency`, `CurrencyPair`, `Venue`, `InstrumentId` |
| USE | `BarType`, `Bar`, `QuoteTick`, `Price`, `Quantity`, `Money` |
| USE | `Strategy`, `StrategyConfig`, `OrderFilled`, `OrderRejected` |
| WRAP | Engine construction, data conversion, target planning and callback projection behind engine-neutral scalar/JSON contracts |
| COPY/ADAPT | Only the already committed target-portfolio strategy pattern, with its source hash retained in closure lineage |
| DO_NOT_USE | Dynamic imports, compatibility fallbacks, provider/network adapters, v2 modules, client-selected executable/profile |

Every concrete symbol is enumerated by
`upgrade/direct-api-contract.json`; every mapped invocation was executed by
U05 on exact G1. The schema-8 product modules must consume this authority rather
than add an unreviewed direct import surface.
