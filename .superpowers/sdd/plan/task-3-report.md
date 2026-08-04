# WS-01C Task 3 report — isolated Python engine build and artifact verifier

## Outcome and scope

Implemented only the Python 3.12 Nautilus engine build contract and artifact
verifier. The contract constructs a sealed external wheel candidate; it does
not install or activate the engine. No broker, exchange, account, order,
database, service, or runtime authority was accessed.

The real upstream engine wheel was not built because there is no approved
external build-wheel cache. This is an explicit external precondition in the
task brief. The implemented build path re-verifies the existing Task 2 cache,
then fails closed at the absent wheel-cache boundary without downloading or
broadening dependencies.

## Files changed

- `engines/nautilus/engine-build-policy.json` — exact CPython 3.12,
  NautilusTrader 1.227.0, 01B source/tag/commit, Rust 1.95.0, and required
  build-wheel policy.
- `engines/nautilus/README.md` — operator contract, external cache schema,
  preconditions, Make invocations, and no-activation boundary.
- `scripts/build_nautilus_engine.py` — build-input verifier, offline sandboxed
  builder, external wheel-cache verifier, artifact-manifest writer, and
  artifact verifier.
- `tests/foundation/test_nautilus_engine_build.py` — focused TDD coverage for
  policy, wrong Python, root Python 3.11 isolation, network mode, real network
  namespace, wheel-cache trust, native-library drift, artifact sealing, and
  Make entry points.
- `Makefile` — `build-nautilus-engine` and `verify-nautilus-engine`.

No lockfile, dependency graph, generated contract, cache, virtual environment,
wheel, native library, or toolchain output was committed.

## Architecture and security decisions

- The repository controller remains Python 3.11. The explicitly supplied
  build interpreter must be one direct, non-symlink CPython 3.12 executable;
  the output manifest records its full patch identity. Wheels must declare the
  exact `>=3.12,<3.15` requirement and only `cp312` tags.
- The builder imports and runs Task 2's verifier before source extraction. It
  consumes the exact pinned source archive and copies the verified Cargo home
  into private mutable staging. Cargo and sibling rustc must be the explicit
  private Rust 1.95.0 tools; no rustup or bare global toolchain path is used.
- All Python environment creation, wheel installation, and upstream wheel
  building runs in root-owned Bubblewrap with `--unshare-net`. The host root is
  read-only and only private external staging is writable. The environment is
  cleared and reconstructed with `CARGO_NET_OFFLINE=true`, `PIP_NO_INDEX=1`,
  `UV_OFFLINE=1`, an explicit `RUSTC`, and the private Cargo directory first in
  `PATH`.
- The approved cached `pip` wheel bootstraps the isolated venv. The build does
  not depend on a globally installed pip package and never asks pip or uv for
  an index.
- The wheel cache is flat and sealed: directory `0500`, manifest and wheels
  `0400`, regular single-link files, exact file set, exact SHA-256 and size,
  and metadata matching every record. The caller must supply the separately
  reviewed SHA-256 of `wheel-cache-manifest.json`. Unknown packages are
  rejected.
- The build destination and every cache/tool path must be absolute and
  external to the checkout. The destination must not exist and its parent must
  already be an operator-owned `0700` directory.
- The sealed artifact directory contains only one wheel and
  `artifact-manifest.json`. The manifest binds the annotated-tag object,
  commit, source/Cargo.lock/pyproject digests, full Python/Cargo/rustc
  identities, Task 2 manifest SHA-256, approved wheel-cache manifest SHA-256,
  produced wheel SHA-256/size, and every embedded native library by path,
  SHA-256, and size. Verification rejects symlinks, mutable files, extra
  files, wrong tags/metadata, digest drift, missing libraries, and unexpected
  native libraries.
- Nothing places the artifact or engine package on Python 3.11's import path.
  A real isolated Python 3.11 subprocess proves `import nautilus_trader`
  raises `ModuleNotFoundError`.

## TDD evidence

The initial focused test was written before the policy/tool implementation.
It produced five expected failures for the absent engine tool/policy while the
root-Python isolation probe already passed:

```text
5 failed, 1 passed
```

The Make contract test was then added before the targets and failed with:

```text
make: *** No rule to make target 'build-nautilus-engine'.  Stop.
1 failed
```

After the minimal implementations, the final focused Task 3 suite passed:

