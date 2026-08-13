# T-G03D hosted workflow and disclosure report

## Scope delivered

- Retained the Foundation workflow's existing checkout depth, frozen root and
  dashboard installs, false live flags, canonical `make ci-portable` command,
  artifact name/path, always-upload condition, and 14-day retention.
- Changed only the hosted `ci-portable` root-test route.  Its private
  `RUNNER_TEMP` 0700 wrapper and `prepare-root-test-install` remain intact.
  `ci-portable-private` now invokes
  `test-all-portable-topology-private` and
  `check-test-governance-topology`, while retaining critical coverage,
  dashboard build, Python-source audit, dependency audit, and all existing
  non-root gates.
- Added `check-test-governance-topology`.  It receives the current
  `GITHUB_RUN_ID` and checked-out head, the locked evidence root, and the
  tracked T-G03A inventory; there is no fallback run ID or head.
- Added topology-aware governance.  It revalidates the sealed reservation,
  tracked/installed inventory byte binding, canonical receipt hashes and
  current Foundation bindings.  A `PASS` receipt requires one exact, passing,
  no-clobber root governance record; a valid deferred native/external receipt
  requires no root governance record.  It then retains legacy and dashboard
  governance runs.  The aggregate disclosure is included in the durable
  `test-governance-topology/test-governance.json` artifact report.
- Strict `make ci`, `make audit`, `make audit-release`, and generic
  `check-test-skips` were not changed.

## TDD evidence

1. Added a routing contract test first.  It failed because
   `ci-portable-private` still selected
   `test-all-portable-private` and generic `check-test-skips`.  The minimal
   Make routing change made it pass.
2. Added receipt-to-root-governance disclosure tests first.  They initially
   failed because `audit_topology_root_records` did not exist.  The new audit
   then made the valid portable-PASS/native-DEFERRED/external-DEFERRED fixture
   pass and rejects an extra/unbound root node.
3. Added the source-level transitive routing test first.  It failed because
   `run_topology_suites` did not exist.  The implementation now audits sealed
   root lane records and invokes only retained legacy/dashboard component
   governance; it does not call the generic root `run_suites()` path.  The
   exact lane runner is also checked to expand only inventory-derived nodes,
   not a broad `tests` argument.

## Fresh validation

- `TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest -q
  tests/governance/test_t_g03d_hosted_disclosure.py
  tests/consolidation/test_repository_shape.py
  tests/foundation/test_d0_closure.py` — `27 passed`.
- `GITHUB_RUN_ID=31641536482 TEST_EVIDENCE_DIR=/tmp/t-g03d-portable.JUp0izXA4A
  make test-portable-source` — passed.  The sealed evidence contains the
  reservation, verified inventory copy, three exact portable governance
  records, and their three receipts; exact lane counts were 2, 27, and 3.
- `GITHUB_RUN_ID=31641536482 RUNNER_TEMP=/tmp make -n ci-portable` — the
  source-required route contains the topology aggregate and topology audit;
  it contains no generic root `pytest ... tests`,
  `test-portable-embedded-proof`, `test-all-portable-private`, or
  `check-test-skips` route.
- `make audit` — PASS (expected dirty scoped checkout).
- `make check-secrets` — PASS.
- `git diff --check` — PASS.

## Known limitations

- `make check-contracts` could not run because this isolated checkout lacks
  `apps/dashboard/node_modules/.bin/openapi-typescript`; no dependencies were
  installed.
- A broader governance test invocation on this WSL/DrvFS checkout has existing
  private-directory mode failures unless `TMPDIR`, `TMP`, and `TEMP` are set
  to `/tmp`; with that override, one existing critical-coverage test still
  expects an unsafe intermediate directory to retain mode `0777` after a
  fail-closed check, but the current implementation tightens it.  This packet
  does not alter that unrelated behavior.
- No engine build, authority acquisition, external corpus/UV action, service
  mutation, migration, live action, dashboard/generated output, or dependency
  change was performed.

## Round 1/5: dynamic portable-root remainder amendment

The accepted amendment closes the ordinary-root coverage gap without restoring
a generic root execution:

- `ci-portable-topology` now creates a new private Package6 custody build,
  rejects anything other than one regular extension, derives its digest, and
  exports the extension identity to the topology path.  It does not use a
  pre-existing extension.
- `test-portable-root-remainder` is invoked before every inventory lane.  The
  topology tool is the only code that uses bare `tests`, and only for
  `--collect-only --portable-embedded-proof -m "not runtime_postgres and not
  host_coupled" -p scripts.test_governance_pytest`.
- The collector seals a no-clobber collection report, sorted candidate file,
  and canonical baseline.  The baseline binds the Foundation run/head, locked
  inventory hash, portable collector policy, custody-extension digest, list
  digest, collection-report digest, and self-hash.
- The generated remainder is the exact baseline-minus-62-inventory set and is
  also sealed as a canonical JSON record and node file.  Its executor accepts
  only that reopened/generated list and requires every selected result to be
  an exact passing governance observation.  An empty remainder still emits a
  sealed empty governance report.
- Topology governance now preserves collection-time marker deselections for
  the existing allowlist policy, while its closed root accounting requires the
  baseline to equal the disjoint remainder execution plus every lane `PASS`
  execution or valid native/external `DEFERRED` receipt expectation.

### Round-1 TDD and validation

1. A dynamic-new-root-node baseline/remainder test was added first and failed
   because the collector/remainder interfaces did not exist.  It passes with a
   synthetic ordinary root node and proves the generated remainder is exactly
   the set difference.
2. A generated-list executor test was added first and failed because no
   remainder executor existed.  It now proves the runner receives only the
   reopened generated node ID, not `tests` or a directory selector.
3. A hostile duplicate-inventory-in-remainder test proves a closed-union
   mismatch fails before it can contribute a green topology aggregate.
4. `TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest -q` over the focused
   T-G03D/topology/repository-shape/D0 tests passed: `57 passed`.
5. The broader governed focused set (excluding one documented pre-existing
   critical-coverage intermediate-mode expectation) passed: `119 passed, 1
   deselected`.
6. A real collection-only baseline was run with a fresh temporary native
   custody build and supplied GitHub context.  It collected `5832` portable
   candidates with `29` preserved marker deselections.  The sealed dynamic
   remainder contained `5770` node IDs.  Full remainder execution was not run:
   it would execute the complete root suite and is outside the requested
   source-contract validation on this host.
7. `make test-portable-source` still passed with supplied nonzero GitHub run
   context (all exact 32 portable source nodes); `make audit`,
   `make check-secrets`, and `git diff --check` passed.
