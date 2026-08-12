# T-G01: portable hosted-CI authority design

**Status:** design only.  This document freezes the T-G02 implementation
decision; it does not authorize a workflow run, runtime action, or authority
metadata change.

## Source-map receipt and current ownership

| Surface | Current owner and public interface | Observed responsibility |
| --- | --- | --- |
| `.github/workflows/foundation.yml` | `Foundation` job, `Run canonical local and CI gate` step | A GitHub-hosted Ubuntu runner installs the pinned root and dashboard dependencies, sets both live approvals to `false`, then runs `make ci`. |
| `Makefile` | public targets `audit`, `audit-release`, `test-all-private`, `ci`, and `ci-private` | `ci` establishes a private `/tmp/trading-agent-ci.*` root and calls `ci-private`; `ci-private` reinstalls the root package then calls `test-all-private` plus governance, coverage, dashboard-build, Python-source, and dependency gates.  `test-all-private` begins with strict `audit`. |
| `scripts/audit_canonical_repo.py` | `audit(root_path, release, portable_requested=False)`, CLI `--portable` | Owns the authority-mode decision.  It rejects `--release --portable` as `E_ARGUMENT`; `_audit_authority` accepts portable mode only when *all* declared external repositories are absent. |
| `packages/consolidation/authority.py` | `parse_source_authority`, `load_source_authority`, `SourceAuthority`, `AuthorityError` | `parse_source_authority` validates the immutable schema and declared component identity without external Git.  `load_source_authority` additionally resolves the exact external Git commit/tree and is therefore strict-mode authority. |
| `scripts/verify_component_snapshot.py` | `verify_snapshot`, `verify_embedded_snapshot` | Strict verification resolves authority source objects.  Portable verification validates the embedded manifest against parsed authority plus the canonical repository's introduction and current revisions, without resolving an external authority checkout. |
| `ops/consolidation/source-authority.json` and the two `*-source-manifest.json` files | tracked immutable evidence | These are the authority declaration and imported component evidence.  The audit's `_immutable_evidence` still requires their introduction blob, `HEAD` blob, and worktree blob to match. |
| `tests/consolidation/test_audit_canonical_repo.py` | portable authority tests named below | Already owns the fail-closed authority and embedded-evidence matrix. |
| `tests/consolidation/test_repository_shape.py` and `tests/foundation/test_d0_closure.py` | Make/workflow topology tests | Own the textual contract for canonical targets and the Foundation workflow command. |

### Current call graph

```text
Foundation workflow
  -> make ci
     -> private TMPDIR wrapper
        -> make ci-private
           -> make prepare-root-test-install
           -> make test-all-private
              -> make audit
                 -> audit(..., portable_requested=False)
                    -> _audit_authority(...)
                       -> load_source_authority(...)
                          -> external authority Git commit/tree resolution
              -> check-d0-closure, check-contracts, check-secrets,
                 test, test-backend, test-dashboard, typecheck-dashboard, lint-dashboard
           -> check-test-skips, check-critical-coverage, build-dashboard,
              audit-python-source, audit-dependencies
```

This is a source-plan adaptation: portable parsing and embedded verification
already exist at `19627785c140c502260f864e462fed9b9925436e`; T-G02 must not
rewrite their semantics.  The defect is only that the hosted workflow enters
the strict target, where its authority checkouts are unavailable.

## Single invariant

**Authority mode is selected solely by an explicit public Make target: local
and release callers remain strict, while the Foundation workflow selects the
portable target; portable mode is valid only when every declared external
authority repository is absent and must retain every non-runtime CI gate.**

There is no `CI` environment check, runner-path heuristic, Make variable
default, fallback from strict to portable, or release exception.  The existing
auditor remains the sole mode enforcer.

## Strict versus portable contract

| Situation | Strict owner / command | Portable owner / command | Required result |
| --- | --- | --- | --- |
| All three external authority repositories are available | `make audit`, `make ci`, or `make audit-release` -> `load_source_authority` / `verify_snapshot` | `make audit-portable` -> existing `--portable` audit | Strict passes when all other checks pass; portable fails `E_AUTHORITY`. |
| All three repositories are absent (hosted checkout) | strict `audit` | `audit-portable` -> `parse_source_authority` / `verify_embedded_snapshot` | Strict fails `E_AUTHORITY`; portable passes only if all embedded evidence and non-runtime gates pass. |
| Exactly one or two repositories are available | strict or portable audit | same `_audit_authority` availability check | Both fail `E_AUTHORITY`; partial availability is never a third mode. |
| Portable requested with release | `audit-release` remains strict | audit CLI sees `--release --portable` | Fail `E_ARGUMENT`; no portable release target exists. |
| Component bytes, manifest identity/aggregate, or immutable evidence changes | `verify_snapshot` / `_immutable_evidence` | `verify_embedded_snapshot` / `_immutable_evidence` | Fail closed: `E_TAMPER`, `E_MANIFEST`, or `E_AUTHORITY` as currently classified. |
| Full CI gate set | `ci-private` | new `ci-portable-private` | Identical gates except `audit` is replaced one-for-one by `audit-portable`; no skip, xfail, dependency, coverage, security, build, or runtime-target change. |

