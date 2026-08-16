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

The C16 characterization entry binds the exact executable governance proofs:

- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_rejects_bare_make_executable`
  proves that bare or alternate Make executables are rejected in reachable
  recipes instead of being ignored.
- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_rejects_literal_make_command_alias`
  proves that a literal `make`, `gmake`, or path to either executable cannot be
  hidden behind a variable alias.
- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_rejects_make_derived_command_alias`
  proves that assignments derived transitively from the built-in `MAKE`
  variable cannot be invoked through `$(NAME)` or `${NAME}` aliases in a
  reachable recipe.
- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_rejects_make_function_derived_command_alias`
  proves that unsupported GNU Make function forms such as `$(value MAKE)` are
  rejected when their result is used through a reachable command alias.
- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_rejects_one_character_make_command_alias`
  proves that GNU Make's one-character `$M` reference form cannot hide the
  same Make-derived command alias.
- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_rejects_unassigned_command_variable`
  proves that a command-position variable such as `$(RUNNER)` is rejected when
  it has no canonical source assignment, so an environment or command-line
  value cannot manufacture an untracked recursive Make edge.
- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_rejects_variable_indirected_recursive_target`
  proves that a recursive Make target hidden behind a variable is rejected
  instead of silently disappearing from the graph.
- `tests/governance/test_p0_m1_p1_boundary.py::test_make_graph_traverses_same_root_dash_c_target`
  proves that `$(MAKE) -C . <target>` is resolved as a root-graph edge, while
  only a literal directory resolving somewhere other than the repository root
  may remain a bounded external leaf.
- `tests/governance/test_p0_m1_p1_boundary.py::test_portable_make_graph_is_literal_and_cannot_reach_host_authority`
  accepts only direct built-in `$(MAKE)` calls with literal targets, folds
  same-root `-C` calls into the graph, and proves that neither `ci` nor
  `ci-portable` can reach `ci-host-authority`. Every command-position Make
  variable other than direct built-in `$(MAKE)` must have a canonical source
  assignment classified as proven-safe. Fixed literal commands seed that safe
  set, and a pure `$(NAME)`, `${NAME}`, or `$N` alias becomes safe only when its
  source variable is already proven-safe. Unassigned or externally supplied
  names, literal Make executables, aliases derived transitively from `MAKE`, GNU
  Make functions, composed/dynamic expansions, repeated assignments, and
  shell-dollar forms fail closed. This is conservative source grammar: it does
  not execute Make or claim to emulate GNU Make functions or arbitrary shell
  expansion.
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
