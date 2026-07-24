# Job Plane Recovery Gate Part 2 Final Evidence

**Evidence date:** 2026-07-16
**Decision basis:** runtime approval, source review, and release authority all
retain independent blockers.

## Stage outcomes

| Stage | Outcome |
|---|---|
| A — PostgreSQL recovery | BLOCKED before first write: exact runbook `APPROVAL_RECORD` absent |
| B — runtime backup/restore | NOT EXECUTED; Stage A prerequisite absent |
| C — runtime `0005`/`0006` | NOT EXECUTED; recovery/baseline/backup and separate migration authority absent |
| D — dashboard dependency | Focused GREEN; exact dev dependency and minimal lock delta independently approved |
| E — clean candidate | NOT CREATED; two Important `0006` review findings remain |
| F — full source verification | Partial: root/release/dashboard green; clean-commit, contracts, and backend reruns not reached |
| G/H — hermetic release/manifests | NO_BUILD; builder/runtime/artifact authority blockers |
| I — exact-path systemd | NOT VERIFIED; no materialized release |

## Exact fresh verification results

```text
AJV focused RED:             3 failed in 1.03s
AJV focused GREEN:           3 passed in 1.02s
npm ci determinism:          package.json PASS; package-lock.json PASS
jobs + Alembic:              783 passed, 1 warning in 143.89s
runtime-release:             237 passed, 1 skipped in 56.19s
dashboard Node tests:        140 passed
dashboard security:          PASS
dashboard TypeScript/lint:   PASS / PASS
dashboard production build: PASS
candidate git diff --check:  PASS
```

NPM reports one low and three moderate advisories, unchanged by the direct
dev-only dependency repair. No audit fix or unrelated upgrade ran.

## Source review result

The dependency repair had no Critical or Important review finding. The full
candidate review found two Important 0006 provenance/history-validation gaps,
so no source was staged or committed. Current candidate status is 29 modified,
6 untracked, and zero staged paths at unchanged HEAD
`e7141221423cc8d4fb3acfd757275e6d9eb69140`.

## Runtime mutation ledger

Part 2 performed no PostgreSQL start/stop, SQL, dump, restore, migration, PID
or socket edit, service/timer change, Job repository call, job/SNAPSHOT,
broker/exchange/research-provider call, dashboard deployment, port/Cloudflare
change, or `/opt` installation.

Only source dependency files, ignored local dependency/build outputs, and
implementation evidence documents were written.

## Final read-only runtime recheck

The final recheck did not mutate runtime state:

| Surface | Final observation |
|---|---|
| Job API | inactive/dead, disabled, PID 0 |
| Worker | inactive/dead, disabled, PID 0 |
| Scheduler | inactive/dead, static, PID 0 |
| Scheduler timer | inactive/dead, disabled |
| Port `8401` | closed |
| PostgreSQL target port `55432` | closed |
| Existing dashboard | active on loopback port `3002`; no restart/deployment performed |
| Trading agent | active; requested/effective mode `PAPER/PAPER` |
| Live gates | `LIVE_EXECUTION_ENABLED=FALSE`; `LIVE_TRADING_APPROVED=FALSE` |
| Kill switch | `INACTIVE` |
| Orders/trades | `30/0` from the earlier read-only evidence in this recovery gate; not reopened after the stop condition |
| Job-plane rows | runtime database offline, therefore current counts are `NOT VERIFIED`; this session made no repository/SQL insert call |
| Cloudflare | existing processes remained present; no route/config command or mutation was performed |

The original repository remains dirty with pre-existing reports and the new
evidence documents. The candidate remains dirty at 29 modified and 6
untracked paths, with zero staged paths. Both worktrees pass `git diff
--check`; neither state qualifies as clean release input.
