# Package-6 portable fixture closure design

**Status:** design only.  This is a bounded closure plan for C-21 I-04 at
`e6c1809cfddb6d4605317e2bf760b6d933480c3f`; it does not authorize an
implementation, a CI/workflow change, a runtime-policy change, external input,
or a T-G03-green claim.

## Decision and evidence

Foundation run `31629180749` at the stated head ended with `307 failed, 5181
passed, 281 skipped, 29 deselected`.  C-21 independently classified 245 as
source-owned portable-fixture failures: 119 in
`tests/foundation/test_package6_runtime_approval.py`, 109 in
`tests/foundation/test_package6_controller_closure.py`, three in
`tests/runtime_release/test_v2_runtime_config.py`, one critical-coverage test,
and 13 command-registry tests.  The remaining 62 are sealed-host or external
authority/capability blocks and are out of scope.

The comparison run `31623400988` at `ab404fb` ended with `113 failed, 5366
passed, 281 skipped, 29 deselected`.  It verifies that the earlier literal-home
fixture defect was removed, but it does not authorize a home fallback.  The
combined run also confirms the previous I-01, I-03, and I-02a repairs on the
host: the portable audit passed, child-environment ancestor failures are gone,
and neither independent component-snapshot failure remains.

The central invariant is deliberately asymmetric:

* `make ci` stays strict and `make ci-portable` continues to allocate and
  export its private `RUNNER_TEMP` child as `TMPDIR`, `TEMP`, and `TMP`.
* Package-6 *test material* may use a separately leased direct child of `/tmp`
  only because `staging_v2._validate_private_root()` already defines that
  exact trusted sticky-root contract.
* Production validators, release/authority identities, manifests, workflow
  routing, live approvals, and gate membership do not change.  The test suite
  must keep proving that a runtime root below `RUNNER_TEMP` is not silently
  accepted as Package-6 staging material.

`I-02b` remains separate: its three real-corpus tests require absent, externally
attested corpus authority.  This design neither skips nor replaces them, and
cannot make T-G03 green.

## Slice P6 — exact `/tmp` Package-6 staging material (231 failures)

### Source map and cause

The shared approval fixture's `_staging_material()` eventually calls
`build_staging_release_authority_v2()`.  Its disposable root was beneath the
pytest root supplied by the portable job, for example:

```text
/home/runner/work/_temp/trading-agent-ci-portable.yiYAWDPhe9/pytest-of-runner/.../package6-staging
```

`packages/runtime_release/staging_v2.py::_validate_private_root()` rejects it
before authority construction.  That code accepts only a non-root, mode-0700,
current-euid directory below the exact root `/tmp`; `/tmp` itself must be
root-owned mode `01777`, and every intermediate descendant must be a
non-symlink, euid-owned directory without group/world-write or special bits.
The caught failure is intentionally generic (`StagingAuthorityError: staging
authority cannot be built`), so all affected controller/config failures are
downstream of the same fixture construction failure.

The closure should introduce a private test helper, for example
`tests/foundation/_package6_staging_fixture.py`.  It is test-only and private
by naming and import policy.  The complete permitted consumer/call chain is:

```text
test_package6_runtime_approval._record
  -> _staging_material -> build_staging_release_authority_v2
test_package6_runtime_controller._sealed_runtime_fixture
  -> imports/calls approval._record
test_package6_controller_closure._finalizer_arguments
  -> calls runtime-controller._sealed_runtime_fixture
test_v2_runtime_config._staging_authority
  -> its separate local staging-authority construction
```

Only these four test modules may import the helper or receive its lease.  The
Package-6 integration test only consumes material already constructed elsewhere
and is not a helper consumer.

The helper leases an entire fixture root directly below `/tmp`, instead of
relocating any production root or only one late authority file.  A local
fixture named to make its special purpose explicit (for example,
`package6_staging_lease`) owns the lease for the outer pytest test lifetime.
It is not a global fixture, an ambient tempfile setting, or a convenience root
for unrelated tests.

