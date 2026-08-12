# Hosted Foundation source-closure design

**Status:** design only; **verdict:** I-01 and I-03 have a narrow source-only
repair path.  The snapshot half of I-02 has one as well.  The real-corpus half
of I-02 is a genuine source/spec conflict at the current head, so a truthful
source-only patch cannot make T-G03 green without an additional, independently
attested corpus authority.  It must not be disguised as a runtime deferral.

## Scope, evidence, and invariant

This design is for the isolated branch head
`ab404fb91a618dc93d6d5cd37d38c548dec30357`.  It changes no live approval;
`LIVE_EXECUTION_ENABLED=false`, `LIVE_TRADING_APPROVED=false`, and
`LIVE_TRADING_ENABLED=false` remain required.  It authorizes neither a workflow
edit, a push, a service, a database, a broker, a release, an external engine
build, Bubblewrap installation, nor acquisition/copying of an external research
corpus.

Foundation run `31623400988` is the controlling hosted evidence.  It ran the
exact head on `ubuntu-24.04`, reached `make ci-portable`, and passed portable
audit with `authority_mode=portable`, plus D0, contracts, and secrets.  The
terminal aggregate was `113 failed, 5366 passed, 281 skipped, 29 deselected`.
This is after the `ab404fb` fixture repair removed the previous 201 literal-home
temporary-root setup errors.  The remaining Important gates are therefore not
masked by the old problem:

| Gate | Hosted evidence | Classification |
|---|---|---|
| I-01 | 48 fixture failures: unsafe ancestor or release attestation rejection | source-owned portable temporary-root defect |
| I-02a | backend and dashboard independent snapshot tests return `E_AUTHORITY` | source-owned portable test-topology defect with an embedded-proof remedy |
| I-02b | three real-source tests call `/home/thenam176/.hermes/crypto-research` and fail before planning | missing external corpus authority; no equivalent embedded proof exists |
| I-03 | `socket.getaddrinfo` test is denied at a write-capable `open` during interpreter import/cache activity | source-owned, host-independent audit-test contract defect |

The invariant for every repair is: strict authority remains explicit and
fail-closed; portable proof is explicit and never selected from ambient
authority availability; the production ancestor policies and expected reason
codes remain unchanged; no gate is skipped, xfailed, deleted, or silently
reclassified.

