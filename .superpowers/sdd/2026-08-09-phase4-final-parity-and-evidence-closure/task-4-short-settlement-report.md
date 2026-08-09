# Task 4 — short-side native settlement repair

## Scope and outcome

This source-only repair closes the reviewed Category B defect in the finite
Nautilus launcher.  It changes neither the verifier, root Decimal oracle,
campaign, receipt/provenance grammar, target strategy, nor any runtime
closure/materialization/provider.

The old `BestPriceFillModel` supplied a simulated best-price order book.  Its
aggressive short SELL limit could therefore use the `LAST/EXTERNAL` bar close
instead of the sealed bid.  The pinned offline Nautilus 1.227.0 source confirms
that `BestPriceFillModel` returns a price-improving simulated book, whereas
base `FillModel` has deterministic limit-fill behavior without that simulation.

The repaired launcher:

- uses `FillModel()`;
- sets `bar_execution=False`, retaining bars for the fixed strategy but keeping
  LAST/EXTERNAL bar prices out of matching;
- delivers the validated L1 settlement quote one nanosecond after the bar;
- formats native limit prices at the instrument's two-decimal precision;
- makes a same-bar stop/take exit marketable at its literal validated exit
  price in the finite L1 settlement projection.

## TDD evidence

The RED test uses the actual pinned CPython 3.12/Nautilus runtime in a
subprocess, with a temporary manifest-bound copy of the real strategy.  It is
not an AST assertion or projection mock.

- Long accounting was already correct.
- The RED short run returned average entry `101`, fees `0.202`, and unrealized
  PnL `0`; the sealed literals require `99`, `0.198`, and `-4`.
- The RED same-bar run exposed the native zero-precision exit price failure.
- The GREEN native matrix covers long, short, and same-bar stop accounting.
  It now proves the short fill at sealed bid `99`, including exact fee and
  unrealized-PnL fields, and proves exact same-bar two-fill accounting.

Fresh verification:

```text
uv run pytest -q tests/nautilus_backtest
389 passed in 20.26s
git diff --check
pass
```

The Nautilus suite includes launcher, independent-reference, isolated-result,
and parity/paper-compatibility suites.  The expected inert
`independent-event mismatch` stderr line was emitted by an exercised negative
case; pytest exited zero.

## Required follow-on authority

No runtime authority was changed and no parity controller was retried.  This
source repair must be committed before the separately reviewed direct-child
policy-only rebind, new no-clobber simulation/paper runtime generation, and
the required reproducible Rust 1.95/LLVM/bwrap native builds (twice per
profile).  Those actions remain outside this task and require their normal
authorization.
