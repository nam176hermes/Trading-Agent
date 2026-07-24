# Package B — Disposable Database Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone. Execute only Package B; never interpret disposable authority as runtime authority.

**Goal:** Prove the 0006 gaps adversarially, create forward-only 0007, verify exact catalog/event authority through restore, and move all active head pins to 0007.

**Architecture:** A technical interlock permits only commit-bound PostgreSQL 16 fixtures under `/tmp`. RED and GREEN use distinct human-reviewed records; runtime PGDATA and port 55432 remain excluded.

**Tech Stack:** PostgreSQL 16, Alembic, psycopg, pytest, canonical catalog SQL, Git.

## Global Constraints

- This file is non-authorizing until the user explicitly approves Package B and the exact disposable records.
- Requested/effective mode remains `paper/paper`; both live gates remain false; kill-switch semantics remain unchanged.
- Never access runtime PGDATA, runtime credentials, database `127.0.0.1:55432`, or Job Plane services.
- `DISPOSABLE_PG_RED` and `DISPOSABLE_PG_GREEN` are separate, commit-bound, short-lived approvals.
- Disposable clusters must use `/tmp/phase4-postgres-*`, loopback, an OS-assigned port excluding 3002/8401/55432, and guaranteed cleanup.
- Do not rewrite 0005 or frozen 0006. Schema/security repair is forward-only 0007.
- Do not synthesize or repair event history; malformed history stops migration.
- Do not enqueue jobs, execute SNAPSHOT, or call any external provider/broker/exchange.
- Do not print role-setting values, passwords, DSNs, password verifiers, or secret-bearing catalog fields.
- Stop on unequal catalog captures, surviving disposable processes/listeners, authority mismatch, or any runtime-path access.

## Package Authority and Exit Gate

- **Entry:** clean Package A commit with exact 0005/0006 hashes.
- **Produces:** reviewed RED/derived catalog evidence, frozen 0007, GREEN/restore evidence, and one active Alembic head at 0007.
- **Exit:** all catalog/event corruptions reject, two actual 0007 captures agree with reviewed target, restore authority passes, and runtime remains untouched.
- **Next:** Package C requires a separate source-review instruction.

---

### Task 4: Add a technical interlock for disposable PostgreSQL

**Files:**
- Modify: `tests/jobs/_postgres.py`
- Modify: `tests/jobs/test_postgres_harness.py`
- Create: `schemas/disposable-postgres-test-approval.schema.json`
- Create: `scripts/validate_disposable_postgres_approval.py`
- Create: `tests/jobs/test_disposable_postgres_approval.py`
- Create outside Git during execution: an unexpired, human-reviewed disposable-test approval record

**Interfaces:**
- Consumes: explicit operator approval for disposable PostgreSQL only.
- Produces: a harness that cannot accidentally target runtime PGDATA or port 55432.
- Required environment: `TRADING_TEST_DISPOSABLE_APPROVAL_RECORD` and exact `TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE` (`DISPOSABLE_PG_RED` or `DISPOSABLE_PG_GREEN`) in addition to `TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES`.
- The external record binds one scope, source commit/tree, expiry, distinct operator/reviewer identities, approved test paths/operation IDs, an optional exact SQL-file hash for RED catalog derivation, `/tmp/phase4-postgres-*`, loopback-only bind, forbidden ports 3002/8401/55432, runtime PGDATA exclusion, and a canonical record digest. Unknown/missing fields or placeholders reject; SQL execution rejects unless the RED record names the derivation operation and exact reviewed hash.
- Every database-starting test exposes a stable operation ID. Without an approval it is an explicitly reported skip; with a record for the other scope it remains a skip and cannot call a PostgreSQL executable. Final verification runs the no-approval suite first, then the exact RED and GREEN operation lists under separate commit-bound records—never the whole suite under one scope.

- [ ] **Step 1: Write mocked RED tests**

Tests must prove no call to `initdb`, `pg_ctl`, `psql`, `pg_dump`, or `pg_restore` unless all three controls are present and the protected record validates. They must reject missing/expired/wrong-scope records, source identity drift, same reviewer/operator, unapproved test paths, PGDATA outside `/tmp/phase4-postgres-*`, non-loopback bind, ports 3002/8401/55432, runtime database settings, and missing cluster marker.

