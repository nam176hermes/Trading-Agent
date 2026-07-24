# Job Plane Recovery Gate Evidence

**Evidence date:** 2026-07-16
**Scope:** read-only recovery diagnosis, source/disposable authority, and release prerequisites
**Decision:** stop before every original-cluster write

## Outcome

The current operator request does not satisfy the execution-authority format in
either reviewed database runbook. It gives task intent and permits read-only
diagnosis, dirty-tree classification, disposable database work, and a staging
release candidate. It is not the access-controlled, time-bounded, dual-reviewed
approval transcript required to recover the original PostgreSQL cluster or
apply the runtime role split.

No PostgreSQL write, original-cluster start or stop, cold copy, logical backup,
restore, role provisioning, or runtime migration was performed as part of this
authority review. Runtime `0005` was not applied. No Job API, worker, scheduler,
or timer was started, stopped, enabled, or disabled; no job was inserted and no
research child or SNAPSHOT ran.

## Source and disposable verification log

The initial reviewed source input passed `make check-contracts`, staged-diff
validation, a scoped secret-pattern scan, and `git diff --check` before commit
`e2aca4b6dd6a02ca3a8db86c9c22bcb51573e59e`. It was cherry-picked into the
isolated candidate as `e7141221423cc8d4fb3acfd757275e6d9eb69140`.

Forward transition authority was then implemented without rewriting `0005`.
The frozen source outcomes are:

| Verification | Exact outcome |
|---|---|
| `0005` working-tree comparison | unchanged; SHA-256 `7b77d9abe0b5cfe84bf69ea60e47441179c99bcb533a6776f629cab103698f4e` |
| `0006` parse/digest | Python AST PASS; SHA-256 `f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd` |
| Authority/role/NULL-fuzz/custom-restore gate | `99 passed` |
| Repository enqueue/query/transaction gate | `39 passed` |
| Cancellation/static capability gate | `8 passed` |
| Worker claim/lease integration | `13 passed` |
| Runtime-release source suite | `237 passed, 1 skipped` |
| Standalone verifier digest vs provisioner pin | exact MATCH at `43527dd2c0f0c11c722c93c0cc28e1c92637d275489ed4925d338ca5534747cd` |
| Full requested jobs/Alembic suite | `780 passed, 3 failed, 1 warning` |
| Focused rerun of the three failures | `3 failed in 1.20s` |
| Candidate `git diff --check` | PASS |
| Candidate status | 27 modified, 5 untracked; not clean |
| Disposable PostgreSQL residue | zero process and zero temporary data directory |

The three failing tests are:

```text
tests/jobs/test_contracts.py::test_public_enqueue_json_schema_reserves_scheduler_namespace
tests/jobs/test_contracts.py::test_enqueue_request_json_schema_enforces_scheduler_identity_coupling
tests/jobs/test_contracts.py::test_enqueue_request_json_schema_enforces_exact_job_payload_pairs_and_assets
```

Each fails before schema evaluation because canonical
`apps/dashboard/node_modules/@redocly/ajv/dist/2020` is absent. The canonical
package manifest/lockfile do not declare that implementation. The old test
path into the sibling legacy dashboard was removed so legacy/transitive
dependencies cannot silently satisfy canonical verification. Adding the exact
direct dev dependency requires explicit approval under `AGENTS.md`; no such
approval was available, so no package or lockfile was changed and no final
candidate commit was created.

The full dashboard suite was not rerun because no dashboard source file was
included in the forward authority diff. A local `npm ci` was used only to
reproduce dependency resolution; it changed no tracked file and its ignored
`node_modules` content is not release evidence.

## Release and systemd gate result

No immutable stage was built. A host/tooling review found no qualifying
sealed, relocatable Python 3.11 runtime: the available 3.11 base runtime is
operator-owned and the existing venvs resolve their base/stdlib into that
mutable tree. The current builder does not construct or import a digest-pinned
relocatable CPython base and selects far more than the required Job API/worker
surface. Therefore release, command, semantic, aggregate, and promotion
digests; candidate path; tamper result; and installed authority are all
`NOT CREATED`.

No v2 candidate units were rendered or installed. Exact-path systemd
verification is `NOT VERIFIED`: the renderer targets absent `/opt` paths,
distinct system users are unprovisioned, protected per-role environment files
and v2 authority/evidence paths are absent, and the requested user-manager
scope conflicts with `User=`/`Group=` system-unit separation. The tracked old
Phase 4 unit parse is not v2 evidence.

## Final read-only runtime boundary

The final metadata check observed:

| Item | State |
|---|---|
| Legacy trading agent | active, PID 333, zero recorded restarts |
| Requested/effective mode | `PAPER / PAPER` |
| Live gates | both present and normalized false; raw environment values were not printed |
| Canonical kill switch | `INACTIVE` at the runtime-selected sentinel path; path/value not printed |
| Job API | inactive/dead, disabled, PID 0 |
| Job worker | inactive/dead, disabled, PID 0 |
| Job scheduler service | inactive/dead, static, PID 0 |
| Job scheduler timer | inactive/dead and disabled |
| TCP 8401 | closed |
| TCP 55432 | closed; `pg_ctl` reports no server and `pg_isready` no response |
| Orders/trades | `30 / 0` from the session's earlier SQLite read-only evidence |

The final check did not reopen the active legacy SQLite database because its
WAL/SHM files are present and this gate forbids legacy-file writes. This avoids
even a shared-memory read-mark update; it does not weaken the earlier 30/0
evidence. The session invoked no runtime Job repository/API, so it inserted no
runtime job. Existing runtime job-table counts remain `NOT VERIFIED` while
PostgreSQL is offline.

