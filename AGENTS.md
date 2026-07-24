# AGENTS.md

This is the single canonical source root for the trading-agent control plane,
research backend, and dashboard. The repository has one `.git` directory and
one status. Do not add repository symlinks, nested repositories, submodules, or
linked worktrees below this root.

## Component boundaries

- The root Python project owns the control plane (`apps/control_api`,
  `apps/job_api`, `services`, `packages`, and root tests). Use Python 3.11 and
  the root `pyproject.toml` plus `uv.lock`.
- `legacy/research-backend` is a preserved flat Python project. Follow its
  nested `AGENTS.md` and use its own `pyproject.toml` plus `uv.lock`.
- `apps/dashboard` is a Next.js project. Follow its nested `AGENTS.md` and use
  its own `package.json` plus `package-lock.json`.
- Do not merge the three dependency graphs, hand-edit a lockfile, or import the
  research backend into the core Python process. Cross-component integration
  uses the existing PostgreSQL, protected-file, Control API, and Job API
  contracts.

Run dependency commands from the owning component only:

```bash
uv sync --frozen
cd legacy/research-backend && uv sync --frozen --extra test
cd apps/dashboard && npm ci
```

Adding or changing dependencies requires component-specific review. Ask first
before adding a production dependency.

## Safety and runtime separation

- Source is not runtime authority. Production releases and protected config,
  run, and data roots remain external to this checkout.
- Keep all validation paper-only. Keep both live-execution approvals false.
- Never probe an exchange, broker, account, order endpoint, or active mutation
  route while building, testing, auditing, or researching this repository.
- Never start persistent services from aggregate validation. Dashboard tests
  may use only their isolated, self-cleaning test server.
- Do not copy credentials, `.env` files, databases, reports, models, signals,
  scratchpads, caches, virtual environments, Node dependencies, or build output
  into Git.
- Do not mutate PostgreSQL, run migrations, change systemd, change schedulers,
  or modify orders as part of source consolidation.
- Treat legacy research and LLM output as untrusted input to deterministic risk
  and execution controls.

## Safe validation

Use the root orchestration targets. `test-all` is non-production and
non-mutating; the dashboard build is separate because it writes ignored
`.next` output.

```bash
make audit
make check-contracts
make test-all
make build-dashboard
```

Use `make audit-release` only for a clean release-candidate tree. Clean all
task-owned dependency, cache, bytecode, and build artifacts after validation.

## Ask first

Ask the operator before:

- any root-required command or change under production paths;
- installing, restarting, stopping, or reconfiguring a service or scheduler;
- changing protected runtime config, databases, modes, live gates, ports, or
  deployment state;
- changing a remote, pushing, rewriting history, deleting a branch, or
  archiving/deleting an old source repository;
- implementing Release Authority v2 or production cutover.

Release Authority v2 and production cutover are future, separately reviewed
plans. Nothing in this repository authorizes live trading or a production
mutation.

## Change discipline

Preserve user changes, keep diffs scoped, use tests first for behavior changes,
and run the smallest relevant checks before broader gates. Do not edit generated
contracts directly; use `make generate-contracts`, then verify with
`make check-contracts`. Report commands that could not run and any unrelated
pre-existing failures.