### Error taxonomy retained by T-G02

| Code / failure | Source owner | T-G02 handling |
| --- | --- | --- |
| `E_AUTHORITY` | `_audit_authority`, `parse_source_authority`/`load_source_authority`, and embedded identity checks | Preserve it for strict-with-none, portable-with-all, any partial availability, malformed authority, and coordinated authority/manifest tamper.  Do not translate it to a pass. |
| `E_ARGUMENT` | `audit()` | Preserve it for `--release --portable`; do not create `audit-portable-release` or a Make escape hatch. |
| `E_MANIFEST`, `E_TAMPER` | snapshot loaders/verifiers and immutable evidence | Preserve exact fail-closed behavior for malformed/changed manifests and component bytes. |
| `E_ROOT`, `E_DIRTY`, `E_FORBIDDEN`, `E_REQUIRED`, `E_SOURCE_PATH`, `E_NESTED_GIT`, `E_TRACKED_LINK`, `E_GIT_OBJECT` | existing canonical audit | Not mode-specific and not changed.  Release continues to use strict `audit-release` and its dirty-tree protection. |
| non-zero Make exit | Make dependency graph | Propagate the failing subtarget unchanged under `set -eu`; no `|| true`, conditional skip, or catch-and-continue recipe. |

## Alternatives considered

### Accepted: explicit parallel portable target chain