```bash
uv run pytest -q --basetemp=/tmp/nautilus-engine-green-6 \
  tests/foundation/test_nautilus_engine_build.py
# 11 passed
```

The tests explicitly reject:

- a CPython 3.11 target at both the interpreter validator and artifact
  verifier boundary;
- `offline=False` before any build input is read;
- an unmanifested native `.so` even when the enclosing wheel digest/size is
  updated in the adversarial fixture;
- an unapproved wheel-cache manifest digest, wheel hash drift, and an extra
  wheel-cache file.

The Bubblewrap test runs a real process inside the build boundary and verifies
that `/proc/net/route` has no host default route.

## Validation commands and results

Focused Task 1/2/3 and provenance tests:

```bash
uv run pytest -q --basetemp=/tmp/nautilus-engine-focused \
  tests/foundation/test_nautilus_engine_build.py \
  tests/foundation/test_nautilus_input_cache.py \
  tests/foundation/test_nautilus_toolchain_cache.py \
  tests/foundation/test_nautilus_provenance.py
# 44 passed
```

Existing provenance and external Task 2 cache:

```bash
uv run python scripts/verify_nautilus_provenance.py --root .
# nautilus provenance verification: PASS

CARGO_NET_OFFLINE=true uv run python scripts/prepare_nautilus_input_cache.py \
  --policy engines/nautilus/input-cache-policy.json \
  --cache /tmp/nautilus-ws01c-input-cache-v2 --verify
# nautilus input cache verification: PASS
```

Repository checks:

```bash
make audit
# result=PASS

make check-contracts
# PASS

make check-secrets
# PASS

python3.11 -m py_compile \
  scripts/build_nautilus_engine.py \
  tests/foundation/test_nautilus_engine_build.py
# PASS

git diff --check
# PASS
```

The fail-closed real-input probe used `/usr/bin/python3.12`, the verified Task
2 cache, the private Rust toolchain, and a deliberately absent approved wheel
cache. It re-verified earlier inputs and exited 2 with:

```text
nautilus engine verification failed: wheel cache has a missing ancestor
```

`uv run ruff` was attempted but the root project has no Ruff executable or
Ruff development dependency, so no Ruff check was available. The repository's
documented checks above and Python compilation passed.

## External paths and identities

- CPython target: `/usr/bin/python3.12` (`Python 3.12.3`)
- Bubblewrap: `/usr/bin/bwrap` (`bubblewrap 0.9.0`)
- Rust installer cache: `/tmp/nautilus-ws01c-rust-cache`
- Private Rust toolchain: `/tmp/nautilus-ws01c-rust-toolchain`
- Private Cargo:
  `/tmp/nautilus-ws01c-rust-toolchain/bin/cargo`
  (`cargo 1.95.0 (f2d3ce0bd 2026-03-21)`)
- Private rustc:
  `/tmp/nautilus-ws01c-rust-toolchain/bin/rustc`
  (`rustc 1.95.0 (59807616e 2026-04-14)`)
- Verified source/Cargo cache: `/tmp/nautilus-ws01c-input-cache-v2`
- Input-cache manifest SHA-256:
  `e0e249e0604d2d6790666f145b7d26a628648a6e5d500a3eb08e6a242e735fff`
- Approved build-wheel cache: not available; must be supplied externally using
  the schema in `engines/nautilus/README.md`.
- Engine artifact destination: not created because the approved wheel cache is
  absent.

Paths under `/tmp` are ephemeral and may be removed by host maintenance.

## Commits

- `5afb429 build(nautilus): add isolated Python engine contract`
- Task report: follow-up documentation commit containing this file.

## Concerns and external preconditions

- A real Nautilus wheel and native-library manifest cannot be produced until
  an operator supplies and separately approves the sealed CPython 3.12 build
  wheel cache. This is the only incomplete external precondition; the code and
  failure boundary are implemented and tested.
- The upstream policy pins Cython and poetry-core exactly. Upstream expresses
  lower bounds for numpy and setuptools and does not pin packaging/pip for this
  controller. Their exact selected wheels are therefore controlled by the
  out-of-band reviewed manifest SHA-256; the verifier rejects any change.
- Bubblewrap is a required host capability for builds. The contract rejects a
  non-root-owned, writable, missing, or non-Bubblewrap sandbox executable.
- No real build was attempted with fabricated or downloaded wheels, and no
  engine activation or Python 3.12 engine import was performed.
