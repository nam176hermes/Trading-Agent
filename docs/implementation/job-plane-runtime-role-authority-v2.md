# Job Plane Runtime Role and Transition Authority v2

**Evidence date:** 2026-07-16
**Status:** `RUNTIME NOT APPLIED — SOURCE RE-REVIEW IN PROGRESS`

## Runtime boundary

Migration `0005_job_plane_role_split` and forward-only
`0006_job_transition_database_authority` were not applied to the runtime
database. PostgreSQL stayed offline, and the prerequisite recovery,
authenticated pre-`0005` baseline, fresh backup, and isolated restore did not
occur.

The separate `job-plane-role-split-rollout.md` is also explicitly not execution
approval and requires its own exact approval transcript. The PostgreSQL
recovery runbook does not grant `0005` or `0006` migration authority.

## Source evidence available

- `0005` remained unmodified at SHA-256
  `7b77d9abe0b5cfe84bf69ea60e47441179c99bcb533a6776f629cab103698f4e`.
- The initial `0006` candidate SHA-256 was
  `f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd`.
- Before independent full-diff review, disposable authority/role/NULL/restore
  verification passed 99 tests, repository groups passed 39 and 8 tests, and
  worker integration passed 13 tests.
- After the dashboard dependency repair, the full jobs/Alembic suite passed
  `783 tests` with one pre-existing deprecation warning.

## Independent review hold

The source candidate is currently held for two Important migration-provenance
findings before it may be committed or represented as runtime authority:

1. `0006` pre/postflight must attest the exhaustive frozen `0005` ACL, policy,
   trigger shape/configuration, and protected trigger-function body surface,
   not selected names/privileges only.
2. Existing event history validation must reject a malformed first event and
   every broken `from_state -> previous to_state` chain, not merely check
   contiguous sequence numbers and the latest target.

The fix must be test-first, disposable-only, independently re-reviewed, and
must produce a new `0006` hash. Until then, even source authority is held; no
runtime ACL/role/function matrix is claimed.

## Runtime result

Runtime head before/after, role existence, grants, fixed function owner/ACL/
signature/return type, direct-DML denials, event atomicity, DDL denial, and
unchanged table counts are all `NOT VERIFIED`. No SQL permission probe or job
row was executed against runtime.
