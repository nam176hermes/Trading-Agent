# Phase 1 Expanded-Scope Prechange Checkpoint

Checkpoint time: 2026-07-11, after the initial safety-first patch and before implementing the expanded Phase 1 requirements supplied in the current task.

## Repository identity

| Surface | Path | Branch | Base commit | Current state |
|---|---|---|---|---|
| Candidate dashboard | `/home/thenam176/projects/trading-dashboard` | `codex/phase-1-safety` | `c7c2ba7abc1a9a00ae48270952b9c370d977ec60` | Phase 1 files modified/untracked; clean before the initial Phase 1 patch |
| Active backend | `/home/thenam176/.hermes/crypto-research` | `master` | `1f25fee78f9c4860e2a9211ce5f42baebd953703` | Runtime-heavy dirty worktree plus scoped Phase 1 changes |
| Active legacy dashboard | `/home/thenam176/.hermes/trading-agent` in `/home/thenam176/.hermes` | `main` | `8fd40a64ef9452f1e1f0d4db64e45cf845e6933a` | Pre-existing deletions/untracked audit files plus scoped Phase 1 changes |
| Migration workspace | `/home/thenam176/projects/trading-agent-migration` | `prep/codex-migration` | `392973875d8189369a020b7d7ef3994a72ac9751` | Documentation untracked |

No reset, clean, checkout-overwrite, commit, or history rewrite is authorized or planned.

## Runtime and service state

| Service | State | PID | Working directory | Command |
|---|---|---:|---|---|
| `trading-agent.service` | active | 4153289 | `/home/thenam176/.hermes/crypto-research` | `.venv/bin/python trading_agent.py` |
| `trading-dashboard.service` | active | 4153224 | `/home/thenam176/.hermes/trading-agent` | `npx next start -p 3002 -H 0.0.0.0` |

Port 3002 is owned by the active legacy Next.js deployment. Cloudflare routing is unchanged.

Current requested mode file: `paper`.

Current canonical root sentinel: absent, therefore the existing implementation reports inactive.

Environment names observed without values:

- Agent: `LIVE_EXECUTION_ENABLED`, `TRADING_MASTER_KEY`, Telegram-related names.
- Dashboard: no trading authentication or master-key variables.

`LIVE_TRADING_APPROVED` and `TRADING_KILL_SWITCH_PATH` are not yet present in the active agent environment. These are expanded-scope gaps.

## Configuration checkpoint

Checksums:

```text
trading-agent.service    b4a9932366e8d386f34b25be80d64f2c8450cbdb04d7f896a57d7f7a2ad1f891
trading-dashboard.service 85646fc774ab0022dd521dc1913b881f63489ca8201099b65af19cb298581288
phase-1 scoped backup    0ca221c1975bf7ebd116cb504c045b13d72eff5d787521c1f26048b1b02de12d
```

Secure backup: `/home/thenam176/.local/share/trading-agent-backups/phase-1-prechange-20260711.tgz`, mode 0600.

Current permissions:

- Backend directory: 0700.
- `.env`, `.mode`, `.keys.enc`: 0600.
- Trading systemd units: 0600.
- Credential-bearing OpenClaw override discovered in Phase 0: 0600.

## Mutation-route inventory

All active dashboard route handlers declaring POST are listed below. No PUT, PATCH, or DELETE route handler was found.

