# P1-U04 candidate build evidence

Status: `U04_ACCEPTED_G1_INACTIVE`
Evidence date: 2026-08-27

Immutable generation record: `NT1231-U04-G1`. Gate A accepted the existing
Build A/Build B and schema-7 closure after fresh P1-scoped topology,
P1-specific host, runtime-release host, specification, security/integrity, and
evidence-replay checks. The global host gate remained truthfully `DEFERRED`
only because the three separately governed PostgreSQL authorities were absent;
P1 validation forbids manufacturing those approvals or running DB/service
checks. This acceptance does not rebuild, rematerialize, activate, or promote
the candidate.

This is a post-build evidence record. The native authority, Build A, Build B,
and schema-7 closure bind the source candidate below. The later evidence-only
commit is intentionally not a build input, which avoids a circular commit/hash
dependency.

## Source and authority binding

| Item | Exact identity |
| --- | --- |
| U03 base commit / tree | `17d892a547f43aec35952d4ffa1fea0ab3d16f9a` / `edca2285e4f4309d05e73e0932740a18e7b28fa8` |
| Original U04 implementation | `ce3185a68631c7a2c179770304e236602215e978` |
| Accepted source HEAD / tree | `7aa1e69a40f1160174f9ef32c1d3ef056720e4b0` / `42ece253bcf6356a522d9fc5fd2582645eddc56c` |
| U02 provenance policy | `f921b50ab5af8a9e518fd599ebcb2425cd7d2f8130263875d46dd34c3e40af8a` |
| U02 deterministic source archive | `a141c913d9c00ef18ac78a416bddfeef85fa06ebd172d98fdd752ad2c5957441` |
| U03 toolchain inputs | `22418d3ecda1ae15044c99350a0bcb26d942b0602c408170e7c2b7ae9523f519` |
| X4 authority receipt | `fcca4a496208c454a7eb6ef122a7f867c2e8cd5497cfd0c374f28a637ff61010` |

X4 verified exact CPython 3.12.3, Rust/Cargo 1.97.1, LLVM 22.1.3,
Bubblewrap 0.9.0, offline package authority, private/disjoint roots, and the
unchanged schema-6 rollback authority. Fresh X4 spec and security reviews both
reported PASS with zero findings.

## Native Build A and Build B

Both builds used separate process trees and separate physical source stages,
with the same X4 receipt and sanitized-environment digest.

| Item | Build A | Build B |
| --- | --- | --- |
| Build receipt SHA-256 | `de627e81e7837ada16c2eb223e75cea88315c698392d82dcbc232d700cfe82a6` | `8f7e8f24a0476f65a1e0f3253cfacd83b0af6ccad4bfa68e2c689cc1011077ed` |
| Wheel SHA-256 | `ecc461d0f634c25db17e0fb79136c3bf0d513edd323d4f9adaaf84346e68b2fb` | `ecc461d0f634c25db17e0fb79136c3bf0d513edd323d4f9adaaf84346e68b2fb` |
| Wheel size | 183626605 bytes | 183626605 bytes |
| Artifact core SHA-256 | `42e5aeeed9489ea7ad01e03c8e4257a732ee321c23a912433eca3e7cbafaaac4` | `42e5aeeed9489ea7ad01e03c8e4257a732ee321c23a912433eca3e7cbafaaac4` |

Final artifact manifest SHA-256:
`24f1439ad5fc4f74dd950f892d39470c8d37f1e052add2dfb52ff20904fc50d3`.
Raw wheel equality, native inventory equality, and authoritative manifest
equality all passed. The 111 native objects all passed ELF64/x86-64, RELRO,
BIND_NOW, non-executable-stack, ABI, and dependency validation. Build A and B
used distinct PID/start-time identities and distinct source descriptor inodes.

Sanitized native commands:

