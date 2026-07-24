# Job Plane Release Authority v2 Candidate Evidence

**Evidence date:** 2026-07-16
**Decision:** `NO_BUILD` — no Release Authority v2 stage, authority document,
promotion document, manifest digest, or candidate path was created.

This document records a read-only prerequisite review. It is not rollout,
promotion, installation, or runtime approval.

## Clean source input observed

| Field | Observed value |
|---|---|
| Isolated worktree | `/home/thenam176/projects/trading-agent-worktrees/job-plane-recovery-candidate` |
| Candidate branch | `codex/job-plane-recovery-candidate` |
| Initial reviewed cherry-pick commit | `e7141221423cc8d4fb3acfd757275e6d9eb69140` |
| Commit tree | `b81625a58f307b7ae5503f6d56f87e21d5f1776b` |
| Status at observation | clean (`git status --short --branch` contained only the branch header) |

The commit above is only the initial clean cherry-pick input. It is not a
built release and this evidence does not approve later changes to that branch.
The original worktree remained separate and dirty; it was not reset, cleaned,
overwritten, or used as release input.

Forward `0006` work was completed in the isolated worktree but remains
uncommitted: 27 modified and 5 untracked paths. Its full jobs/Alembic gate is
`780 passed, 3 failed`; all three failures are caused by the canonical
dashboard not declaring the AJV 2020 implementation used by the cross-language
contract tests. The required dependency change was not approved and therefore
was not made. There is no final clean candidate commit from which a release
could legally be built.

## Host toolchain evidence

| Tool/runtime | Read-only observation | Gate result |
|---|---|---|
| `/usr/bin/python3.11` | absent | `BLOCKED` |
| PATH `python3.11` | UV CPython 3.11.15 beneath `/home/thenam176/.local/share/uv/python`, UID/GID `1000:1000` | rejected by root-owned interpreter gate |
| Existing Phase 4 application Python | root-owned CPython 3.11.15 | not hermetic: base prefix, base exec prefix, and stdlib resolve into the operator-owned UV tree |
| Existing Phase 4 backend Python | root-owned CPython 3.11.15 | not hermetic for the same reason |
| `/usr/bin/python3` | root-owned CPython 3.12.3 | incompatible with root project `>=3.11,<3.12` and backend `==3.11.*` |
| `/usr/bin/node` | root-owned Node.js v24.14.1 with a non-writable root-owned ancestor chain | static executable prerequisite only; dashboard is out of the requested minimal release scope |
| `/usr/bin/npm` | root-owned npm 11.11.0 with a non-writable root-owned ancestor chain | static executable prerequisite only |
| `uv` | user-owned `uv 0.11.7` | builder does not pin its version/hash or require a root-safe executable |

No interpreter inspected on this host qualifies as a reviewed hermetic Python
3.11 runtime for a sealed v2 release.

## Builder blockers

### 1. Hermetic runtime construction is structurally incomplete

`ops/release-v2/build-stage.sh` creates two ordinary environments using
`python -m venv --without-pip --copies`. Immediately afterward it requires
each environment's `sys.base_prefix`, `sys.base_exec_prefix`, and stdlib path
to be the generated stage or one of its descendants.

The builder never copies or extracts a CPython base runtime/stdlib into the
stage and accepts no digest-pinned relocatable runtime bundle. An ordinary
venv therefore retains an external base prefix and cannot satisfy the gate.
Substituting either operator-owned UV Python or the existing Phase 4 venv
would weaken, not satisfy, release containment.

Required remediation is a separately reviewed, digest-pinned relocatable
CPython 3.11 input and extraction/layout design whose interpreter, stdlib,
extension modules, and dependency paths are all sealed beneath the candidate.
It needs a successful end-to-end test, not an identity string supplied by a
fixture.

### 2. Available offline caches fail the builder's cache policy

Metadata-only scans found:

| Cache | Approximate size | Symlinks | Hard-linked regular files | Descendants with forbidden mode bits |
|---|---:|---:|---:|---:|
| `/home/thenam176/.cache/uv` | 15 GB | 1,492 | 446,870 | 406,281 |
| `/home/thenam176/.npm` | 8.8 GB | 482 | 22 | 399,781 |

