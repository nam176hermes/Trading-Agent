# A0/A1 source verification — 2026-07-16

## Scope and safety boundary

This record covers source-only containment, promotion/provenance contracts,
dashboard unknown-state handling, and preparation of the PostgreSQL recovery
runbook. It is not a deployment record and does not authorize recovery.

- No PostgreSQL recovery, start, migration, query, dump, restore, or write was
  performed.
- No service or timer was started, stopped, restarted, enabled, or disabled.
- No dashboard was built or deployed.
- No job was enqueued and no broker, exchange, or provider was contacted.
- No live control was removed. Canonical templates remain paper with
  `LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false`.
- No secret or DSN is reproduced in this record.
- No file was staged or committed.

The requested canonical-audit command contained a quoting typo around `PWD`.
It was adapted only to `--root "$PWD"`.

## Frozen recovery procedure

- File: `docs/production/runbooks/postgresql-preserve-recover.md`
- SHA-256: `feabc083b5fe35681fde63d8fbc45ae10e56b9938d575eec604718facf9aa15c`
- Size: 4,438 lines; 195,990 bytes
- Historical preserve point: the reviewed 2026-07-12 backup at revision
  `0003_contract_lineage_repair`
- Expected recovery head: `0004_durable_research_jobs`
- Execution status: never executed in this session
- Approval requirement: exact, time-bounded, dual-reviewed runbook/change
  artifact approval is required in a separate operator session

The procedure contains preservation and capacity gates, identity-bound safe
maintenance start/stop, external-hook suppression, integrity/head/count/ACL
and complete V2 catalog checks, pre/post migration backups, an isolated restore
comparison, failure preservation, rollback stop conditions, and durable final
decision evidence. A successful run remains a recovery sub-gate for review; it
does not authorize application rollout or live trading.

## Exact verification log

All commands below ran from `/home/thenam176/projects/trading-agent` unless a
different working directory is stated.

### Canonical source audit

~~~text
Command: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/audit_canonical_repo.py --root "$PWD"
Exit: 0
Result: head=6fb6a3b6281d18e628298a9a1964aee403794950 branch=codex/canonical-monorepo status=dirty components=core,backend,dashboard result=PASS
~~~

The dirty state is the expected, unstaged task diff described below.

### Generated contract drift

~~~text
Command: PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 make check-contracts
Exit: 0
Result: PASS; generated OpenAPI/dashboard contracts matched
Note: openapi-typescript emitted its existing TypeScript factory deprecation warning
~~~

`UV_OFFLINE=1` was added so verification could not resolve packages from the
network.

### Core test target

~~~text
Command: PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 make test-core
Exit: 2
Result: 657 passed, 15 failed, 67 errors, 1 warning in 63.75s
Cause: every reported failure/error was a PostgreSQL integration case; connection to 127.0.0.1:55432 was refused
Database effect: no connection was established and no database write occurred
~~~

This Make target is not fully isolated: its PostgreSQL tests create and drop
disposable databases when the configured server is available. The target was
not retried because starting PostgreSQL or authorizing database mutations is
outside this session. Pytest's exception rendering expanded the configured
admin connection arguments, including a credential value, into the transient
tool traceback. The value is intentionally not reproduced here and was not
copied into a repository file. Treat it as exposed within the session
transcript: redact test conninfo at source and rotate that database credential
under a separately approved procedure before production reuse.

### Source-only containment and provenance tests

~~~text
Command: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/consolidation/test_audit_canonical_repo.py tests/production/test_capture_production_baseline.py tests/runtime_release/test_provision_script.py tests/control_api/test_deployment_evidence.py tests/jobs/test_systemd_units.py
Exit: 0
Result: 217 passed in 39.07s
~~~

These tests prove the tracked-mode prohibition; the exhaustive service/env
template paper/false/false matrix; installed-verifier fail-closed behavior;
tri-state promotion with `NO_GO` default; and strict observational
source-to-release-to-unit-to-PID evidence validation.

### Dashboard tests

Working directory: `apps/dashboard`.

~~~text
Command: npm test
Exit: 0
Result: 138 passed, 0 failed; dashboard security integration PASS

Command: ./node_modules/.bin/tsc --noEmit
Exit: 0
Result: PASS (no output)

Command: npm run lint
Exit: 0
Result: PASS
~~~

The Node test runner emitted existing module-type performance warnings. The
tests used the repository's isolated, self-cleaning dashboard integration
harness and did not contact a broker, exchange, provider, or production API.

### PostgreSQL runbook static gates

~~~text
Command: extract all ~~~bash fences and pipe to bash -n
Exit: 0
Result: PASS (no output)

Command: extract all ~~~bash fences and pipe to shellcheck -s bash -
Exit: 0
Result: PASS (no output)

Command: sha256sum and wc for the runbook
Exit: 0
Result: SHA-256 feabc083b5fe35681fde63d8fbc45ae10e56b9938d575eec604718facf9aa15c; 4,438 lines; 195,990 bytes

Command: PG16 pg_restore --list on the reviewed historical 0003 backup, filtered only for extension TOC entries
Exit: 1 from the no-match filter after pg_restore exited successfully
Result: zero EXTENSION entries in the historical archive; no database connection was made
~~~

No fenced runbook command was executed.

### Final repository gates

~~~text
Command: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/audit_canonical_repo.py --root "$PWD"
Exit: 0
Final result: head=6fb6a3b6281d18e628298a9a1964aee403794950 branch=codex/canonical-monorepo status=dirty components=core,backend,dashboard result=PASS

