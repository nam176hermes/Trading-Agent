# P1-U04 immutable native-authority snapshot design

Status: integration-lead revised design for bounded repair round 2/2.

## Problem

The replacement Build A started from exact X4 receipt
`1aa60f4f2fd4a9a4fd3d8055c548b0ddda0c5c43cdd8091041415c3e00b1a4ed`.
While that build was running, host unattended-upgrade replaced the reviewed
libcurl 10.12 libraries with 10.13. The `/usr/lib/x86_64-linux-gnu` inventory
kept the same record counts but changed from
`0ae47028eb1b5818d5c1054c8a75ee40d8851634f77196308d157f3366387b11`
to `3ceeb7a55c5de77fd9b544245600b350e9875505304a61cfe2735615ad46c178`.
Production phase-B replay correctly failed closed. The sealed Build A cannot be
reused and Build B is forbidden.

The old package bytes are absent from the local package cache. Network access,
package fallback, and host downgrade are prohibited. Re-signing the mutable
live `/usr` tree would leave the same race in place.

## Decision

Materialize one private external snapshot from the currently installed real
host native authority, verify the source inventory before and after copying,
seal the snapshot, and bind candidate policy plus X4 receipts to its exact
receipt and tree digests. Build A and Build B mount snapshot sources at the
existing namespace destinations. They do not mount the corresponding mutable
host paths.

The fixed external root is:

`/home/thenam176/.cache/trading-agent/nautilus-v1.231-native-authority-3ceeb7a55c5d`

It contains only the reviewed CPython executable/stdlib, native include and
library directories, GCC support directories, and exact binutils paths already
admitted by U03. No package is downloaded or installed. The receipt records the
real-host source identities, the sealed snapshot identities, and the source to
namespace-destination mapping. Installed libcurl package versions are diagnostic
only and never authority.

## Security and reproducibility invariants

- Source inventories are read twice around copy; any drift aborts and publishes
  nothing.
- Staging is a fresh private sibling and publication is atomic to an absent
  fixed destination.
- The threat model is `COOPERATIVE_HOST`. Hostile same-UID, root, ptrace,
  kernel, and physical-storage mutation is out of scope. Within that model,
  copying and verification are descriptor-rooted and no-follow; every source
  entry has stable pre/copy/post identity; publication uses a verified parent
  and no-replace rename; and Bubblewrap receives a verified open snapshot-root
  FD rather than re-resolving an untrusted pathname.
- Snapshot root/directories are task-owned mode `0500`; regular data files are
  task-owned mode `0400`, executable files mode `0500`, and all regular files
  are single-link. The source receipt separately preserves root-UID and
  policy-bound source GID/modes, including legitimate nonzero source GIDs.
  No special or writable nonsymlink entry is accepted.
- A complete real-host scan of all 14 mappings finds exactly three external
  symlinks. They are preserved as reviewed dead links:
  `/usr/lib/python3.12/sitecustomize.py` to
  `/etc/python3.12/sitecustomize.py`,
  `/usr/lib/x86_64-linux-gnu/libblas.so.3` to
  `/etc/alternatives/libblas.so.3-x86_64-linux-gnu`, and
  `/usr/lib/x86_64-linux-gnu/liblapack.so.3` to
  `/etc/alternatives/liblapack.so.3-x86_64-linux-gnu`. Bubblewrap mounts no
  `/etc`; a host/native policy probe must prove all three paths are symlinks but
  all three targets remain unavailable in the empty-root namespace. Every other
  escape, loop, broken link, or unapproved cross-map target is rejected.
- Publication requires canonical three-way equality over every mapped entry:
  `source_before == destination-projected snapshot == source_after`. Equality
  covers path, type, mode class, symlink target, regular-file size and SHA-256;
  the projection accounts only for deliberate sealed-mode normalization. A
  self-consistent snapshot that differs from either source inventory is invalid.
- The verifier rejects extra, missing, special, multiply linked, writable, or
  identity-drifted entries and rejects receipt or policy digest drift.
- Bubblewrap keeps `--unshare-all`, `--tmpfs /`, `--clearenv`, verified-FD source
  handoff, and empty ambient environment. Only mount sources change; namespace
  destinations and build commands do not.
- Host/native qualification uses the real sealed snapshot. Portable tests may
  use synthetic fixtures but never claim host authority.
- Authority is a one-way, non-self-referential chain. A canonical snapshot
  receipt hashes payload-only bytes and the exact mappings; the receipt file is
  outside the payload tree and excluded from its digest. Committed candidate
  policy binds the receipt SHA-256, payload-tree digest, fixed root, and exact
  namespace destinations. X4 then binds exact HEAD/tree and policy, and Build
  A/B bind X4. An existing root or the `3ceeb7a55c5d` locator suffix is never
  accepted without this chain.
- Build A and Build B still require separate processes, physical stages, source
  inodes, writable trees, and exact raw/native equality.
- Active/rollback 1.227 remains untouched. Candidate 1.231 remains
  `CANDIDATE_ONLY_NOT_ACTIVATED`.

## Execution and circuit breaker

Round 1 uses TDD to add the snapshot materializer/verifier and mapped mounts.
The RED set includes a destination-byte mismatch while source-before and
source-after inventories match. It then materializes and seals the real
snapshot, regenerates exact candidate policy, and reruns the complete governed
X3 acceptance: focused/full U04 tests, pin-inventory and governance
reconciliation, `make ci-portable NONINTERACTIVE=1`, and fresh exact-byte spec
plus security/replay reviews. Only accepted X3 bytes may enter host preflight.
Fresh host-authority reviews must PASS before the stale Build A is removed
recoverably and X4 is re-preflighted.

Round 1 stopped before real materialization when host discovery found the two
BLAS/LAPACK alternatives links omitted by the original exact-dead-link rule.
Round 2 is limited to the complete three-link exception set and sandbox probes
above. It may not add another exception, change package versions, fetch bytes,
weaken equality, or fall back to live `/usr`.

If round 2 fails, stop with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.
