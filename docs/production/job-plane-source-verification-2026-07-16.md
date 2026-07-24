# Job-plane source verification and rollout stop record

**Observed at:** `2026-07-16T15:12:29Z`

**Repository:** `/home/thenam176/projects/trading-agent`

**Branch / HEAD:** `codex/canonical-monorepo` / `9641281a8508709cab212fb460308467681854ef`

**Decision:** `NO_GO`

This record covers the source-only portion of roadmap A2/B1. It is not database,
release-provisioning, service, job, provider, or timer approval. No runtime or
configured database state was changed while producing it.

## Stop-gate result

| Gate | Observed evidence | Result |
|---|---|---|
| PostgreSQL health | `ops/postgres/verify-cluster.sh` returned `127.0.0.1:55432 - no response` with exit 2 | `UNAVAILABLE` |
| Runtime Alembic head | Cannot authenticate to an unavailable server | `NOT_VERIFIED` (neither `0004` nor `0005` is claimed) |
| Runtime role/ACL matrix | Requires the reviewed database runbook and explicit approval | `NOT_EXECUTED` |
| Source Alembic graph | Disposable PostgreSQL tests upgrade through `0005_job_plane_role_split` | `STATICALLY_VERIFIED` |
| Release Authority v2 source | Runtime-release suite passed; independent post-fix review passed | `STATICALLY_VERIFIED` |
| Immutable v2 candidate | Release audit rejected the dirty worktree; no reviewed hermetic Python or promotion lifecycle exists | `NOT_BUILT` |
| Job API, user scope | loaded, inactive/dead, disabled, PID 0, restarts 0 | `INACTIVE` |
| Worker, user scope | loaded, inactive/dead, disabled, PID 0, restarts 0 | `INACTIVE` |
| Job API/worker, system scope | not found, inactive, PID 0 | `ABSENT` |
| Port 8401 | no listening socket | `CLOSED` |
| Scheduler service, user scope | loaded, inactive/dead, static, PID 0 | `INACTIVE` |
| Scheduler timer, user scope | loaded, inactive/dead, disabled | `DISABLED` |
| Scheduler, system scope | service and timer not found | `ABSENT` |
| Exact rollout approval | Not supplied in this session | `ABSENT` |

The first failing gate is sufficient to stop. All runtime actions were skipped.

## Source changes delivered

### Job API authority

- Production composition is v2-only and obtains an opaque factory-issued
  authority before constructing the repository or starting Uvicorn.
- Enqueue and cancel recheck authority before database readiness/head and before
  the repository mutation. Authority-negative tests assert zero enqueue/cancel
  repository calls.
- Readiness is fail-closed when authority, authentication, database health, or
  exact `0005_job_plane_role_split` head is absent.
- Protected-authority exceptions are sanitized and do not disclose paths,
  credentials, DSNs, or supplied values.

### Retry-stable idempotency and namespace ownership

- The dashboard creates one 32-character lowercase hexadecimal operation ID and
  retains it across a dropped-response retry. The BFF derives one stable
  `dashboard:<action>:<operation-id>` key.
- Public/operator enqueue rejects the reserved `schedule:` namespace before any
  repository call.
- Scheduler identity accepts only the exact valid-Gregorian-minute form
  `schedule:snapshot:<YYYY-MM-DDTHH:MMZ>` coupled to actor type `SCHEDULER`, job
  type `SNAPSHOT`, and priority 0.
- Repository deduplication binds payload fingerprint, actor type, actor ID, and
  priority. An exact retry returns the original job without appending another
  event; actor or priority mismatch is a conflict.

### Database authority split

- Added forward-only migration `0005_job_plane_role_split` and exact provisioning
  SQL for distinct `trading_job_api`, `trading_job_worker`, and
  `trading_job_scheduler` LOGIN roles.
- The former shared `trading_jobs` role becomes `NOLOGIN`; runtime composition
  requires the exact role for each service and exact head `0005`.
- PUBLIC/default privileges are closed; RLS, column-level grants, append-only
  event enforcement, exact cancel enforcement, and cross-role denials are
  tested on disposable PostgreSQL 16 clusters.
- The reviewed runbook preserves pre/post backups and evidence, verifies
  identity/head/counts/integrity/ACL/RLS, and stops before services.

