# AUTH0/R0 Pre-build Assessment

**Date:** 2026-07-20
**User direction:** "Đóng AUTHO/RO và sửa core baseline."
**Safety:** Source-only, paper-only. No runtime PostgreSQL, service, scheduler, broker, exchange, account, order, or live-gate mutation.

## Decision

Proceed by integrating the already reviewed `codex/job-plane-authority-v4` source line into a disposable clean clone first, then repair only remaining reproducible core failures. Do not rebuild Release Authority v2 from scratch and do not merge the entire dirty recovery worktree.

## Potential score

**9/10.** Closing source authority and restoring a green core gate unlocks every later trading-domain phase while reducing the largest current correctness and release risk.

## Existing-system duplication

The required functionality largely exists on `codex/job-plane-authority-v4`:

- reviewed transfer manifest and package plans;
- dashboard contract-toolchain repair;
- forward-only `0006` and `0007` source migrations;
- database authority verifier and disposable PostgreSQL tests;
- hermetic release-v2 source and tests;
- recovery approval preparation tooling.

Reimplementing these capabilities would duplicate reviewed work and discard existing evidence.

## Genuine gaps

1. `codex/canonical-monorepo` does not contain the authority-v4 commits.
2. Canonical `make test-core` is not green.
3. The authority worktree cannot pass `make audit` because a linked worktree has a `.git` file while the audit requires a standalone `.git` directory.
4. The original canonical worktree contains unrelated tracked and untracked changes and is not a safe first integration surface.
5. Runtime recovery and production migration remain separately approval-gated and are excluded from this source closure.

## Build versus patch

| Approach | Effort | Value | Decision |
|---|---:|---:|---|
| Rebuild AUTH0/R0 | High | Low | Reject |
| Merge the dirty recovery candidate | High risk | Low | Reject |
| Integrate reviewed authority-v4 commits in a clean standalone clone | Medium | High | Adopt |

## Core idea extracted

Treat source authority, runtime authority, migration approval, and live trading authority as separate capabilities. A green source branch does not authorize runtime deployment or database mutation.

## UI decision

No new UI is needed. Dashboard changes already present in the reviewed authority source remain projection and safety-state work only. The dashboard must not become an enforcement boundary.

## Scope

Included:

- freeze branch heads and tree identities;
- verify the canonical and recovery-candidate trees are equivalent;
- integrate the reviewed authority-v4 commit range in a standalone clone;
- run canonical audits and focused tests;
- reproduce and repair remaining core test failures with tests first;
- preserve D0.1 separately;
- produce exact source evidence.

Excluded:

- production PostgreSQL start, stop, restore, migration, role or ACL changes;
- systemd or scheduler changes;
- release provisioning under `/opt`, `/etc`, `/run`, or protected paths;
- broker, exchange, account, balance, position, or order calls;
- live execution or production cutover;
- remote push or branch deletion.

## Acceptance gates

1. Integration clone has a real `.git` directory and clean starting status.
2. The authority commit range applies without importing dirty recovery-worktree artifacts.
3. `make audit` passes.
4. `make check-contracts` passes.
5. Focused AUTH0/R0 tests pass.
6. `make test-core` passes, or every remaining failure is proven unrelated and recorded as a blocker.
7. Live flags remain false and no runtime mutation command is executed.
8. Final diff contains only reviewed authority work, explicit baseline fixes, D0.1, and maintained evidence.

## Rollback

All integration work begins in a disposable standalone clone on a dedicated local branch. If a gate fails, retain the original canonical worktree unchanged and discard the integration candidate only after a separate destructive-operation approval.
