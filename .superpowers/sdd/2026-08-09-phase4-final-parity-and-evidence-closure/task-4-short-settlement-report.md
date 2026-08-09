# Task 4 — short-side native settlement repair

## Completed repair and authority boundary

This source-only repair closes the reviewed Category B settlement defects in
the finite Nautilus launcher.  It changes neither the verifier, root Decimal
oracle, campaign, fixture, result validator, target strategy, nor a runtime
closure/materializer/provider.

The first repair (`4ccb793`) replaced price-improving simulated book matching
with base `FillModel()` and disabled bar matching.  Independent review found
that its post-bar `+1ns` quote left the preceding event's L1 book active when
the next eligible `on_bar` order was submitted.  The final source repair
`d4502b19bc206399f363154d7594f4548f760693` projects each validated, sealed
L1 quote immediately before its corresponding bar.  Thus every eligible bar
observes its own bid/ask, not the prior event's book; bars remain strategy
callbacks only (`bar_execution=False`).  The finite projection also retains
the literal same-bar stop/take exit bounds.

The direct child
`2ead1c5d62a3d19ca951fd9a0e76a295e5084194` rebinding is policy-only: it
updates both simulation and paper compatibility policies with the new launcher
hash and binds `source_commit` to `d4502b19…`.  This re-authorizes the changed
launcher authority leaf at policy level.  No runtime closure was materialized,
no provider was contacted, and no parity controller was run or retried.

## TDD and native evidence

Before the production edit, the pinned native CPython 3.12 / NautilusTrader
1.227.0 regression was RED for `session-boundary`: it produced average entry
`100`, fees `0.1`, unrealized PnL `2`, and a different event digest; sealed
literals require `102`, `0.102`, `0`, and the fixed digest.

The GREEN native subprocess matrix covers all eight repository-ordered cases:
long, short, partial fill, same-bar stop/take, stale quote, zero liquidity,
session boundary, and event digest.  It compares the complete canonical
result, including accounting, counts, precedence, and event digest.  In
particular it proves the short SELL fill at sealed bid `99`, fees `0.198`, and
unrealized PnL `-4`; it also proves the session-boundary fill at `102`.

The qualification interpreter is no longer a hardcoded retained generation or
a skip-capable test.  The test resolves an offline CPython 3.12 candidate only
when its closure manifest and policy agree on the reviewed wheel target and
engine version, then probes the imported `nautilus_trader` version.  Absence is
an explicit test failure.  An always-running injected-engine test covers base
`FillModel`, `bar_execution=False`, and the quote-before-bar schedule for all
eight scenario plans.

## Fresh verification

```text
uv run pytest -q tests/nautilus_backtest
402 passed in 24.80s

uv run pytest -q tests/foundation/test_nautilus_runtime_closure.py \
  -k 'checked_in_polic or policy_binds'
5 passed, 33 deselected

uv run pytest -q tests/foundation/test_nautilus_native_entry_guard.py \
  -k materializer_builds_the_policy_bound_guard_reproducibly_offline
2 passed, 10 deselected

make check-broad-handler-inventory
pass

make check-secrets
pass
```

The last guard test performs two independent offline native builds for each
policy profile under the reviewed Rust 1.95.0 / LLVM / bwrap configuration.
`git diff --check` passed before both source and policy commits.

`make check-contracts` remains blocked by this worktree's missing dashboard
`apps/dashboard/node_modules/.bin/openapi-typescript`; no dependencies were
installed.  `make audit` remains blocked because this linked worktree exposes
`.git` as an indirection file and the canonical-repository audit rejects it
(`E_ROOT: .git`).  Neither blocker reflects a source or policy failure.
