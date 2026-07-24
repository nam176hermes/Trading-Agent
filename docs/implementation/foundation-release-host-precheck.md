# Foundation Release Host Precheck

## Scope

Package 01 covers the root application release only. PostgreSQL, schedulers, services, deployment and live trading were not touched.

## Baseline

- Branch: `codex/canonical-monorepo`
- Package baseline: `30835ebec8e4b357f1d02cbb87a49a689e2de842`
- Canonical precheck: `make audit-release`
- Initial host command: `make test-runtime-release-host`
- Initial result: exit 2, one failure and one skip.

The initial failure was an offline UV resolution error. `anyio==4.14.1` was absent from the mutable operator cache. A later isolated reproduction also showed `fastapi==0.139.0` absent. This established that the existing release build depended on cache warmth rather than a complete external dependency closure.

## Safety boundary

The work stayed source-only and paper-only:

- No PostgreSQL access or migration.
- No exchange, broker, provider, account or order endpoint.
- No scheduler or persistent service start.
- No dependency or lockfile change.
- No live gate change.
- No wheel binary committed to Git.

## Caller and change surface

The reviewed public surface was `ReleasePolicy`, `build_release`, `phase4_app_release_policy` and `scripts/build_phase4_release.py`. Changes were limited to the Make target, runtime-release implementation, preparation script, tests and Package 01 evidence.

## Root cause

The release builder invoked `uv sync --offline` against the operator UV cache. The lockfile was valid, but the cache was incomplete and mutable. The fix uses a sealed, lock-addressed wheelhouse and installs the exported production closure with `uv pip sync --offline --no-index --find-links`.

## Evidence

- Original RED log: `/tmp/p01-host-release-red-successor.log`
- Final pre-document host proof log: `/tmp/p01-host-release-no-source-checkout.log`
- Builder: `packages/runtime_release/manifest.py:536`
- Wheelhouse preparation: `scripts/prepare_runtime_release_wheelhouse.py:78`
