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
