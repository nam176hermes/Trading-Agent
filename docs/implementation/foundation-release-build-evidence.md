# Foundation Release Build Evidence

## Build controls

The controlled preparation step performs these actions:

1. Requires a clean repository.
2. Exports the production dependency closure from `uv.lock` with `--frozen`, `--no-dev`, `--no-editable` and `--no-emit-project`.
3. Downloads pinned binary wheels from public PyPI with `pip==25.1.1`, `--require-hashes`, `--no-deps` and `--only-binary=:all:`.
4. Proves installation in a fresh virtual environment with network disabled.
5. Writes and seals the wheelhouse manifest.
6. Promotes the wheelhouse and verifies it again.

The release build verifies the wheelhouse before and after installation. It uses `uv pip sync` with `--require-hashes`, `--strict`, `--only-binary=:all:`, `--offline`, `--no-index`, `--find-links`, `--no-cache` and copy link mode.

## Relocatable project source

Third-party dependencies come from the wheelhouse. Project modules remain copied source from the exact Git object. The virtual environment contains a fixed relative `.pth` file:

```text
../../../..
../../../../apps/control_api
```

These paths resolve inside the promoted release. They do not point to the checkout or wheelhouse.

## Executed proof

Command:

```bash
make test-runtime-release-host
```

Observed result before evidence-document commit:

```text
1 passed, 1 skipped, 242 deselected
exit 0
```

The test builds twice from the same commit, compares manifests and logical digests, rejects symlinks, removes bytecode, relocates the second artifact, imports the Job API and worker, imports Uvicorn, and runs `python -m uvicorn --help`.

It also inspects runtime `sys.path` and asserts:

- The source checkout is absent.
- The promoted release root is present.
- The promoted `apps/control_api` path is present.

## Evidence locations

- Host test: `tests/runtime_release/test_build.py:187`
- Offline install: `packages/runtime_release/manifest.py:536`
- Relative source mapping: `packages/runtime_release/manifest.py:524`
- Host log: `/tmp/p01-host-release-no-source-checkout.log`