- [ ] **Step 2: Implement the narrow interlock**

The validator exposes a reusable pure function consumed by the harness; the CLI and harness must make the same decision. The harness must set `cluster_name=trading-agent-disposable-tests`, choose an OS-assigned loopback port excluding 3002, 8401, and 55432, and stop the cluster in `finally`. Environment variables are only technical interlocks; only the exact reviewed external record supplies authority.

- [ ] **Step 3: Verify without starting PostgreSQL**

```bash
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_disposable_postgres_approval.py \
  tests/jobs/test_postgres_harness.py
```

Expected: mocked/unit tests pass and no PostgreSQL process is created.

- [ ] **Step 4: Commit**

```bash
git add tests/jobs/_postgres.py tests/jobs/test_postgres_harness.py \
  schemas/disposable-postgres-test-approval.schema.json \
  scripts/validate_disposable_postgres_approval.py \
  tests/jobs/test_disposable_postgres_approval.py
git commit -m "test: require explicit disposable postgres authority"
```

**Stop condition:** no exact disposable approval, any reference to runtime PGDATA, or any listener on 55432.

---

### Task 5: Produce adversarial RED proof and freeze catalog/repair inputs

**Files:**
- Create: `packages/job_authority/__init__.py`
- Create: `packages/job_authority/verifier.py`
- Create: `scripts/verify_job_plane_authority.py`
- Create: `tests/jobs/test_job_authority_verifier.py`
- Create: `tests/jobs/test_job_authority_catalog.py`
- Create: `tests/jobs/test_job_event_chain_authority.py`
- Create: `ops/postgres/job-plane-authority/query-contract-v1.json`
- Create: `ops/postgres/job-plane-authority/acl-repair-v1.sql`
- Create after two identical captures: `ops/postgres/job-plane-authority/catalog-0006-v1.snapshot`
- Create after review: `ops/postgres/job-plane-authority/catalog-0006-v1.manifest.json`
- Create after two identical rolled-back derivations: `ops/postgres/job-plane-authority/catalog-0007-v1.snapshot`
- Create after review: `ops/postgres/job-plane-authority/catalog-0007-v1.manifest.json`
- Create: `docs/implementation/job-plane-0006-exhaustive-audit.md`
- Create: `docs/implementation/job-plane-event-chain-verification.md`

**Interfaces:**
- `load_frozen_contract(path: Path) -> FrozenAuthorityContract`
- `load_migration_literals(path: Path) -> FrozenAuthorityContract`
- `capture_catalog(connection, contract) -> CatalogEvidence`
- `find_event_chain_violations(connection, contract) -> tuple[Violation, ...]`
- `verify_authority(connection, contract_path, migration_path) -> AuthorityEvidence`

The immutable records have these exact fields:

```python
@dataclass(frozen=True, slots=True)
class FrozenAuthorityContract:
    catalog_query_id: str
    catalog_sql: str
    event_chain_query_id: str
    event_chain_sql: str

@dataclass(frozen=True, slots=True)
class CatalogEvidence:
    query_id: str
    sha256: str
    row_count: int
    canonical_bytes: bytes

@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    job_id: str
    event_id: str | None
    sequence: int | None

@dataclass(frozen=True, slots=True)
class AuthorityEvidence:
    head: str
    catalog: CatalogEvidence
    event_chain_query_id: str
    violations: tuple[Violation, ...]
```

`query-contract-v1.json` is the non-self-referential source of the reviewed query bytes. `acl-repair-v1.sql` contains exactly one fixed statement and no psql metacommand or dynamic SQL. The verifier reads 0007 literal constants through AST parsing, requires them to equal those frozen inputs byte-for-byte, and must not import or execute migration code. Migration hashes are recorded later in an external authority manifest; 0007 never contains its own SHA-256. The migration and Python verifier execute the same canonical serialization SQL bytes and compute SHA-256 over the same UTF-8, C-sorted, newline-terminated stream. Catalog bytes include the `alembic_version` relation definition but exclude its row value; exact head is queried and checked separately before/after catalog verification.

