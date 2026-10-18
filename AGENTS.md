# AGENTS.md

This is the single canonical source root for the trading-agent control plane,
research backend, and dashboard. The repository has one `.git` directory and
one status. Do not add repository symlinks, nested repositories, submodules, or
linked worktrees below this root.

## Component boundaries

- The root Python project owns the control plane (`apps/control_api`,
  `apps/job_api`, `apps/operator_api`, `apps/operator_cli`, `services`,
  `packages`, and root tests). Use Python 3.11 and
  the root `pyproject.toml` plus `uv.lock`.
- `legacy/research-backend` is a preserved flat Python project. Follow its
  nested `AGENTS.md` and use its own `pyproject.toml` plus `uv.lock`.
- `apps/dashboard` is a Next.js project. Follow its nested `AGENTS.md` and use
  its own `package.json` plus `package-lock.json`.
- Do not merge the three dependency graphs, hand-edit a lockfile, or import the
  research backend into the core Python process. Cross-component integration
  uses the existing PostgreSQL, protected-file, Control API, and Job API
  contracts.

Install only when needed, from the owning component's frozen lockfile; exact
commands are in [README.md](README.md#local-setup). Dependency changes require
component-specific review; ask before adding a production dependency.

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

Repository rules and operator decisions override skills. Load only the relevant
[development workflow](docs/development-workflow.md) section for routing,
delegation, test escalation, or resumption.

Use [North Star V4.0](docs/NORTH-STAR-V4.md) as the canonical product North Star,
adopted by the operator on 2026-10-15: crypto-first, with a 24/7 spot paper
workflow and BTC proposed as the initial instrument. Section 15 defines its
precedence over historical product roadmaps and preserves existing safety,
economic, qualification and runtime authority contracts. The accepted
[Core / Optional Intelligence delta](docs/adr/ADR-NORTH-STAR-CORE-OPTIONAL-INTELLIGENCE.md)
remains a supporting decision record. Core paper/shadow follows actual
dependencies; advanced P4–P8 is optional for Core completion. V4 milestones
NS0–NS6 do not rename phases or expand the active P3 campaign.
Research completion, economic acceptance, paper qualification and live activation
are separate verdicts. Read the task's accepted plan in the selected checkout;
main, an implementation worktree and a qualified candidate are not interchangeable.

The operator-approved default is **local development with publication by phase**:
finish P3 locally, publish a review batch when authorized, resolve external
review, then plan and build P4 locally; repeat for later phases. Follow the
[phase workflow design](docs/superpowers/specs/2026-09-18-local-phase-workflow.md)
and [implementation plan](docs/superpowers/plans/2026-09-18-local-phase-workflow.md).
Use focused checks during development and full applicable checks at candidate
freeze. Do not require a push or hosted CI after each small change. Publication
does not replace local validation, and local validation does not grant runtime
authority. The authenticated local qualification lane uses protected, source-bound
receipts; preserve its gates and the separate hosted publication requirements.
Report missing or expired local qualification as HELD without blocking ordinary
local source development. See the [official LOCAL runbook](docs/implementation/p3/local-official-runbook.md).

1. **Scope once.** Record outcome, component, risk, paths, authority and checks;
   inspect cwd, status, HEAD/tree and scoped diff. Refresh for changed state or
   handoff. Preserve user changes. Non-trivial implementation uses an external
   clean worktree from the accepted parent, never below this root.
2. **Trace before editing.** Read nested rules, callers, consumers and tests.
   Route structural questions to Codebase Memory, symbols to Serena when
   available, exact evidence to `rg`/source. Reuse unchanged context; expand for
   named gaps. Use pinned official docs and the dashboard's installed guide.
3. **Scale the process.** Mechanical: inspect/edit/check. Bounded: short seam,
   files, behavior and checks. Architecture/contracts/durable authority: approved
   spec/plan. Choose one applicable engineering workflow; load specialized skills
   only for a concrete gap. Respect selected models. Agents require explicit
   direction, normally 0–2 independent scopes and one writer per file.
4. **Implement minimally.** Ponytail full: reuse code, stdlib, platform, installed
   dependencies, then minimal new code. Fix root causes test-first; refactor
   only while green. No speculative abstractions or unrelated cleanup. Never
   weaken trust validation, fixed-precision money, security, recovery or accessibility.
5. **Verify and review.** Focused checks precede required gates. Review correctness
   and quality, then simplicity; no automatic extra agents. Use Codex Security
   for sensitive diffs and direct Playwright for browser behavior. Unavailable
   review is NOT REVIEWED, never PASS. Evidence proves only its exercised scope.
6. **Hand off.** Report files/behavior, commands/results, SHA/tree and dirty state,
   gaps and risks. Separate source, ledger, remote/main, runtime and live verdicts.
   Stop before commit/push/PR/merge, deploy, service, database, broker or live
   mutation unless separately authorized.

### Keep the feedback loop bounded

- Default to the operator's Astra High selection. Do not lower effort automatically
  or expand scope to justify it. Escalation above High needs a named unresolved
  problem; instructions and project defaults do not change the running model.
- Before a broad or long check, state the changed input or acceptance requirement
  it covers, existing evidence, and known prerequisites. Reuse valid evidence;
  never present retained evidence as a fresh run or rebind official receipts.
- During implementation, run focused checks. Run required aggregate gates at
  candidate freeze, not after each small edit. A failed aggregate is repaired
  with focused checks before the required final rerun.
- `make check-test-skips` reruns suites; it is not a report-only check. Do not
  repeat a known authority/environment failure without changed prerequisites.
- Stop expanding once the requested behavior and applicable checks are satisfied.
  Extra frameworks, helpers, review rounds or test matrices need a concrete gap.
  Missing mandatory proof remains HELD; efficiency never waives a safety gate.

## Safe validation

Start root feedback with `uv run python scripts/dev.py test <test-path>`;
it captures output and owns private temporary storage. Nested component rules
still apply. For Python changes, also run `uv run python scripts/dev.py static`;
`make test-all` does not include Python static checks. Documentation-only work
needs diff/link/command and safeguard review; it does not imply full source
qualification.

Use linked worktrees for implementation and focused checks. Before running
`make audit` or `make test-all`, prepare the exact candidate in a standalone
checkout with its own `.git` directory, then verify SHA/tree and scoped diff.
These gates reject linked worktrees. Preserve user changes and existing
commit/push approval boundaries during handoff; portable checks do not replace
required canonical or hosted qualification gates.

For broad or high-risk source completion retain `make audit`,
`make check-contracts`, `make test-all`, and `make build-dashboard`, plus
required security, concurrency, persistence, recovery, and authority checks.
`test-all` already includes audit/contracts and dashboard tests/type/lint;
do not rerun covered checks without changed inputs or a distinct evidence need.
It remains non-production and non-mutating; build writes ignored `.next`.

Source qualification additionally requires `make ci-portable NONINTERACTIVE=1`
in genuine hosted context; do not fabricate GitHub run IDs or bypass gates.
Use `make audit-release` only for a clean release candidate. Missing authority
stays held. Clean only task-owned artifacts; preserve pre-existing environments.

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
contracts directly; use the owning generator (`make generate-contracts`, or
`make generate-p1-nautilus-contracts` for Nautilus P1 protocol contracts), then
verify with `make check-contracts`. Report commands that could not run and any
unrelated pre-existing failures.