GitHub documents `GITHUB_WORKSPACE` as the checkout directory and `RUNNER_TEMP`
as a job-cleaned temporary directory; it recommends variables rather than
hard-coded filesystem paths.  The actual failed run shows the Ubuntu checkout
at `/home/runner/work/Trading-Agent/Trading-Agent` and runner temporary paths
under `/home/runner/work/_temp`.  The hosted proof must use `RUNNER_TEMP`, not a
literal runner home.  The GitHub documentation does *not* promise POSIX mode or
ownership for that path, so the implementation must prove the actual path
through the existing production validators rather than infer safety from the
documentation alone.  References: [GitHub default variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables#default-environment-variables),
[GitHub-hosted runners](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners).

## I-01: use the GitHub-owned temporary base, retain all ancestor policy

`packages/runtime_release/paper_application/environment.py` rejects every
ancestor that is not a directory owned by root or the runtime uid, or that has
group/world-write or special bits.  Its sole `/tmp` exception is explicitly
limited to issued Package 6 staging material.  `ops/phase4b/verify-release.py`
independently rejects a writable or special-mode ancestor.  Neither validator
may be changed.

The smallest compatible root is a mode-0700 `mktemp` child of the
GitHub-supplied `RUNNER_TEMP`, not a child of `/tmp`, `GITHUB_WORKSPACE`, a
literal `/home/...`, or an ambient `$HOME`.  On the recorded runner that is
under `/home/runner/work/_temp`, outside the checkout (so it cannot dirty the
audit), and its complete ancestor chain is exactly what the production
validators inspect.  `RUNNER_TEMP` is required rather than falling back to
`/tmp`: lack of a safe supplied base is fail-closed, not an excuse to broaden
the `/tmp` exception.

### Exact production/test changes

1. In `Makefile`, change only the `ci-portable` temporary-root allocation to
   require `RUNNER_TEMP` and allocate
   `mktemp -d "${RUNNER_TEMP:?}/trading-agent-ci-portable.XXXXXXXXXX"`.
   Preserve the existing `0700` chmod, uid/mode `stat` assertion, trap, safe
   cleanup, `TMPDIR/TEMP/TMP` exports, and one dispatch to
   `ci-portable-private`.  Strict `ci` remains byte-for-byte unchanged.
2. Extend `tests/foundation/test_d0_closure.py` and
   `tests/consolidation/test_repository_shape.py` to require the exact portable
   `RUNNER_TEMP` allocation and prohibit `/tmp/trading-agent-ci-portable` in
   that recipe.  Keep the all-non-runtime-gates and single-dispatch assertions.
3. In `tests/jobs/test_child_environment.py`, add one focused regression using
   its existing custom `tmp_path` fixture: first demonstrate that a child made
   under `/tmp` is rejected as `ENVIRONMENT_ROOT_ANCESTOR_UNSAFE`; then use the
   fixture-provided root, create the normal `0700/0711` roots through
   `_settings`, and require `ResearchEnvironmentSettings.from_source({})` and
   `build_child_environment()` to succeed.  This test fails if portable Make
   puts `TMPDIR` below `/tmp` and passes only after Make supplies an ancestor
   accepted by the real validator.  Existing successful command-registry and
   standalone-release tests are retained as the cross-check for their distinct
   verifier.

No fixture expected outcome changes: the three command reason-code failures in
the hosted run are downstream of failed release attestation and must continue
to assert their original job-type results after the attestation succeeds.

### I-01 TDD and validation

* **RED:** add the Make topology assertion and the explicit `/tmp`-reject/
  fixture-root-accept test.  The topology test fails against the current
  `/tmp` recipe; the acceptance half fails under a forced `/tmp` `TMPDIR`.
* **GREEN:** make the one portable recipe substitution above; do not touch
  `environment.py`, `verify-release.py`, command-registry authority code, or
  expected job reason codes.
* **Focused proof:** run the three affected modules:
  `tests/jobs/test_child_environment.py`, `tests/jobs/test_command_registry.py`,
  and `tests/runtime_release/test_standalone_verifier.py`, plus the two Make
  topology modules with `RUNNER_TEMP` set to a freshly created, owner-checked,
  non-sticky test directory.  A direct `/tmp` control must fail exactly at the
  existing ancestor policy.
* **Hosted proof:** Foundation must show these 48 failures absent while the
  current `/tmp`-ancestor rejection tests still pass.  This is the required
  actual owner/mode proof; no documentation claim substitutes for it.

## I-02: keep strict evidence, use embedded component proof only where it exists

### Existing exact APIs and records

For component snapshots, the source already contains the needed immutable
record and verifier split:

* `ops/consolidation/source-authority.json` pins core, backend, and dashboard
  identities; `parse_source_authority()` parses that fixed schema without
  resolving external repositories, while `load_source_authority()` requires
  their immutable Git objects.
* `ops/consolidation/backend-source-manifest.json` pins backend commit
  `59578f984b72d5d03583a2c06b15a53a224b31c8`, tree
  `54e688e9f144aecd2ee204ab95953f7c57069d3c`, 135 entries, blobs, modes,
  sizes, SHA-256s and aggregate digest.  The dashboard manifest analogously
  pins commit `84627f16e9753b1104d661697720b93897f27d27`, tree
  `792f572dea8f819438785e43ee05e07c5b6567bd`, 223 entries and the same class of
  immutable fields.
* `scripts/verify_component_snapshot.py::verify_snapshot()` performs strict
  authority resolution through `_validate_source()`.  Its
  `verify_embedded_snapshot()` counterpart still parses the authority and
  canonical manifest, verifies component/policy identity, current regular
  files, or the exact introduction revision's Git blobs and SHA-256s; it merely
  does not resolve absent external authority repositories.
* `scripts/audit_canonical_repo.py` already uses that embedded API only after
  explicit `--portable`, and still rejects all-present or partial authority as
  `E_AUTHORITY`, rejects release+portable, checks introduction history, and
  checks the tracked authority/manifests as immutable evidence.

The real research checks have no corresponding record.  The only matching
tracked material is prose in Phase 3/3B implementation reports and scalar
assertions in `tests/control_api/test_real_data_plan.py`,
`test_phase3b_backfill.py`, and `test_phase3b_source_analysis.py`.  It records
the combined inventory hash `dbc941...c7b4ce` and counts, but has neither an
immutable entry inventory, per-file digests/blobs, a signed/attested source
manifest, nor the corpus itself.  In particular, no API can recompute that
hash from Git objects because the cited corpus is deliberately external and
untracked.

### I-02a: component snapshots — feasible

Add a root pytest option named `--portable-embedded-proof` in
`tests/conftest.py`, default false.  It is set only by the explicit portable
Make target chain (a target-specific `PYTEST_ADDOPTS +=
--portable-embedded-proof` propagated to `test` from
`test-all-portable-private`); strict targets never set it.  This is not an
authority fallback: the option selects a verifier with no authority lookup and
does not alter audit authority mode.  The option must be rejected or ignored by
production code; it is test-harness-only.

Refactor only the final verifier portions of
`tests/consolidation/test_backend_snapshot.py` and
`tests/consolidation/test_dashboard_snapshot.py` behind a small local helper:

* normal strict invocation keeps its current subprocess call to
  `verify_component_snapshot.py`, and therefore keeps asserting that
  `load_source_authority()` can resolve the three external repositories and
  regenerate the canonical manifest;
* explicit portable invocation imports `parse_source_authority` and
  `verify_embedded_snapshot`, obtains the same independently discovered
  introduction commit, and asserts the same component identity, policy,
  entry/blob/mode/size/digest inventory, and historical source snapshot.

Add adversarial tests for (a) option absent -> strict API is selected, (b)
option present -> embedded API is selected, (c) malformed authority,
manifest identity drift, aggregate drift, changed introduction blob, and
shallow history all fail.  Existing portable-audit tests already cover most of
the latter set; the focused test must prove the direct snapshot test cannot
accidentally call strict `verify_snapshot()` in portable mode.  No test is
skipped: each mode runs its selected full proof.

### I-02b: real research corpus — not feasible under current rules

The three failed tests are:

* `test_reviewed_real_source_builds_exact_approved_apply_plan`,
  `build_real_plan(REAL_ROOT)`;
* `test_real_backfill_plan_has_only_approved_evidence`,
  `build_phase3b_backfill_plan(REAL_ROOT)`;
* `test_real_phase3b_source_analysis_has_reviewed_exact_counts`,
  `analyze_phase3b_sources(REAL_ROOT)`.

All invoke `planner.plan_migration()` and require the real directory before
any planned inventory can be constructed.  A miniature/generated fixture
would validate parser behavior, but cannot prove the reviewed corpus hashes,
2,186/16,517/23,961 counts, 41,039 lineage rows, quarantines, or direct-field
provenance.  Calling it an equivalent portable provenance proof would create
synthetic research evidence.  Copying the corpus, creating a new assertion-only
JSON record from the existing test constants, or treating a prose report as a
sealed manifest would each violate the evidence invariant.

Accordingly, **no portable test-harness mode is authorized for these three
real-corpus tests today**.  Normal strict tests remain correct and must retain
their direct corpus assertions.  To make a future portable equivalent possible,
an owner must separately provide a reviewed immutable research-source manifest
whose identity is bound to a source authority (or a hermetic approved corpus
artifact), followed by its own design/review packet.  That is new authority and
external input, outside this source-only closure.

This is a genuine plan/spec conflict: the current `test-all-portable-private`
requires all non-runtime tests, the three tests require absent external
evidence, and the requested constraints forbid every honest way to make that
evidence present or replace it.  Therefore T-G03 cannot be declared feasible
or green from a source-only I-01/I-03 patch.  The controller must obtain narrow
authority for a separately attested corpus-proof design, or change the
continuation's gate definition through review; neither is a deferral, skip, or
xfail.

## I-03: preserve fail-closed policy and make the socket test deterministic

The current test helper writes `sitecustomize.py`, installs the audit hook, and
then lets CPython create cache files on a cold hosted runner.  The hook correctly
denies that write-capable `open` before the user program reaches
`socket.getaddrinfo`; the current expected `socket.` substring is therefore an
incorrect temporal assumption, not evidence of network access.

The safest intended contract is stronger and deterministic: do **not** add an
import-time allowlist for `open` or any socket event.  Instead, make the test
child set `PYTHONDONTWRITEBYTECODE=1` before Python starts, so its own cache
side effect is removed and each audited socket program reaches its target
socket event.  The audit hook remains fail-closed for every write-capable open,
all socket events (except the existing narrowly requested construction case),
process APIs, and filesystem mutation APIs.  This preserves the test's intended
event-specific assertion without permitting an operation through the guard.

Exact changes in `tests/nautilus_engine_cli/test_cli.py`:

1. `_restricted_environment()` sets `PYTHONDONTWRITEBYTECODE` to `"1"` in the
   returned child environment, replacing any inherited value; it does not alter
   the hook's `open` branch.
2. Keep `test_audit_guard_blocks_network_operations` asserting the exact
   `audit policy blocked socket.` prefix for all three programs.  Add a cold
   sitecustomize control (remove any cached `sitecustomize` bytecode before the
   subprocess) so the regression fails without the environment setting on a
   clean runner.
3. Add a paired adversarial assertion that a direct write still fails with
   `audit policy blocked write-capable open`, and retain the existing
   `allow_socket_construction=True` test requiring `socket.connect` denial.
   This proves bytecode suppression did not weaken the audit hook or permit a
   network operation.

TDD: RED is the cold-cache socket test under the current environment, which
reproduces the hosted write-capable-open result; GREEN sets the interpreter
switch and restores deterministic socket-event denial.  Run the focused audit
guard group plus all `tests/nautilus_engine_cli/test_cli.py` non-Bubblewrap
tests.  Never change the expected text merely to accept either outcome, and
never permit `open` only to satisfy the test.

## Deferred-runtime classification and sequencing

Bubblewrap, UID-map/fakeroot provisioning, sealed runtime materialization,
offline toolchains, semantic release inputs, and the engine provider remain
deferred only for their runtime/host-coupled packets.  They may remain covered
by retained tests only when those tests can exercise a source-owned fail-closed
boundary without requiring the unavailable capability.  This design neither
removes nor marks their existing failures as successful.

The research corpus is different: it is currently consumed by ordinary
unmarked aggregate tests and lacks a portable embedded verifier.  It is thus a
genuine closure conflict, not a deferred runtime check, until an approved
attested-corpus packet exists.

Implement in three independently reviewable slices:

1. **I-01:** Make root substitution plus the direct policy regression.  Review
   only `Makefile` and its three named tests; no validator/source authority
   change.
2. **I-03:** test-harness bytecode control plus socket/write adversarial tests.
   Review only `tests/nautilus_engine_cli/test_cli.py`.
3. **I-02a:** explicit pytest portable option and the two component snapshot
   test helpers.  Review the precise option propagation, strict-default
   behavior, embedded verifier selection, and tamper/shallow-history probes.

After each slice: run its RED then GREEN checks, `git diff --check`,
`make audit-portable`, `make check-contracts`, and `make check-secrets` where
dependencies are available; run the affected root and backend/dashboard tests;
then obtain independent review.  A hosted rerun follows only an approved
combined source patch.  It may close I-01/I-03/I-02a, but must still report
I-02b red rather than misstate T-G03 as green.

## Allowed and forbidden implementation surfaces

If and only if the conflict is separately resolved, the source-only slices may
touch these tracked files:

* `Makefile`;
* `tests/conftest.py`;
* `tests/foundation/test_d0_closure.py`;
* `tests/consolidation/test_repository_shape.py`;
* `tests/consolidation/test_backend_snapshot.py`;
* `tests/consolidation/test_dashboard_snapshot.py`;
* `tests/jobs/test_child_environment.py`;
* `tests/nautilus_engine_cli/test_cli.py`.

Forbidden: `.github/workflows/**`; production validators in
`packages/runtime_release/**`, `services/job_worker/**`, and
`ops/phase4b/verify-release.py`; source authority/manifests; test skip policy;
locks/dependencies/generated contracts; runtime/release/evidence artifacts;
research corpus/data; external engine toolchain/materializer; services,
databases, broker/exchange paths; and all live flags.  The only design-turn
tracked change is this document; the accompanying C-11 receipt is ignored.

## Completion decision

No implementation is approved by this document.  An implementation handoff is
**NOT READY** for a claim of T-G02/T-G03 completion because I-02b is unresolved.
It is ready only for the bounded I-01, I-03, and I-02a slices, with their
independent reviews and with the conflict kept open.  T-G03 can become GREEN
only after the corpus authority contradiction is resolved without a skip,
ambient fallback, synthetic evidence, or external-engine build.
