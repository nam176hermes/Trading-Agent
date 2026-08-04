# WS-01C Task 2 report — source and Cargo input cache

## Scope completed

Implemented only the external Nautilus source/Cargo input cache. No Nautilus
engine was built, installed, activated, or imported. No broker, exchange,
account, order, database, service, or runtime endpoint was accessed.

## Files changed

- `engines/nautilus/input-cache-policy.json` — reviewed, immutable 01B source
  boundary and expected `Cargo.lock`/`pyproject.toml` digests.
- `scripts/prepare_nautilus_input_cache.py` — acquisition and offline
  verification tool.
- `tests/foundation/test_nautilus_input_cache.py` — focused policy,
  acquisition, offline, immutability, digest-drift, missing-input, symlink,
  and private-toolchain tests.

## Cache records and external paths

The actual cache and private toolchain remain outside Git:

- Rust installer cache: `/tmp/nautilus-ws01c-rust-cache`
- Materialized Rust 1.95.0 toolchain:
  `/tmp/nautilus-ws01c-rust-toolchain`
- Nautilus source/Cargo cache:
  `/tmp/nautilus-ws01c-input-cache-v2`
- External input-cache manifest:
  `/tmp/nautilus-ws01c-input-cache-v2/input-cache-manifest.json`

The generated manifest SHA-256 is
`e0e249e0604d2d6790666f145b7d26a628648a6e5d500a3eb08e6a242e735fff`.
It contains 45,769 individually hash-bound downloaded or derived inputs.

The official source archive entry is SHA-256
`a00d3ab0c5b2ba1e4a4ac4c9af70f5b3fe30717d9b42a328e51696e3894a45e2`.
The cache records upstream commit
`280ae1762df51a492a4ce71506a40b5c8706def5` and `Cargo.lock` SHA-256
`083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed`.
The explicitly supplied private tools reported `rustc 1.95.0
(59807616e 2026-04-14)` and `cargo 1.95.0 (f2d3ce0bd 2026-03-21)`.

## Security decisions

- The committed policy reproduces the 01B annotated-tag object, peeled commit,
  repository, source URL, source-archive SHA-256, and reviewed source-file
  digests exactly.
- Acquisition uses only the caller-provided absolute Cargo path. It validates
  direct, non-symlink Cargo and sibling rustc executables, verifies both are
  Rust 1.95.0, removes `RUSTUP_HOME`, sets `RUSTC` directly to the private
  sibling executable, and never executes a bare `cargo` or `rustc` from
  `PATH`.
- The source archive is retained as one hash-bound artifact. Its extraction is
  temporary; absolute, escaping, hard-link, special-file, and non-rooted
  archive entries are rejected. Nautilus's own in-root relative LICENSE
  symlinks are accepted only after proving their targets remain under the
  archive root.
- Every cache file, including the manifest, is verified as a single regular,
  non-symlink file with SHA-256 and mode `0400`; every cache directory is
  non-symlink mode `0500`. Missing, unexpected, mutable, hard-linked,
  symlinked, and hash-drifted cache inputs fail verification.
- Offline verification reads only the policy and cache; it neither invokes
  Cargo nor contacts the network.

## TDD and validation evidence

The initial committed-policy test failed because the policy was absent; the
tool-presence test then failed because the cache tool was absent; the
acquisition test then failed because `acquire` was absent. The minimal policy
and tool were added before extending the focused behavioral tests.

Passed commands:

```bash
uv run pytest -q tests/foundation/test_nautilus_input_cache.py \
  tests/foundation/test_nautilus_toolchain_cache.py \
  tests/foundation/test_nautilus_provenance.py
# 26 passed

uv run python scripts/verify_nautilus_provenance.py --root .
# nautilus provenance verification: PASS

CARGO_NET_OFFLINE=true uv run python scripts/prepare_nautilus_input_cache.py \
  --policy engines/nautilus/input-cache-policy.json \
  --cache /tmp/nautilus-ws01c-input-cache-v2 --verify
# nautilus input cache verification: PASS
```

The actual acquisition used only
`/tmp/nautilus-ws01c-rust-toolchain/bin/cargo`; it ran `cargo fetch --locked`
and did not run a build.

## Commits

- `81c6a63 build(nautilus): cache source and Cargo inputs`
- `0030155 fix(nautilus): pin cached source archive digest`
- `303b117 fix(nautilus): reject symlinked cache ancestors`
- `d1cab5f fix(nautilus): create private cache parents safely`
- `766536b fix(nautilus): bind input cache staging to parent fd`

## Concerns

- The completed external cache is under `/tmp`, which may be cleared by host
  maintenance. Preserve it in an operator-owned private external-cache path
  before relying on it for a later WS-01C task.
- The manifest intentionally tracks Cargo registry/index inputs as well as
  crate archives, producing a large (45,769-entry) closure. This is expected
  for a fail-closed offline cache and remains external to Git.

## Review fix round 1 — symlink ancestry and portable tests

The review found that final-node `lstat` checks did not detect a symlink in an
ancestor of a supplied Cargo/rustc or cache path. The cache tool now walks each
lexical ancestor with `lstat` before invoking Cargo, materializing a cache, or
verifying one. An absolute path which traverses a symlink now fails with a
`symlinked ancestor` error. This covers both the caller-supplied Cargo/rustc
path and the cache path used by acquisition and offline verification.

