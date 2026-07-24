# Phase 1 Safety Containment

## Closed P0 findings

- Every legacy-dashboard mutation route uses one server-side `authorizeMutation` policy. Missing auth configuration returns `503 CONFIGURATION_ERROR`; missing or wrong credentials return `401 UNAUTHORIZED`.
- The client AuthGuard remains locked on timeout, fetch failure, and missing configuration.
- Live execution requires both `LIVE_EXECUTION_ENABLED` and `LIVE_TRADING_APPROVED`, with only `1`, `true`, `yes`, or `on` accepted after normalization.
- The central Python `LiveExecutionPolicy` is checked while loading/hot-reloading mode, before real adapter initialization, at the agent execution method, and inside `ExchangeAdapter.create_order`/OCO. Sandbox order submission is denied.
- Dashboard and Python resolve `TRADING_KILL_SWITCH_PATH`; missing sentinel is `INACTIVE`, valid sentinel is `ACTIVE`, and invalid/unreadable state is `UNKNOWN` and halts execution.
- Reconciliation and signal-quality GET routes only read existing artifacts. Exchange-status GET no longer probes exchanges.

## Runtime containment

Protected environment files and systemd drop-ins set both live gates false and the canonical sentinel path. The dashboard password remains intentionally empty, so all mutations are disabled until an operator supplies a secret outside Git.

The runtime drill activated the canonical sentinel, and the active agent logged `KILL SWITCH ACTIVE`. Orders remained 30 and trades remained 0. The sentinel was cleared and the agent restarted in paper mode.

No exchange order API was called.
