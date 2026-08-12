# Package-6 controller-closure case-isolation identity amendment

**Status:** design only.  This narrowly amends the C-38 case-isolation
design after hosted Foundation `31640288425` at
`df218baf33fcc747927a207b3bad13cdbc3a987e` reported the two repaired
controller-closure loops as source-owned failures.  It authorizes neither an
implementation nor a hosted-green claim.  It does not change a production
validator, runtime authority, workflow, Make target, native component,
engine/build input, external artifact, or live-execution setting.

## Corrected invariant

C-38 requires a distinct *active scenario* lease: a real factory call inside
each loop iteration, the exact lease passed to the runtime construction, and
descriptor-checked cleanup only after the child has been reaped and that
scenario's output assertions finish.  It does **not** require a device/inode
or pathname to be globally unique after its earlier issued root has been
removed.

The current implementation adds the completed lease's root, `(st_dev,
st_ino)`, and evidence-child pathname to three history sets and rejects a
later fresh lease if one of those historical values recurs.  The comparison is
made before the current value is added, not after it; nevertheless it is an
invalid cross-lifetime requirement.  A filesystem may reuse an inode after
descriptor-checked removal, and `tempfile.mkdtemp()` promises a unique name
only while a directory of that name remains present.  Neither reuse indicates
shared active staging state or a failure of `Package6StagingLease.assert_valid`.

The hosted failure therefore does not justify weakening allocation or cleanup.
It identifies an over-strong test assertion.  The isolation contract is:

1. before construction, the just-issued lease passes `assert_valid()`, is a
   direct child of `/tmp`, and its fixed `evidence` child is absent;
2. the complete runtime construction and crash/output assertion path use that
   exact live lease, which remains valid and owns a real `evidence` directory;
3. the existing `finally` calls descriptor-validated `cleanup()` without
   suppressing errors; and
4. after cleanup, that scenario's root and `evidence` child are absent before
   the loop may begin another construction.

This proves no scenario can inherit an existing evidence/stage/authority/
runtime tree.  It intentionally makes no claim about identities of roots that
have already been removed.

## Exact implementation boundary

Change only `tests/foundation/test_package6_controller_closure.py`:

* in each of the two looped crash tests, remove the three completed-case
  history sets (`issued_roots`, `issued_identities`, and
  `issued_evidence_roots`), their three historical-membership assertions, and
  their three post-cleanup `add()` calls;
* retain the existing per-case factory call, `assert_valid()` checks,
  direct-`/tmp` assertion, pre-construction `not evidence_root.exists()`,
  explicit `lease=case_lease` forwarding, real child crash/output assertions,
  `finally: case_lease.cleanup()`, and post-cleanup absence assertions; and
* retain the same-module AST topology guard.  Tighten it only if necessary to
  make clear that its subject is factory-before-finalizer and
  cleanup-after-output ordering; it must not invent a historical
  pathname/device/inode uniqueness predicate.

No helper, approval fixture, runtime-controller fixture, Package-6 production
source, validator, authority, native component, Makefile, workflow, or other
test module may change.  Do not replace the assertions with an ambient temp
root, reset a root, use `exist_ok`, clear a history set, mock the factory, or
skip/xfail either case.

## RED, GREEN, and review evidence

The witnessed RED is Foundation `31640288425` at `df218ba`: both named crash
tests reach the stale cross-lifetime identity predicate after a completed
lease.  The implementation receipt must preserve the exact failing nodes and
must distinguish this from a production validator, native-custody, or
portable-root failure.

For GREEN, first run the bounded AST topology test, then the two full looped
tests and the existing Package-6 lease hostile/consumer/forwarding checks.
The runtime proof must still use the real factory and verify all four active
scenario conditions above.  If this isolated checkout stops before the crash
boundary at the known sealed native-custody/source-binding gate, record that
boundary without building, emulating, substituting authority, or skipping
tests.  The next controller-owned hosted Foundation run remains authoritative:
all six pre-link and three post-link scenarios must retain their original
crash-output expectations without a fixed-child collision or stale
historical-identity failure.

An independent review must reject any removal of live-lease validation,
pre-construction empty-evidence check, post-output descriptor cleanup, or
post-cleanup absence check.  It must also reject a change outside the one
closure test module or a claim that temporal inode reuse proves simultaneous
lease sharing.

## C-38 status

This amendment is required.  C-38's word “fresh” means a newly issued,
currently valid and empty lease for each active scenario; it was implemented
as stronger historical root/device/inode/evidence-path uniqueness and thereby
became nondeterministic.  C-38 otherwise remains in force: its approved scope,
factory-only allocation, no-reset rule, lifetime ordering, and hostile helper
coverage are unchanged.

## Stop conditions

Stop and report **NOT READY** rather than widen scope if removing only the
stale historical predicates fails to restore the two source tests; the real
factory cannot issue and cleanup one active direct-`/tmp` root per scenario;
the child/output assertions require access after cleanup; any remaining
failure needs a helper, production, validator, authority, workflow, native,
or external change; or the hosted native-custody proof remains unavailable.
The command-registry, sealed-capability, and real-corpus authority blocks are
unchanged.