Catalog scope is the target database plus exactly `trading_owner`, `trading_migrator`, `trading_reader`, `trading_jobs`, `trading_job_api`, `trading_job_worker`, and `trading_job_scheduler`; include every membership edge touching a named role and global/schema default ACLs owned by `trading_owner`. Role settings use an exact reviewed safe-key policy: serialize only approved non-secret settings such as the fixed `search_path`; if any other key exists, report only its key name and fail without returning or hashing its value. Exclude unrelated ambient roles, OIDs, timestamps, physical filenames, password/verifier fields, and secret values. Include exact schemas, objects, columns, constraints, indexes, sequences, functions, triggers, policies/RLS, raw ACL/default ACL, owners, named-role attributes, and memberships.

- [ ] **Step 1: Write the frozen query contract and catalog drift tests before 0007 exists**

The query contract contains only query IDs and exact SQL; it contains no expected migration or catalog digest. Freeze `acl-repair-v1.sql` with only `ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;`. Cover extra event-suppressing trigger, disabled/altered trigger, changed function body/search path/result, overload, PUBLIC EXECUTE, wrong grantee/grant option, policy drift, RLS drift, extra relation/sequence/column ACL/default ACL, wrong owner, role membership, and role attribute drift. AST tests use a temporary synthetic migration until final 0007 exists.

- [ ] **Step 2: Write the complete event-chain matrix**

The query must report stable codes for no history, sequence start/gap/duplicate, bootstrap not `NULL -> QUEUED` or carrying an attempt ID, later NULL or disconnected `from_state`, an edge outside the 12 ordinary plus two retry edges in `packages/job_contracts/transitions.py`, final-state mismatch, and cross-job attempt. A retry edge is valid only when it immediately follows the terminal event for the same attempt, its referenced `attempt_number` is below `jobs.max_attempts`, and metadata matches one exact authority: `PROCESS_RETRY_SCHEDULED`/`WORKER` or `LEASE_EXPIRED_RETRY_SCHEDULED`/`RECOVERY`. It begins a new retry epoch. Report wrong actor/reason, changed/null attempt, forged or over-budget retry, an event after terminal without that retry, and duplicate terminal transition within one epoch. A later terminal result after a valid retry cycle is legal.

Add explicit negative fixtures for wrong actor, wrong reason, different/null attempt, retry after max attempts, retry not adjacent to its terminal event, and bootstrap with an attempt ID; add positive worker and recovery retry cycles followed by a second valid terminal event.

- [ ] **Step 3: Run pure verifier/unit tests without PostgreSQL**

```bash
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_verifier.py
```

- [ ] **Step 4: Commit the exact RED probe before requesting authority**

```bash
git add packages/job_authority/__init__.py \
  packages/job_authority/verifier.py \
  scripts/verify_job_plane_authority.py \
  tests/jobs/test_job_authority_verifier.py \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  ops/postgres/job-plane-authority/query-contract-v1.json \
  ops/postgres/job-plane-authority/acl-repair-v1.sql
git commit -m "test(db): add exact job authority verifier and RED probes"
```

- [ ] **Step 5: Validate and run under a commit-bound `DISPOSABLE_PG_RED` record**

```bash
ACL_REPAIR_SHA256="$(sha256sum \
  ops/postgres/job-plane-authority/acl-repair-v1.sql | awk '{print $1}')"
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_RED \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-tree "$(git rev-parse HEAD^{tree})" \
  --expected-sql-sha256 "$ACL_REPAIR_SHA256"
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_RED \
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py
```

Expected: the test command exits zero because it proves and classifies the intended 0006 authority gaps; no test is made green by weakening an assertion or bypassing the harness.

- [ ] **Step 6: Capture exact pre/post catalogs and commit reviewed RED evidence**

Build two fresh disposable databases from the committed source, capture both 0006 snapshots with the frozen query contract, and require byte equality. In each fixture, execute only the hash-bound `acl-repair-v1.sql` inside an explicit transaction, capture the candidate post-repair catalog, and roll back; require both post-repair captures to be byte-equal and prove the transaction left no change. Review the query/SQL surface and secret scan before accepting either snapshot and its non-null SHA-256 manifest. The deterministic audit documents record exact query IDs, hashes, violation codes, test totals, rollback proof, and the fact that runtime PostgreSQL was not touched.

