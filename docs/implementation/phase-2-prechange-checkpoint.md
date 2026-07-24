# Phase 2 Pre-change Checkpoint

Captured: 2026-07-11T09:44:35-04:00 (America/Toronto).

## Repository matrix

| Surface | Path | Branch | Commit | Status |
|---|---|---|---|---|
| Migration workspace | `/home/thenam176/projects/trading-agent-migration` | `prep/codex-migration` | `1da694fd80f8f0b390e29045956cdd98ee31a1a0` | clean before this document |
| Candidate dashboard | `/home/thenam176/projects/trading-dashboard` | `codex/phase-1-safety` | `8760b0a0aa0bc7fa43e128d5cf935f264ce9eaf7` | clean |
| Legacy backend/data root | `/home/thenam176/.hermes/crypto-research` | `master` | `c9763076f8179e2f6498d154a0a893f25d74dcc6` | 1,842 pre-existing/runtime entries; read-only in Phase 2 |
| Active legacy dashboard | `/home/thenam176/.hermes` | `main` | `a130303114508b5e3fd5ea44eb8d6234b47d6cf8` | 577 repo-wide pre-existing/runtime entries; untouched in Phase 2 |

The two repositories changed by Phase 2 are already on non-main feature/preparation branches. No reset, clean, checkout-overwrite, or legacy-data rewrite is permitted.

## Runtime service matrix

| Service | State | PID | Working directory |
|---|---|---:|---|
| `trading-agent.service` | active | 4181928 | `/home/thenam176/.hermes/crypto-research` |
| `trading-dashboard.service` | active | 4183789 | `/home/thenam176/.hermes/trading-agent` |

The active legacy dashboard owns port 3002. Phase 2 will not stop, restart, replace, or repoint either service or the Cloudflare route.

## Safety invariants

| Invariant | Baseline |
|---|---|
| Requested mode | `paper` |
| Effective mode | `paper` (Phase 1 runtime evidence and active service configuration) |
| `LIVE_EXECUTION_ENABLED` | configured in active agent environment; value must remain false |
| `LIVE_TRADING_APPROVED` | configured in active agent environment; value must remain false |
| Canonical kill switch | absent, therefore `INACTIVE` |
| Orders | 30 |
| Trades | 0 |

Only environment variable names were inspected. No secret value was read or recorded.

## Legacy source inventory

| Source | Role | Baseline |
|---|---|---|
| `reports/report_*.json` | Historical market reports | latest valid semantic timestamp `2026-06-25T04:54:37.766581+00:00`; 10 assets; stale |
| `memory/decisions.jsonl` | Historical decisions | 29,943,886 bytes; latest write 2026-06-25; expected total 16,653 |
| `memory/typed_decisions.jsonl` | Larger typed-decision archive | 109,426,014 bytes; optional/read-only |
| `memory/trading.db` | Current operational heartbeat and counts | 68,186,112 bytes; orders 30; trades 0 |
| `live_prices.json` | Current ephemeral live-price heartbeat | updates continuously |
| Capability evidence | Legacy benchmark artifacts if present | no current evidence implies `UNKNOWN` |
| Cost evidence | Scratchpad/call-count-derived data | must be labeled `ESTIMATED` or `UNKNOWN`, never exact by default |

The API receives the root only from `TRADING_DATA_ROOT`. Domain code must not embed the path above.

## Baseline validation

Candidate dashboard commands run before Phase 2 edits:

- `npm test`: PASS.
- `npx tsc --noEmit`: PASS.
- `npm run lint`: PASS with zero errors.
- `npm run build`: PASS on Next.js 16.2.6; five pages and ten existing API routes generated.

The build emitted structured warnings for invalid legacy market reports, which are expected from the Phase 1 adapter and must move behind the Control API boundary.

## Contract scope and planned files

The migration workspace will gain a Python package containing strict Pydantic contracts, read-only repositories, FastAPI application, deterministic schema generation, tests, and Phase 2 documentation. Generated OpenAPI will remain in the migration workspace. The candidate dashboard will gain generated TypeScript/Zod artifacts, a Control API client, isolated legacy fallback, and same-origin read proxies.

The active backend and active legacy dashboard are read-only inputs and rollback surfaces. They are not implementation targets for Phase 2.

## Rollback assumptions

1. `CONTROL_API_ENABLED=false` selects the isolated Phase 1 legacy adapter in the candidate dashboard.
2. Stopping the localhost-only Control API cannot affect port 3002 or `trading-agent.service`.
3. No migration or write occurs, so rollback never restores legacy data.
4. Live gates and canonical kill-switch semantics are untouched.
5. Dirty legacy repositories are never reset.
