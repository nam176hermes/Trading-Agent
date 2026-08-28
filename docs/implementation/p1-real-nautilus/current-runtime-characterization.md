# Current real Nautilus runtime characterization

Status: `P1-01_ACCEPTED`

The accepted product starting path is the existing sealed, real Cython-v1
`BacktestEngine` execution-simulation path. It is not the independent Decimal
oracle and it does not import Nautilus into root Python 3.11.

```text
RunBacktestSimulation envelope
  -> hash-bound five-input fixture
  -> EngineSpawnProvider / Bubblewrap / native guard
  -> sealed CPython 3.12 + Nautilus 1.231 G1
  -> nautilus_backtest.py
  -> BacktestEngine + target_portfolio_strategy.py
  -> canonical EngineEvent envelope
  -> root-side exact Decimal oracle/result validator
```

Lineage is `cython-v1`, Nautilus `1.231.0`, upstream commit
`27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`, schema-7 G1
`24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255`,
and target schema 8. A future `runtime_v2` implements the engine-neutral session
boundary in a different package/closure; it is not imported, installed or run
by P1.

U05 observed real callbacks `on_start`, `on_bar`, and `on_order_filled` for
entry/flatten scenarios. `on_order_rejected` remains implemented and tested but
was not fabricated as an observed callback. Engine disposal is unconditional.

No production behavior changes in P1-01. Candidate activation, provider,
network, broker, exchange, live and production authority remain false.
