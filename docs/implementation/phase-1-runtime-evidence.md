# Phase 1 Runtime Evidence

Captured 2026-07-11 03:56–04:00 America/Toronto.

## Identity and state

| Item | Evidence |
|---|---|
| Agent | `trading-agent.service`, PID 4180316, cwd `/home/thenam176/.hermes/crypto-research`, active |
| Dashboard | `trading-dashboard.service`, PID 4183789, cwd `/home/thenam176/.hermes/trading-agent`, active |
| Listener | `0.0.0.0:3002`, public `/api/meta` HTTP 200 |
| Dashboard commit identity | `a130303114508b5e3fd5ea44eb8d6234b47d6cf8` plus pre-existing dirty-worktree warning in the evidence set |
| Requested/effective mode | paper / paper |
| Execution capability | NON_LIVE |
| Environment gates | `LIVE_EXECUTION_ENABLED=false`; `LIVE_TRADING_APPROVED=false` |
| Kill switch | INACTIVE after successful drill |
| Research freshness | latest valid market report 2026-06-25 04:54:37Z; STALE |
| Live-price heartbeat | current on 2026-07-11; separate from research health |

## Runtime safety smoke

- Unauthenticated local `POST /api/trading/mode` returned `503 CONFIGURATION_ERROR`; `.mode` remained paper.
- Public `GET https://tradingcompanydirect.com/api/meta` returned 200 and no secret.
- Reconciliation and signal-quality GET returned 200 from read-only implementations.
- Canonical kill-switch drill produced an active-agent log: `KILL SWITCH ACTIVE — deactivate the canonical sentinel to resume`.
- During and after the drill: orders = 30, trades = 0. These match the prechange counts, proving no order/trade record was created.
- After clearing the sentinel, the agent restarted and logged `Mode loaded: requested=paper effective=paper` and `Paper mode — no exchange connection needed`.

No real order was submitted, modified, or cancelled.
