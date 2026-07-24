# Job Plane Blocker Closure — Package Index and Common Gates

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

This file is the dependency/authority index. Each package file below repeats its own hard constraints and can be copied independently. Execute exactly one package per explicitly authorized session unless a later user instruction says otherwise.

**Goal:** Close the database-authority, source-provenance, hermetic-release, and recovery-approval blockers without starting Job Plane services or weakening paper-only containment.

**Architecture:** Work is split into four independently authorized planes: disposable PostgreSQL verification, clean source authority, hermetic static release, and runtime maintenance. Frozen migration 0006 remains immutable; forward-only 0007 verifies exact catalog and event history and performs only the global default-function ACL repair. Application, backend, and maintenance-only migrator become separate sealed components bound by one aggregate authority.

**Tech Stack:** Python 3.11, PostgreSQL 16, Alembic, psycopg, pytest, JSON Schema, npm, systemd, Git worktrees, python-build-standalone.

## Global Constraints

- This turn is plan-only: execute no task, start no PostgreSQL process (including disposable fixtures), and make no runtime/release change beyond writing this plan.
- Requested/effective mode remains `paper/paper`.
- `LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false` remain mandatory.
- Do not change kill-switch semantics or enable any live path.
- Do not call a provider, exchange, broker, or order endpoint.
- Do not start the runtime PostgreSQL cluster without a valid dual-reviewed 50-field recovery record.
- Do not apply 0005, 0006, or 0007 to the runtime database under recovery authority.
- Do not start Job API, worker, scheduler, or timer during blocker closure.
- Do not enqueue a job or execute SNAPSHOT.
- Keep the scheduler timer disabled; do not emit it into the release candidate.
- Never build a release from a dirty source tree.
- Never commit secrets, real approvals, environment files, dumps, caches, logs, runtime reports, or mutable evidence.
- Do not rewrite frozen migrations. Forward repair only.
- Do not use `pg_resetwal`, initialize runtime PGDATA, delete `postmaster.pid`, or overwrite recovery evidence.
- Every command that can start disposable PostgreSQL requires exact, unexpired disposable-test approval.
- Tasks that implement Release Authority v2 or perform a network-enabled input acquisition require the repository-mandated, scope-specific operator approval before execution.
- Runtime recovery, runtime migration, and Job service rollout require separate approvals.

---

## Dependency and authority map

```text
Canonical toolchain fix ─────────────────────────┐
Approval tooling commit ────────────────────────┤
Frozen 0006 baseline commit ────────────────────┤
                                                ├─> database-authority source commit
DISPOSABLE_PG_RED approval                       │
  -> 0006 adversarial RED proof                  │
  -> exact two-build 0006 catalog                │
  -> forward-only 0007                           │
  -> DISPOSABLE_PG_GREEN approval                │
  -> 0007/restore GREEN proof ───────────────────┘

database-authority source commit
  -> canonical backend-subtree review
  -> pinned CPython and wheel inputs
  -> release-tooling source verification
  -> final clean commit
  -> sealed app + backend + migrator components
  -> aggregate manifests
  -> two-build reproducibility
  -> tamper suite
  -> exact-path systemd verification
  -> reviewed candidate promotion to staging

separately approved MAINTENANCE_KIT_PROVISIONING
  -> root-owned migrator + authority + exact runbooks only
  -> no service/unit installation

dual-reviewed RUNTIME_RECOVERY
  -> recover/verify/backup/restore runtime at 0004
  -> controlled stop

separately reviewed RUNTIME_MIGRATION_BACKUP
  -> fresh backup/restore at exact approved entry state
  -> controlled stop for human review

separately reviewed RUNTIME_MIGRATION_0005_0007
  -> bind the fresh backup/restore digest
  -> 0005 -> frozen 0006 -> 0007
  -> authority verification
  -> controlled stop
```

The following approvals are deliberately non-interchangeable:

| Authority | Allows | Explicitly denies |
|---|---|---|
| `DISPOSABLE_PG_RED` | PostgreSQL 16 fixtures under `/tmp/phase4-postgres-*`; adversarial 0006 tests; exact hash-bound ACL-repair derivation inside an always-rolled-back transaction | Runtime PGDATA, port 55432, systemd, runtime credentials, any unreviewed SQL |
| `DISPOSABLE_PG_GREEN` | 0007, role-matrix, and restore tests on disposable fixtures | Runtime migration, Job services, runtime backup |
| `SOURCE_RELEASE_IMPLEMENTATION` | Tasks 9-15 source/tooling work, approved build-input acquisition, staging-only builds, and independently pinned bootstrap-interpreter/validator/materializer hashes | `/opt`, systemd installation, runtime PostgreSQL, service startup |
| `MAINTENANCE_KIT_PROVISIONING` | Future root-owned installation of only the sealed migrator (including exact runbooks/launchers) and authority | App/backend activation, unit installation, daemon reload, service startup, database access |
| `RUNTIME_RECOVERY` | Exact unchanged recovery runbook under the external 50-field approval record | 0005-0007, Job services, enqueue |
| `RUNTIME_MIGRATION_BACKUP` | Fresh custom dump and isolated restore at one exact entry state, then stop | Role/schema mutation, recovery retry, service rollout, enqueue, timer |
| `RUNTIME_MIGRATION_0005_0007` | Maintenance-only apply and verify after human review binds that fresh backup | Backup creation under the same approval, recovery retry, service rollout, enqueue, timer |
| `JOB_ROLLOUT` | Future API/worker rollout only | Out of scope for this plan |

