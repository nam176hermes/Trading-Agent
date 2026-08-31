# P1 module ownership

| Surface | Responsibility |
|---|---|
| `packages/engine_contracts` | Generic wire envelopes only |
| `packages/nautilus_runtime_contracts` | Closed P1 scalar/JSON contracts |
| `engines/nautilus/runtime_v1` | Only new Cython-v1 imports and native product session |
| `services/job_worker` | Authority, artifact binding, spawn, result validation |
| `packages/engine_event_ledger` | Canonical raw event truth |
| `packages/engine_portfolio_projection` | Pure event-to-accounting projection |
| `packages/portfolio_reducer` | Independent engine-neutral accounting |
| `services/paper_runtime` | Single local child/checkpoint/recovery owner |

Research strategies, provider credentials, live adapters, dashboard concerns
and v2 imports do not belong to the P1 engine slice.
