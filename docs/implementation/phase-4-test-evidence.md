# Phase 4 Test Evidence

## Evidence status

This document records only checks observed during the Phase 4 implementation
session. It is not the final Phase 4 acceptance report. Commands marked
`PENDING` or `NOT RUN` must not be inferred to have passed.

## Verified migration and database evidence

Before revision `0004_durable_research_jobs` was applied, a custom-format
PostgreSQL backup was created outside Git:

```text
path: /home/thenam176/.local/share/trading-agent-backups/phase4-preapply-20260712T131219Z.dump
mode: 0600
size: 31,823,453 bytes
sha256: 1f4b64bd74811ec9977befd1590e59872c3a32b9988686c08070756cc97798f8
```

The session restore drill restored that pre-apply dump into a temporary
database and verified the pre-Phase-4 state: Alembic
`0003_contract_lineage_repair`, 20 application tables excluding
`alembic_version`, 43,055 canonical rows and 222 quarantine rows. The temporary
restore database was then removed. The runtime apply subsequently reached
`0004_durable_research_jobs` without changing the canonical or quarantine
counts.

A fresh read-only runtime query in this documentation task observed:

```text
role: trading_reader
revision: 0004_durable_research_jobs
application tables excluding alembic_version: 26
canonical rows: 43,055
quarantine rows: 222
jobs: 0
job_attempts: 0
job_events: 0
job_artifacts: 0
scheduler_heartbeats: 0
worker_heartbeats: 0
```

The `trading_jobs` permission smoke was exercised transactionally. The role
could use its approved queue DML/revision-read surface, while DDL, job/event
DELETE, job TRUNCATE and event UPDATE were rejected. The owner-level
append-only trigger separately rejected event UPDATE and DELETE. Test records
were rolled back; all six Phase 4 tables remain empty. No owner credential is
used by an application service.

## Verified automated checks

| Scope | Evidence | Status |
|---|---|---|
| Job implementation regression at commit `89724be` | `uv run pytest -q tests/jobs` | PASS, 455 tests; one pre-existing Starlette deprecation warning |
| Exact Job API/OpenAPI contract repair | Contracts, API auth/security and generation tests | PASS, 88 tests |
| Contract generation | `uv run python scripts/generate_contracts.py --check --dashboard-root ../trading-dashboard` | PASS |
| Contract isolation | Control API OpenAPI and the two dashboard-generated Control API files compared with pre-change copies | PASS, byte-identical |
| Contract review | Success/error envelopes, status codes, exact type/payload pairing and asset allowlist | CLEAN after repair |
| Research backend immutable/semantic suite | Offline fixture suite at backend `51de1cf` | PASS, 178 tests; 2 intended skips; final integrity review CLEAN |
| Final repository-wide suite | `uv run pytest -q` after backend repin | PASS, 559 tests; one Starlette dependency deprecation warning |
| Final Alembic/compile chain | contract `--check`, `uv run alembic current`, `compileall`, `git diff --check` | PASS; runtime head `0004_durable_research_jobs` |
| Backend isolated integration | Temp Git archive plus two read-only hashed legacy report fixtures; all outputs redirected to temp | PASS, 43/43; source hashes unchanged |
| Phase 1 safety regression | asset registry, broker, live policy, paper trader and safety suites | PASS, 85; 2 intended connectivity skips |
| Scheduler focused suite at commit `26754a7` | Injected clock plus disposable PostgreSQL repository tests | PASS, 12 tests |
| Dashboard isolated worktree | Final `843d449` unit/integration, TypeScript, lint, build, process-boundary and client-bundle scans | PASS: 67 Node tests plus security integration; typecheck, zero-finding lint, production build and scans passed; focused re-review CLEAN; not deployed |
| Systemd unit syntax/runtime path | `systemd-analyze --user verify` against all four unit files | BLOCKED: fixed `/opt/trading-agent-phase4/releases/phase4-0001/.venv/bin/python3.11` does not exist |

The isolated dashboard evidence includes successful production build and zero
bundle hits for the Job API token, process bridge or shell boundary. The final
67-test count replaces obsolete Python-bridge tests with explicit zero-process
and typed-retirement coverage. None of that evidence means the active
dashboard was restarted, deployed or cut over.

## Final verification still required

| Acceptance command or check | Status |
|---|---|
| Fresh repository-wide `uv run pytest -q` after all Phase 4 commits | PASS, 559 tests |
| Final `uv run alembic current` command evidence | PASS, `0004_durable_research_jobs (head)` |
| Final backend isolated integration target (`43/43`) | PASS in disposable archive; no active output write |
| Final Phase 1 safety target (`85` pass, `2` intended connectivity skips) | PASS |
| Final isolated dashboard `npm test`, typecheck, lint and build | PASS; not deployed |
| Job API live/ready/auth/enqueue/list/detail/cancel runtime smoke | NOT RUN; service absent |
| Worker claim, real service heartbeat, cancellation and timeout smoke | NOT RUN; service absent |
| Scheduler runtime slot/dedup heartbeat observation | NOT RUN; timer absent |
| Controlled real-output `SNAPSHOT` | NOT RUN |
| Rollback drill involving Phase 4 services | NOT RUN; services were never installed |

No test result in this document proves a production job completed, proves a
runtime heartbeat exists, or authorizes a scheduler timer.
