# WS-01C Task 4 report — sealed CPython 3.12 build-wheel cache

## Outcome and scope

Implemented the separate acquisition and offline-verification boundary for the
exact six-wheel CPython 3.12 build closure accepted by Task 3. A real cache was
downloaded only from the public PyPI index, hash/size bound, sealed externally,
and verified inside a Bubblewrap network namespace with no network route.

No package was installed into the root/controller Python or any other Python.
No Nautilus engine was built, imported, installed, or activated. No Rust tool,
compiler, broker, exchange, account, order, database, service, or runtime
authority was accessed.

## Files changed

- `engines/nautilus/wheel-cache-policy.json` — exact CPython 3.12, public-index,
  six-package acquisition policy bound to the Task 3 engine policy SHA-256.
- `scripts/prepare_nautilus_wheel_cache.py` — explicit-Python acquisition,
  exact-wheel manifest writer/sealer, and offline verifier.
- `tests/foundation/test_nautilus_wheel_cache.py` — focused policy,
  acquisition, privacy, compatibility, sealing, offline, adversarial, CLI,
  and direct Task 3 acceptance tests.
- `scripts/build_nautilus_engine.py` — wheel metadata selection now uses the
  single root-level distribution metadata record, allowing the real
  setuptools wheel's vendored nested `.dist-info` records without confusing
  them for the wheel's own identity.
- `engines/nautilus/README.md` — acquisition and offline verification contract
  and operator commands.

No lockfile, dependency graph, generated contract, cache, wheel, virtual
environment, native library, or toolchain output was committed.

## Exact reviewed build closure

The selected versions satisfy Task 3 and upstream `pyproject.toml`: Cython and
poetry-core retain the upstream exact pins, numpy satisfies `>=1.26.4`, and
setuptools satisfies `>=82`. Packaging and pip provide Task 3's isolated
bootstrap/build tooling. Acquisition used `--only-binary=:all:`, `--no-deps`,
`--implementation cp`, `--python-version 3.12`, and exact `==` requirements.

