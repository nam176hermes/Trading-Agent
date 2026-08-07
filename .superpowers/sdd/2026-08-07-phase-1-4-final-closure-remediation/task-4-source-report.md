# Task 4 source report

## Source-only status

Implemented the source/TDD portion of Task 4:

- Strict root reconstruction of canonical mounted scenario bytes, including
  duplicate-key, float, unknown-key, semantic-identity, and artifact-binding
  rejection.
- A root-only Decimal reference oracle with fixed eight-scenario golden values
  and deterministic execution-input mutation coverage.
- Expanded five-input, eighteen-attribute parity validation. The original
  zero-order validator remains unchanged and rejects simulation commands.
- A fixed external `TargetPortfolioStrategy` source file and launcher wiring
  which no longer returns `_run_execution_simulation` as an engine result.
- A two-file launcher inventory in the closure policy/materializer, binding
  both `/engine/launcher/nautilus_backtest.py` and
  `/engine/launcher/target_portfolio_strategy.py`.

Focused source checks passed:

```text
uv run pytest tests/nautilus_backtest tests/jobs/test_engine_result_validation.py \
  tests/foundation/test_nautilus_runtime_closure.py -q
130 passed
```

## Deliberate boundary

No external runtime closure, wheel cache, broker/provider, network, database,
or paper authority was opened or mutated. In particular, v12 was not created.

This is not a runtime-parity claim. The worker fails closed unless its caller
supplies an independently reconstructed expected outcome; the mounted-artifact
resolver and sealed 1.227.0 runtime API qualification are publication-stage
work. The exact external cache/account API calls in the strategy path require
that later sealed source review and normal spawn-path qualification before any
v12 materialization can be considered.
