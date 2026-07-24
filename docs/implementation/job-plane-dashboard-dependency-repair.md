# Job Plane Dashboard Dependency Repair

**Evidence date:** 2026-07-16
**Status:** `TASK 2 GREEN`

## Reviewed source evidence

Task 1 classified this document as deferred deterministic Task 2 evidence in
`docs/implementation/job-plane-v4-transfer-manifest.csv`. Before the evidence
was used, the source artifact in the dirty recovery candidate was checked
against the manifest:

```text
expected sha256: c39582ffea32af9d8cf22aa2e9cfd020104062f3bbf85e73522c2e963d18ea31
observed sha256: c39582ffea32af9d8cf22aa2e9cfd020104062f3bbf85e73522c2e963d18ea31
```

The reviewed evidence established that three root Job Plane contract tests use
`@redocly/ajv/dist/2020` to validate Python-generated schemas, that the package
was missing from the canonical dashboard manifest, and that the approved exact
repair was the dev dependency `@redocly/ajv@8.11.2`. Its focused RED was three
module-resolution failures; its focused GREEN was three passing AJV contract
tests. The source artifact was read only and was not treated as runtime
authority.

## Canonical generator toolchain

The complete in-repository generator requires these exact direct dev
dependencies:

- `@redocly/ajv@8.11.2` for cross-language JSON Schema checks;
- `openapi-typescript@7.13.0` for static dashboard API types;
- `openapi-zod-client@1.18.3` for runtime Zod schemas.

No production dependency is added. The dashboard owns the manifest, lockfile,
and `node_modules` installation used by `scripts/generate_contracts.py`.

## NPM resolution audit

The baseline lock contained 496 package paths. A normal npm install resolved
the three exact tools but changed three pre-existing versions:

| Package path | Baseline | Normal install |
| --- | ---: | ---: |
| `node_modules/hasown` | `2.0.3` | `2.0.4` |
| `node_modules/js-yaml` | `4.1.1` | `4.2.0` |
| `node_modules/zod` | `4.4.3` | `3.25.76` |

That result was rejected. A disposable manifest-only resolution lab then
tested npm strategies before retrying the integration worktree. The smallest
successful full-install strategy was one additional flag:

```text
npm install --save-dev --save-exact --ignore-scripts \
  --install-strategy=nested \
  @redocly/ajv@8.11.2 \
  openapi-typescript@7.13.0 \
  openapi-zod-client@1.18.3
```

The full disposable install and the independent integration install produced
the same npm-generated manifest and lock hashes:

```text
package.json       a957fa12a39d17cffd166a04be3a0f353b1ce8808a40dffcbe917864ded59af5
package-lock.json  6a97809f5bc3f987bbeb53a0618a45e493572872cb40b5f05071a3feb0c403bf
```

Both audits proved:

- all 496 pre-existing package paths remained present at exactly their prior
  versions;
- no pre-existing direct dependency changed;
- the only new direct entries were the three approved exact dev dependencies;
- 72 new lock entries were confined to the approved dependency subtrees:
  4 under `@redocly/ajv`, 19 under `openapi-typescript`, and 49 under
  `openapi-zod-client`;
- no unrelated package entry was added or removed.

A plain `npm ci --ignore-scripts` reproduced the disposable manifest and lock
hashes unchanged and left both generator executables available from
`apps/dashboard/node_modules/.bin`.

## Verification

The contract generator now defaults to `apps/dashboard`, ignores an ambient
`CONTRACT_TOOL_ROOT`, and retains explicit `--tool-root` only as a diagnostic
override. The test sets the ambient variable to a nonexistent external path.

```text
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/control_api/test_generation.py

2 passed in 9.44s
```

The required clean install reproduced the audited hashes and installed 505
packages:

```text
cd apps/dashboard && npm ci --ignore-scripts && cd ../..
# exit 0; package.json and package-lock.json hashes unchanged
```

The complete Task 2 gates passed:

```text
env -u CONTRACT_TOOL_ROOT -u DASHBOARD_ROOT make check-contracts
# exit 0

PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/control_api/test_generation.py tests/jobs/test_contracts.py
# 74 passed in 8.58s

git diff --check
# exit 0, no output
```

An additional check set `CONTRACT_TOOL_ROOT` to a nonexistent child of
`/home/thenam176/projects/trading-dashboard`; `make check-contracts` still
passed. An explicit `--tool-root apps/dashboard` diagnostic check also passed.
The script, Makefile, and contract tests contain no default sibling-dashboard
reference.

Dashboard component verification also passed:

```text
npm test
# 140 tests passed; dashboard security integration: PASS

./node_modules/.bin/tsc --noEmit
# exit 0

npm run lint
# exit 0

npm run build
# exit 0; Next.js 16.2.6 production build completed
```

Npm continued to report four known advisories (one low and three moderate).
Contract generation emits the existing `createTypeAliasDeclaration`
deprecation warning from the approved generator stack, and dashboard Node
tests emit existing module-type warnings. No audit fix, dependency upgrade,
warning-suppression flag, dashboard deployment, or runtime operation was
performed.
