# P1-U04 X4 Live Rollback Revalidation Design

## Status and authority

This is the new bounded architecture-escalation packet authorized by the
operator after commit `6cc5ad121259182bba9d49c7ad0e17dfc27976ee` recorded the
previous packet's terminal security finding. It is limited to live schema-6
rollback recomputation at the X4 production boundary and a stale-replay
regression. It has at most two implementation/review rounds.

The packet does not authorize Build A or Build B by itself. Native build remains
blocked until the implementation passes fresh spec and security/replay review,
X4 preflight is rerun on the exact reviewed source, a new receipt is sealed, and
fresh X4 receipt reviews both pass.

## Root cause

`_validate_x4_authority_receipt()` validates the recorded
`checks.rollback_authority` object only by field shape. It recomputes source,
policy, tool, host, and output-root state, but never derives the current selected
1.227 schema-6 authority. A receipt with a replaced, well-formed rollback digest
therefore reaches phase A. The same omission is present in the phase-A
revalidation before Build A publication and in phases B and FINAL because all
of those boundaries call the same validator.

The defect is reproducible at the source base: changing only the receipt's
`closure_sha256`, resealing its digest, and calling phase A produces
`STALE_ROLLBACK_REPLAY_ACCEPTED=YES`.

## Considered approaches

1. Reuse the existing materializer validator in-process. Load
   `materialize_nautilus_runtime_closure.py`, run its existing historical
   schema-1 base validation plus `_selected_base_authority()`, and project the
   result into the receipt's schema-6 fields. This is the selected approach: it
   keeps one physical authority implementation and one shared X4 gate.
2. Duplicate the schema-6 filesystem checks in the builder. Rejected because it
   creates a second authority implementation that can drift.
3. Spawn the existing one-line preflight command as a subprocess. Rejected
   because parsing a subprocess receipt adds another boundary and error surface
   without improving authority over the existing in-process validator.

## Design

Add one private builder helper that derives the live rollback projection from
the rollback root already selected by `_candidate_roots()`:

- load the existing runtime-closure materializer from its exact repository
  path;
- load the exact repository rollback policy;
- validate the retained historical `runtime-closure-v3` manifest and records;
- call the existing `_selected_base_authority()` physical schema-6 attestor;
- return only the eight canonical X4 receipt fields:
  `artifact_generation`, `artifact_manifest_sha256`, `closure_sha256`,
  `generation`, `manifest_mode`, `manifest_sha256`, `result`, and `schema`;
- translate any load or materializer validation failure into a fail-closed
  `VerificationError` without accepting synthetic authority.

After the receipt's rollback object passes its strict shape checks,
`_validate_x4_authority_receipt()` must compare it for exact equality with that
live projection. A mismatch raises before the phase-specific output-root check
returns. Because Build A calls this validator before building and again before
publication, and Build B calls it at B entry, before B publication, and before
final publication, the one comparison covers every required boundary.

No receipt schema changes, command-line changes, dependency changes, fallback,
or new authority source are allowed.

## TDD and acceptance

The RED test must prove that Build A accepts the receipt initially, then fails
closed when the live rollback projection changes before its second phase-A
validation; `build-a` must remain absent. Existing X4 fixtures may inject only
the already-recorded rollback projection so portable tests remain synthetic and
do not access host authority.

GREEN requires:

- the focused stale-replay regression passes;
- all candidate-closure portable tests pass with zero skip/xfail additions;
- fresh spec review reports no Critical or Important finding;
- fresh security/replay review reports no Critical or Important finding.

Only after those gates pass may X4 preflight be rerun against the exact reviewed
commit/tree. Missing host authority remains `DEFERRED`; supplied invalid host
authority is `FAIL`. Active/rollback 1.227 remains byte-for-byte unchanged and
1.231 remains candidate-only and inactive.

## Prohibited scope

No push, PR, merge, deployment, production mutation, broker credential access,
network/live trading, activation, promotion, skip, xfail, weakened assertion,
package fallback, moving version, or ambient compiler/package authority is
authorized.
