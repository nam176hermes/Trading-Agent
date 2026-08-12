# Package-6 controller-closure per-case lease isolation design

**Status:** design only.  This is a narrow C-38 follow-up to the C-37 hosted
Foundation classification at
`146655d592e38b5838a9c74dad8777aec12cbca2`.  It authorizes neither an
implementation nor a hosted-green claim.  It does not change a production
validator, runtime authority, workflow, Make target, engine/build input, or
live-execution setting.

## Decision and source evidence

Hosted Foundation `31636504627` reached the current portable audit pass and
then failed exactly two controller-closure tests:

```text
test_controller_result_crash_before_link_leaves_no_output
test_controller_result_crash_after_link_has_one_complete_output
```

Both tests currently receive one outer `package6_staging_lease` and loop over,
respectively, six and three independent crash boundaries
(`tests/foundation/test_package6_controller_closure.py:936-1071`).  Each loop
iteration calls `_finalizer_arguments(case, monkeypatch, lease=...)`.  That
function forwards the same lease unchanged to
`_sealed_runtime_fixture()` (`:450-462`), which forwards it to `_record()`
(`tests/foundation/test_package6_runtime_controller.py:373-400`).

`_record()` constructs staging material at the fixed `lease.root`
(`tests/foundation/test_package6_runtime_approval.py:91-236`).  The resulting
approval capability fixes `evidence_root` to `lease.root / "evidence"`
(`:340-343`), and `_sealed_runtime_fixture()` creates that path using
`mkdir(mode=0o700)` without `exist_ok`
(`tests/foundation/test_package6_runtime_controller.py:405-410`).  Thus the
first case has materialized a fixed child before the second case attempts an
independent complete runtime fixture.  The second construction correctly gets
`FileExistsError`; it is not a `RUNNER_TEMP` path-predicate failure, a missing
native extension, or a production-validator defect.

The existing lease helper already provides exactly the safe primitive required
for a new scenario: `create_package6_staging_lease()` issues a direct, unique
private child of `/tmp`, records its `(st_dev, st_ino)`, and
`Package6StagingLease.cleanup()` removes only that issued root through
revalidated no-follow directory descriptors
(`tests/foundation/_package6_staging_fixture.py:30-89`).  The C-30 closure
tests already establish the helper's hostile-root, ambient-temp, descriptor
cleanup, and complete `_finalizer_arguments()` lifetime contracts.  C-38 must
preserve them rather than replace or weaken them.

## Chosen topology: one fresh lease for each loop iteration

Change only the two failing looped controller-closure tests so that each
boundary allocates and owns its own `create_package6_staging_lease()` result.
The same lease is passed explicitly through that iteration's
`_finalizer_arguments(..., lease=case_lease)`, child finalizer invocation,
wait, and boundary-specific output assertions.  A `try/finally` in that loop
must call `case_lease.cleanup()` only after the whole individual scenario is
finished; cleanup errors are not swallowed.  The test must not accept a
preexisting `case_lease.root / "evidence"` child before construction.

The two test functions must no longer receive the pytest
`package6_staging_lease` fixture, since retaining it would allocate an unused
outer `/tmp` root and obscure the per-case lifetime.  All other
controller-closure tests retain their existing fixture behavior.

This deliberately keeps each direct `/tmp` root alive through both the
complete fixture construction and the spawned crash case's `waitpid` result.
It is not enough to clean after `_finalizer_arguments()` returns: the test's
child execution and its post-crash output assertions are one scenario.  After
those assertions, the root has no further valid consumer, so its immediate
test-owned cleanup is required rather than deferred to process exit.

The test may use the helper's existing factory and cleanup method directly;
it must not reimplement allocation, recursive deletion, chmod-based cleanup,
or a general context manager.  The closure module is already one of C-30's
four exact approved helper consumers, so importing the existing factory there
does not widen the consumer boundary.

### Rejected alternatives

* **Reuse/reset a single lease root:** rejected.  The source proves that a
  complete construction creates a fixed `evidence` child plus staged,
  authority, runtime, and sealed material under that root.  Some staged paths
  are deliberately made read-only, and a valid capability binds paths and
  contents from that construction.  Removing only `evidence`, or trying to
  reset the tree between cases, would create a new cleanup/identity policy and
  could invalidate or reuse sealed fixture state.  It cannot be treated as a
  harmless test reset.
* **Make `capability.evidence_root.mkdir(..., exist_ok=True)`:** forbidden
  production/runtime behavior change that would hide a collision and weaken a
  meaningful creation boundary.
* **Put the loop under one portable temporary root or use ambient
  `TMPDIR`/`RUNNER_TEMP`:** forbidden; Package-6 staging validation remains
  bound to a private direct `/tmp` child.