## Reviewed authority sources

| Procedure | Observed SHA-256 | Authority rule |
|---|---|---|
| `docs/production/runbooks/postgresql-preserve-recover.md` | `feabc083b5fe35681fde63d8fbc45ae10e56b9938d575eec604718facf9aa15c` | Requires the exact dual-reviewed transcript in Section 4 before any service stop, directory creation, PostgreSQL start, or other write. |
| `docs/production/runbooks/job-plane-role-split-rollout.md` | `62027f15a5a529a4e9f98e3960582aa8e72ac2320db681d6aed74d047c16dddb` | Requires a separate exact operator/reviewer approval record before password input, backup, role mutation, migration, or rolled-back runtime permission probes. |

The recovery transcript must bind, among other fields, distinct operator and
reviewer identities, a maximum four-hour window, change and incident records,
the runbook/source commit/source tree/`0004` migration hashes, an independently
reviewed clean PostgreSQL 16 `0001` through `0004` catalog hash, original and
isolated system identities, path/link-count evidence, independent preservation
and backup destinations, and every explicit `ALLOW_*` and risk-acceptance
field. None of that exact authenticated transcript was provided in this
session.

The later role-split transcript separately binds the recovered database
evidence, exact pre-`0005` dump, installed Release Authority v2 identities,
safety and semantic evidence, migration/provisioning hashes, three distinct
protected role inputs, and the exact `ALLOW_*` fields. It was not provided, and
the current task expressly keeps the runtime database at `0004`.

## Gate matrix

| Gate | Required state | Evidence available to this review | Result |
|---|---|---|---|
| Recovery execution approval | Authenticated exact dual-reviewed transcript | No conforming transcript | **FAIL** |
| Canonical recovery source | Exact approved commit/tree and completely clean `/home/thenam176/projects/trading-agent` | Original worktree is dirty; the two reviewed runbooks were not part of the observed base commit | **FAIL** |
| Clean `0004` catalog authority | Independent disposable PostgreSQL 16 catalog and reviewed digest | Not bound by an execution transcript | **NOT AUTHORIZED** |
| Original-cluster identity | Exact system ID, PGDATA metadata/link counts, endpoint, and offline state bound by approval | May be inspected read-only; not approval-bound | **NOT AUTHORIZED** |
| Preservation/backup targets | New canonical private destinations on approved independent storage | No approval-bound destination set | **NOT AUTHORIZED** |
| Runtime database baseline | PostgreSQL 16, loopback `127.0.0.1:55432`, `trading_agent`, exact `0004_durable_research_jobs` | Must be established only through the approved recovery procedure | **NOT VERIFIED** |
| Runtime `0005` role split | Healthy accepted `0004`, reviewed backup, installed RA v2, exact separate approval | Explicitly outside this session | **NOT AUTHORIZED** |
| Application runtime | Job API/worker/scheduler remain inactive; timer remains disabled; zero jobs | No runtime rollout was authorized or performed | **PRESERVED** |

## Clean-path dependency cycle

The recovery runbook hardcodes:

```text
REPO=/home/thenam176/projects/trading-agent
```

It then requires that exact repository to match the approved `HEAD` and tree
and have an empty `git status --porcelain --untracked-files=all` result before
any recovery execution and again before a possible `0003` to `0004` migration.
The operator also forbids resetting, cleaning, or overwriting the original dirty
worktree.

Consequently, an isolated clean worktree or a staging Release Authority v2
candidate source commit may exist and may be valid for source verification, but
it does not satisfy the frozen runbook's hardcoded canonical-path check. The
recovery sequence cannot safely begin until one of these is independently
reviewed and approved:

1. a revised, hash-bound runbook that accepts an explicit canonical clean source
   path and validates it as strictly as the current hardcoded path; or
2. a separately approved method that makes the hardcoded canonical tree clean
   without losing, overwriting, or misclassifying any user-owned change.

Changing the runbook changes its digest and invalidates any approval prepared
for the prior digest. The revised procedure and its new hash therefore require
fresh independent review before execution.

## Additional handoff gap

The recovery runbook ends by performing a controlled PostgreSQL stop and proving
the original cluster is cleanly shut down. The role-split runbook, by contrast,
requires PostgreSQL to be healthy and listening and explicitly does not start or
recover it. A later runtime `0005` operation therefore also needs a separate,
reviewed PostgreSQL start/handoff authority. Recovery evidence alone cannot be
treated as permission to leave or start the cluster running.

## Safest authorized sequence

1. Complete read-only offline diagnosis and preserve secret-free observations.
2. Classify every original-worktree change and resolve every unknown critical
   path before release preparation.
3. Produce and review a clean isolated source commit.
4. Resolve the hardcoded clean-path cycle and independently review the resulting
   recovery runbook digest.
5. Produce and independently review the clean disposable PostgreSQL 16
   `0001`-through-`0004` expected catalog evidence.
6. Bind all non-secret original/isolated identities and preservation targets in
   the exact authenticated, dual-reviewed recovery transcript.
7. Only within that transcript's time window, execute the single approved
   recovery/preservation/backup/restore procedure and its mandatory final stop.
8. Keep runtime `0005`, application services, timers, jobs, and SNAPSHOT outside
   this recovery gate.

Until those gates pass, the database recovery and runtime role split remain
**NO-GO**.
