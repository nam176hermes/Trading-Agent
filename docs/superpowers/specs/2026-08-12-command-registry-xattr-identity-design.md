# Command-registry finite xattr-identity fixture design

**Status:** design only. This is the narrow C-42 follow-up to the hosted
Foundation C-37 classification at
`146655d592e38b5838a9c74dad8777aec12cbca2`. It authorizes neither an
implementation nor a hosted-green or T-G03 claim. It changes no production
command-registry, manifest, validator, authority, policy, reason-code order,
workflow, Make target, engine input, runtime state, or live-execution setting.

## Evidence and decision

Hosted Foundation `31636504627` ran the unchanged portable chain and reached
the v1 command-registry fixture in fourteen tests. The C-35 ordinary-path
diagnostic published the authoritative, redacted inner rejection before v1
collapsed it to `COMMAND_RELEASE_NOT_APPROVED`:

```text
category=xattr
outcome=present_or_unreadable
reason_code=COMMAND_ANCESTOR_XATTR_UNSAFE
paths=<filesystem-root>, <fixture-ancestor-5>, ...
```

This follows the existing test-local `_FixtureIdentityEvidence` through the
private `services.job_worker.command_registry._lstat` and `_listxattr` seams.
It currently calls the real `os.listxattr()` for every declared ancestor and
returns those ambient attributes unchanged. The fixture already models only
uid zero in `lstat`, while preserving the real type, mode, size, device, inode,
and gid. Thus the host result is evidence of an ambient filesystem attribute,
not of release bytes, manifest entries, digest verification, a command type,
or protected runtime authority.

The selected approach is a finite test identity *view*. It is not a claim that
the real filesystem root or any runner ancestor is clean. For the one
synthetic release fixture, the view supplies root-owned/no-xattr metadata to
the two declared private seams only. Every real observation still occurs first
and is recorded; all bytes, names, entries, modes, device/inode values, and
digests continue to come from the real fixture filesystem. This makes the
unit fixture represent a protected deployment without weakening the production
check that an actual deployed ancestor must have no xattrs.

### Rejected alternatives

* **Move the fixture below a different temporary root or accept ambient
  xattrs:** rejected. Portable Foundation must keep its existing private
  `RUNNER_TEMP` child, and neither a runner topology nor a host attribute is
  release authority.
* **Return an empty xattr tuple for every path:** rejected. That would hide
  hostile release xattrs and create an unbounded bypass.
* **Change `_validate_no_xattrs`, `_validate_ancestor_chain`,
  `_verify_authority_release`, a manifest, or a reason code:** rejected. The
  host result does not disprove those production policies.
* **Treat the real `/` metadata as a fixture-owned fact:** rejected. `/` and
  ambient parents remain immutable physical observations. The fixture model is
  a narrow test-only return value after that observation, never a statement
  about their true runtime identity.

## Fixture identity boundary

The implementation remains entirely in
`tests/jobs/test_command_registry.py`. It may evolve
`_FixtureIdentityEvidence`, `_deployment()`, and tests in that module, but may
not add a general helper module or alter any production import.

For each `_deployment()` invocation, construct one immutable declared path
set from exactly:

1. the release root;
2. the release-manifest path;
3. every expected release entry used to construct that manifest; and
4. each lexical absolute parent of those paths through its filesystem anchor.

Use `Path.absolute()` rather than resolving links when constructing this set;
the real `lstat` must retain symlink evidence. The set is finite and belongs to
that one fixture invocation. It must not expand after setup, learn a path from
a request, include a current working directory merely because it is ambient,
or be shared between test invocations.

There are deliberately two distinct records for each declared path:

| Record | Meaning | May reach command-registry validation? |
| --- | --- | --- |
| Physical observation | The real `lstat` or `os.listxattr(..., follow_symlinks=False)` result taken from the actual path, including immutable `/` and ambient ancestors. | Only the unchanged `lstat` fields copied into the view; observed xattrs themselves never do. |
| Fixture identity view | The controlled identity supplied only as the return value of this fixture's private `_lstat`/`_listxattr` monkeypatches. | Yes, only for this synthetic release fixture. |

