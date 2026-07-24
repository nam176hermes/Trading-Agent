# Codex prompt - Phase 0 runtime inventory

You are auditing the existing Trading Agent system from this migration
workspace.

## Locations

- Candidate canonical dashboard: `./trading-dashboard`
- Research/trading backend: `./crypto-research`
- Existing deployment candidate: `./legacy-trading-agent`
- NTA-1 baseline: `docs/nta-1/`
- Upgrade plan: `docs/upgrade-plan/`

## Scope and prohibitions

This phase is read-only except for writing the final audit inside this migration
repository.

Do not edit a linked project, move or delete data, stop or restart processes,
change cron/systemd/PM2/supervisor/tmux/screen, change tunnels, install
dependencies, expose secrets, send any order, call any order-creation endpoint,
or enable live execution.

## Required reading

Read completely:

- `AGENTS.md`
- Each linked project's `AGENTS.md`, `CLAUDE.md`, and relevant README
- `docs/nta-1/README.md`
- `docs/nta-1/docs/architecture.md`
- `docs/upgrade-plan/UPGRADE-PLAN.md`
- `docs/upgrade-plan/BACKLOG.csv`
- `docs/upgrade-plan/acceptance-gates.md`
- `docs/upgrade-plan/contract-catalog.md`

## Audit tasks

1. Inventory processes and deployments:
   - PID, start time, command, cwd, repo root, commit, port, environment source,
     and data root.
   - Prove which repo serves port 3002.
   - Inventory cron, systemd user/system units, PM2, supervisor, tmux, screen,
     and any application scheduler.

2. Inventory execution safety:
   - Trace every code path able to submit, cancel, or modify an order.
   - Identify every broker/exchange adapter and account mode.
   - Inspect all live-enablement flags without printing their values when they
     may contain secrets.
   - Confirm the effective mode and whether live order submission is blocked.

3. Map sources of truth and ownership:
   - Reports, decisions JSONL, SQLite, scratchpads, memory, models, paper
     portfolio, order/fill state, dashboard adapters, and app write paths.
   - Identify which component wrote the newest record of each kind.

4. Reconfirm known findings:
   - Dashboard chooses an unrelated latest JSON report.
   - Market and signal APIs fail.
   - Decision total is limited to 50.
   - Confidence scales differ.
   - Frontend assets/signals drift from backend data.
   - Capability score is hard-coded.
   - ADA, AVAX, DOT, LINK, or MATIC can fall through to a stock broker.
   - Circuit-breaker function is duplicated.
   - Backend dependency reproduction is incomplete.
   - A running process is not producing fresh market reports.

5. Run safe checks where already supported:
   - Git status and recent commit identity.
   - Dashboard build, typecheck, lint, and existing tests.
   - Existing backend unit and smoke tests that cannot submit orders.
   - Parse representative report and decision records.
   - Measure freshness of the newest valid business records.

Do not modify dependencies merely to make a check pass. Report skipped checks
and their reasons.

## Deliverable

Create `docs/audits/phase-0-runtime-inventory.md` containing:

- Executive summary
- Runtime process inventory
- Repo and deployment map
- Data-flow map
- Scheduler map
- Execution-path inventory
- Source-of-truth matrix
- Confirmed findings
- Findings not confirmed
- New risks
- Phase 1 file-impact list
- Recommended canonical repo, dashboard, URL, and data root
- Proposed backup/restore and rollback commands, not executed
- Go/no-go decision for consolidation
- Redacted command evidence
- Final Git status for this workspace and all linked repos

Stop after the report. Do not implement Phase 1.