### Worker containment and manifest binding

- Runtime command authority is `SNAPSHOT` only with exact immutable cwd,
  interpreter, and argv: `<backend-python> -I -B main.py --mode snapshot
  --research-only`; `shell=False` and no free-form argv.
- Child environment starts empty and admits only the research allowlist plus
  paper/false/false safety values. Broker, exchange, generic live, and database
  credentials are excluded.
- Worker requires exact `trading_job_worker` database identity/head before
  recovery and checks safety before heartbeat/claim and again immediately before
  process spawn.
- Safety and semantic evidence are read from protected, code-owned v2 paths and
  are re-attested against the exact static producer-policy binding. Rotation
  during one operation is rejected.
- Result and artifact validation seals hashes and semantic lineage before final
  state transition.

### Release and deployment provenance

- Added the structural source-to-release-to-unit-to-PID evidence schema at
  `ops/evidence/source-release-unit-pid.schema.json`. It explicitly says schema
  validation is not promotion authority and requires semantic link checks.
- Release Authority v2 statically binds Git objects, complete artifact sets,
  locks, generated contracts, Alembic graph/head, three distinct DB roles,
  SNAPSHOT-only command/environment policy, exact units, paths, and standalone
  verifier.
- The standalone verifier SHA-256 is pinned independently by the fake-root
  provisioner. Root execution, activation, timer material, extra units/drop-ins,
  and mutable/ambiguous content reject.
- Activation/promotion build, parse, load, and application attestation remain
  deliberately unavailable and fail closed. Static authority is not runtime
  promotion evidence.

## Frozen artifact identities

| Artifact | SHA-256 |
|---|---|
| `alembic/versions/0005_job_plane_role_split.py` | `7b77d9abe0b5cfe84bf69ea60e47441179c99bcb533a6776f629cab103698f4e` |
| `ops/postgres/provision-job-roles.sql` | `4b2964256a05d60caaa9e4e94b046c2f52e369a6f70acd016f2e7e295cdac691` |
| `docs/production/runbooks/job-plane-role-split-rollout.md` | `62027f15a5a529a4e9f98e3960582aa8e72ac2320db681d6aed74d047c16dddb` |
| `ops/release-v2/verify-stage.py` | `4618114f2c3f4ac9048b956cff3a07c3cce3b605d43beb6a17563f45e266a07d` |
| `ops/release-v2/provision-root.sh` | `ae0d9c9d5b1a8d5a7bf8d2d8dad29450bf62ffdcff86ddee7b4ffb8c236a2bb6` |
| `docs/production/release-authority-v2.md` | `2a8cc3903275997e0ad8987399b934f347ae8259d33e7a25285704716074df4c` |
| `ops/evidence/source-release-unit-pid.schema.json` | `65de656d105977578d24242d0c00e626c8296eb36d2babe1eae2c9c77a70b033` |

These are source-worktree identities, not an immutable release manifest. No
candidate path or candidate authority digest exists.

## Exact verification log

All tests below were run from the canonical repository. PostgreSQL tests use
disposable isolated clusters and do not connect to `127.0.0.1:55432`.

```text
make check-contracts
PASS (exit 0)

PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/jobs tests/control_api/test_alembic_schema.py
700 passed, 1 existing Starlette/httpx deprecation warning in 113.17s

PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/runtime_release
236 passed, 1 environment-conditional xattr skip in 53.86s

cd apps/dashboard && npm test && ./node_modules/.bin/tsc --noEmit && npm run lint
140 tests passed; TypeScript passed; lint passed

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/audit_canonical_repo.py --root "$PWD"
head=9641281a8508709cab212fb460308467681854ef branch=codex/canonical-monorepo status=dirty components=core,backend,dashboard result=PASS

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/audit_canonical_repo.py --root "$PWD" --release
E_DIRTY: .superpowers/sdd/task-4-report.md (expected fail-closed release gate)

bash ops/postgres/verify-cluster.sh
127.0.0.1:55432 - no response (exit 2)

systemctl --user show trading-job-api.service trading-job-worker.service ...
both loaded, inactive/dead, disabled, MainPID=0, NRestarts=0

systemctl show trading-job-api.service trading-job-worker.service ...
both not-found/inactive, MainPID=0, NRestarts=0

ss -ltnp | rg ':8401\b'
no match (exit 1)

git diff --check
PASS (exit 0, no output)

git status --short --branch
branch codex/canonical-monorepo; 100 worktree entries:
70 modified and 30 untracked; nothing staged or committed
```