```text
uv run --frozen --offline python scripts/build_nautilus_engine.py --build-candidate-a --offline --authority-receipt <X4_RECEIPT> --authority-receipt-sha256 fcca4a496208c454a7eb6ef122a7f867c2e8cd5497cfd0c374f28a637ff61010
uv run --frozen --offline python scripts/build_nautilus_engine.py --build-candidate-b --offline --authority-receipt <X4_RECEIPT> --authority-receipt-sha256 fcca4a496208c454a7eb6ef122a7f867c2e8cd5497cfd0c374f28a637ff61010
```

Both commands returned zero. No network/package fallback or ambient compiler
authority was used.

## Schema-7 candidate closure

The production candidate materializer ran with no caller-supplied authority:

```text
uv run --frozen --offline python scripts/materialize_nautilus_runtime_closure.py --materialize-candidate
```

It returned zero and produced:

- closure schema: `7`;
- activation status: `CANDIDATE_ONLY_NOT_ACTIVATED`;
- closure manifest SHA-256: `24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255`;
- canonical candidate attestation SHA-256: `11cd9d08092f2d133eaf820220b9bf2540edb747f2257b0e70c401605981ad10`;
- file inventory SHA-256: `48c6e3e6c4c7dda92c76501e63f80be0b98dfceb9a2e2609a41439fa7c69ef50`;
- native dependency inventory SHA-256: `82eb559376246753ce9d939bc5a818a42f2cb26580f98b425d427f3c35e35fd7`;
- sealed-import qualification script SHA-256: `f4cdbe7eb2b7515ddbd14aec3ba95e070bcd05bf021322c7d542e3eed14ae5c8`;
- sealed import result: exact `nautilus_trader 1.231.0`, PASS.

Post-seal attestation was repeated with identical bytes. The closure contains
88 regular files and 12 directories, no symlinks, no writable or multiply
linked regular files, and no foreign-owned entries.

## Rollback and isolation

The exact 1.227 rollback remained schema 6 with closure digest
`14d4fd990dccfdbb8b6dfe964a04ae9e80fefb30914cf433de1bc503b8ad03fa`,
closure-manifest SHA-256 `b143564cf3ad63b4ca01afb9a27e7496c9b1c6ff1f3c46cf10b6c4a047545d20`,
and artifact-manifest SHA-256 `105579383ea3c5e44104bbe162ab78380f7abb5654e15ac3b600beee54ed93d2`.

Real Bubblewrap sealed imports were executed with the candidate absent and
again after deterministic candidate rematerialization. Both returned the same
receipt `8a18580bc116317018ed2546a6f7879ceebac62db6323e1337feb202a3fc3da4`
and exact modules `nautilus_trader 1.227.0`, `numpy 2.4.4`, and `pandas 2.3.3`.
Shared writable state, shared regular-file inodes, mixed runtime process
references, active policy changes, and candidate activation all equal zero.

## Portable evidence and limitations

Portable attempt 28 at the accepted source HEAD passed:

- topology: 8059 passed;
- governance: 8854 passed, 2 governed skips, 0 failed;
- critical coverage: 1361 passed, 44 governed skips, 5 deselected, ratchets PASS;
- publication manifest SHA-256: `cf25537d84d076e11b9263c7ca3c8bddc30c761c62f6cb11212c93b9ff6059fe`;
- checksum manifest SHA-256: `cc6889356c887d9faa71416a247d34648ea0ea298edbc486d2da61840362609b`;
- source archive SHA-256: `47095dc1b8c250088c0e74e081940298da4829518b2b0247f0c0d4b5aa815205`.

Known limitation: native isolation is evaluated under the documented
cooperative-host threat model; privileged host compromise is outside scope.
There are no DEFERRED P1, X4-X8, or runtime-release authorities. The three
PostgreSQL authorities remain intentionally `ABSENT/DEFERRED` and outside P1.
This evidence does not authorize
activation or promotion of 1.231, broker access, trading, push, PR, merge,
deployment, production mutation, or a U05 qualification verdict.