* **Parametrize only to obtain pytest fixture teardown:** not selected for
  this narrowly scoped repair because these are intentionally looped
  crash-boundary scenarios.  Per-case explicit lifetime lets each existing
  loop retain its boundary-specific sequence while proving fresh full-runtime
  construction and immediate, checked cleanup.

## Exact allowed implementation scope

The implementation slice may change only:

* `tests/foundation/test_package6_controller_closure.py`.

It may add the existing helper factory to that module's current private-helper
import, remove the unused fixture parameter from exactly the two failing
tests, and make the per-boundary allocation/cleanup and regression assertions
described below.  No change is allowed to
`tests/foundation/_package6_staging_fixture.py`, the approval or
runtime-controller fixture modules, Package-6 production source, validator,
authority/manifest, native component, Makefile, workflow, or any other test
module.  In particular, this slice does not alter the C-30 four-consumer or
explicit forwarding guard; it must continue to pass unchanged.

## TDD, regressions, and evidence

### RED

The hosted C-37 result is the witnessed behavioral RED: with a single
fixture-owned root, the second loop iteration fails at the actual
non-`exist_ok` creation of `lease.root / "evidence"`.  The implementation
receipt must preserve the exact failing test names, the duplicate-child
evidence, and the hosted run ID; it must not call this a validator failure.

Before changing the two loops, add a bounded regression in the same closure
test module that fails against their old topology and verifies all of the
following implementation facts:

* neither looped test accepts `package6_staging_lease` as its outer argument;
* each loop body explicitly obtains `create_package6_staging_lease()` before
  calling `_finalizer_arguments`, passes that same object as `lease=`, and
  has a `finally` path that calls that object's `cleanup()`; and
* the cleanup is structurally after the full per-case body, not immediately
  after `_finalizer_arguments`.

The regression may use bounded AST/source inspection of just these two test
functions.  It must not mock the production validator, invent a second
factory, or assert a guessed lease name.  It exists to prevent future
accidental reintroduction of the one-outer-lease topology.

### GREEN

For every iteration, the updated tests must make these behavioral assertions
around the real factory:

* `case_lease.assert_valid()` succeeds, its root is a direct child of `/tmp`,
  and `case_lease.root / "evidence"` is absent before the real
  `_finalizer_arguments` construction;
* construction receives precisely that `case_lease`, and the existing child
  process/crash and output assertions retain their current behavior;
* only after the child has been reaped and all output assertions complete,
  `case_lease.cleanup()` removes the issued root; and
* the next loop iteration constructs under a fresh issued root rather than
  inheriting the preceding root's `evidence`, stage, authority, runtime, or
  disposable children.

The final bullet must be proven from real per-case allocation and cleanup, not
from a fake lease or `exist_ok` monkeypatch.  The test must fail closed if the
helper reports a replaced root, unsafe mode, or descriptor-identity mismatch;
it must never delete a replacement directly.  Existing C-30 hostile tests for
ambient temporary variables, `/tmp`/mode/symlink/unsafe-root rejection, and
descriptor cleanup remain required and unmodified.

Run the bounded topology regression and both full looped tests first.  On a
local checkout lacking the native descriptor-custody extension, record the
known capability block rather than build or emulate it.  The authoritative
GREEN for the two original cases is their next controller-owned hosted
Foundation run, where the actual extension path is available: all six
pre-link and three post-link boundaries must complete without a repeated
`evidence` creation collision and must retain their original crash-output
claims.  This repair does not authorize changing the test to skip or xfail
when the native extension is unavailable.

At minimum record `git diff --check`, the unchanged C-30 forwarding/consumer
tests, the focused closure selection, `make audit`, `make check-contracts`,
and `make check-secrets` where dependencies permit.  Do not run an external
engine build, acquire external inputs, mutate runtime state, or run live
execution.

## Review and stop conditions

An independent reviewer must check the exact diff and source map above,
confirm that the only tracked test-source file is the controller-closure
module, and verify that every per-case cleanup happens after—not before—the
child wait and all scenario assertions.  The review must reject any reset of
the old root, ambient-root fallback, permissive evidence creation, swallowed
cleanup failure, unverified recursive cleanup, or production/native/validator
change.

Stop and report **NOT READY** instead of widening scope if the existing
factory cannot supply a fresh direct `/tmp` root per case; a per-case root
cannot be descriptor-cleaned after a completed case; the full scenario needs
the root after cleanup; the current external native-custody block prevents the
required hosted proof; or a fix requires a production/runtime/validator,
workflow, authority, or Make change.  The separate C-37 command-registry
xattr design and all external blocks, including I-02b corpus authority,
remain untouched.