`cache_manifest` rejects symlinks, regular files with link count other than
one, unsafe owner/mode entries, and special files. These conventional caches
cannot be passed directly. A private curated cache may be possible, but its
lock completeness and byte stability are `NOT VERIFIED`; no offline sync or
build was attempted in this review.

### 3. Current release selection is not least-scope

The builder archives the complete commit, moves the complete legacy backend
and dashboard trees, and treats every remaining tracked path as the
application artifact. The current source-proof schema maps every Git blob to
the stage and explicitly requires both backend and dashboard component trees.
Approximate committed scope observed during review was:

- 368 application paths after moving components, including 128 documentation
  paths, 74 test paths, and 6 scheduler-related paths;
- 133 backend paths;
- 211 dashboard paths.

This does not meet the gate that the release contain only Job API/worker code,
their required libraries, approved migrations/contracts, and the exact
SNAPSHOT backend command surface. Pruning only the shell copy step is
insufficient because the verifier would reject missing source entries. The
source-proof and authority schema need an explicit, deterministic allowlisted
artifact-selection manifest bound to the same commit/tree.

### 4. Promotion/runtime authority is deliberately unavailable

The static schema can bind a stage, command manifest, semantic policy, and unit
bytes, but the v2 activation builder/parser deliberately reject all inputs.
The runtime v2 loader and installed-application attestor also deliberately
fail closed. Consequently:

- promotion metadata was not created;
- no protected static authority or activation was published;
- no runtime manifest digest was configured;
- a static stage, even if built, would not authorize service startup.

This fail-closed behavior must remain until promotion lifecycle, rotating
safety/semantic evidence, installed-tree attestation, and rollback are
separately implemented and reviewed.

### 5. Remaining reproducibility/provenance gates

- The current builder executes a user-managed `uv` without binding its hash or
  version into the authority.
- It requires a caller-supplied prior-release SHA-256 but validates only its
  syntax during build. The documented Phase 4 metadata digest is available in
  source evidence, while its former sealed cache path is absent. The exact
  prior object must be independently identified and reverified before use.
- No `None`, placeholder, or guessed digest may be substituted for the prior,
  release, command, or semantic manifest digest.

## Test and command evidence

This review executed metadata/read-only commands only:

```text
git worktree list --porcelain
git status --short --branch
git rev-parse HEAD
git rev-parse HEAD^{tree}
command -v / realpath / stat for Python, uv, Node, npm, and systemd-analyze
isolated Python version/base-prefix/stdlib metadata probes
node --version
npm --version
uv --version
find/stat/du aggregate cache metadata scans
rg/sed/nl inspection of builder, v2 schema, tests, units, and release docs
getent metadata checks for proposed service identities
```

No release test suite was rerun by this focused review. Prior reported test
counts remain inherited evidence, not proof of a successful candidate build.
In particular, current v2 fixtures use small shell files as synthetic Python
executables and literal CPython identity strings, while builder tests exercise
rejection paths only. There is no successful real hermetic v2 build test.

Separate source work updated the expected Alembic ancestry to exact
`0006 -> 0005 -> 0004`. The current standalone verifier SHA-256 is
`43527dd2c0f0c11c722c93c0cc28e1c92637d275489ed4925d338ca5534747cd`,
and the provisioning script pins the same value. The full runtime-release test
suite for that source state reported `237 passed, 1 skipped`. These facts prove
source consistency only; because the source is uncommitted and the real
hermetic builder prerequisites above fail, they do not produce a release,
release manifest, command manifest, semantic manifest, promotion record, or
tamper-test result.

## Actions explicitly not taken

- no `build-stage.sh`, `uv sync`, dashboard build, or release dependency sync;
- one canonical-dashboard `npm ci` was used only to reproduce isolated tests;
  it changed no tracked package/lock file and its ignored dependency tree is
  excluded from release evidence;
- no candidate output, authority, promotion, manifest, or digest publication;
- no write under `/opt`, `/etc`, `/run`, or `/var/lib`;
- no database command or migration;
- no service/timer install, reload, start, stop, restart, enable, or disable;
- no job enqueue, claim, cancellation, execution, or SNAPSHOT;
- no broker, exchange, or research-provider call.

## Release prerequisite verdict

`NO_GO`: the clean branch is a valid isolated source-review input, but a v2
candidate cannot be built until the hermetic runtime design, curated offline
caches, minimal artifact-selection schema, toolchain pinning, and promotion
authority blockers above are resolved and independently verified.
