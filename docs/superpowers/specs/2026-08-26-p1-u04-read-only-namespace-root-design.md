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

Bubblewrap creates empty `/etc/python3.12` and `/etc/alternatives` structural
directories before the final remount; none is a mount or an authority source.
The host/native policy probe must then prove both sides of the boundary in the
real Bubblewrap namespace:

- parent creation with `exist_ok=True` succeeds only because the empty parents
  already exist, and creation of each exact target below fails with `EROFS`:
  - `/etc/python3.12/sitecustomize.py`
  - `/etc/alternatives/libblas.so.3-x86_64-linux-gnu`
  - `/etc/alternatives/liblapack.so.3-x86_64-linux-gnu`
- a fresh file can be created, read, and removed under the bound build stage.

This exact `EROFS` oracle distinguishes a read-only root from a writable empty
root where a missing parent could otherwise cause a misleading `ENOENT`. The
snapshot symlink validator admits exactly the corresponding three
path-target pairs and rejects every fourth external link. No `/etc` mount,
recursive remount, live `/usr` fallback, or additional writable mount is
allowed.

## Ordering and evidence

`--remount-ro /` is the final namespace mutation. It appears after all
structural directories, the build-stage `--bind-fd`, and every other mount, but
before `--chdir`, environment construction, and the candidate command.
Portable tests assert exact invocation ordering, exact three-link policy, and
the `EROFS` oracle. Host/native qualification runs the negative and positive
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
