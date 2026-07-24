# Phase 4 Pre-change Checkpoint

Captured `2026-07-12T00:08:14-04:00` (`America/Toronto`) before Phase 4
ADRs, schema, Job Command API, worker, scheduler, dashboard integration, systemd
units, or runtime changes.

This checkpoint was read-only. It did not start or restart a service, enable a
timer, call an exchange or broker, read an exchange credential, submit or
cancel an order, mutate PostgreSQL/SQLite, or call a dashboard mutation route.

## Repository identity and worktree state

| Scope | Branch | Commit | Pre-existing state |
|---|---|---|---|
| Migration main | `main` | `19f4f52c1085bed6e009a653b16ea26398cc7aef` | Clean tracked tree; one pre-existing untracked path, `docs/audits/` |
| Phase 4 worktree | `codex/phase-4-durable-jobs` | `19f4f52c1085bed6e009a653b16ea26398cc7aef` | Clean |
| Candidate dashboard | `codex/phase-2-control-api` | `ef6fffcc99626a67438170a3448a1519e23acae6` | Clean |
| Research backend | `master` | `0b977fe110f2fd66c3bc7e981b8531cb5dd7a8ac` | Pre-existing: 22 tracked changes and 1,820 untracked files |
| Active dashboard parent | `main` | `a130303114508b5e3fd5ea44eb8d6234b47d6cf8` | Pre-existing within `trading-agent/`: five tracked changes and seven untracked paths |
| Dashboard security worktree | `codex/security-phase4-hardening` | `be9e7eef5f84dd04f37aabc62e00d98cfb40afe3` | Clean; reviewed but not merged or deployed |

The research backend tracked changes include runtime state, signals, models,
and three source files (`ml_predictor.py`, `run_arena_round.py`, and
`train_ensemble.py`). The active dashboard tracked changes are its modified
`.gitignore` and four deleted legacy components. These user/runtime changes are
not Phase 4 inputs and must not be reset, cleaned, reformatted, or committed by
Phase 4.

## Runtime safety baseline

| Invariant | Observed |
|---|---|
| Requested mode | `paper` |
| Effective mode | `paper` |
| Execution capability | `NON_LIVE` (`MODE_NOT_LIVE`) |
| `LIVE_EXECUTION_ENABLED` | `false` |
| `LIVE_TRADING_APPROVED` | `false` |
| Canonical kill switch | `INACTIVE` |
| SQLite orders / trades | `30 / 0`, read using URI `mode=ro` |
| `trading-agent.service` | active, main PID `4181928` |
| `trading-dashboard.service` | active, main PID `4183789` |
| Dashboard listener | `0.0.0.0:3002`, Next PID `4183820` |
| Cloudflared | PID `3283180` |

Only the two named live-gate variables were read from the active agent process
environment. No other process environment value was printed.

**PRE-CHANGE SAFETY GATE: PASS.** Any later mismatch requires stopping Phase 4
and investigating before applying schema, starting services, or enabling a
timer.

## PostgreSQL baseline

| Check | Observed |
|---|---|
| Endpoint | `127.0.0.1:55432` |
| Alembic | `0003_contract_lineage_repair` |
| Application tables | `20`, excluding `alembic_version` |
| Reader default transaction | read-only `on` |
| Canonical rows | `43,055` |
| Quarantine rows | `222` |

The checkpoint used the protected reader configuration and did not print a
password or DSN. Phase 4 must use a new Alembic revision and must not rewrite
the Phase 3/3B migration runs or canonical research entities.

## Existing scheduler and Phase 4 service state

| Surface | Observed |
|---|---|
| Matching user timers | `0` |
| Matching system timers | `0` |
| Matching user cron entries | `0` |
| `127.0.0.1:8401` listener | absent |
| `trading-job-api.service` | not found / inactive |
| `trading-job-worker.service` | not found / inactive |
| `trading-job-scheduler.service` | not found / inactive |
| `trading-job-scheduler.timer` | not found / inactive |

Existing `trading-pipeline.sh`, `trading-cron-tick.sh`, and
`trading-debate.sh` files are not scheduled. They background multiple commands,
use shell expansion, and are not approved Phase 4 worker entry points.

## Existing dashboard run boundary

The active dashboard route
`src/app/api/trading/run/route.ts` currently:

- reads `run_status.json` for GET and before POST;
- writes `running` state through temporary-file rename;
- spawns `.venv/bin/python main.py --mode debate` as a detached process;
- updates `run_status.json` from an in-memory child exit callback;
- returns process state and PID rather than a durable canonical job.

This read/check/write sequence is not an atomic lock and is not durable across
dashboard process failure. It is the Phase 4 replacement target, not an
approved fallback.

