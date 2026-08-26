# P1-U04 immutable native-authority snapshot design

Status: integration-lead design for bounded repair round 1/2.

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
real-host source identities, the sealed snapshot identities, the source to
namespace-destination mapping, and installed libcurl package versions.

## Security and reproducibility invariants

- Source inventories are read twice around copy; any drift aborts and publishes
  nothing.
- Staging is a fresh private sibling and publication is atomic to an absent
  fixed destination.
- Snapshot directories are non-writable, regular files are single-link, and
  executable versus data modes are explicit. Symlinks are preserved and must
  remain within the admitted namespace mapping.
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

Round 2 is reserved only for a concrete binding, TOCTOU, or mount-isolation
finding against round 1. It may tighten validation but may not change package
versions, fetch bytes, weaken equality, or fall back to live `/usr`.

If round 2 fails, stop with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.
