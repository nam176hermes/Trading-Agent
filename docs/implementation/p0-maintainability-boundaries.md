# P0 maintainability responsibility boundaries

The frozen P0 hotspots have narrow, reviewed responsibilities. Their approved
first-party imports are pinned in `p0-maintainability-hotspots.json` from the
AST of the exact `baseline_sha` Git blobs. The maintainability checker rejects
any current first-party import not present in that reviewed baseline; a future
dependency requires a deliberate manifest update and code review.

## `scripts/t_g03_capability_topology.py`

Owns P0 test-governance topology; portable, native, and external lane
orchestration; capability and authority classification; P0 receipts and their
validation; P0 native candidate acceptance mechanics; P0 semantic-result
projection; and P0 topology evidence aggregation.

It does not own strategy algorithms, market-data ingestion, real Nautilus
backtest or paper execution, broker or exchange APIs, portfolio optimization,
LLM reasoning, or quant training.

## `scripts/check_artifact_firewall.py`

Owns P0 evidence-tree validation, manifest and checksum validation, artifact
path and custody checks, secret-sensitive evidence screening, and portable
evidence publication validation.

It does not own trading-domain validation, market data, strategy, order
lifecycle, or Nautilus runtime behavior.

## `scripts/check_p0_ci_closure.py`

Owns historical P0 closure and qualification proof. It must not become a
generic P1/P2 qualification engine.

## P1 runtime boundary

A future real Nautilus runtime is a P1 component outside the frozen P0
topology. It may expose a narrow, reviewed capability/report interface whose
facts P0 governance observes. P0 governance does not import the P1 runtime,
implement strategy or execution behavior, submit orders, or acquire runtime
authority.

```text
P1 real Nautilus runtime
    |
    v  narrow capability/report interface
P0 governance observes facts

P0 governance
    X  does not implement execution
```

The hotspot manifest and checker enforce this boundary for source coupling.
In particular,
`test_checker_rejects_new_frozen_runtime_import_without_review` synthesizes an
`engines.nautilus.runtime` import in a frozen hotspot and proves that the real
checker rejects the drift. The adjacent generic, local-package, `from`-import,
and nested-package cases prevent alternate import spellings from bypassing the
same rule.

## Portable CI authority boundary

The portable source route and host qualification route remain separate:

```text
ci -> ci-portable -> portable source checks
                     X  ci-host-authority
```

The boundary reuses existing executable governance proofs instead of adding a
second Makefile parser:

- `tests/test_test_all_host_split.py::test_ci_routes_only_to_the_portable_gate_and_never_host_authority`
  parses prerequisites and recursive Make invocations, proves that `ci` has the
  sole prerequisite `ci-portable`, and proves that neither `ci` nor
  `ci-portable` can reach `ci-host-authority`. It does not execute host targets.
- `tests/test_p0_ci_closure.py::test_pending_source_matrix_is_an_executable_closed_contract`
  runs the fail-closed closure validator. That validator requires the
  Foundation workflow to invoke only `make ci-portable NONINTERACTIVE=1` and
  requires its actual authorization pair to be
  `LIVE_EXECUTION_ENABLED=false` plus `LIVE_TRADING_APPROVED=false`.
- `tests/jobs/test_child_environment.py::test_child_environment_is_fixed_empty_start_and_dedicated_names_only`
  proves that a portable research child starts from a fixed, credential-free
  environment and materializes the legacy/secondary
  `LIVE_TRADING_ENABLED=false` flag as well as both current authority gates as
  false.

`LIVE_TRADING_ENABLED` is not a Foundation workflow authority key. It is absent
there and fail-closed; wherever a portable child environment materializes that
legacy/secondary key, it must be explicitly `false`. No source test, portable
receipt, or successful CI result grants host, broker, exchange, production, or
live execution authority.
