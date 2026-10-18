# Claude Code project onboarding report

Date: 2026-10-16 · Checkout: `main` at `8ee1c18a` (dirty: pre-existing doc/harness
edits, listed in `git status`) · Scope: source checkout only; no runtime, database,
service, broker or live mutation was performed.

Authoritative sources are not repeated here: `AGENTS.md` (operating contract),
`README.md` (setup, map), `docs/development-workflow.md` (tools, gates),
`docs/NORTH-STAR-V4.md` (product direction). This report records what was verified.

## 1. Architecture summary

One Git root, three independent dependency graphs:

| Component | Stack | Lock | Entry |
| --- | --- | --- | --- |
| Control plane (`apps/control_api`, `job_api`, `operator_api`, `operator_cli`, `packages/`, `services/`) | Python 3.11, FastAPI, SQLAlchemy/Alembic, psycopg, Polars/DuckDB | `uv.lock` | Control API `127.0.0.1:8400` (read-only), Job API `:8401` (durable jobs), Operator API `:8402` (bounded commands), CLI `trading-agent` |
| Research backend (`legacy/research-backend`) | flat Python 3.11 project | own `uv.lock` | nested `AGENTS.md` |
| Dashboard (`apps/dashboard`) | Next.js 16.3.4, React 19.2, TypeScript, Tailwind 4 | `package-lock.json` | client only; BFF routes proxy Control API |
| Engine | NautilusTrader 1.231.0 (cp312, rollback 1.227.0), native custody extension in `native/` | pinned closures | `trading-agent-nautilus` |

PostgreSQL 16 is the integration store (migration head `0019_p2_security_master`;
source migrations through 0020). Contracts in `generated/` come from
`scripts/generate_contracts.py`. Ports and pins above were verified in source
(`apps/*/config.py`, `control_api/main.py`, `derive_project_status.py`).

## 2. Current status (derived, not from docs)

`uv run python scripts/derive_project_status.py`, run 2026-10-16:

- Phase `PRE_P3_CLOSURE`; scope `PAPER_LOCAL_ONLY`; `live_enabled=false`,
  `live_eligible=false`; broker/network/production authority all false.
- PASS: P0 source complete, P1_COMPLETE, ARCH_CONTRACT_READY, HWC architecture,
  boundaries, operator API/CLI/journal/service, portable headless proof, source complete.
- HELD (11 blockers): PRE_P3_READY (qualified candidate provenance absent),
  HWC_SOURCE_READY, HWC_PORTABLE_QUALIFIED, P1_H, P1_LTS, P2 source/runtime/qualified,
  P3 baselines/evaluation protocol, ALPHA_REGISTRY_FOUNDATION — each needs a valid
  v2 candidate receipt. P3 phase gates all HELD (`E_P3_PHASE_EXIT_RECEIPT`).
- Product: North Star V4.0 (adopted 2026-10-15) — next step is mandate decisions and
  a small NS0–NS1 fixture/mock slice, independent of P3 closure (V4 §11, §18).

Code-level debt markers are minimal: the 6 `TODO` hits in source are all validator
literals that reject placeholder values, not open work.

## 3. Environment and dependencies

| Tool | Found | Notes |
| --- | --- | --- |
| uv | 0.11.7 | |
| Python | 3.11.15 (root and legacy `.venv`), 3.12.3 system | 3.12 serves the Nautilus cp312 runtime |
| Node / npm | 22.23.0 / 10.9.8 (mise) | |
| PostgreSQL | psql 16.15; `pg_ctl` at `/usr/lib/postgresql/16/bin` (not on PATH) | operator cluster on `127.0.0.1:5432`; untouched |
| gh, rg, make, docker | present | |
| codebase-memory-mcp 0.10.8, serena 1.7.0 | present in `~/.local/bin` | |
| ruff, basedpyright | not on PATH | pinned and run through `scripts/dev.py` |

Installed dependencies: **none needed.** `uv sync --frozen --dry-run` reports no changes
for root (41 packages) and legacy (94 packages); `npm ls --depth=0` in the dashboard
reports a valid tree. No upgrades, global installs or remote scripts were run.

Environment variables: services load authority from protected files or systemd
credentials (`CREDENTIALS_DIRECTORY`) and fail closed without them; none were created.

## 4. Claude Code configuration

| Item | State |
| --- | --- |
| `CLAUDE.md` | Imports `AGENTS.md`; quick map, tooling, verified pitfalls, current priorities. This session corrected the Nautilus interpreter note and added the contracts pitfall and priorities. |
| `.claude/rules/{dashboard,research-backend}.md` | Path-scoped pointers to nested `AGENTS.md` |
| `.claude/settings.json` | Operator-local, not committed (`.gitignore`). On 2026-10-18 the operator removed all `ask` rules; the `deny` list (history rewrite, remote changes, root provisioning/rollback scripts, Serena edit/memory tools) stays local. Root `AGENTS.md` "Ask first" still applies. |
| `.mcp.json` | `codebase-memory` (analysis profile) and `serena` (`claude-code` context, no dashboard). Both **pending approval** — approve with `/mcp` in an interactive session. |
| Hooks | None, on purpose (`CLAUDE.md`: no automatic full-suite hooks). |
| Skills / subagents | No project-local additions. The user-level superpowers/ponytail skills and built-in agents cover the workflow; scientific-domain skills are irrelevant here and not loaded. |