For the transitive controller path, authorize the smallest test-only ownership
threading: `_finalizer_arguments(..., lease=...)` receives the outer fixture's
lease and passes the same object to
`_sealed_runtime_fixture(..., lease=...)`; that helper passes it unchanged to
the approval fixture's `_record(..., lease=...)`/`_staging_material()` path.
The local v2 helper receives its own outer test lease.  No inner helper creates
or closes a substitute lease.  The exact names may follow local style, but the
same fixture-owned object and explicit chain are required.

### Required helper contract

The helper must:

1. Create its root exclusively with `tempfile.mkdtemp(dir="/tmp", prefix=...)`.
   It must neither consult nor export `TMPDIR`, `TEMP`, `TMP`, `$HOME`, or
   `RUNNER_TEMP` for this allocation.
2. Immediately set mode `0700`, `lstat` the result, and require: direct parent
   exactly `/tmp`; directory and not symlink; current effective uid; exact mode
   `0700`; no special bits.  It must separately check `/tmp` against the same
   root-owned `01777` contract used by the production validator.  Record
   `(st_dev, st_ino)` for the issued root.
3. Yield that root for the affected outer test's complete lifetime.  The
   fixture's pytest finalizer, not `_record()`, `_staging_material()`,
   `_sealed_runtime_fixture()`, or `_finalizer_arguments()`, owns cleanup.
   Thus controller/finalizer assertions retain valid authority and bundle paths
   until the test returns.  The existing Package-6 fixture builders continue
   to seal stage files and dynamic authorities as they do now; no artificial
   authority document is added.
4. Clean up only after reopening through a `/tmp` directory descriptor with
   `O_DIRECTORY|O_NOFOLLOW`, rechecking the issued device/inode, type, uid and
   mode.  Recursive deletion must be descriptor-relative and symlink-safe.  If
   the issued root was replaced or fails verification, the helper fails closed
   and does not follow or delete the replacement.

This is a test fixture lease, not a production-safe-root abstraction.  It may
not be imported by `packages/**`, `services/**`, `ops/**`, `scripts/**`, or the
Makefile.

### Exact allowed files

The initial implementation slice may change only:

* new `tests/foundation/_package6_staging_fixture.py`;
* `tests/foundation/test_package6_runtime_approval.py`;
* `tests/foundation/test_package6_runtime_controller.py`;
* `tests/foundation/test_package6_controller_closure.py`; and
* `tests/runtime_release/test_v2_runtime_config.py`.

The runtime-controller and controller-closure changes are limited to the
fixture-scoped lease parameter/call-site threading above.  They must not add a
module-global lease, an autouse/global environment override, a deferred process
cleanup, or cleanup at `_record()` return.  Do not widen the slice implicitly.

In particular, this slice must not edit `Makefile`, `.github/workflows/**`,
`packages/runtime_release/staging_v2.py`, any validator, authority/manifest,
or runtime/production fixture.  It must not add a `/tmp` Make fallback or a
global tempfile environment override.

### TDD and hostile regressions

RED first, in focused tests:

* under a synthetic non-`/tmp` fixture root with otherwise private `0700`
  metadata, Package-6 authority construction rejects the root; and
* the current affected approval/controller/v2 fixture setup reproduces the
  `StagingAuthorityError` when it is fed the portable pytest root.

GREEN uses only the leased direct `/tmp` root.  Add tests that assert all of
the following:

* the issued staging root is a direct `/tmp` child and exactly `0700`, owned by
  the current euid; the complete production construction and existing tamper
  tests succeed from it;
* a `RUNNER_TEMP`-derived root, or any other root outside `/tmp`, remains
  rejected by the actual staging validator; this must be a real rejection
  probe, not a monkeypatch of `_validate_private_root()`;
* `/tmp` itself, `0755`/`0770`/sticky staging children, a symlink root,
  wrong-owner metadata, an unsafe intermediate, and a root replacement after
  issuance are rejected or cause fail-closed cleanup as applicable;
* the helper never reads ambient tempfile variables and no affected test sees
  its ordinary runtime root changed to `/tmp`; and
* a static import/consumer regression permits exactly the four enumerated
  Package-6 modules and checks the approval -> runtime-controller ->
  controller-closure lease forwarding chain; and
