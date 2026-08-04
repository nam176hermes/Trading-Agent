# Isolated Nautilus engine build contract

This directory defines a paper-safe build boundary for NautilusTrader
`v1.227.0`. It creates a CPython 3.12 wheel as a sealed external candidate. It
does not install, import, activate, or run the engine.

## Required external inputs

All paths must be absolute, private, and outside this checkout:

- CPython 3.12 executable (the repository controller remains Python 3.11);
- Task 1's private Rust 1.95.0 `cargo` and sibling `rustc`, never rustup or a
  global Rust installation;
- Task 2's verified source/Cargo cache;
- a flat, sealed build-wheel cache and its separately reviewed manifest
  SHA-256;
- an absent artifact destination under an operator-owned private directory;
- Bubblewrap, normally `/usr/bin/bwrap`, for the network namespace.

The pinned upstream build script selects `clang`, `clang++`, and the LLVM
linker on Linux. Those host build tools must already be installed in
`/usr/bin`; the contract does not install system packages. They were not
present on the implementation host, so they are a second execution-time host
precondition after the approved wheel cache.

The approved build-wheel cache is produced separately by
`scripts/prepare_nautilus_wheel_cache.py` from the exact versions in
`wheel-cache-policy.json`. The acquisition command requires an explicit
CPython 3.12 executable, uses only the public PyPI index, downloads wheels
without dependencies into private staging and a private temporary pip cache,
and never installs them. The build itself still fails closed rather than
downloading a missing wheel. The cache must contain only regular,
single-link `0400` wheel files and `wheel-cache-manifest.json`; its directory
must be `0500`. The manifest itself must be `0400`, its SHA-256 must be supplied
out of band, and it has this schema:

```json
{
  "schema_version": 1,
  "python_minor": "3.12",
  "artifacts": [
    {
      "filename": "poetry_core-2.3.1-py3-none-any.whl",
      "package": "poetry-core",
      "version": "2.3.1",
      "role": "build",
      "sha256": "<64 lowercase hexadecimal characters>",
      "size": 123456
    }
  ]
}
```

Every artifact listed must be present and no other file is accepted. Wheel
metadata must match each manifest record. The cache must contain exactly the
pinned Cython 3.2.4 and poetry-core 2.3.1 inputs plus approved `numpy`,
`packaging`, `pip`, and `setuptools` wheels compatible with CPython 3.12. The
operator-reviewed manifest digest is the approval boundary for the otherwise
unpinned upstream `numpy`, `packaging`, `pip`, and `setuptools` constraints.

Create the cache under an existing operator-owned `0700` parent outside this
checkout, then save the printed manifest digest out of band:

```bash
/usr/bin/python3.12 -I scripts/prepare_nautilus_wheel_cache.py \
  --policy engines/nautilus/wheel-cache-policy.json \
  --engine-policy engines/nautilus/engine-build-policy.json \
  --cache /absolute/private/nautilus-wheel-cache \
  --python /usr/bin/python3.12 \
  --acquire

/usr/bin/python3.12 -I scripts/prepare_nautilus_wheel_cache.py \
  --policy engines/nautilus/wheel-cache-policy.json \
  --engine-policy engines/nautilus/engine-build-policy.json \
  --cache /absolute/private/nautilus-wheel-cache \
  --manifest-sha256 <reviewed-sha256> \
  --verify
```

Verification is offline: it does not invoke Python package tooling or access
an index. It rechecks the manifest digest, the exact file set, modes, regular
single-link status, wheel metadata and CPython 3.12 tags, and every wheel's
recorded SHA-256 and size.

## Build and verification

Example using the external Task 1/2 paths created during WS-01C. Paths under
`/tmp` are ephemeral and should be copied to an operator-owned private cache
before they are relied on:

```bash
make build-nautilus-engine \
  NAUTILUS_ENGINE_PYTHON=/usr/bin/python3.12 \
  NAUTILUS_ENGINE_INPUT_CACHE=/tmp/nautilus-ws01c-input-cache-v2 \
  NAUTILUS_ENGINE_WHEEL_CACHE=/absolute/private/approved-wheel-cache \
  NAUTILUS_ENGINE_WHEEL_CACHE_MANIFEST_SHA256=<reviewed-sha256> \
  NAUTILUS_ENGINE_CARGO=/tmp/nautilus-ws01c-rust-toolchain/bin/cargo \
  NAUTILUS_ENGINE_ARTIFACTS=/absolute/private/nautilus-1.227.0-cp312

make verify-nautilus-engine \
  NAUTILUS_ENGINE_PYTHON=/usr/bin/python3.12 \
  NAUTILUS_ENGINE_ARTIFACTS=/absolute/private/nautilus-1.227.0-cp312
```

The build first re-verifies the Task 2 cache and every wheel. It creates its
own CPython 3.12 environment under private staging, copies the Cargo cache, and
runs every environment-creation, wheel-installation, and build command inside
Bubblewrap with a new network namespace. The host filesystem is read-only
inside that namespace except for the staging directory. `CARGO_NET_OFFLINE`,
`PIP_NO_INDEX`, and `UV_OFFLINE` are also set. A caller cannot select a
network-enabled build path.

The destination contains only the wheel and `artifact-manifest.json`, both
`0400` under a `0500` directory. The manifest binds source/Cargo/wheel-cache
provenance, Python/Rust identities, the wheel hash and size, and every native
library inside the wheel by path, SHA-256, and size. Verification rejects a
wrong Python, mutable or substituted artifact, unexpected file, wheel drift,
or an unmanifested native library.