| Route | Classification | Current guard | Expanded-scope action |
|---|---|---|---|
| `/api/trading/plan` | MUTATION_LOW_RISK | `requireMasterKey` | Move to one shared mutation policy and audit |
| `/api/trading/watchlist` | MUTATION_LOW_RISK | `requireMasterKey` | Shared policy, validation, audit |
| `/api/trading/run` | MUTATION_EXECUTION_SENSITIVE | `requireMasterKey` | Shared policy, idempotency/audit; remains paper research only |
| `/api/trading/service` | MUTATION_EXECUTION_SENSITIVE | `requireMasterKey` | Shared policy and strict action allowlist |
| `/api/trading/mode` | MUTATION_EXECUTION_SENSITIVE | `checkAuth` | Structured live-block response, dual-gate status, audit, atomic 0600 write |
| `/api/trading/kill-switch` | MUTATION_EXECUTION_SENSITIVE | `checkAuth` | Shared policy, canonical resolver, atomic/synced mutation, audit and re-read |
| `/api/trading/close-position` | MUTATION_EXECUTION_SENSITIVE | `requireMasterKey` | Shared policy; paper-only proof |
| `/api/trading/update-stop` | MUTATION_EXECUTION_SENSITIVE | `requireMasterKey` | Shared policy; paper-only proof |
| `/api/trading/keys` | SECRET_MANAGEMENT | `requireMasterKey` | Shared policy; no credential connectivity test in Phase 1 verification |

The initial patch made both helpers fail closed when their configuration is absent, but the policy is still split across two credentials. The expanded requirement calls for one server-side policy for every mutation.

The client `AuthGuard` no longer authorizes after timeout or fetch failure. It remains UX only.

## Execution-path inventory

Real-order sinks reachable in maintained code:

1. Active service path: `trading_agent.py::_execute_live` -> `OrderExecutor.execute` -> `ExchangeAdapter.create_order`; optional OCO uses `create_oco_order`.
2. Legacy research path: `main.py` -> `execute_live.live_execute` -> `exchange.ccxt_bridge.place_order` -> adapter order creation.
3. Stock broker path: `main.py` -> `broker.execute` -> `broker.place_order` -> Alpaca HTTP order endpoint.

Backtest-only `place_order` call sites are separate simulation paths.

The active service path currently checks only `LIVE_EXECUTION_ENABLED`. It lacks the independent `LIVE_TRADING_APPROVED` gate and centralized structured policy required by the expanded scope.

## Kill-switch inventory

The initial patch moved both primary components to the root sentinel:

`/home/thenam176/.hermes/crypto-research/.kill_switch`

Remaining gaps:

- No shared `TRADING_KILL_SWITCH_PATH` resolver.
- Dashboard and Python still implement path resolution independently.
- Read/permission error semantics are not fail-closed; Python currently propagates or treats absence only.
- `src/app/api/trading/execution/route.ts` still embeds a direct `.kill_switch` check.
- Mutation is not yet atomic/fsynced/audited with a shared state contract.

## Files expected to change

Active legacy dashboard:

- `src/lib/trading/auth.ts` and a shared mutation/audit/kill-switch helper.
- All nine mutation route handlers listed above.
- `src/components/trading/auth-guard.tsx` and auth integration tests.
- Mode, kill-switch, meta, reconciliation, signal-quality routes and tests.
- Dashboard identity/status UI.

Active backend:

- `trading_agent.py`.
- New centralized live-execution policy module.
- `kill_switch.py`.
- `asset_registry.py`, `broker.py`, and phase-1 tests.
- `safety_engine.py` regression coverage only; thresholds remain unchanged.
- `pyproject.toml`, constraints/lock material, Python-version/system-dependency documentation, and an offline verification script if supported.

Candidate dashboard:

- Legacy report catalog/runtime contract layer, routes, shared confidence formatter, canonical action normalization, capability evidence, UI state components, meta route, and integration tests.

Migration documentation:

- Required implementation, runtime, known-limitation, asset-registry, dashboard-recovery, safety-containment, test-evidence, rollback, and ADR files.

Systemd/config:

- Prefer new drop-ins and protected environment files instead of further editing vendor/base units.

## Rollback assumptions

- The scoped tar archive is the source-level/config checkpoint for files changed by the initial patch.
- Unit/drop-in state and checksums will be captured again before any expanded-scope runtime change.
- Candidate remains undeployed; no port/tunnel cutover is part of this task.
- If rollback removes the hard live policy, `trading-agent.service` must remain stopped or without execution credentials until the safe code is restored. Dashboard kill switch alone is not an acceptable rollback safety boundary.
- `.mode` must remain `paper` throughout implementation and rollback.
