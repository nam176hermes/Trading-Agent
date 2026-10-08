# Trading Agent

**AI-assisted market research with traceable decisions and explicit risk controls.**

[Explore the dashboard](https://tradingcompanydirect.com/dashboard) ·
[Architecture](docs/adr/ADR-HWC-HEADLESS-OPERATOR-BOUNDARIES.md) ·
[Local setup](#local-setup) ·
[Validation](#safe-validation)

Trading Agent is an early-stage research workspace for independent researchers
and small trading teams. It brings market observations, recorded agent reasoning
and risk assessments into one place, so a researcher can inspect the evidence
behind a decision instead of relying on a signal alone.

The product goal is to make research easier to review, reproduce and challenge.
AI-generated analysis is treated as untrusted input; deterministic controls and
explicit operator authority govern execution.

## Explore the public preview

The [Hermes research dashboard](https://tradingcompanydirect.com/dashboard)
is available without signing in, in **read-only mode**.

1. Open **Overview** to see the report timestamp, covered assets and decision archive.
2. Visit **Signals** to filter assets and inspect their recorded rationale.
3. Use **Risk** to review historical asset assessments.
4. Open **History** to filter decisions, page through records and inspect evidence.
5. Check **System & data** for API connectivity, execution mode and cost evidence.

The interface supports desktop and mobile layouts, explicit loading/error states
and manual refresh. Research timestamps remain visible throughout the workflow.

> **Preview status — 8 October 2026:** the connected dataset contains 10 assets
> and 16,517 historical decisions; the latest research report is dated 25 June
> 2026. These are historical observations, not current market prices or verified
> investment returns. The preview is PAPER/read-only, with both live-trading
> approvals disabled.

| Area | Current public preview |
| --- | --- |
| Market observations and signals | Connected historical reports with asset search and recorded rationale |
| Decision history | Filters, pagination and evidence inspection; latest 500 records loaded for browsing |
| Risk assessments | Recorded per-asset research assessments, not active account limits |
| System and cost evidence | Read API status and available source evidence; missing values remain unknown |
| Execution, portfolio and performance | Not connected; no orders, balances, equity curve or returns claimed |
| New research jobs | Unavailable in the public preview; requires authenticated operator access and the job service |

The hosted preview is deployed separately from this repository's tracked dashboard
snapshot. Its current UI includes deployment changes that have not yet been
published here; cloning this repository does not reproduce that exact preview.

## How Claude fits the roadmap

We plan to use Claude for source-grounded research summaries, alternative
hypotheses and explanations of recorded results. The intended workflow is:
**select an asset → review timestamped evidence → generate and challenge a
research thesis → save a report that can be inspected later**.

End-to-end Claude integration is a development milestone, not a capability
claimed by the current public demo. Planned evaluation covers evidence citation,
unsupported claims, missing-data handling, latency and token cost. Prices,
portfolio arithmetic, risk limits and execution permissions remain deterministic
system responsibilities.

## Architecture

| Component | Responsibility |
| --- | --- |
| [Dashboard](apps/dashboard) | Next.js client for research and operational views |
| [Control API](apps/control_api) | Read-only access to canonical state |
| [Job API](apps/job_api) | Durable research-job boundary |
| [Operator API](apps/operator_api) and [CLI](apps/operator_cli) | Bounded, authenticated operator commands |
| [Research backend](legacy/research-backend) | Preserved research pipeline, isolated from the core Python process |
| [Domain](packages/domain) and [event ledger](packages/event_ledger) | Fixed-precision contracts, deterministic replay and auditability |

The core uses Python 3.11 and PostgreSQL; the dashboard uses Next.js and TypeScript.
Each component retains its own dependency lockfile. See the
[headless architecture](docs/adr/ADR-HWC-HEADLESS-OPERATOR-BOUNDARIES.md) for ownership
and API boundaries.

## Next milestones

- Complete and evaluate the Claude research-to-report workflow.
- Add an authorized data-refresh workflow with visible provenance and freshness.
- Connect verified paper portfolio and performance read models.
- Test the research workflow with prospective users and measure task completion,
  usefulness and time saved.

These are planned milestones, not claims of deployed functionality or customer
traction. Research completion, paper qualification and live activation are
separate acceptance decisions.

## Engineering reference

The following setup, validation and authority notes describe the canonical source
repository. They do not grant deployment or live-trading permission.

This standalone repository is the single source root for the trading-agent
control plane, preserved research backend, and dashboard. It consolidates
reviewed source while leaving production releases, protected configuration,
runtime state, and the three provenance repositories outside the checkout.

The active safety posture is paper-only. Live execution and live-trading
approval must both remain false. Local validation must not contact an exchange,
broker, account, order endpoint, or active production mutation route.

`make ci` is source-safe and aliases portable CI. `make ci-host-authority` is
an explicit operator-managed authority lane: DEFERRED or UNAVAILABLE host
evidence is never PASS. P0-11 supplies an executable source closure matrix,
not final qualification. P1/P1-H and P2 source work exist. Canonical Pre-P3
and P3 status are derived from `docs/implementation/project-status.json` and
`docs/implementation/p3/p3-source-status.json`. P3 V2.1 source development is
separate from disposable SQL/native qualification and official research.
Production authority and live-trading authority remain false or unavailable.

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
│   ├── operator_api/             protected operator command boundary
│   ├── operator_cli/             dashboard-independent operator client
│   └── dashboard/                Next.js UI, own package-lock.json
├── services/                     control-plane services
├── packages/                     shared control-plane packages
│   ├── domain/                   D0 typed domain contracts and fixed precision
│   └── event_ledger/             deterministic replay and ledger contracts
├── legacy/research-backend/      preserved flat backend, own uv.lock
├── alembic/versions/              PostgreSQL source migrations through source-only 0020
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

`make ci` is the canonical alias for the portable `make ci-portable` gate. It runs repository
and generated-contract audits, secret hygiene, the source-safe root suite,
backend and dashboard tests, dashboard type checking, lint and build, Python
source security analysis, and production plus dev/test dependency audits.
It does not start persistent services, run migrations, invoke a broker, or
contact production mutation routes.

Host-only authority qualification is deliberately separate behind
`make ci-host-authority`. It requires both native and external authority
artifacts to validate as PASS; DEFERRED or UNAVAILABLE is a non-zero result.
It is dispatched only to the protected Linux/x64 authority-host workflow and
is never reachable from `make ci` or portable GitHub Actions.

The narrower non-building aggregate remains available as `make test-all`.
Run `uv run python scripts/dev.py static` once before its offline static-policy
tests to populate the pinned checker cache. The error-level gate retains warning
diagnostics and their baselines; warnings alone do not fail either local or CI output.
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

## Headless operator CLI

The installed `trading-agent` command talks directly to the loopback Control,
Job, and Operator APIs; it does not require the dashboard. Read commands include
`status`, `capabilities`, `jobs list`, `jobs show`, and `kill-switch status`.
Operator mutations require a caller-selected `--idempotency-key`; use
`mode paper`, `kill-switch activate`, or `kill-switch clear`. Configuration and
examples are in [`docs/operations/hwc-operator-api.md`](docs/operations/hwc-operator-api.md).

## Foundation status

The canonical project-status projection is generated by
`scripts/derive_project_status.py` and verified in portable CI. Pre-P3 gate
receipts now bind a versioned SHA-256 source closure covering every tracked
source path, mode, and byte except strictly validated receipt/projection
outputs. Identical source can therefore survive squash, rebase, or cherry-pick
promotion, while any source, lockfile, contract, mode, path, or evidence drift
holds the gate. Candidate qualification alone does not grant P3 authority; a
separate protected-main promotion receipt is required. See
[`docs/operations/pre-p3-qualification.md`](docs/operations/pre-p3-qualification.md).

The D0 source foundation currently includes:

- D0.1 fixed-precision primitives, canonical decimal policy, instrument IDs,
  clocks, and trading constraints;
- D0.2 strict domain events, signals, portfolios, risk decisions, and order
  contracts with generated-schema drift checks;
- D0.3 deterministic immutable-set replay, aggregate snapshots, durable
  outbox/inbox idempotency contracts, with the activated protected database
  source head `0011_engine_backtest_worker_authority`.

The executable [D0 closure matrix](docs/implementation/d0-closure-matrix.json)
has no unresolved source requirement. `make check-d0-closure` validates every
implementation path, exact test proof, CI collection path, required final gate,
and the source/runtime boundary. D0 source readiness is closed.

The operator-managed PostgreSQL has not been migrated, mutated, or probed by
these validation gates. A separately approved disposable PostgreSQL 16 run for
`b42607cb8c21d7a7b5ffeb854f08d62e9d15ff2f` bound all three runtime commands,
restore-semantic parity and cleanup to a protected transcript and durable
hash-sealed archive. The executable D0 matrix records
`runtime_postgres_parity=PASS`. This disposable proof does not authorize
production PostgreSQL access, migration, service changes or live trading. See
`docs/implementation/foundation-postgres-approval-evidence.md`.

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

The accepted HWC architecture keeps the trading core headless. Control API is
the read authority, Job API owns durable research jobs, and the bounded
Operator API is the sole target for operator state commands. Dashboard and CLI
are replaceable clients; this source architecture grants no deployment or live
authority. See `docs/adr/ADR-HWC-HEADLESS-OPERATOR-BOUNDARIES.md`.

The future process layout, protected state and credential ownership, ordering,
rollback rules, and explicit held deployment verdicts are recorded in
`docs/operations/hwc-process-topology.md`. It is documentation only and does not
install or activate services.

Read current gate status from source and retained receipts:

```bash
uv run python scripts/derive_project_status.py
```

The result distinguishes HWC, pre-P3 and P3 gates and their hold reasons.
Local tests and feature-branch evidence do not replace protected-main Foundation
qualification, independent holdout custody, or a phase-exit receipt. Source
changes do not grant deployment or live authority.
