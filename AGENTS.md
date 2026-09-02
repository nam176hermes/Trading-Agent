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
- New P3+ interfaces must obey the accepted HWC ownership and boundary policy:
  Control API is read-only, Job API owns durable jobs, and bounded operator
  state commands belong only to Operator API. Dashboard and CLI remain clients.

## Default Codex workflow

Use this workflow for every task unless the operator gives a narrower
task-specific instruction. Repository rules and explicit operator decisions
always override skills and tools.

1. **Contract and route.** Record the observable outcome, component, accepted
   baseline, invariants, prohibited actions, and evidence required for a
   verdict. Use the applicable Superpowers process skill: brainstorming for a
   feature, systematic debugging for a defect, writing/executing plans for an
   approved multi-step design, and verification before any completion claim.
2. **Baseline and isolate.** Check the real cwd, status, HEAD, tree, and diff.
   Preserve user changes. Use an external clean worktree from the exact
   accepted parent for non-trivial implementation; never create one below this
   repository. Parallel agents require explicit operator direction, one owner
   per write surface, and fresh verification of the integrated result.
3. **Trace before editing.** Read the applicable instructions and complete
   flow. Prefer Codebase Memory for structural discovery when available, then
   confirm callers, consumers, configuration, and tests with `rg` and source.
   Use current official documentation for version-sensitive APIs; for Next.js,
   read the installed guide required by `apps/dashboard/AGENTS.md` first.
4. **Reduce the design.** After understanding the flow, apply Ponytail full:
   reuse repository code, then stdlib, native platform behavior, and installed
   dependencies before writing the smallest coherent new code. Do not add
   speculative abstractions, configuration, dependencies, or unrelated
   cleanup. Fix defects once at the shared root cause, not at each symptom.
5. **Approve the seam.** A bounded change needs a short design covering the
   seam, files, behavior, and checks. An architectural change needs an approved
   spec and implementation plan. Do not implement beyond the approved design.
6. **Implement test-first.** Demonstrate the gap with a focused failing test or
   deterministic reproduction, make the minimal change, prove it passes, and
   refactor only while green. Never simplify away trust-boundary validation,
   fixed-precision money behavior, security, recovery, or accessibility.
7. **Verify and review.** Run focused checks before broader component gates.
   Review correctness and engineering quality independently from a separate
   Ponytail over-engineering pass. Use Codex Security for sensitive diffs and
   direct Playwright browser smoke for browser-facing behavior. A tool report,
   worker summary, or green unit suite is evidence only for what it exercised.
8. **Qualify source.** After affected checks pass, run the safe root gates below
   and `make ci-portable NONINTERACTIVE=1` when source qualification is in
   scope. Use `make audit-release` only for a clean release candidate. Never
   infer host, runtime, paper activation, or live authority from source gates.
9. **Hand off evidence.** Report changed behavior, files, exact commands and
   results, candidate SHA/tree, skipped checks, and residual risks. Keep source
   verdict, official ledger status, remote branch/main state, and runtime/live
   readiness separate. Stop before commit, push, PR, merge, deploy, service,
   database, broker, or live mutation unless separately authorized.

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
