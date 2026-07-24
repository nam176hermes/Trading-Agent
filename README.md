# Trading Agent Canonical Source

This standalone repository is the single source root for the trading-agent
control plane, preserved research backend, and dashboard. It consolidates
reviewed source while leaving production releases, protected configuration,
runtime state, and the three provenance repositories outside the checkout.

The active safety posture is paper-only. Live execution and live-trading
approval must both remain false. Local validation must not contact an exchange,
broker, account, order endpoint, or active production mutation route.

## Project map

```text
trading-agent/
├── .git/                         one standalone Git authority
├── AGENTS.md                     root operating rules
├── Makefile                      safe cross-component orchestration
├── pyproject.toml + uv.lock      control-plane dependency authority
├── apps/
│   ├── control_api/              read-only Control API
│   ├── job_api/                  job API and contract boundary
│   └── dashboard/                Next.js UI, own package-lock.json
├── services/                     control-plane services
├── packages/                     shared control-plane packages
│   ├── domain/                   D0 typed domain contracts and fixed precision
│   └── event_ledger/             deterministic replay and ledger contracts
├── legacy/research-backend/      preserved flat backend, own uv.lock
├── alembic/versions/              PostgreSQL source migrations through 0008
├── generated/                    generated contracts
├── ops/consolidation/            source authority and import manifests
├── scripts/                      audit and contract tooling
└── tests/consolidation/          one-root and provenance gates
```

Component authority is intentionally local:

- the root `uv.lock` owns core/control-plane Python dependencies;
- `legacy/research-backend/uv.lock` owns research-backend dependencies;
- `apps/dashboard/package-lock.json` owns dashboard dependencies.

The immutable source checkpoints are recorded in
`ops/consolidation/source-authority.json`:

| Component | Approved source commit | Approved source tree |
|---|---|---|
| Core/application and ops | `d9d46fa363f26bd78f5560300d26913494e11e4d` | `bfac951424d09f21359fcc11abb0bbe000456b4e` |
| Research backend | `59578f984b72d5d03583a2c06b15a53a224b31c8` | `54e688e9f144aecd2ee204ab95953f7c57069d3c` |
| Dashboard subtree | `84627f16e9753b1104d661697720b93897f27d27` | `792f572dea8f819438785e43ee05e07c5b6567bd` |

There is no unified uv or npm workspace, nested Git repository, submodule, or
repository symlink. Core Python must not import the flat research backend in
process. Components integrate through the established database, protected-file,
Control API, and Job API contracts.

## Local setup

Use Python 3.11. Install only from the lockfile owned by the component you are
working in:

```bash
# Core/control plane
uv sync --frozen

# Research backend
cd legacy/research-backend
uv sync --frozen --extra test
cd ../..

# Dashboard
cd apps/dashboard
npm ci
cd ../..
```

Do not merge dependency graphs or hand-edit lockfiles. Dependency directories
and build output are local artifacts and must remain untracked.

## Safe validation

From the repository root:

```bash
make ci
```

`make ci` is the canonical local and GitHub Actions gate. It runs repository
and generated-contract audits, secret hygiene, the source-safe root suite,
backend and dashboard tests, dashboard type checking, lint and build, Python
source security analysis, and production plus dev/test dependency audits.
It does not start persistent services, run migrations, invoke a broker, or
contact production mutation routes.

The narrower non-building aggregate remains available as `make test-all`.
Useful focused gates include:

```bash
make check-secrets
make check-contracts
make check-d0-closure
make test-runtime-release
make test-security
make test-dashboard
make typecheck-dashboard
make lint-dashboard
make build-dashboard
make audit-dependencies
```

Useful component-only gates are `make test-core`, `make test-backend`,
`make test-dashboard`, `make typecheck-dashboard`, and `make lint-dashboard`.
`make test-runtime-release` runs all hermetic runtime-release tests. The two
host-coupled cases are isolated behind `make test-runtime-release-host` and are
not part of portable CI.
`make test-runtime-postgres` is an explicit read-only smoke against the
operator-managed PostgreSQL. `make test-runtime-dual-read` separately checks
the mutable legacy dataset against the reviewed PostgreSQL snapshot, so legacy
data drift cannot misreport the database as unavailable. Neither target is part
of the non-mutating source gate.
`make audit-release` additionally requires a clean index and worktree.

## Foundation status

The D0 source foundation currently includes:

- D0.1 fixed-precision primitives, canonical decimal policy, instrument IDs,
  clocks, and trading constraints;
- D0.2 strict domain events, signals, portfolios, risk decisions, and order
  contracts with generated-schema drift checks;
- D0.3 deterministic immutable-set replay, aggregate snapshots, durable
  outbox/inbox idempotency contracts, and migration source head
  `0008_trading_domain_ledger`.

The executable [D0 closure matrix](docs/implementation/d0-closure-matrix.json)
has no unresolved source requirement. `make check-d0-closure` validates every
implementation path, exact test proof, CI collection path, required final gate,
and the source/runtime boundary. D0 source readiness is closed.

The operator-managed PostgreSQL has not been migrated or mutated by these
validation gates. Runtime PostgreSQL parity remains `PENDING_APPROVAL` in the
matrix and requires the separately approved
`make test-event-ledger-runtime-postgres` proof. That pending runtime status does
not weaken or masquerade as source readiness.

## Source versus runtime

This checkout is source authority, not production runtime authority. Immutable
production releases, protected configuration, database state, run state, and
operator data stay in their existing external locations. Source consolidation
does not change services, schedulers, ports, databases, trading mode, live
gates, risk policy, strategies, prompts, models, or orders.

The imported backend and dashboard manifests under `ops/consolidation/` bind
their exact source commits, trees, paths, modes, and bytes. The old repositories
remain unchanged provenance and rollback sources until a separately approved
archival decision.

## Plan boundaries

This repository now contains the reviewed **Release Authority v2 static source
implementation** and hermetic verification tests. It can describe and verify an
offline sealed candidate, but no real candidate has been built, installed, or
activated. The activation and promotion API remains deliberately unavailable.
See `docs/production/release-authority-v2.md` for exact stop conditions.

1. **Release Authority v2 activation** remains a separate plan requiring
   reviewed authority bytes, a clean committed source tree, approved offline
   inputs, and explicit operator authorization.
2. **Production cutover** remains a later, separately approved plan for root
   provisioning, deployment, smoke checks, rollback, and any archival decision.

Neither plan enables live trading. Any root, production, runtime, remote, or
cutover mutation requires explicit operator approval.