Add explicit `audit-portable`, `test-all-portable-private`,
`ci-portable-private`, and `ci-portable` targets.  The new private CI target
is a verbatim gate sibling of `ci-private`, differing only through
`test-all-portable-private`, whose prerequisite list replaces only `audit`
with `audit-portable`.  Change the workflow's one command to `make
ci-portable`.

This is accepted because `scripts/audit_canonical_repo.py` already gives an
explicit `--portable` public CLI and fail-closes all three authority states.
It keeps `make ci`, `make audit`, and `make audit-release` byte-for-byte
semantic strict entry points, while a reviewed workflow diff visibly selects
portable mode.

### Rejected: auto-select portable mode from `CI`, a GitHub variable, or absent paths

This would make the same public `make ci` command change authority semantics
based on ambient environment or filesystem state.  It violates the invariant,
the packet's prohibition on silent CI environment auto-detection, and the
source-owned distinction in `_audit_authority` between no authority, partial
authority, and complete authority.

### Rejected: relax portable audit to permit complete external authority

The existing `test_portable_flag_rejects_fully_available_authorities` and
`_audit_authority` deliberately make that `E_AUTHORITY`.  Permitting it would
create an ambiguous local mode and weaken the current strict authority proof.
T-G02 must reuse, not alter, `--portable`.

## Frozen T-G02 implementation specification

### Exact tracked files and public names

1. **`Makefile`**

   Add `audit-portable`, `test-all-portable-private`, `ci-portable`, and
   `ci-portable-private` to `.PHONY`.

   - `audit-portable` must be exactly the existing `audit` command with one
     appended argument:
     `uv run python scripts/audit_canonical_repo.py --root "$(CURDIR)" --portable`.
   - `test-all-portable-private` must have the exact existing
     `test-all-private` prerequisite set with `audit` replaced by
     `audit-portable`: `audit-portable check-d0-closure check-contracts
     check-secrets test test-backend test-dashboard typecheck-dashboard
     lint-dashboard`.  It must not include `build-dashboard`.
   - `ci-portable` must copy the security shape of `ci`: `set -eu`, a new
     `mktemp -d /tmp/trading-agent-ci-portable.XXXXXXXXXX` directory, mode
     `0700`, owner/mode assertion, the same `find -P ... -xdev` cleanup before
     `rm -rf`, an EXIT trap, and `TMPDIR`, `TEMP`, and `TMP` set only for
     `$(MAKE) ci-portable-private`.
   - `ci-portable-private` must call `$(MAKE) prepare-root-test-install` once,
     then call exactly `$(MAKE) test-all-portable-private check-test-skips
     check-critical-coverage build-dashboard audit-python-source
     audit-dependencies`.  It takes no prerequisites and does not invoke
     `ci`, `ci-private`, `audit`, or `audit-release`.

   The focused test must parse the `ci-portable` recipe's `$(MAKE)` target
   tokens (not use substring matching): its complete target-token list must
   be exactly `("ci-portable-private",)`.  This explicitly rejects the exact
   target tokens `ci` and `ci-private`, while allowing the required
   `ci-portable-private` token.

   Do not change existing `audit`, `audit-release`, `test-all-private`,
   `test-all`, `ci`, or `ci-private` recipes.

2. **`.github/workflows/foundation.yml`**

   Change only the current `Run canonical local and CI gate` command from
   `make ci` to `make ci-portable`.  Retain the existing checkout, uv/Python,
   Node, dependency installation, live=false environment, timeout,
   permissions, concurrency, and test-governance artifact upload.  No new
   environment variable or conditional is permitted.

3. **`tests/consolidation/test_repository_shape.py`**

   Extend `MAKE_TARGETS` with the four portable names.  Add one focused test
   named `test_portable_ci_targets_are_explicit_and_retain_all_non_runtime_gates`.
   It must parse the Makefile as this file already does and assert all four
   targets are in `.PHONY`; assert the exact portable test prerequisite set;
   assert both portable CI targets have no Make prerequisites; assert the
   portable private recipe performs one root reinstall and contains all six
   post-suite gates; and use exact target-token matching for the portable
   wrapper as specified above.  The test must parse scalar workflow `run:`
   values into `workflow_run_values`, derive
   `make_run_values = [value for value in workflow_run_values if value.startswith("make ")]`,
   and assert `make_run_values == ["make ci-portable"]`.  It must also assert
   `"make ci" not in make_run_values`; it must **not** assert that the
   substring `"run: make ci"` is absent, because the valid
   `run: make ci-portable` line contains that substring.

   Retain the existing test name
   `test_foundation_workflow_delegates_to_the_canonical_local_ci_gate` and
   update it to the same exact workflow-run-value contract: the Foundation
   workflow has one Make run value, exactly `make ci-portable`, and zero exact
   `make ci` values.  Retain its assertions that the workflow does not spell
   out individual test, build, Bandit, npm-audit, or pip-audit commands.

4. **`tests/foundation/test_d0_closure.py`**

   Update only the workflow-string expectations in
   `test_property_contract_and_closure_gates_are_collected_by_ci` from
   `run: make ci` to `run: make ci-portable`.  Keep its strict-local Makefile
   assertions for `ci`, `ci-private`, and `test-all-private`; add exact
   portable assertions only if necessary to prevent a duplicate/incomplete
   recipe.  Do not alter the D0 matrix or its historical final proof commands.

5. **`tests/consolidation/test_audit_canonical_repo.py`**

   Add exactly one dedicated test named
   `test_portable_audit_rejects_partial_external_authority_availability`.
   It must create the existing `_valid_root(tmp_path)` fixture, call
   `_remove_authority_repositories(repository, keep="core")` so exactly the
   declared `core` authority repository remains, invoke
   `_run(repository, "--portable")`, and assert a non-zero exit with stderr
   exactly `E_AUTHORITY`.  Do not modify the existing strict-only
   `test_audit_rejects_partial_external_authority_availability`; the two tests
   deliberately prove both requested modes reject a partial authority set.

6. **No changes** to `scripts/audit_canonical_repo.py`,
   `packages/consolidation/authority.py`,
   `scripts/verify_component_snapshot.py`,
   `ops/consolidation/source-authority.json`, either source-manifest JSON,
   lockfiles, generated contracts, trading/runtime/recovery sources, or live
   flags.

### TDD and verification order for T-G02

1. Run its prescribed baseline: `uv run pytest -q tests/consolidation`,
   `make check-contracts`, and `make check-secrets`.
2. Add the exact-token repository-shape test as the **sole valid RED** and
   run it in isolation.  It must fail because portable Make targets/workflow
   routing do not yet exist, not because a fixture or dependency is missing.
3. Add the named portable-partial-authority test as a **baseline PASS
   regression**, not a RED test.  Run that test in isolation immediately; it
   must pass because `_audit_authority` already fail-closes any partial
   availability before a portable success path is considered.  Do not change
   the auditor or manufacture a failure for this established behavior.
4. Implement the Makefile and workflow changes, then run the focused
   repository-shape test and the portable-partial regression together.
5. Update the two existing workflow-topology assertions, run their focused
   test files, then `uv run pytest -q tests/consolidation`.
6. Run `make check-contracts` and `make check-secrets`; no generator is run.
7. Run `make ci-portable` only on an authority-absent hosted-like checkout.
   On this design baseline all three declared authority paths exist, so that
   command is *required* to stop at `E_AUTHORITY`; treating it as local green
   would contradict `test_portable_flag_rejects_fully_available_authorities`.
   The all-absent PASS is already executable in the audit fixture test; the
   end-to-end hosted Foundation PASS is the independent T-G03 gate after an
   authorized push.

### Generated-artifact and runtime boundary

No public API, contract, schema, manifest, authority JSON, dependency lock,
or generated artifact changes.  `make ci-portable` retains the existing
non-runtime validation/build behavior, including its isolated temporary native
test build and dashboard build.  It does not start persistent services,
mutate PostgreSQL, call exchanges/brokers/accounts/orders, access a runtime
release, or change `LIVE_EXECUTION_ENABLED=false` or
`LIVE_TRADING_APPROVED=false`.

## Design self-review

- Ownership is unambiguous: audit mode belongs to the existing auditor;
  target selection belongs to Make; hosted selection belongs solely to the
  workflow.
- The design does not introduce a third authority state or an environmental
  fallback.
- All named errors retain their source owner and fail-closed behavior.
- The planned diff is two implementation files and three focused test files,
  with no dependency or generated artifacts; it remains within T-G02's
  four-hand-written-production-file ceiling.
- Hosted execution is deliberately deferred to T-G03; this design does not
  claim a hosted CI run.