* a controller-closure lifetime regression proves that the lease is still
  valid throughout `_finalizer_arguments()` and is cleaned only by the outer
  fixture finalizer after the test completes.

The negative root test can allocate its non-`/tmp` case in a test-owned
directory outside `/tmp` solely to prove the validator's path predicate, then
perform verified cleanup.  It is not a runtime fallback and must not name a
user home directory.

Run focused approval, controller-closure, and v2-runtime-config groups before
their combined root-test selection.  An independent reviewer must inspect the
cleanup race boundary and prove that the production validator diff is empty.

## Slice CC — critical-coverage legacy directory (one failure)

`test_critical_coverage_tightens_current_user_owned_writable_ancestor` creates
`legacy-evidence-root` as mode `0777` and asks
`check_critical_coverage._prepare_private_directory()` to create a child
`reports` directory.  The algorithm only tightens a current-user writable
intermediate below a root-owned sticky ancestor (the `/tmp` model); otherwise
it rejects the unsafe writable directory.  In the hosted path the root is
under the private, non-sticky `RUNNER_TEMP` allocation, so the intermediate
remains `0777` and the test's expected `0700` assertion fails.

Choose the smaller, policy-preserving test correction, not an algorithm
generalization: make the deliberately legacy writable directory the final
requested report directory.  The existing explicit `ancestor == absolute`
branch then hardens the current-user-owned final directory to `0700`.  Rename
the test to describe a legacy report root, rather than a writable intermediate.

Add the paired hostile regression: a current-user `0777` *intermediate* under
a non-sticky private root must still cause `CoverageGateError`, must remain
untrusted rather than be silently accepted, and must not leave a report or
temporary artifact.  Retain the existing symlink report-directory, predictable
temporary-symlink, parent binding, and early-failure cleanup tests.

This slice changes only `tests/governance/test_test_governance.py`.  It does
not edit `scripts/check_critical_coverage.py`: hardening arbitrary writable
non-sticky ancestors would require a new descriptor-race/ownership policy and
is not justified by this one test.  If the final-directory branch cannot meet
the stated assertions on the hosted runner, stop and request a separate
security design; do not relax the writer's rejection.

TDD: RED is the current portable-root intermediate case; GREEN is the
self-final legacy report-root test plus the retained non-sticky-intermediate
rejection.  Independently review the exact final-vs-intermediate distinction.

## Slice CR — command-registry identity fixture diagnosis (13 failures)

The 13 failures are not evidence that a release validator or job-type policy is
wrong.  They persist below the corrected `RUNNER_TEMP` root.  The positive
fixture in `tests/jobs/test_command_registry.py::_deployment()` changes only
`services.job_worker.command_registry._lstat` to report uid 0, then asks the
real v1 command-attestation path to run.  `_verify_authority_release()` catches
every internal verifier failure and intentionally returns the generic
`COMMAND_RELEASE_NOT_APPROVED / immutable release attestation failed` result.
The three `COMMAND_TYPE_INVALID` differences occur because capability
attestation fails before `build_command()` reaches job-type validation.

The fixture's private lstat seam does not model all relevant evidence.  The
test-local `fixture_verify_release()` calls `_validate_artifact()` and
`_walk_release()`, which in turn validate ancestor metadata and invoke the
separate `_listxattr()` seam.  Hosted output contains only the intentionally
collapsed outer error, so it cannot establish whether the unmodeled failing
fact is an ancestor mode/xattr, an identity surface, or another fixture
assumption.  Changing expected reason codes, treating `RUNNER_TEMP` as root
authority, or loosening production checks is therefore forbidden.

### Diagnostic-only first step

The first CR patch is test-only instrumentation in
`tests/jobs/test_command_registry.py`; it must not edit
`services/job_worker/command_registry.py` or
`packages/runtime_release/manifest.py`.

Replace the broad uid-only fake with an explicit fixture-identity evidence
object scoped to the issued release root, manifest, release entries, and the
finite ancestor chain actually passed to the command-registry seams.  It must:

* call real `lstat` first and preserve type, mode, size, device and inode;
  model only root uid (and, if a later checked path requires it, root gid) for
  the declared fixture set;
* route `_listxattr` through a recording seam that preserves actual observed
  attributes and records each inspected path; it must never silently return an
  empty set merely to obtain a green result;
* make an unexpected path or a path outside the declared fixture/ancestor set
  a test failure, not a permissive identity; and
* report a structured, redacted in-test diagnostic distinguishing lstat
  ownership/mode/type, xattr, manifest/exact-set, digest, and race outcomes
  before the outer command code collapses them.  No absolute runner-home value
  belongs in an assertion or tracked receipt.

The initial run must reveal one concrete rejection class on the hosted runner.
Only then may a second design decide whether a faithful fixture model exists.
There is no approved green implementation route yet.

If the observed failure is an ambient ancestor xattr/identity outside the
issued release model, a possible follow-up is a test-owned *declared* identity
model that supplies verified root-owned, no-xattr metadata only to the private
command-registry seams for that finite model.  It must retain real bytes,
inode/device, modes, exact entry manifest, and all hostile tests.  The model
must be overridden by existing wrong-owner, writable/special-mode, symlink,
extra/missing/changed-file, xattr, mutation, re-attestation, expiry, and
capability-single-use tests; none may become a mock-only pass.  A real-xattr
test must explicitly route its selected target to the actual attribute set (or
the injected hostile attribute), while every other declared path remains
auditable.

If the evidence instead shows that the release model requires a capability that
cannot be represented through these existing private test seams without
changing `verify_release`, its policies, authority identity, or a protected
runtime root, classify that exact capability as a new authority conflict and
stop.  Do not guess a fixture exception.

Allowed CR files: `tests/jobs/test_command_registry.py` only.  Prohibited:
command-registry production source, release-manifest source, validators,
authority objects, xattr policy, reason-code ordering, Make/workflow, and all
runtime/live surfaces.

TDD for the diagnostic patch: RED captures the generic hosted-equivalent
fixture failure while asserting that the new recorder identifies its inner
class; GREEN is only the recorder's deterministic classification, not a
release-policy bypass.  A second RED/GREEN/review loop is mandatory if a
faithful test fixture is subsequently proposed.  The three inactive-job tests
keep expecting `COMMAND_TYPE_INVALID`; they may turn green only after positive
attestation succeeds and execution reaches type validation.

## Validation, review, and stopping conditions

Each slice is independently implemented, reviewed, and committed before the
next.  For each, record exact RED and GREEN commands; run its focused tests,
`git diff --check`, `make audit-portable`, `make check-contracts`, and
`make check-secrets` when dependencies are available.  Report environment
limited checks rather than altering dependencies or locks.  No engine build,
corpus acquisition, provider call, service, database, broker/exchange path, or
live action is permitted.

After all three independently approved source slices, a controller-owned
Foundation rerun must execute the unchanged `make ci-portable` chain and
provide the authoritative result.  Success for I-04 requires the 231 Package-6
failures, the coverage failure, and the 13 command-registry failures to be
absent while hostile policy tests remain.  It does not make any of the 62
external failures pass and does not close I-02b.

Stop immediately if: the direct `/tmp` contract is unavailable; helper cleanup
cannot be made descriptor-verified; a Package-6 runtime validator must change;
the coverage fix requires accepting a non-sticky writable intermediate; the
command diagnostic identifies a non-representable protected identity; any
strict/portable target, validator, authority, manifest, workflow, or live flag
would need to change; or a proposed result would skip, xfail, delete, or
reclassify I-02b.  In each case report **NOT READY** with the narrow required
authority rather than broadening scope.

## Non-goals and final status

This document changes no code and approves no release.  It preserves all
strict CI and portable `RUNNER_TEMP` allocation invariants.  Bubblewrap,
fakeroot/user-namespace, semantic-runtime, legacy-producer, and real-corpus
groups remain external or separately governed.  In particular:

```text
I-04: design path only; NOT READY pending independent slice work and hosted rerun
I-02b: unresolved corpus-authority contradiction
T-G03: NOT GREEN
```