| Package | Exact public artifact | Size | SHA-256 |
| --- | --- | ---: | --- |
| cython 3.2.4 | `cython-3.2.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl` | 3,388,969 | `55b6c44cd30821f0b25220ceba6fe636ede48981d2a41b9bbfe3c7902ce44ea7` |
| numpy 2.4.3 | `numpy-2.4.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | 16,621,358 | `e7dd01a46700b1967487141a66ac1a3cf0dd8ebf1f08db37d46389401512ca97` |
| packaging 26.0 | `packaging-26.0-py3-none-any.whl` | 74,366 | `b36f1fef9334a5588b4166f8bcd26a14e521f2b55e6b9de3aaa80d3ff7a37529` |
| pip 26.1 | `pip-26.1-py3-none-any.whl` | 1,812,804 | `4e8486d821d814b77319acb7b9e8bf5a4ee7590a643e7cb21029f209be8573c1` |
| poetry-core 2.3.1 | `poetry_core-2.3.1-py3-none-any.whl` | 340,949 | `db1cf63b782570deb38bfba61e2304a553eef0740dc17959a50cc0f5115ee634` |
| setuptools 82.0.1 | `setuptools-82.0.1-py3-none-any.whl` | 1,006,223 | `a59e362652f08dcd477c78bb6e7bd9d80a7995bc73ce773050228a348ce2e5bb` |

## External cache and approval digest

- Explicit acquisition interpreter: `/usr/bin/python3.12` (`Python 3.12.3`)
- External private cache:
  `/home/thenam176/.cache/nautilus-ws01c-wheel-cache-v1`
- External manifest:
  `/home/thenam176/.cache/nautilus-ws01c-wheel-cache-v1/wheel-cache-manifest.json`
- Operator-review manifest SHA-256:
  `0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b`
- Cache directory mode: `0500`
- Six wheel modes and manifest mode: `0400`

The published cache is flat and contains only those six wheels plus
`wheel-cache-manifest.json`. The private staging home, downloads directory,
and pip cache were removed before final placement. No acquisition staging
directory remains.

## Security and behavior decisions

- The wheel policy is bound to Task 3's committed engine policy digest
  `e1a9292997b9b4ac821b1292f8e340d92aeffd49422a7b379f7d76dce443b0dd`.
  Its package names must exactly equal Task 3's pinned and reviewed-unpinned
  package sets.
- Acquisition rejects a non-absolute, in-checkout, existing, symlinked, or
  non-private cache destination. Its immediate parent must be operator-owned
  mode `0700`.
- The supplied interpreter must be a direct, non-symlink, non-group/world
  writable executable reporting exact `CPython 3.12.x`. Pip is invoked only as
  that explicit path with `-I -m pip download`; no `install` action exists.
- The subprocess environment is reconstructed. Ambient `PIP_CACHE_DIR`,
  `PIP_INDEX_URL`, `PYTHONPATH`, user-site settings, and pip configuration are
  not inherited. `PIP_CONFIG_FILE=/dev/null`, a private staging `HOME`, and an
  explicit private temporary `--cache-dir` are used.
- Acquisition accepts exactly one wheel per reviewed package, validates the
  root-level wheel metadata name/version and CPython 3.12 or pure-Python tags,
  records every SHA-256 and size, then seals files `0400` and the directory
  `0500` before atomic final placement.
- Offline verification invokes no Python package tool or other subprocess. It
  rejects a wrong out-of-band manifest digest, mutable cache, missing/extra
  file, symlink, hard link, size/hash drift, metadata mismatch, duplicate
  package, unknown manifest field, or incompatible wheel ABI/tag.
- The emitted manifest uses exactly Task 3's schema. Both the focused test and
  a real network-disabled probe passed the actual Task 3
  `verify_wheel_cache` function against the materialized cache.

## TDD evidence

The committed wheel-policy test was written first and failed because the
policy file did not exist. After the minimal policy was added, it passed.
The acquisition/schema test was then written and failed because the tool did
not exist. After the minimal acquisition/verifier implementation it passed.

An adversarial compatibility test then demonstrated that the first tag parser
accepted `py3-cp311-any`; the red run failed with `DID NOT RAISE`. The parser
was narrowed to accept only CPython 3.12 native ABI wheels or
`py3-none-any`/`py2.py3-none-any` pure wheels.

Finally, the first real public acquisition failed closed on setuptools 82.0.1
because it legitimately contains its own root metadata plus vendored nested
`.dist-info/METADATA` records. The fixture was updated to reproduce that real
shape before production was changed; six tests failed at the same metadata
boundary. Both verifiers were then narrowed to the single root-level metadata
record, and the combined Task 3/4 suite passed.

## Validation commands and results

Focused Task 1–4 and provenance suite:

```bash
uv run pytest -q --basetemp=/tmp/nautilus-wheel-cache-final-focused \
  tests/foundation/test_nautilus_wheel_cache.py \
  tests/foundation/test_nautilus_engine_build.py \
  tests/foundation/test_nautilus_input_cache.py \
  tests/foundation/test_nautilus_toolchain_cache.py \
  tests/foundation/test_nautilus_provenance.py
# 52 passed
```

Syntax and diff checks:

```bash
uv run python -m py_compile \
  scripts/prepare_nautilus_wheel_cache.py \
  scripts/build_nautilus_engine.py \
  tests/foundation/test_nautilus_wheel_cache.py
git diff --check
# PASS
```

Repository gates:

```bash
make audit
# result=PASS
make check-contracts
# PASS
make check-secrets
# PASS
```

Real offline verification used `/usr/bin/bwrap --unshare-net` and returned:

```text
nautilus wheel cache verification: PASS
Task 3 wheel cache verification: PASS 6
```

`uv run ruff` was attempted, but Ruff is not installed or declared in the
root development dependencies. The documented repository checks, Python
compilation, focused tests, and offline verification all passed.

## Commits

- `9957afb build(nautilus): seal Python build wheel cache`
- Task report: follow-up documentation commit containing this file.

## Concerns and external preconditions

- The real cache is under the operator's private `~/.cache` tree. It is
  external to Git but remains subject to operator cleanup or host loss; copy it
  to durable operator-managed storage before relying on it for a release.
- The Cython and numpy artifacts are Linux x86-64 manylinux wheels. A build on
  another architecture/platform requires a separately reviewed policy/cache
  and a new manifest digest; this cache must not be silently reused.
- Any Task 3 engine policy change invalidates the wheel policy's bound digest
  and requires explicit review and cache regeneration.
- This task intentionally did not build the Nautilus engine. Task 3's recorded
  `clang`, `clang++`, and `lld` host precondition remains unresolved and no
  compiler was installed or acquired here.
