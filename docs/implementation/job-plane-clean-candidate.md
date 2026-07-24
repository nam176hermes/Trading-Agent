# Job Plane Clean Candidate Provenance

**Evidence date:** 2026-07-16
**Status:** `BLOCKED — SOURCE HARDENED, FINAL CLEAN CANDIDATE NOT CREATED`

## Purpose

This record separates reviewed Job Plane source from the preserved dirty
canonical worktree. It is source provenance only: it is not an immutable
release, promotion, database approval, or service-rollout approval.

## Original worktree boundary

The frozen dirty-tree inventory was captured from:

- path: `/home/thenam176/projects/trading-agent`;
- branch: `codex/canonical-monorepo`;
- base commit: `9641281a8508709cab212fb460308467681854ef`;
- frozen status: 70 modified and 34 untracked paths, with zero staged paths;
- inventory: 104 unique paths in
  `docs/implementation/job-plane-worktree-classification.csv`.

No reset, clean, checkout overwrite, blanket staging, or bulk copy was used.
The following nondeterministic internal reports were deliberately excluded:

- `.superpowers/sdd/task-4-report.md`;
- `.superpowers/sdd/task-5-report.md`.

No frozen path was classified as runtime data, local configuration, secret
risk, unknown, or an unrelated pre-existing user change. The two generated
contract files were regenerated and checked through `make check-contracts`.

## Reviewed selected commit

Exactly 102 reviewed paths were staged by explicit path families after diff
review. The staged name-list SHA-256 was:

`ab04a74abd088cfb50523dec33bba364b201b3f1fe7c3d404ba42a4448bc80a5`

`git diff --cached --check`, the scoped high-risk secret-pattern scan, and
`make check-contracts` passed before the commit. The resulting source commit
on `codex/canonical-monorepo` is:

`e2aca4b6dd6a02ca3a8db86c9c22bcb51573e59e`

Commit subject: `feat(job-plane): freeze contained release input`.

This commit contains reviewed A0/A1 containment/provenance work and A2/B1 Job
Plane work. It does not contain the two excluded internal reports or any of
the implementation evidence documents created after the frozen snapshot.

## Isolated worktree

A linked worktree was created outside the repository root, as required by
`AGENTS.md`:

- path:
  `/home/thenam176/projects/trading-agent-worktrees/job-plane-recovery-candidate`;
- branch: `codex/job-plane-recovery-candidate`;
- base: `9641281a8508709cab212fb460308467681854ef`;
- reviewed cherry-pick: `e7141221423cc8d4fb3acfd757275e6d9eb69140`;
- initial tree: `b81625a58f307b7ae5503f6d56f87e21d5f1776b`.

The isolated worktree was clean immediately after the reviewed cherry-pick.
It was not populated by copying the repository. It is now the only allowed
location for the forward `0006` transition-authority source work.

## Forward authority worktree freeze

The isolated worktree now contains the reviewed forward `0006` authority work,
but it is deliberately **not** a release candidate. Its frozen status is 27
modified and 5 untracked paths, with zero staged paths. Migration
`0005_job_plane_role_split.py` is unchanged from the reviewed input. The new
migration SHA-256 is:

`f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd`

The database-authority/role/restore gate passed 99 disposable PostgreSQL 16
tests, repository-focused suites passed 39 and 8 tests, and worker integration
passed 13 tests. The complete requested jobs/Alembic command then produced:

```text
780 passed, 3 failed, 1 warning
```

All three failures are in the cross-language JSON Schema checks in
`tests/jobs/test_contracts.py`. The test correctly stopped using the unrelated
legacy dashboard checkout, but the canonical `apps/dashboard` package does not
declare or install `@redocly/ajv`. A fresh focused rerun produced the same three
failures with `Cannot find module .../@redocly/ajv/dist/2020`.

Adding `@redocly/ajv@8.11.2` would change `package.json` and the lockfile and is
a dependency change requiring explicit approval under `AGENTS.md`. It was not
added. The legacy dashboard's dependency tree was not reused. Consequently the
worktree was not staged or committed, and no clean final candidate commit/tree
is claimed.

The final candidate remains blocked until all of these gates pass:

1. `0005` remains byte-for-byte frozen and `0006` is forward-only from it.
2. All runtime mutation paths use fixed database capabilities.
3. The disposable PostgreSQL 16 role matrix, event atomicity, and restore
   parity pass.
4. Required head/release references use exact `0006` ancestry.
5. The complete requested source test set passes from this worktree.
6. `git diff --check` passes and the final candidate status is clean.
7. A fresh name/diff/secret/runtime-artifact review finds no excluded input.

Until then, no final candidate commit/tree or release input is claimed. The
initial cherry-pick remains a clean reviewed input, not the completed candidate.

## Frozen forward-change path list

Modified paths:

```text
apps/job_api/config.py
docs/production/release-authority-v2.md
ops/release-v2/build-stage.sh
ops/release-v2/provision-root.sh
ops/release-v2/verify-stage.py
ops/systemd/job-api.env.example
packages/runtime_release/v2.py
services/job_store/repository.py
services/job_store/worker_repository.py
services/job_worker/main.py
tests/control_api/test_alembic_schema.py
tests/jobs/test_contracts.py
tests/jobs/test_job_api.py
tests/jobs/test_job_api_auth.py
tests/jobs/test_job_api_security.py
tests/jobs/test_job_role_permissions.py
tests/jobs/test_repository_cancel_acl.py
tests/jobs/test_repository_enqueue.py
tests/jobs/test_repository_queries.py
tests/jobs/test_repository_transactions.py
tests/jobs/test_systemd_units.py
tests/jobs/test_worker_claims.py
tests/jobs/test_worker_leases.py
tests/jobs/test_worker_lifecycle.py
tests/jobs/test_worker_recovery.py
tests/runtime_release/test_v2.py
tests/runtime_release/test_v2_provisioning.py
```

Untracked paths:

```text
alembic/versions/0006_job_transition_database_authority.py
docs/adr/ADR-job-transition-database-authority.md
tests/jobs/test_job_transition_authority.py
tests/jobs/test_job_transition_restore.py
tests/jobs/test_repository_transition_capabilities.py
```

## Rollback

Rollback is branch-scoped: discard or revert only commits created on
`codex/job-plane-recovery-candidate`. Preserve the original worktree, its two
excluded reports, all implementation evidence, runtime PGDATA, services, and
database state. No rollback command was executed in this gate.