For `lstat`, the view may replace only `st_uid` with zero. It must preserve the
real `st_mode`, `st_size`, `st_dev`, `st_ino`, `st_gid`, link count, and times.
For `listxattr`, the ordinary positive view returns the empty tuple, but only
after the real attributes have been observed and recorded. It may not change
or cache `Path.lstat`, `Path.stat`, `Path.open`, `Path.iterdir`, `read_bytes`,
`os.listxattr`, hashing, manifest construction, or digest comparison. The
existing fixture verifier continues to run the real private validator helpers
and the real file hashes.

The identity model's sole permitted bindings are the existing private
command-registry seams:

```text
services.job_worker.command_registry._lstat
services.job_worker.command_registry._listxattr
```

Existing fixture scaffolding may still bind its local `_verify_release` test
verifier and protected-authority inputs as it already does; the identity view
itself must not patch a production validator, `verify_release`, an authority
object, a manifest reader, or a global filesystem API. In particular, the
model cannot be imported by `packages/**`, `services/**`, `scripts/**`, a
workflow, or another test module.

### Dynamic boundary and completeness guards

Both model methods must call their real filesystem operation *before* applying
the view, append only a redacted path token to diagnostics, and then reject a
request outside the frozen declared set with `AssertionError`. Such a request
must receive neither a synthetic uid nor a synthetic empty xattr set. Add
bounded tests for both an undeclared `lstat` request and an undeclared
`listxattr` request using an existing path outside the fixture set. The
diagnostic remains `fixture_boundary / undeclared_path`; it must not disclose
the absolute path or attribute names.

On a successful positive attestation, an evidence assertion must prove that
all paths the command-registry seam inspected were declared and that the
fixture's required root, manifest, expected entries, and lexical ancestor
closure were physically observed. This is a dynamic path guard, not a
best-effort list: a missing expected observation or a request that the model
did not declare fails the test. It prevents a future source change from gaining
an ambient identity exception silently.

The normal positive diagnostic is `verified/no_rejection` and has no exception
note. If a hostile test causes the real fixture verifier to reject, the current
redacted canonical exception-note behavior remains: it records the classified
inner failure, synthetic path tokens only, and preserves the outer
`COMMAND_RELEASE_NOT_APPROVED`. It must never serialize real absolute paths,
actual xattr names, release bytes, manifest contents, or authority material.

## Xattr behavior and hostile overrides

The no-xattr view is only for the ordinary declared positive identity. A
test-specific override has priority for the selected declared path, after its
real observation:

* `inject_xattrs(path, attributes)` returns the explicit injected hostile tuple
  for that path. It remains bounded to the declared set and must be used by
  `test_attestation_rejects_any_extended_attribute_on_protected_paths` for its
  ancestor, root, manifest, interpreter, and data cases. The test must assert
  the intentional hostile diagnostic/outer rejection, not merely rely on a
  runner's unrelated xattr.
* A separate `require_actual_xattrs(path)` (or equivalently narrow explicit
  policy) returns the actual observed tuple for that one declared target. The
  real-xattr test must obtain its `_FixtureIdentityEvidence`, mark only
  `data/model.bin` for actual-xattr treatment, set a real test attribute with
  `os.setxattr(..., follow_symlinks=False)`, verify that the actual target
  reports it, and then run the real command-registry path. It may skip only
  when the filesystem cannot set the test attribute. It must not satisfy this
  test through `inject_xattrs`, an empty response, or an ambient ancestor.

The override state is per evidence instance and cannot be inferred from an
attribute name, a pathname prefix, `TMPDIR`, `$HOME`, `RUNNER_TEMP`, or an
ambient root. Actual observed attribute tuples may remain private in memory so
the test can prove real observation; redacted diagnostics must not expose their
names. An unreadable real `listxattr` still follows the production seam's
fail-closed `COMMAND_*_XATTR_UNSAFE` behavior and is not converted to an empty
fixture result.

## Existing behavioral coverage that must remain real

The implementation must preserve or strengthen these tests without changing
their production expectations:

* wrong-owner, writable, and special-mode cases wrap the bounded model's
  `lstat` return and modify only the selected result. Since type/mode/device/
  inode are otherwise real, the original rejection reaches the fixture
  verifier;
* the symlink case must continue from the real `lstat` type before any uid
  substitution;
* an extra entry is outside the frozen expected set and must fail the dynamic
  identity boundary rather than receive synthetic metadata; missing entries
  remain an exact-set mismatch, and changed bytes remain a real SHA-256
  mismatch;
