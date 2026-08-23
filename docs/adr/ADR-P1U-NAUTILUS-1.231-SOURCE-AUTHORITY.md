# ADR P1-U02: Nautilus 1.231 source authority

- Status: accepted for candidate qualification only
- Date: 2026-08-23
- Candidate closure schema: 7
- Runtime effect: none

## Decision

The primary build-source authority for Nautilus Trader 1.231.0 is the complete
Git object tree at peeled commit
`27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`. The commit is accepted only when
the offline bare cache proves that annotated tag object
`d3e1685e979925d7b0ffacd1b3f442547686e18f` is the exact object at
`refs/tags/v1.231.0` and peels to that exact commit.

Build input is a deterministic local normalization of that Git tree into a
regular-file tar archive. It is not an upstream archive, an upstream signature,
or an upstream attestation. The normalizer rejects non-blob entries, unsupported
modes, unsafe or NFC-plus-case-fold colliding paths, generated-binary suffixes, and unsafe,
out-of-tree, missing, cyclic, or non-regular symlink targets. Each original Git
path is bound to its original mode and blob, resolved path and blob, output
mode, output digest, and size. The archive contains only those regular files;
parent directories are created by extraction. Fixed ordering, PAX tar metadata,
and gzip time make the result reproducible offline from the same verified Git
cache.

The official PyPI sdist is an independent cross-check only. Its SHA-256 is
`142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f`.
All 57 build-input paths shared with the primary tree are byte-identical. The
sdist omits these five exact-commit inputs:

- `examples/quickstarts/lighter-rust-data-client/Cargo.toml`
- `examples/tutorials/Cargo.toml`
- `patches/pyo3-stub-gen/Cargo.toml`
- `python/pyproject.toml`
- `rust-toolchain.toml`

That bounded omission is why the sdist is not selected as build authority. It
does not contradict the shared paths. The closed policy binds the complete
62-record build-input manifest and the safe sdist layout.

The official CPython 3.12 manylinux wheel is a digest-verified artifact only.
It is not source authority. Its SHA-256 is
`8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216`;
the policy additionally binds its filename, size, tags, roots, member-name
layout, and native-member count.

## Attestation disposition

The annotated Git tag contains no embedded PGP or SSH signature. The PyPI
release files have no legacy GPG signature. PyPI metadata observation found
PEP 740 publish attestations whose subjects name the pinned artifact digests,
but P1-U02 does not admit or cryptographically verify those bundles offline.
Their disposition is therefore
`PRESENT_NOT_CRYPTOGRAPHICALLY_VERIFIED` and non-authoritative. Matching subject
digests do not establish signer identity, source authenticity, or build
provenance here.

Digest verification establishes byte identity against this reviewed policy; it
does not establish a signature or attestation. The exact tag-object-to-commit
relationship establishes Git object identity, but the unsigned annotated tag
does not establish a cryptographic publisher identity.

## Offline cache contract

The verifier accepts only an explicitly supplied private cache outside the
repository under a cooperative owner-controlled host model. Task-UID ownership,
single-link regular files, directory mode `0500`, and file mode `0400` are
inspection preconditions and have exactly:

- `upstream.git`
- `nautilus_trader-27a8e54e7ac3c57d6cbf8891f0283dfbaee97317-materialized.tar.gz`
- `nautilus_trader-1.231.0.tar.gz`
- `nautilus_trader-1.231.0-cp312-cp312-manylinux_2_35_x86_64.whl`

Git commands disable network protocols, lazy fetching, system/global config,
and optional locks. The verifier rejects external common directories,
worktree-scoped configuration, include directives, promisor or partial-clone
state, alternates, fsck overrides, non-bare repositories, and unexpected object
formats before authority reads. It binds the effective Git common/object
directories to the supplied cache, then independently regenerates the primary
archive and compares its exact archive and manifest identities.

The modes do not defend against a hostile process with the same UID. Concurrent
same-UID mutation, root, ptrace or kernel adversaries, rename/chmod races, and
TOCTOU attacks are explicitly out of scope. The verifier claims deterministic
pre-verification inspection and reproducibility on the cooperative host; it
does not claim concurrent-mutation resistance or invent a hostile-same-UID
mechanism.

No supplied cache returns `DEFERRED` with exit 3. Any supplied cache that is
missing, incomplete, stale, mutable, unsafe, ambiguous, or mismatched returns
`FAIL` with exit 2. A complete offline verification returns `PASS` with exit 0.
There is no pytest skip or xfail path.

## Acquisition observation

A GitHub auto-generated commit tarball was observed during acquisition with
SHA-256 `3953eefd58bcfded576d6802d847fa8caf7a4bb7d5def362c8bbb887a162006e`.
It contained 42 symlink entries, was quarantined outside the accepted cache,
and is neither accepted build input nor described as stable, signed, or
attested. The accepted regular-file materialization is instead derived from and
reproducible from the verified Git objects.

## Consequences and boundaries

P1-U03 and P1-U04 can reproduce the same build-source bytes offline using this
schema-7 policy and the same verified cache. Candidate bytes, caches, and
attestation bundles remain outside Git. This decision does not install or
activate 1.231, change dependencies or lockfiles, alter the active 1.227
rollback authority, access a broker or exchange, or authorize runtime,
deployment, production, or live-trading mutation.
