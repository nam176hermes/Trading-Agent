# P1-U04 read-only namespace root design

Status: operator-approved bounded architecture-escalation packet, round 1/2.

## Problem

The immutable native-authority snapshot preserves exactly three reviewed dead
symlinks whose targets are under `/etc`. An empty Bubblewrap root alone is not
sufficient: `--tmpfs /` remains writable, so candidate code could create those
targets and make unreceipted bytes reachable through the preserved links.

## Decision

After all namespace directories, symlinks, read-only authority mounts, and the
writable build-stage bind have been installed, Bubblewrap receives the final
pair `--remount-ro /`. Bubblewrap documents this operation as non-recursive;
therefore the root mount becomes read-only while the separately bound build
stage remains writable.

The host/native policy probe must prove both sides of that boundary in the real
Bubblewrap namespace:

- creation of each exact target below fails:
  - `/etc/python3.12/sitecustomize.py`
  - `/etc/alternatives/libblas.so.3-x86_64-linux-gnu`
  - `/etc/alternatives/liblapack.so.3-x86_64-linux-gnu`
- a fresh file can be created, read, and removed under the bound build stage.

The snapshot symlink validator admits exactly the corresponding three
path-target pairs and rejects every fourth external link. No `/etc` mount,
recursive remount, live `/usr` fallback, or additional writable mount is
allowed.

## Ordering and evidence

`--remount-ro /` is the final namespace mutation. It appears after the
build-stage `--bind-fd` and before `--chdir`, environment construction, and the
candidate command. Portable tests assert exact invocation ordering and exact
three-link policy. Host/native qualification runs the negative and positive
write probes through the real `/usr/bin/bwrap`; synthetic authority cannot
satisfy that qualification.

No native build may start until complete X3 acceptance and fresh X3 reviews
PASS, then X4 is regenerated for the accepted exact commit/tree and fresh X4
reviews PASS. Build A and Build B remain separate processes bound to that exact
X4 receipt.

## Circuit breaker

This packet permits at most two implementation/review rounds. A finding may be
fixed only within the read-only-root, writable-stage, exact-three-link scope.
If the second review does not PASS, stop with
`P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.

All existing prohibitions remain: no network or package fallback, host
downgrade, activation, promotion, broker access, push, merge, deployment, live
trading, or U05 work. Active/rollback 1.227 remains byte-for-byte unchanged.
