# Claude Code entry point

@AGENTS.md

The imported `AGENTS.md` is the canonical operating contract for every coding
agent. Read the nested `AGENTS.md` before working in `apps/dashboard/` or
`legacy/research-backend/`; path-scoped rules point to them without copying policy.

Read only the relevant section of `docs/development-workflow.md` for tools,
checks, handoff or resumption. Use [North Star V4.0](docs/NORTH-STAR-V4.md) as the
canonical product direction and the accepted task plan in the current checkout.
V4 section 15 preserves the active P3 and authority contracts; its NS milestones
do not replace qualification gates. Supporting ADRs are linked from `AGENTS.md`.
Do not infer current phase completion from historical receipt or gate counts.

Start focused root feedback with a discovered test path:

```bash
uv run python scripts/dev.py test tests/foundation/test_dev_commands.py
uv run python scripts/dev.py test --native-custody tests/foundation/test_package6_controller_closure.py
```

`--native-custody` belongs before the test path. The wrapper owns private temp
storage and native test custody; it does not grant runtime authority. Component
commands and candidate-freeze gates are in the canonical workflow.

Claude permission settings (`.claude/settings.json`) are operator-local and not
committed; they are a prompt-time guard, not a sandbox. Honor the root approval
boundaries even if a permission mode or tool can run a command.
Do not create automatic full-suite hooks or copy secrets into project settings.

## Quick map (verified 2026-10-16; README owns the full map)

| Path | Role | Entry point |
| --- | --- | --- |
| `apps/control_api` | read-only Control API, `127.0.0.1:8400` | `control_api/main.py` |
| `apps/job_api` | durable research jobs, `:8401` | `main.py` |
| `apps/operator_api` | bounded operator commands, `:8402` | `main.py` |
| `apps/operator_cli` | headless client, `trading-agent` script | `cli.py:main` |
| `packages/`, `services/` | domain, ledger, risk, Nautilus engine glue; workers/scheduler | — |
| `legacy/research-backend` | flat research pipeline, own `uv.lock` | nested `AGENTS.md` |
| `apps/dashboard` | Next.js 16.3 client, own `package-lock.json` | nested `AGENTS.md` |
| `generated/`, `alembic/` | generator-owned contracts; source migrations (never run them) | — |

Gate status comes from `uv run python scripts/derive_project_status.py`, never from docs.

## Claude Code tooling

- `.mcp.json` registers local `codebase-memory` (analysis profile, read-only) for
  structure and `serena` (`claude-code` context) for symbols, matching the routing above.
  Edit with Claude's own tools, not Serena's edit/memory tools. Approve both servers once with `/mcp`.
- `.claude/rules/` loads the nested component rules by path. The nested
  `CLAUDE.md` files are generic behavior notes; the nested `AGENTS.md` files govern.
- No hooks are configured on purpose (see above).

## Verified pitfalls

- A live operator Control API usually listens on `127.0.0.1:8400`. The dashboard
  security smoke then fails with "reader trading GET without Control API returned
  HTTP 200 (expected 503)". That is a host collision, not a regression. Never stop
  that service to make a test pass.
- If `npm ls` reports `next` as invalid, `node_modules` is stale; run `npm ci` in
  `apps/dashboard`. Extraneous `@emnapi/*` and `sharp-wasm32` entries are harmless.
- `pg_ctl` is not on `PATH` (`/usr/lib/postgresql/16/bin`). Disposable PostgreSQL
  needs explicit approval; never touch the operator cluster.
- The Nautilus engine runtime is cp312 (tests override the interpreter with
  `TRADING_NAUTILUS_PINNED_PYTHON`); the control plane stays on 3.11.
- Project and runtime receipts expire, and this host's clock jumps. Re-derive status
  instead of trusting an earlier run.
- `make check-contracts` stops at the P1 lineage gate on a dirty checkout; that is a
  state precondition. Its other sub-targets run individually.
- Root `make test` takes about an hour. Host `bwrap` drift (Ubuntu upgrade, 2026-09-19)
  fails the sealed-uv-exec policy digest test, and Git seal tests have a 30 s budget
  that load can exceed. Treat both as host drift, not regressions.

## Current priorities

Phase is `PRE_P3_CLOSURE`; held gates come from the status script above. Product
next step is NS V4 section 18 (mandate, then a small NS0–NS1 fixture slice), kept
independent of P3 closure. Onboarding evidence: `docs/CLAUDE_PROJECT_ONBOARDING_REPORT.md`.
