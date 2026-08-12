# Hosted CI portable test temporary-root repair design

## Decision

`BLOCKER_CLASSIFICATION: SOURCE_OWNED_PORTABLE_TEST_FIXTURE_DEFECT`

Foundation run
[`31619647114`](https://github.com/nam176hermes/Trading-Agent/actions/runs/31619647114)
ran `f00c35893d8664dfdac6d0727bd05df35527a53a` on
`codex/phase1-terra-autopilot-19627785c140`.  Its portable audit emitted the
following exact line before the test phase:

```text
head=f00c35893d8664dfdac6d0727bd05df35527a53a branch=codex/phase1-terra-autopilot-19627785c140 status=clean authority_mode=portable components=core,backend,dashboard result=PASS
```

Later in the same pre-temp-root-repair run, tests failed with
`FileNotFoundError` for `/home/thenam176/.cache/task7-environment-*` and the
aggregate was `66 failed, 5208 passed, 281 skipped, 29 deselected, 201
errors`.  This observed sequence establishes that the repaired portable audit
was not the failing gate, while the literal fixture root is the repeated
source-owned cause.  It does not close hosted CI: the post-repair
`make ci-portable` hosted gate remains required.

`make ci-portable` already creates a private `0700`
`/tmp/trading-agent-ci-portable.XXXXXXXXXX` directory, verifies its owner and
mode, installs an exit cleanup trap, and invokes `ci-portable-private` with
`TMPDIR`, `TEMP`, and `TMP` set to that directory.  Python's `tempfile`
default selection uses that process environment.  The failures occur only
because the affected calls override the default with `dir=...`.

The repair is test-only.  No production source, runtime authority, policy,
artifact, manifest, live gate, workflow, or Make target needs to change.

## Invariant

This packet prohibits only a statically literal user-home path supplied as the
`dir` argument to a `tempfile` test-fixture constructor.  Removing such an
override lets the affected constructor use the process default.  Under
`make ci-portable`, Make supplies that default through its owner-verified,
`0700`, trap-cleaned `TMPDIR`, `TEMP`, and `TMP` environment.  Existing
fixture cleanup and any explicit leaf `chmod(0o700)` remain in place.

This packet does **not** govern deliberate non-home overrides such as the 61
existing `dir="/tmp"` calls in 42 tracked Python test modules, and does not
claim every disposable fixture descends from Make's root.  An explicit `dir=`
always takes precedence over the environment and remains outside this focused
hosted failure repair.

No test may weaken an asserted runtime identity merely because that identity
contains an absolute path.  A path supplied to `tempfile` as fixture storage
is distinct from a literal asserted as a sealed or historical runtime/policy
identity.

## Exhaustive classification

The following are all tracked Python test calls at this head that pass the
literal `/home/thenam176/.cache` to `tempfile.mkdtemp` or
`tempfile.TemporaryDirectory`.  They are disposable fixture setup and are the
complete implementation file set.  For each listed call, remove only the
`dir="/home/thenam176/.cache"` keyword; preserve its prefix, context manager
or `try/finally`, cleanup, permissions, test body, and test marker.

| Path | Current lines | Calls | Classification |
| --- | --- | ---: | --- |
| `legacy/research-backend/tests/test_phase4_research_only.py` | 1207 | 1 | `secure_tmp_path` fixture |
| `tests/control_api/test_status_repositories.py` | 71 | 1 | function-local database fixture |
| `tests/foundation/test_nautilus_runtime_closure.py` | 216-220 | 1 | closure-input fixture |
| `tests/jobs/test_child_environment.py` | 29 | 1 | `tmp_path` fixture |
| `tests/jobs/test_command_registry.py` | 46 | 1 | `tmp_path` fixture |
| `tests/jobs/test_engine_artifacts.py` | 27 | 1 | `tmp_path` fixture |
| `tests/jobs/test_nautilus_closure.py` | 88, 1275-1278, 1318, 1350 | 4 | closure fixtures/cases |
| `tests/jobs/test_safety_state.py` | 27 | 1 | `tmp_path` fixture |
| `tests/jobs/test_safety_state_exporter.py` | 29 | 1 | `tmp_path` fixture |
| `tests/jobs/test_semantic_manifest_builder.py` | 27 | 1 | `secure_tmp_path` fixture |
| `tests/jobs/test_worker_safety.py` | 25 | 1 | `tmp_path` fixture |
| `tests/runtime_release/test_backend_release_smoke.py` | 16 | 1 | `linux_tmp_path` fixture |
| `tests/runtime_release/test_config.py` | 16 | 1 | `tmp_path` fixture |
| `tests/runtime_release/test_provision_script.py` | 713-715, 742-744, 775-777, 807, 836, 884, 914, 934, 959, 978 | 10 | isolated fakeroot/provisioning fixtures |
| `tests/runtime_release/test_provisioning_generators.py` | 34 | 1 | `linux_tmp_path` fixture |
| `tests/runtime_release/test_standalone_verifier.py` | 140-142 | 1 | `native_tmp_path` fixture |

The remaining literal cache-root occurrences are intentionally outside this
fixture repair and must not be edited:

- `tests/foundation/test_d0_closure.py:35` asserts a historical closure-matrix
  command, not fixture creation.
- `tests/foundation/test_nautilus_native_entry_guard.py:23,26`,
  `tests/foundation/test_nautilus_runtime_closure.py:37,40,440,443,554,557`,
  and `tests/foundation/test_nautilus_sealed_uv_exec.py:25,28,1347` assert
  checked policy/receipt identity.  They do not provide a `tempfile` root.
- All other `/home/thenam176/...` test literals are policy, runtime, legacy
  rejection, or historical-evidence assertions.  In particular, the data,
  systemd, safety, and release path assertions are not a portable fixture
  creation path and are forbidden from this repair.

## TDD sequence

1. **RED — source-level guard.** In
   `tests/consolidation/test_absolute_source_paths.py`, add a focused AST scan
   over tracked root and legacy Python test files.  It must report
   `path:line` for every statically literal user-home directory supplied to
   `tempfile.mkdtemp` or `tempfile.TemporaryDirectory`, and fail when that
   report is non-empty.  The visitor must:

   - resolve `import tempfile as alias`, `from tempfile import mkdtemp`, and
     `from tempfile import TemporaryDirectory`, including aliases of the
     direct imports;
   - recognize both a selected `tempfile` module attribute and a selected
     direct-import name as one of the two constructors;
   - inspect a named `dir=`, the third positional argument (index two) for
     either constructor, and a statically knowable
     `**{"dir": value}` dictionary expansion;
   - resolve `import pathlib`, `import pathlib as alias`,
     `from pathlib import Path`, and `from pathlib import Path as alias` when
     recognizing a `Path` constructor;
   - recognize a literal string beginning `/home/` and a `Path("/home/...")`
     expression in each resolved binding form (`pathlib.Path(...)`,
     `alias.Path(...)`, direct `Path(...)`, and a direct-import alias) as a
     literal user-home directory; and
   - inspect only those constructor directory arguments, not unrelated
     policy/runtime/historical string assertions.

   Focused AST-snippet unit cases will prove rejection of direct and
   module-aliased `tempfile`, direct-import-aliased constructor,
   third-positional, static-`**kwargs`, direct `Path`, qualified
   `pathlib.Path`, module-aliased `pathlib` (`pl.Path`), and direct-import
   alias (`P(...)`) forms.  They will also prove that `/tmp`, dynamically
   computed directory expressions, and a policy literal that is not a
   selected constructor directory argument are not selected.  No global
   monkeypatch is involved.
   On the current head the production scan fails with exactly the 28 calls in
   the table.
2. **RED — Make propagation proof.** In
   `tests/foundation/test_d0_closure.py`, add the portable analogue of the
   existing private-CI assertion.  It must require the `ci-portable` recipe to
   create `trading-agent-ci-portable`, `chmod 0700`, assert
   `uid:700`, install the cleanup trap, and invoke `ci-portable-private` with
   all three of `TMPDIR`, `TEMP`, and `TMP` set to `ci_tmpdir`.  This proves
   the portable Make invocation supplies its default temporary root to child
   commands; it deliberately makes no claim about calls with an explicit
   `dir=` override.
3. **GREEN.** Remove only the 28 literal `dir=` overrides listed above.  The
   constructors then use inherited `tempfile` selection; cleanup and modes
   remain as they were.
4. Run the two new tests plus the touched-module tests.  The scanner passing
   proves no selected `tempfile` fixture constructor has a literal user-home
   root; the Make assertion proves `ci-portable` supplies the owned default
   temporary root.  Neither assertion claims to govern explicit non-home
   overrides.

The first regression is intentionally source-oriented rather than a global
`tempfile` monkeypatch.  It catches every committed literal bypass regardless
of which fixture fails first on a hosted runner, while preserving ordinary
runtime identity assertions and avoiding process-wide test behavior changes.

## Alternatives rejected

1. **Create `/home/thenam176/.cache` in CI.** Rejected: this encodes a
   developer home into hosted execution, evades rather than fixes portable
   source, and bypasses the existing owned-root lifecycle.
2. **Change the workflow or Make target to fabricate a compatibility
   directory.** Rejected: `ci-portable` already exports the correct private
   root.  Adding another root expands CI scope and leaves direct test runs
   non-portable.
3. **Global `tempfile` monkeypatch or test configuration override.** Rejected:
   it obscures which tests violate the invariant, risks unrelated fixture
   behavior, and is expressly unnecessary.
4. **Change runtime/policy literals to dynamic home paths.** Rejected: those
   assertions encode intentional safety identity or evidence and are not the
   failure mechanism.
5. **Skip, xfail, or relax the affected tests.** Rejected: it suppresses the
   hosted gate instead of making the fixtures portable.

## Scope and validation

Allowed implementation changes are the 16 test modules in the table plus:

- `tests/consolidation/test_absolute_source_paths.py` for the exhaustive
  user-home fixture-root scanner; and
- `tests/foundation/test_d0_closure.py` for portable Make-root propagation.

No files under `apps/`, `packages/`, `services/`, `scripts/`, `ops/`,
`engines/`, `.github/`, manifests, authority documents, lockfiles, or the
Makefile are in scope.  Do not invoke external engine builds, runtime tests,
services, databases, credentials, brokers, or live systems.

Focused validation after implementation:

```bash
UV_OFFLINE=1 uv run pytest -q tests/consolidation/test_absolute_source_paths.py
UV_OFFLINE=1 uv run pytest -q tests/foundation/test_d0_closure.py
UV_OFFLINE=1 uv run pytest -q \
  tests/control_api/test_status_repositories.py \
  tests/jobs/test_child_environment.py tests/jobs/test_command_registry.py \
  tests/jobs/test_engine_artifacts.py tests/jobs/test_nautilus_closure.py \
  tests/jobs/test_safety_state.py tests/jobs/test_safety_state_exporter.py \
  tests/jobs/test_semantic_manifest_builder.py tests/jobs/test_worker_safety.py \
  tests/runtime_release/test_backend_release_smoke.py \
  tests/runtime_release/test_config.py tests/runtime_release/test_provision_script.py \
  tests/runtime_release/test_provisioning_generators.py \
  tests/runtime_release/test_standalone_verifier.py
(cd legacy/research-backend && UV_OFFLINE=1 uv run --frozen --extra test pytest -q \
  tests/test_phase4_research_only.py)
git diff --check
```

Do not run the Bubblewrap/Nautilus closure cases or external engine builds as
part of this repair.  Broader hosted closure remains `make ci-portable` after
the normal independent implementation/review loop; it must retain every
non-runtime gate and may not be weakened.

## Non-runtime declaration

This design authorizes no production mutation.  It changes neither runtime
paths nor their asserted policy identities, does not access private or live
systems, and retains
`LIVE_EXECUTION_ENABLED=false`, `LIVE_TRADING_APPROVED=false`, and
`LIVE_TRADING_ENABLED=false`.
