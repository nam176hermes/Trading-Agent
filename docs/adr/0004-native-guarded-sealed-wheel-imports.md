# ADR 0004: Native-guarded sealed-wheel imports

- Status: Accepted for source architecture
- Operational status: Not activated
- Date: 2026-08-08
- Owners: Trading Agent source and release maintainers

## Context

The sealed Nautilus launcher previously tried to make Python module state an
import-security boundary. On entry to the wheel-import scope it rewrote
`sys.modules`, inserted a custom `sys.meta_path` finder, repaired standard-library
parent/child attributes, and later attempted to restore those mutable objects.
The result depended on which lazy standard-library imports happened before the
snapshot. Repeated isolated Nautilus failures demonstrated that this state
machine was not a stable authority boundary.

The enforceable boundary already exists outside mutable Python module state.
The native entry guard admits the exact CPython argv, including `-I -S`, and an
empty environment. Closure attestation and Bubblewrap bind the interpreter,
launcher, read-only wheel closure, inputs, mounts, and network isolation. The
launcher can therefore use normal CPython import semantics over an explicitly
bounded path without inventing a second import system.

## Decision

The launcher accepts dependency roots only after
`_require_production_stdlib_sys_path()` proves that the initial path contains
standard-library roots only. It resolves every supplied sealed root strictly,
rejects duplicates, appends the roots after the original standard-library path
in deterministic wheel-name order, and restores the original path on both
success and error.

Both the zero-order and execution-simulation profiles use one
`_extract_sealed_wheels` implementation and the same bounded dependency-path
scope. Wheel extraction remains confined to the profile's private extraction
root and retains the existing validation of traversal, symlink, and directory
members plus bad-ZIP rejection.

CPython owns ordinary import and package semantics inside the scope. The
launcher does not snapshot, delete, replace, reseed, or restore `sys.modules`,
`sys.meta_path`, or standard-library parent attributes. Because sealed roots
follow the verified standard-library entries, a wheel cannot shadow a
standard-library top-level package such as `json` or `importlib`. Between sealed
roots, the first wheel in deterministic wheel-name order has precedence.

Authority is assigned as follows:

1. The native guard owns exact process entry, argv, `-I -S`, and the empty
   environment.
2. Closure attestation and the spawn boundary own executable, artifact, input,
   and Bubblewrap mount identity.
3. Bubblewrap owns the read-only filesystem and no-network process boundary.
4. The launcher verifies the stdlib-only initial path and temporarily exposes
   only strictly resolved sealed extraction roots after it.
5. CPython owns standard-library lazy import and parent/child behavior.

## Consequences

- Mutable Python module and finder state is no longer treated as admission
  authority.
- Standard-library imports retain precedence over sealed wheels.
- Wheel precedence is explicit, stable, and shared by both launcher profiles.
- An ambient current directory, source checkout, site-packages directory, or
  environment-provided path is not admitted by the path scope.
- Modules legitimately imported during execution follow normal CPython cache
  semantics; only `sys.path` is restored by the scope.
- Binding this semantic policy into closure schema 6 remains a separate,
  reviewed task. This ADR alone does not publish or activate a closure.

## Rejected alternatives

- Continuing to expand trusted-preload and parent-attribute repair maps was
  rejected because their correctness depends on import order.
- Prepending sealed wheel roots was rejected because a wheel could shadow a
  standard-library top-level package.
- Admitting the checkout, current directory, user site, or ambient
  `site-packages` was rejected because those paths are not closure authority.
- Treating the Python context manager as process admission was rejected because
  native guard, closure attestation, and Bubblewrap provide that boundary.

## Safety and operational scope

This decision is source-only and paper-only. It does not inspect private
diagnostics, publish or materialize a closure, mutate an external cache, start
an engine, contact a provider or broker, access an account, change protected
configuration, modify a service or scheduler, or authorize paper/live trading.

## Verification

Direct `python3.12 -I -S` subprocess regressions prove the stdlib-only initial
path, sealed-root resolution, ambient-current-directory exclusion, stdlib-first
and deterministic sealed-root precedence, normal lazy standard-library imports,
unchanged module/finder state, and `sys.path` restoration on success and error.
The affected launcher protocol, isolated backtest, and target-strategy source
tests plus repository audit and contract gates remain required before commit.
