# Phase 4 semantic simulation closure

## Final disposition

Phase 4 selected `runtime-closure-v12-r12-simulation` as the only qualified
execution-simulation generation. Generations `runtime-closure-v12-r9-simulation`,
`runtime-closure-v12-r10-simulation`, and
`runtime-closure-v12-r11-simulation` are rejected and preserved unchanged as
forensic evidence. They are neither rollback authority nor parity evidence.

The selected closure is schema 6, profile `execution-simulation`, semantic
profile `nautilus-execution-simulation-v2`, and uses the fixed dependency
policy `native-guarded-stdlib-first-sealed-wheel-path-v1`. It contains 90
read-only manifest mounts plus its sealed manifest sidecar. Its reviewed
authority is:

| Binding | SHA-256 |
| --- | --- |
| Closure | `14d4fd990dccfdbb8b6dfe964a04ae9e80fefb30914cf433de1bc503b8ad03fa` |
| Manifest | `b143564cf3ad63b4ca01afb9a27e7496c9b1c6ff1f3c46cf10b6c4a047545d20` |
| Runtime policy | `746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2` |
| Policy-bound source commit | `1683f1324826b78a715f017a7749fe3d1f7b37f4` |
| Native guard source | `a25053355abcfece9b7d5c524f4a3d3c06ce727aec8224012ef9b683240fd880` |
| Simulation native guard binary | `151b1570623253295ae36ea4b0933ad1f051fa56277ac9d1f54edcedc2c60c9a` |
| Campaign manifest | `6bb32115b6488fe42ffed77448dff894330ddc94b34b6bae44457e668302bf3c` |
| Strategy source | `6cc129ac9d0c6a09718500eb96d76398bd2925c8fa4f996ac85f37962bc38384` |
| Parity record | `89d2b127b7972805cce9900d109f8f3696d540aa8abfe11452c6218a221fb9ff` |

The native guard was built reproducibly twice per simulation and paper profile
offline with the reviewed Rust/Cargo 1.95.0 and LLVM inputs. Bubblewrap is
root-owned, network is unshared, and runtime inputs are descriptor- and
digest-bound.

## Exact qualification

One `long-accounting` diagnostic ran through the normal
`EngineSpawnProvider`: one controller invocation, one provider run, exit 0,
empty error streams, and exactly one sealed request plus sidecar.

The parity controller then ran exactly once and produced one canonical
`nautilus-phase4-parity-evidence-v2` PASS record. It covered exactly two normal
provider runs for each repository-ordered scenario:

1. `long-accounting`
2. `short-accounting`
3. `partial-fill`
4. `same-bar-stop-take-profit`
5. `stale-quote`
6. `zero-liquidity`
7. `session-boundary`
8. `event-digest`

All 16 runs have run-1/run-2 byte equality, actual event equality with the
independently rebuilt Decimal reference event, and launcher result-digest
equality with the independent root result digest. Transport custody is exactly
16 provider roots and 32 mode-`0400`, single-link request/sidecar members, with
no response member or failure receipt.

This is a bounded offline fixture qualification. It grants no service,
scheduler, provider, broker, account, database, deployment, paper-runtime, or
live-execution authority.