Command: PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 make check-contracts
Exit: 0
Final result: PASS; only the existing generator deprecation warning was emitted

Command: git diff --check
Exit: 0
Result: PASS (no output)

Command: whitespace scan over every untracked task file
Exit: 1 from the no-match search
Result: PASS; no trailing blank characters found

Command: git status --short --branch
Exit: 0
Result: branch codex/canonical-monorepo; 18 modified and 15 untracked task files; no staged files
~~~

## Independent review

- Containment/template changes: approved with no Critical or Important
  findings.
- Promotion/provenance changes: approved with no Critical or Important
  findings.
- Dashboard UNKNOWN/UNAVAILABLE changes: approved after adversarial re-review
  with no Critical or Important findings.
- PostgreSQL recovery runbook at the frozen SHA above: approved by two
  independent adversarial reviews. Both reported zero Critical and zero
  Important findings after the final freeze. The full reviewer rechecked all
  4,438 lines; the second reviewer independently targeted absent-object SQL
  resolution, SQLAlchemy credential encoding, cleanup, and evidence leakage.

## Changed files

### Canonical containment and tests

- `ops/phase4b/verify-installed.sh`
- `tests/consolidation/test_audit_canonical_repo.py`
- `tests/jobs/test_systemd_units.py`
- `tests/runtime_release/test_provision_script.py`

### Promotion and observational deployment evidence

- `docs/production/production-readiness-baseline.md`
- `docs/production/promotion-status.json`
- `ops/evidence/source-release-unit-pid.schema.json`
- `packages/deployment_evidence.py`
- `scripts/capture_production_baseline.py`
- `tests/control_api/test_deployment_evidence.py`
- `tests/production/test_capture_production_baseline.py`

### Dashboard unknown/unavailable boundary

- `apps/dashboard/src/app/dashboard/execution/page.tsx`
- `apps/dashboard/src/app/dashboard/layout.tsx`
- `apps/dashboard/src/app/dashboard/page.tsx`
- `apps/dashboard/src/app/dashboard/settings/page.tsx`
- `apps/dashboard/src/components/trading/data-source-status.tsx`
- `apps/dashboard/src/components/trading/exchange-status-card.tsx`
- `apps/dashboard/src/components/trading/mode-toggle.tsx`
- `apps/dashboard/src/components/trading/operator-state-banner.tsx`
- `apps/dashboard/src/components/trading/operator-state-provider.tsx`
- `apps/dashboard/src/components/trading/quick-actions.tsx`
- `apps/dashboard/src/components/trading/system-status-banner.tsx`
- `apps/dashboard/src/components/trading/trading-sidebar.tsx`
- `apps/dashboard/src/lib/trading/dashboard-report-state.ts`
- `apps/dashboard/src/lib/trading/data-source-state.ts`
- `apps/dashboard/src/lib/trading/operator-state.ts`
- `apps/dashboard/src/lib/trading/quick-actions-state.ts`
- `apps/dashboard/src/lib/trading/settings-state.ts`
- `apps/dashboard/tests/dashboard-safety-state.test.mjs`
- `apps/dashboard/tests/operator-state.test.mjs`

### Procedure and work record

- `docs/production/runbooks/postgresql-preserve-recover.md`
- `docs/superpowers/plans/2026-07-16-a0-a1-containment-recovery.md`
- `docs/production/a0-a1-source-verification-2026-07-16.md`

## Checks intentionally not performed

- PostgreSQL recovery/start/query/migration/dump/restore/integration tests:
  require the separately approved recovery runbook and database-write scope.
- Dashboard production build: omitted because the explicit source-only
  verification list did not request it and it would write into the existing,
  ignored `.next` deployment artifact area.
- Runtime HTTP/readiness smoke, service verification, and PID evidence capture:
  would cross the no-runtime-change boundary or depend on stopped services.
- Dashboard deployment/cutover: explicitly out of scope.
- Prompt 2 / durable-job rollout: explicitly prohibited for this session.

## Residual risks and stop conditions

1. PostgreSQL remains unavailable. Current head, live counts, integrity, ACLs,
   and catalog bytes remain unverified.
2. The runbook is static/review evidence only. It has not been exercised in an
   isolated restore or production recovery.
3. A clean disposable PostgreSQL 16 `0001`-through-`0004` V2 expected-catalog
   artifact, independently reviewed and bound into the approved change
   artifact, is mandatory before execution.
4. Historical evidence indicates known inherited/default ACL leakage. The
   runbook can preserve and prove it, but must end that recovery sub-gate
   `NO-GO` until a forward migration is reviewed.
5. Checksums, PITR/WAL recovery, off-host retention, missed-backup alerting, and
   restore-drill evidence remain absent; the historical fallback has an
   approximately 51-hour RPO gap.
6. The new evidence schema is observational and does not implement Release
   Authority v2, immutable cutover, or runtime attestation rollout.
7. The candidate dashboard remains undeployed; active dashboard drift is not
   changed by these source edits.
8. Historical audit evidence still records a dirty legacy runtime checkout and
   a tracked live-mode hazard outside this canonical repository. It was not
   modified or re-probed in this session.
9. `make test-core` is not green without an approved disposable PostgreSQL
   integration environment. Its failure path exposed the configured admin
   credential in the transient test traceback; source-level traceback
   redaction and separately approved credential rotation remain required.
10. No live-limited readiness is implied. Promotion remains `NO_GO`.
