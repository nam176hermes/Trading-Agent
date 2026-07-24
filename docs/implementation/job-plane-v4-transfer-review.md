# Job Plane v4 approval-tooling transfer review

## Scope and authority

Package A Task 1 authorized a source-only transfer from the dirty candidate at
`/home/thenam176/projects/trading-agent-worktrees/job-plane-recovery-candidate`
onto approved base `e7141221423cc8d4fb3acfd757275e6d9eb69140`. The isolated
destination is
`/home/thenam176/projects/trading-agent-worktrees/job-plane-authority-v4` on
branch `codex/job-plane-authority-v4`.

This transfer did not authorize PostgreSQL, services, schedulers, listeners,
jobs, providers, exchanges, brokers, order endpoints, runtime mutation, or
secret inspection. It transferred only permanently non-authorizing recovery
approval preparation tooling, its focused tests and review, and the reviewed
master/package plans.

## Worktree creation evidence

The destination was created with:

```text
git worktree add -b codex/job-plane-authority-v4 \
  /home/thenam176/projects/trading-agent-worktrees/job-plane-authority-v4 \
  e7141221423cc8d4fb3acfd757275e6d9eb69140
```

Immediately after creation, the destination branch was
`codex/job-plane-authority-v4`, `HEAD` was
`e7141221423cc8d4fb3acfd757275e6d9eb69140`, the NUL-delimited status count was
zero, and the raw status SHA-256 was
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

## Frozen source inventory and preservation

The source inventory used `git status --porcelain=v1 -z --untracked-files=all`.
Before transfer and again after all reviewed paths were applied, it recorded:

| Evidence | Before | After |
| --- | --- | --- |
| branch | `codex/job-plane-recovery-candidate` | `codex/job-plane-recovery-candidate` |
| `HEAD` | `e7141221423cc8d4fb3acfd757275e6d9eb69140` | `e7141221423cc8d4fb3acfd757275e6d9eb69140` |
| NUL status records | `65` | `65` |
| raw status SHA-256 | `8279f841581b92d75e0e7c34cb6622fa6c873fd53ae473f85416212471a817ba` | `8279f841581b92d75e0e7c34cb6622fa6c873fd53ae473f85416212471a817ba` |
| reviewed inventory SHA-256 | `f871125edd3e550390cc9b67318fedd9d4e90f62856fa1d61cb3995db6c745fb` | `f871125edd3e550390cc9b67318fedd9d4e90f62856fa1d61cb3995db6c745fb` |

The reviewed inventory digest binds each status, path, and recorded source
digest. All non-secret-risk source digests were rechecked after transfer. The
single secret-risk path, `ops/systemd/job-api.env.example`, was never content
read or hashed. Its before/after non-content metadata remained inode `94443`,
size `578`, mode `0644`, and mtime/ctime
`2026-07-16 13:05:27.902876975 -0400`.

## Manifest decisions

`job-plane-v4-transfer-manifest.csv` freezes all 65 dirty-tree records with the
required path, status, classification, digest-or-`NOT_READ`, destination,
owning task, inclusion flag, and reviewer decision fields.

| Classification | Count | Decision |
| --- | ---: | --- |
| `APPROVAL_TOOLING_OR_PLAN` | 18 | approved for Task 1 inclusion |
| `PACKAGE_A_TASK_2_INPUT` | 4 | `INCLUDE_IN_CANDIDATE`, deferred to Task 2 |
| `PACKAGE_A_TASK_3_INPUT` | 29 | `INCLUDE_IN_CANDIDATE`, deferred to Task 3 |
| `UNRELATED_USER_CHANGE` | 13 | excluded |
| `SECRET_RISK` | 1 | excluded and `NOT_READ` |
| `UNKNOWN_REQUIRES_REVIEW` | 0 | none |

No runtime, nondeterministic, local, credential, database, report, log, cache,
or other mutable artifact was transferred by Task 1. The candidate had no
separately classified runtime/nondeterministic/local path. The `include` value
`deferred` means the path is reviewed for eventual candidate inclusion but was
not transferred by Task 1; `owning_task` controls when it may be applied. All 33
deferred paths have their eventual same-path destination and the exact reviewer
decision `INCLUDE_IN_CANDIDATE`.

The user explicitly ruled that `ops/systemd/job-api.env.example` is excluded
from Package A even though the original Task 3 add list named it. It remains
`SECRET_RISK`, its digest remains `NOT_READ`, its destination remains
`NOT_TRANSFERRED`, its inclusion flag remains `no`, and it was not content
read. That ruling does not authorize Task 2 or Task 3 execution.

## Inclusion and non-authorization review

The 18 approved paths are exactly the schema, example record, DATA-001
preparation draft, validator, focused test, preparation review, master plan,
and the eleven package-plan files named in the manifest. Each was reconstructed
with a separate path-scoped `apply_patch` operation. Every resulting file has
mode `0644`, and every destination SHA-256 matches the frozen source SHA-256 in
the manifest.

Both JSON-compatible YAML preparations are identical except for `record_id`.
They retain:

- `authorization_state: DRAFT_NOT_AUTHORIZED`;
- exactly 50 ordered transcript fields;
- `execution_mode: PAPER_ONLY`;
- `live_execution_approved: false` and `live_trading_approved: false`;
- `execution_status: NOT_EXECUTED`; and
- `RECOVERY APPROVAL STATUS: DRAFT — NOT AUTHORIZED`.

All human identities, attestations, decisions, approval-window values,
integrity bindings, current revalidation, and current recovery outcomes remain
`REQUIRES_REVIEWER_INPUT`. Schema-only validation prints `NON-AUTHORIZING` for
both records. Default validation rejects both with
`YAML_PREPARATION_ONLY`; there is no renderer or command that creates an
executable approval record.

## Focused validation

The only pytest suite run was:

```text
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q -p no:cacheprovider \
  tests/production/test_postgres_recovery_approval.py
```

Result: `144 passed in 15.88s`. The tests use disposable temporary directories,
Git repositories, and evidence files only. No PostgreSQL process, service,
scheduler, listener, job, provider, exchange, broker, or order endpoint was
started or called.

## Reviewer decision

The transfer is accepted for Package A Task 1 only. It remains preparation
material and planning evidence, not current runtime evidence, human approval,
release authority, recovery authority, or trading authority. The deferred Task
2 and Task 3 manifest decisions repair future candidate eligibility only; they
do not transfer those paths or begin either task.
