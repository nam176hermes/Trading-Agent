# Package 1 - P0 Host Release Proof

## Goal

Close the failing host-coupled release gate by producing an offline, copied, symlink-free and runnable release artifact on the actual host.

The current failure is:

```text
uv_exit=1
Failed to download anyio==4.14.1
Network connectivity is disabled
requested data wasn't found in the cache
```

This package must determine whether the problem is only incomplete offline dependency preparation or an actual release-builder defect.

## Why this goes first

Until the release can be built from locked inputs without network access, the project cannot claim immutable or hermetic runtime readiness. Runtime rollout must not depend on a mutable source tree, a warm developer cache, or an unpinned internet download.

## In scope

- Inspect `tests/runtime_release/test_build.py`.
- Inspect lockfiles and release-builder implementation.
- Build a complete offline dependency closure.
- Seed a controlled UV cache or create an explicit wheelhouse.
- Prove no symlinks, editable installs, source-tree imports or network access.
- Prove the built release is runnable.
- Add deterministic manifest and cache/wheel inventory evidence.
- Run the canonical host release test.

## Out of scope

- PostgreSQL runtime parity.
- Job API/worker/scheduler rollout.
- Public dashboard cutover.
- Live trading.
- Strategy/model changes.
- Unrelated dependency upgrades.

## Required pre-check

1. Read `AGENTS.md`.
2. Confirm repository commit and clean/isolated worktree.
3. Run:

```bash
make audit-release
```

4. Capture current failure from:

```bash
make test-runtime-release-host
```

5. Confirm failure is still the missing offline artifact and not a different regression.

## Workstream A - Dependency closure inventory

Create a deterministic inventory from the canonical lockfile:

```text
package
version
artifact filename
artifact type
sha256
platform tag
python tag
source URL/reference
license
```

Requirements:

- Include `anyio==4.14.1`.
- Do not depend on unspecified “latest” packages.
- Do not silently use packages already installed globally.
- Do not rely on the operator’s mutable UV cache as authority.
- Record native ABI/system-library requirements.

## Workstream B - Controlled offline source

Choose one approved approach:

### Option 1: Managed UV cache seed

- Populate cache from the lockfile while network is explicitly allowed in a controlled preparation step.
- Export a deterministic cache inventory.
- Run release test with network disabled.
- Prove a missing artifact produces a clear fail-closed result.

### Option 2: Local wheelhouse

Preferred if it provides clearer reproducibility:

```text
<external-controlled-artifact-root>/wheelhouse/
docs/implementation/wheelhouse-manifest.json
```

Wheel binaries and mutable UV cache content must remain outside Git. Commit only the deterministic manifest, hashes, provenance and verification logic.

The build must use:

```text
--no-index
--find-links <wheelhouse>
```

or an equivalent offline-only mechanism.

## Workstream B2 - Change and dependency gate

Before changing release tooling or dependency manifests:

- identify exact callers and tests for every public release symbol;
- record allowed create/modify paths;
- request explicit approval before changing `pyproject.toml`, lockfiles or dependencies;
- do not upgrade unrelated packages;
- do not add wheel binaries, UV cache content or build output to Git.

## Workstream C - Release isolation proof

The release artifact must prove:

- no `.git`;
- no symlink;
- no editable install;
- no path to the source worktree in `sys.path`;
- no UV cache path required at runtime;
- no credentials, `.env`, database, logs, reports or runtime artifacts;
- fixed interpreter and command paths;
- package imports succeed;
- application starts in a harmless read-only smoke mode.

## Workstream D - Reproducibility and tamper checks

Build twice from the same clean commit.

Compare:

```text
source commit
lockfile hash
dependency inventory hash
file manifest
logical release digest
interpreter version
```

Tamper tests:

- modify one copied source file → verification fails;
- add a symlink → verification fails;
- remove `anyio` → runnable/import check fails;
- add source-worktree `.pth` → verification fails;
- enable network unexpectedly → build policy test fails.

## Acceptance

The following must exit 0:

```bash
make audit-release
make test-runtime-release-host
```

Additional acceptance:

- Release build succeeds with network disabled.
- Missing dependency artifacts fail before promotion.
- Release is copied and symlink-free.
- Release runs without the source checkout.
- No unapproved dependency version changes.
- The release evidence contains no secret or absolute private credential path.

## Stop conditions

Stop if:

- offline build still reaches the network;
- a dependency cannot be pinned;
- the release imports from the source worktree;
- a symlink is required;
- unrelated packages must be upgraded;
- the clean worktree cannot be established;
- runtime services would need to be started.

## Deliverables

```text
docs/implementation/foundation-release-host-precheck.md
docs/implementation/foundation-offline-dependency-closure.md
docs/implementation/foundation-release-build-evidence.md
docs/implementation/foundation-release-reproducibility.md
docs/implementation/foundation-release-tamper-evidence.md
docs/implementation/foundation-release-host-final.md
```

## Final decision

Return exactly one:

```text
GO - HOST RELEASE PROOF CLOSED
```

or:

```text
NO-GO - HOST RELEASE PROOF STILL FAILING
```