```bash
git add ops/postgres/job-plane-authority/catalog-0006-v1.snapshot \
  ops/postgres/job-plane-authority/catalog-0006-v1.manifest.json \
  ops/postgres/job-plane-authority/catalog-0007-v1.snapshot \
  ops/postgres/job-plane-authority/catalog-0007-v1.manifest.json \
  docs/implementation/job-plane-0006-exhaustive-audit.md \
  docs/implementation/job-plane-event-chain-verification.md
git commit -m "docs(db): freeze reviewed 0006 authority evidence"
```

**Stop condition:** unequal captures, unstable fields, secret-bearing output, a surviving disposable process, or any runtime listener/PGDATA access.

---

### Task 6: Implement forward-only 0007 and prove restore authority

**Files:**
- Create: `alembic/versions/0007_job_event_chain_authority.py`
- Verify unchanged: `ops/postgres/job-plane-authority/catalog-0007-v1.snapshot`
- Verify unchanged: `ops/postgres/job-plane-authority/catalog-0007-v1.manifest.json`
- Create only after 0007 is final: `ops/postgres/job-plane-authority/authority-manifest-v1.json`
- Modify: `packages/job_contracts/transitions.py`
- Modify: `packages/job_contracts/__init__.py`
- Modify: `tests/jobs/test_job_transition_authority.py`
- Modify: `tests/jobs/test_job_transition_restore.py`
- Modify: `tests/jobs/test_job_role_permissions.py`
- Update: `docs/adr/ADR-job-transition-database-authority-v2.md`
- Create: `docs/implementation/job-plane-0007-disposable-evidence.md`

**Interfaces:**
- 0007 literal constants: `CATALOG_QUERY_ID`, `CATALOG_SNAPSHOT_SQL`, `EVENT_CHAIN_QUERY_ID`, `EVENT_CHAIN_VIOLATIONS_SQL`, and reviewed pre/post catalog digests.
- 0007 retains all eight fixed transition functions from 0006; it adds no generic mutation API.
- `authority-manifest-v1.json` binds the hashes of frozen 0006, final 0007, the query contract, `acl-repair-v1.sql`, both catalog snapshots, and both catalog manifests. No migration contains or attempts to predict its own hash.

- [ ] **Step 1: Implement the smallest forward repair**

Upgrade must require owner/session `trading_owner`, PostgreSQL 16, exact 0006 head, zero runtime-role sessions, and writer-blocking locks on jobs/attempts/events. It must require the reviewed 0006 catalog digest and zero event-chain violations, then execute only:

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner
REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
```

It must require the reviewed 0007 catalog digest, exact runtime-role DML denial, and exact existing fixed-function EXECUTE grants. `downgrade()` always raises.

- [ ] **Step 2: Freeze the migration and external authority manifest**

0007's literal query and repair SQL bytes must equal the already committed query/repair inputs. Insert the already reviewed pre/post catalog digests, then freeze 0007. Compute the final 0007 file hash only after its bytes stop changing and write it to `authority-manifest-v1.json`, never into 0007. Static tests reject any literal/input mismatch or placeholder. Commit only real 64-hex digests.

- [ ] **Step 3: Run no-PostgreSQL checks and commit the exact GREEN candidate**

```bash
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_verifier.py
git add alembic/versions/0007_job_event_chain_authority.py \
  packages/job_authority/verifier.py \
  packages/job_contracts/__init__.py \
  packages/job_contracts/transitions.py \
  ops/postgres/job-plane-authority/authority-manifest-v1.json \
  tests/jobs/test_job_authority_verifier.py \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  tests/jobs/test_job_transition_authority.py \
  tests/jobs/test_job_transition_restore.py \
  tests/jobs/test_job_role_permissions.py \
  docs/adr/ADR-job-transition-database-authority-v2.md
git commit -m "db: add forward-only job event chain authority"
```

- [ ] **Step 4: Validate and run under a commit-bound `DISPOSABLE_PG_GREEN` record**

```bash
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_GREEN \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-tree "$(git rev-parse HEAD^{tree})"
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_GREEN \
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  tests/jobs/test_job_transition_authority.py \
  tests/jobs/test_job_role_permissions.py \
  tests/jobs/test_repository_transition_capabilities.py
