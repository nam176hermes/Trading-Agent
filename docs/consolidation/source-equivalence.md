# Canonical Source Equivalence Evidence

Task 8 was captured read-only from `2026-07-14T00:12:46-04:00` through
`2026-07-14T00:30:35-04:00`. The source-equivalence result is **PASS**. The
final clean-release decision remains Task 9; this record does not authorize a
release, installation, service change, production cutover, or live trading.

Evidence below records commands, timestamps, exit status, high-level results,
and immutable identities only. It contains no credential, token, DSN, process
environment, protected configuration value, database row, or source file
content.

## Immutable identities

| Evidence | Identity |
|---|---|
| Canonical base HEAD used for Task 8 | `c586185ecffd62bc02f296e5785abe6a0b3b9a1d` |
| Canonical base tree | `08fd2473165b446a53be4cb57b2522664dad0b5e` |
| Source-authority file SHA-256 | `28f83e50619a3b8526d7ad076f7c27a92dd79acd30c9fc26d011cb01ab5596e3` |
| Backend source commit / tree | `41f055b48033714c660f44cc20498b7545366e75` / `b15af11d8600e042e20403dba982a3c1bc1b4b60` |
| Backend introduction commit | `3cca4c0c68bc8f04edd711aedffd91ed9f2602f8` |
| Backend manifest file / aggregate SHA-256 | `78d52c9c95a779f0a3d5c31cf8781b92a33e7d22f9b0ccba4d205d42c5d191b9` / `77ae54891dec68d56c58ac3597d47f8a5ef4bfbdbbe9527eff83a3d6730fafd3` |
| Dashboard source commit / subtree | `ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb` / `3246350253575256b0566cfd54076e8e8ce0412e` |
| Dashboard introduction commit | `0d5f81c172e11542b14d8cd3d0af2aba32bc1091` |
| Dashboard manifest file / aggregate SHA-256 | `6384f6e7ef4805e3384bd8e4103b54cf4490baea1e681548ac327f9d2bb4d393` / `e4ddfc0bf5ba552842ecb3a199564716eb482683ea27cd3289d9d689d1bf2584` |
| Sealed Phase 4B metadata SHA-256 | `f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c` |

The backend manifest contains 131 explicit entries and the dashboard manifest
contains 210. Each sentinel has exactly one add commit. Independent revision
verification reproduced both approved snapshots at the introduction commits.
Current HEAD has no nested `.git`, tracked symlink, or gitlink entry.

## Read-only verification log

| Timestamp | Command group | Exit | High-level result |
|---|---|---:|---|
| `2026-07-14T00:22:02-04:00` | SHA-256 and aggregate/entry-count calculation for authority and both manifests | 0 | Identities in the table above reproduced |
| `2026-07-14T00:22:14-04:00` | Branch, HEAD, tree/subtree, path-delta, and porcelain comparison for all old sources | 0 | Task 0 source checkpoint reproduced |
| `2026-07-14T00:27:34-04:00` | Mode file, kill-switch presence, exactly two named live approvals, and SQLite counts through read-only mode | 0 | `paper`, `false/false`, inactive, `30/0` |
| `2026-07-14T00:29:55-04:00` | Installed Phase 4B verifier against the sealed stage and metadata digest | 0 | PASS |
| `2026-07-14T00:30:21-04:00` | Unique introduction lookup, both independent revision verifiers, nested-Git scan, and index mode scan | 0 | Both snapshots and current Git shape PASS |
| `2026-07-14T00:30:35-04:00` | Worktree-versus-HEAD object comparison for all three lockfiles | 0 | All lockfiles unchanged |

## Complete component matrix

All commands ran from the canonical root unless the root Makefile selected the
component directory.

| Timestamp | Command | Exit | High-level result |
|---|---|---:|---|
| `2026-07-14T00:12:46-04:00` | `make audit` | 0 | PASS for the one root and all three components; status was dirty only because the intended Task 8 test edit was present |
| `2026-07-14T00:12:50-04:00` | `make check-contracts` | 0 | Generated API contracts reproduced without drift; the known generator deprecation warning remained |
| `2026-07-14T00:12:59-04:00` | `make test-core` | 0 | 668 core tests passed |
| `2026-07-14T00:13:47-04:00` | `make test-backend` | 0 | 224 backend tests passed and 2 were skipped |
| `2026-07-14T00:15:19-04:00` | `make test-dashboard` | 0 | 76 of 76 Node tests passed; isolated security integration passed |
| `2026-07-14T00:15:38-04:00` | `make typecheck-dashboard` | 0 | TypeScript check passed |
| `2026-07-14T00:15:48-04:00` | `make lint-dashboard` | 0 | ESLint passed |
| `2026-07-14T00:16:17-04:00` | `make build-dashboard` | 0 | Next.js 16.2.6 production build passed |
| `2026-07-14T00:28:12-04:00` | `uv run pytest -q tests/consolidation` | 0 | 158 consolidation tests passed |