* the mutation-after-attestation test must re-run the fixture verifier and
  reject the changed real bytes; neither metadata view may cache a successful
  hash or an old release walk;
* the capability single-use, expiry, full re-attestation budget, prepared-spawn
  expiry, and authority/semantic rotation tests must again reach their original
  downstream assertions only after a positive initial attestation. A repeated
  attestation must repeat real observations rather than reuse a prior view;
* the injected-xattr and real-xattr tests described above must each remain
  hostile and independent; and
* the inactive job-type cases must again reach `COMMAND_TYPE_INVALID` after
  positive release attestation. That is confirmation of the existing policy
  order, not a reason-code change.

Do not skip, xfail, delete, weaken, or replace any of these tests. Do not
convert an extra-entry boundary assertion into a permissive dynamically
declared path. The intended exact release set remains the manifest entry set
created from real fixture files.

## TDD and validation

### RED

The witnessed behavioral RED is the fourteen C-37 hosted failures, whose
ordinary path emitted `COMMAND_ANCESTOR_XATTR_UNSAFE` on a redacted ambient
ancestor. Before changing the evidence object, add a topology-independent
focused test that supplies a controlled nonempty real `os.listxattr` observation
for one declared ancestor. It must assert that the old evidence return is that
nonempty observed tuple, so the new expectation of an empty ordinary fixture
view fails before the implementation change. The test also asserts that the
physical observation was retained privately and redacted externally.

Run that test against the pre-change evidence implementation and record the
failure. Also retain the C-37 run ID, exact fourteen test names, and redacted
xattr reason as the hosted RED; do not substitute a local topology result for
that evidence.

### GREEN

After the finite view is implemented, run first:

1. the controlled ordinary-xattr-view RED test, both undeclared-path guards,
   the mode diagnostic, and the injected/real xattr tests;
2. the exact-set, missing/changed/symlink, wrong-owner/writable/special-mode,
   mutation, capability, expiry, re-attestation, and inactive-type selections;
   and
3. the complete `tests/jobs/test_command_registry.py` module under a private,
   non-sticky, mode-0700 temporary root, matching the portable fixture shape.

The local command must not use a literal user-home path or a `/tmp` fallback.
It may use an already provisioned `RUNNER_TEMP` child only after asserting that
the selected base and issued child are current-user owned, directories,
non-symlink, and mode 0700. If that safe root is unavailable, report that exact
environment limitation instead of altering temp-root policy. The existing
overridden `tmp_path` fixture intentionally makes a sticky `/tmp` root an
invalid local positive topology because real modes are preserved.

Run `git diff --check`, `make audit`, `make check-contracts`, and
`make check-secrets` where dependencies and external-authority preconditions
allow. Do not install dependencies, build an external engine, acquire corpus or
network input, start a service, touch a database, or use a live path. The
authoritative closure is a controller-owned hosted Foundation rerun of
unchanged `make ci-portable`: the fourteen command-registry failures must be
absent, all hostile tests must remain present, and the other sealed/external
groups must be reported separately.

## Exact allowed scope, review, and stop conditions

An approved implementation may modify only:

* `tests/jobs/test_command_registry.py`.

It may introduce no production source, test helper module, Make/workflow,
dependency, runtime, authority, or policy change. It must not edit
`services/job_worker/command_registry.py`, `packages/runtime_release/**`,
manifests, validators, `.github/**`, `Makefile`, or any live surface.

An independent review must trace every model return path and prove: the model
is bound only to `_lstat` and `_listxattr`; no undeclared request receives
synthetic metadata; all preserved stat fields and bytes/digests are real; the
normal no-xattr view cannot suppress an injected or real target xattr; the
redaction is maintained; and every hostile/downstream test above still has a
real rejection route. The review must also compare the production
command-registry and release-manifest diffs to empty.

Stop and report **NOT READY** rather than widening scope if the finite closure
cannot be asserted at runtime; the private seams cannot distinguish ordinary,
injected, and real-xattr paths; physical observations cannot occur before the
view; a hostile test needs an ambient exception; a positive result requires a
mode/uid/xattr relaxation outside this fixture view; or any production,
validator, authority, manifest, workflow, Make, engine, corpus, service,
database, or live change is proposed. The C-37 sealed capability groups and
I-02b corpus-authority contradiction remain unchanged.