Recommended execution packages:

| Package | Tasks | Primary blocker closed | Complexity | Gate before next package |
|---|---:|---|---|---|
| A | 1-3 | Dirty source, non-authorizing approval tooling, external contract generator, frozen 0006 baseline | M | Clean scoped commits; no PostgreSQL |
| B | 4-7 | Disposable authority, exhaustive catalog/event chain, forward-only 0007, all active head pins | XL | GREEN role/restore suite under disposable approvals |
| C | 8 | Canonical backend provenance | M | All 79 paths reviewed and subtree frozen |
| D | 9-11 | Hermetic Python/wheel closure and release tooling | XL | Offline closure, fixture reproducibility/tamper/systemd green |
| E | 12-13 | Immutable recovery source binding and separate migration authority contract | L | Both validators/runbooks green; examples remain non-authorizing |
| F | 14-15 | Exact clean commit and immutable staging candidate | L | Two final builds agree; no Git/runtime mutation |
| G | 16 | Root-owned maintenance kit | M, provisioning-bound | Exact sealed kit; no units/services installed |
| H | 17 | Human recovery authority | M, review-bound | Exact protected 50-field record; unexpired dual review |
| I | 18 | Runtime recovery/backup at 0004 | L, operational | Exact baseline and restore proof; controlled stop |
| J | 19 | Runtime backup authority, then separately signed migration to 0007 | L, operational/review-bound | Exact catalog/ACL/event authority; services still off |

Do not combine Packages G, H, I, or J into one approval or one implicit maintenance window.

## Copy-ready package files

| Package | Standalone plan |
|---|---|
| A | [Clean source baseline](01-package-a-clean-source-baseline.md) |
| B | [Disposable database authority](02-package-b-database-authority.md) |
| C | [Backend provenance](03-package-c-backend-provenance.md) |
| D | [Hermetic release tooling](04-package-d-hermetic-release-tooling.md) |
| E | [Runtime authority contracts](05-package-e-runtime-authority-contracts.md) |
| F | [Clean candidate and staging release](06-package-f-clean-candidate-release.md) |
| G | [Root-owned maintenance kit](07-package-g-maintenance-kit.md) |
| H | [Dual-reviewed recovery approval](08-package-h-recovery-approval.md) |
| I | [Runtime PostgreSQL recovery](09-package-i-runtime-recovery.md) |
| J | [Runtime backup and migration](10-package-j-runtime-migration.md) |

Copy the complete selected file, including its header and constraints. Do not copy only the task body, because the authority and stop conditions are part of the package.

## File responsibility map

- `scripts/generate_contracts.py`: canonical in-repository contract generator selection.
- `tests/jobs/_postgres.py`: sole disposable-cluster harness and technical interlock.
- `packages/job_authority/verifier.py`: read-only exact catalog and event-chain verification.
- `alembic/versions/0007_job_event_chain_authority.py`: frozen forward-only 0007 contract and ACL repair.
- `ops/postgres/job-plane-authority/`: reviewed stable catalog snapshots and manifest.
- `packages/runtime_release/backend_policy.py`: canonical backend subtree authority.
- `packages/runtime_release/hermetic_inputs.py`: strict input-record parsing and hash validation.
- `packages/runtime_release/hermetic_python.py`: safe standalone-Python materialization and relocation checks.
- `packages/runtime_release/wheelhouse.py`: offline wheel selection, hash, ABI, and license verification.
- `packages/runtime_release/release_manifests.py`: canonical component and aggregate manifest composition.
- `packages/runtime_release/source_authority.py`: protected external final-source authority validation.
- `ops/release-v2/build-component.py`: builds one sealed app, backend, or maintenance-only migrator component.
- `ops/release-v2/build-candidate.py`: offline, source-archive-only candidate orchestration.
- `ops/release-v2/compose-candidate.py`: composes authority without installing or running services.
- `ops/release-v2/verify-stage.py`: standalone fail-closed verifier.
- `docs/production/runbooks/postgresql-preserve-recover-v2.md`: recovery from sealed source/release authority, never a mutable checkout.
- `scripts/validate_job_plane_runtime_migration_approval.py`: separate one-attempt runtime migration authority.

---
