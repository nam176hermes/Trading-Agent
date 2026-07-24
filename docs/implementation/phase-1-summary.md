# Phase 1 Safety-First Summary

Date: 2026-07-11 (America/Toronto)

## Outcome

All locally actionable Phase 1 P0 safety findings are closed. Live trading remains NO-GO. No real order was submitted, changed, or cancelled.

## Closed

- All nine legacy-dashboard mutation routes share fail-closed server authorization and audit attempts.
- Missing dashboard password disables mutations. The public caller cannot change mode.
- `.mode=live` is insufficient: two independent environment gates, canonical kill state, risk state, adapter state, and credentials are evaluated centrally and again at the exchange order boundary.
- Dashboard and agent share `TRADING_KILL_SWITCH_PATH`; invalid/unreadable state halts. A runtime drill proved the active agent halted without changing order/trade counts.
- Trading GET routes no longer reconcile, score, or probe exchanges.
- Protected config/drop-ins are 0700/0600 and identify deployment/effective mode without exposing secrets.
- Candidate market/signals recover from mixed JSON, expose freshness/source/schema, count all decisions, normalize confidence/actions, and show evidence-based capability UNKNOWN.
- Asset routing is deny-by-default. Ten cryptos are classified only as crypto and direct venue routing is disabled until market metadata is verified; none can fall through to Alpaca.
- Duplicate circuit-breaker implementation was removed without changing thresholds.
- Python 3.11 direct dependencies are pinned in `pyproject.toml` and a Phase 1 constraints file, with import verification.

## Runtime changes

Systemd was daemon-reloaded and both services were restarted after builds/tests. The dashboard remained available on port 3002 except for normal restart seconds; Cloudflare routing was unchanged. Candidate dashboard was not deployed.

Current state: dashboard and agent active, requested/effective mode paper/paper, both hard gates false, canonical kill switch inactive, orders 30, trades 0.

## Remaining

Dashboard mutations remain intentionally locked pending operator-supplied protected auth secret. Credentials previously exposed through broadly readable configuration require external rotation. Research scheduling, Control API, PostgreSQL, durable jobs, model OOS validation, and venue-market verification remain future work.

Decision: **GO FOR PHASE 2 — CONTRACT-FIRST CONTROL API**, while live trading remains locked.