Same-suite JUnit reruns at `2026-07-14T00:19:57-04:00` and
`2026-07-14T00:26:10-04:00` independently confirmed the executed core and
backend counts without changing shared pytest configuration.

The first dashboard-test attempt found the intentionally cleaned, ignored
dependency directory absent. `npm ci` restored the exact locked dependency
graph, after which the recorded dashboard matrix passed. The package lock and
both uv locks remained byte-identical to HEAD. Generated dependency, build,
bytecode, and test-cache artifacts were removed after validation.

## Final isolated tamper matrix

The final audit cases build disposable repositories below `/tmp`; they never
edit this canonical repository or an old source repository. Each case invokes
the real audit, requires a nonzero exit, checks the exact stable error, and
proves the canonical HEAD and porcelain state did not move.

| Isolated fault | Stable result |
|---|---|
| Missing imported backend file | `E_TAMPER` with a relative destination path |
| Extra imported backend file | `E_TAMPER` with a relative destination path |
| Modified imported backend file | `E_TAMPER` with a relative destination path |
| Changed manifest aggregate | `E_MANIFEST` |
| Authority commit/tree mismatch | `E_AUTHORITY` |
| Nested Git directory | `E_NESTED_GIT` with a relative path |
| Tracked forbidden file | `E_FORBIDDEN` with a relative path |
| Dirty release candidate | `E_DIRTY` with a relative path |
| Untrusted content or absolute source location | absent from both stdout and stderr |

The focused final matrix passed 8 tests and the complete audit module passed
63 tests.

## Old-source checkpoint comparison

Read-only comparison at `2026-07-14T00:22:14-04:00` found no drift from the
Task 0 checkpoint.

| Source | Branch | Current identity | Porcelain | Result |
|---|---|---|---:|---|
| Core application/ops | `codex/phase-4-durable-jobs` | HEAD `5a808f5bffa5faeb85ed7ad546c91c850a2f5a10`; authority tree `bfac951424d09f21359fcc11abb0bbe000456b4e` at `d9d46fa363f26bd78f5560300d26913494e11e4d` | 0 | PASS; only the two approved plan/spec documents differ after authority |
| Research backend | `codex/phase-4-research-only` | `41f055b48033714c660f44cc20498b7545366e75` / `b15af11d8600e042e20403dba982a3c1bc1b4b60` | 0 | PASS |
| Dashboard | `codex/security-phase4-hardening` | `ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb` / subtree `3246350253575256b0566cfd54076e8e8ce0412e` | 0 | PASS |

No old repository was edited, reset, cleaned, committed, or pushed.

## Phase 4B and trading-safety recapture

The installed verifier returned its expected PASS result. No provisioning
process or rejected residue was present. At `2026-07-14T00:27:34-04:00`, the
read-only safety checks observed the established requested/effective mode
`paper/paper`, both live approvals `false/false`, an inactive kill switch, and
unchanged orders/trades counts `30/0`. The mode file remained `paper`. The
active process does not publish a `TRADING_MODE` variable; its absence is
outside the Task 0 selected-variable gate, which reads exactly the two named
live approvals, so this record does not invent or require an environment
value. No environment was dumped. SQLite was opened read-only. No service was
started or restarted, and no runtime, database, systemd, scheduler, order, or
trade state was changed.

## Acceptance-criterion mapping

| Canonical design acceptance criterion | Evidence | Task 8 result |
|---|---|---|
| Installed Phase 4B independently verifies | Installed verifier recapture and sealed metadata identity above | PASS |
| Standalone single Git root | Root audit, standalone `.git`, and absence of nested Git, symlink, and gitlink entries | PASS for structure; final clean release status is intentionally retained for Task 9 |
| Old repositories unchanged | Branch, HEAD, tree/subtree, path-delta, and zero-porcelain comparison above | PASS |
| Approved component snapshots reproduce | Unique introduction commits and both independent revision verifications | PASS |
| Forbidden runtime, secret, dependency, and build paths are untracked | Root audit plus isolated forbidden-path and redaction tests | PASS |
| Core, backend, and dashboard verification passes | Complete component matrix above | PASS |
| Contracts and absolute source paths pass | Contract check, root audit, and 158-test consolidation suite | PASS |
| Paper mode, false live gates, and no-order invariant hold | Read-only Phase 4B/safety recapture above | PASS |

Task 8 therefore establishes source equivalence. It does not make the final
`GO FOR MONOREPO RELEASE AUTHORITY V2` decision; Task 9 must still prove an
empty canonical status and pass the clean release audit at the committed
candidate.
