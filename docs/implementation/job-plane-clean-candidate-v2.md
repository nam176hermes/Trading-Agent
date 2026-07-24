# Job Plane Clean Candidate v2

**Evidence date:** 2026-07-16
**Status:** `NOT CREATED — SOURCE REVIEW AND RELEASE STOP CONDITIONS OPEN`

## Source identity

The isolated linked worktree remains:

```text
path:   /home/thenam176/projects/trading-agent-worktrees/job-plane-recovery-candidate
branch: codex/job-plane-recovery-candidate
HEAD:   e7141221423cc8d4fb3acfd757275e6d9eb69140
tree:   b81625a58f307b7ae5503f6d56f87e21d5f1776b
```

No new candidate commit was created. Final status is 29 modified and 6
untracked paths, with zero staged paths. `git diff --check` passes. Ignored
`node_modules` and `.next` validation output are not release inputs.

## Completed dependency repair

The exact direct dev dependency `@redocly/ajv@8.11.2` was added through npm to
the canonical dashboard manifest and lockfile. The only new lock nodes are the
package, nested `json-schema-traverse@1.0.0`, `require-from-string@2.0.2`, and
`uri-js-replace@1.0.1`; no existing package version changed.

The prior three contract failures were reproduced RED and then passed GREEN.
`npm ci --ignore-scripts` left the manifest and lock hashes unchanged. An
independent dependency diff review found no Critical or Important issue.

## Fresh source test evidence before final review

| Gate | Result |
|---|---|
| Three AJV cross-language tests | RED `3 failed`, GREEN `3 passed` |
| Jobs plus Alembic | `783 passed`, one Starlette deprecation warning |
| Runtime-release source suite | `237 passed, 1 skipped` |
| Dashboard Node/security suite | `140 passed`, integration PASS |
| Dashboard TypeScript | PASS |
| Dashboard lint | PASS |
| Dashboard production build | PASS |
| Candidate diff check | PASS |

These checks ran against uncommitted source. The requested clean-commit rerun,
`make check-contracts`, backend offline suite, isolated 43-check harness, and
Phase 1 safety target were not reached after the later stop conditions.

## Independent candidate review blockers

The complete 35-path diff review found no secret, runtime data, local config,
cache, generated build output, or unrelated user path. Migration `0005`
remains unchanged at SHA-256
`7b77d9abe0b5cfe84bf69ea60e47441179c99bcb533a6776f629cab103698f4e`.

It nevertheless found two Important defects in the uncommitted `0006` source:

1. Pre/postflight checks selected ACL/policy names and trigger presence rather
   than exhaustively attesting the frozen policy definitions, table/column
   privileges, exact trigger configuration/count, and protected trigger-
   function bodies.
2. Existing event validation accepted a malformed first event and broken
   `from_state -> prior to_state` chains when sequence numbers were contiguous
   and the latest target matched the job.

The review therefore held all 32 interdependent Job Plane paths as one atomic
set. A test-first repair was dispatched but interrupted without file changes
when the independent hermetic-release review reached the user's explicit stop
condition. Migration `0006` remains at SHA-256
`f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd`.

## Commit and rollback result

Final candidate commit: `NOT CREATED`.

No stage, commit, reset, clean, checkout overwrite, squash, or history rewrite
was performed in Part 2. Rollback remains limited to reverting the approved
dashboard dependency files or later candidate-only commits; original dirty
evidence and runtime state must remain untouched.