Not added: Playwright MCP (the dashboard `AGENTS.md` prefers direct Playwright),
a GitHub MCP (`gh` covers it) and database MCPs (they would put the operator cluster in
reach — rejected on safety grounds).

## 5. Changes made in this session

- `CLAUDE.md`: Nautilus note corrected (`TRADING_NAUTILUS_PINNED_PYTHON` is a test
  launcher override, `tests/nautilus_backtest/test_launcher_protocol.py:48`); added the
  `check-contracts` dirty-checkout pitfall and a "Current priorities" section.
- This report.
- `apps/dashboard/.next` rebuilt by `make build-dashboard` (ignored output).

Concurrency note: a Codex desktop session was editing `CLAUDE.md` and
`.claude/settings.json` at the same time (Serena deny list, 00:43). Those edits were
preserved; `.mcp.json`, the quick map and the rules predate this session.

## 6. Verification results

All runs on the working tree, 2026-10-16. Logs were kept in the session scratchpad.

| Check | Command | Result |
| --- | --- | --- |
| Workspace doctor | `uv run python scripts/dev.py doctor` | PASS, 2 warnings: native custody authority pair not supplied (expected outside custody runs); 19 linked worktrees, all outside this root |
| Lint + types | `uv run python scripts/dev.py static` | PASS: Ruff clean; root and legacy Basedpyright 0 errors |
| Root tests | `make test` (excludes `runtime_postgres`, `host_coupled`) | 11004 passed, 251 skipped, **3 failed** (59 min). All three are environment, not source: (a) `test_nautilus_sealed_uv_exec.py::test_committed_policy_binds_all_task3_sources_and_private_toolchain_policies` — committed policy pins `/usr/bin/bwrap` SHA-256 `52231e1c…`; host now has `e3189038…` after Ubuntu upgraded `bubblewrap` 0.9.0-1ubuntu0.1→0ubuntu0.3 on 2026-09-19; (b) `test_engine.py::test_engine_generates_from_the_exact_current_commit_source` and (c) `test_git_source.py::test_t2_group_02_exact_accepted_source_finishes_inside_seal` — "Git object-store seal deadline exceeded" under concurrent load. (b) passes alone (267 s); (c) still misses alone by ~1 s (30.96 s vs 30.0 s) on this WSL host with 5,569 loose Git objects |
| Legacy backend tests | `make test-backend` | PASS: 507 passed, 2 skipped |
| Secret hygiene | `make check-secrets` | PASS |
| Contracts | `make check-contracts` | Stops at `check-p1-nautilus-lineage`: "P1 lineage source checkout is not clean" (state, not code). All other sub-checks run individually and pass: boundaries, pin inventory (267 s), HWC status, project status, `generate_contracts --check`, Nautilus protocol `--check`, HWC boundaries (0 violations) |
| Dashboard types | `make typecheck-dashboard` | PASS |
| Dashboard lint | `make lint-dashboard` | PASS |
| Dashboard tests | `make test-dashboard` | 199 pass / 0 fail; security integration smoke fails: "reader trading GET without Control API returned HTTP 200 (expected 503)" — caused by the live operator Control API on `:8400` (environment collision, not a regression) |
| Production build | `make build-dashboard` | PASS |
| Startup smoke | import `control_api.main`, `apps.job_api.main`, `apps.operator_api.main`, `apps.operator_cli.cli`; `trading-agent --help` | PASS. Servers were not started: they need protected authority/credentials and would collide with live operator services |

Not run, by rule: `make test-all` / `make audit` (they need a clean standalone
candidate clone), `runtime_postgres` and `host_coupled` suites (operator database or
host authority), `make ci-portable` (hosted context), anything touching an exchange.

## 7. Remaining blockers

1. **Qualification receipts** — 11 HELD gates need a qualified v2 candidate
   (standalone clone, LOCAL/hosted qualification). Not a source defect.
2. **Dirty `main`** — uncommitted AGENTS/README/workflow/`scripts/dev.py` edits block the
   lineage gate and any candidate freeze. Commit or move them to a branch first
   (operator decision).
3. **Dashboard security smoke vs. live `:8400`** — run it where no Control API is
   bound, or in the standalone candidate environment.
4. **Host drift in root tests** — the `bwrap` digest in the committed sealed-uv-exec
   policy no longer matches the upgraded host binary; resealing that policy is an
   authority change for the operator, not a test edit. The 30 s Git seal budget is
   marginal here; `git gc` (operator-approved) or a quieter host should restore it.
5. **MCP approval** — `codebase-memory` and `serena` stay inactive until approved.
6. Housekeeping (operator's call): `.claude/settings.local.json` holds stale one-off
   allows (old scratchpad paths, `cat *`, `git clone *`, `python3 -`) that are broader
   than the project allowlist; `.claude/settings.json.bak*` and root `codex_plan.zip`
   are untracked leftovers.

## 8. Recommended next priorities

1. Land or branch the pending harness/doc edits so `main` is clean again.
2. Close the NS V4 §10 mandate decisions and write the NS0–NS1 plan: one fixture/replay
   BTC spot paper session through research → plan → risk → internal paper →
   reconciliation → dashboard, no paid APIs or broker.
3. In parallel and independently, continue P3/PRE_P3 closure on a standalone
   candidate per the accepted plan; derive status rather than trusting this report.