The private-toolchain fixture now leaves its temporary `bin` and toolchain
directories at `0700`, rather than `0500`, so its deliberate rename/symlink
mutation is portable on a real Linux `/tmp` filesystem. Cached output remains
tested as `0500` directories and `0400` files.

Source archive preflight now explicitly rejects an absolute symlink `linkname`
before extraction. The existing relative-link containment check remains in
place for Nautilus's reviewed in-root LICENSE links.

New regressions prove:

- Cargo and rustc reject an otherwise-regular executable through a symlinked
  ancestor.
- Offline cache verification rejects a cache path through a symlinked parent.
- Extraction rejects an absolute archive symlink target.
- The final-node symlink case still rejects correctly on a real `/tmp` pytest
  base directory.

TDD red evidence: before the hardening, the new focused tests produced four
expected failures—Cargo and rustc aliases were accepted, a cache alias was
accepted, and an absolute archive link reached `tarfile` rather than the
reviewed verifier rejection. After the minimal hardening, the actual Linux
temporary-root validation passed:

```bash
uv run pytest -q --basetemp=/tmp/nautilus-input-cache-review-2 \
  tests/foundation/test_nautilus_input_cache.py \
  tests/foundation/test_nautilus_toolchain_cache.py \
  tests/foundation/test_nautilus_provenance.py
# 30 passed

uv run python scripts/verify_nautilus_provenance.py --root .
# nautilus provenance verification: PASS

CARGO_NET_OFFLINE=true uv run python scripts/prepare_nautilus_input_cache.py \
  --policy engines/nautilus/input-cache-policy.json \
  --cache /tmp/nautilus-ws01c-input-cache-v2 --verify
# nautilus input cache verification: PASS
```

## Review fix round 3 — descriptor-bound staging and final placement

The parent-directory preparation now returns an open no-follow directory file
descriptor and acquisition keeps it open for the entire transaction. It checks
the destination with `os.stat(..., dir_fd=parent_fd, follow_symlinks=False)`,
creates a randomly named private staging directory with `os.mkdir(...,
dir_fd=parent_fd)`, and opens that staging directory with `O_NOFOLLOW`.

All staging writes are addressed through the open staging descriptor's
`/proc/self/fd/<n>` handle. Cargo receives that descriptor through
`pass_fds`, so its explicitly-set `CARGO_HOME` remains bound to the verified
staging directory. Final placement uses `os.replace` with both source and
destination `dir_fd=parent_fd`; it never reuses the caller's mutable lexical
cache-parent path. A final descriptor-relative destination check fails closed
if the target changed during acquisition. Failed staging directories are thawed
and removed via the verified parent descriptor.

The deterministic regression opens the verified parent, renames it, and
replaces the original lexical parent path with a symlink before acquisition
continues. Before this fix, acquisition wrote the cache through that substituted
path. It now places the cache only in the renamed, descriptor-backed parent and
leaves the substituted directory untouched.

Fresh validation used a real Linux `/tmp` pytest base directory:

```bash
uv run pytest -q --basetemp=/tmp/nautilus-input-cache-round3-full \
  tests/foundation/test_nautilus_input_cache.py \
  tests/foundation/test_nautilus_toolchain_cache.py \
  tests/foundation/test_nautilus_provenance.py
# 32 passed

uv run python scripts/verify_nautilus_provenance.py --root .
# nautilus provenance verification: PASS

CARGO_NET_OFFLINE=true uv run python scripts/prepare_nautilus_input_cache.py \
  --policy engines/nautilus/input-cache-policy.json \
  --cache /tmp/nautilus-ws01c-input-cache-v2 --verify
# nautilus input cache verification: PASS
```

## Review fix round 2 — new cache parents

The round-1 preflight required `cache.parent` to already exist, accidentally
rejecting the documented normal case of acquiring into a new external cache
path. Acquisition now creates any missing parent components using
descriptor-relative `mkdir`/`open` operations rooted at `/`, with
`O_DIRECTORY | O_NOFOLLOW` and `dir_fd`. Each existing component is inspected
without following symlinks; a raced replacement can only produce a fail-closed
unsafe-ancestor error. Newly created components are explicitly set to `0700`.
Path traversal segments are rejected before the walk begins.

The new regression proves acquisition succeeds for a missing
`new-parent/nested/cache` path, verifies that both created parent directories
are `0700`, and completes offline verification. The existing cache-alias test
now also proves acquisition rejects a missing cache target through a symlinked
parent, in addition to rejecting verification through that alias.

TDD red evidence: the new cache-parent test initially failed with `input cache
has a missing ancestor`. After descriptor-relative creation, the focused
validation ran using a real Linux `/tmp` pytest base directory:

```bash
uv run pytest -q --basetemp=/tmp/nautilus-input-cache-round2-full \
  tests/foundation/test_nautilus_input_cache.py \
  tests/foundation/test_nautilus_toolchain_cache.py \
  tests/foundation/test_nautilus_provenance.py
# 31 passed

uv run python scripts/verify_nautilus_provenance.py --root .
# nautilus provenance verification: PASS

CARGO_NET_OFFLINE=true uv run python scripts/prepare_nautilus_input_cache.py \
  --policy engines/nautilus/input-cache-policy.json \
  --cache /tmp/nautilus-ws01c-input-cache-v2 --verify
# nautilus input cache verification: PASS
```