`src/app/api/trading/pipeline-status/route.ts` also reads `run_status.json`.
After Phase 4 integration, normal dashboard status must come from the Job API;
the file must remain unchanged or become archive-only.

The existing file itself is old runtime state:

```text
path: /home/thenam176/.hermes/crypto-research/run_status.json
mode: 0664
size: 244 bytes
mtime: 2026-05-16T23:21:56.108704975-04:00
sha256: 465457bb4064881137d36d132e5b7463c905fca065115e5720ad1f35a3c76d66
```

Phase 4 must not update or delete it during implementation or rollback.

## Existing process-spawn and command surfaces

Active dashboard server routes contain two actual `spawn()` call sites:

1. `/api/trading/run`: detached `main.py --mode debate`; Phase 4 must remove it.
2. `/api/trading/close-position`: paper-position command; outside Phase 4 job
   types and must not be called or broadened by this phase.

No `exec`, `execFile`, or other research-job process boundary is approved in
the dashboard. The reviewed security branch already removes shell execution
from key management but is not yet deployed.

## Approved backend CLI entry points

`main.py` exposes audited fixed modes through `--mode`:

| Phase 4 job | Existing fixed CLI entry point | Scheduling policy |
|---|---|---|
| `SNAPSHOT` | `.venv/bin/python main.py --mode snapshot` | automatic at UTC minute `00` and `30`, plus operator manual |
| `DEBATE` | `.venv/bin/python main.py --mode debate --symbol <registered asset>` | operator manual only |
| `REPLAY` | `.venv/bin/python main.py --mode replay --session <validated identifier>` | operator manual only |
| `BACKTEST` | `.venv/bin/python main.py --mode backtest --symbol <registered asset>` | operator manual only |

This table approves semantic entry points, not literal implementation argv.
The Phase 4 command ADR and registry must reconcile the user-specified typed
payloads with the actual CLI: the client may never supply executable, module,
script, cwd, environment, output path, timeout, or arbitrary argv.

## Legacy output roots

| Relative path | Current files | Directory mode | Phase 4 treatment |
|---|---:|---:|---|
| `reports/` | 4,627 | `0755` | legacy research artifacts; result validator may reference new validated reports |
| `signals/` | 2 | `0775` | legacy output; never store raw content in jobs |
| `decisions/` | 2,255 | `0755` | legacy archive/output |
| `memory/` | 75 | `0755` | legacy operational/archive data; `run_status.json` is not job truth |
| `logs/` | 6,135 | `0755` | legacy logs; Phase 4 uses a separate protected `0700` job-artifact root |
| `models/` | 39 | `0775` | read-only for Phase 4; no training or model changes |

The future worker may write only approved research output and protected job
artifact paths. It must not make these broad legacy directory permissions the
new artifact-security policy.

## Source fingerprints

```text
active run route:
4edc3bbaea849b19ea078587cd86c90c5e72287ba850c61fb130313e19278ca8

active pipeline-status route:
a7549df85e3d5cc0bcb60e1b0f9da7fe423e361c705e0f971fee73fda97209d4

backend main.py:
cee82d5672db2b3153d6f031ed2e0990a54305bb342a5e6eb6625cd2e49be16c

legacy scheduler scripts:
trading-pipeline.sh  7e55d7918c20bf6694443d411ade03887c01e1d7e8c7e1c22d82a1e9a51f7623
trading-cron-tick.sh 7f65d31c45ac4e224cd970addc5d42c227d114acb74e5ee58b2c09d82722dd61
trading-debate.sh    33279bd55c30c0f87d7de88963e14654fa3c00bcc654882b438d2507db81c0af
```

These paths remain legacy sources. Phase 4 does not edit the backend or shell
scripts unless a later, separately reviewed integration task requires a focused
change in an isolated worktree.

## Rollback assumptions

Phase 4 rollback is forward-safe:

1. Disable the Phase 4 scheduler timer first.
2. Stop scheduler, worker, and Job API services.
3. Disable dashboard job-command controls through an explicit feature flag.
4. Do not restore dashboard process spawn or `run_status.json` writes.
5. Preserve jobs, attempts, events, heartbeats, and artifact references for
   audit; a schema downgrade is not the normal rollback.
6. Leave the read-only Control API and PostgreSQL canonical research reads
   unchanged.
7. Leave active agent/dashboard services, port 3002, Cloudflare, live gates,
   kill switch, strategy, models, prompts, orders, and trades unchanged.

No Phase 4 runtime service or timer may start until schema, idempotency, lease,
fencing, allowlist, worker safety, cancellation, timeout, result validation,
and scheduler slot tests pass.

**PHASE 4 PRE-CHANGE CHECKPOINT: PASS — ADR WORK MAY BEGIN.**