The independent Release Authority v2 post-fix review also passed its 55 focused
tests with one environment-conditional skip and found no remaining
Critical/High issue in its static-only scope.

## Changed-file inventory

Prompt 2 changes are concentrated in these groups. Prompt 1 dashboard safety
and source-provenance files remain present in the same pre-existing dirty
worktree and were not reset or cleaned.

- Job API/contracts: `apps/job_api/`, `packages/job_contracts/api.py`, generated
  Job API schema/OpenAPI, and the dashboard job BFF/operation-ID path.
- Store/scheduler: `services/job_store/`, `services/job_scheduler/main.py`,
  `alembic/versions/0005_job_plane_role_split.py`, and
  `ops/postgres/provision-job-roles.sql`.
- Worker: `services/job_worker/`, `legacy/research-backend/main.py`, and exact
  API/worker environment templates under `ops/systemd/`.
- Runtime authority: `packages/runtime_release/{config,job_plane,semantic,v2}.py`,
  `packages/runtime_release/__init__.py`, and `ops/release-v2/`.
- Evidence/docs: `ops/evidence/source-release-unit-pid.schema.json`,
  `docs/production/release-authority-v2.md`, this record, the role-split runbook,
  and `docs/superpowers/plans/2026-07-16-job-plane-release-v2.md`.
- Tests: corresponding files under `tests/jobs/`, `tests/runtime_release/`,
  `tests/control_api/`, plus `apps/dashboard/tests/trading-job-bff.test.mjs`.

The final porcelain summary is recorded above after this document and the NO_GO
candidate record were added. The full path-by-path porcelain output is included
in the session handoff; nothing was staged, committed, reset, or cleaned.

## Runtime evidence intentionally absent

The following deliverables cannot truthfully be produced under the observed
stop gates:

- authenticated runtime database head, counts, integrity, or split-role ACL
  matrix;
- an immutable release candidate, installed authority, promotion record, or
  release-to-unit/PID link;
- Job API readiness, worker heartbeat, one-job row/event/artifact evidence, or
  idle-queue evidence;
- runtime rollback evidence.

No placeholder is interpreted as success. The companion candidate record uses
explicit `NO_GO`, `UNAVAILABLE`, `NOT_VERIFIED`, and `NOT_EXECUTED` states.

## Rollback boundary

No runtime rollback was needed because nothing was deployed, migrated, started,
or enqueued. Source rollback is limited to reverting this branch's scoped
source/doc/test changes after review. Do not use `git reset`, `git clean`, or
otherwise disturb inherited dirty-worktree changes.

For a future explicitly approved attempt, use
`docs/production/runbooks/postgresql-preserve-recover.md` first, then the exact
hashed `docs/production/runbooks/job-plane-role-split-rollout.md`. Preserve all
database rows, events, artifacts, release bytes, backups, and evidence. Keep the
scheduler service/timer disabled and stop again before API/worker activation.

## Residual risks and required next gate

1. PostgreSQL is unavailable; its system identity, health, `0004` prerequisite,
   and eventual `0005` state are not verified.
2. The source worktree is dirty, so no immutable source commit/tree can bind all
   Prompt 2 bytes.
3. No reviewed root-owned hermetic Python 3.11, production-root provisioner,
   service identities/path ACLs, or immutable promotion lifecycle exists.
4. Runtime v2 authority deliberately rejects; API and worker cannot become
   ready from this source state.
5. Direct holders of a split DB credential could issue separately granted state
   and event statements and omit an event. Repository paths are atomic, but a
   future hardening phase should use fixed transition functions or deferred
   audit-completeness constraints.
6. A real SNAPSHOT invokes external research providers. It remains prohibited
   until a separately approved runtime observation explicitly authorizes that
   provider-bearing action.

The next permissible action is independent review of this source diff and both
runbooks. Database recovery/verification, migration, release provisioning, API
start, worker start, and one SNAPSHOT each require their own exact approval and
must stop on the first mismatch.
