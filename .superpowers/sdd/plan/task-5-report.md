# WS-01C Task 5 report — private LLVM build toolchain

## Outcome and scope

Implemented a pinned external LLVM cache, offline verifier, safe extractor, and
atomic private toolchain materializer. Task 3 now requires this explicit LLVM
root, uses absolute `CC`/`CXX`/`LD`, and rejects ambient compiler names in its
later system `PATH` entries. No system/global compiler was installed. No engine
was built or activated, and no broker, exchange, account, order, database,
service, or runtime authority was accessed.

## Official asset and verification

- Release: LLVM 22.1.3, `llvmorg-22.1.3`, Linux x86-64.
- Archive: `LLVM-22.1.3-Linux-X64.tar.xz`, 1,939,973,900 bytes.
- URL: `https://github.com/llvm/llvm-project/releases/download/llvmorg-22.1.3/LLVM-22.1.3-Linux-X64.tar.xz`.
- Archive SHA-256: `6e776c396895837a168a36d13e0b0e4552680eda58d8dab6aa5fb75e2c3d13ea`.
- Official detached signature SHA-256:
  `26a5f92193a21119c105d3e5d9046bdf11337458bff815213d44f4ee8ed81a22`.
- Initial acquisition passed `gpgv` using LLVM's published release keyring:
  good EDDSA signature from Cullen Rhodes, fingerprint
  `71046D1E9C6656BDD61171873E83BABF4A4F9E85`.
- Committed policy SHA-256:
  `3b58cae00f0db0eaa489d3302b83ac61e94e81a15fa8eb40de9d11e2a9fd0954`.

The initial signature establishes the acquisition provenance. The pinned
archive SHA-256/size and per-tool SHA-256/size records are the future offline
approval boundary; future cache and toolchain verification does not contact
GitHub or a keyserver.

## External cache and tool identities

- Verified acquisition inputs:
  `/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-acquisition`
- Sealed archive cache:
  `/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-cache`
- Cache-manifest SHA-256:
  `1cfcd5f0503e14db9d4bc00edaaa9cf42271325af3354b92f1c0dbd6ca87a12d`
- Sealed direct-binary toolchain:
  `/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-toolchain`
- Toolchain-manifest SHA-256:
  `7c0dcd43ee50169dbdc51d4b07c7580d74a0fa50a4bd6aec8457980cad517b29`

The release archive contains `clang -> clang-22`, `clang++ -> clang`, and
`ld.lld -> lld`. Materialization resolves those within the reviewed archive
root and writes three independent, single-link, non-symlink mode `0500`
binaries under mode `0500` directories:

- `clang`: 271,521,920 bytes,
  `05148feea3de2d50a7a1a7c51aedddb2cde03da93038f0d6b13f49422c6d744f`;
  `clang version 22.1.3 (... e9846648fd6183ee6d8cbdb4502213fcf902a211)`.
- `clang++`: same direct bytes and identity as `clang`, but a distinct inode.
- `ld.lld`: 200,223,320 bytes,
  `879d47621106d726c18f0d25f25bf73167d4109521b147341e40139181e3db05`;
  `LLD 22.1.3 (... e9846648fd6183ee6d8cbdb4502213fcf902a211)`.

Cache publication and materialization use randomly named private staging under
an open no-follow parent descriptor, seal files/directories, and atomically
rename into an absent destination. Verification rejects unsafe/out-of-root
archive paths or links, duplicate/special members, symlinked ancestors,
mutable or hard-linked files, unexpected files, manifest/policy drift, and
binary identity drift. Absolute private invocations and focused hostile-`PATH`
tests prove no ambient identity fallback.

## TDD and validation evidence

Initial red runs produced 11 expected failures for the absent policy/tool and
three expected Task 3 failures for the absent explicit LLVM contract. A later
acquisition regression failed specifically because descriptor-backed
`/proc/self/fd` staging was mistaken for an untrusted lexical symlink; the
minimal descriptor-bound exception then passed. The ambient-fallback test also
failed before Task 3 gained the fail-closed system-path check.

Passed final checks:

```text
uv run pytest -q --basetemp=/tmp/nautilus-llvm-final-3 \
  tests/foundation/test_nautilus_llvm_toolchain.py \
  tests/foundation/test_nautilus_engine_build.py \
  tests/foundation/test_nautilus_wheel_cache.py \
  tests/foundation/test_nautilus_input_cache.py \
  tests/foundation/test_nautilus_toolchain_cache.py \
  tests/foundation/test_nautilus_provenance.py
# 66 passed

make audit
make check-contracts
make check-secrets
uv run python -m py_compile <changed Python files>
git diff --check
# PASS
```

Both cache and toolchain verification passed inside Bubblewrap with
`--unshare-net`. A compile/link probe with an otherwise empty environment and
private-only `PATH` produced and ran an x86-64 ELF using absolute private
`clang++ -fuse-ld=lld`.

## Commits

- `287563a build(nautilus): add private LLVM toolchain cache`
- `ea945d2 fix(nautilus): reject ambient LLVM fallback`
- Task report: follow-up documentation commit containing this file.

## Concerns

- The acquisition directory and sealed cache intentionally retain separate
  copies of the 1.94 GB official archive. The acquisition copy preserves the
  official signature/attestation evidence; an operator may archive it to
  durable private storage after approving the sealed cache.
- The private binaries still use the host's normal Linux runtime and C/C++
  development libraries. A private-only compile/link probe passed on this
  host, but this task intentionally did not run the prohibited engine build.
- The rejected unsigned-for-this-platform LLVM 18 exploratory download and
  the temporary compile proof were moved to the user's recoverable trash; they
  are not part of the approved cache.