```

Expected: every catalog/event corruption rejects atomically; clean 0004 -> 0005 -> 0006 -> 0007 succeeds without inserting a job.

The GREEN gate must perform two independent fresh `0004 -> 0005 -> 0006 -> 0007` upgrades from the committed candidate. Capture the actual post-0007 catalog from each, require the two captures to be byte-equal, and require both to equal the rolled-back derived `catalog-0007-v1.snapshot`. Verify exact head separately as 0007. Any mismatch invalidates the authority manifest and stops; do not regenerate expected bytes from the failing run.

- [ ] **Step 5: Prove custom dump/restore without conflating global roles**

Use two wholly separate disposable clusters, each with database name `trading_agent`. The source reaches exact 0007 and creates a mode-0600 custom dump with `--create`. On the target, first create an exact 0004 baseline, run `ops/postgres/provision-job-roles.sql` there so its exact-name/head preflight succeeds, verify global attributes/memberships/settings, then drop only that disposable database while retaining the global roles. Restore the `--create` dump through the maintenance database with `pg_restore --create --exit-on-error`. Verify restored database-level ACLs separately from the independently provisioned globals, plus head, catalog digest, event-chain result, direct-DML denial, append-only denial, and counts. Never target runtime, use a differently named restore database, or use a globals dump that can carry password hashes.

- [ ] **Step 6: Commit only sanitized deterministic GREEN evidence**

```bash
git add docs/implementation/job-plane-0007-disposable-evidence.md \
  docs/implementation/job-plane-event-chain-verification.md
git commit -m "docs(db): record disposable 0007 authority evidence"
```

**Rollback:** stop and remove only disposable fixtures; revert source commit if rejected. Never downgrade a database.

---

### Task 7: Move every active head pin to 0007

**Files:**
- Modify: `apps/job_api/config.py`
- Modify: `services/job_worker/main.py`
- Modify: `services/job_store/worker_repository.py`
- Modify: `ops/systemd/job-api.env.example`
- Modify: `packages/runtime_release/v2.py`
- Modify: `ops/release-v2/build-stage.sh`
- Modify: `ops/release-v2/verify-stage.py`
- Modify: matching tests under `tests/jobs` and `tests/runtime_release`
- Create: `docs/production/runbooks/job-plane-transition-authority-rollout.md`

**Interfaces:**
- Required active chain: `0007 -> 0006 -> 0005 -> 0004`.
- Historical recovery and frozen migration references retain their original revision values.

- [ ] **Step 1: Write RED head-graph tests**

Tests must reject an API, worker, unit, or release whose expected head is 0006. Release source proof must include 0007, the query contract, both catalog snapshots/manifests, the external authority manifest, and the read-only verifier.

- [ ] **Step 2: Update active pins without global search-and-replace**

Use an ordered ancestry tuple instead of adding another fixed `great_grandparent` field. Do not modify 0006's own revision metadata, the pre-recovery 0004 runbook, or obsolete v1 provisioning into appearing v2-compatible.

The new rollout runbook must preserve by exact reference/hash the protected-stdin, first-use role procedure in `docs/production/runbooks/job-plane-role-split-rollout.md` and `ops/postgres/provision-job-roles.sql`; it stops if any job role already exists and contains no service-start, timer, enqueue, or SNAPSHOT step.

- [ ] **Step 3: Run focused tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/control_api/test_alembic_schema.py \
  tests/jobs/test_job_api.py tests/jobs/test_job_api_auth.py \
  tests/jobs/test_job_api_security.py tests/jobs/test_worker_lifecycle.py \
  tests/jobs/test_systemd_units.py tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_provisioning.py
git add apps/job_api/config.py services/job_worker/main.py \
  services/job_store/worker_repository.py ops/systemd/job-api.env.example \
  packages/runtime_release/v2.py ops/release-v2/build-stage.sh \
  ops/release-v2/verify-stage.py tests/control_api/test_alembic_schema.py \
  tests/jobs/test_job_api.py tests/jobs/test_job_api_auth.py \
  tests/jobs/test_job_api_security.py tests/jobs/test_worker_lifecycle.py \
  tests/jobs/test_systemd_units.py tests/jobs/test_job_transition_authority.py \
  tests/runtime_release/test_v2.py tests/runtime_release/test_v2_provisioning.py \
  docs/production/runbooks/job-plane-transition-authority-rollout.md
git commit -m "jobs: require verified 0007 database authority"
```

**Exit gate:** exactly one Alembic head exists and every executable path requires 0007.

---
